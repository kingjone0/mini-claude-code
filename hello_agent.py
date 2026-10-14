"""Claude Agent SDK 最小验证：一次提问 + 一个自定义 tool。

**API runtime 配置的唯一来源是项目根的 `.env`。** 这个脚本不读
`~/.claude/settings.json`，也不接受父进程环境里的 `ANTHROPIC_*` / `API_TIMEOUT*`
（后者会被主动摘掉 —— 理由见 `isolate_from_parent_env()`）。
缺配置直接报错退出，**不静默回落**。

为什么要这么切：`.env` 管的是**内层 Agent 调模型**要用的东西，
`~/.claude/settings.json` 管的是**外层 Claude Code 自己**。两者混在一起时，
某次实验到底用了哪条 key、哪个端点就说不清，而第三方端点的账单只给总额，
事后无法归因 —— 第三阶段跑 SWE-bench 消融正是要这个粒度。

从哪读的会打印出来并记进 `servers/experiments/out/usage.jsonl` 的 `key_source`；
被摘掉的父进程变量名记进同一行的 `parent_env_scrubbed`。

密钥不进代码、不进仓库。
"""

import asyncio
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
    create_sdk_mcp_server,
    query,
    tool,
)

ENV_FILE = PROJECT_ROOT / ".env"

# 钉死到 Claude Code 的真身二进制。
# 不指 C:\Users\<用户名>\.nvmd\bin\claude.exe —— 那是 nvmd 的启动垫片。
# 好处：绕开 .cmd 垫片被拒的问题、不依赖 PATH、不受 nvmd 切换 node 版本影响。
CLI_PATH = r"E:\node.js\24.21.0\node_modules\@anthropic-ai\claude-code\bin\claude.exe"

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


def parse_dotenv(path: Path) -> dict[str, str]:
    """极简 .env 解析：`KEY=VALUE`，忽略空行与 `#` 注释，去掉成对引号。

    故意不引第三方库 —— 这里只需要读三五个键，多一个依赖就多一处环境漂移。
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

    唯一来源就是 `.env`：
      · 不读 `~/.claude/settings.json`（那是外层 Claude Code 的配置）
      · 不接受父进程继承来的 `ANTHROPIC_*` / `API_TIMEOUT*`
    两条都不走，所以配置不全时**只有报错这一种结果** —— 见 ConfigError。

    key_source 形如 `env:.env`，会原样打印并记进用量日志。

    设了 HELLO_AGENT_BASE_URL 就用它覆盖 ANTHROPIC_BASE_URL —— 反向对照用；
    DOTENV_PATH 只换 .env 的**位置**，不是 key 的第二个来源。
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
    """把错误信息里可能夹带的凭据抹掉再打印/落盘。

    第三方端点的报错常常带出 key 的尾巴（实测见过 `Your api key: ****TEST`）。
    失败运行要记进 usage.jsonl，绝不能顺手把凭据也记进去。
    """
    return re.sub(r"(sk-|Bearer\s+)[A-Za-z0-9_\-]{8,}", r"\1<已打码>", text)


@tool("word_count", "统计一段文本的字符数和词数", {"text": str})
async def word_count(args: dict[str, Any]) -> dict[str, Any]:
    text = args["text"]
    return {
        "content": [
            {
                "type": "text",
                "text": f"「{text}」→ 字符数 {len(text)}，词数 {len(text.split())}",
            }
        ]
    }


def log_stderr(line: str) -> None:
    print(f"[cli stderr] {line}", file=sys.stderr, flush=True)


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

    server = create_sdk_mcp_server(name="localtools", version="1.0.0", tools=[word_count])

    options = ClaudeAgentOptions(
        cli_path=CLI_PATH,
        mcp_servers={"localtools": server},
        allowed_tools=["mcp__localtools__word_count"],
        # 必须是 []，不能是 ["user"]、也不能省略。
        #
        # 实测优先级（见 VERIFY.md）：settings.json 的 env 块 > options.env > 进程继承环境。
        #   - 写成 ["user"]：settings.json 会静默覆盖下面的 env=，反向对照将无法失败
        #   - 省略不写：默认等于 ["user","project","local"]，同样会覆盖
        #   - 写成 []    ：只有下面的 env= 生效，端点来源唯一、可验证
        #
        # `[]` 还顺手把项目 CLAUDE.md 与 user / project / local settings 全挡在内层 CLI 之外
        # （SDK types.py：「Must include "project" to load CLAUDE.md files」）。
        # 这正是要的：内层 Agent 的 API runtime 配置只由 .env 决定，filesystem
        # settings 一概不参与；父进程漏进来的同名变量也在上面被摘掉了。
        setting_sources=[],
        env=env,
        max_turns=5,
        stderr=log_stderr,
    )

    prompt = (
        "请调用 word_count 工具，参数 text 传 'hello world foo'，"
        "然后把工具返回的结果原样告诉我。"
    )
    print(f"=== 提问 ===\n{prompt}\n")

    result: ResultMessage | None = None
    seen_models: set[str] = set()

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                if message.model:
                    seen_models.add(message.model)
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"[assistant] {block.text}")
                    elif isinstance(block, ToolUseBlock):
                        print(f"[tool_use]  {block.name}({block.input})")
            elif isinstance(message, UserMessage):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        print(f"[tool_result] {block.content}")
            elif isinstance(message, ResultMessage):
                result = message
    except Exception as exc:  # noqa: BLE001
        # 失败运行同样要留一行 —— 「能失败」的对照正是靠它留证。
        # 不捕获的话异常直接冒泡，这一行就永远记不上（已经踩过一次）。
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
    print(f"  model_usage    = {result.model_usage}")
    print(f"  AssistantMessage.model 出现过的值 = {sorted(seen_models)}")

    row = log_usage(
        result,
        script=Path(__file__).name,
        key_source=key_source,
        extra={"parent_env_scrubbed": scrubbed},
    )
    print("\n=== 用量已追加到 usage.jsonl ===")
    print(
        f"  run={row['run']}  model={row['model']}  "
        f"input={row['input']}  output={row['output']}  cache_read={row['cache_read']}  "
        f"turns={row['num_turns']}  key_source={row['key_source']}"
    )

    return 0 if not result.is_error else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
