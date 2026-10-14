"""实验专用 server：在 tool handler 里**故意往 stdout 写东西**。

⚠️ 这是实验副本，不是生产 server。不要注册给任何客户端。

它验证的是：mcp 2.x 的 stdio transport 启动之后，fd 1 被 `_claim_fd` 抢占
（`mcp/server/stdio.py:161-179`：fd 0 → null，fd 1 → stderr），
所以 handler 里的杂输出**够不到协议线**。

两种写法都试，是为了区分「Python 对象层被兜住」和「fd 本身被换掉」：

    print(...)          走 sys.stdout 对象 → 它的 buffer 指向 fd 1
    os.write(1, ...)    绕过一切 Python 对象，直接写文件描述符 1

如果两者都出现在 stderr 里，说明被换掉的是 **fd 本身**，不是 Python 层的包装。
"""

import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

STUDY_ROOT = Path(os.environ.get("STUDY_ROOT", "."))

server = MCPServer(name="stray-in-handler", version="1.0.0")


def log(msg: str) -> None:
    print(f"[stray-server] {msg}", file=sys.stderr, flush=True)


@server.tool()
def word_count(text: str) -> str:
    """在返回之前，故意往 stdout 写两行杂东西。"""
    # ① Python 对象层
    print("!!! STRAY-VIA-PRINT !!!", flush=True)
    # ② 绕过 sys.stdout，直接写 fd 1
    os.write(1, b"!!! STRAY-VIA-OSWRITE-FD1 !!!\n")

    # 辅助证据：fd 1 和 fd 2 现在是不是指向同一个东西
    log(f"fstat(1) = {os.fstat(1)}")
    log(f"fstat(2) = {os.fstat(2)}")
    log(f"fd1 与 fd2 同源？ {os.fstat(1) == os.fstat(2)}")

    return f"「{text}」→ 字符数 {len(text)}，词数 {len(text.split())}"


@server.tool()
def read_study_file(relpath: str) -> str:
    """与生产 server 同实现。

    放进来只是为了让客户端 B（hello_agent_stdio.py）能原样跑完它的三步提示词 ——
    本实验要证明的是「handler 里写 stdout 之后，真实客户端仍能正常通信」，
    客户端那边一行都不该改。
    """
    root = STUDY_ROOT.resolve()
    target = (root / relpath).resolve()
    if not target.is_relative_to(root):
        log(f"拒绝越界读取：{relpath!r} -> {target}")
        return f"拒绝：{relpath} 越出 {root}（本工具只允许读该目录之内）"
    if not target.is_file():
        return f"不存在：{relpath}"
    text = target.read_text(encoding="utf-8", errors="replace")
    return text[:20000] + (f"\n…（已截断，原文共 {len(text)} 字符）" if len(text) > 20000 else "")


if __name__ == "__main__":
    log("启动：transport=stdio（handler 内含 stdout 杂输出）")
    server.run()
