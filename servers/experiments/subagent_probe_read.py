"""subagent_probe_read.py —— 把 subagent_probe.py 的产物读成人能看的样子。

探针产物是 JSON，但 `parent_tool_use_id`、Task* 消息、子代理回报全混在几百条
`SystemMessage` 里，直接翻文件看不出来。这个脚本只做一件事：**把非 System 的消息
按顺序展开**，并把 Task* 消息自带的字段全打出来（那些类的字段 SDK 文档里没写）。

用法：
    python servers/experiments/subagent_probe_read.py out/subagent-probe-agent.json
    python servers/experiments/subagent_probe_read.py out/subagent-probe-task.json --text-only
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from agent.config import force_utf8_output  # noqa: E402


def clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + f" …(+{len(s) - n})"


def main() -> int:
    force_utf8_output()
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not argv:
        print(__doc__)
        return 2
    path = Path(argv[0])
    if not path.is_absolute():
        path = HERE / path
    data = json.loads(path.read_text(encoding="utf-8"))
    summary, messages = data["summary"], data["messages"]

    print(f"══ {path.name} ══")
    print(f"靶场 {summary['fixture']}")
    print(f"spawn_tool={summary['spawn_tool']}  forward_subagent_text={summary['forward_subagent_text']}")
    print(f"P1 父层调用的工具：{summary.get('P1_parent_tool_names')}  spawn={summary['P1_spawn_called']} 次")
    print(f"P2 带 parent 的消息 {summary['P2_nested_messages']} 条 / 工具 {summary['P2_nested_tool_uses']} 次"
          f"（MCP {summary['P2_nested_mcp_calls']}）→ {summary['P2_nested_tool_names']}")
    print(f"P3 转发的子代理文本 {summary['P3_subagent_text_forwarded']} 个")
    print(f"共 {summary['message_count']} 条消息")
    print()

    text_only = "--text-only" in flags
    wide = "--wide" in flags
    for r in messages:
        if r["cls"] == "SystemMessage":
            continue
        pid = (r.get("parent_tool_use_id") or "")[-12:]
        head = f"[{r['i']:>3}] {r['cls']:<24} parent=…{pid or '-':<12}"
        blocks = r.get("blocks") or []
        if not blocks:
            # ResultMessage 把字段直接放在 rec 顶层，其它未知类型放在 rec["fields"]。
            fields = r.get("fields")
            if not fields:
                fields = {k: v for k, v in r.items()
                          if k not in ("i", "cls", "parent_tool_use_id", "blocks", "fields")}
            if fields:
                # Task* / ResultMessage 等：把字段压缩成一行
                keys = {k: v for k, v in fields.items()
                        if k in ("task_id", "tool_use_id", "status", "description", "summary",
                                 "subagent_type", "task_type", "last_tool_name", "usage",
                                 "num_turns", "duration_ms", "is_error", "subtype", "result",
                                 "total_cost_usd", "output_file", "message", "data", "content")}
                print(f"{head} {json.dumps(keys, ensure_ascii=False)[:1200]}")
            else:
                print(head)
            continue
        for b in blocks:
            t = b.get("type")
            if t == "text":
                print(f"{head} TEXT  {clip(b.get('text', ''), 3000 if wide else 1200)}")
            elif t == "thinking":
                print(f"{head} THINK {b.get('len')} 字符")
            elif t == "tool_use":
                print(f"{head} USE   {b.get('name')}  {json.dumps(b.get('input'), ensure_ascii=False)[:600]}")
            elif t == "tool_result":
                err = " [ERROR]" if b.get("is_error") else ""
                print(f"{head} RESULT{err} {b.get('chars')} 字符: {clip(b.get('text', ''), 900 if wide else 400)}")
            else:
                print(f"{head} {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
