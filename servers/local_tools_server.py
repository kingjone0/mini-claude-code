"""独立进程 MCP server：stdio 传输，暴露 2 个工具。

启动命令（客户端 A 和客户端 B 用的是同一条，一行都不用改）：
    D:\\Anaconda3\\envs\\aicoding\\python.exe servers\\local_tools_server.py

⚠️ stdio 传输就是 stdin/stdout 上的 JSON-RPC。
   本文件所有日志一律走 stderr 和**文件**，绝不碰 stdout。
   （mcp 2.x 的 transport 会抢占 fd 1 做兜底，见 README「print 到 stdout」一节。）

日志为什么要落盘：stderr 能不能被看见取决于**父进程**——Claude Code 会吞掉
MCP server 的 stderr（CLAUDE.md 陷阱 8），所以 stderr 不能当唯一调试依据。
文件日志是第二条独立通道，不经过任何父进程。
"""

import logging
import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

# 工具能读的根目录。故意做成可配 —— 换根目录不用改代码。
# 缺省取当前目录（公开仓库不钉死任何本机私有路径）；用 STUDY_ROOT 覆盖。
STUDY_ROOT = Path(os.environ.get("STUDY_ROOT", "."))
MAX_CHARS = 20000

# ── 日志落盘 ────────────────────────────────────────────────────────────
# 位置沿用仓库既有的日志目录 servers/experiments/out/（证据和日志都落在那里）。
# ⚠️ 基准取 __file__ 而不是 cwd：cwd 由拉起 server 的客户端决定，
#    用 cwd 算相对路径会让日志漂到不可预期的位置。
# MCP_SERVER_LOG 可覆盖 —— 验证时指向临时文件，不去动真实日志。
# 本文件是**运行时状态**，不进仓库（.gitignore 里单独点了名）。
LOG_PATH = Path(
    os.environ.get("MCP_SERVER_LOG")
    or Path(__file__).resolve().parent / "experiments" / "out" / "local-tools-server.log"
)

LOGGER = logging.getLogger("local-tools")
LOGGER.setLevel(logging.DEBUG)
# 关键：不向 root 传播。root 上可能挂着别人装的 handler（甚至指向 stdout），
# 传播过去等于把协议线的处置权交出去。
LOGGER.propagate = False


def _install_handlers() -> None:
    """挂两条**互相独立**的通道：文件 + stderr。

    任一条被掐掉，另一条仍在 —— 这正是本次改造的目的。
    """
    LOGGER.handlers.clear()

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s pid=%(process)d %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        LOGGER.addHandler(file_handler)
    except OSError as exc:
        # 文件日志是"附加保障"，不能因为它打不开就让 server 起不来。
        # 但也不能静默 —— 否则就成了「以为有日志、其实没有」。
        print(
            f"[local-tools] 文件日志不可用（{LOG_PATH}）：{exc}；本次只有 stderr",
            file=sys.stderr,
            flush=True,
        )

    # stderr 通道：格式与改造前**逐字一致**（"[local-tools] 消息"），
    # 这样 out/*.stderr.log 那批既有证据仍能逐行对照。
    # StreamHandler 默认就是 sys.stderr —— 这里显式传，防止将来被误改成 stdout。
    err_handler = logging.StreamHandler(sys.stderr)
    err_handler.setFormatter(logging.Formatter("[local-tools] %(message)s"))
    LOGGER.addHandler(err_handler)


_install_handlers()


def log(msg: str) -> None:
    """一条日志，两个落点（文件 + stderr）。名字保留，调用点不用动。"""
    LOGGER.info(msg)


server = MCPServer(
    name="local-tools",
    version="1.0.0",
    instructions="本机通用小工具：文本统计、读取 STUDY_ROOT 工作区下的文件。",
)


@server.tool()
def word_count(text: str) -> str:
    """统计一段文本的字符数和词数。

    算法与 hello_agent.py 的进程内版本逐字一致，便于两者直接对比输出。
    """
    # ⚠️ 只记长度，不记内容 —— 用户文本不进日志。
    LOGGER.info("handler 调用：word_count(text 长度=%d)", len(text))
    try:
        result = f"「{text}」→ 字符数 {len(text)}，词数 {len(text.split())}"
    except Exception:
        LOGGER.exception("handler 失败：word_count")
        raise
    LOGGER.info("handler 成功：word_count → 字符数 %d", len(text))
    return result


@server.tool()
def read_study_file(relpath: str) -> str:
    """读取 STUDY_ROOT 下的文本文件（相对路径，如 notes/示例.txt）。

    越界访问（.. 逃逸、绝对路径、指向工作区外）一律拒绝并说明原因。
    这是第二阶段「权限与沙箱」的最小原型：工具自己守住自己的边界。
    """
    # relpath 是路径不是内容，记它；文件正文一律不进日志。
    LOGGER.info("handler 调用：read_study_file(relpath=%r)", relpath)
    try:
        root = STUDY_ROOT.resolve()

        # Path 语义：若 relpath 是绝对路径（含 C:\... 或 E:\...），"/" 会直接丢弃 root。
        # 所以不能只看 relpath 长什么样，必须在拼接、规范化之后比较真实落点。
        target = (root / relpath).resolve()

        if not target.is_relative_to(root):
            log(f"拒绝越界读取：{relpath!r} -> {target}")
            LOGGER.info("handler 成功：read_study_file 拒绝越界")
            return f"拒绝：{relpath} 越出 {root}（本工具只允许读该目录之内）"

        if not target.is_file():
            LOGGER.info("handler 成功：read_study_file 目标不存在")
            return f"不存在：{relpath}"

        text = target.read_text(encoding="utf-8", errors="replace")
        LOGGER.info("handler 成功：read_study_file 读到 %d 字符（正文不记入日志）", len(text))
        if len(text) > MAX_CHARS:
            return text[:MAX_CHARS] + f"\n…（已截断，原文共 {len(text)} 字符）"
        return text
    except Exception:
        LOGGER.exception("handler 失败：read_study_file")
        raise


if __name__ == "__main__":
    # PID 由文件日志的 formatter 打在**每一行**上（见 %(process)d），
    # 这里不再重复写一遍 —— 重复过一次，读起来像两个 pid。
    log(f"启动：transport=stdio，STUDY_ROOT={STUDY_ROOT}")
    log(f"cwd={Path.cwd()}")
    log(f"python={sys.executable}")
    log(f"日志文件：{LOG_PATH}")
    server.run()  # 默认 transport="stdio" → anyio.run(run_stdio_async)
