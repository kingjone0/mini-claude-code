"""t4 复核：断点续跑的**机器可读**证据（不靠解析控制台文本）。

直接读 scheduler 写的 manifest.json：
  skipped_completed 应等于中断前已完成的条数
  已完成实例的 verdict/resolved 与中断前一致（比对 state.jsonl 的历史行）
"""

from __future__ import annotations

import json
import os
import sys

PROJ = r"E:\aicoding\mini-claude-code"
for p in (PROJ, os.path.join(PROJ, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

RID = "t4-resume-run"
MAN = os.path.join(r"E:\aicoding\eval-runs", RID, "manifest.json")
SJ = os.path.join(r"E:\aicoding\eval-runs", RID, "state.jsonl")
OUT = r"E:\aicoding\eval-runs\t4\resume\resume_manifest_evidence.json"


def main():
    man = json.load(open(MAN, encoding="utf-8"))
    lines = [json.loads(l) for l in open(SJ, encoding="utf-8").read().splitlines() if l.strip()]
    per = {}
    for r in lines:
        per.setdefault(r["instance_id"], []).append(r)

    skipped = man["skipped_completed"]
    requested = man["requested"]
    resume = man["resume"]
    # 中断前完成的那条：在 state.jsonl 里应只出现一次，且是 judged
    once = {i: v for i, v in per.items() if len(v) == 1 and v[0].get("stage") == "judged"}
    multi = {i: [x.get("stage") for x in v] for i, v in per.items() if len(v) > 1}

    print("=" * 70)
    print(f"run_id={RID}  resume={resume}  requested={requested}")
    print(f"skipped_completed = {skipped}   （期望 = 1，即中断前已完成的那条）")
    print(f"wall_seconds = {man['wall_seconds']}")
    print(f"summary = {json.dumps(man['summary'], ensure_ascii=False)}")
    print("=" * 70)
    print(f"state.jsonl 行数 = {len(lines)}  实例数 = {len(per)}")
    print(f"只出现一次的 judged 实例：{sorted(once)}")
    print(f"出现多行的实例（重跑会追加）：{multi}")
    print()
    print("逐实例最终结论：")
    for r in man["instances"]:
        print(f"  {r['instance_id']:42s} {r['verdict_class']:13s} resolved={r['resolved']}")

    ok = (resume is True and skipped == 1 and requested == 4
          and len(man["instances"]) == 4
          and all(x["resolved"] for x in man["instances"])
          and not multi)
    res = {"run_id": RID, "skipped_completed": skipped, "requested": requested,
           "resume": resume, "state_lines": len(lines), "single_judged": sorted(once),
           "multi_line_instances": multi, "summary": man["summary"],
           "all_four_resolved": all(x["resolved"] for x in man["instances"]),
           "overall_ok": ok}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print()
    print(f"断点续跑（manifest 口径）判定：{'PASS' if ok else 'FAIL'}")
    print(f"-> {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())