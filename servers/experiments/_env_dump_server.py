"""一次性探针：把 Claude Code 拉起 MCP server 时**真实传给它的环境**dump 出来。

目的：与其一个个猜哪个变量可用（`CLAUDE_PROJECT_DIR`？`.`？相对路径？），
不如直接问 —— 一次就把「有哪些变量」「cwd 是什么」全拿到。

⚠️ 只写**键名**和少量白名单前缀的值，绝不 dump 值。
   环境里带着 `ANTHROPIC_AUTH_TOKEN`，全量 dump 会把它写进仓库。
"""

import json
import os
import sys
from pathlib import Path

# 只对这些前缀的值做记录（都是路径类，不含密钥）
SAFE_PREFIXES = ("CLAUDE", "MCP", "PWD", "CD", "HOME", "USERPROFILE", "APPDATA", "TEMP", "TMP")

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("mcp-env-dump.json")

payload = {
    "cwd": os.getcwd(),
    "all_env_keys": sorted(os.environ.keys()),
    "safe_values": {
        k: v for k, v in os.environ.items() if k.upper().startswith(SAFE_PREFIXES)
    },
    "stdin_is_tty": sys.stdin.isatty(),
    "stdout_is_tty": sys.stdout.isatty(),
}

out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

# dump 完之后要**真的把 server 跑起来**。
#
# 早先的版本在 dump 后就 sys.exit(0)，结果 dump 文件根本没被写出来 ——
# 那说明客户端压根没 spawn 这个进程（而不是"spawn 了但写失败"）。
# 现在它是个功能完整的 MCP server：只要客户端连上了，dump 必定存在。
# 于是「有没有 dump」本身就成了一条判据：连没连上，一看便知。
from mcp.server.mcpserver import MCPServer  # noqa: E402

server = MCPServer(name="dumper", version="1.0.0")


@server.tool()
def ping() -> str:
    """连通性测试，返回 pong。"""
    return "pong"


if __name__ == "__main__":
    server.run()
