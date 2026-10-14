"""t5 冒烟：从 trace 里提出真实的 token / 回合 / 工具调用口径。

**为什么要另写一个提取器**：`coding_agent.py` 在「撞上 40 回合上限」这条错误路径上，
`events` 是空的 ⇒ `summarize(events)` 全是 0，落进 `usage.jsonl` 的也是 null。
也就是说 **`usage.jsonl` 这条线在 error 路径上拿不到用量**（这本身是 t5 要报的接线问题）。
幸运的是**独立 pytest 判定不依赖它**，而 trace 里逐回合 usage 是齐的 ⇒ 从 trace 取数。

口径说明（与预注册 §10.1 一致，务必写明是哪一层）：
- `input_uncached`  = Σ 逐回合 `usage.input_tokens`（**去重后**，见 trace caveat 第 4 条：
  连续两条带完全相同 usage 的助手消息属同一次 API 调用的流式分片，只记第一份）
- `cache_read` / `cache_creation` = Σ 对应字段
- `output_tokens`   = Σ 逐回合 `usage.output_tokens`
- 这三个是**父层**口径。baseline 臂没有子代理/scout ⇒ 端到端 == 父层（本脚本会显式核对）。

用法：python t5_usage_from_trace.py <agent-*.json 路径>
"""

from __future__ import annotations

import collections
import json
import os
import sys

PREREG_NOTE = ("本脚本的三个 token 口径与预注册 §10.1 对齐；其中 input_uncached 是**主指标**。"
               "来源是 trace 逐回合 usage，不是 usage.jsonl（后者在 error 路径上为 null）。")


def main():
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    tr = doc["trace"]
    steps = tr["steps"]

    turns = [s for s in steps if s.get("type") == "turn"]
    # ⚠ trace 里工具调用的 type 是 **`tool_call`**（不是 tool_use / tool）——
    # 用错名字会得到「0 次工具调用」而看起来像 agent 没干活（实测踩过）。
    tools = [s for s in steps if s.get("type") == "tool_call"]
    tool_counter = collections.Counter(s.get("tool") or "(未知)" for s in tools)

    # 去重：连续两条 assistant 消息带完全相同的 usage ⇒ 同一个 API 调用的分片，只记第一份
    deduped = []
    prev = None
    for s in turns:
        u = s.get("usage") or {}
        key = (u.get("input_tokens"), u.get("cache_read_input_tokens"),
               u.get("cache_creation_input_tokens"))
        if prev is not None and key == prev:
            continue
        deduped.append(s)
        prev = key

    def total(field, rows):
        return sum(int(((r.get("usage") or {}).get(field)) or 0) for r in rows)

    raw_in = total("input_tokens", turns)
    de_in = total("input_tokens", deduped)
    de_cr = total("cache_read_input_tokens", deduped)
    de_cc = total("cache_creation_input_tokens", deduped)
    de_out = total("output_tokens", deduped)

    term = tr.get("termination") or {}
    run = tr.get("run") or {}

    # 耗时：从 run.started_at 到文件 mtime 不可靠；用 trace 最后一 step 的 t_ms 与 run.started_at
    last_t = max((s.get("t_ms") or 0) for s in steps) if steps else 0

    out = {
        "source": os.path.abspath(path),
        "label": run.get("label"),
        "instance_mode": run.get("instance_mode"),
        "instance_repo_root": run.get("instance_repo_root"),
        "instance_problem_file": run.get("instance_problem_file"),
        "instance_task_sha256": run.get("instance_task_sha256"),
        "steps_total": len(steps),
        "turn_records_raw": len(turns),
        "turn_records_deduped": len(deduped),
        "input_uncached_raw": raw_in,
        "input_uncached": de_in,
        "cache_read": de_cr,
        "cache_creation": de_cc,
        "output_tokens": de_out,
        "tool_calls_total": sum(tool_counter.values()),
        "tool_calls_per_tool": dict(tool_counter),
        "last_step_t_ms": last_t,
        "termination": {
            "outcome": term.get("outcome"),
            "agent_stop": term.get("agent_stop"),
            "baseline_exit_code": term.get("baseline_exit_code"),
            "final_exit_code": term.get("final_exit_code"),
            "changed_files": term.get("changed_files"),
            "test_files_touched": term.get("test_files_touched"),
            "agent_error": term.get("agent_error"),
        },
        "prereg_note": PREREG_NOTE,
    }
    print("=" * 78)
    print(f"t5 trace 用量提取：{path}")
    print("=" * 78)
    for k in ("label", "instance_mode", "instance_repo_root", "instance_problem_file",
              "instance_task_sha256", "steps_total", "turn_records_raw",
              "turn_records_deduped", "input_uncached_raw", "input_uncached",
              "cache_read", "cache_creation", "output_tokens", "tool_calls_total",
              "last_step_t_ms"):
        print(f"  {k:<26} = {out[k]}")
    print(f"  tool_calls_per_tool        = {out['tool_calls_per_tool']}")
    print(f"  termination                = {json.dumps(out['termination'], ensure_ascii=False)}")
    print()
    print(f"  Σ三个口径 = input_uncached({de_in}) + cache_read({de_cr})"
          f" + cache_creation({de_cc}) = {de_in + de_cr + de_cc}  （≈真实 prompt 规模）")

    dest = os.path.join(os.path.dirname(os.path.abspath(path)), "t5_usage_from_trace.json")
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n  落盘：{dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())