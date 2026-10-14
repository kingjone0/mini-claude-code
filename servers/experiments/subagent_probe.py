"""subagent_probe.py —— 探针：SDK 的 subagent（Agent 工具）通道到底通不通。

━━ 为什么先探不用直接实现 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

`coding_agent.py` 的整个安全模型建立在 **`tools=[]`（禁用全部内置工具）** 上，
而 subagent 恰恰是**一个内置工具**（`types.py:2212`：`agents` 是「invokable via
the Agent tool」）。把 `tools=[]` 改成 `tools=["Agent"]` 是在这份代码里第一次
**主动放开一个内置工具** —— 在动主循环之前，先把这条通道单独验证一遍。

本脚本只回答四个"能不能"，不建实验、不下结论：

  P1  spawn 工具的真名是 `Agent` 还是 `Task`？（两种都试，看哪个能派出去）
  P2  子代理拿得到 MCP 工具吗？（它的 tool_use 会带 `parent_tool_use_id` 吗）
  P3  父 agent 看得到子代理的什么？（只有最终总结，还是全量过程）
  P4  `forward_subagent_text=True` 会不会改变发出的消息流？

用法：
    python servers/experiments/subagent_probe.py --spawn-tool Agent
    python servers/experiments/subagent_probe.py --spawn-tool Task --forward-text
产物：servers/experiments/out/subagent-probe-<标签>.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

import coding_agent as ca  # noqa: E402
from agent.config import force_utf8_output  # noqa: E402

from claude_agent_sdk import (  # noqa: E402
    AgentDefinition, AssistantMessage, ClaudeAgentOptions, ResultMessage,
    TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock, UserMessage, query,
)

OUT_DIR = HERE / "out"
FIXTURE_NAME = "more-itertools-multi"

# 父 agent 的题面：**逼它必须派子代理**（它自己不被允许读那个文件 —— 见 spawn_tools）。
PARENT_PROMPT = """你要回答一个问题：`more_itertools/recipes.py` 里一共有多少个**顶层** `def ` 定义？

硬约束：**你自己不能用 read_file / search_code 去读那个文件** —— 必须派一个子代理去做。

请用 subagent（Agent 工具）派一个子代理，让它去数，然后把它回报的数字告诉我。
如果子代理机制不可用，就直接回答「subagent 不可用」并说明你看到的错误。"""

SUBAGENT_DESC = "统计代码文件里的顶层函数定义个数（只读）"
SUBAGENT_PROMPT = """你是一个**只读**子代理。

用 read_file 读派你的消息里指定的那个文件，数出里面**顶层**（行首没有缩进的）
`def ` 的个数，然后**只回报一个数字和一行依据**（文件路径 + 行号区间即可）。

