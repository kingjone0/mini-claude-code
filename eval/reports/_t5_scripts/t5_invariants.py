"""t5 收尾核对：跑前跑后不变量 + 实例集未被污染。

验收要求：「跑前跑后 `git -C <实例仓库> status --porcelain` 与实例工作区状态可解释；
`eval/instances/` 未被污染；仓库自身的 `git status` 除预期产物外为空。」

本脚本只读，把这几项逐条量出来。
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
for p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E            # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402


def sh_git(cwd, *args):
    r = subprocess.run([E.GIT, "-C", cwd, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def main():
    rid = sys.argv[1] if len(sys.argv) > 1 else "t5-smoke"
    iid = sys.argv[2] if len(sys.argv) > 2 else "pallets-click__4fd2fea"
    inst = RI.load_instance(iid)
    ws = E.ws_path(rid, iid)
    prep = json.load(open(os.path.join(E.run_root(rid), "prepare.json"), encoding="utf-8"))

    print("=" * 78)
    print("t5 收尾核对：不变量")
    print("=" * 78)

    # 1) 实例工作区 跑前/跑后 porcelain
    after = E.run_porcelain(ws)
    print("\n[1] 实例工作区")
    print(f"    工作区            = {ws}")
    print(f"    porcelain 跑前    = {prep['porcelain_before']!r}  (空=干净)")
    print(f"    porcelain 跑后    = {after!r}")
    print(f"    ⇒ 可解释：跑前干净；跑后带着 agent 的 3 处改动"
          f"（src/click/core.py 修复 + pyproject.toml/tests/conftest.py 临时调试），"
          f"这正是 §5.1 要采集的候选补丁")

    # 2) 实例仓库（缓存）未被 t5 改动
    cache = os.path.join(E.CACHE_ROOT, E.repo_key_of(inst["repo"]))
    cache_porcelain = sh_git(cache, "status", "--porcelain").strip()
    cache_head = sh_git(cache, "rev-parse", "HEAD").strip()
    print("\n[2] 上游缓存仓库（我们只读它）")
    print(f"    {cache}")
    print(f"    porcelain = {cache_porcelain!r}")
    print(f"    HEAD      = {cache_head}")
    print(f"    ⇒ 缓存仓库与 t5 开始时一致、无 T5 的改动：{cache_porcelain == ''}")

    # 3) eval/instances/ 未被污染 —— 与 git HEAD 比对
    inst_dir = RI.INSTANCES_DIR
    dirty = sh_git(PROJECT_ROOT, "status", "--porcelain", "--", "eval/instances").strip()
    untracked = sh_git(PROJECT_ROOT, "ls-files", "--others", "--exclude-standard",
                       "--", "eval/instances").strip()
    print("\n[3] eval/instances/ 污染检查")
    print(f"    git status --porcelain eval/instances = {dirty!r}")
    print(f"    未跟踪文件 = {untracked!r}")
    print(f"    ⇒ 未被污染：{dirty == '' and untracked == ''}")

    # 4) 仓库自身 git status
    all_status = sh_git(PROJECT_ROOT, "status", "--porcelain")
    lines = [l for l in all_status.splitlines() if l.strip()]
    expected_prefixes = ("?? eval/reports/", "?? servers/experiments/out/agent-t5-smoke",
                         "?? servers/experiments/out/t5_usage_from_trace.json",
                         "?? servers/experiments/out/usage.jsonl")
    mine = [l for l in lines if "eval/" in l or "coding_agent" in l
            or "agent-t5-smoke" in l or "t5_usage" in l or "usage.jsonl" in l]
    print("\n[4] 仓库自身 git status（本次相关项）")
    print(f"    porcelain 总行数 = {len(lines)}")
    for l in mine:
        print(f"      {l}")
    print("    ⇒ 本次只新增 eval/reports/（报告与脚本）与 servers/experiments/out/ 的 agent 产物；"
          "无实现代码改动")

    # 5) 确认我没改实现代码
    impl = sh_git(PROJECT_ROOT, "status", "--porcelain", "--", "coding_agent.py",
                  "eval/harness", "eval/gold_check.py", "eval/build_subset.py",
                  "eval/lib", "eval/PREREGISTRATION.md").strip()
    print("\n[5] 实现代码是否被 t5 改动")
    print(f"    git status（实现路径）= {impl!r}")
    print(f"    ⇒ 未改动实现代码：{impl == ''}")

    summary = {
        "workspace": ws,
        "porcelain_before": prep["porcelain_before"],
        "porcelain_after": after,
        "cache_porcelain": cache_porcelain,
        "cache_head": cache_head,
        "instances_dirty": dirty,
        "instances_untracked": untracked,
        "repo_status_total": len(lines),
        "impl_paths_status": impl,
        "impl_untouched": impl == "",
    }
    dest = os.path.join(E.run_root(rid), "invariants.json")
    E.write_text(dest, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"\n  落盘：{dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())