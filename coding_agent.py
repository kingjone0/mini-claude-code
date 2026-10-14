"""coding_agent.py —— mini Coding Agent 主循环。

任务：让 `fixtures/mini_repo/` 的失败测试通过。

━━ 四条设计要点（见 DESIGN.md 第 6、7 节）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. **不采信 agent 的自述。** 成功只由「pytest 的真实退出码」判定 ——
   agent 说「已修复」不算数。这是整个验收的立足点。

2. **靶场每轮先还原。** 跑前 `git checkout` 复位，保证「同一道题」可反复跑，
   指标才有可比性（第三阶段换 SWE-bench 时是同一个道理）。

3. **agent 只有一条写文件的通路。** `allowed_tools` 只放开 code-tools 的 5 个工具，
   内置的 Bash / Write / Edit 全部不可用 —— 沙箱不是建议，是唯一通路。

4. **全程留痕。** 工具调用序列（含失败的调用）落盘：这是「失败自省」的证据 ——
   能看出失败后是**换了策略**，还是原地重试。

用法：
    # 正向：修好靶场
    python coding_agent.py

    # 反向 A：拿掉写工具，看它会不会谎报成功
    python coding_agent.py --no-write

    # 反向 B：端点故意写错（沿用 AGENT_BASE_URL_OVERRIDE），应当整体失败
    $env:AGENT_BASE_URL_OVERRIDE="https://api.deepseek.com/anthropic-WRONG-PATH"; python coding_agent.py
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))                       # agent.config
sys.path.insert(0, str(PROJECT_ROOT / "servers" / "experiments"))  # usage_log

from agent.config import (  # noqa: E402  （agent/config.py）
    CLI_PATH,
    CONFIG_HINT,
    ConfigError,
    force_utf8_output,
    load_endpoint_env,
    isolate_from_parent_env,
    redact,
    scrub,
)
from agent.trace import TraceRecorder, plain  # noqa: E402
from usage_log import log_usage  # noqa: E402

from claude_agent_sdk import (  # noqa: E402
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

FIXTURE_ROOT = PROJECT_ROOT / "fixtures"
# 第三方大靶场放这里 —— **不进本仓库**（那是别人的 MIT 代码，且有它自己的 git 历史）。
# main() 会按 --fixture 覆盖 FIXTURE。
FIXTURE = FIXTURE_ROOT / "mini_repo"
TARGETS_ROOT = PROJECT_ROOT.parent / "targets"
PYTHON = sys.executable
SERVER_SCRIPT = PROJECT_ROOT / "servers" / "code_tools_server.py"

# 每个靶场的**允许改动范围**与**测试范围**。
# 两者的判据不同，所以分开写：
#   改测试 = 作弊（硬失败，验收会查）
#   改范围外 = 越界（也是硬失败）
FIXTURE_RULES: dict[str, dict[str, Any]] = {
    "mini_repo": {
        "scope": ("calc.py", "stats.py"),
        "tests": ("test_calc.py", "test_stats.py"),
    },
    "small_pkg": {
        "scope": ("inventory/",),
        "tests": ("tests/",),
    },
    "more-itertools": {
        "scope": ("more_itertools/",),
        "tests": ("tests/",),
    },
    # 多缺陷版（4 处缺陷、2 个模块）。用来问「subagent 分工有没有价值」——
    # 单缺陷、11 次调用那个规模问不出这个问题（见 setup_multi_target.py 开头）。
    "more-itertools-multi": {
        "scope": ("more_itertools/",),
        "tests": ("tests/",),
    },
    # 硬靶场（3 处缺陷，全部注入在**私有辅助函数**里）。与上两个的区别只有一点：
    # 失败测试的名字指向公开包装函数（DiscreteFourierTransformTests /
    # SlidingWindowTests / IsliceExtendedTests），grep 那个名字**不会**命中缺陷，
    # 所以上一轮那条"一步命中"的快速路径在这里不存在（见 setup_hard_target.py 开头）。
    "more-itertools-hard": {
        "scope": ("more_itertools/",),
        "tests": ("tests/",),
        # 预注册的不变量是「基线失败数**必须**等于 32」，而不是「基线是红的」。
        # ⚠️ 这两个不是一回事：HEAD 被挪、`git clean` 没生效、或者缺陷只注进去
        # 一部分（本靶场第一次建就踩过：子串判据静默漏注了 27 个失败，基线只剩 5，
        # 而退出码照样是 1，守卫放行）—— 这些情况下"红的"依然成立，数量才是证据。
        # 所以这里显式声明期望值，基线对不上就直接中止（见 main 里的守卫）。
        # 只给**量过并且钉死**的靶场写；没写 = 只查"红的"，不假装查过数量。
        "expect_failed": 32,
    },
}


def resolve_fixture(name: str) -> Path:
    """先找 `fixtures/<name>`，再找 `../targets/<name>`（大靶场在仓库之外）。"""
    local = FIXTURE_ROOT / name
    if local.is_dir():
        return local.resolve()
    external = TARGETS_ROOT / name
    if external.is_dir():
        return external.resolve()
    raise SystemExit(
        f"找不到靶场 {name!r}：既不在 {FIXTURE_ROOT}，也不在 {TARGETS_ROOT}。\n"
        f"  大靶场要先准备：python setup_target.py"
    )


def _matches(relpath: str, patterns: tuple[str, ...]) -> bool:
    """relpath 是否命中 patterns 中的某一条（精确相等，或以它开头当目录前缀）。"""
    return any(relpath == p or relpath.startswith(p) for p in patterns)


# 第三阶段 P0 实例模式用的「测试侧文件」判据。与 `eval/lib` + 预注册 §3.1 的分类规则
# **同源**（路径含 tests/test/testing 段，或 basename 匹配 test_*.py / *_test.py / conftest.py）。
# 这里重复一份而不 import eval/：`coding_agent.py` 是 agent 侧产物，不该被评测目录的
# 可用性绑住（eval/ 缺失时 agent 仍要能跑 fixture 路）。
_TEST_DIR_RE = re.compile(r"(^|/)(tests|test|testing)(/|$)")
_TEST_BASENAME_RE = re.compile(r"(^|/)(test_[^/]+\.py|[^/]+_test\.py|conftest\.py)$")


def _is_test_path(relpath: str) -> bool:
    p = (relpath or "").replace("\\", "/")
    return bool(_TEST_DIR_RE.search(p) or _TEST_BASENAME_RE.search(p))

SERVER_NAME = "code-tools"
# ── A/B 实验用的唯一变量：repo_map 的默认详细度 ────────────────────────────
# 两臂：
#   A（对照）AGENT_REPO_MAP_DEFAULT=symbols  —— 旧行为，贵且会被截断
#   B（处理）AGENT_REPO_MAP_DEFAULT=summary  —— 新行为，全仓只要 359 字符
#
# ⚠️ **实测过的事实**（不是推断）：SDK 给 MCP 子进程的环境是
#    `{**os.environ, **mcp_servers[name]["env"]}`，所以父进程 export 的这个变量
#    **本来就能被子进程继承**（探针 `servers/experiments/env_probe.py` 的 C1 组：
#    父进程设 symbols、不显式传 -> 子进程 os.environ 里实测到 'symbols'）。
#
#    那为什么还要显式传？因为**"能到"不等于"该靠它"**：
#      ① 靠继承的话，"这一臂是哪一臂"取决于调用方有没有记得 export ——
#         跑 A 臂时忘了设，会静默变成 B 臂，而产物里看不出来（陷阱 14 的失效模式）。
#      ② 显式传让实验条件**写死在代码里**，与 AGENT_REPO_ROOT 的做法一致。
#      ③ 两臂取值都不为空 —— 连"不设置=summary"这条隐含默认值也不依赖。
REPO_MAP_ENV_KEY = "AGENT_REPO_MAP_DEFAULT"
REPO_MAP_ARMS = ("symbols", "summary")
# 「工具描述」实验的两个文本通道。取值合法性**只在 server 那一处校验**
# （非法值让 server 启动失败）—— 同一条纪律落在一个实现上，比落在多份拷贝上可靠。
INSTRUCTIONS_ENV_KEY = "AGENT_SERVER_INSTRUCTIONS"
REPO_MAP_DESC_ENV_KEY = "AGENT_REPO_MAP_DESC"
ALL_TOOLS = [
    f"mcp__{SERVER_NAME}__list_files",
    f"mcp__{SERVER_NAME}__search_code",
    f"mcp__{SERVER_NAME}__read_file",
    f"mcp__{SERVER_NAME}__repo_map",
    f"mcp__{SERVER_NAME}__apply_patch",
    f"mcp__{SERVER_NAME}__run_tests",
]
# P2：父层按需取 scout findings 的通道。**只给父 agent**，不进 SCOUT_TOOLS、
# 不进 --subagent（B/C）臂 —— 那两臂是冻结的历史对照，一个字节都不能变。
POLICY_EXTRA_TOOL = f"mcp__{SERVER_NAME}__read_findings"
FINDINGS_DIR_ENV_KEY = "AGENT_FINDINGS_DIR"
WRITE_TOOL = f"mcp__{SERVER_NAME}__apply_patch"

# 纵深防御：即使 `tools=[]` 的行为在将来某版变化，也仍然拦得住。
# 抽成模块常量是因为 **trace 的运行头也要记它** —— 在那边手抄一份副本，
# 等于记了个不保证与真实生效值一致的东西（陷阱 14 的失效模式）。
DISALLOWED_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Grep", "Glob",
    "WebFetch", "WebSearch", "Task", "NotebookEdit", "TodoWrite",
]
PERMISSION_MODE = "dontAsk"
SETTING_SOURCES: list[str] = []

# ── ④ 子代理（subagent 分工）───────────────────────────────────────────────
#
# ⚠️ 同一个东西有三个名字，这是本层最容易写错的地方（探针实测，见 SUBAGENT.md §2）：
#   · CLI `--tools` 名单里，spawn 工具叫 "Task"
#   · 模型看到的工具名是 "Agent"
#   · 消息类型是 Task*（TaskStartedMessage / TaskProgressMessage / …）
# 往 `tools` 里写 "Agent" **不报错** —— 它会静默变成「零个内置工具」，
# 与写一个完全不存在的名字（NoSuchToolZZZ）行为完全同形：
# 341 条消息、0 次调用、不抛异常。所以这里只留一个常量，别处一律引用它。
SUBAGENT_TOOL = "Task"
# 模型**看到**的名字。判「这一轮到底派了没有」要看 tool_use 事件里的这个字符串，
# 不是上面那个 —— 两者不同名正是本层最容易记错账的地方。
SUBAGENT_SPAWN_NAME = "Agent"
# 侦察兵只读。给 run_tests 是为了让它能自己确认「失败的确实是这条测试」；
# **不给 apply_patch** —— 改动必须由父 agent 自己做，否则「谁改的」事后说不清，
# 独立验收也失去意义。
SCOUT_TOOLS = [
    f"mcp__{SERVER_NAME}__list_files",
    f"mcp__{SERVER_NAME}__search_code",
    f"mcp__{SERVER_NAME}__read_file",
    f"mcp__{SERVER_NAME}__repo_map",
    f"mcp__{SERVER_NAME}__run_tests",
]
# 侦察兵的回合上限。**8 太小了，实测会把侦察兵截断在半路。**
# 证据：C 臂（强制派一次）的 `TaskNotificationMessage.summary` 里会写明
# "NOTE: this agent stopped at its 8-turn limit before finishing. The text below is PARTIAL"。
# 第一轮 4 次强制运行里 **2 次被截断**（子代理工具调用 26 / 20 次），
# 硬靶场 C-1 也被截断（16 次）。跑完的那几次只用 12–17 次调用。
# 被截断的侦察兵交回来的是半份报告 —— 那不是「分工有没有用」，
# 那是「分工被我自己的预算掐死了」，会同时低估子代理的成本和父层的收益。
# 24 回合 ≈ 40+ 次工具调用，够它把两个文件翻完还有余量。
# ⚠️ 这个数字是**治疗的一部分**：改它必须写进 AB-SUBAGENT-HARD.md §7（偏离声明）。
SCOUT_MAX_TURNS = 24

MAX_TURNS = 40
# ── D8（t8 新增）：**实例模式**的回合上限。
# t5 端到端冒烟实测：真实仓库上 40 回合不够 —— agent 用 28 次 read + 23 次 run_tests
# 做探索就打满了，**它其实已经改对了 `core.py`**，却被上限截断、没机会收尾，
# 结果 usage 走异常路径、指标全 0。真实仓库（click 的 core.py 有 3134 行）比
# `fixtures/` 下的小靶场大得多，探索成本高一个量级。
# ⇒ 实例模式给更宽的预算；**`--fixture` 路的默认值一个字不动**（见 main 里的取值逻辑）。
INSTANCE_MAX_TURNS = 120
MAX_EVENT_TEXT = 4000


def build_agents() -> dict[str, Any]:
    """子代理名册。

    必须与放行它的那一臂**同时**出现：只把 Task 放开而没有名册，
    模型拿到的是「可以派子代理」却没有任何可派的对象。
    """
    return {
        "scout": AgentDefinition(
            description="只读侦察兵：在指定文件或失败测试附近定位根因，"
                        "回报「文件:行 + 根因 + 建议的最小修改」。它不能改任何文件。",
            prompt=(
                "你是一个只读侦察兵。给你一个文件或一条失败测试，你的工作是**定位根因**，"
                "不是修它。\n"
                "可用工具：list_files / search_code / read_file / repo_map / run_tests。\n"
                "**不要尝试改任何文件**（你没有写权限）。\n"
                "最后用一段话说清楚：① 根因在 文件:行；② 为什么它会导致这个失败；"
                "③ 建议的最小修改是什么（用文字描述，不要动手改）。\n"
                "如果没能确定根因，就直说没确定 —— 不要猜一个看起来合理的答案交差。"
            ),
            tools=SCOUT_TOOLS,
            mcpServers=[SERVER_NAME],   # 字符串 = 引用父层同名 MCP server
            maxTurns=SCOUT_MAX_TURNS,
        ),
    }


def sandbox_spec(allow_write: bool, enable_subagent: bool,
                 policy_mode: bool = False) -> dict[str, Any]:
    """**唯一**一处算「这一次跑放开了什么、拦住了什么」。

    装配 options 用它，trace 运行头也用它。两处各算一遍的话，
    留痕记下来的就是「我以为放了什么」而不是「真的放了什么」——
    这正是陷阱 14 的失效模式（记录与生效值脱钩）。

    policy_mode（P1 驱动派发臂）：父层**不放** Task —— 派发由 driver 做，
    模型手里没有 spawn 通道（否则 D 与 C 的区别就没了）。父层多出的唯一东西
    是 read_findings（P2 的按需读取通道）。enable_subagent 与 policy_mode
    互斥（main 里拦），所以三段逻辑不会同时为真。
    """
    mcp_tools = ALL_TOOLS if allow_write else [t for t in ALL_TOOLS if t != WRITE_TOOL]
    if policy_mode:
        # P2 通道只在 policy 臂存在；B/C 臂（enable_subagent）保持逐字旧行为。
        mcp_tools = mcp_tools + [POLICY_EXTRA_TOOL]
    builtin = [SUBAGENT_TOOL] if enable_subagent else []
    return {
        "mcp_tools": mcp_tools,
        "builtin_tools": builtin,
        "allowed_tools": mcp_tools + builtin,
        # 用代码减掉，不靠人去记得改：DISALLOWED_TOOLS 里本来就有 "Task"。
        # 靠人记的那一份，早晚会和 DISALLOWED_TOOLS 的真实内容分叉。
        "disallowed_tools": [t for t in DISALLOWED_TOOLS if t not in builtin],
        "agents": sorted(build_agents()) if enable_subagent else [],
        "permission_mode": PERMISSION_MODE,
        "strict_mcp_config": True,
        "setting_sources": list(SETTING_SOURCES),
    }

TASK_BASE = """仓库根目录下有一个小 Python 项目，它的测试目前有失败。

