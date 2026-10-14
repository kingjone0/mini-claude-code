"""env_probe_server.py —— 一次性探针（t1 用）：报出 MCP 子进程实际看到的环境变量。

目的：**先验证，再决定**。要确认 `AGENT_REPO_MAP_DEFAULT` 到底能不能到达
code_tools_server 子进程，不能靠猜 —— 方法是在子进程里直接把 os.environ 打出来。

⚠️ 这是 MCP server，stdout 是协议线：所有输出走 stderr（CLAUDE.md 陷阱 8/10）。

用法（由 env_probe.py 拉起，不需要手工跑）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

server = MCPServer(name="env-probe", version="1.0.0")

# ⚠️ 落盘判据，**不依赖模型的转述**。
# 第一版靠解析 SDK 消息里的 ToolResultBlock，结果 SDK 根本没把 tool_result 暴露出来
# （只给了 AssistantMessage 的 TextBlock）—— 于是所有 case 的 child_seen 都是 None，
# 看起来像"透传失败"，其实是**探针自己瞎了**。
# 教训与 CLAUDE.md 陷阱 4 同源：判定只能看落盘的原始事实，不能看模型的复述。
EVIDENCE_PATH = Path(
    os.environ.get("ENV_PROBE_EVIDENCE")
    or (Path(__file__).resolve().parent / "out" / "env-probe-child.jsonl")
)
CASE = os.environ.get("ENV_PROBE_CASE", "?")


def _dump(tag: str) -> None:
    keys = ["AGENT_REPO_ROOT", "AGENT_REPO_MAP_DEFAULT", "AGENT_TOOLS_LOG",
            "ANTHROPIC_MODEL", "DOTENV_PATH"]
    print(f"[env-probe:{tag}] pid={os.getpid()} cwd={os.getcwd()}", file=sys.stderr, flush=True)
    for k in keys:
        print(f"[env-probe:{tag}] {k}={os.environ.get(k)!r}", file=sys.stderr, flush=True)


@server.tool()
def ping(x: str = "") -> str:
    """返回子进程里 os.environ 的真实取值（用于验证环境透传）。"""
    _dump("tool-call")
    value = os.environ.get("AGENT_REPO_MAP_DEFAULT")
    # 权威证据落盘：子进程自己写的，不经模型转述
    with EVIDENCE_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "case": CASE,
            "pid": os.getpid(),
            "AGENT_REPO_ROOT": os.environ.get("AGENT_REPO_ROOT"),
            "AGENT_REPO_MAP_DEFAULT": value,
            "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL"),
        }, ensure_ascii=False) + "\n")
    return f"AGENT_REPO_MAP_DEFAULT={value}\nAGENT_REPO_ROOT={os.environ.get('AGENT_REPO_ROOT')}"


if __name__ == "__main__":
    _dump("boot")
    server.run()