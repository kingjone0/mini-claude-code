"""agent/config.py —— API runtime 配置的唯一入口（agent/ 下的程序共用）。

**为什么会有这个模块**：`hello_agent.py` 与 `hello_agent_stdio.py` 当初**故意各自自包含**
（不互相 import）——它们的输出被 `VERIFY.md` 逐行引用，抽公共代码会让证据对不上。
但到第三个程序时再复制一遍就不合理了，所以公共部分收到这里。

⚠️ **那两个脚本不改成 import 本模块** —— 它们是冻结的验证产物。
   本模块的行为与它们**逐字一致**；改这里时要同步核对那边（见项目 CLAUDE.md 陷阱 13/14）。
"""

from __future__ import annotations

import os
import re
import sys
from contextlib import suppress
from pathlib import Path

# ── 路径 ──────────────────────────────────────────────────────────────────
# 基准取 __file__ 而非 cwd：cwd 由启动者决定，用它算相对路径会漂到不可预期的位置。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Claude Code 的真身二进制（约 230 MB）。不要指 nvmd 的垫片 —— 见陷阱 2。
CLI_PATH = r"E:\node.js\24.21.0\node_modules\@anthropic-ai\claude-code\bin\claude.exe"

# ── 配置命名空间 ──────────────────────────────────────────────────────────
# `.env` 里允许出现、且会被显式传给内层 CLI 的变量命名空间。
# 它与 isolate_from_parent_env() 的摘除范围**必须一致** —— 同一组键，
# 只从 .env 进来；父进程里的一律摘掉，再由 options.env 显式放回。
ENV_NAMESPACE = ("ANTHROPIC_", "API_TIMEOUT")

# 逐个点名的「会改变内层 Agent 实际行为」的父进程变量。
#
# 为什么**不按前缀清** `CLAUDE_CODE_`：同一前缀下还有 ENTRYPOINT /
# MESSAGING_SOCKET / MESSAGING_TOKEN / SESSION_ID / EXECPATH 这些**运行所需**的
# 控制面变量，按前缀清会误伤。这里只清经代码确认会改变模型行为的四个。
#
# 每个的理由都是「它决定内层 Agent 跑什么」：
#   EFFORT_LEVEL                 → reasoning effort
#   SUBAGENT_MODEL               → 子代理模型
#   AUTO_COMPACT_WINDOW          → 自动压缩阈值，改变上下文与 token 用量
#   DISABLE_NONESSENTIAL_TRAFFIC → 关掉 updater / 遥测 / 插件下载等副作用请求
INHERITED_BEHAVIOR_KEYS = (
    "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
)

# 少了就跑不起来的。凭据是「二选一」，单独判。
REQUIRED_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL")
CREDENTIAL_KEYS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")