不要修改任何文件。不要读别的文件。"""

READ_TOOLS = [
    f"mcp__{ca.SERVER_NAME}__list_files",
    f"mcp__{ca.SERVER_NAME}__search_code",
    f"mcp__{ca.SERVER_NAME}__read_file",
]

# spawn 工具的候选真名 —— SDK 文档里 "Agent tool" 与 "Task-spawned" 两种说法并存，
# 所以**两个都算**，并且把父层实际调用的工具名全部记下来（不靠猜）。
SPAWN_NAMES = ("Agent", "Task")


def _safe(v, depth: int = 0):
    """把任意对象压成 JSON 可序列化的形式（深度/长度都设限，避免把整个会话塞进产物）。"""
    if depth > 3:
        return f"<{type(v).__name__}>"
    if v is None or isinstance(v, (int, float, bool)):
        return v
    if isinstance(v, str):
        return v if len(v) <= 4000 else v[:4000] + f"…(+{len(v) - 4000})"
    if isinstance(v, dict):
        return {str(k): _safe(x, depth + 1) for k, x in list(v.items())[:40]}
    if isinstance(v, (list, tuple)):
        return [_safe(x, depth + 1) for x in v[:40]]
    if hasattr(v, "__dict__"):
        try:
            return {k: _safe(x, depth + 1) for k, x in vars(v).items() if not k.startswith("_")}
        except TypeError:
            pass
    return f"<{type(v).__name__}>"


def dump_message(msg, idx: int) -> dict:
    rec: dict = {
        "i": idx,
        "cls": type(msg).__name__,
        "parent_tool_use_id": getattr(msg, "parent_tool_use_id", None),
    }
    if isinstance(msg, AssistantMessage):
        rec["model"] = getattr(msg, "model", None)
        blocks = []
        for b in msg.content:
            if isinstance(b, TextBlock):
                blocks.append({"type": "text", "text": b.text})
            elif isinstance(b, ToolUseBlock):
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
            elif isinstance(b, ThinkingBlock):
                blocks.append({"type": "thinking", "len": len(getattr(b, "thinking", "") or "")})
            elif isinstance(b, ToolResultBlock):
                blocks.append({"type": "tool_result", "id": getattr(b, "tool_use_id", None)})
            else:
                blocks.append({"type": type(b).__name__})
        rec["blocks"] = blocks
    elif isinstance(msg, UserMessage):
        blocks = []
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for b in content:
                if isinstance(b, ToolResultBlock):
                    c = b.content
                    if isinstance(c, dict):
                        texts = [x.get("text", "") for x in c.get("content", [])
                                 if isinstance(x, dict)]
                    elif isinstance(c, list):
                        texts = [x.get("text", "") for x in c if isinstance(x, dict)]
                    else:
                        texts = [str(c)]
                    joined = "\n".join(texts)
                    blocks.append({"type": "tool_result", "id": getattr(b, "tool_use_id", None),
                                   "is_error": getattr(b, "is_error", None),
                                   "chars": len(joined), "text": joined[:2000]})
                else:
                    blocks.append({"type": type(b).__name__})
        else:
            blocks.append({"type": "text", "text": str(content)[:400]})
        rec["blocks"] = blocks
    elif isinstance(msg, ResultMessage):
        for k in ("num_turns", "duration_ms", "is_error", "total_cost_usd", "usage",
                  "subtype", "result"):
            v = getattr(msg, k, None)
            if v is not None:
                rec[k] = v if not isinstance(v, str) else v[:2000]
    else:
        # TaskStartedMessage / TaskProgressMessage / TaskUpdatedMessage /
        # TaskNotificationMessage / SystemMessage … —— 这些类的字段 SDK 文档里没写，
        # 直接把它们自带的字段全部记下来（只记类名等于把证据丢了）。
        try:
            rec["fields"] = {k: _safe(v) for k, v in vars(msg).items() if not k.startswith("_")}
        except TypeError:
            rec["fields"] = None
    return rec


async def main() -> int:
    force_utf8_output()
    argv = sys.argv[1:]
    spawn_tool = argv[argv.index("--spawn-tool") + 1] if "--spawn-tool" in argv else "Agent"
    forward_text = "--forward-text" in argv
    label = argv[argv.index("--label") + 1] if "--label" in argv else (
        f"{spawn_tool.lower()}{'-fwd' if forward_text else ''}")
    max_turns = int(argv[argv.index("--max-turns") + 1]) if "--max-turns" in argv else 12

    ca.FIXTURE = ca.resolve_fixture(FIXTURE_NAME)
    ca.allow_write = False
    env, key_source = ca.load_endpoint_env()

    spawn_tools = [spawn_tool]
    agents = {
        "counter": AgentDefinition(
            description=SUBAGENT_DESC,
            prompt=SUBAGENT_PROMPT,
            tools=READ_TOOLS,
            mcpServers=[ca.SERVER_NAME],
            maxTurns=6,
        )
    }

    options = ClaudeAgentOptions(
        cli_path=ca.CLI_PATH,
        mcp_servers={
            ca.SERVER_NAME: {
                "type": "stdio",
                "command": ca.PYTHON,
                "args": [str(ca.SERVER_SCRIPT)],
                "env": {"AGENT_REPO_ROOT": str(ca.FIXTURE),
                        ca.REPO_MAP_ENV_KEY: "summary"},
            }
        },
        # ⚠️ 第一次主动放开一个**内置**工具。只放 spawn 工具本身。
        tools=spawn_tools,
        allowed_tools=spawn_tools + READ_TOOLS,
        # 纵深防御要减掉被放开的那个名字，否则自己把自己否掉。
        disallowed_tools=[t for t in ca.DISALLOWED_TOOLS if t not in spawn_tools],
        permission_mode=ca.PERMISSION_MODE,
        strict_mcp_config=True,
        setting_sources=ca.SETTING_SOURCES,
        env=env,
        agents=agents,
        forward_subagent_text=forward_text,
        max_turns=max_turns,
        stderr=lambda line: print(f"[cli stderr] {line}", file=sys.stderr, flush=True),
    )

    print(f"靶场：{ca.FIXTURE}")
    print(f"spawn 工具：{spawn_tools}   forward_subagent_text={forward_text}   密钥来源={key_source}")
    print(f"子代理 counter 的工具：{READ_TOOLS}")
    print("--- 消息流 ---")

    messages: list[dict] = []
    result: dict | None = None
    idx = 0
    async for msg in query(prompt=PARENT_PROMPT, options=options):
        idx += 1
        rec = dump_message(msg, idx)
        messages.append(rec)
        if isinstance(msg, ResultMessage):
            result = rec
        names = [b.get("name") or b.get("type") for b in rec.get("blocks", [])]
        pid = rec.get("parent_tool_use_id")
        print(f"  [{idx:02d}] {rec['cls']:<16} parent={pid or '-':<24} {names}", flush=True)

    # ── 汇总：四个问题各自的答案 ────────────────────────────────────────
    tool_uses = [(r["i"], r["parent_tool_use_id"], b["name"], b.get("input"))
                 for r in messages for b in r.get("blocks", []) if b.get("type") == "tool_use"]
    nested = [t for t in tool_uses if t[1]]
    nested_mcp = [t for t in nested if str(t[2]).startswith(f"mcp__{ca.SERVER_NAME}__")]
    # ⚠️ 第一次跑这里写的是 `t[2] == spawn_tool`，于是「真名是 Agent 而我把
    # --spawn-tool 传成 Task」时读数会是 0 —— 一个**看起来像结论的假阴性**。
    # 改成：父层实际调用的名字全部记录，spawn 次数按候选名单算。
    parent_tool_names = [t[2] for t in tool_uses if t[1] is None]
    spawn_calls = [t for t in tool_uses if t[1] is None and t[2] in SPAWN_NAMES]
    subagent_texts = [b["text"] for r in messages if r.get("parent_tool_use_id")
                      for b in r.get("blocks", []) if b.get("type") == "text"]

    summary = {
        "fixture": str(ca.FIXTURE),
        "spawn_tool": spawn_tool,
        "forward_subagent_text": forward_text,
        "key_source": key_source,
        "parent_prompt": PARENT_PROMPT,
        "subagent_definition": {"name": "counter", "description": SUBAGENT_DESC,
                                "prompt": SUBAGENT_PROMPT, "tools": READ_TOOLS,
                                "mcpServers": [ca.SERVER_NAME]},
        "P1_spawn_called": len(spawn_calls),
        "P1_parent_tool_names": parent_tool_names,
        "P1_spawn_calls": [{"i": i, "name": n, "input": inp} for i, _, n, inp in spawn_calls],
        "P2_nested_messages": sum(1 for r in messages if r.get("parent_tool_use_id")),
        "P2_nested_tool_uses": len(nested),
        "P2_nested_tool_names": [t[2] for t in nested],
        "P2_nested_mcp_calls": len(nested_mcp),
        "P3_subagent_text_forwarded": len(subagent_texts),
        "P3_forwarded_text_sample": subagent_texts[:3],
        "result": result,
        "message_count": len(messages),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"subagent-probe-{label}.json"
    out.write_text(json.dumps({"summary": summary, "messages": messages},
                              ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n--- 四个问题 ---")
    print(f"  P1 父层实际调用的工具：{parent_tool_names}")
    print(f"     spawn 工具（{'/'.join(SPAWN_NAMES)}）被调用：{len(spawn_calls)} 次")
    print(f"  P2 带 parent_tool_use_id 的消息：{summary['P2_nested_messages']} 条；"
          f"其中工具调用 {len(nested)} 次（MCP {len(nested_mcp)} 次）")
    print(f"  P3 转发出来的子代理文本块：{len(subagent_texts)} 个")
    print(f"  产物：{out}  （{out.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
