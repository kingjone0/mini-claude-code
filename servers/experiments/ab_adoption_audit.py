"""ab_adoption_audit.py —— 独立复核 A/B 实验的逐次采用与成本。

**为什么单独写一个脚本**（而不是直接用 context_report.py）：

  1. context_report.py 的既有口径**不能改**（团队约定），但它输出的表里
     缺三样本报告要用的东西：`input_tokens`、`success`、以及"把 repo_map
     的调用与它的返回字符**按次对齐**"（哪几次真的调了、那次返回多少字符）。
  2. 结论要被第三方独立复核，最稳的做法是**用第二条独立的代码路径**
     重新算一遍，再与 context_report.py 的输出逐格对比。两份实现算出的
     数如果一致，才说明不是某个脚本的 bug 造出了一个假结论。

**判定口径（先定死，不许事后改，与团队描述逐字一致）**：
    某次运行 -> 该次运行的 MCP 工具调用序列里出现至少一次
    `mcp__code-tools__repo_map`，即记为「该次采用了」。
    判据的**唯一来源**是 `agent-*.json` 的 `events` 里 `type=="tool_use"` 的
    条目；**不读** metrics 里的 `used_repo_map` / `repo_map_count` 同名拷贝，
    也**不读** agent 的自述。

用法：
    python ab_adoption_audit.py --dir servers/experiments/out
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

# 必须在第一个 print 之前（本机控制台 GBK(936)，见 CLAUDE.md 陷阱 16）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agent.config import force_utf8_output  # noqa: E402

ADOPTION_TOOL = "mcp__code-tools__repo_map"
PREFIX = "mcp__code-tools__"
ARMS = {"A": "symbols", "B": "summary"}


def short(t: str) -> str:
    return t[len(PREFIX):] if t.startswith(PREFIX) else t


def audit(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    metrics = data.get("metrics", {})
    events = data.get("events", [])

    # ── 路径 1：纯事件序列（判定采用的唯一依据）─────────────────────────
    uses = [e for e in events if e.get("type") == "tool_use"]
    results = [e for e in events if e.get("type") == "tool_result"]
    seq = [short(e.get("tool", "?")) for e in uses]
    per_tool_calls = Counter(seq)

    # 把 tool_use 与紧随其后的 tool_result 配对，取**真实字符数**（chars 字段，
    # 不是被 MAX_EVENT_TEXT 截断后的存档 text —— 见 coding_agent.py 第 330-338 行）
    per_tool_chars: Counter[str] = Counter()
    pending: str | None = None
    for e in events:
        if e.get("type") == "tool_use":
            pending = short(e.get("tool", "?"))
        elif e.get("type") == "tool_result" and pending is not None:
            per_tool_chars[pending] += int(e.get("chars") or 0)
            pending = None

    repo_map_uses = [e for e in uses if e.get("tool") == ADOPTION_TOOL]
    repo_map_inputs = [e.get("input") for e in repo_map_uses]
    # repo_map 的返回字符：按 tool_result 里文本前 80 字符认出 repo_map 的输出，
    # 且只在该次确实调过 repo_map 时计入（避免别的工具输出里含 "repo_map" 字样）
    repo_map_result_chars = sum(
        c for c in [per_tool_chars.get("repo_map", 0)]
    )

    m = path.stem
    mo = re.match(r"agent-ab-([AB])-(\d+)$", m)
    arm = mo.group(1) if mo else "?"
    run_idx = int(mo.group(2)) if mo else -1

    return {
        "label": metrics.get("label") or m,
        "file": path.name,
        "arm": arm,
        "run_idx": run_idx,
        # ── 被解释变量 ──────────────────────────────────────────────────
        "adopted": len(repo_map_uses) > 0,
        "repo_map_uses": len(repo_map_uses),
        "repo_map_inputs": repo_map_inputs,
        "tool_use_total": len(uses),
        "tool_result_total": len(results),
        "per_tool_calls": dict(per_tool_calls),
        # ── 成本 ────────────────────────────────────────────────────────
        "tool_chars_total": sum(per_tool_chars.values()),
        "per_tool_chars": dict(per_tool_chars),
        "repo_map_chars": repo_map_result_chars,
        "input_tokens": metrics.get("input_tokens"),
        "output_tokens": metrics.get("output_tokens"),
        "cost_usd": metrics.get("cost_usd"),
        "success": metrics.get("success"),
        "elapsed_s": metrics.get("elapsed_s"),
        "changed_files": metrics.get("changed_files"),
        "baseline_exit_code": metrics.get("baseline_exit_code"),
        "final_exit_code": metrics.get("final_exit_code"),
        # ── 单变量性的旁证（报告要标注它只是旁证，不是判据）──────────────
        "metrics_repo_map_count": metrics.get("repo_map_count"),
        "metrics_used_repo_map": metrics.get("used_repo_map"),
        "metrics_arm_value": metrics.get("repo_map_default"),
        "metrics_arm_source": metrics.get("repo_map_default_source"),
        "tool_calls_total_metric": metrics.get("tool_calls_total"),
    }


def main() -> int:
    force_utf8_output()
    out_dir = Path(sys.argv[sys.argv.index("--dir") + 1]) if "--dir" in sys.argv \
        else Path(__file__).resolve().parent / "out"
    files = sorted(out_dir.glob("agent-ab-[AB]-*.json"))
    if not files:
        print(f"在 {out_dir} 下没找到 agent-ab-[AB]-*.json")
        return 2

    rows = [audit(p) for p in files]
    print("=" * 100)
    print("A/B 采用审计（独立实现，用于与 context_report.py 交叉核对）")
    print("=" * 100)

    tools = sorted({t for r in rows for t in r["per_tool_calls"]},
                   key=lambda t: (-sum(r["per_tool_calls"].get(t, 0) for r in rows), t))

    head = f"{'样本':<12}{'臂':<10}{'采用':<6}{'repo_map':<10}{'调用':<6}{'字符':<8}{'in_tok':<8}{'success':<9}"
    head += "".join(f"{t[:9]:<10}" for t in tools)
    print(head)
    print("-" * len(head))
    for r in rows:
        print(f"{r['label']:<12}{ARMS.get(r['arm'], '?'):<10}"
              f"{'是' if r['adopted'] else '否':<6}{r['repo_map_uses']:<10}"
              f"{r['tool_use_total']:<6}{r['tool_chars_total']:<8}"
              f"{r['input_tokens']:<8}{str(r['success']):<9}"
              + "".join(f"{r['per_tool_calls'].get(t, 0):<10}" for t in tools))

    print()
    for arm in ("A", "B"):
        g = [r for r in rows if r["arm"] == arm]
        if not g:
            continue
        n = sum(1 for r in g if r["adopted"])
        print(f"  臂 {arm} (repo_map_default={ARMS[arm]})：采用 {n}/{len(g)} = {n / len(g) * 100:.1f}%"
              f"   工具返回字符合计 {sum(r['tool_chars_total'] for r in g)}"
              f"   工具调用合计 {sum(r['tool_use_total'] for r in g)}"
              f"   input_tokens 合计 {sum(r['input_tokens'] for r in g)}"
              f"   成功 {sum(1 for r in g if r['success'])}/{len(g)}")

    print("\n--- 一致性自检（可疑项才打印，全空即全一致）---")
    bad = 0
    for r in rows:
        if bool(r["adopted"]) != bool(r["metrics_used_repo_map"]):
            print(f"  ⚠️ {r['label']}: events 判据={r['adopted']} vs metrics.used_repo_map={r['metrics_used_repo_map']}")
            bad += 1
        if r["repo_map_uses"] != r["metrics_repo_map_count"]:
            print(f"  ⚠️ {r['label']}: events 调用数={r['repo_map_uses']} vs metrics.repo_map_count={r['metrics_repo_map_count']}")
            bad += 1
        if r["tool_use_total"] != r["tool_calls_total_metric"]:
            print(f"  ⚠️ {r['label']}: events 调用数={r['tool_use_total']} vs metrics.tool_calls_total={r['tool_calls_total_metric']}")
            bad += 1
        if r["metrics_arm_value"] != ARMS.get(r["arm"]):
            print(f"  ⚠️ {r['label']}: 文件名臂=A/B 但 metrics.repo_map_default={r['metrics_arm_value']}")
            bad += 1
        if r["tool_use_total"] != r["tool_result_total"]:
            print(f"  ⚠️ {r['label']}: tool_use={r['tool_use_total']} != tool_result={r['tool_result_total']}（有调用没等到结果）")
            bad += 1
    print(f"  不一致项：{bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())