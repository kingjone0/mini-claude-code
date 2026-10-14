"""t4 复核：基础设施错误分类 + 超时优先于退出码。

检查点：
  (A) 分类表本身：§5.4 的六个分类里，哪些计入分母、哪些计作 agent 失败。
      断言 COUNTS_IN_DENOMINATOR / COUNTS_AS_AGENT_FAILURE 的成员符合预注册。
  (B) classify_run 的**顺序**：timed_out=True 且 returncode=1 时必须返回 timeout，不是 agent_result。
  (C) 真的压超时跑一次（把判定超时压到 0.05s），看 run_one 给出的 verdict_class。
  (D) 收集错误（exit>=2）=> infra_error。
  (E) 无逐 node 结果 => infra_error。
  (F) summarize() 的分母口径：infra/timeout 不进分母。
"""

from __future__ import annotations

import json
import os
import sys

PROJ = r"E:\aicoding\mini-claude-code"
for p in (PROJ, os.path.join(PROJ, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E            # noqa: E402
from eval.harness import judge as J          # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402

OUT = r"E:\aicoding\eval-runs\t4\infra_timeout"
PROBE = "pallets-click__4fd2fea"


def main():
    os.makedirs(OUT, exist_ok=True)
    res = {}
    fails = []

    # ---------- (A) 分类表 ----------
    print("=" * 70)
    print("(A) §5.4 分类表：哪些进分母 / 哪些算 agent 失败")
    print("=" * 70)
    print(f"  ALL_CLASSES            = {J.ALL_CLASSES}")
    print(f"  COUNTS_IN_DENOMINATOR  = {J.COUNTS_IN_DENOMINATOR}")
    print(f"  COUNTS_AS_AGENT_FAILURE= {J.COUNTS_AS_AGENT_FAILURE}")
    res["classes"] = {"all": list(J.ALL_CLASSES),
                      "in_denominator": list(J.COUNTS_IN_DENOMINATOR),
                      "as_agent_failure": list(J.COUNTS_AS_AGENT_FAILURE)}
    if J.INFRA_ERROR in J.COUNTS_IN_DENOMINATOR or J.TIMEOUT in J.COUNTS_IN_DENOMINATOR:
        fails.append("infra_error/timeout 被错误地计入了分母")
    if J.INFRA_ERROR in J.COUNTS_AS_AGENT_FAILURE or J.TIMEOUT in J.COUNTS_AS_AGENT_FAILURE:
        fails.append("infra_error/timeout 被错误地计作 agent 失败")
    print("  => infra/timeout 不进分母、不算 agent 失败："
          f"{J.INFRA_ERROR not in J.COUNTS_IN_DENOMINATOR and J.TIMEOUT not in J.COUNTS_IN_DENOMINATOR}")

    # ---------- (B) classify_run 顺序 ----------
    print()
    print("=" * 70)
    print("(B) classify_run：超时必须先于退出码")
    print("=" * 70)
    fake_timeout = {"timed_out": True, "returncode": 1, "meta": {"errors": 0}, "results": {}}
    fake_fail = {"timed_out": False, "returncode": 1, "meta": {"errors": 0},
                 "results": {"t::x": "failed"}}
    fake_usage = {"timed_out": False, "returncode": 3, "meta": {"errors": 1},
                  "results": {"t::x": "failed"}}
    fake_nores = {"timed_out": False, "returncode": 0, "meta": {"errors": 0}, "results": {}}
    c1 = J.classify_run(fake_timeout)
    c2 = J.classify_run(fake_fail)
    c3 = J.classify_run(fake_usage)
    c4 = J.classify_run(fake_nores)
    print(f"  timed_out=True, rc=1  => {c1}   （期望 timeout；若为 agent_result 则顺序写反）")
    print(f"  timed_out=False,rc=1  => {c2}   （期望 agent_result）")
    print(f"  rc=3 + errors         => {c3}   （期望 infra_error）")
    print(f"  rc=0 但无逐 node 结果 => {c4}   （期望 infra_error）")
    res["classify_run"] = {"timeout_rc1": c1, "fail_rc1": c2, "usage_rc3": c3, "nores_rc0": c4}
    if c1 != J.TIMEOUT:
        fails.append(f"timed_out=True 未被判成 timeout（实得 {c1}）—— §8.4 顺序写反")
    if c2 != J.AGENT_RESULT:
        fails.append(f"正常失败未被判成 agent_result（实得 {c2}）")
    if c3 != J.INFRA_ERROR:
        fails.append(f"exit>=2 未被判成 infra_error（实得 {c3}）")
    if c4 != J.INFRA_ERROR:
        fails.append(f"无逐 node 结果未被判成 infra_error（实得 {c4}）")

    # ---------- (C) 实操超时 ----------
    print()
    print("=" * 70)
    print("(C) 实操：把判定超时压到 0.05s，跑一条真实实例")
    print("=" * 70)
    inst = RI.load_instance(PROBE)
    rid = "t4-infra-timeout-probe"
    row = RI.run_one(PROBE, inst["patch"], run_id=rid, label="t4-timeout",
                     patch_kind="gold", judge_timeout=1, timeout_probe=True)
    print(f"  verdict_class = {row['verdict_class']}（期望 timeout）")
    print(f"  resolved      = {row['resolved']}（期望 False）")
    print(f"  timed_out     = {row.get('timed_out')}（期望 True）")
    print(f"  exit_code     = {row.get('exit_code')}（被强杀也是 1，与「有失败」同值）")
    print(f"  进分母？{row['verdict_class'] in J.COUNTS_IN_DENOMINATOR}（期望 False）")
    print(f"  算 agent 失败？{row['verdict_class'] in J.COUNTS_AS_AGENT_FAILURE}（期望 False）")
    res["timeout_probe"] = {"verdict_class": row["verdict_class"], "resolved": row["resolved"],
                            "timed_out": row.get("timed_out"), "exit_code": row.get("exit_code"),
                            "notes": row.get("notes", []),
                            "in_denominator": row["verdict_class"] in J.COUNTS_IN_DENOMINATOR}
    if row["verdict_class"] != J.TIMEOUT:
        fails.append(f"实操超时未判成 timeout（实得 {row['verdict_class']}）")
    if row["verdict_class"] in J.COUNTS_IN_DENOMINATOR:
        fails.append("实操超时被计入了分母")

    # ---------- (F) summarize 分母口径 ----------
    print()
    print("=" * 70)
    print("(F) summarize()：infra/timeout 不进分母")
    print("=" * 70)
    rows = [{"verdict_class": J.AGENT_RESULT, "resolved": True},
            {"verdict_class": J.AGENT_RESULT, "resolved": False},
            {"verdict_class": J.EMPTY_PATCH, "resolved": False},
            {"verdict_class": J.PATCH_CONFLICT, "resolved": False},
            {"verdict_class": J.INFRA_ERROR, "resolved": False},
            {"verdict_class": J.TIMEOUT, "resolved": False}]
    s = J.summarize(rows)
    print(f"  {json.dumps(s, ensure_ascii=False)}")
    print(f"  total={s['total']}（期望 6）  denominator={s['denominator']}（期望 4）")
    print(f"  resolved={s['resolved']}  infra_retryable={s['infra_retryable']}（期望 2）")
    res["summarize"] = s
    if s["denominator"] != 4:
        fails.append(f"分母口径不对：期望 4，实得 {s['denominator']}")
    if s["infra_retryable"] != 2:
        fails.append(f"infra_retryable 不对：期望 2，实得 {s['infra_retryable']}")

    res["failures"] = fails
    with open(os.path.join(OUT, "infra_timeout.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print()
    if fails:
        for x in fails:
            print("FAIL:", x)
        return 1
    print("基础设施错误分类 + 超时优先：全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())