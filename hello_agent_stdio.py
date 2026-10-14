"""客户端 B：用 Claude Agent SDK 连「独立进程 + stdio」的 MCP server。

与 hello_agent.py 的唯一区别在 mcp_servers 那一行：

    hello_agent.py        {"localtools": <进程内对象>}          ← create_sdk_mcp_server
    hello_agent_stdio.py  {"local-tools": {"type": "stdio", …}}  ← 独立进程

server 那一侧一行代码都不用改 —— 这正是今天要证明的事。

**API runtime 配置的唯一来源是项目根的 `.env`**，规则与 hello_agent.py 完全一致：
不读 `~/.claude/settings.json`，也不接受父进程继承来的 `ANTHROPIC_*` / `API_TIMEOUT*`，
缺配置直接报错退出。密钥不进代码、不进仓库。
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "servers" / "experiments"))

from usage_log import log_usage  # noqa: E402

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

ENV_FILE = PROJECT_ROOT / ".env"

# 钉死到 Claude Code 的真身二进制（见项目 CLAUDE.md 陷阱 2）
CLI_PATH = r"E:\node.js\24.21.0\node_modules\@anthropic-ai\claude-code\bin\claude.exe"

# server 的启动命令 —— 与注册给 Claude Code 的那条逐字相同。
# 用环境变量覆盖是为了做 stdout 实验时不必改文件（单变量对照）。
PYTHON = os.environ.get("MCP_PYTHON", r"D:\Anaconda3\envs\aicoding\python.exe")
SERVER_SCRIPT = os.environ.get(
    "MCP_SERVER_SCRIPT", r"E:\aicoding\mini-claude-code\servers\local_tools_server.py"
)

SERVER_NAME = "local-tools"

SECRET_KEYS = {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"}

# `.env` 里允许出现、且会被显式传给内层 CLI 的变量命名空间。
# 它与 isolate_from_parent_env() 的摘除范围**必须一致** —— 同一组键，
# 只从 .env 进来；父进程里的一律摘掉，再由 options.env 显式放回。
ENV_NAMESPACE = ("ANTHROPIC_", "API_TIMEOUT")

# 逐个点名的「会改变内层 Agent 实际行为」的父进程变量。
#
# 为什么**不按前缀清** `CLAUDE_CODE_`：同一前缀下还有 ENTRYPOINT /
# MESSAGING_SOCKET / MESSAGING_TOKEN / SESSION_ID / EXECPATH 这些**运行所需**的
# 控制面变量（CLI 内部那个 `XS` 排除集就是它们），按前缀清会误伤。
# 这里只清经代码确认会改变模型行为的四个，扩大范围要逐个补证据。
#
# 每个的理由都是「它决定内层 Agent 跑什么」：
#   EFFORT_LEVEL                 → reasoning effort（CLI 里 `a.CLAUDE_CODE_EFFORT_LEVEL`）
#   SUBAGENT_MODEL               → 子代理模型（CLI 里 `AX()` 读它）
#   AUTO_COMPACT_WINDOW          → 自动压缩阈值，改变上下文与 token 用量
#   DISABLE_NONESSENTIAL_TRAFFIC → 关掉 updater / 遥测 / 插件下载等副作用请求
#
# 不清的后果：外层会话的调参会顺着进程环境淌进内层实验，而 .env 挡不住它们
# —— 因为它们的来源根本不是 .env。消融实验会因此不可比。
INHERITED_BEHAVIOR_KEYS = (
    "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
)

# 少了就跑不起来的。凭据是「二选一」，单独判。
REQUIRED_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL")
CREDENTIAL_KEYS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")

# 打印/落盘时的兜底判据：键名像密钥、或值长得像凭据，都打码。
SECRET_KEY_PAT = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|AUTH", re.I)
CREDENTIAL_VALUE_PAT = re.compile(r"(sk-|Bearer\s+)[A-Za-z0-9_\-]{8,}")

CONFIG_HINT = """
怎么修：
  1) 确认项目根有 .env —— 没有就 `cp .env.example .env`
  2) 至少填这三样：ANTHROPIC_AUTH_TOKEN（或 ANTHROPIC_API_KEY）、
     ANTHROPIC_BASE_URL、ANTHROPIC_MODEL
  3) 想临时换个 .env 的位置做对照实验，用 DOTENV_PATH=<文件>

