"""t5：采集 agent 的工作区 diff，然后交给 harness **独立判定**（§5.1）。

这是 P0 最核心的一条对照：**agent 说自己成功不算数，harness 的独立 pytest 说了算。**
本脚本做三件事：
1. `git -C <ws> status --porcelain` + `git diff --binary` 采集 agent 留下的候选补丁
   （与预注册 §5.1 的采集口径一致：从工作区取，不接受 agent 自贴的 diff）；
2. 把 agent 的**自述**（trace 里的 assistant_text + console 里的判定行）原样摘出来；
3. 调 `run_instance.run_one` 判定，并把 F2P/P2P 逐条结果落盘。

注意：判定会在**新建的工作区**里重跑（§8.1），不复用 agent 那个已污染的工作区 ——
这正是「判定不采信 agent 现场」的落地方式。

用法：python t5_collect_and_judge.py <instance_id> <run_id> <agent_ws>
"""

from __future__ import annotations

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
for p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E            # noqa: E402
from eval.harness import judge as J          # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402


def main():
    iid, rid, ws = sys.argv[1], sys.argv[2], sys.argv[3]
    inst = RI.load_instance(iid)
    root = E.run_root(rid)

    # --- 1) 采集 agent 的候选补丁（§5.1 口径）
    porcelain_after = E.run_porcelain(ws)
    diff = E.git(ws, "diff", "--binary")
    diff_text = diff.stdout or ""
    cand_path = os.path.join(root, "agent_candidate_patch.diff")
    E.write_text(cand_path, diff_text)

    status_path = os.path.join(root, "porcelain_after.txt")
    E.write_text(status_path, porcelain_after + "\n")

    paths = J.patch_touched_paths(diff_text)
    kept, test_side, dropped, touched_tests, test_paths = J.stripped_patch(diff_text)

    print("=" * 78)
    print(f"t5 采集 + 独立判定：{iid}")
    print("=" * 78)
    print(f"  agent 工作区      = {ws}")
    print(f"  porcelain(跑后)   = {porcelain_after!r}")
    print(f"  采集到的 diff     = {len(diff_text)} 字节 -> {cand_path}")
    print(f"  diff 涉及文件     = {paths}")
    print(f"  测试侧文件        = {test_paths}   touched_tests={touched_tests}")
    print(f"  剥离测试侧后剩     = {J.patch_touched_paths(kept)}")

    # --- 2) agent 自述
    agent_json = os.path.join(PROJECT_ROOT, "servers", "experiments", "out",
                              "agent-t5-smoke.json")
    self_report = {"assistant_texts": [], "final_lines": []}
    if os.path.exists(agent_json):
        with open(agent_json, encoding="utf-8") as f:
            doc = json.load(f)
        for s in doc.get("trace", {}).get("steps", []):
            if s.get("type") == "assistant_text":
                self_report["assistant_texts"].append(s.get("preview") or "")
        term = doc.get("trace", {}).get("termination") or {}
        self_report["agent_error"] = term.get("agent_error")
        self_report["agent_stop"] = term.get("agent_stop")
    # coding_agent 自己打印的判定行（它对 fixture 口径的自评，不是 harness 判定）
    log = os.path.join(PROJECT_ROOT, "servers", "experiments", "out", "agent-t5-smoke.log")
    if os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "判定" in line or "==>" in line:
                    self_report["final_lines"].append(line.rstrip())
    E.write_text(os.path.join(root, "agent_self_report.json"),
                 json.dumps(self_report, ensure_ascii=False, indent=2) + "\n")

    print("\n  --- agent 自述（摘录）---")
    for t in self_report["assistant_texts"]:
        print(f"    [assistant] {t[:200]}")
    print(f"    [agent_stop] {self_report.get('agent_stop')}")
    print(f"    [agent_error] {self_report.get('agent_error')}")
    for l in self_report["final_lines"]:
        print(f"    [console] {l.strip()}")

    # --- 3) harness 独立判定：用**新建工作区**跑同一个候选补丁
    row = RI.run_one(iid, diff_text, run_id=rid + "-judge", label="t5-judge",
                     patch_kind="file:agent_candidate_patch.diff",
                     keep_workspace=False)
    judg_path = E.art_dir(rid + "-judge", iid)
    E.write_text(os.path.join(root, "harness_verdict.json"),
                 json.dumps(row, ensure_ascii=False, indent=2) + "\n")

    print("\n  --- harness 独立判定 ---")
    print(f"    verdict_class = {row['verdict_class']}")
    print(f"    resolved      = {row['resolved']}")
    print(f"    touched_tests = {row.get('touched_tests')}   "
          f"test_side_paths={row.get('test_side_paths')}")
    print(f"    判定补丁文件   = {row.get('judged_patch_paths')}")
    print(f"    证据目录      = {judg_path}")
    j = row.get("judged") or {}
    if j:
        print(f"    F2P {j['f2p_total'] - len(j['f2p_not_passed'])}/{j['f2p_total']}")
        for n in j["f2p_not_passed"]:
            print(f"      未过: {n}")
        print(f"    P2P {j['p2p_total'] - len(j['p2p_not_passed'])}/{j['p2p_total']}")
        for n in j["p2p_not_passed"]:
            print(f"      未过: {n}")
    print(f"    notes: {row.get('notes')}")

    print("\n  --- 对照结论 ---")
    said_ok = bool(self_report["final_lines"]) and any(
        "成功" in l and "未成功" not in l for l in self_report["final_lines"])
    print(f"    agent 自述「修好了」= {said_ok}（依据：{self_report['final_lines']}）")
    print(f"    harness 独立判定 resolved = {row['resolved']}")
    print(f"    两者一致 = {said_ok == bool(row['resolved'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())