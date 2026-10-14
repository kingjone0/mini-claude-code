"""t5 前置：实测「venv 里的 t4 残留 .pth 是否真的会遮蔽我自己的实例工作区」。

这是 t4 报的 D1/D3 在现场的一个具体体现：venv 按 `<repo>-<base_commit[:8]>` 命名 =>
**跨 run_id 共享**；而 `.pth` 挂载点写在 venv 里 => 上一个 run 的挂载会留在里面。

本脚本只读，不改任何东西：对一个实例，逐个看它的 venv 里每个 .pth 会把
`import <pkg>` 解析到哪个工作区，并明确回答「是否指向别处」。

用法：python t5_venv_shadow_probe.py <instance_id>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
for p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E          # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402


def pth_files(vd):
    sp = os.path.join(vd, "Lib", "site-packages")
    if not os.path.isdir(sp):
        return sp, []
    return sp, sorted(f for f in os.listdir(sp) if f.endswith(".pth"))


def main():
    iid = sys.argv[1]
    inst = RI.load_instance(iid)
    vd = E.venv_dir(inst)
    py = os.path.join(vd, "Scripts", "python.exe")
    sp, pths = pth_files(vd)
    pkg = None

    print("=" * 78)
    print(f"venv 遮蔽探针：{iid}")
    print(f"  venv        = {vd}")
    print(f"  venv 存在   = {os.path.isdir(vd)}   python 存在 = {os.path.exists(py)}")
    print(f"  site-packages 里的 .pth：")
    for f in pths:
        with open(os.path.join(sp, f), encoding="utf-8") as fh:
            body = fh.read().strip().replace("\n", " ; ")
        kind = "本 harness" if f.startswith("_eval_editable_") else "**非本 harness（残留）**"
        print(f"    [{kind}] {f}")
        print(f"        {body[:170]}")

    if not os.path.exists(py):
        print("\n  (venv 不存在或无 python，无法探针)")
        return 1

    # 推断 import 名需要一个工作区；这里用临时的空目录只为取包名，不建工作区。
    # 包名从实例的 collection_scope 反推不可靠 => 直接试常见名，或从缓存仓库布局看。
    cand_names = []
    # 包名尽量用 harness **自己的**推断逻辑会认的布局：src/<pkg> 或 <pkg> 或 <pkg>.py。
    # 这里只读缓存仓库的 tree（不建 eval-runs 工作区、不碰 venv），所以按 tree 走两层。
    cache = os.path.join(E.CACHE_ROOT, E.repo_key_of(inst["repo"]))
    blocked = {"setup", "conftest", "test", "tests", "testing", "noxfile", "tasks",
               "docs", "doc", "examples", "benchmarks", "scripts", "artwork"}
    for base in ("src", ""):
        spec = f"{inst['base_commit']}:{base}" if base else inst["base_commit"]
        r = E.git(cache, "ls-tree", "--name-only", spec)
        if r.rc != 0:
            continue
        for line in (r.stdout or "").splitlines():
            t = line.strip()
            if not t:
                continue
            name = t[:-3] if t.endswith(".py") else t
            if name in blocked or "/" in name or name.startswith(("_", ".")):
                continue
            cand_names.append(name)
    seen = set()
    cand_names = [c for c in cand_names if not (c in seen or seen.add(c))]
    print(f"\n  从缓存仓库 {inst['base_commit'][:8]} 的顶层布局推断的候选包名：{cand_names}")

    resolved = {}
    for name in cand_names:
        pr = E.sh([py, "-c",
                   "import os,sys,importlib;"
                   f"m=importlib.import_module({name!r});"
                   "sys.stdout.write(os.path.realpath(getattr(m,'__file__','') or 'MISSING'))"],
                  cwd=os.path.expanduser("~"), timeout=120)
        if pr.rc == 0:
            resolved[name] = pr.stdout.strip()
            print(f"    import {name:<20} -> {pr.stdout.strip()}")
            pkg = name
            break
        else:
            print(f"    import {name:<20} -> 失败（{pr.stderr.strip()[-120:]}）")

    print("\n  结论：")
    if not pkg:
        print("    没有任何候选包能被导入 => 该 venv 当前无法用于判定。")
        return 1
    path = resolved[pkg]
    # 判定这个路径属于哪个 run_id 的工作区
    import re
    m = re.match(r"^(.*?[\\/]eval-runs[\\/][^\\/]+[\\/][^\\/]+)[\\/]", path)
    owner = m.group(1) if m else "(不在 eval-runs 下)"
    print(f"    import {pkg} 解析到：{path}")
    print(f"    归属工作区：{owner}")
    stale = [(f) for f in pths if not f.startswith("_eval_editable_")]
    print(f"    非本 harness 的残留 .pth：{stale}")
    print(f"    => 残留是否**当前正在生效**：{bool(stale) and owner != '(不在 eval-runs 下)'}")
    out = {
        "instance_id": iid,
        "venv": vd,
        "pth_files": pths,
        "stale_pth": stale,
        "import_name": pkg,
        "resolved_path": path,
        "owner_workspace": owner,
    }
    dest = os.path.join(E.RUNS_ROOT, "t5", "venv_shadow_probe.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n  证据落盘：{dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())