这里**不会**回落到 ~/.claude/settings.json，也不会用父进程里的
ANTHROPIC_* / API_TIMEOUT* —— 缺什么就报什么，免得实验条件说不清。
"""


class ConfigError(RuntimeError):
    """运行所需的 API 配置缺失或非法。直接失败，不回落。"""

PROMPT = (
    "请依次调用下面三个工具，每次都要真的调用，然后把每次的原始返回一字不改地列出来：\n"
    "1) word_count，参数 text 传 'hello world foo'\n"
    "2) read_study_file，参数 relpath 传 '大纲.md'（只要前 3 行）\n"
    "3) read_study_file，参数 relpath 传 '../../Windows/win.ini'（这是越界对照，预期被拒绝）"
)


def parse_dotenv(path: Path) -> dict[str, str]:
    """极简 .env 解析：`KEY=VALUE`，忽略空行与 `#` 注释，去掉成对引号。

    与 `hello_agent.py` 里的实现保持一致 —— 两个脚本各自自包含，不互相 import。
    """
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key.strip()] = val
    return out


def _in_namespace(key: str) -> bool:
    """这个键属不属于「由 .env 独家负责」的那组变量。"""
    return key.startswith(ENV_NAMESPACE)


def load_endpoint_env() -> tuple[dict[str, str], str]:
    """从项目根的 `.env` 读全部 API runtime 配置。返回 `(env, key_source)`。

    规则与 `hello_agent.py` 逐字一致（两个脚本各自自包含，不互相 import）：
    唯一来源就是 `.env`，不读 `~/.claude/settings.json`，
    也不接受父进程继承来的 `ANTHROPIC_*` / `API_TIMEOUT*`。
    配置不全时只有报错这一种结果 —— 见 ConfigError。
    """
    env_file = Path(os.environ.get("DOTENV_PATH") or ENV_FILE)
    if not env_file.is_file():
        raise ConfigError(f"找不到配置文件 {env_file}（可用 DOTENV_PATH 换位置）")

    env = {k: v for k, v in parse_dotenv(env_file).items() if _in_namespace(k)}

    missing = [k for k in REQUIRED_KEYS if not env.get(k)]
    if not any(env.get(k) for k in CREDENTIAL_KEYS):
        missing.insert(0, f"{' / '.join(CREDENTIAL_KEYS)}（二选一）")
    if missing:
        raise ConfigError(f"{env_file.name} 缺少运行所需的配置：{'、'.join(missing)}")

    override = os.environ.get("HELLO_AGENT_BASE_URL")
    if override:
        env["ANTHROPIC_BASE_URL"] = override

    return env, f"env:{env_file.name}"


def isolate_from_parent_env() -> list[str]:
    """摘掉父进程里不该继承给内层 CLI 的变量，返回被摘掉的**键名**（不含值）。

    两组：
      · 命名空间组 —— `ANTHROPIC_*` / `API_TIMEOUT*`，键从 `.env` 独家进来
      · 行为组     —— `INHERITED_BEHAVIOR_KEYS`，逐个点名

    命名空间组为什么非摘不可：SDK 给内层 CLI 的环境是 `{**os.environ, **options.env}`
    （subprocess_cli.py:810-815 附近）—— options.env 只能**覆盖同名键**，
    删不掉 .env 没写的键。父进程里那些没被记账的 `ANTHROPIC_*` 会原样漏进去，
    变成「key_source 说是 A、实际生效的是 B」的来源，而且完全不报错。

    行为组是同一类问题的另一半：外层会话调着 /effort、子代理模型、压缩窗口，
    这些会顺着进程环境淌进内层实验 —— 换一套 `.env` 也挡不住，因为它们的
    来源根本不是 `.env`。摘掉之后，内层跑的才是「由本次实验配置决定的行为」。

    只返回键名：这一组里可能混着凭据形态的东西，落盘必须干净。
    规则与 `hello_agent.py` 逐字一致（两个脚本各自自包含，不互相 import）。
    """
    removed = sorted(
        {k for k in os.environ if _in_namespace(k)}
        | {k for k in INHERITED_BEHAVIOR_KEYS if k in os.environ}
    )
    for k in removed:
        del os.environ[k]
    return removed


def redact(env: dict[str, str]) -> dict[str, str]:
    """打印用：键名像密钥、或值长得像凭据，都只显示长度。

    双重判据是必要的：`.env` 现在什么都能写，光靠固定的 SECRET_KEYS 挡不住
    将来加进来的 `ANTHROPIC_CUSTOM_HEADERS: "… Bearer <key>"` 之类。
    """
    return {
        k: (
            f"<{len(v)} 字符，已隐藏>"
            if k in SECRET_KEYS
            or SECRET_KEY_PAT.search(k)
            or CREDENTIAL_VALUE_PAT.search(v)
            else v
        )
        for k, v in env.items()
    }


def scrub(text: str) -> str:
    """把错误信息里可能夹带的凭据抹掉再打印/落盘。同 hello_agent.py。"""
    return re.sub(r"(sk-|Bearer\s+)[A-Za-z0-9_\-]{8,}", r"\1<已打码>", text)


def log_stderr(line: str) -> None:
    """CLI 的 stderr。stdin/stdout 才是 MCP 协议线，stderr 可以随便用。"""
    print(f"[cli stderr] {line}", file=sys.stderr, flush=True)


def stdio_server_config() -> dict[str, Any]:
    """stdio 配置的确切形状（claude_agent_sdk/types.py:606 McpStdioServerConfig）。

    注意与进程内版本的区别：这里传的是**数据**（怎么把进程拉起来），
    不是一个 Python 对象。所以这个 server 完全可以是别的语言写的。
    """
    return {
        "type": "stdio",
        "command": PYTHON,
        "args": [SERVER_SCRIPT],
    }


async def main() -> int:
    try:
        env, key_source = load_endpoint_env()
    except ConfigError as exc:
        # 配置不全是**明确失败**，不是回落。失败运行同样要留一行（见项目 CLAUDE.md 陷阱 13）。
        print(f"[配置错误] {exc}", file=sys.stderr)
        print(CONFIG_HINT, file=sys.stderr)
        row = log_usage(
            None,
            script=Path(__file__).name,
            key_source="config-error",
            extra={"aborted": True, "config_error": str(exc)[:200]},
        )
        print(f"  已记一行（token 为 null）：run={row['run']}  key_source={row['key_source']}")
        return 1

    # 必须在 query() 之前：SDK 会拿 os.environ 当基底，摘晚了就漏进去了。
    scrubbed = isolate_from_parent_env()

    print("=== 本次使用的 key 来源 ===")
    print(f"  {key_source}")
    print()
    print("=== 生效的端点配置（密钥已打码）===")
    for k, v in redact(env).items():
        print(f"  {k} = {v}")
    print(f"  cli_path = {CLI_PATH}")
    print()
    print("=== 父进程环境里被摘掉的变量（只记键名，不记值）===")
    print(f"  命名空间 {ENV_NAMESPACE} → {[k for k in scrubbed if _in_namespace(k)]}")
    print(f"  行为配置 INHERITED_BEHAVIOR_KEYS → {[k for k in scrubbed if k in INHERITED_BEHAVIOR_KEYS]}")
    print()

    cfg = stdio_server_config()
    print("=== 本客户端使用的 MCP 配置 ===")
    print(f"  mcp_servers = {json.dumps({SERVER_NAME: cfg}, ensure_ascii=False)}")
    print(f"  等价于命令行: {cfg['command']} {' '.join(cfg['args'])}")
    print()

    options = ClaudeAgentOptions(
        cli_path=CLI_PATH,
        # ↓↓↓ 与 hello_agent.py 唯一的差别 ↓↓↓
        mcp_servers={SERVER_NAME: cfg},
        # 独立进程时，工具名前缀还是 mcp__<server 名>__<工具名>
        allowed_tools=[
            f"mcp__{SERVER_NAME}__word_count",
            f"mcp__{SERVER_NAME}__read_study_file",
        ],
        # 必须是 []，不能是 ["user"]、也不能省略。原因见项目 CLAUDE.md 陷阱 1：
        # settings.json 的 env 块会静默覆盖 options.env，反向对照将无法失败。
        # `[]` 同时把 CLAUDE.md 与 user/project/local settings 挡在内层 CLI 之外，
        # 内层 Agent 的 API runtime 配置只由 .env 决定。
        setting_sources=[],
        env=env,
        max_turns=8,
        stderr=log_stderr,
    )

    print(f"=== 提问 ===\n{PROMPT}\n")

    result: ResultMessage | None = None
    seen_models: set[str] = set()
    tool_calls: list[tuple[str, Any]] = []
    tool_results: list[str] = []

    try:
        async for message in query(prompt=PROMPT, options=options):
            if isinstance(message, AssistantMessage):
                if message.model:
                    seen_models.add(message.model)
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"[assistant] {block.text}")
                    elif isinstance(block, ToolUseBlock):
                        tool_calls.append((block.name, block.input))
                        print(
                            f"[tool_use]  {block.name}"
                            f"({json.dumps(block.input, ensure_ascii=False)})"
                        )
            elif isinstance(message, UserMessage):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        tool_results.append(str(block.content))
                        print(f"[tool_result] {block.content}")
            elif isinstance(message, ResultMessage):
                result = message
    except Exception as exc:  # noqa: BLE001
        # 失败运行同样要留一行用量。同 hello_agent.py。
        msg = scrub(str(exc))
        print(f"\n[中断] {type(exc).__name__}: {msg}")
        row = log_usage(
            None,
            script=Path(__file__).name,
            key_source=key_source,
            extra={
                "aborted": True,
                "error": f"{type(exc).__name__}: {msg}"[:200],
                "parent_env_scrubbed": scrubbed,
            },
        )
        print(f"  已记一行（token 为 null）：run={row['run']}  key_source={row['key_source']}")
        return 1

    print("\n=== 结果 ===")
    if result is None:
        print("没有收到 ResultMessage —— 运行未正常结束")
        row = log_usage(
            None,
            script=Path(__file__).name,
            key_source=key_source,
            extra={"note": "没有收到 ResultMessage", "parent_env_scrubbed": scrubbed},
        )
        print(f"  已记一行（token 字段为 null）：run={row['run']}  key_source={row['key_source']}")
        return 1

    print(f"  subtype        = {result.subtype}")
    print(f"  is_error       = {result.is_error}")
    print(f"  num_turns      = {result.num_turns}")
    print(f"  duration_ms    = {result.duration_ms}")
    print(f"  session_id     = {result.session_id}")
    print(f"  total_cost_usd = {result.total_cost_usd}")
    print(f"  result         = {result.result}")
    print(f"  AssistantMessage.model 出现过的值 = {sorted(seen_models)}")

    # 这条判据是本脚本的"能不能失败"：没调到工具，就不算验证通过。
    called = [name for name, _ in tool_calls]
    print(f"\n=== 工具调用清点 ===")
    print(f"  调用次数 = {len(tool_calls)}")
    for name in called:
        print(f"    - {name}")

    ok = (
        f"mcp__{SERVER_NAME}__word_count" in called
        and f"mcp__{SERVER_NAME}__read_study_file" in called
        and not result.is_error
    )
    print(f"\n=== 判定：{'两个工具都被真实调用，且运行无错' if ok else '未达标'} ===")

    # 越界那次必须被拒绝：返回里出现"拒绝"字样才算沙箱真的生效
    escaped = any("拒绝" in r for r in tool_results)
    print(f"=== 越界拦截：{'命中（返回中含「拒绝」）' if escaped else '未命中 —— 需要排查'} ===")

    row = log_usage(
        result,
        script=Path(__file__).name,
        key_source=key_source,
        extra={
            "tool_calls": called,
            "escape_blocked": escaped,
            "parent_env_scrubbed": scrubbed,
        },
    )
    print("\n=== 用量已追加到 usage.jsonl ===")
    print(
        f"  run={row['run']}  model={row['model']}  "
        f"input={row['input']}  output={row['output']}  cache_read={row['cache_read']}  "
        f"turns={row['num_turns']}  key_source={row['key_source']}"
    )

    return 0 if (ok and escaped) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