SECRET_KEYS = {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"}

# 打印/落盘时的兜底判据：键名像密钥、或值长得像凭据，都打码。
# 双重判据是必要的 —— `.env` 现在什么都能写，光靠固定白名单挡不住将来加进来的
# `ANTHROPIC_CUSTOM_HEADERS: "… Bearer <key>"` 这类键。漏掉一个的代价远大于多打码一个。
SECRET_KEY_PAT = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|AUTH", re.I)
CREDENTIAL_VALUE_PAT = re.compile(r"(sk-|Bearer\s+)[A-Za-z0-9_\-]{8,}")

CONFIG_HINT = """
怎么修：
  1) 确认项目根有 .env —— 没有就 `Copy-Item .env.example .env`
  2) 至少填这三样：ANTHROPIC_AUTH_TOKEN（或 ANTHROPIC_API_KEY）、
     ANTHROPIC_BASE_URL、ANTHROPIC_MODEL
  3) 想临时换个 .env 的位置做对照实验，用 DOTENV_PATH=<文件>

这里**不会**回落到 ~/.claude/settings.json，也不会用父进程里的
ANTHROPIC_* / API_TIMEOUT* —— 缺什么就报什么，免得实验条件说不清。
"""


class ConfigError(RuntimeError):
    """运行所需的 API 配置缺失或非法。直接失败，不回落。"""


def force_utf8_output() -> None:
    """把 stdout/stderr 切到 UTF-8（带 replace），必须在任何 print 之前调用。

    **为什么需要**：本机控制台代码页是 GBK(936)。打印它装不下的字符
    （emoji、替换字符 U+FFFD、各种符号）会抛 UnicodeEncodeError，
    **把原始证据直接盖住** —— 明明 agent 在正常工作，却因为一个 print 崩掉。

    已实际发生过一次：`coding_agent.py` 打印 MCP 工具返回值时崩掉。
    仓库里 `probe_client.py` 早就有同一个函数 —— 那次忘了在新程序里加。
    所以它放在公共模块，新程序一律先调。
    """
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")


# ── .env ──────────────────────────────────────────────────────────────────
def parse_dotenv(path: Path) -> dict[str, str]:
    """极简 .env 解析：`KEY=VALUE`（等号两边空格会被 strip），忽略空行与 `#` 注释，
    去掉成对引号。与 hello_agent*.py 的实现逐字一致。"""
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
    return key.startswith(ENV_NAMESPACE)


def load_endpoint_env() -> tuple[dict[str, str], str]:
    """从项目根 `.env` 读全部 API runtime 配置。返回 `(env, key_source)`。

    唯一来源就是 `.env`：不读 `~/.claude/settings.json`，也不接受父进程继承来的
    `ANTHROPIC_*` / `API_TIMEOUT*`。配置不全时**只有报错这一种结果** —— 见 ConfigError。
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

    # 反向对照用：临时把端点改错，验证「失败确实来自端点配置」。
    override = os.environ.get("AGENT_BASE_URL_OVERRIDE")
    if override:
        env["ANTHROPIC_BASE_URL"] = override

    return env, f"env:{env_file.name}"


def isolate_from_parent_env() -> list[str]:
    """摘掉父进程里不该继承给内层 CLI 的变量，返回被摘掉的**键名**（不含值）。

    两组：
      · 命名空间组 —— `ANTHROPIC_*` / `API_TIMEOUT*`，键从 `.env` 独家进来
      · 行为组     —— `INHERITED_BEHAVIOR_KEYS`，逐个点名

    命名空间组为什么非摘不可：SDK 给内层 CLI 的环境是 `{**os.environ, **options.env}`
    —— options.env 只能**覆盖同名键**，删不掉 .env 没写的键。父进程里那些没被记账的
    `ANTHROPIC_*` 会原样漏进去，变成「key_source 说是 A、实际生效的是 B」的来源，
    而且完全不报错。

    必须**在 query() 之前**调用：SDK 拿 os.environ 当基底，摘晚了就漏进去了。

    只返回键名：这一组里可能混着凭据形态的东西，落盘必须干净。
    """
    removed = sorted(
        {k for k in os.environ if _in_namespace(k)}
        | {k for k in INHERITED_BEHAVIOR_KEYS if k in os.environ}
    )
    for k in removed:
        del os.environ[k]
    return removed


# ── 打码 ──────────────────────────────────────────────────────────────────
def redact(env: dict[str, str]) -> dict[str, str]:
    """打印用：键名像密钥、或值长得像凭据，都只显示长度。"""
    return {
        k: (
            f"<{len(v)} 字符，已隐藏>"
            if k in SECRET_KEYS or SECRET_KEY_PAT.search(k) or CREDENTIAL_VALUE_PAT.search(v)
            else v
        )
        for k, v in env.items()
    }


def scrub(text: str, secrets: list[str] | None = None) -> str:
    """把可能夹带凭据的文本抹掉再打印/落盘。

    两道：
      ① 按 `sk-…` / `Bearer …` 的**形态**抹（挡未知来源的凭据）
      ② 按**已知真实值**精确替换（挡 provider 自带的打码格式，如 `****TEST`
         —— 那种不是 sk- 形态，形态匹配抓不到）
    """
    text = re.sub(r"(sk-|Bearer\s+)[A-Za-z0-9_\-]{8,}", r"\1<已打码>", text)
    for s in secrets or []:
        if s and len(s) >= 8:
            text = text.replace(s, "<已打码>")
    return text
