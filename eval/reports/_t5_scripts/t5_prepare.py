"""t5 冒烟：准备实例工作区 + problem_statement 文件（不改任何实现代码）。

做三件事：
1. 用 harness 自己的 `fresh_workspace` 建实例工作区（与判定阶段同一个工作区，§8.1 明确
   「agent 阶段与判定阶段使用同一个工作区」）；
2. 把实例的 `problem_statement` 原文写成文件，供 `--problem-file` 使用
   （预注册 §3.4：题面来自提交信息原文，不由我代笔）；
3. **跑前记录**：`git -C <实例 repo> status --porcelain`、HEAD、以及 venv 的 `.pth` 现状。

用法：python t5_prepare.py <instance_id> <run_id>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
for p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E            # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402


def main():
    iid = sys.argv[1]
    rid = sys.argv[2]
    inst = RI.load_instance(iid)
    art = E.art_dir(rid, iid)
    os.makedirs(art, exist_ok=True)

    # 1) 实例工作区
    ws = E.fresh_workspace(inst, rid, log_path=os.path.join(art, "workspace.log"))
    inst["_ws"] = ws

    # 2) problem_statement -> 文件（原文，不做任何改写）
    pf = os.path.join(E.run_root(rid), "_problem.txt")
    E.write_text(pf, inst["problem_statement"] + "\n")

    # 3) 跑前状态
    head = E.git(ws, "rev-parse", "HEAD").stdout.strip()
    porcelain_before = E.run_porcelain(ws)
    base_head = E.git(os.path.join(E.CACHE_ROOT, E.repo_key_of(inst["repo"])),
                      "rev-parse", inst["base_commit"]).stdout.strip()

    vd = E.venv_dir(inst)
    sp = os.path.join(vd, "Lib", "site-packages")
    pths = sorted(f for f in (os.listdir(sp) if os.path.isdir(sp) else []) if f.endswith(".pth"))

    out = {
        "instance_id": iid,
        "run_id": rid,
        "workspace": ws,
        "problem_file": pf,
        "problem_statement": inst["problem_statement"],
        "workspace_head": head,
        "base_commit": inst["base_commit"],
        "cache_head_at_base": base_head,
        "porcelain_before": porcelain_before,
        "porcelain_before_empty": porcelain_before == "",
        "venv": vd,
        "venv_pth_files": pths,
        "stale_pth_files": [f for f in pths if not f.startswith("_eval_editable_")],
        "f2p": inst["FAIL_TO_PASS"],
        "p2p_count": len(inst["PASS_TO_PASS"]),
        "collection_scope": inst["collection_scope"],
        "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    dest = os.path.join(E.run_root(rid), "prepare.json")
    E.write_text(dest, json.dumps(out, ensure_ascii=False, indent=2) + "\n")

    print("=" * 78)
    print(f"t5 冒烟准备：{iid}   run_id={rid}")
    print("=" * 78)
    print(f"  工作区        = {ws}")
    print(f"  工作区 HEAD   = {head}")
    print(f"  base_commit   = {inst['base_commit']}")
    print(f"  porcelain(跑前) = {porcelain_before!r}  （空 = 干净）")
    print(f"  problem 文件  = {pf}")
    print(f"  problem 原文（{len(inst['problem_statement'])} 字符）：")
    for line in inst["problem_statement"].splitlines():
        print(f"      | {line}")
    print(f"  F2P ({len(inst['FAIL_TO_PASS'])} 条)：")
    for n in inst["FAIL_TO_PASS"]:
        print(f"      {n}")
    print(f"  P2P：{len(inst['PASS_TO_PASS'])} 条；collection_scope={inst['collection_scope']}")
    print(f"  venv          = {vd}")
    print(f"  venv 里的 .pth：{pths}")
    if out["stale_pth_files"]:
        print(f"  ! 非本 harness 的残留 .pth：{out['stale_pth_files']}"
              "（来自 t4 复核；t5 只记录，不修改实现代码）")
    print(f"\n  落盘：{dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())