任务：**让所有测试通过。**

硬约束：
1. **先读仓库根目录的 `README.md`** —— 那里写了允许修改哪些文件，以及其它约束。
2. **不要修改任何测试文件** —— 改测试让它通过不算修好，验收会检查测试文件未被改动。
3. 不要新增第三方依赖。

可用工具：
- list_files / search_code：定位代码
- read_file：读文件（输出带行号）
- apply_patch：改代码（old 片段必须在文件中唯一出现，否则会被拒绝）
- run_tests：跑测试，返回真实退出码

建议流程：先跑一次 run_tests 看失败在哪 → 读失败信息与相关代码，定位**根因**（失败点不一定是根因所在）→
apply_patch 修改 → 再跑 run_tests 确认。

如果一次没修好，根据失败信息继续；但**不要靠改测试来让测试通过**。

做完后，用一段话说明：你改了哪几处、每处的根因是什么。"""

# ── 第三阶段 P0：**实例入口**的题面（SWE-bench 语义）─────────────────────────
# 用途：让 harness 把 agent 指向 `eval-runs/<run_id>/<instance_id>/repo` 这类**任意实例工作区**，
# 而不是 `fixtures/` 下的小靶场。题面正文来自 `--problem-file`（内容 = 预注册 §3.4 规定的
# 「修复提交的提交信息原文」，由实例构造器生成），**不在本文件里代笔**。
# 与 TASK_BASE 的区别只有两点：①问题描述是真实的、来自外部文件；②不承诺 README 里写了约束
# （真实仓库的 README 不会为评测而改）。其余纪律（不许改测试、不新增依赖）保持一致 ——
# 只不过这次的「不许改测试」由 harness 在判定前**强制剥离**来保证（预注册 §5.3），
# 不依赖 agent 自律。
INSTANCE_TASK_TEMPLATE = """你在一个真实的开源 Python 仓库里工作，仓库根目录就是当前目录。

任务描述（来自该仓库的真实修复记录）：
---
{problem}
---

要求：**让与该问题相关的测试通过**，并且不要破坏仓库里其它已有的测试。

硬约束：
1. **不要修改任何测试文件** —— 判定前测试侧改动会被强制剥离，改测试不会让判定变绿。
2. 不要新增第三方依赖。
3. 改动应当是对根因的定点修复，不要做无关的大范围重构。

可用工具：
- list_files / search_code：定位代码
- read_file：读文件（输出带行号）
- apply_patch：改代码（old 片段必须在文件中唯一出现，否则会被拒绝）
- run_tests：跑测试，返回真实退出码

建议流程：先跑一次 run_tests 看失败在哪 → 读失败信息与相关代码，定位**根因**
（失败点不一定是根因所在）→ apply_patch 修改 → 再跑 run_tests 确认。

做完后，用一段话说明：你改了哪几处、每处的根因是什么。"""

# ④ 层才追加的一段。**不并进 TASK_BASE**：题面邀请一个**不存在的能力**
# 比不提它更坏 —— 探针 `--tools Agent` 那次就是这么空转的
# （341 条消息、0 次调用、还不报错）。能力与邀请必须同时出现。
TASK_SUBAGENT_NOTE = """

关于子代理（可选，不是必须）：
- 你可以用 Agent 工具派一个 `scout`。它是**只读**的：能 list_files / search_code /
  read_file / repo_map / run_tests，**不能改任何文件**。
- 它适合干的活：失败点分散在**多个文件**、或者某个文件很大（几千行），
  你不想把整份文件正文读进自己的上下文时，把「定位根因」这一步派给它。
- 它回报的是**结论**（文件:行 + 根因 + 建议改法），不是改动。**改动仍然由你自己做。**
- 它可能答错。它给的 `文件:行`，动手前用 read_file 自己核一眼。
- **不要为了用而用**：如果失败点集中在一处、或者你已经知道根因，直接改更快。"""

# ⚠️ 只用于**通道阳性对照**，不参与 A/B。
# 教训来自探针：`--tools` 里写一个不认识的名字**不报错**，而是静默变成
# 「零个内置工具」，与阴性对照行为完全同形。所以「这次没派子代理」这句话
# 有两种完全不同的含义 ——「通道不通」或「模型不选它」——
# 只有让题面**强制**派一次，才能把它们分开。没有这一步，
# adoption=0 这个数字是不可解释的。
TASK_SUBAGENT_FORCE = """

