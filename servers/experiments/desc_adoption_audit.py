"""desc_adoption_audit.py —— 从产物 events **现算**「repo_map 是否被采用」。

采用的操作定义（预先写在 `AB-TOOL-DESC.md` §3，本脚本只是它的实现）：
    在一次运行里，`events` 中 `type == "tool_use"` 且
    `tool == "mcp__code-tools__repo_map"` 的条目数 > 0，
    即视为该次运行**采用了** repo_map。

**刻意不读 `metrics.used_repo_map` / `metrics.repo_map_count` 作为判定依据** ——
那是同一事实的第二份拷贝，两者不一致时无从判断谁对。本脚本把它们算出来只为对照，
对照不一致会被单独列出来（那本身就是一条值得记的发现）。

与 `desc_ab_runner.py` 是**两条独立的代码路径**算同一件事：编排器记它看到的，
本脚本从产物重算。一致才说明不是某个脚本的 bug 造出了一个假结论。

用法：
    python desc_adoption_audit.py                       # 默认量实验数据
    python desc_adoption_audit.py --glob "agent-ab-[AB]-*.json"   # 量已知答案的旧数据（量具对照）
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import agent.config as agent_config  # noqa: E402

agent_config.force_utf8_output()

OUT_DIR = Path(__file__).resolve().parent / "out"
REPO_MAP_TOOL = "mcp__code-tools__repo_map"
DEFAULT_GLOB = "agent-desc-ab-[ABC]-*.json"

# 各臂计划生效的文本档位：(instructions 档, repo_map 描述档)。
# 量具**自己**知道计划，才能独立判断某一份产物是不是被配错了臂。
ARM_PLAN = {"A": ("v1", "v1"), "B": ("v2", "v1"), "C": ("v1", "v2")}


def arm_of(path: Path) -> str:
    """从文件名里取臂名：agent-desc-ab-A-3.json -> A。

    取**倒数第二段**而不是固定下标 —— 固定下标在文件名段数变化时会静默返回 "?"，
    而 "?" 会被当成一个正常臂去汇总（旧产物 `agent-ab-A-1.json` 就是这样被归成
    一个叫 "?" 的臂的）。宁可返回 "?" 也要让它出现在异常清单里。
    """
    parts = path.stem.split("-")
    if len(parts) >= 2 and len(parts[-2]) == 1 and parts[-2].isalpha():
        return parts[-2]
    return "?"


def measure(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("events") or []
    uses = [e for e in events if e.get("type") == "tool_use"]
    results = [e for e in events if e.get("type") == "tool_result"]

    per_tool: dict[str, int] = defaultdict(int)
    for e in uses:
        per_tool[str(e.get("tool"))] += 1

    rm_uses = [e for e in uses if e.get("tool") == REPO_MAP_TOOL]
    rm_results = [e for e in results if e.get("tool") == REPO_MAP_TOOL]

    metrics = data.get("metrics") or {}
    trace_arm = ((data.get("trace") or {}).get("run") or {}).get("arm") or {}
    steps = ((data.get("trace") or {}).get("steps")) or []
    trace_tool_calls = None
    trace_turns = None
    if steps:
        trace_tool_calls = sum(1 for s in steps if s.get("type") == "tool_call")
        trace_turns = sum(1 for s in steps if s.get("type") == "turn")

    return {
        "file": path.name,
        "arm": arm_of(path),
        "repo_map_tool_uses": len(rm_uses),
        "repo_map_tool_results": len(rm_results),
        "adopted": len(rm_uses) > 0,
        "tool_calls_total": metrics.get("tool_calls_total"),
        "trace_tool_calls": trace_tool_calls,
        "uses_seen": len(uses),
        "results_seen": len(results),
        "metrics_used_repo_map": metrics.get("used_repo_map"),
        "metrics_repo_map_count": metrics.get("repo_map_count"),
        "success": metrics.get("success"),
        "baseline_exit_code": metrics.get("baseline_exit_code"),
        "final_exit_code": metrics.get("final_exit_code"),
        "num_turns": trace_turns if trace_turns is not None else metrics.get("num_turns"),
        "input_tokens": metrics.get("input_tokens"),
        "arm_evidence": {
            "trace_instr": trace_arm.get("server_instructions_raw"),
            "trace_desc": trace_arm.get("repo_map_desc_raw"),
            "metrics_instr": metrics.get("server_instructions_raw"),
            "metrics_desc": metrics.get("repo_map_desc_raw"),
        },
        # 有 trace.run 才说明这份产物是 trace 层之后的产物 —— 用它区分
        # 「旧产物没有这些字段」（说明）与「新产物字段是空的」（异常）。
        "has_trace_run": bool(trace_arm),
        "per_tool": dict(per_tool),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default=DEFAULT_GLOB)
    args = ap.parse_args()

    paths = sorted(OUT_DIR.glob(args.glob))
    if not paths:
        print(f"[FAIL] 没有匹配 {args.glob} 的产物（目录 {OUT_DIR}）")
        return 2

    rows = [measure(p) for p in paths]

    print("=" * 100)
    print(f"采用率现算（操作定义：events 里 {REPO_MAP_TOOL} 的 tool_use 条目数 > 0）")
    print(f"glob = {args.glob}    命中 {len(rows)} 份产物")
    print("=" * 100)
    print(f"{'文件':<26} {'臂':>2} {'采用':>4} {'rm_use':>6} {'rm_res':>6} {'工具调用':>7} "
          f"{'m.adopted':>9} {'m.count':>7} {'success':>7} {'基线':>4} {'终':>3} {'turns':>5}")
    print("-" * 100)
    for r in rows:
        print(f"{r['file']:<26} {r['arm']:>2} {str(r['adopted']):>4} {r['repo_map_tool_uses']:>6} "
              f"{r['repo_map_tool_results']:>6} {str(r['tool_calls_total']):>7} "
              f"{str(r['metrics_used_repo_map']):>9} {str(r['metrics_repo_map_count']):>7} "
              f"{str(r['success']):>7} {str(r['baseline_exit_code']):>4} "
              f"{str(r['final_exit_code']):>3} {str(r['num_turns']):>5}")

    print()
    print("=" * 100)
    print("按臂汇总")
    print("=" * 100)
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    for arm in sorted(by_arm):
        rs = by_arm[arm]
        adopted = sum(1 for r in rs if r["adopted"])
        print(f"  臂 {arm}: 采用 {adopted}/{len(rs)}"
              f"   （metrics 自报 {sum(1 for r in rs if r['metrics_used_repo_map'])}/{len(rs)}，"
              f"repo_map_count 求和 {sum(int(r['metrics_repo_map_count'] or 0) for r in rs)}）")

    print()
    print("=" * 100)
    print("对照与异常（如实列出，不掩盖）")
    print("=" * 100)
    problems: list[str] = []
    infos: list[str] = []

    for r in rows:
        # 结构性检查：每次 tool_use 都应有一条 tool_result。
        # ⚠️ 只能比**总数**，不能按工具名比 —— `events` 里的 tool_result 事件
        #    **不带工具名**（字段只有 i, type, chars, text）。按工具名比会凭空
        #    造出「采用了但没结果」的假缺口（本量具的第一版就这样误报过两次）。
        if r["uses_seen"] != r["results_seen"]:
            problems.append(f"[{r['file']}] tool_use({r['uses_seen']}) != "
                            f"tool_result({r['results_seen']}) —— 结构性缺口")
        # 三条**独立**记录对同一件事的计数必须一致：events（旧内联流）、
        # trace.steps（trace 层）、metrics（汇总）。三者不一致说明有记录器漏记。
        if (r["trace_tool_calls"] is not None
                and r["trace_tool_calls"] != r["uses_seen"]):
            problems.append(
                f"[{r['file']}] 三条独立计数不一致：events.tool_use={r['uses_seen']} "
                f"trace.steps.tool_call={r['trace_tool_calls']} "
                f"metrics.tool_calls_total={r['tool_calls_total']}")
        if bool(r["metrics_used_repo_map"]) != r["adopted"]:
            problems.append(f"[{r['file']}] events 现算 adopted={r['adopted']} 但 "
                            f"metrics.used_repo_map={r['metrics_used_repo_map']}")
        if r["baseline_exit_code"] != 1:
            problems.append(f"[{r['file']}] 基线 pytest 退出码 {r['baseline_exit_code']!r} != 1")

        ev = r["arm_evidence"]
        if not r["has_trace_run"]:
            infos.append(f"[{r['file']}] 无 trace.run（trace 层之前的旧产物，不做自证核对）")
        elif all(v is None for v in ev.values()):
            problems.append(f"[{r['file']}] 有 trace.run 但两个文本通道字段**都是空的** —— "
                            "这份产物无法自证它跑在哪一臂")
        else:
            if (ev["trace_instr"] != ev["metrics_instr"]
                    or ev["trace_desc"] != ev["metrics_desc"]):
                problems.append(f"[{r['file']}] trace 与 metrics 两处自证不一致：{ev}")
            plan = ARM_PLAN.get(r["arm"])
            if plan is None:
                problems.append(f"[{r['file']}] 臂名 {r['arm']!r} 不在计划表里，无法核对档位")
            elif (ev["trace_instr"], ev["trace_desc"]) != plan:
                problems.append(f"[{r['file']}] 实测档位 {(ev['trace_instr'], ev['trace_desc'])} "
                                f"!= 臂 {r['arm']} 的计划 {plan}")

    if problems:
        for p in problems:
            print(f"  * {p}")
    else:
        print("  (无)")
    if infos:
        print()
        print("  说明（非异常）：")
        for i in infos:
            print(f"  - {i}")

    print()
    print(f"每次运行实际调用过的工具（用于看『它到底在用什么』）：")
    for r in rows:
        tools = ", ".join(f"{k.split('__')[-1]}×{v}" for k, v in sorted(r["per_tool"].items()))
        print(f"  {r['file']:<26} {tools}")

    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
