"""t15 验收之外的反向核查：`--resume` 对 timeout/infra 槽的语义（离线，零模型调用）。

scheduler_p1.py:333 的跳过条件是 `prev.get("stage") == "judged"`。本脚本用**真实的**
`read_triple_state` / `append_triple_state`（直接 import scheduler_p1）在临时目录里
复现该判定，证明：

  ① timeout/infra 行（stage="failed_infra"）**不会**被跳过，会被当作待跑槽重新提交；
  ② 同一 (实例, 臂) 再追加一行后，`read_triple_state` 只保留**最后一行** ⇒ 终态被改写；
  ③ `len(read_triple_state())` 按 (iid, arm) 去重 ⇒ 重跑**不增加** started_arms 计数
     （预算少记），而实际 spawn 次数 +1。

输出：eval/reports/p1-review-resume.txt / .json
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile

ROOT = r"E:\aicoding\mini-claude-code"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

from eval.harness import scheduler_p1 as SP   # noqa: E402

OUT_TXT = os.path.join(ROOT, "eval", "reports", "p1-review-resume.txt")
OUT_JSON = os.path.join(ROOT, "eval", "reports", "p1-review-resume.json")
IID = "demo-instance"
LINES: list[str] = []
R: dict = {}


def say(s=""):
    LINES.append(s)


def main():
    tmp = tempfile.mkdtemp(prefix="t15-resume-")
    run_root = os.path.join(tmp, "batch")
    os.makedirs(run_root, exist_ok=True)

    # 模拟「跑到一半被止损」：A 判完、B 超时、C 判完
    rows = [
        {"instance_id": IID, "arm": "A", "stage": "judged", "verdict_class": "agent_result",
         "resolved": True, "wall_s": 100.0},
        {"instance_id": IID, "arm": "B", "stage": "failed_infra", "verdict_class": "timeout",
         "resolved": False, "wall_s": 900.02},
        {"instance_id": IID, "arm": "C", "stage": "judged", "verdict_class": "agent_result",
         "resolved": False, "wall_s": 120.0},
    ]
    for r in rows:
        SP.append_triple_state(run_root, r)

    prior = SP.read_triple_state(run_root)
    # 与 scheduler_p1.py:333 逐字相同的判定
    skipped, pending = [], []
    for arm in ("A", "B", "C"):
        prev = prior.get((IID, arm))
        if prev and prev.get("stage") == "judged":
            skipped.append(arm)
        else:
            pending.append(arm)

    say("# t15 反向核查：`--resume` 对 timeout/infra 槽的语义（离线）")
    say()
    say(f"临时批次目录：{run_root}")
    say()
    say("## ① 跳过判定（复刻 scheduler_p1.py:333 的表达式）")
    say(f"- 状态文件里 3 行：A=judged/agent_result、B=failed_infra/timeout、C=judged/agent_result")
    say(f"- skipped（跳过，不重跑）= {skipped}")
    say(f"- pending（会重新提交）= {pending}")
    say(f"- 复刻源码字符串：`if prev and prev.get(\"stage\") == \"judged\":` "
        f"（scheduler_p1.py:333）")
    say(f"- ⇒ timeout 槽被判定为 pending：{'是' if 'B' in pending else '否'}")
    R["skip_predicate"] = {"skipped": skipped, "pending": pending}

    # ② 再追加一行（模拟 resume 重跑 B）后，终态被改写
    say()
    say("## ② 重跑同一 (实例, 臂) 后终态被改写")
    say(f"- 重跑前 B 的终态：{json.dumps(prior[(IID, 'B')], ensure_ascii=False)}")
    SP.append_triple_state(run_root, {
        "instance_id": IID, "arm": "B", "stage": "judged", "verdict_class": "agent_result",
        "resolved": True, "wall_s": 220.0, "note": "resume 重跑后的新终态"})
    after = SP.read_triple_state(run_root)
    say(f"- 重跑后 B 的终态：{json.dumps(after[(IID, 'B')], ensure_ascii=False)}")
    say(f"- 文件行数 = {sum(1 for _ in io.open(SP.triple_state_jsonl(run_root), encoding='utf-8'))}"
        f"（同一 (实例,臂) 现在有两行，读回取**最后一行**）")
    R["rewrite"] = {"before": prior[(IID, "B")], "after": after[(IID, "B")]}

    # ③ 预算少记
    say()
    say("## ③ 预算计数（scheduler_p1.py:131-136 的 started_arms 公式）")
    real_spawns = 4            # A + B + C + resume 重跑 B
    counted = 35 + len(after)  # REV2_INITIAL_STARTED + len(prior) 的真实代码形态
    say(f"- 实际 spawn 次数 = {real_spawns}（含 resume 重跑 B 的一次）")
    say(f"- `len(read_triple_state())` = {len(after)}（按 (instance, arm) 去重）⇒ "
        f"started_arms = 35 + {len(after)} = {counted}")
    say("- ⇒ 重跑**不增加** started_arms：REV2 的 75 槽上限会被少记，实际启动数比账面多 1。")
    R["budget"] = {"real_spawns": real_spawns, "len_prior": len(after),
                   "started_arms_formula": counted}

    R["source_refs"] = ["eval/harness/scheduler_p1.py:131-136",
                        "eval/harness/scheduler_p1.py:333",
                        "eval/harness/scheduler_p1.py:312"]
    say()
    say("## 源码位置")
    for s in R["source_refs"]:
        say(f"- {s}")

    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(R, ensure_ascii=False, indent=2) + "\n")
    with io.open(OUT_TXT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(LINES) + "\n")
    print("resume-predicate: pending=%s skipped=%s rewritten=%s"
          % (pending, skipped, prior[(IID, "B")]["verdict_class"] != after[(IID, "B")]["verdict_class"]))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
