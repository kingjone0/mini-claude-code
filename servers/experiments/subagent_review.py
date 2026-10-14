"""看一次 ④ 层运行的产物：父流口径 + 子代理一格账 + 子代理说了什么。

用法：
    python servers/experiments/subagent_review.py <label> [--full-text]

存在的理由：`subagent_ab_runner.py` 的 report 只给三臂的中位数/极差，
而 §7 要写的是「这一次里子代理报了哪几条根因、父 agent 有没有复核、它有没有被
回合上限截断」—— 那些是定性证据，必须在原始产物里逐次看。
产物是 `out/agent-<label>.json`。

字段名是踩过坑才记下来的（第一版全猜错了）：
  - 运行头在 `trace["run"]`，**不是** 顶层 `run`；
  - 事件里的文本字段叫 `preview`（**截断过的预览**），真实长度在同一条记录的 `chars` 里，
    **不是** `text`；
  - 子代理的最终报告在 `TaskNotificationMessage` 的 `fields.summary` 里（同样会被截断）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"

METRIC_KEYS = (
    "success", "baseline_failures", "final_failures", "input_tokens", "output_tokens",
    # P0 三口径：input_tokens == input_uncached（未缓存输入），另有 cache_read/cache_creation。
    # 真实 prompt 规模 = input_uncached + cache_read + cache_creation。
    "input_uncached", "cache_read", "cache_creation",
    "cost_usd", "elapsed_s", "turns", "tool_calls_total", "tool_calls_failed",
    "test_files_touched", "subagent_spawn_calls", "subagent_adopted",
    "subagent_tool_calls", "subagent_result_chars", "subagent_tokens_estimate",
    "task_messages_per_type",
)

TRUNC_MARK = "turn limit"


def norm_tools(d: dict | None) -> dict:
    return {k.replace("mcp__code-tools__", ""): v for k, v in (d or {}).items()}


def clip(s: str, n: int, full: bool) -> str:
    if full or len(s) <= n:
        return s
    return s[:n] + f" …（共 {len(s)} 字符，加 --full-text 看全）"


def brief(pattern: str) -> int:
    """每行一次运行：父层主指标 + 子代理一格账 + **有没有被回合上限截断**。

    截断这一列是 `subagent_ab_runner.py` 的 report 里**没有**的 ——
    而被截断的子代理交回的是半份报告，那会让"分工有没有用"整个问错（陷阱 41）。
    """
    files = sorted(OUT.glob(f"agent-{pattern}.json"))
    if not files:
        print(f"[没有产物] out/agent-{pattern}.json")
        return 1
    head = (f"{'label':28s} {'ok':>3s} {'in_unc':>7s} {'cache_rd':>9s} {'cache_cr':>9s} "
            f"{'out_tok':>7s} {'cost':>6s} "
            f"{'s':>6s} {'调用':>4s} {'spawn':>5s} {'采用':>4s} {'子调用':>6s} {'子报告':>7s} 状态")
    print(head)
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        m = d.get("metrics") or {}
        steps = d["trace"]["steps"]
        notes = [s for s in steps if s.get("type") == "task_message"
                 and s.get("task_msg") == "TaskNotificationMessage"]
        trunc = any(TRUNC_MARK in str((s.get("fields") or {}).get("summary") or "")
                    for s in notes)
        state = "—" if not notes else ("⚠️截断" if trunc else "完整")
        # P0 前的旧产物没有三个新键：input_uncached 回落 input_tokens（同口径），
        # cache_read/creation 回落 None → 显示为空，别用 0 冒充「没数据」。
        in_unc = m.get("input_uncached")
        if in_unc is None:
            in_unc = m.get("input_tokens")
        print(f"{p.stem[6:]:28s} {str(m.get('success'))[:3]:>3s} "
              f"{in_unc if in_unc is not None else '-':>7} "
              f"{m.get('cache_read') if m.get('cache_read') is not None else '-':>9} "
              f"{m.get('cache_creation') if m.get('cache_creation') is not None else '-':>9} "
              f"{m.get('output_tokens') or 0:>7} "
              f"{(m.get('cost_usd') or 0):>6.3f} {(m.get('elapsed_s') or 0):>6.0f} "
              f"{m.get('tool_calls_total') or 0:>4} {m.get('subagent_spawn_calls') or 0:>5} "
              f"{str(m.get('subagent_adopted'))[:1]:>4s} "
              f"{m.get('subagent_tool_calls') or 0:>6} "
              f"{m.get('subagent_result_chars') or 0:>7} {state}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if sys.argv[1] == "--brief":
        # 默认看硬靶场；要别的靶场就 --brief <通配>，如 --brief subagent-ab-A-*
        pat = sys.argv[2] if len(sys.argv) > 2 else "subagent-ab-hard-*"
        return brief(pat)
    label = sys.argv[1]
    full = "--full-text" in sys.argv
    path = OUT / f"agent-{label}.json"
    if not path.is_file():
        print(f"[没有产物] {path}")
        return 1

    d = json.loads(path.read_text(encoding="utf-8"))
    m = d.get("metrics") or {}
    tr = d.get("trace") or {}
    run = tr.get("run") or {}
    steps = tr.get("steps") or []

    print(f"=== {label} ===")
    if run:
        print(f"靶场 {run.get('fixture')}　HEAD {run.get('fixture_head')}　"
              f"脏 {run.get('fixture_dirty')}　起点 {run.get('started_at')}")
    term = tr.get("termination") or {}
    print(f"结果 {term.get('outcome')}　基线退出码 {term.get('baseline_exit_code')}　"
          f"终局退出码 {term.get('final_exit_code')}　"
          f"改过的文件 {term.get('changed_files')}")

    print("--- 父流口径（子代理的用量不在这里）---")
    for k in METRIC_KEYS:
        if k in m:
            v = m[k]
            if isinstance(v, dict):
                v = json.dumps(v, ensure_ascii=False)
            print(f"  {k:26s} {v}")
    # P0：真实 prompt 规模 = 未缓存 + 缓存读取 + 缓存写入。
    # 旧产物没有新键时不猜，直接说明缺哪个。
    if "input_uncached" in m or "cache_read" in m:
        parts = [m.get("input_uncached"), m.get("cache_read"), m.get("cache_creation")]
        if all(isinstance(x, int) for x in parts):
            print(f"  {'prompt_total(三口径之和)':26s} {sum(parts)}")
    print(f"  {'工具分布':26s} {json.dumps(norm_tools(m.get('tool_calls_per_tool')), ensure_ascii=False)}")

    # 强制派一次时不写进 metrics 的那两个字段在 trace.run 里
    for k in ("subagent_forced", "arm"):
        if k in run:
            print(f"  {k:26s} {run[k]}")

    texts = [s for s in steps if s.get("type") == "subagent_text"]
    subs = [s for s in steps if s.get("type") == "subagent_tool_call"]
    notes = [s for s in steps if s.get("type") == "task_message"
             and s.get("task_msg") == "TaskNotificationMessage"]
    print(f"--- 子代理：{len(subs)} 次工具调用 / {len(texts)} 段文本 / {len(notes)} 条通知 ---")
    for s in subs:
        print(f"  [子工具 {s.get('seq')}] {s.get('tool_name') or s.get('name')}")
    for s in texts:
        print(f"  [文本 seq={s.get('seq')} chars={s.get('chars')}] "
              f"{clip(s.get('preview') or '', 500, full)}")
    for s in notes:
        f = s.get("fields") or {}
        summ = str(f.get("summary") or "")
        flag = "⚠️ 被回合上限截断" if TRUNC_MARK in summ else "完整"
        print(f"  [通知] status={f.get('status')}　{flag}")
        print(f"         {clip(summ, 1200, full)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