【通道检查 — 本次运行强制】你必须**先用 Agent 工具派一次 `scout` 子代理**，
然后才能自己动手改代码。这一条优先于上面「不要为了用而用」。"""


def build_task(enable_subagent: bool, force_spawn: bool = False,
                policy_suffix: str | None = None) -> str:
    """题面。子代理那段只在**真的放开了 spawn 工具**时才出现。

    force_spawn 只在通道阳性对照里为真（见 TASK_SUBAGENT_FORCE）。
    policy_suffix：P1 驱动派发臂的追加段（陈述句索引，见 policy_dispatch）。
      ⚠️ 与 enable_subagent 互斥使用 —— A/B/C 三臂的题面构造路径逐字不变。
    """
    if not enable_subagent:
        return TASK_BASE + (policy_suffix or "")
    task = TASK_BASE + TASK_SUBAGENT_NOTE
    return task + TASK_SUBAGENT_FORCE if force_spawn else task


def build_instance_task(problem_file: Path, *,
                        policy_suffix: str | None = None) -> str:
    """**实例入口专用**的题面：直接读 `--problem-file` 的正文（第三阶段 P0 的 SWE-bench 语义）。

    ⚠️ 为什么单独一条函数而不是改 `build_task`：
      `--fixture` 路（A/B/C 三臂 + 全部既有产物）依赖 `TASK_BASE` **逐字节不变**；
      把实例题面塞进 `build_task` 会让「这一轮用的是哪个题面」变成靠参数回忆的事。
      这里保持两条路彻底分开：不传 `--problem-file` 时 `build_task` 原样返回，
      传了才走这里。

    题面内容**原文来自预注册 §3.4**（修复提交的提交信息），文件由构造器/评测脚本生成；
    这里只做「读进来 + 拼上仓库事实」，不改写、不润色、不补充提示。

    policy_suffix（P1 C 臂）：driver 派发索引追加段。**只有**当它非空时才追加到实例题面
    之后（W3 判据：C 相对 A 只多出这一段；A/B 永远不带）。缺省 None ⇒ 与改动前逐字同形。
    """
    body = problem_file.read_text(encoding="utf-8").strip()
    task = INSTANCE_TASK_TEMPLATE.format(problem=body)
    if policy_suffix:
        task = task + policy_suffix
    return task


# ── 靶场与验证（**不经过 agent**，这是独立判据）────────────────────────────
def run_git(args: list[str]) -> subprocess.CompletedProcess:
    """在**靶场自己**里跑 git。

    ⚠️ cwd 用 FIXTURE 而不是仓库根，这样两种靶场都能工作：
      · 仓库内的（fixtures/small_pkg）：git 会用本仓库，`. ` 相对 cwd 解析 → 只影响该子目录
      · 仓库外的（../targets/more-itertools）：用目标自己的 git 历史
    路径一律写 `.`，不再拼接前缀 —— 前缀拼错是"静默改错地方"的经典来源。
    """
    return subprocess.run(["git", *args], cwd=str(FIXTURE),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def reset_fixture() -> str:
    """把靶场还原到版本控制里的初始状态。

    两步，缺一不可：
      ① `git checkout` 还原**被修改**的已跟踪文件
      ② `git clean -fd` 删掉 agent **新建的未跟踪文件**
         （`.` 限在靶场目录内；靶场本身在版本控制里，所以不会误删我的东西）

    ⚠️ **跑完必须再调一次**（见 main() 第 4 步）——
        靶场是被 git 跟踪的文件，而 agent 会改它。不还原的话，
        下一次 `git add -A` 会把 agent 的改动当成我的改动提交进去。
        已经踩过一次：`d46c379` 里带进了 `pricing.py` 的修复，
        导致靶场不再是"坏的"（pytest 从 13 failed 变成 0 failed）。

    ⚠️ 对**外部大靶场**还有一层要求：注入的缺陷必须已经在它自己的 HEAD 里
        （`setup_target.py` 会提交）。否则 `git checkout` 会把缺陷一起还原掉。
    """
    r1 = run_git(["checkout", "--", "."])
    r2 = run_git(["clean", "-fd", "."])
    parts = [(r1.stdout + r1.stderr).strip(), (r2.stdout + r2.stderr).strip()]
    return " | ".join(p for p in parts if p) or "(无输出)"


def run_pytest() -> tuple[int, str]:
    """独立跑一次靶场测试。**这是成功与否的唯一判据。**"""
    r = subprocess.run(
        [PYTHON, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(FIXTURE), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1800,
    )
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def pytest_counts(text: str) -> tuple[int, int]:
    """从 pytest 输出里取（失败数, 通过数）。取不到就是 (0, 0)。

    口径必须和 `setup_hard_target.py:failed_count` 一致 —— **同一个数字，
    建靶场时用它校验、跑批时用它守门**，两处用不同正则就会得到两个"都对"的数。
    只看末尾 1500 字符：pytest 的汇总行在最后，而前面可能出现零星的 "1 failed"
    （例如某个 subtest 的局部报告），扫全文会取到错的那个。
    """
    failed = passed = 0
    for m in re.finditer(r"(\d+) (failed|passed)", text[-1500:]):
        n, kind = int(m.group(1)), m.group(2)
        if kind == "failed":
            failed = max(failed, n)
        else:
            passed = max(passed, n)
    return failed, passed


def repo_prefix() -> str:
    """cwd 在仓库根之下的相对前缀（cwd 就是仓库根时为空串）。

    ⚠️ 为什么必须问 git 而不能自己拼：`fixtures/small_pkg` 在**本仓库之内**，
    它的仓库根是 `E:\\aicoding\\mini-claude-code`；而 `../targets/more-itertools`
    有自己独立的 git 历史，仓库根就是它自己。两种靶场的前缀不一样，
    只有 git 知道当前是哪一种。
    """
    r = run_git(["rev-parse", "--show-prefix"])
    return (r.stdout or "").strip().replace("\\", "/")


def changed_files() -> list[str]:
    """靶场内被改动的文件，路径**相对于靶场根**。

    ⚠️ `git status --porcelain` 输出的是**相对仓库根**的路径，而且
    `--porcelain` 会强制忽略 `status.relativePaths` 配置 —— 「cwd 换了输出就跟着换」
    这个直觉是错的（`git status` 也不接受 `--relative`）。实测：
        cwd = fixtures/small_pkg → ` M fixtures/small_pkg/inventory/pricing.py`
        多出来的那一层前缀会被 scope 判成「越界改动」→ 干得完全正确却判定失败。

    这里用 `git rev-parse --show-prefix` 把那一层剥掉：
      · 仓库内的靶场 → 前缀 `fixtures/small_pkg/` → 得到 `inventory/pricing.py`
      · 仓库外的靶场 → 前缀为空串        → 原样返回
    两种靶场于是输出同一种形状，`FIXTURE_RULES` 里的 scope 才能两边通用。
    """
    r = run_git(["status", "--porcelain", "--", "."])
    prefix = repo_prefix()
    out = []
    for line in (r.stdout or "").splitlines():
        if len(line) > 3:
            p = line[3:].strip().strip('"').replace("\\", "/")
            if prefix and p.startswith(prefix):
                p = p[len(prefix):]
            out.append(p)
    return sorted(out)


def git_head() -> str:
    """靶场自己的 HEAD。

    大靶场是带独立 git 历史的第三方仓库（`../targets/more-itertools`），
    不记这个就没有「同一次运行」可言：指标挂在哪个源码版本上，事后无从对账。
    """
    r = run_git(["rev-parse", "HEAD"])
    return (r.stdout or "").strip() or "(未知)"


def fixture_dirty() -> bool:
    """靶场是否有未提交改动。

    正常情况应当是干净的（本轮开头刚 reset 过）。不干净就意味着
    **这一轮跑的代码不是 HEAD 里的那一份** —— 而所有指标都是拿 HEAD 当锚点的。
    """
    r = run_git(["status", "--porcelain", "--", "."])
    return bool((r.stdout or "").strip())


def tool_result_text(block: ToolResultBlock) -> str:
    """把 ToolResultBlock.content 归一成纯文本。

    ⚠️ content 的**实际形状是 dict**（如 `{"result": "…"}`），不是字符串。
    第一版直接 `str(content)`，于是 summarize() 里按 "退出码 =" 切出来的
    是一整段带分隔线的文本 —— 指标算错，而且错得不明显（看起来"有值"）。
    这类 bug 的代价：拿着错数字去写验证记录。
    """
    content = block.content
    if isinstance(content, dict):
        return str(content.get("result") or content.get("text") or content)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("result") or item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


# ── ④ 子代理流量的记录 ────────────────────────────────────────────────────
# Task* 生命周期消息的类名。**按类名分派，不 import 这几个类**：
# 它们在这版 SDK 的 __init__ 里不一定导出，而 import 失败会让整个 agent
# 起不来 —— 为一个辅助记录把主流程搞挂不划算。类名对不上就当它不存在。
TASK_MESSAGE_TYPES = (
    "TaskStartedMessage", "TaskProgressMessage",
    "TaskUpdatedMessage", "TaskNotificationMessage",
)


def record_subagent_block(block: Any, parent_id: str, events: list[dict], idx: int,
                          recorder: TraceRecorder | None) -> int:
    """记一条**子代理流**里的块，返回新的 idx。

    ⚠️ 为什么不并进父流：`tool_calls_total` / `run_tests_exit_codes` /
    `read_count` 这些指标是前几轮所有实验的**被解释变量**。子代理的 tool_use
    一旦混进去，同一个指标在「加不加 ④ 层」两种条件下就不是同一个东西了 ——
    这种口径漂移不会报错，只会让两臂的数字没法比。所以子代理的流量单独用
    `subagent_*` 类型记，父流口径**一个字都不动**。
    """
    if isinstance(block, ToolUseBlock):
        idx += 1
        events.append({"i": idx, "type": "subagent_tool_use", "tool": block.name,
                       "input": plain(block.input), "parent_tool_use_id": parent_id})
        if recorder is not None:
            recorder.note("subagent_tool_call", parent_tool_use_id=parent_id,
                          tool=block.name, tool_use_id=getattr(block, "id", None),
                          input=plain(block.input))
        print(f"\n  [subagent tool_use] {block.name}  "
              f"{json.dumps(plain(block.input), ensure_ascii=False)[:200]}", flush=True)
    elif isinstance(block, ToolResultBlock):
        idx += 1
        full = tool_result_text(block)
        events.append({"i": idx, "type": "subagent_tool_result", "chars": len(full),
                       "text": full[:MAX_EVENT_TEXT], "parent_tool_use_id": parent_id})
        if recorder is not None:
            recorder.note("subagent_tool_result", parent_tool_use_id=parent_id,
                          tool_use_id=getattr(block, "tool_use_id", None),
                          is_error=bool(block.is_error), chars=len(full),
                          preview=full[:MAX_EVENT_TEXT])
        print(f"  [subagent tool_result] {full[:300]}", flush=True)
    elif isinstance(block, TextBlock):
        idx += 1
        events.append({"i": idx, "type": "subagent_text", "chars": len(block.text),
                       "text": block.text[:MAX_EVENT_TEXT], "parent_tool_use_id": parent_id})
        if recorder is not None:
            recorder.note("subagent_text", parent_tool_use_id=parent_id,
                          chars=len(block.text), preview=block.text[:MAX_EVENT_TEXT])
        print(f"\n  [subagent] {block.text[:400]}", flush=True)
    elif isinstance(block, ThinkingBlock):
        if recorder is not None:
            recorder.note("subagent_thinking", parent_tool_use_id=parent_id,
                          chars=len(getattr(block, "thinking", "") or ""))
    else:
        if recorder is not None:
            recorder.unknown_block("subagent", block)
    return idx


def record_task_message(message: Any, events: list[dict], idx: int,
                        recorder: TraceRecorder | None) -> int:
    """记一条 Task* 生命周期消息，返回新的 idx。不是 Task* 就原样返回。

    ⚠️ `TaskProgressMessage.usage` 是**子代理**的用量，它**不在**
    `ResultMessage.usage` 里。不记这条，④ 层的成本会静默偏低 ——
    探针实测：父层 input_tokens 只从 1,928 涨到 4,482（+132%），
    而真实总成本从 0.0287 涨到 0.2516（8.8×）。
    """
    name = type(message).__name__
    if name not in TASK_MESSAGE_TYPES:
        return idx
    idx += 1
    fields = {k: plain(v) for k, v in vars(message).items() if k != "usage"}
    usage = plain(getattr(message, "usage", None))
    events.append({"i": idx, "type": "task_message", "task_msg": name,
                   "fields": fields, "usage": usage})
    if recorder is not None:
        recorder.note("task_message", task_msg=name, fields=fields, usage=usage)
    print(f"\n  [{name}] {json.dumps(fields, ensure_ascii=False)[:300]}", flush=True)
    return idx


def _mcp_server_env(*, repo_root: Any, repo_map_default: str,
                    findings_dir: Path | str | None = None) -> dict[str, str]:
    """**唯一**一处装配「传给 stdio MCP server 的 env」（W4 的证据与实跑同源）。

    ⚠️ 为什么抽成函数而不是留在 run_agent 里内联：预注册 W4 要逐键比对三臂的 server env，
    判据必须作用在**真正生效的那份**上。在外面手抄一份 dict，早晚和这里分叉（陷阱 14）。
    不传 findings_dir 时输出与改动前逐字同形（A/B 臂就是这个形状）。
    """
    return {
        # 把靶场根显式传给 server —— 不靠 server 的默认值，
        # 否则"这次跑的是哪个靶场"会变成一件要靠猜的事。
        "AGENT_REPO_ROOT": str(repo_root),
        # 同理显式传 A/B 变量：不靠父进程继承（能继承，但不可靠 —— 见上方注释）
        REPO_MAP_ENV_KEY: repo_map_default,
        # P2：policy 臂才给父层 server findings 目录（其余臂不出现这个键，
        # server 用自己的默认值 —— 与改动前同形）。
        **({FINDINGS_DIR_ENV_KEY: str(findings_dir)} if findings_dir else {}),
    }


# ── P1 三臂接线自检的**唯一落盘函数**（eval/harness/arm_check.py 引用它）──────
def arm_report_row(*, argv: list[str], repo_map_default: str, arm_source: str,
                   allow_write: bool, enable_subagent: bool, force_spawn: bool,
                   policy_mode: bool, max_turns: int, turn_source: str,
                   task_text: str | None, findings_dir: str | None,
                   server_script: str, python_exe: str,
                   policy: dict | None = None) -> dict:
    """把「这一次运行**真的**传给 agent 的东西」组装成一行可核对的记录。

    ⚠️ 为什么必须由 coding_agent.py 自己产出、而不是让自检脚本在外面拼：
    W2/W4 要判的是**真正生效的那份装配**。在外面手抄一份 options/env，
    早晚和 `run_agent()` 里的实现分叉 —— 那正是本仓库反复栽过的失效模式（陷阱 14）。
    所以这里的每一项都取自 main() 里已经算出来的实参，并且 server env 走
    `_mcp_server_env()`（与 run_agent 装配 options 时**同一个函数**）。

    字段与预注册的对应：
      · argv / argv_tail            → W1（逐 token diff 只多出臂开关）
      · sandbox                     → W2（options 装配差异；由 sandbox_spec 现算）
      · task_sha256 / task_chars    → W3（题面差异）
      · mcp_server_env              → W4（stdio server 的 env）
      · max_turns / turn_source     → K4（三臂一律不传 --max-turns ⇒ 实例默认 120）
      · policy                      → §1.2 G-K（组键、K_expected、index_chars、worktree）
    """
    import hashlib as _hl
    server_env = _mcp_server_env(repo_root=(argv[argv.index("--repo-root") + 1]
                                            if "--repo-root" in argv else None),
                                 repo_map_default=repo_map_default,
                                 findings_dir=findings_dir)
    row = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "argv": [str(a) for a in argv],
        "argv_str": " ".join(str(a) for a in argv),
        "flags_present": sorted({t for t in map(str, argv) if t.startswith("--")}),
        "repo_map_default": repo_map_default,
        "repo_map_default_source": arm_source,
        "allow_write": allow_write,
        "subagent": enable_subagent,
        "subagent_forced": force_spawn,
        "delegate": ("policy" if policy_mode else None),
        "sandbox": sandbox_spec(allow_write, enable_subagent, policy_mode=policy_mode),
        "max_turns": max_turns,
        "max_turns_source": turn_source,
        "task_sha256": (_hl.sha256((task_text or "").encode("utf-8")).hexdigest()
                        if task_text is not None else None),
        "task_chars": (len(task_text or "") if task_text is not None else None),
        "mcp_server": {"name": SERVER_NAME, "command": python_exe,
                       "args": [str(server_script)], "env": server_env},
        "policy": policy,
    }
    return row


def append_arm_report(path_str: str, row: dict) -> None:
    """追加一行（UTF-8 + flush + fsync）。写失败不影响主流程 —— 但自检会看到缺行。"""
    p = Path(path_str)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ── agent 循环 ────────────────────────────────────────────────────────────
async def run_agent(env: dict[str, str], *, allow_write: bool,
                    repo_map_default: str = "summary",
                    enable_subagent: bool = False,
                    force_spawn: bool = False,
                    recorder: TraceRecorder | None = None,
                    policy_suffix: str | None = None,
                    findings_dir: Path | None = None,
                    instance_task: str | None = None,
                    max_turns: int = MAX_TURNS) -> tuple[Any, list[dict]]:
    """跑一次 agent 循环。返回 (ResultMessage|None, 事件序列)。

    repo_map_default: 传给 MCP 子进程的 AGENT_REPO_MAP_DEFAULT（A/B 实验的唯一变量）。

    enable_subagent: 是否放开 ④ 层。它同时管三件事 —— spawn 工具、scout 名册、
    题面里那段邀请。绑在一起是有意的：只放工具不写题面，模型不知道有它；
    只写题面不放工具，就是探针里那次「对一个不存在的能力的邀请」（空转还不报错）。

    recorder: 全链路 trace 记录器；为 None 就不记。
    `events` 与 `trace` **并存**，不是替代关系：
      · events 是原始流水，是下游 context_report.py / ab_adoption_audit.py 的判据来源，
        动它等于让之前所有实验的判据换一套口径；
      · trace 补的是 events 里**根本没存**的那几个字段 —— tool_use_id、is_error、
        逐回合 usage、观察时刻（见 agent/trace.py 开头那张表）。

    policy_suffix / findings_dir（P1 驱动派发臂）：把 driver 侦察索引追加进题面、
    给父层 server 传 AGENT_FINDINGS_DIR。两者都不传时 options 装配逐字同旧行为。
    """
    spec = sandbox_spec(allow_write, enable_subagent, policy_mode=bool(policy_suffix))

    options = ClaudeAgentOptions(
        cli_path=CLI_PATH,
        mcp_servers={
            SERVER_NAME: {
                "type": "stdio",
                "command": PYTHON,
                "args": [str(SERVER_SCRIPT)],
                # 把靶场根与实验变量**显式**传给 server —— 不靠 server 的默认值，
                # 否则"这次跑的是哪个靶场/哪一臂"会变成一件要靠猜的事。
                # ⚠️ env 的唯一装配处是 _mcp_server_env()：预注册 W4 的逐键比对与这里
                #    引用同一个函数，避免「检查用的副本」与「生效的值」分叉（陷阱 14）。
                "env": _mcp_server_env(repo_root=FIXTURE,
                                       repo_map_default=repo_map_default,
                                       findings_dir=findings_dir),
            }
        },
        # ── 让它只有一条通路：沙箱工具 ──────────────────────────────────
        # 内置工具默认为空 —— 「禁用全部内置工具」（Bash / Read /
        # Edit / Write / Grep / Glob / WebFetch …）。
        #
        # ⚠️ 这里踩过一次，别再用 allowed_tools 当白名单：
        #    allowed_tools 的语义是「**免询问自动放行**」，不在其中的工具只是
        #    「需要权限」，并没有从模型上下文里消失。反向对照实测（--no-write）：
        #    agent 仍然调了 Bash 19 次、Read 9 次、Edit 2 次、Write 2 次，
        #    其中一次 Read 读了沙箱**之外**的 out/agent-verify-pass.log。
        #    SDK 文档原话：「To restrict which tools are available at all, use tools」。
        #
        # ④ 层是这条规则的**第一个例外**，而且必须是显式的：只有 enable_subagent
        # 为真时 builtin_tools 才是 ["Task"]，为假时仍然是 [] —— 走上层不放宽。
        tools=spec["builtin_tools"],
        # 下面三条是纵深防御：即使 tools 的行为在将来某版变化，也仍然拦得住。
        allowed_tools=spec["allowed_tools"],
        disallowed_tools=spec["disallowed_tools"],
        # 名册与放行必须同时在场。为假时传 None 而不是空 dict ——
        # 空 dict 语义上也是「有名册，只是没内容」，会让人以为这一臂本该有名册。
        agents=build_agents() if enable_subagent else None,
        permission_mode=PERMISSION_MODE,   # 不提示；未预先批准的一律拒绝
        # 只加载我们显式传入的 MCP server —— 不让 ~/.claude.json / .mcp.json
        # 里的注册混进实验（否则实验条件说不清）。
        strict_mcp_config=True,
        # 必须是 []：写成 ["user"] 会让 settings.json 的 env 静默覆盖 options.env
        # （见陷阱 1，VERIFY.md 有原始输出）。同时也把内层 CLI 的 CLAUDE.md 挡在外面。
        setting_sources=SETTING_SOURCES,
        env=env,
        max_turns=max_turns,
        stderr=lambda line: print(f"[cli stderr] {line}", file=sys.stderr, flush=True),
    )

    events: list[dict] = []
    result: Any = None
    idx = 0

    async for message in query(prompt=(instance_task if instance_task is not None
                                       else build_task(enable_subagent, force_spawn,
                                                       policy_suffix=policy_suffix)),
                               options=options):
        # ⚠️ 子代理的消息与父流**长得一模一样**（同样是 AssistantMessage /
        #    UserMessage），区别只在 `parent_tool_use_id` 有没有值。
        #    不在这里分流，子代理的 tool_use 会被算进父 agent 的 tool_calls_total ——
        #    而那个指标是前几轮所有实验的被解释变量（详见 record_subagent_block）。
        nested_id = getattr(message, "parent_tool_use_id", None)
        if nested_id and isinstance(message, (AssistantMessage, UserMessage)):
            for block in message.content:
                idx = record_subagent_block(block, nested_id, events, idx, recorder)
            continue
        if isinstance(message, AssistantMessage):
            if recorder is not None:
                # 一回合 = 一条 AssistantMessage。逐回合 usage 就挂在这里。
                recorder.begin_turn(getattr(message, "usage", None))
            for block in message.content:
                idx += 1
                if isinstance(block, TextBlock):
                    text = block.text[:MAX_EVENT_TEXT]
                    events.append({"i": idx, "type": "assistant_text", "text": text})
                    if recorder is not None:
                        recorder.text(block.text)
                    print(f"\n[assistant] {text}", flush=True)
                elif isinstance(block, ToolUseBlock):
                    events.append({
                        "i": idx, "type": "tool_use",
                        "tool": block.name, "input": block.input,
                    })
                    if recorder is not None:
                        recorder.tool_use(block)
                    print(f"\n[tool_use] {block.name}  {json.dumps(block.input, ensure_ascii=False)[:300]}",
                          flush=True)
                elif isinstance(block, ThinkingBlock):
                    # 思维原文是「它为什么这么做」最直接的证据。旧产物里这类块被
                    # 整个丢掉（不认识就跳过），于是只剩行为、没有理由。
                    # 字段名实测是 `thinking`（不是 `text`）—— 用 dataclasses.fields()
                    # 在 claude_agent_sdk.types 上核过，见交付记录。
                    if recorder is not None:
                        recorder.thinking(getattr(block, "thinking", "") or "")
                else:
                    # 不认识的块**不静默丢弃** —— 丢掉的代价是将来行为变了而 trace 里什么都没有。
                    if recorder is not None:
                        recorder.unknown_block("assistant", block)
        elif isinstance(message, UserMessage):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    idx += 1
                    full = tool_result_text(block)
                    # ⚠️ 存进事件的 `text` 是**截断过的**（控制产物体积），
                    #    所以必须另存**真实字符数**。
                    #    否则「上下文成本」根本测不出来：截断会把所有大输出
                    #    都记成同一个上限值，指标失去分辨力，
                    #    于是"改成 repo_map 到底省了多少"就无从比较。
                    events.append({
                        "i": idx,
                        "type": "tool_result",
                        "chars": len(full),              # 真实长度（未截断）
                        "text": full[:MAX_EVENT_TEXT],   # 截断存档
                    })
                    if recorder is not None:
                        recorder.tool_result(block, text=full)
                    print(f"[tool_result] {full[:400]}", flush=True)
                elif isinstance(block, TextBlock):
                    if recorder is not None:
                        recorder.user_text(block.text)
                else:
                    if recorder is not None:
                        recorder.unknown_block("user", block)
        elif isinstance(message, ResultMessage):
            result = message
        else:
            # Task* 生命周期消息（开始 / 进度 / 状态 / 完成）。旧版本把它们跟
            # SystemMessage 一起静默丢掉 —— 于是「子代理花了多少」这个问题
            # 在产物里根本没有答案（成本会静默偏低，见 record_task_message）。
            # 不是 Task* 的（SystemMessage 等）原样忽略。
            idx = record_task_message(message, events, idx, recorder)

    return result, events


# ── 主流程 ────────────────────────────────────────────────────────────────
def result_summary(result: Any) -> dict[str, Any] | None:
    """`ResultMessage` 上**以前一个都没被留下**的那组字段。

    最要紧的是 `permission_denials`：陷阱 15（沙箱洞）那次如果当时记了它，
    「内置工具被放行」一眼就能看出来，不必等反向对照跑完再人工去数调用次数。
    `errors` / `api_error_status` / `terminal_reason` 同理 —— 它们是
    「这一轮为什么停」的**结构化**答案，而不是让人去 grep 日志。
    """
    if result is None:
        return None
    keys = ("subtype", "is_error", "num_turns", "duration_ms", "duration_api_ms",
            "total_cost_usd", "stop_reason", "terminal_reason", "api_error_status",
            "permission_denials", "errors", "session_id",
            # ⚠️ `usage` 必须在这一组里。实测：`AssistantMessage.usage` 的
            #    `output_tokens` **键存在、值恒为 0**（17 个回合全是 0），
            #    整个运行里唯一报出真实输出 token 的地方就是 ResultMessage。
            #    不记它，trace 就答不了「这一轮贵在哪」。
            "usage", "model_usage")
    return {k: plain(getattr(result, k, None)) for k in keys}


def summarize(events: list[dict]) -> dict:
    """从事件序列里抽出指标 —— 这些面试可直接引用。"""
    uses = [e for e in events if e["type"] == "tool_use"]
    results = [e for e in events if e["type"] == "tool_result"]
    per_tool: dict[str, int] = {}
    for e in uses:
        per_tool[e["tool"]] = per_tool.get(e["tool"], 0) + 1

    # 每次 run_tests 的真实退出码序列 —— 这是「失败自省」的直接证据：
    # 序列里是 [1, 0] 说明它改完复验了；是 [1, 1, 1] 说明在盲目重试。
    test_runs: list[int] = []
    for e in results:
        m = re.search(r"退出码\s*=\s*(\d+)", e["text"])
        if m:
            test_runs.append(int(m.group(1)))

    return {
        "tool_calls_total": len(uses),
        "tool_calls_per_tool": per_tool,
        "run_tests_exit_codes": test_runs,
        "run_tests_count": per_tool.get(f"mcp__{SERVER_NAME}__run_tests", 0),
        "apply_patch_count": per_tool.get(f"mcp__{SERVER_NAME}__apply_patch", 0),
        "search_count": per_tool.get(f"mcp__{SERVER_NAME}__search_code", 0),
        "read_count": per_tool.get(f"mcp__{SERVER_NAME}__read_file", 0),
        # A/B 实验的被解释变量：「这次运行采用了 repo_map 吗」。
        # ⚠️ 判据只看这里的**调用序列计数**，不看 agent 自述 —— 自述会说
        #    "我用了仓库地图"而实际没调，也会反过来。定义先定死，不许事后改。
        "repo_map_count": per_tool.get(f"mcp__{SERVER_NAME}__repo_map", 0),
        "used_repo_map": per_tool.get(f"mcp__{SERVER_NAME}__repo_map", 0) > 0,
    }


def summarize_subagent(events: list[dict]) -> dict:
    """④ 层的**独立**账。

    为什么单独一个函数、单独一组键，而不并进 summarize()：summarize() 里
    每一个键都是前几轮实验的被解释变量。把子代理的调用数加进
    `tool_calls_total`，会让「加 ④ 层之前」和「之后」的同一个指标变成两个东西。
    新的量只用**新键**，父流口径一个字不动。

    ⚠️ 「adoption」只认**调用序列计数**，不认 agent 自述 ——
    与 used_repo_map 同一条纪律：自述会说"我派了子代理"而实际没派。
    """
    spawns = [e for e in events
              if e["type"] == "tool_use" and e["tool"] == SUBAGENT_SPAWN_NAME]
    nested = [e for e in events if str(e["type"]).startswith("subagent_")]
    task_msgs = [e for e in events if e["type"] == "task_message"]
    by_type: dict[str, int] = {}
    for e in task_msgs:
        by_type[e["task_msg"]] = by_type.get(e["task_msg"], 0) + 1

    # 子代理用量**只在** TaskProgressMessage.usage 里，ResultMessage.usage 里没有。
    # 实测样本看着是累计值（1,898 → … → 9,750），所以取 max 当作「这个子代理
    # 一共用了多少」的估计。**是估计，不是精确值** —— 名字里带 estimate 免得
    # 下游把它当成和 input_tokens 同级的实测量。
    tokens = [int(m["usage"]["total_tokens"]) for m in task_msgs
              if isinstance(m.get("usage"), dict)
              and isinstance(m["usage"].get("total_tokens"), (int, float))]
    return {
        "subagent_enabled": True,
        "subagent_spawn_calls": len(spawns),
        "subagent_adopted": len(spawns) > 0,
        "subagent_messages": len(nested),
        "subagent_tool_calls": sum(1 for e in nested if e["type"] == "subagent_tool_use"),
        "subagent_text_chars": sum(int(e.get("chars") or 0) for e in nested
                                   if e["type"] == "subagent_text"),
        "subagent_result_chars": sum(int(e.get("chars") or 0) for e in nested
                                     if e["type"] == "subagent_tool_result"),
        "task_messages_per_type": by_type,
        "subagent_tokens_estimate": max(tokens) if tokens else None,
        "subagent_token_samples": len(tokens),
    }


async def main() -> int:
    global FIXTURE

    # 必须在任何 print 之前：控制台是 GBK，打印装不下的字符会崩掉证据（已踩过一次）。
    force_utf8_output()

    ap = argparse.ArgumentParser()
    ap.add_argument("--max-turns", type=int, default=None,
                    help="agent 回合上限。缺省：--fixture 路 = %d（与改动前一致）；"
                         "**实例模式（--repo-root）= %d**（真实仓库探索成本高一个量级，"
                         "t5 冒烟实测 40 回合会在 agent 已改对后仍被截断）。"
                         "显式给值则两边都用这个值。"
                         % (MAX_TURNS, INSTANCE_MAX_TURNS))
    ap.add_argument("--repo-root", default=None,
                    help="第三阶段 P0 **实例入口**：直接指定实例工作区（绝对路径），"
                         "例如 E:\\aicoding\\eval-runs\\<run_id>\\<instance_id>\\repo。"
                         "给了它就**不再解析 --fixture**（两者互斥）。"
                         "用途：让 harness 把 agent 指向任意实例，而不是 fixtures/ 下的小靶场。")
    ap.add_argument("--problem-file", default=None,
                    help="第三阶段 P0 **实例入口**：题面正文文件（预注册 §3.4 的提交信息原文）。"
                         "给定后用它替换 TASK_BASE 的问题描述；不传 = 走原来的固定题面。")
    ap.add_argument("--label", default="local", help="本次运行的标签（决定落盘文件名）")
    ap.add_argument("--fixture", default=None, choices=sorted(FIXTURE_RULES),
                    help="靶场名：fixtures/ 下的小靶场，或 ../targets/ 下的大靶场"
                         "（缺省 mini_repo；与 --repo-root 互斥）")
    ap.add_argument("--no-write", action="store_true",
                    help="反向 A：拿掉 apply_patch，看它会不会谎报成功")
    ap.add_argument("--subagent", action="store_true",
                    help="④ 层：放开 spawn 工具 + scout 名册 + 题面邀请，"
                         "并把子代理的流量单独记账。"
                         "**默认关闭** —— 不传就是加 ④ 层之前的现状（A/B 的臂 A）。")
    ap.add_argument("--subagent-force", action="store_true",
                    help="**只用于通道阳性对照**：在题面里要求必须派一次 scout。"
                         "「这次没派子代理」有两种含义 —— 通道不通、或模型不选它；"
                         "只有强制派一次才能把它们分开。**不参与 A/B**。")
    ap.add_argument("--delegate", choices=("policy",), default=None,
                    help="P1 **驱动派发**（D 臂）：由 driver 决定派发 —— 解析基线失败、"
                         "按测试类聚成 K 组，每组在第一次模型回合之前派一个 scout"
                         "（快照隔离 + 有界索引 + 截断自动续派 + 独立进程校验）。"
                         "与 --subagent 互斥；默认不加 = 改动前的现状（A 臂逐字同形）。")
    ap.add_argument("--policy-group-mode", choices=("class_name", "test_file"),
                    default=None,
                    help="--delegate=policy 的**分组口径**。缺省行为按入口自动选："
                         "· 实例模式（--repo-root）⇒ 强制 test_file（§1.2 G-K 判据）；"
                         "· fixture 路 ⇒ 强制 class_name（保持第二阶段 D 臂历史产物逐字同形）。"
                         "显式传值只用于机制验证/回归测试；正常 P1 跑批**不要传它**。"
                         "⚠️ 它不是第三个实验变量：只是「让 --delegate=policy 在不同入口下"
                         "各自走正确分组规则」的实现自带物（W1 的 argv diff 仍然只多出 "
                         "`--delegate policy` 两个 token）。")
    ap.add_argument("--scout-turn-cap", type=int, default=None,
                    help="driver 侧 scout 的回合上限（缺省 = SCOUT_MAX_TURNS=%d）。"
                         "**仅用于制造截断场景做机制验证**（P3 负向对照），"
                         "正式跑批一律用缺省值。" % SCOUT_MAX_TURNS)
    ap.add_argument("--repo-map-default", default=None, choices=REPO_MAP_ARMS,
                    help="A/B 实验的**唯一变量**：repo_map 的默认详细度。"
                         "symbols = 臂 A（旧行为，贵且会被截断）；"
                         "summary = 臂 B（新行为，359 字符）。"
                         "不传时取进程环境里的 AGENT_REPO_MAP_DEFAULT，"
                         "两者都没有则用 summary。")
    ap.add_argument("--arm-report-file", default=None,
                    help="P1 三臂接线自检用：把**本次运行真实生效的装配**（argv、沙箱 spec、"
                         "MCP server env、题面 SHA、派发分组与 G-K 判据）写成一行 JSON 追加到"
                         "指定文件。只记「真的传给了什么」，不记「我以为传了什么」（陷阱 14）。"
                         "⚠️ 它不是实验变量：三臂都带同一个路径由 harness 决定，"
                         "内容按臂分开落盘，互不覆盖。")
    args = ap.parse_args()
    # ⚠️ `--fixture` 的缺省值在**解析之后**才填（argparse 的 default=None）：
    # 只有这样才能把「用户没给」与「用户给了 mini_repo」分开 —— 否则
    # `--repo-root X` 会被误认为同时给了 `--fixture mini_repo` 而无法检测互斥。
    # 填完之后，不传 --fixture 的调用路径与改动前**逐字相同**。
    fixture_given = args.fixture is not None
    if args.fixture is None:
        args.fixture = "mini_repo"
    # ④ 层是「A/B 的臂」而不是「默认行为」：不传 --subagent 时，这一轮与加 ④ 层
    # 之前**逐字节相同**（题面、tools、allowed/disallowed 都没变）。
    enable_subagent = bool(args.subagent)
    # --subagent-force 单独出现是没有意义的（通道都没放开，强制派什么？）。
    # 静默忽略它会让"我明明加了 --subagent-force 却没派"变成一件要查代码的事，
    # 所以在入口就拦住。
    if args.subagent_force and not enable_subagent:
        print("[中止] --subagent-force 必须与 --subagent 一起用（通道都没放开）。",
              file=sys.stderr)
        return 2
    force_spawn = bool(args.subagent_force)
    # P1 驱动派发臂。与 --subagent 互斥：前者是「driver 派、模型没有 spawn 通道」，
    # 后者是「模型自愿派」。同时为真就说不清「这一轮是谁在派」，两臂的对照也废了。
    policy_mode = args.delegate == "policy"
    if policy_mode and (enable_subagent or force_spawn):
        print("[中止] --delegate=policy 与 --subagent/--subagent-force 互斥"
              "（驱动派发 vs 模型自愿派发，是两个不同的东西）。", file=sys.stderr)
        return 2
    instance_mode = bool(args.repo_root)   # P1：提前到守卫之前（原守卫引用了后文才赋值的它）
    # ⚠️ **P1 接线改动 #1+分组口径自动选择**：让 `--delegate=policy` 在实例模式下可达
    # （预注册 §1.1 缺口①），并按入口自动选正确的分组规则（缺口②）。
    # 原守卫形如 `policy_mode and args.fixture != "more-itertools-hard"`，且写在
    # `instance_mode` 赋值**之前**：那既把实例模式一律拦死（C 臂根本跑不起来），
    # 又引用了当时还不存在的局部名。现在按「入口」分两侧，两侧各自仍然刚性：
    #   · fixture 路（非实例模式）：**继续只允许 more-itertools-hard**，逐字不变 ——
    #     第二阶段 D 臂把 K=3 分组判据钉在它身上，历史批次的对照条件不许事后放宽；
    #     分组口径强制 class_name（保持历史产物逐字同形）。
    #   · 实例路（--repo-root）：放行；分组口径强制 test_file（§1.2 G-K 判据）。
    #     pilot-12 的 F2P node id 不含 `<Name>Tests` 形状，旧口径会整批回落成 K=1。
    # 这**不是新增第二个变量**：W1 的 argv diff 仍然只允许多出 `--delegate policy`
    # 两个 token；`--policy-group-mode` 由 harness 根据入口自动注入（或不传走缺省），
    # 与 W2 允许 C 多出 read_findings、W4 允许多出 AGENT_FINDINGS_DIR 同理。
    if policy_mode:
        if not instance_mode:
            if args.fixture != "more-itertools-hard":
                print(f"[中止] --delegate=policy 在非实例模式下仍只允许 --fixture "
                      f"more-itertools-hard（收到 {args.fixture!r}）。", file=sys.stderr)
                return 2
            if args.policy_group_mode is None:
                args.policy_group_mode = "class_name"
            elif args.policy_group_mode != "class_name":
                print(f"[中止] 非实例模式下 --policy-group-mode 必须是 class_name"
                      f"（收到 {args.policy_group_mode!r}）。", file=sys.stderr)
                return 2
        else:
            if args.policy_group_mode is None:
                args.policy_group_mode = "test_file"
            elif args.policy_group_mode != "test_file":
                print("[中止] 实例模式下的 --delegate=policy 必须用 --policy-group-mode "
                      "test_file（P1 预注册 §1.2 的 G-K 判据：组键 = 测试文件路径）。"
                      "class_name 口径在 pilot-12 上会整批回落成单组 OTHER（K 恒为 1），"
                      "那测的就不是「按失败组分工」这个干预。", file=sys.stderr)
                return 2
    elif args.policy_group_mode is not None:
        print("[中止] --policy-group-mode 只对 --delegate=policy 有意义。", file=sys.stderr)
        return 2
    scout_turn_cap = args.scout_turn_cap or SCOUT_MAX_TURNS
    if scout_turn_cap != SCOUT_MAX_TURNS and not policy_mode:
        print("[中止] --scout-turn-cap 只对 --delegate=policy 有意义。", file=sys.stderr)
        return 2

    # 解析"A/B 两臂各是什么"的唯一取值。三条来源，**优先级写死**：
    #   命令行 --repo-map-default  >  父进程 AGENT_REPO_MAP_DEFAULT  >  "summary"
    # 非法值直接中止 —— 不静默回落（否则"变量设错"会伪装成"这一臂没效果"）。
    arm_source: str
    if args.repo_map_default:
        repo_map_default, arm_source = args.repo_map_default, "--repo-map-default"
    else:
        raw = os.environ.get(REPO_MAP_ENV_KEY)
        if raw and raw not in REPO_MAP_ARMS:
            print(f"[中止] {REPO_MAP_ENV_KEY}={raw!r} 不是合法值，只支持 {REPO_MAP_ARMS}；"
                  "这里不做静默回落。", file=sys.stderr)
            return 2
        repo_map_default = raw or "summary"
        arm_source = "env:" + REPO_MAP_ENV_KEY if raw else "默认（未设置）"

    # 与 --fixture 互斥：两个都指定就说不清"这次跑的是哪个仓库"，宁可拦下来。
    # 不传 --repo-root 时，下面每一行都不执行 ⇒ `--fixture` 路**逐字不变**。
    if instance_mode and fixture_given:
        print(f"[中止] --repo-root 与 --fixture 互斥（收到 --fixture={args.fixture!r}）；"
              "实例入口请只给 --repo-root。", file=sys.stderr)
        return 2
    if args.problem_file and not instance_mode:
        print("[中止] --problem-file 只在 --repo-root 模式下有意义"
              "（fixture 路的题面是固定的 TASK_BASE，不做替换）。", file=sys.stderr)
        return 2
    if instance_mode:
        root = Path(args.repo_root).expanduser()
        if not root.is_dir():
            print(f"[中止] --repo-root 不是目录：{root}", file=sys.stderr)
            return 2
        if not (root / ".git").exists() and not (root / ".git").is_file():
            # 判定链路的 changed_files()/reset_fixture() 都依赖 git；没有 .git 就没法
            # 采集 agent 的补丁，也就没法判定。提前拦住，别跑到一半才发现。
            print(f"[中止] --repo-root 下没有 .git：{root}", file=sys.stderr)
            return 2
        FIXTURE = root.resolve()
        print(f"实例模式：repo_root={FIXTURE}")
    else:
        FIXTURE = resolve_fixture(args.fixture)

    instance_task: str | None = None
    _instance_problem_file: Path | None = None   # P1：保留到 policy_suffix 定稿后再拼题面
    if args.problem_file:
        pf = Path(args.problem_file).expanduser()
        if not pf.is_file():
            print(f"[中止] --problem-file 不存在：{pf}", file=sys.stderr)
            return 2
        _instance_problem_file = pf
        # ⚠️ **不在这里**就拼题面：C 臂的 policy_suffix 要到下面第 2.4 步才产出。
        # 提前拼会让 C 臂的题面缺索引段 ⇒ W3 / G-C2 判 harness_bug（治疗未施加）。
        # A/B 臂没有 suffix，晚一步拼结果一样；C 臂必须等。
        print(f"实例题面文件已就绪：problem_file={pf}")

    # ── D8（t8）：回合上限。三条来源，优先级写死：
    #   命令行 --max-turns  >  实例模式默认(INSTANCE_MAX_TURNS)  >  MAX_TURNS
    # ⚠ `--fixture` 路不传 --max-turns 时取 MAX_TURNS(=40)，与改动前**逐字相同**。
    if args.max_turns is not None:
        max_turns = args.max_turns
        turn_source = "--max-turns"
    elif instance_mode:
        max_turns = INSTANCE_MAX_TURNS
        turn_source = f"实例模式默认({INSTANCE_MAX_TURNS})"
    else:
        max_turns = MAX_TURNS
        turn_source = f"fixture 默认({MAX_TURNS})"

    out_dir = PROJECT_ROOT / "servers" / "experiments" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / f"agent-{args.label}"

    print("=" * 72)
    print(f"mini Coding Agent  |  label={args.label}  "
          f"{'repo_root=' + str(FIXTURE) if instance_mode else 'fixture=' + str(args.fixture)}  "
          f"allow_write={not args.no_write}")
    print(f"回合上限 = {max_turns}  （来源：{turn_source}）")
    # A/B 实验条件必须**每次运行都打在脸上** —— 它是唯一变量，
    # 若日志里看不出来，"这一轮是哪一臂"就只能靠回忆。
    print(f"repo_map 默认详细度 = {repo_map_default!r}  （来源：{arm_source}）")
    print(f"④ 子代理 = {'开（spawn 工具 + scout 名册 + 题面邀请）' if enable_subagent else '关（现状）'}"
          + ("　⚠️ 通道阳性对照：题面强制派一次" if force_spawn else ""))
    if policy_mode:
        # D 臂的臂身份必须打在脸上（与 repo_map 变量同一条纪律）。
        print(f"P1 驱动派发 = 开（--delegate=policy；scout 回合上限 {scout_turn_cap}"
              f"{'，⚠️ 非缺省值：机制验证用' if scout_turn_cap != SCOUT_MAX_TURNS else ''}）")
    print("=" * 72)

    # 1) 配置
    try:
        env, key_source = load_endpoint_env()
    except ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        print(CONFIG_HINT, file=sys.stderr)
        row = log_usage(None, script=Path(__file__).name, key_source="config-error",
                        extra={"aborted": True, "config_error": str(exc)[:200]})
        print(f"  已记一行（token 为 null）：run={row['run']}")
        return 1

    scrubbed = isolate_from_parent_env()

    print("\n=== key 来源 ===")
    print(f"  {key_source}")
    print("=== 生效的端点配置（密钥已打码）===")
    for k, v in redact(env).items():
        print(f"  {k} = {v}")
    print(f"  cli_path = {CLI_PATH}")
    print(f"  父进程环境被摘掉的键：{scrubbed or '(无)'}")

    # 2) 复位靶场 + 基线
    print("\n=== 复位靶场 ===")
    print(f"  {reset_fixture()}")
    base_code, base_out = run_pytest()
    base_failed, base_passed = pytest_counts(base_out)
    print(f"=== 基线 pytest：退出码 {base_code}　失败 {base_failed}　通过 {base_passed} ===")
    print("  " + "\n  ".join(base_out.splitlines()[-4:]))

    if base_code == 0:
        # ⚠️ **守卫**：靶场一开始就是绿的，这一轮无论 agent 做什么都不构成验证。
        #
        # 为什么要显式拦住，而不是随它跑：这个失效模式**不报错**。
        # 它最可能的成因是「HEAD 里的靶场已经是修复版」——
        # 而靶场的"坏"是 **HEAD 的不变量**，任何把修复提交进 HEAD 的提交都会触发它。
        # 症状会伪装成一次漂亮的成功：基线 0 → 0、判定"成功"、耗时很短。
        #
        # 已经踩过一次：d46c379 误把 agent 的修复提交进 HEAD，
        # 之后每轮都从"已修复"开始，却一路显示成功。
        #
        # ⚠️ **实例模式例外**（第三阶段 P0）：实例工作区的基线是「真实修复提交的父提交」，
        # 它**本来就不一定是红的** —— 缺陷是通过 harness 施加 `test_patch` 才暴露的
        # （预注册 §4.1：起点状态 = 基线 + test_patch）。所以这里不能拦，
        # 否则每一条实例都会被误判成"不构成验证"。判定权威是 harness 的独立 pytest，
        # 不是这个面向 fixtures/ 的守卫。
        if instance_mode:
            print("\n[提示] 实例模式下基线全绿是**正常**的：")
            print("  缺陷由 harness 施加 test_patch 后暴露（§4.1），判定权在 harness，不在本守卫。")
        else:
            print("\n[中止] 靶场初始状态就是通过（0 失败）—— 本次运行不构成验证。")
            print("  最可能的原因：HEAD 里的靶场已经是修复版。")
            print("  查：git log -p -- fixtures/ | Select-String 'subtotal_cents'")
            return 3

    # 2.2) 数量守卫 —— 只有当这个靶场**声明了期望失败数**才生效。
    #
    # 「基线是红的」比「基线坏成我预期的样子」弱得多：缺陷只注进去一部分、
    # `git clean` 没清干净、HEAD 被挪到另一个坏版本，这些情况下退出码照样是 1。
    # 本靶场第一次建就正是这样：静默漏注了 27 个失败，基线只剩 5，守卫放行，
    # 一整批数据会在"靶场比设计的简单得多"的情况下跑完，而且**全程不报错**。
    expect_failed = None if instance_mode else FIXTURE_RULES[args.fixture].get("expect_failed")
    if expect_failed is not None and base_failed != expect_failed:
        print(f"\n[中止] 基线失败数 {base_failed} != 期望 {expect_failed}。")
        print("  可能原因：①缺陷没注全 ②靶场工作区不干净 ③HEAD 被挪到了别的版本。")
        print(f"  查：cd {FIXTURE} ; git rev-parse HEAD ; git status --porcelain")
        return 3
    if expect_failed is None:
        print(f"  （本靶场未声明 expect_failed，只校验「基线是红的」）"
              if not instance_mode else
              "  （实例模式：不校验基线失败数 —— 缺陷由 harness 的 test_patch 暴露，见 §4.1）")

    # 2.5) trace 运行头 —— 在 agent 开跑**之前**把「这次跑的到底是什么」定下来。
    #      重点是 fixture_head 与 sandbox：没有它们就没有「同一次运行」可言
    #      （靶场是带独立 git 历史的第三方仓库，指标挂在哪个版本上事后对不出来）。
    #      ⚠️ 这里**不记 env** —— 里面有凭据；只取不需要打码的 model 名。

    # 2.4) P1 驱动派发（仅 --delegate=policy）：**在父 agent 第一次模型回合之前**
    #      把 K 组侦察全部跑完。题面（含 ≤2000 字符索引）在这里定稿 ——
    #      结论先于任何 turn 进上下文，这就是 D 臂与 B/C 臂的本质区别。
    policy_out: dict | None = None
    policy_error: str | None = None
    policy_suffix: str | None = None
    # §1.2 G-K 的判据结果（非 policy 臂恒为 None ⇒ 旧产物形状不变）。
    gk_check: dict | None = None
    if policy_mode:
        # agent/ 在 sys.path[0]（PROJECT_ROOT）之下，但模块名要带包前缀才找得到。
        import agent.policy_dispatch as pd

        def _load_instance_for_dispatch(repo_root_str: str) -> dict:
            """从工作区路径反查实例 JSON，用于取 test_patch 做 C 臂分组。

            只在 policy_mode + instance_mode 下调用；找不到就抛异常让外层兜底。
            """
            import glob as _glob
            instances_dir = PROJECT_ROOT / "eval" / "instances"
            for p in _glob.glob(str(instances_dir / "*.json")):
                try:
                    with open(p, encoding="utf-8") as fh:
                        obj = json.load(fh)
                except Exception:  # noqa: BLE001
                    continue
                if obj.get("instance_id") and obj.get("repo"):
                    # 匹配条件：工作区 realpath 含 instance_id 子串（run_id 命名规则保证唯一）
                    iid = obj["instance_id"]
                    if iid in repo_root_str:
                        return obj
            raise RuntimeError(f"无法从 {repo_root_str} 反查实例 JSON")

        # ⚠️ **P1 接线改动 #4（t7 repair-round-2）**：实例模式下 coding_agent 看到的
        # 工作区是「base_commit + 未叠 test_patch」的状态，而 F2P 缺陷是通过 harness
        # 施加 test_patch 后才暴露的（预注册 §4.1）。因此 `base_out`（agent 自己跑的
        # 基线 pytest）通常全绿 ⇒ group_failures 拿不到任何 F2P 形状的失败行 ⇒
        # dispatch_grouping_unobservable ⇒ C 臂整批不可测。
        #
        # 修法：实例模式下先在工作区施加 test_patch、跑一次 pytest 拿到「暴露后」的
        # 失败输出用于分组，再 `git checkout -- . && git clean -fdq` 还原到基线状态，
        # 让后续 agent 阶段仍从正确的起点开始。这与 harness 的判定流程同源
        # （run_instance.run_one 也是先 fresh_workspace → apply test_patch → 判定的）。
        #
        # ⚠️ 这不是新增变量：test_patch 是实例定义的固定字段（与 gold patch 一样），
        # 三臂都施加同一份；它只影响 C 臂的分组依据，不影响 A/B 的任何行为。
        dispatch_pytest_text = base_out
        dispatch_source = "agent_baseline"
        if instance_mode and args.problem_file:
            try:
                inst_for_tp = _load_instance_for_dispatch(str(FIXTURE))
                tp = (inst_for_tp.get("test_patch") or "").strip()
                if tp:
                    tp_path = out_dir / "_dispatch_test_patch.diff"
                    # ⚠️ git apply 要求 patch 文件以换行结尾；JSON 里的 test_patch 字段
                    # 可能不带尾换行 ⇒ "corrupt patch at line N"。这里强制补上。
                    tp_body = tp if tp.endswith("\n") else (tp + "\n")
                    tp_path.write_text(tp_body, encoding="utf-8", newline="\n")
                    ap = subprocess.run(
                        ["git", "-C", str(FIXTURE), "apply", "--whitespace=nowarn",
                         str(tp_path)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=120)
                    print(f"[dispatch] git apply test_patch exit={ap.returncode}"
                          f"{' stderr=' + repr(ap.stderr[-200:]) if ap.returncode != 0 else ''}")
                    if ap.returncode == 0:
                        # 实例模式下的 pytest 必须换掉仓库自带 ini（本机 pytest 9 对旧选项
                        # 会 INTERNALERROR exit 3），与 harness 判定同源。
                        ini = out_dir / "_dispatch_pytest.ini"
                        ini.write_text("[pytest]\n", encoding="utf-8", newline="\n")
                        # ⚠️ 命令口径与 policy_dispatch.run_group_tests 保持一致
                        # （VERIFY_PYTHON + PYTEST_VERIFY_ARGS），避免两处用不同参数导致
                        # 分组时看到的输出形态与 scout 校验时不一致。
                        # 额外加 -c/--rootdir 是实例模式必需的（替换仓库 ini + 钉 rootdir）。
                        probe = subprocess.run(
                            [str(PYTHON), "-m", "pytest", "-q", "--no-header",
                             "-p", "no:cacheprovider",
                             "-c", str(ini), "--rootdir", str(FIXTURE)],
                            cwd=str(FIXTURE), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=600)
                        dispatch_pytest_text = ((probe.stdout or "") + (probe.stderr or ""))
                        dispatch_source = "test_patch_applied"
                        print(f"[dispatch] 施加 test_patch 后基线：exit={probe.returncode}  "
                              f"用于 C 臂分组（{len(dispatch_pytest_text)} 字符）")
                        # 诊断：打印前几行失败行，便于事后核对正则是否命中
                        fail_lines = [ln for ln in dispatch_pytest_text.splitlines()
                                      if ln.strip().startswith("FAILED") or "ERROR" in ln]
                        if fail_lines:
                            print(f"[dispatch] 失败行样例（前 3 条）：")
                            for fl in fail_lines[:3]:
                                print(f"  {fl[:200]}")
                        else:
                            print("[dispatch] ⚠ 未找到任何 FAILED/ERROR 行")
                            # 打印完整输出以便诊断
                            print(f"[dispatch] 完整 pytest 输出（前 500 字符）：")
                            print(dispatch_pytest_text[:500])
                    else:
                        print(f"[dispatch] test_patch 施加失败，退回用 agent 基线输出分组",
                              file=sys.stderr)
                    # 还原工作区到 base_commit 状态（agent 阶段的正确起点）
                    subprocess.run(["git", "-C", str(FIXTURE), "checkout", "--", "."],
                                   capture_output=True, timeout=120)
                    subprocess.run(["git", "-C", str(FIXTURE), "clean", "-fdq"],
                                   capture_output=True, timeout=300)
                else:
                    print("[dispatch] 实例无 test_patch，退回用 agent 基线输出分组")
            except Exception as exc:  # noqa: BLE001
                import traceback
                print(f"[dispatch] 取 test_patch 失败：{type(exc).__name__}: {exc}",
                      file=sys.stderr)
                print(traceback.format_exc()[-500:], file=sys.stderr)

        groups = pd.group_failures(dispatch_pytest_text, mode=args.policy_group_mode)
        observable = bool(groups)
        if args.policy_group_mode == "test_file":
            missing = [g for g in groups if not (g.get("sample_lines") or [])]
            if missing:
                observable = False
        gk_check = {
            "mode": args.policy_group_mode,
            "groups_observed": [g["group"] for g in groups],
            "k_observed": len(groups),
            "observable": observable,
            "rule": ("§1.2：K_expected = |{ n.split('::')[0] for n in FAIL_TO_PASS }|；"
                     "基线里取不到任何 F2P 形状的失败行 => dispatch_grouping_unobservable"),
            "dispatch_pytest_source": dispatch_source,
        }
        print(f"\n=== P1 驱动派发：K={len(groups)} 组 "
              f"{[(g['group'], g['failed_count']) for g in groups]} ===")
        if args.policy_group_mode == "test_file" and not observable:
            # ⚠️ **门槛，不是免责声明（§1.2 最后一行）**：分组不可观测时**不许**回落成
            # 「随便分一组」继续 —— 那 C 臂测的就不是「按失败组分工」这个干预了。
            # 这里选择**不派任何 scout、索引为空**，让 W3 / G-C2 当场把这次运行判成
            # harness_bug（治疗未按设计施加），而不是产出一个看起来正常的数字。
            print("[中止派发] dispatch_grouping_unobservable：基线输出里取不到任何 "
                  "F2P 形状的失败行（`X.py::…` 的 FAILED/SUBFAILED/ERROR 行）。\n"
                  "  ⇒ 本次运行按 §1.2 记 harness_bug（治疗未按设计施加），"
                  "不得改用「随便分 K 组」继续；C 臂若普遍如此 ⇒ 按 §4.4 如实报不可测。",
                  file=sys.stderr, flush=True)
            gk_check["pass"] = False
            gk_check["reasons"] = ["dispatch_grouping_unobservable"]
            groups = []
        try:
            verify_extra: list[str] = []
            if instance_mode:
                # 实例模式下的独立校验必须换掉仓库自带 ini（本机 pytest 9 对旧选项会
                # INTERNALERROR exit 3），并把 rootdir 钉回快照根 —— 与 harness 判定同源。
                # ⚠️ 临时 ini 写在 driver 的 out_dir（**不在 worktree 副本之内**）：
                # 写进副本会让 §5.4 的「快照未被改动」反证变成非空，那是假阳性。
                ini = out_dir / "_policy_verify_pytest.ini"
                ini.write_text("[pytest]\n", encoding="utf-8", newline="\n")
                verify_extra = ["-c", str(ini), "--rootdir", str(FIXTURE)]
            policy_out = await pd.run_policy_dispatch(
                env=env, groups=groups, label=args.label, fixture=FIXTURE,
                out_dir=out_dir, recorder=None, turn_cap=scout_turn_cap,
                pytest_extra_args=(verify_extra or None))
            # P4 验收证据：派发结束时快照必须已被移除（git worktree list 只剩主工作区）。
            wt_check = subprocess.run(
                ["git", "-C", str(FIXTURE), "worktree", "list"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            wt_lines = [ln for ln in (wt_check.stdout or "").splitlines() if ln.strip()]
            policy_out["metrics"]["policy_worktrees_after_dispatch"] = len(wt_lines)
            policy_suffix = pd.policy_task_suffix(
                policy_out["index_text"], policy_out["index_chars"], policy_out["k"])
            print(f"  索引实测 {policy_out['index_chars']} 字符"
                  f"（上限 {pd.DEPENDENCY_OUTPUT_MAX_CHARS}）　"
                  f"派发后 worktree 数={len(wt_lines)}")
            if gk_check is not None:
                # §1.2 的落盘判据在这里补齐（K_expected / index_chars / worktree 残留）。
                # ⚠️ K_expected **只用于自检与归因**，永远不进 agent 可见输入：
                # 它来自实例 JSON 的 FAIL_TO_PASS —— 那是 gold 侧信息（§6.1 同样只用它做归因）。
                gk_check.update({
                    "index_chars": policy_out["index_chars"],
                    "index_max": pd.DEPENDENCY_OUTPUT_MAX_CHARS,
                    "worktrees_after_dispatch": len(wt_lines),
                    "snapshot_head": policy_out["metrics"].get("policy_snapshot_head"),
                    "dispatch_log": str(out_dir / pd.DISPATCH_LOG_NAME),
                })
        except Exception as exc:  # noqa: BLE001 —— 派发炸了也要留下可判定的一轮
            policy_error = scrub(f"{type(exc).__name__}: {exc}")
            print(f"\n[driver 派发异常] {policy_error}", file=sys.stderr)
            if gk_check is not None:
                gk_check["dispatch_error"] = policy_error[:400]

    # ⚠️ P1 C 臂接线改动 #3：实例题面必须在 policy_suffix 定稿**之后**才拼。
    # 上面 `if args.problem_file:` 只保存了 problem_file 路径，没真正读+拼；
    # 这里拿到 suffix 后才调 build_instance_task(..., policy_suffix=...)。
    # A/B 臂 policy_mode=False ⇒ policy_suffix=None，build_instance_task 内部
    # 不追加任何内容 ⇒ 与改动前逐字同形（W3 要求的「A 与 B 题面逐字节相同」）。
    # C 臂 policy_suffix 非空 ⇒ 题面 = INSTANCE_TASK_TEMPLATE + policy_task_suffix。
    if instance_mode and _instance_problem_file is not None:
        instance_task = build_instance_task(_instance_problem_file,
                                            policy_suffix=policy_suffix)
        print(f"实例题面已定稿：{len(instance_task)} 字符"
              f"{'（含 policy_task_suffix ' + str(len(policy_suffix)) + ' 字符）' if policy_suffix else ''}")

    # P1 接线自检的落盘（--arm-report-file；三臂由 harness 各给一份路径）。
    # 放在**题面定稿之后、父 agent 开跑之前**：这样 W3 记的是真正进上下文的那份题面，
    # 而 C 臂的 G-K 结果也已经拿到。写失败不改变判定（判定权在 harness 的独立 pytest），
    # 但自检会因为缺行而判 harness_bug —— 宁可缺证据停批，也不许拿推断顶替。
    if args.arm_report_file:
        try:
            append_arm_report(args.arm_report_file, arm_report_row(
                argv=list(sys.argv[1:]), repo_map_default=repo_map_default,
                arm_source=arm_source, allow_write=not args.no_write,
                enable_subagent=enable_subagent, force_spawn=force_spawn,
                policy_mode=bool(policy_mode), max_turns=max_turns,
                turn_source=turn_source,
                task_text=(instance_task if instance_task is not None
                           else build_task(enable_subagent, force_spawn,
                                           policy_suffix=policy_suffix)),
                findings_dir=(out_dir if policy_mode else None),
                server_script=str(SERVER_SCRIPT), python_exe=str(PYTHON),
                policy=gk_check))
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] --arm-report-file 写入失败：{type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)

    recorder = TraceRecorder({
        "label": args.label,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fixture": ("__instance__" if instance_mode else args.fixture),
        "fixture_root": str(FIXTURE),
        "fixture_head": git_head(),
        "fixture_dirty": fixture_dirty(),
        # 第三阶段 P0 实例入口的溯源三元组：不记这些，「这一轮跑的是哪条实例」事后对不出来
        # （与 fixture 路记 fixture/fixture_head 是同一条纪律）。
        "instance_mode": instance_mode,
        "instance_repo_root": str(FIXTURE) if instance_mode else None,
        "instance_problem_file": (str(Path(args.problem_file).expanduser())
                                  if args.problem_file else None),
        "instance_task_sha256": (hashlib.sha256(instance_task.encode("utf-8")).hexdigest()
                                 if instance_task is not None else None),
        "task": (instance_task if instance_task is not None
                 else build_task(enable_subagent, force_spawn, policy_suffix=policy_suffix)),
        "model": env.get("ANTHROPIC_MODEL"),
        "arm": {"repo_map_default": repo_map_default, "source": arm_source,
                "env_key": REPO_MAP_ENV_KEY,
                # 文本通道实验的另外两个变量。记的是**进程环境里的原始值**
                # （未设置就是 None，生效值等价于 "v1"）—— 不在这里替它补默认值，
                # 否则"没设置"和"设成了 v1"会变得无法区分。
                "server_instructions_raw": os.environ.get(INSTRUCTIONS_ENV_KEY),
                "repo_map_desc_raw": os.environ.get(REPO_MAP_DESC_ENV_KEY),
                # ④ 层这一臂的开关。题面里那段邀请的原文**已经并进上面 task 字段**，
                # 所以这里只记开关，不另抄一份题面（副本会分叉）。
                "subagent": enable_subagent,
                "subagent_forced": force_spawn,
                # P1：驱动派发臂。D 臂 = delegate=policy（A/B/C 恒为 None/False，
                # 旧产物的 arm 形状不变 —— 新键只在 policy 时有值）。
                "delegate": args.delegate,
                "policy": bool(policy_mode)},
        # ⚠️ 沙箱留痕与真正装配 options 的是**同一个函数**。两处各算一遍的话，
        #    记下来的就是"我以为放了什么"而不是"真的放了什么"（陷阱 14）。
        "sandbox": sandbox_spec(not args.no_write, enable_subagent,
                                policy_mode=bool(policy_suffix)),
        "baseline_exit_code": base_code,
        "baseline_failures": base_failed,
        "baseline_passed": base_passed,
        "expect_failed": expect_failed,
        "max_turns": max_turns,
    })
    durable_finals: list[dict] = []
    if policy_out is not None:
        import agent.policy_dispatch as pd
        # R4：这里**从盘上读回** durable 终态记录（policy_dispatch.jsonl），
        # 而不是把内存里的 records 抄一遍 —— 「状态记录」的意义就是它不在内存里。
        # 读不到就如实标记（policy_durable_finals_read=0 + 一条说明性 note），
        # 绝不用内存副本假装是 durable 记录。
        durable_finals = pd.read_group_finals(out_dir, args.label)
        recorder._run["policy_group_finals"] = durable_finals
        recorder._run["policy_durable_finals_read"] = len(durable_finals)
        for r in durable_finals:
            recorder.note("policy_dispatch", k=r["k"], group=r["group"],
                          group_failed=r.get("group_failed"),
                          event=r.get("event"), group_status=r.get("group_status"),
                          attempt_status=r.get("attempt_status"),
                          retried=bool(r.get("retried")),
                          retry_outcome=r.get("retry_outcome"),
                          truncated_retry_succeeded=r.get("truncated_retry_succeeded"),
                          verified_ids=r.get("verified_ids") or [],
                          scout_file=r.get("scout_file"),
                          snapshot_head=r.get("snapshot_head"),
                          scout_accounting=r.get("scout_accounting"),
                          source="policy_dispatch.jsonl（durable 读回）",
                          why=r.get("why") or "")
        if not durable_finals:
            recorder.note("policy_dispatch", source="内存（durable 记录缺失）",
                          read_error=(f"{out_dir.name}/policy_dispatch.jsonl 里读不到 "
                                      f"label={args.label} 的终态记录；"
                                      f"下面的组信息来自内存，不可当状态记录用"))
        recorder._run["policy_groups"] = [
            {"group": r["group"], "failed": r["group_failed"]} for r in policy_out["records"]]
        recorder._run["policy_snapshot_head"] = policy_out["metrics"]["policy_snapshot_head"]
        recorder._run["scout_turn_cap"] = scout_turn_cap

    # 3) 跑 agent
    print("\n=== agent 开始 ===")
    t0 = time.time()
    agent_error: str | None = None
    result: Any = None
    events: list[dict] = []
    try:
        result, events = await run_agent(env, allow_write=not args.no_write,
                                     repo_map_default=repo_map_default,
                                     enable_subagent=enable_subagent,
                                     force_spawn=force_spawn,
                                     recorder=recorder,
                                     policy_suffix=policy_suffix,
                                     findings_dir=(out_dir if policy_mode else None),
                                     instance_task=instance_task,
                                     max_turns=max_turns)
    except Exception as exc:  # noqa: BLE001 —— 这里就是要兜住一切
        # 端点写错时 CLI 报的是「There's an issue with the selected model…」——
        # **说的是模型、实际是 URL 错了**（陷阱 3）。第一版让它崩成 traceback，
        # 结果是「反向对照没跑完就退出」，判定为空。改成记成一次**可判定的失败**，
        # 继续走独立验证 —— 反向对照要的是结论，不是堆栈。
        agent_error = scrub(f"{type(exc).__name__}: {exc}")
        recorder.note("agent_exception", error=agent_error)
        print(f"\n[agent 异常] {agent_error}", file=sys.stderr)
    elapsed = time.time() - t0

    # 4) 独立验证 —— 只认这个
    print("\n=== 独立验证（不经过 agent）===")
    now_code, now_out = run_pytest()
    now_failed, now_passed = pytest_counts(now_out)
    files = changed_files()

    # 实例模式：没有 FIXTURE_RULES，用「测试侧文件」这一条通用判据（与预注册 §3.1 同源）。
    # 与 fixture 路的 scope/tests 双判据不同：真实仓库没有「允许改哪些源码文件」的白名单，
    # 唯一的硬约束是「不许改测试」（§5.3 由 harness 强制剥离，这里只是如实记录）。
    rules = None if instance_mode else FIXTURE_RULES[args.fixture]
    if instance_mode:
        tests_touched = sorted(f for f in files if _is_test_path(f))
        outside: list[str] = []          # 实例模式不设源码白名单
    else:
        tests_touched = sorted(f for f in files if _matches(f, rules["tests"]))
        outside = sorted(
            f for f in files
            if not _matches(f, rules["tests"]) and not _matches(f, rules["scope"])
        )

    success = (now_code == 0) and not tests_touched and not outside

    # ⚠️ 判定所需的三个量（退出码、改动清单、pytest 原文）都已经拿到，
    #    立刻把靶场复位 —— 否则 agent 的改动会留在工作区里，
    #    被下一次 `git add -A` 当成我的改动提交（已踩过一次，见 reset_fixture 的注释）。
    #    注意 `now_out` 是内存里的字符串，复位不影响后续落盘。
    # ⚠️ **实例模式不复位**：实例工作区不属于本仓库，agent 的改动正是 harness 要采集的
    #    「候选补丁」（预注册 §5.1：由 harness 用 git diff 从工作区采集）。在这里复位
    #    等于把被测产物删掉。fixture 路的复位是为了保护本仓库不被 `git add -A` 误提交，
    #    而实例工作区在 `eval-runs/` 下，不存在这个风险。
    if instance_mode:
        print(f"  （实例模式：不复位工作区 —— agent 的改动由 harness 采集为候选补丁）")
    else:
        print(f"  复位靶场：{reset_fixture()}")
    print(f"  pytest 退出码：{base_code} -> {now_code}")
    print(f"  pytest 失败数：{base_failed} -> {now_failed}"
          f"（通过 {base_passed} -> {now_passed}）")
    print(f"  改动文件：{files or '(无)'}")
    print(f"  测试文件被改：{tests_touched or '否'}")
    print(f"  越界改动：{outside or '无'}")
    print(f"  ==> 判定：{'成功' if success else '未成功'}")

    # 5) 指标 + 留痕
    metrics = summarize(events)
    # ④ 层的账**单独一格**，父流指标一个都不动（见 summarize_subagent 的说明）。
    # 关掉时也显式写 subagent_enabled=False ——「缺这个字段」和「这一臂压根
    # 没有 ④ 层」在产物里必须能区分开，否则下游会把旧产物当成臂 A。
    if enable_subagent:
        metrics.update(summarize_subagent(events))
    else:
        metrics["subagent_enabled"] = False
    # 「强制的通道对照」与「真的采用了」必须分开记：前者是**我自己要求**它派的，
    # 把它算进采用率就等于自己给自己刷了一个 adoption。
    metrics["subagent_forced"] = force_spawn
    # P1 驱动派发臂：**模型手里没有 spawn 通道**，subagent_* 那组键不适用。
    # 但键必须在场（值 0/False）——跑批器 METRIC_KEYS 跨臂取同一组键，
    # D 臂缺键会让汇总把「没派」读成「没数据」。
    # ⚠️ B4（修复轮 #3）：D 臂的派发主口径 = `policy_scout_sessions`（= Σ每组 attempts，
    # driver 侧续派也算）+ `policy_k_groups`；治疗门槛是 `policy_c1_gate`
    # （每组至少有一次 scout 结论）。`policy_dispatch_calls` 已退役 —— 它只是
    # 「每组最后一次尝试的 Task 数」，含义含混，不许再当门槛（见 agent/policy_dispatch.py）。
    if policy_mode:
        metrics.update({
            "subagent_spawn_calls": 0, "subagent_adopted": False,
            "subagent_messages": 0, "subagent_tool_calls": 0,
            "subagent_text_chars": 0, "subagent_result_chars": 0,
            "task_messages_per_type": {}, "subagent_tokens_estimate": None,
            "subagent_token_samples": 0,
        })
        metrics.update((policy_out or {}).get("metrics") or {
            "policy_enabled": True, "policy_error": (policy_error or "")[:400]})
        if policy_error:
            metrics["policy_error"] = policy_error[:400]
        # R4：durable 终态记录**从盘上读回**的条数。0 = 没读到（不许把它读成"没有组"）。
        metrics["policy_durable_finals_read"] = len(durable_finals)
    metrics.update({
        "label": args.label,
        "allow_write": not args.no_write,
        # A/B 实验条件落盘 —— 下游 context_report.py 靠它把样本归臂，
        # 而不是靠文件名猜。
        "repo_map_default": repo_map_default,
        "repo_map_default_source": arm_source,
        "server_instructions_raw": os.environ.get(INSTRUCTIONS_ENV_KEY),
        "repo_map_desc_raw": os.environ.get(REPO_MAP_DESC_ENV_KEY),
        "agent_error": agent_error,
        "elapsed_s": round(elapsed, 1),
        "baseline_exit_code": base_code,
        "final_exit_code": now_code,
        # 失败数（不只是退出码）。预注册里"基线必须恰好 32 个失败"这条不变量
        # 要有**数字**才算验过 —— 只记退出码等于没记（见 pytest_counts 的说明）。
        "baseline_failures": base_failed,
        "final_failures": now_failed,
        "final_passed": now_passed,
        "changed_files": files,
        "test_files_touched": tests_touched,
        "edits_outside_scope": outside,
        "success": success,
    })

    print("\n=== 指标 ===")
    for k in ("tool_calls_total", "tool_calls_per_tool", "run_tests_exit_codes",
              "run_tests_count", "apply_patch_count", "repo_map_count",
              "used_repo_map", "elapsed_s"):
        print(f"  {k} = {metrics[k]}")
    # ④ 层单独打印，且**无论开关都打** ——「这一臂有没有 ④ 层」和
    # 「有但一次都没派」是两件完全不同的事，日志里必须一眼分得开。
    print(f"  subagent_enabled = {metrics['subagent_enabled']}")
    if enable_subagent:
        for k in ("subagent_spawn_calls", "subagent_adopted", "subagent_tool_calls",
                  "subagent_messages", "subagent_text_chars", "subagent_result_chars",
                  "task_messages_per_type", "subagent_tokens_estimate"):
            print(f"  {k} = {metrics[k]}")
    if policy_mode:
        # D 臂的账单独打：派发由 driver 做，父层 spawn=0 是**设计**不是缺陷。
        for k in sorted(k for k in metrics if k.startswith("policy_")):
            v = metrics[k]
            if isinstance(v, (int, float, str, bool)) or v is None:
                print(f"  {k} = {v}")

    if result is not None:
        print(f"\n=== 结果 ===")
        print(f"  subtype={result.subtype}  is_error={result.is_error}  turns={result.num_turns}")
        final_text = (result.result or "")[:1500]
        print(f"  agent 自述：{scrub(final_text)}")
        metrics["agent_claimed_summary"] = scrub(final_text)

    usage_extra = ({"delegate": args.delegate,
                    **{k: v for k, v in metrics.items() if k.startswith("policy_")}}
                   if policy_mode else {})

    # ⚠ D6（t8 修）：trace 必须在 `log_usage` **之前**收口。
    # 原顺序是先 log_usage 再 recorder.finish，于是 agent 异常（result=None）时
    # log_usage 拿不到 trace，用量字段全 null；而 events 为空又让 tool_calls_total=0，
    # 与 trace 里真实发生的 77 次调用矛盾。后果是 **P1 的成本外推系统性漏掉失败轮**
    # （而失败轮往往最贵）。⇒ 先 finish 拿到 trace，再把它交给 log_usage 回填。
    trace = recorder.finish({
        "outcome": "success" if success else "failed",
        "agent_stop": getattr(result, "subtype", None),
        "baseline_exit_code": base_code,
        "final_exit_code": now_code,
        "baseline_failures": base_failed,
        "final_failures": now_failed,
        "changed_files": files,
        "test_files_touched": tests_touched,
        "edits_outside_scope": outside,
        "agent_error": agent_error,
        "result": result_summary(result),
        # P1：driver 侧派发记录（父层 TaskNotification 之外的那份账）。
        **({"policy_records": policy_out["records"],
            "policy_error": policy_error} if policy_mode else {}),
    })

    row = log_usage(result, script=Path(__file__).name, key_source=key_source,
                    trace=trace,
                    extra={"label": args.label,
                           "repo_map_default": repo_map_default,
                           "used_repo_map": metrics["used_repo_map"],
                           # D 臂的账要能跨运行汇总（policy_* 全量进 usage.jsonl；
                           # A/B/C 臂没有这些键 —— 旧行的形状不变）。
                           **usage_extra,
                           **{k: metrics[k] for k in
                           ("success", "run_tests_count", "apply_patch_count",
                            "final_exit_code", "test_files_touched")}})

    # 用量也记进 JSON —— 否则 context_report.py 要跨两个文件才能拼出全貌，
    # 而"跨文件拼"正是最容易拼错的地方。
    for src_key, dst_key in (("input", "input_tokens"), ("output", "output_tokens"),
                             ("total_cost_usd", "cost_usd")):
        metrics[dst_key] = row.get(src_key)
    # P0：三个 input 口径分开落盘。**口径说明（写死在这里，别靠猜）**：
    #   input_tokens == input_uncached == model_usage 各模型 inputTokens 之和，
    #     —— 第三方端点把它定义为**未缓存输入**；
    #   cache_read / cache_creation == model_usage 的 cacheRead/CreationInputTokens 之和；
    #   真实 prompt 规模 = 三者之和。C-1 实测 65,027 vs 合计 1,250,691（差 19×），
    #   所以「父层上下文 ×1.68」这类结论从此必须写明是**未缓存口径**。
    # ResultMessage.usage 是第三个口径（只含主会话回合，不含 title/subagent 旁路），
    # 原样留在 trace.termination.result.usage 与 usage.jsonl 的 result_usage 里对账。
    for src_key, dst_key in (("input_uncached", "input_uncached"),
                             ("cache_read", "cache_read"),
                             ("cache_creation", "cache_creation")):
        metrics[dst_key] = row.get(src_key)

    # 6) trace 收尾 —— 判定口径分两栏写，**故意不合并**：
    #      outcome    = 独立 pytest 的结论，唯一判据（DESIGN.md 第 6 节）
    #      agent_stop = CLI 自己的说法（ResultMessage.subtype）
    #    两者不一致本身就是信息：agent 说「已完成」而测试仍然失败 ——
    #    旧产物里这只剩一个布尔，看不出它当时到底是怎么说的。
    #    ⚠ D6（t8）：这次 finish **已在上面调用过**（为的是让 log_usage 能拿到 trace
    #    做异常路径回填）。trace 是同一个对象，这里不再重复收口。

    # 7) 落盘（运行时产物；里程碑时冻结成带名字的快照再提交，见 .gitignore 约定）
    prefix.with_suffix(".json").write_text(
        json.dumps({"metrics": metrics, "events": events, "trace": trace},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with prefix.with_suffix(".log").open("w", encoding="utf-8") as fh:
        fh.write(f"# {datetime.now().isoformat(timespec='seconds')}  label={args.label}\n")
        fh.write(f"success={success}  baseline={base_code}  final={now_code}\n")
        # A/B 条件写在 .log 头部：这是"本轮是哪一臂"最容易被人工核对到的位置。
        fh.write(f"repo_map_default={repo_map_default}  (来源：{arm_source})\n")
        fh.write(f"used_repo_map={metrics['used_repo_map']}  "
                 f"repo_map_count={metrics['repo_map_count']}\n")
        fh.write(f"changed={files}\n\n")
        fh.write(base_out + "\n\n" + "─" * 60 + "\n\n" + now_out + "\n")

    print(f"\n  证据已落盘：{prefix.name}.json / .log")
    print(f"  trace：{len(trace['steps'])} 步  outcome={trace['termination']['outcome']}  "
          f"agent_stop={trace['termination']['agent_stop']}")
    print(f"  用量行：run={row['run']}  model={row['model']}  "
          f"input_uncached={row['input_uncached']}  cache_read={row['cache_read']}  "
          f"cache_creation={row['cache_creation']}  "
          f"output={row['output']}  turns={row['num_turns']}")

    # 实例模式的测试侧改动由 eval harness 后续剥离并独立判定；不能把
    # tests_touched 这一过程指标当成 agent 进程级 infra/exit 失败，否则 C 臂
    # 的正常 policy 运行会被 scheduler 误标 infra_error。fixture 模式仍保留
    # 原有严格退出码语义，便于本地靶场自检。
    return 0 if (success or (instance_mode and agent_error is None and result is not None)) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
