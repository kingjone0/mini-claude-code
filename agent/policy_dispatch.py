"""agent/policy_dispatch.py —— P1–P5：由 **driver** 决定派发的侦察流水线。

为什么存在（④ 层 Round 2 的两条实测结论，别再赌模型自愿）：
  ① 题面邀请派发 0/4 —— TASK_BASE 已写死 DIY 流程，邀请段在和协议竞争；
  ② 强制派发时子代理报告 6.8–8.1 万字符全文进父层上下文 ——
     派发买到的是「更贵的自己做」。

所以本模块把「派不派」从模型手里拿走（确定性入口），并把报告做小（有界索引）。
五个机制与预注册 AB-SUBAGENT-V2.md §1.1 一一对应：

  P1 驱动派发   run_policy_dispatch()：driver 解析基线 pytest 输出、按测试类聚成
                K 组，每组用 SDK 的 Task 工具起一个 scout —— 父 agent 的第一个
                模型回合开始之前，结论就已经在上下文里。
  P2 有界报告   完整 findings 落 out/scout-<label>-<k>.json；进父层的只有一份
                ≤DEPENDENCY_OUTPUT_MAX_CHARS(2000) 字符的索引 + read_findings(id)
                按需取单条（成本不许从「通知」搬到「读取」）。
  P3 截断即状态 每次派发一条 durable JSONL 记录（dispatched/completed/truncated）；
                summary 含 "turn limit"（subagent_review.TRUNC_MARK 同源判据）→
                自动续派一次 → 仍失败该组标 undiagnosed，半份报告不当完整结论。
  P4 快照隔离   git worktree add --detach <tmp> HEAD；scout 的 MCP env
                AGENT_REPO_ROOT 指向快照；父 agent 仍在原工作区；
                结束（含异常路径）worktree remove，不留残留。
  P5 独立校验   每条 finding 带 predictedFailureCount；harness 在**另一个进程**
                （独立 subprocess，不是 scout 自己）于靶场副本应用 minimalFix、
                跑 pytest -k <组>，预测==实际 ⇒ verified，否则 refuted。
                只有 verified 的 finding 进父层索引。

R2/R4/R5/R7（第二轮复审 must-fix 里落在本模块的部分，只动**作用域/记账/状态记录**）：
  R2  finding id 变成**整个 findings 目录内唯一**：`<label>-g<k>-f<n>`（归一化时就带
      运行 label）。修之前 id 形如 `g<k>-f<n>`、只在**单份产物内**唯一，而父层
      read_findings 对整个 out/ glob ⇒ 多轮产物并存时全面重名（第一轮跑批实测
      12 次调用 0 次成功）。id 仍可读、schema 字段不变，产物落点也不变。
  R4  每组一条**终态记录**：`event` = completed | undiagnosed（组的状态，不是某一次
      尝试的状态），带 attempts 链、retried、retry_outcome。「截断 → 自动续派 → 成功」
      从记录里直接读得出来；父层再从 policy_dispatch.jsonl **读回**这些记录，
      不靠内存变量兜（见 coding_agent.py 的 policy_durable_finals 段）。
  R5  每个 scout 一条 scout 侧用量记录（独立会话能拿到的都记，拿不到的写明为什么）。
  R7  verify 记录里那个失败数不再叫 `observed_before` —— 它不是观察值（见
      verify_finding 的字段说明）。

B1/B2/B4（修复轮 #3，第二轮复审的 blocker 与 must-fix；只动作用域/记账/状态记录/会话装配）：
  B1  结论来源锁定：**只有 scout 自己的产出**能当 scout 结论 —— 通知 summary、
      scout 自己的 output_file、以及 scout 那侧（`parent_tool_use_id` == 本次 harness
      自己那把 Task tool_use）的流式文本。包装会话（driver 主会话）的文本一律不采。
      这条修的是一个**已实测发生**的泄漏：R5 把消息循环从「收到通知就 return」改成
      「继续消费到 ResultMessage」之后，包装模型有机会在通知之后自己接着干 ——
      v2-fix-trunc 里 k3 那条 verified finding 就是包装模型自己调了 11 次
      mcp__code-tools__* 之后写在自己 assistant 文本里的（SDK transcript 里
      isSidechain=false 那一行，内容与 scout 产物逐字一致），旧代码把它当成了 scout 结论。
      同时（冗余的第二道锁）**包装会话不再拥有代码工具**：会话级 allowed_tools 只留
      ["Task"]，scout 的 MCP 权限改由它自己的 AgentDefinition.permissionMode 兜。
      解不到 scout 结论 ⇒ 该组按 undiagnosed，绝不拿包装模型的产出顶替。
  B2  `policy_scout_usage.jsonl` 的键名与文档一致（output_tokens / cost_usd / subtype /
      task_usage），**同时**保留 usage_log 原名（output / total_cost_usd / …）—— 两套
      名字在同一行里，映射表见 SCOUT_USAGE_LIMITS，`按文档名读`不再读到 None。
  B4  治疗门槛落盘：**逐组**记 `c1 = {dispatched_once, scout_conclusion_obtained,
      group_status, scout_conclusion_source}`；主口径 = `policy_scout_sessions`
      （= Σ每组 attempts，driver 侧续派也算）+ `policy_k_groups`。旧的
      `policy_dispatch_calls` 含义含混（只是「每组最后一次尝试的 Task 数」，
      v2-fix-trunc 里 =4，而 K=3、会话数=6、全部 attempt 的 Task 数=9）⇒ 退役，
      改名为 policy_task_tool_use_count（全部 attempt 的 Task tool_use 总数，
      **含包装模型自己发起的**）+ policy_task_tool_use_count_last_attempt，
      两者都**不得**当门槛用。

纪律：本模块不改动 A/B 判定 —— 成功与否仍只由独立 pytest 退出码判定；
默认参数下（不加 --delegate=policy）coding_agent.py 与本模块零交集。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "servers" / "experiments"))

from claude_agent_sdk import (  # noqa: E402
    AgentDefinition,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    query,
)

# ── 常量（口径写死在这里，别处一律引用）────────────────────────────────────
# P2 索引上限。借参考实现 @nanmicoder/dsh-agent-teams 的同名常量。
DEPENDENCY_OUTPUT_MAX_CHARS = 2000
# P3 截断判据。**与 servers/experiments/subagent_review.py 的 TRUNC_MARK 同源**：
# TaskNotificationMessage.summary 首句 "NOTE: this agent stopped at its N-turn
# limit before finishing. The text below is PARTIAL"。改措辞要两处一起改。
TRUNC_MARK = "turn limit"
# P5 校验解释器：任务书钉死的 Anaconda 环境（唯一装了 pytest 的解释器）。
# 与 servers/experiments/mutate.py 硬编码同一路径的做法一致。
VERIFY_PYTHON = r"D:\Anaconda3\envs\aicoding\python.exe"
# 校验命令与 setup_hard_target.py / run_pytest 同口径（不含 --tb：只要计数行）。
PYTEST_VERIFY_ARGS = ["-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
DISPATCH_LOG_NAME = "policy_dispatch.jsonl"
# R5：scout 侧用量单独一个 JSONL（不混进 usage.jsonl —— 那个文件的口径是
# 「一个 run 一行」，混进 scout 会话会让所有历史汇总脚本把 scout 当一次 run）。
SCOUT_USAGE_LOG_NAME = "policy_scout_usage.jsonl"
SERVER_NAME = "code-tools"   # 与 coding_agent.SERVER_NAME 同值；不 import 避免环依赖
SCOUT_RESULT_TIMEOUT_S = 900      # 单个 scout 会话兜底超时（15 min）
# R5：收到 TaskNotification **之后**最多再等这么久去拿 ResultMessage。
#
# 为什么需要它：旧实现在 TaskNotification 到手时立刻 `return`，等于提前关掉 CLI
# 会话 —— 那个会话的 ResultMessage（usage / model_usage / total_cost_usd 的唯一出处）
# 永远不会到达，于是 scout 侧成本无从记起（R5 的根因之一）。改成继续消费到
# ResultMessage，同时给尾段一个**独立**上限：通知之后会话若卡住，不会把整轮拖满
# SCOUT_RESULT_TIMEOUT_S（900 s）。
TAIL_AFTER_NOTIFY_S = 180
FINDING_REQUIRED_FIELDS = ("id", "file", "line", "symptom", "rootCause",
                           "minimalFix", "evidence", "predictedFailureCount")

# ── B1：结论来源与会话装配（**写死在这里，装配与判据都引用同一份**）──────────
# scout 结论的合法来源。只有这三个 —— 都是 scout 自己写的：
#   summary            TaskNotificationMessage.summary（scout 的收尾文本）
#   output_file        scout 自己的任务输出文件（CLI 写的，scout 署名）
#   stream_text_scout  scout 那一侧的流式文本（parent_tool_use_id == 本次 harness 的
#                      Task tool_use id）—— 需要 forward_subagent_text=True 才会转发
FINDINGS_SOURCES = ("summary", "output_file", "stream_text_scout")
# 包装会话（driver 主会话）**只允许 Task**：它自己不许拿代码工具。
# 这是 B1 的第二道锁 —— 第一道是上面的来源锁定（判据层），这一道是装配层。
# 包装会话（driver 主会话）**只允许 Task**：它自己不许拿代码工具。
# ⚠️ 这个常量只用于**装配层文档与检查**（见 _scout_options 的说明：会话级 allowed_tools
# 砍成只 ["Task"] 会把 scout 一起掐死，实测过），真正拦包装模型的是 PreToolUse 钩子
# `guard_code_tools_for_subagent`（按 agent_id 判来源）。
WRAPPER_ALLOWED_TOOLS = ["Task"]
# driver 会话的权限模式。写死成常量：它是**实测稳定**的那一档，别为了别的机制改它。
PERMISSION_MODE_DONT_ASK = "dontAsk"# scout 自己的权限模式。会话级 allowed_tools 收紧到只 ["Task"] 之后，scout 的
# mcp__code-tools__* 调用要靠它自己的权限模式兜住 —— 否则会被 dontAsk 拦成
# "Permission to use … has been denied"（t3 探针实测过：只列 ["Task"] 时 scout 空转）。
# ⚠️ scout 仍然**只拿到** 5 个只读 MCP 工具（AgentDefinition.tools）+ 一张
# 内置工具黑名单（disallowedTools）—— 「只读」这条不变量由工具清单保证，不由权限模式保证。
SCOUT_PERMISSION_MODE = "bypassPermissions"
SCOUT_DISALLOWED_TOOLS = ["Bash", "Read", "Write", "Edit", "Grep", "Glob",
                          "WebFetch", "WebSearch", "NotebookEdit", "TodoWrite"]
SCOUT_TOOL_NAMES = ("list_files", "search_code", "read_file", "repo_map", "run_tests")
# B4：治疗门槛的主口径（写死成常量，落盘到 metrics 的 policy_gate_metric，
# 免得 t13/t16 再问「C1 到底用哪个数」）。
PRIMARY_GATE_METRIC = "policy_scout_sessions+policy_k_groups"
GATE_CRITERION = "每组至少有一次 scout 结论（scout_conclusion_obtained）"
# 退役键：含义含混（「每组最后一次尝试的 Task 数」），不再写进新产物。
RETIRED_METRIC_KEYS = ("policy_dispatch_calls",)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append_jsonl(path: Path, obj: dict) -> None:
    """durable 追加一行 JSON（UTF-8；崩溃也不丢已写记录 —— P3 的「durable」在这）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ── P1：基线失败 → 分组 ────────────────────────────────────────────────────
# 两种分组**模式**（口径不同，但都只读同一段 pytest 输出）：
#
#   mode="test_file"（P1 三臂 C 用；预注册 §1.2 G-K 写死）
#     组键 = **测试文件路径**。判据与 §1.2 逐字对应：
#         K_expected(i) = |{ n.split('::')[0] for n in instance(i).FAIL_TO_PASS }|
#     pilot-12 的 F2P node id 形状是 `tests/test_options.py::test_x[opts1-True]` /
#     `test/test_output.py::test_floatfmt_decimal` —— **不含** `<Name>Tests::` 类名。
#     旧实现（class_name 模式）在这批上整批命中不到 ⇒ 全部回落成单组 OTHER、K 恒为 1，
#     C 臂就变成「一个 scout 打全仓」的另一套干预（预注册 §1.1 缺口②）。
#     ⚠️ 这是**让既有开关在实例模式下可达**的必要接线，不是新增第二个变量：
#     W1 的 argv diff 仍然只允许多出 `--delegate policy` 两个 token。
#
#   mode="class_name"（第二阶段 D 臂的历史口径；默认值，**逐字不变**）
#     组键 = `<Name>Tests` 类名；认不出时回落单组 OTHER。
#     more-itertools-hard 批次（K=3）的全部产物都是这个口径 ⇒ 默认值不能动，
#     否则历史对照的分组依据会被事后改掉。
#
# 排序（两种模式一致）：失败数降序、组键升序 ⇒ 确定性，同一基线两次必得同一分组。
FAILED_LINE_PAT = re.compile(r"^(?:FAILED|SUBFAILED\b|ERROR)\s")
NODE_ID_IN_LINE = re.compile(r"(\S+\.py(?:::[^\s]+)*)")


def _failure_lines(text: str) -> list[str]:
    """从 pytest（-q --tb=line 形态）输出里挑出「一条失败/错误用例」的行。

    两种形态都要吃：
      · `FAILED tests/test_more.py::TestA::test_b - assert ...`
      · `SUBFAILED(slice_args=(-4, 3, 2)) tests/test_more.py::TestA::test_b`
      · `ERROR tests/test_basic.py - fixture error` / `ERROR testing/test_x.py::t - …`
    判据保持与旧实现同样的宽度（含 `ERROR ` 出现在行任意位置的情形），
    只是把它拆成一个可复用的函数，供两种模式共用。
    """
    out = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if "::" not in s and "ERROR" not in s:
            continue
        if FAILED_LINE_PAT.match(s) or "ERROR " in s:
            out.append(s)
    return out


def group_failures_by_test_file(text: str) -> tuple[list[dict], bool]:
    """§1.2 的分组：按**测试文件路径**聚合基线失败行。

    返回 `(groups, observable)`：
      observable=False ⇒ 输出里取不到任何 F2P 形状的失败行（没有 `X.py::…` 的
      FAILED/SUBFAILED/ERROR 行）⇒ groups=[]。调用方必须据此标
      `dispatch_grouping_unobservable`（§1.2 最后一行的门槛），
      **不许**回落成「随便分一组」继续 —— 那会让 C 臂测的不是同一个干预。
    """
    counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    for s in _failure_lines(text):
        m = NODE_ID_IN_LINE.search(s)
        if not m:
            continue
        key = m.group(1).split("::")[0]
        if not key.endswith(".py"):
            # 例如 `ERROR tests/test_basic.py - …` 已经带 .py；
            # 不带 .py 的形状（目录、`-` 之类）不足以当组键，跳过。
            continue
        counts[key] = counts.get(key, 0) + 1
        samples.setdefault(key, [])
        if len(samples[key]) < 3:
            samples[key].append(s[:240])
    groups = [{"group": k, "failed_count": n, "sample_lines": samples.get(k, []),
               "group_key_kind": "test_file"}
              for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return groups, bool(groups)


def group_failures(text: str, *, mode: str = "class_name") -> list[dict]:
    """从 pytest（-q --tb=line 形态）输出里把失败聚成组。**mode 见上方注释。**

    实测锚点（more-itertools-hard，32 failed，mode="class_name"）：
    FAILED/SUBFAILED/ERROR 且含 '::' 的行共 32 条，正则 r'(\\w+Tests)\\b' 命中
    {IsliceExtendedTests:27, SlidingWindowTests:3, DiscreteFourterTransformTests:2}
    —— 与预注册 K=3 的 ground truth 一致。（原样保留旧口径的行为；类名拼写以代码为准：
    DiscreteFourierTransformTests。）
    认不出任何 `<Name>Tests::` 形状时回落为单组 OTHER（K=1；D 臂门槛 C4 会拦）。

    ⚠️ 默认值是 `"class_name"`：**第二阶段 D 臂的全部产物走这条路径，逐字不变**。
    P1 三臂的 C 臂由 coding_agent.py 显式传 `mode="test_file"`（§1.2）。
    """
    if mode == "test_file":
        groups, _observable = group_failures_by_test_file(text)
        return groups
    if mode != "class_name":
        raise ValueError(f"group_failures: 未知 mode {mode!r}"
                         "（只支持 'class_name' / 'test_file'）")
    fail_lines = [ln for ln in text.splitlines()
                  if ("::" in ln and (ln.startswith("FAILED")
                                      or ln.startswith("SUBFAILED")
                                      or "ERROR " in ln))]
    counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    for ln in fail_lines:
        names = re.findall(r"(\w+Tests)\b", ln)
        keys = names or ["OTHER"]
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
            s = samples.setdefault(k, [])
            if len(s) < 3:
                s.append(ln.strip()[:240])
    groups = [{"group": k, "failed_count": n, "sample_lines": samples.get(k, [])}
              for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return groups


# ── P4：git worktree 快照 ──────────────────────────────────────────────────
class SnapshotWorktree:
    """`git worktree add --detach <tmp> HEAD` 的一次性快照（P4 的根治对象）。

    C-2 教训：父代理在子代理侦察**期间**打了补丁，子代理再读发现缺陷「不见了」，
    于是去找不存在的备份产物，24 回合烧光。快照让 scout 的世界与父的工作区解耦。
    用完必须 remove（调用方负责 finally）；remove 走 `--force`——快照上不允许
    有任何改动（那是「scout 只读」的反证检查位），但清理不该被边角情况卡死。
    """

    def __init__(self, repo_root: Path, label: str):
        self.repo_root = Path(repo_root)
        self.label = label
        self.path: Path | None = None

    def _git(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(self.repo_root),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    def create(self) -> Path:
        base = Path(tempfile.gettempdir()) / "mcc-snapshots"
        base.mkdir(exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f"{self.label}-", dir=str(base)))
        r = self._git(["worktree", "add", "--detach", str(tmp), "HEAD"])
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"git worktree add 失败：{(r.stdout + r.stderr).strip()[:400]}")
        self.path = tmp
        return tmp

    def head(self) -> str:
        if self.path is None:
            raise RuntimeError("快照还没建")
        r = self._git(["-C", str(self.path), "rev-parse", "HEAD"])
        return (r.stdout or "").strip()

    def status_porcelain(self) -> str:
        if self.path is None:
            return ""
        r = subprocess.run(["git", "-C", str(self.path), "status", "--porcelain"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return (r.stdout or "").strip()

    def remove(self) -> str:
        if self.path is None:
            return "(未创建，无需清理)"
        r = self._git(["worktree", "remove", "--force", str(self.path)])
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            # 兜底：目录直接删 + prune 掉悬空登记。清不掉是事实，如实上报。
            shutil.rmtree(self.path, ignore_errors=True)
            self._git(["worktree", "prune"])
        return out or f"(exit={r.returncode})"


# ── P5：findings 的独立进程校验 ────────────────────────────────────────────
def apply_minimal_fix(snapshot: Path, finding: dict) -> tuple[bool, str]:
    """在快照副本上应用一条 finding 的 minimalFix（old 唯一匹配才动手）。

    与 code_tools_server.apply_patch 同一条「唯一匹配」纪律：歧义就拒绝，
    宁可不验证也不能验错地方。返回 (是否应用, 说明)。
    ⚠️ 只在**副本**上动 —— 快照本体（scout 读的那份）由调用方先复制出来。
    """
    mf = finding.get("minimalFix") or {}
    rel, old, new = str(mf.get("file") or ""), mf.get("old"), mf.get("new")
    if not rel or old is None or new is None:
        return False, "minimalFix 缺 file/old/new"
    target = (snapshot / rel).resolve()
    root = snapshot.resolve()
    if target != root and not target.is_relative_to(root):
        return False, f"minimalFix.file 越出副本根：{rel!r}"
    if not target.is_file():
        return False, f"文件不存在：{rel}"
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"读取失败：{exc}"
    n = text.count(old)
    if n == 0:
        return False, f"old 在 {rel} 中不存在（scout 给的行号/原文对不上快照）"
    if n > 1:
        return False, f"old 在 {rel} 中出现 {n} 次，无法确定改哪一处"
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True, f"已应用（唯一匹配，替换 1 处）：{rel}"


def run_group_tests(cwd: Path, group_key: str, *, node_ids: list[str] | None = None,
                    extra_args: list[str] | None = None) -> tuple[int, int, int, str]:
    """独立进程跑该组的测试，返回 (exit, failed, passed, tail)。

    解释器与命令口径 = 任务书钉死的 VERIFY_PYTHON + PYTEST_VERIFY_ARGS。
    计数复用 coding_agent.pytest_counts（同一个正则实现，两处不抄两份）。

    ⚠️ P1 §1.2 的两种组键需要两种选测方式：
      · `node_ids` 非空 ⇒ **直接把这些工作区相对 node id 当参数传给 pytest**。
        C 臂在实例模式下按「测试文件路径」分组，而一个文件里既有缺陷测试、也有本来就绿的
        测试；用 `-k <file>.py` 会把后者也算进「该组失败数」⇒ predictedFailureCount
        永远对不上，P5 校验会**整批 refuted**（那是量具坏了，不是 scout 没用）。
        传 node id 才是「这一组 = 这些用例」。
      · `group_key` 走 `-k`（第二阶段 D 臂的历史口径，逐字不变）：类名形状下
        `-k IsliceExtendedTests` 选出来的恰好就是那一组。
      · 两者都为空时退回仓库根跑全量 —— 与旧行为一致。
    """
    from coding_agent import pytest_counts
    exe = VERIFY_PYTHON if Path(VERIFY_PYTHON).is_file() else sys.executable
    cmd = [exe, *PYTEST_VERIFY_ARGS]
    if extra_args:
        cmd += list(extra_args)
    sel = [str(n) for n in (node_ids or []) if str(n).strip()]
    if sel:
        cmd += sel
    elif group_key:
        cmd += ["-k", group_key]
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    text = (p.stdout or "") + (p.stderr or "")
    f_, p_ = pytest_counts(text)
    return p.returncode, f_, p_, text[-1200:]


def verify_finding(snapshot: Path, copy_dir: Path, finding: dict,
                   group_key: str, group_failed: int,
                   *, node_ids: list[str] | None = None,
                   extra_args: list[str] | None = None) -> dict:
    """P5 主流程：在 snapshot 的**干净副本** copy_dir 上应用改法并复跑该组。

    程序判定 verified/refuted（不采信 scout 自述）：
      verified ⇔ 应用成功 ∧ exit != 0 合法之外的判据全中：
                 修复后该组失败数 == 0 且 predictedFailureCount == 报给该组的失败数。
      其余一律 refuted，note 里写清楚差在哪一步。
    ⚠️ 验证进程 ≠ 侦察进程：这里是 harness（driver 进程）直接 spawn 的独立
      subprocess，scout 不参与；参考实现只把这句话写在提示词里，我们写在代码里。

    ⚠️ R7（字段改名，语义没动）：旧记录里那个 `observed_before` **不是观察值** ——
      它的值是 `group_failed`，即 driver 起始基线那次整仓 pytest 输出按测试类聚合
      出来的**基线计数**，本进程从来没有在副本里先跑一遍"修复前"（跑一遍要多花一次
      pytest，且会把 D 臂的墙钟成本再抬一档）。名字读起来像"我观察到了修复前有几个
      失败"，所以改名为 `baseline_group_failed` 并加 `baseline_source` 写明出处；
      真正由本进程观察到的只有 `observed_after`（应用修复后复跑该组）。
    """
    rec = {"finding_id": finding.get("id"), "group": group_key,
           "verdict": "refuted", "note": "", "applied": False,
           "predicted": finding.get("predictedFailureCount"),
           "baseline_group_failed": group_failed,
           "baseline_source": ("driver 起始基线（整仓 `pytest -q` 输出）按测试类聚合出的"
                               "该组失败数；**不是**在校验副本里现跑出来的观察值"),
           "observed_after": None, "exit_code": None}
    if copy_dir.exists():
        shutil.rmtree(copy_dir, ignore_errors=True)
    shutil.copytree(snapshot, copy_dir, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".pytest_cache"))
    ok, why = apply_minimal_fix(copy_dir, finding)
    rec["applied"], rec["note"] = ok, why
    if not ok:
        return rec
    try:
        code, f_after, p_after, tail = run_group_tests(copy_dir, group_key,
                                                       node_ids=node_ids,
                                                       extra_args=extra_args)
    except subprocess.TimeoutExpired:
        rec["note"] += "；校验 pytest 超时"
        return rec
    rec["exit_code"], rec["observed_after"] = code, f_after
    pred = finding.get("predictedFailureCount")
    # P5 主判据（任务书口径）：**预测 == 实际失败数** 才算 verified。
    # observed_after==0 是同一件事的印证（改对了才会归零），两者都记进证据。
    checks = []
    checks.append((f"predictedFailureCount={pred} 与该组基线失败数 {group_failed} 不符"
                   f"（基线口径见 baseline_source）",
                   pred == group_failed))
    checks.append(("修复后该组仍有失败" if f_after else None, f_after == 0))
    bad = [msg for msg, good in checks if not good]
    if bad:
        rec["note"] += "；" + "；".join(bad)
        rec["tail"] = tail[-600:]
        return rec
    rec["verdict"] = "verified"
    rec["note"] += f"；{group_key} 失败 {group_failed} → 0（预测吻合）"
    return rec


# ── P2：索引构建 ───────────────────────────────────────────────────────────
def build_index(groups: list[dict], records: list[dict],
                max_chars: int = DEPENDENCY_OUTPUT_MAX_CHARS) -> tuple[str, int]:
    """≤max_chars 的父层索引。每行 = id | file:line | 最小改法一句话 | 证据指针。

    溢出策略（不许悄悄截掉一条还声称「全部结论在此」）：
      逐行拼装到预算为止；放不下的组不进正文，但在尾行**点名**
      「因索引上限省略：<ids>（可用 read_findings 取）」。
      若连尾行都放不下，则砍正文行数直到尾行能放下 —— 透明度优先于行数。
    返回 (文本, 真实字符数)。

    B1-3：索引行末尾标出**这条结论是哪一侧产出的**（来源 = scout 的哪个通道）。
    来源不在 FINDINGS_SOURCES 里（= 包装会话文本，理论上已不可能出现）的条目
    **一律不进索引** —— 这里是第二道判据层的锁，跟抽取层的锁互为冗余。
    """
    lines: list[str] = []
    omitted: list[str] = []
    header = "【driver 已派出的侦察结论（仅 scout 自己产出且过 P5 校验的条目；细节用 read_findings(id) 单条取回）】"
    used = len(header) + 1
    for rec in records:
        if rec["status"] != "completed_verified":
            g = next((x for x in groups if x["group"] == rec["group"]), None)
            n = g["failed_count"] if g else "?"
            lines.append(f"- 组 {rec['group']}：未定位（{rec.get('why', '原因见 scout 产物')}），"
                         f"不要依赖这组结论。scout={rec.get('scout_file', '-')}")
            continue
        # 一条 verified finding 的索引行。**先算长度再决定收不收**（used 只在收下时增长）。
        for f in rec["findings"]:
            if f.get("verification", {}).get("verdict") != "verified":
                continue   # refuted 的不进索引（P5 验收判据）
            src = (rec.get("finding_sources") or {}).get(f["id"]) or rec.get("summary_source")
            if src is None:
                # 旧产物（B1 之前）没有来源字段。不臆断成 scout，也不因此丢掉结论 ——
                # 行里如实写「未记录」。新产物一定带来源。
                src_label = "未记录（B1 之前的产物）"
            elif src in FINDINGS_SOURCES:
                src_label = f"scout/{src}"
            else:
                # B1：产出者不是 scout（例如旧标签 stream_text = 包装会话文本）
                # ⇒ 一律不进父层索引。
                continue
            mf = (f.get("minimalFix") or {}).get("summary") or \
                 str((f.get("minimalFix") or {}).get("new") or "")[:80]
            line = (f"- {f['id']} | {f.get('file')}:{f.get('line')} | {mf} | "
                    f"预计消除 {f.get('predictedFailureCount')} 个失败 | "
                    f"来源={src_label} | 详见 read_findings('{f['id']}')")
            if used + len(line) + 1 <= max_chars:
                lines.append(line)
                used += len(line) + 1
            else:
                omitted.append(str(f["id"]))
    body = header + "\n" + "\n".join(lines)
    if omitted:
        tail = f"\n（因索引上限省略：{', '.join(omitted)}，可用 read_findings 取）"
        while len(body) + len(tail) > max_chars and len(lines) > 1:
            dropped = lines.pop(-1)
            omitted.insert(0, dropped.split("|")[0].strip("- ").split()[0])
            tail = f"\n（因索引上限省略：{', '.join(omitted)}，可用 read_findings 取）"
            body = header + "\n" + "\n".join(lines)
        body += tail
    return body, len(body)


def policy_task_suffix(index_text: str, index_chars: int, k: int) -> str:
    """policy 臂的题面追加段：**陈述句**，没有「要不要用 subagent」的选择题。"""
    return f"""

已为你派出 {k} 组侦察（driver 决定，不是你的选择），结论如下
（完整明细不在这里，按 id 用 read_findings 工具单条取回）：
{index_text}
（索引实测 {index_chars} 字符，上限 {DEPENDENCY_OUTPUT_MAX_CHARS}。）

这些结论来自**只读**侦察，已经过程序独立校验，但可能仍不完整或过时
（侦察基于版本快照，此后仓库没变）。动手前用 read_file 核一眼行号。
改动由你自己做；未被定位的组请自行排查。"""


# ── P1+P3：单组 scout 会话 ────────────────────────────────────────────────
def _scout_prompt(group: dict, label: str, k_idx: int) -> str:
    g = group["group"]
    samples = "\n".join(group.get("sample_lines") or [])
    sel_hint = ""
    if group.get("group_key_kind") == "test_file":
        # 组键是**文件路径**时，`-k <path>` 选不出东西（pytest 的 -k 只匹配用例名）。
        # 不写这一句，侦察兵会拿文件名去填 keyword= —— 那是一次「看起来调用了、
        # 实际什么都没跑」的空转（server 的 run_tests 会把整仓当选择集）。
        sel_hint = (f"本组的范围就是文件 `{g}`：run_tests 请传 test_path=\"{g}\""
                    f"（**不要**把文件路径填进 keyword=；keyword 只用于用例名片段）。")
    return f"""你是只读侦察兵，负责测试组 `{g}`（当前该组有 {group['failed_count']} 个失败）。

失败样例：
{samples}

任务：在该仓库里定位这一组失败的**根因**，不改任何文件（你也没有写权限）。
可用工具：list_files / search_code / read_file / repo_map / run_tests
（run_tests 传 keyword="{g}" 只看本组）。
{sel_hint}

最后一条消息**必须是一个 JSON 代码块**（除此之外不要再写别的），形状：
```json
{{"findings": [
  {{"id": "g{k_idx}-f1", "file": "相对路径", "line": 行号整数,
    "symptom": "观察到什么", "rootCause": "为什么导致这些失败",
    "minimalFix": {{"file": "相对路径", "old": "文件中唯一出现的原文片段",
                    "new": "建议替换成的文本", "summary": "一句话改法"}},
    "evidence": "支撑证据（测试输出/代码行为）",
    "predictedFailureCount": 按此最小改法本组应从多少个失败变 0}}
]}}
```
规则：
- id 必须是 g{k_idx}-f1、g{k_idx}-f2 …（组号 {k_idx}）。
- minimalFix.old 必须是目标文件里**逐字唯一出现**的片段（先用 read_file 核对原文）。
- predictedFailureCount 是你预测修复后本组消除的失败总数（通常=本组当前失败数）。
- 没能定位根因就交 {{"findings": []}} —— 交一份猜的比交白卷更坏。"""


def _extract_last_json(text: str) -> dict | None:
    """取文本里最后一个平衡的 {...}（含 ```json 围栏内的）。解析失败返回 None。"""
    fence = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = list(fence)
    if not candidates:
        # 手工扫最后一个平衡括号块
        end = text.rfind("}")
        while end != -1:
            depth = 0
            for i in range(end, -1, -1):
                ch = text[i]
                if ch == "}":
                    depth += 1
                elif ch == "{":
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[i:end + 1])
                        break
            if candidates:
                break
            end = text.rfind("}", 0, end)
    for c in reversed(candidates):
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            continue
    return None


def _jsonable(u: Any) -> Any:
    """把 SDK 的 usage / TaskUsage 转成可 JSON 序列化的形状（认不得的整体 str 截断）。

    与 usage_log.plain_result_usage 同一套降级阶梯（dict → 原样；对象带 items() →
    展开；其余 str）。这里独立一份是因为要处理的**不只是** ResultMessage.usage，
    还有 TaskNotificationMessage.usage（TypedDict 或 CLI 原样的 dict）。
    """
    if u is None:
        return None
    if isinstance(u, (bool, int, float, str)):
        return u
    if isinstance(u, dict):
        return {str(k): (v if isinstance(v, (bool, int, float, str, type(None)))
                         else str(v)[:400]) for k, v in u.items()}
    items = getattr(u, "items", None)
    if callable(items):
        try:
            return {str(k): (v if isinstance(v, (bool, int, float, str, type(None)))
                             else str(v)[:400]) for k, v in items()}
        except Exception:  # noqa: BLE001
            pass
    return {"_raw": str(u)[:400]}


# R5：scout 侧用量记录里「拿不到什么」的书面说明。
# ⚠️ 不用空字段冒充已实现：拿不到的键**根本不出现在记录里**，理由写在这里，
#    并且每个 scout 记录里带一句 `unavailable_note` 指向同一段文字。
# ⚠️ B2（修复轮 #3）：落盘行的**两套键名映射表**也写在这里 —— 复审实测「按文档名读
#    policy_scout_usage.jsonl 全是 None」，因为旧行只有 usage_log.py 的原名。现在
#    同一行里两套都写、值相等：
#       文档名（本模块与 AB-SUBAGENT-V2.md 用的）  |  usage_log.py 原名（usage.jsonl 口径）
#       input_uncached                            | input_uncached（同名，本来就是 P0 加的）
#       cache_read                                | cache_read（同名）
#       cache_creation                            | cache_creation（同名）
#       output_tokens                             | output
#       cost_usd                                  | total_cost_usd
#       subtype                                   | subtype（同名）
#       num_turns                                 | num_turns（同名）
#       task_usage                                | （usage_log 里没有；来自 TaskUsage）
#    两套值由 usage_log.log_usage 的**同一次**调用产生（口径只有那一处实现），
#    不是在这里手算第二遍。
USAGE_KEY_MAP = {
    "input_uncached": "input_uncached", "cache_read": "cache_read",
    "cache_creation": "cache_creation", "output_tokens": "output",
    "cost_usd": "total_cost_usd", "subtype": "subtype", "num_turns": "num_turns",
    "task_usage": None,
}
SCOUT_USAGE_LIMITS = (
    "拿得到：① TaskNotificationMessage.usage（SDK TaskUsage）—— scout 自己的 "
    "total_tokens/tool_uses/duration_ms；② 该 scout 会话的 ResultMessage —— "
    "usage / model_usage（逐模型 inputTokens/cacheReadInputTokens/"
    "cacheCreationInputTokens/outputTokens/costUSD）/ total_cost_usd / num_turns。"
    "因为每次派发都是**独立的 query() 会话**，所以 ② 就是「每个 scout 一条」的成本口径。"
    "拿不到：SDK 不给 **scout 单独**的 cache 拆分与费用 —— TaskUsage 只有三个整数，"
    "usage/model_usage 是『该会话』口径（scout + driver 那 1–2 个包装回合：发题面、调 Agent）。"
    "所以 cost_usd / cache_* 记的是**scout 会话**的值，不是严格只含 scout 的；"
    "这一点写在 scope 字段里，不假装它是纯 scout 成本。"
    "键名：本文件的行**同时**写文档名与 usage_log 原名（值相等），"
    "映射表见 USAGE_KEY_MAP / key_map 字段 —— output_tokens=output、"
    "cost_usd=total_cost_usd、subtype/subtype、num_turns/num_turns、"
    "task_usage 只在文档名这一套里有（来自 TaskUsage，usage_log 不管它）。"
)


def _usage_row_via_log_usage(result: Any) -> dict | None:
    """用 usage_log.log_usage 算出这一会话的用量行（**三口径只有那一处实现**）。

    ⚠️ 为什么落到 %TEMP% 再删掉：log_usage 的契约是「写一行到 log_path 并返回那一行」，
    没有纯函数形态；usage_log.py **不在本轮范围内**，不能为了取返回值去改它的签名。
    所以写到一个一次性文件上、取回 row 后立刻删除 —— 仓库里不留任何多余文件，
    也不会让同一个 scout 在正式文件里出现两行（那会让下游汇总重复计数）。
    """
    if result is None:
        return None
    import usage_log
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")   # 文件名里不能有 ':'（Windows）
    tmp = Path(tempfile.gettempdir()) / f"mcc-scout-usage-{os.getpid()}-{stamp}.jsonl"
    try:
        return usage_log.log_usage(result, script="agent/policy_dispatch.py",
                                   key_source="scout-session", log_path=tmp)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _scout_accounting(*, result: Any, notify_usage: Any, elapsed_s: float,
                      label: str, k_idx: int, group: str, task_id: Any,
                      usage_log_path: Path) -> dict:
    """R5 + B2：一个 scout 一条 scout 侧用量记录（六口径 + 两套键名 + 拿不到的书面说明）。

    六个口径的落点（预注册 §6.8「scout 侧只做记录」的兑现）：
      input_uncached / cache_read / cache_creation / output_tokens / cost_usd
        —— 来自该 scout 会话的 ResultMessage（经 usage_log.log_usage 的**同一套**
           三口径实现，不在这里手抄一份）。
      elapsed_s —— 本进程墙钟（scout 会话从起会话到收 ResultMessage）。

    B2：**写进 policy_scout_usage.jsonl 的那一行同时带文档名与 usage_log 原名**
    （映射表 USAGE_KEY_MAP）。旧实现直接用 log_usage 的行，只有 usage_log 原名，
    于是「按文档名读」全是 None（复审实测）。
    """
    row = _usage_row_via_log_usage(result)
    unavailable = ""
    if result is None:
        unavailable = ("该 scout 会话没有返回 ResultMessage（流在 TaskNotification 之后结束/"
                       "空转或超时）⇒ 六个口径里除 elapsed_s 外全部**无数据**，"
                       "只留 TaskNotificationMessage 的 TaskUsage 三个整数。")
    scope = "scout 会话（独立 query()；含 driver 的 1–2 个包装回合）"
    rec = {
        "ts": _now_iso(), "run": os.environ.get("RUN_LABEL", "default"),
        "script": "agent/policy_dispatch.py", "key_source": "scout-session",
        "model": (row or {}).get("model"),
        "label": label, "k": k_idx, "group": group, "task_id": task_id,
        "scope": scope,
        # ── 文档名（本模块 / AB-SUBAGENT-V2.md 用这一套）──────────────────
        "input_uncached": (row or {}).get("input_uncached"),
        "cache_read": (row or {}).get("cache_read"),
        "cache_creation": (row or {}).get("cache_creation"),
        "output_tokens": (row or {}).get("output"),
        "cost_usd": (row or {}).get("total_cost_usd"),
        "elapsed_s": round(elapsed_s, 1),
        "num_turns": (row or {}).get("num_turns"),
        "subtype": (row or {}).get("subtype"),
        # TaskUsage：scout 自己报的聚合（total_tokens/tool_uses/duration_ms）。
        "task_usage": _jsonable(notify_usage),
        # ── usage_log 原名（usage.jsonl 口径；值同上，保留以免下游按旧名读）──
        "output": (row or {}).get("output"),
        "total_cost_usd": (row or {}).get("total_cost_usd"),
        "result_usage": (row or {}).get("result_usage"),
        "models": (row or {}).get("models"),
        "key_map": USAGE_KEY_MAP,
        "source": {
            "session_usage": "ResultMessage.usage / model_usage / total_cost_usd",
            "task_usage": "TaskNotificationMessage.usage（SDK TaskUsage）",
            "elapsed_s": "本进程墙钟",
        },
        "limits": SCOUT_USAGE_LIMITS,
    }
    if unavailable:
        rec["unavailable_note"] = unavailable
    # B2：正式文件由**本函数**写一行（已经两套键名齐全）；上面那次 log_usage 只用于
    # 取值，写的是 %TEMP% 里的一次性文件（已删）。
    if result is not None:
        _append_jsonl(usage_log_path, rec)
    return rec


def main_thread_code_tool_denial(input_data: dict) -> str | None:
    """主线程（包装会话）调代码工具时要给的理由；不是这种情况就返回 None。"""
    tool = str((input_data or {}).get("tool_name") or "")
    if tool.startswith(f"mcp__{SERVER_NAME}__") and not (input_data or {}).get("agent_id"):
        return ("driver 包装会话不允许使用代码工具：请只调 Task 派 scout 去侦察，"
                "不要自己动手查代码（这次调用已被 harness 策略拒绝）。")
    return None


def make_wrapper_guard():
    """每个 driver 会话一份的 PreToolUse 钩子（闭包持有「这一轮派了几次」的计数）。

    两条策略，都是 B1 的「从构造上消灭包装模型自研」：
      ① **代码工具只给 scout**：主线程（agent_id 为空）调 mcp__code-tools__* 一律拒。
      ② **包装会话只许派一次 Task**：第二次起拒。理由 —— 「截断后自动续派」是
         harness（driver）自己的行为（有 policy_dispatch.jsonl 的 retry_dispatched
         记录可追），不许由包装模型自己再派一次来冒充；而且实测放它自己派会失控：
         cap=2 的那次负对照里包装模型自发派了 **12** 个额外 Task、Task tool_use 总计 45 次、
         scout 侧账单 5.78 USD（同一组本该 6 次会话）。

    返回 `{}` = 不表态，交回正常权限流程。
    """
    seen = {"task": 0}

    async def guard(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        data = input_data or {}
        tool = str(data.get("tool_name") or "")
        agent_id = data.get("agent_id")
        if tool in ("Task", "Agent"):
            if agent_id:
                return {}          # 子代理再派（scout 没有 Task 工具，防御而已）
            seen["task"] += 1
            if seen["task"] > 1:
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "driver 包装会话这一轮只许派一次 scout。续派由 harness 在下一次"
                        "派发里自己做（有 durable 派发记录），不要自己再派。"),
                }}
            return {}
        reason = main_thread_code_tool_denial(data)
        if reason:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }}
        return {}

    return guard


async def guard_code_tools_for_subagent(input_data: dict, tool_use_id: str | None,
                                        context: Any) -> dict:
    """B1-2 的**代码工具**那一条（模块级函数，供检查脚本与文档直接引用）。

    与 `make_wrapper_guard()` 里的代码工具判据同源（都走 `main_thread_code_tool_denial`），
    区别只是它不带「每会话派一次」的计数 —— 真实会话用闭包那一份。

    ⚠️ 为什么不直接把 allowed_tools 砍成 ["Task"]（这条路**试过**）：session 级
    allowed_tools 是**整个会话共享**的，只留 ["Task"] 时 scout 自己的
    `mcp__code-tools__*` 也会被判成 "Permission to use … has been denied because
    Claude Code is running in don't ask mode"，scout 全程空转（实测见
    out/probe_b1_permission.txt 与 out/v2-b1-trunc.console.log：3 组全 undiagnosed、
    0 份 scout 结论、白花 2.18 USD）；`AgentDefinition.permissionMode` 也兜不住。
    钩子按**调用来源**（agent_id）判，才能同时做到「scout 正常」+「包装会话一次都拿不到」。
    """
    reason = main_thread_code_tool_denial(input_data or {})
    if reason:
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}
    return {}


def _scout_agent(turn_cap: int) -> AgentDefinition:
    """scout 的 AgentDefinition（**唯一**一处定义，B1-2 的检查也引用它）。

    两条不变量写在这里，别处不许再抄一份：
      · 只读：tools 只有 5 个只读 MCP 工具 + 一张内置工具黑名单（没有 apply_patch、
        没有 Bash/Write/Edit）—— 「scout 不改文件」是**工具清单**保证的。
      · 权限：会话级 allowed_tools 收紧到只 ["Task"] 之后，scout 的 MCP 调用靠它自己的
        permissionMode 兜住（否则会被 dontAsk 拦成 "Permission to use … denied"）。
    """
    return AgentDefinition(
        description="只读侦察兵：定位指定测试组的失败根因，交结构化 findings。不能改文件。",
        prompt=("你是一个只读侦察兵。只用 list_files/search_code/read_file/repo_map/run_tests，"
                "不要尝试改任何文件。结论必须诚实：没定位到就交空的 findings，不要猜。"),
        tools=[f"mcp__{SERVER_NAME}__{t}" for t in SCOUT_TOOL_NAMES],
        disallowedTools=list(SCOUT_DISALLOWED_TOOLS),
        permissionMode=SCOUT_PERMISSION_MODE,
        mcpServers=[SERVER_NAME],
        maxTurns=turn_cap,
    )


def _scout_options(*, env: dict, snapshot: Path, out_dir: Path,
                   agents: dict) -> ClaudeAgentOptions:
    """driver 侧 scout 会话的**唯一**一处 options 装配（B1-2 的装配层证据）。

    为什么抽成函数：B1 要求「证明包装会话里不存在 code-tools」—— 判据必须是
    **真正生效的那份装配**，不能在检查脚本里手抄一遍（抄出来的两份早晚分叉，
    这正是本仓库栽过多次的失效模式）。所以装配只有这一处，检查脚本与实跑引用的
    是同一个函数。

    B1 的两条装配纪律：
      · 会话级 `allowed_tools` **只有 ["Task"]** —— 包装会话（driver 主会话）不再被
        预批准任何 `mcp__code-tools__*`。它自己动手调代码工具会被 dontAsk 拦下
        （实测计数见 scout 记录的 `wrapper_mcp_calls`，修复后应恒为 0）。
      · scout 的 MCP 权限改由它自己的 `AgentDefinition.permissionMode` 兜住，
        而 scout 的「只读」仍由 AgentDefinition.tools（5 个只读工具）+ disallowedTools
        保证 —— 权限模式放开的是"免询问"，不是"更多工具"。
      · `forward_subagent_text=True`：scout 那一侧的文本（parent_tool_use_id =
        本次 harness 的 Task tool_use id）才会被转发；B1 的流式 fallback 依赖它，
        而它转发的是子代理文本，不是主会话文本。
    """
    return ClaudeAgentOptions(
        cli_path=_cli_path(),
        mcp_servers={
            SERVER_NAME: {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(PROJECT_ROOT / "servers" / "code_tools_server.py")],
                # P4：scout 的整个沙箱世界 = 快照。父层看不到这个 env。
                "env": {
                    "AGENT_REPO_ROOT": str(snapshot),
                    "AGENT_REPO_MAP_DEFAULT": "summary",
                    "AGENT_FINDINGS_DIR": str(out_dir),
                },
            }
        },
        tools=["Task"],                     # 包装会话唯一放开的内置工具
        # B1-2：预批准仍然是「Task + 5 个只读代码工具」—— 因为 scout 用的是同一份会话级
        # 白名单（只砍白名单会把 scout 一起掐死，见 guard_code_tools_for_subagent 的说明）。
        # 「包装会话不得用代码工具」改由 PreToolUse 钩子按**调用来源**判：主线程一律拒，
        # 子代理（scout）放行 ⇒ 包装模型一次都拿不到代码工具的结果，这条路从构造上断了。
        allowed_tools=["Task", *[f"mcp__{SERVER_NAME}__{t}" for t in SCOUT_TOOL_NAMES]],
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[make_wrapper_guard()])]},
        disallowed_tools=[t for t in
                          ["Bash", "Read", "Write", "Edit", "Grep", "Glob",
                           "WebFetch", "WebSearch", "NotebookEdit", "TodoWrite"]],
        agents=agents,
        # ⚠️ 回到 "dontAsk"（与 t3/t4/t10 全部实测批次同一形态）：刚性的「不许问」+
        # allowed_tools 白名单 + 上面那个 PreToolUse 钩子。**不要**为了按来源判权限
        # 而改成 "default" 或 can_use_tool —— 实测那一次会话连 TaskNotification 都没等到
        # （形态不稳），而钩子在 dontAsk 下照样生效。
        permission_mode=PERMISSION_MODE_DONT_ASK,
        strict_mcp_config=True,
        setting_sources=[],
        forward_subagent_text=True,         # B1：转发 scout 那一侧的文本（带 parent id）
        env=env,
        stderr=lambda line: print(f"[scout cli stderr] {line}", file=sys.stderr, flush=True),
    )


def select_findings_payload(*, summary: str, output_file_text: str | None,
                            scout_texts: list[str]) -> tuple[dict | None, str | None]:
    """B1：从**只属于 scout 的**三个来源里取出 findings JSON（顺序即优先级）。

    返回 (payload 或 None, 来源标签或 None)。来源标签 = FINDINGS_SOURCES 之一：
      summary           → scout 的 TaskNotification.summary
      output_file       → scout 自己的任务输出文件
      stream_text_scout → scout 那一侧的流式文本（调用方已按 parent_tool_use_id 过滤）
    ⚠️ 包装会话（主会话）的文本**不在入参里** —— 它根本没有这个入口。
    抽成纯函数是为了让检查脚本能离线断言「包装文本永远不参与」这条性质。
    """
    if summary:
        p = _extract_last_json(summary)
        if isinstance(p, dict) and isinstance(p.get("findings"), list):
            return p, "summary"
    if output_file_text:
        p = _extract_last_json(output_file_text)
        if isinstance(p, dict) and isinstance(p.get("findings"), list):
            return p, "output_file"
    if scout_texts:
        p = _extract_last_json("\n".join(scout_texts))
        if isinstance(p, dict) and isinstance(p.get("findings"), list):
            return p, "stream_text_scout"
    return None, None


async def _scout_session(*, env: dict, group: dict, k_idx: int, label: str,
                         snapshot: Path, out_dir: Path, dispatch_log: Path,
                         recorder: Any, turn_cap: int,
                         followup_partial: str | None,
                         parent_tool_use_id: str) -> dict:
    """起一个 scout 会话（Task 通道），等 TaskNotification 与 ResultMessage，收 findings。

    返回 {"task_id", "status": completed|truncated|failed, "summary",
          "raw_findings": [...]|None, "spawn_tool_use_ids": [...],
          "summary_source", "evidence_author", "wrapper_mcp_calls",
          "wrapper_extra_task_calls", "elapsed_s", "result_subtype",
          "scout_accounting": {...}}
    followup_partial 非 None 时 = P3 续派：提示词里带上半份报告让它接着干。

    ⚠️ R5 的修正在这个函数：旧实现在 TaskNotification 到手时立刻从消息循环里
    `return`，等于**提前关掉 CLI 会话** —— 那个会话的 ResultMessage 永远到不了，
    而 usage/model_usage/total_cost_usd 只有它有 ⇒ scout 侧成本无从记账。现在改为
    「收到通知后继续消费到 ResultMessage」（尾段另有 TAIL_AFTER_NOTIFY_S 上限）。

    ⚠️ B1（修复轮 #3）也在同一个循环里：继续消费给包装模型留出了「通知之后自己接着干」
    的窗口（已实测发生：包装模型自己补派 Task、自调 11 次 MCP 工具、把 findings 写在
    自己的 assistant 文本里）。本函数现在①只接受属于本次 harness 那把 Task 的通知、
    ②只收 `parent_tool_use_id == harness 那把 Task` 的文本（包装会话文本一个字不收）、
    ③装配层把包装会话的 allowed_tools 收成只 ["Task"]。三个来源全落空 ⇒ 该组
    undiagnosed（status=failed + raw_findings=None），**绝不拿包装模型的产出顶替**。
    """
    scout_name = "scout"
    agents = {scout_name: _scout_agent(turn_cap)}
    first = _scout_prompt(group, label, k_idx)
    if followup_partial:
        first = (
            f"{first}\n\n上一次侦察被回合上限截断，这是它留下的半份报告，"
            f"请在此基础上继续（不必从零重来）：\n{followup_partial[:4000]}"
        )
    options = _scout_options(env=env, snapshot=snapshot, out_dir=out_dir, agents=agents)
    # ⚠️ 「必须前台运行」这条不是客套话：实测（本任务）有一次包装模型把 scout 派成了
    # **后台任务**（Agent 的 tool_result 是 "Async agent launched successfully…"），
    # 于是 driver 会话立刻收尾、TaskNotification 永远不会到达这条流 ⇒ 该组只能记
    # undiagnosed，而 scout 还在后台白烧钱。题面写死前台（run_in_background=false），
    # 同时在下面用 `wrapper_background_spawns` 把这个失效模式计数留档。
    prompt = (f"立刻用 Agent 工具派一个 subagent_type=scout 的子代理，"
              f"**必须前台运行**（run_in_background=false —— 后台任务收不到结果通知），"
              f"prompt 参数逐字使用下面这段（不要改写、不要自己先做任何事）：\n\n{first}")

    task_ids: set[str] = set()
    notify: dict | None = None
    notify_usage: Any = None
    # B1：**只有 scout 那一侧**的文本才进这里（见 _consume 的过滤）。
    scout_texts: list[str] = []
    spawn_ids: list[str] = []          # 主会话里出现过的全部 Agent/Task tool_use id
    main_mcp_calls = 0                 # 包装模型自己调 mcp__code-tools__* 的次数（应恒为 0）
    main_mcp_pending: set[str] = set()  # 上面这些调用的 tool_use id（用于配 tool_result）
    main_mcp_denied = 0                # 其中被权限拦下的条数
    scout_tool_results = 0
    scout_tool_errors: list[str] = []
    background_spawns = 0              # 包装模型把 scout 派成后台任务的次数（应为 0）
    ignored_notifications = 0
    result: Any = None
    t0 = time.monotonic()
    agen = query(prompt=prompt, options=options)

    async def _consume():
        """把这条流消费到 ResultMessage（或流结束），过程中**只认 scout 自己的产出**。

        ⚠️ 两条纪律（B1）：
          ① 收到 TaskNotification 后**不返回**（R5 要 ResultMessage），但**只接受第一份
             属于本次 harness 那把 Task 的通知** —— 包装模型自己又派一次 Task 的通知
             一律忽略并计数（旧实现会被后到的通知覆盖 notify，等于让包装模型的
             spawn 顶掉 scout）。
          ② 文本只收 `parent_tool_use_id == 本次 harness 那把 Task tool_use
             id` 的（= scout 那一侧；forward_subagent_text=True 才会转发文本）。
             主会话（parent_tool_use_id 为 None）的文本**一个字都不收** ——
             那是包装模型自己的话，不是 scout 结论。
        """
        nonlocal notify, notify_usage, result, main_mcp_calls, ignored_notifications
        nonlocal scout_tool_results, main_mcp_denied, background_spawns
        it = agen.__aiter__()
        while True:
            wait_s = TAIL_AFTER_NOTIFY_S if notify is not None else SCOUT_RESULT_TIMEOUT_S
            try:
                message = await asyncio.wait_for(it.__anext__(), timeout=wait_s)
            except StopAsyncIteration:
                return None
            except asyncio.TimeoutError:
                print(f"[driver] scout 会话等待超时（{wait_s}s，"
                      f"{'通知之后' if notify else '通知之前'}）", file=sys.stderr, flush=True)
                return None
            name = type(message).__name__
            if isinstance(message, ResultMessage):
                result = message
                return message
            pid = getattr(message, "parent_tool_use_id", None)
            harness_id = spawn_ids[0] if spawn_ids else None
            if name in ("TaskStartedMessage", "TaskUpdatedMessage",
                        "TaskNotificationMessage", "TaskProgressMessage"):
                tid = getattr(message, "task_id", None)
                if tid:
                    task_ids.add(tid)
                if name == "TaskNotificationMessage":
                    nu = getattr(message, "usage", None)
                    tuid = getattr(message, "tool_use_id", None)
                    # B1：只认「本次 harness 那把 Task」的通知（tuid 为空时退化为
                    # 「第一份通知」，且必须还没有出现过别的 Task spawn）。
                    ours = (notify is None
                            and (tuid is None or harness_id is None or tuid == harness_id)
                            and (tuid is not None or len(spawn_ids) <= 1))
                    if ours:
                        notify = {"task_id": tid,
                                  "status": getattr(message, "status", None),
                                  "summary": getattr(message, "summary", "") or "",
                                  "output_file": getattr(message, "output_file", None),
                                  "tool_use_id": tuid,
                                  "accepted_as": "harness_scout"}
                        notify_usage = nu
                    else:
                        # 包装模型自己派的 Task（或迟到的第二份通知）——记一笔，不用它的结论。
                        ignored_notifications += 1
            if hasattr(message, "content") and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        if pid is None:
                            # 主会话的 tool_use。Agent/Task 记 id（第一个就是本次 harness
                            # 那把）；mcp__code-tools__* 说明包装模型自己动手了 —— 记数。
                            if block.name in ("Agent", "Task"):
                                spawn_ids.append(block.id)
                                if recorder is not None:
                                    recorder.note("policy_spawn_call", tool_use_id=block.id,
                                                  group=group["group"], k=k_idx,
                                                  parent_tool_use_id=parent_tool_use_id,
                                                  wrapper_extra=len(spawn_ids) > 1)
                            elif str(block.name).startswith(f"mcp__{SERVER_NAME}__"):
                                main_mcp_calls += 1
                                if block.id:
                                    main_mcp_pending.add(block.id)
                    elif isinstance(block, TextBlock):
                        # B1：只收 scout 那一侧的文本。
                        if pid is not None and harness_id is not None and pid == harness_id:
                            scout_texts.append(block.text)
                    elif isinstance(block, ToolResultBlock) and pid is None:
                        # 包装会话里那些 MCP 调用的回执：被 hook/dontAsk 拦下的会带 is_error；
                        # 另外顺手把「scout 被后台派发」这个失效模式认出来（它会让
                        # TaskNotification 永远到不了，见 _scout_session 的 prompt 说明）。
                        if block.tool_use_id in main_mcp_pending and getattr(block, "is_error", False):
                            main_mcp_denied += 1
                        if block.tool_use_id in spawn_ids:
                            txt = block.content
                            txt = txt if isinstance(txt, str) else str(txt)
                            if "Async agent launched" in txt:
                                background_spawns += 1
                    elif isinstance(block, ToolResultBlock) and pid is not None:
                        scout_tool_results += 1
                        if getattr(block, "is_error", False) and len(scout_tool_errors) < 3:
                            scout_tool_errors.append(str(getattr(block, "content", ""))[:160])
        return None

    try:
        await asyncio.wait_for(_consume(), timeout=SCOUT_RESULT_TIMEOUT_S)
    finally:
        # 提前返回（收到通知但没等到 ResultMessage）时把生成器关掉，别留着 CLI 子进程。
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001 —— 关不掉也不该盖住已有结论
            pass
    elapsed_s = time.monotonic() - t0
    summary = (notify or {}).get("summary") or ""
    output_file_text: str | None = None
    if notify and notify.get("output_file"):
        try:
            of = Path(str(notify["output_file"]))
            if of.is_file():
                output_file_text = of.read_text(encoding="utf-8", errors="replace")
        except OSError:
            output_file_text = None
    # B1：三个来源全是 scout 自己写的（见 FINDINGS_SOURCES）；包装模型的文本不在候选里。
    payload, src = select_findings_payload(
        summary=summary, output_file_text=output_file_text, scout_texts=scout_texts)
    raw_findings = None
    if isinstance(payload, dict) and isinstance(payload.get("findings"), list):
        raw_findings = [f for f in payload["findings"] if isinstance(f, dict)]
    evidence_author = "scout" if (raw_findings is not None and src in FINDINGS_SOURCES) else "none"

    truncated = TRUNC_MARK in summary.lower() if summary else (
        notify is None and result is not None)
    if notify is None:
        status = "failed"
    elif truncated:
        status = "truncated"
    elif str(notify.get("status")) == "completed":
        status = "completed"
    else:
        status = "failed"
    # R5：scout 侧用量记录（scout 会话一条）。
    accounting = _scout_accounting(
        result=result, notify_usage=notify_usage, elapsed_s=elapsed_s,
        label=label, k_idx=k_idx, group=group["group"],
        task_id=(notify or {}).get("task_id"),
        usage_log_path=out_dir / SCOUT_USAGE_LOG_NAME)
    return {"task_id": (notify or {}).get("task_id"), "status": status,
            "summary": summary,
            # B1-3：结论来源（scout 通知 / scout output / scout 那侧的流式文本 / 无）
            "summary_source": src,
            "evidence_author": evidence_author,
            "raw_findings": raw_findings, "spawn_tool_use_ids": spawn_ids,
            "harness_spawn_tool_use_id": (spawn_ids[0] if spawn_ids else None),
            "wrapper_extra_task_calls": max(0, len(spawn_ids) - 1),
            # B1-2 的反证计数：包装会话**尝试**调 MCP 的次数、其中被权限拦下的次数、
            # 以及"有效调用"（= 尝试 - 被拦，必须为 0 才算包装会话真的拿不到代码工具）。
            "wrapper_mcp_calls": main_mcp_calls,
            "wrapper_mcp_denied": main_mcp_denied,
            "wrapper_mcp_effective": max(0, main_mcp_calls - main_mcp_denied),
            "ignored_notifications": ignored_notifications,
            "wrapper_background_spawns": background_spawns,
            "scout_text_msgs": len(scout_texts),
            "scout_tool_results": scout_tool_results,
            "scout_tool_errors": scout_tool_errors,
            "elapsed_s": round(elapsed_s, 1),
            "duration_s": round(elapsed_s, 1),
            "scout_accounting": accounting,
            "result_subtype": getattr(result, "subtype", None),
            "task_ids_seen": sorted(task_ids)}


def _cli_path() -> str:
    from agent.config import CLI_PATH
    return CLI_PATH


# ── 总入口 ─────────────────────────────────────────────────────────────────
def finding_id(label: str | None, k_idx: int, idx: int) -> str:
    """R2：全局唯一的 finding id —— `<label>-g<k>-f<n>`。

    为什么必须带 label：scout 产物落在一个**共享**目录（servers/experiments/out/，
    预注册 §1.1 冻结的落点），而跑批从不清空它 —— 于是 `g1-f1` 这种"只在单份产物内
    唯一"的 id 在多轮产物并存时必然重名（第一轮跑批实测 12 次 read_findings 全废，
    服务端日志里每个 id 都出现在 5 份产物里）。label 本身就是「哪一次运行」，
    拼进去之后 id 在整个 findings 目录内唯一，同时**仍然可读**（不是哈希）：
    `subagent-ab-v2-D-1-g1-f1` 一眼能看出是 D-1 那次运行的第 1 组第 1 条。

    label 为 None 时退化成旧形状（自检/离线脚本用，见 subagent_selfcheck 第 8 组）。
    """
    base = f"g{k_idx}-f{idx}"
    return f"{label}-{base}" if label else base


def normalize_finding(raw: dict, k_idx: int, idx: int,
                      label: str | None = None) -> dict | None:
    """字段齐全才收（缺 = None，宁可少一条也不留半条）。id 强制唯一形状。

    id 由 driver **重写**、不采信 scout 自报的那个 —— scout 只知道自己这一组，
    它给不出跨运行的唯一 id（见 finding_id）。R2 之后 id = `<label>-g<k>-f<n>`；
    不带 label（旧调用形态）时保持 `g<k>-f<n>`。
    """
    f = {k: raw.get(k) for k in FINDING_REQUIRED_FIELDS}
    if any(v is None for v in (f["id"], f["file"], f["line"], f["symptom"],
                               f["rootCause"], f["minimalFix"], f["evidence"])):
        return None
    if not isinstance(f["predictedFailureCount"], int):
        try:
            f["predictedFailureCount"] = int(f["predictedFailureCount"])
        except (TypeError, ValueError):
            return None
    f["id"] = finding_id(label, k_idx, idx)
    # ⚠️ 只保留 FINDING_REQUIRED_FIELDS 里的键 —— 预注册 §1.1 冻结了 schema，
    #    scout 多报的字段（比如它自报的旧形状 id）一律不进产物，免得 schema 悄悄长胖。
    f["line"] = int(f["line"]) if str(f["line"]).isdigit() else f["line"]
    return f


async def run_policy_dispatch(*, env: dict, groups: list[dict], label: str,
                              fixture: Path, out_dir: Path,
                              recorder: Any, turn_cap: int = 24,
                              verify_python_note: str = "",
                              pytest_extra_args: list[str] | None = None) -> dict:
    """P1–P5 全流程。返回 {index_text, index_chars, k, records, metrics, snapshot}。

    ⚠️ `pytest_extra_args`（P1 实例模式接线）：把额外参数**原样**带给两处独立校验的
    pytest（`-c <临时 ini>` 与 `--rootdir`）。真实仓库自带的 ini 在本机 pytest 9 下会
    INTERNALERROR(exit 3)，不替换配置就没法跑 P5 校验 —— 这是「让 C 臂可跑」的必要接线，
    不是第二个变量（它只作用于 driver 侧的独立校验进程，不进 agent 的任何上下文）。
    缺省 None ⇒ 命令行与第二阶段 D 臂逐字同形。

    ⚠️ **快照泄漏是本函数唯一的资源风险**（P4 验收：git worktree list 不得有残留）。
    快照在 try 之外创建、在 finally 里移除 —— 调用方拿到的是「已经清理完毕」的
    结果；snapshot 字段只留路径/head 字符串做证据，不再要求调用方兜底。
    任何一组抛异常，该组记 failed，不影响其它组。

    每组返回的 record 里（R4/R5 新增，其余字段与改动前逐字同形）：
      attempts          尝试链 [{n, status, task_id, elapsed_s, scout_accounting, ...}]
      scout_accounting  该组**最后一次尝试**的 scout 侧用量（明细见 attempts）
    """
    dispatch_log = out_dir / DISPATCH_LOG_NAME
    snapshot = SnapshotWorktree(fixture, label)
    snap_path = snapshot.create()
    records: list[dict] = []
    metrics: dict[str, Any] = {}
    try:
        snap_head = snapshot.head()
        clean_at_end = ""

        for k_idx, group in enumerate(groups, start=1):
            gkey = group["group"]
            base_rec = {
                "ts": _now_iso(), "label": label, "k": k_idx, "group": gkey,
                "group_failed": group["failed_count"], "turn_cap": turn_cap,
                "snapshot_head": snap_head, "snapshot_path": str(snap_path),
            }
            # R4：这一组的**尝试链**（durable 记录里的 attempts 就是它）。
            # 不只在内存里攒 —— 每个 attempt 各写一行 JSONL，终态行带全链。
            attempts: list[dict] = []
            _append_jsonl(dispatch_log, {**base_rec, "event": "dispatched",
                                         "attempt_no": 1})
            if recorder is not None:
                recorder.note("policy_dispatch", k=k_idx, group=gkey,
                              group_failed=group["failed_count"],
                              snapshot=str(snap_path), snapshot_head=snap_head,
                              event="dispatched")
            session: dict | None = None
            retried = False
            try:
                session = await _scout_session(
                    env=env, group=group, k_idx=k_idx, label=label,
                    snapshot=snap_path, out_dir=out_dir, dispatch_log=dispatch_log,
                    recorder=recorder, turn_cap=turn_cap, followup_partial=None,
                    parent_tool_use_id=f"driver-k{k_idx}")
                attempts.append(_attempt_record(1, session, retry_of=None))
                if session["status"] == "truncated":
                    # P3：自动续派一次（新会话、同一 scout 定义，带上半份报告）。
                    retried = True
                    _append_jsonl(dispatch_log, {**base_rec, "event": "retry_dispatched",
                                                 "attempt_no": 2,
                                                 "partial_chars": len(session["summary"])})
                    if recorder is not None:
                        recorder.note("policy_dispatch", k=k_idx, group=gkey,
                                      event="retry_dispatched",
                                      partial_chars=len(session["summary"]))
                    session = await _scout_session(
                        env=env, group=group, k_idx=k_idx, label=label,
                        snapshot=snap_path, out_dir=out_dir, dispatch_log=dispatch_log,
                        recorder=recorder, turn_cap=turn_cap,
                        followup_partial=session["summary"],
                        parent_tool_use_id=f"driver-k{k_idx}r")
                    attempts.append(_attempt_record(2, session, retry_of=1))
            except Exception as exc:  # noqa: BLE001 —— 一组炸不许带崩整轮
                session = {"task_id": None, "status": "failed", "summary": "",
                           "raw_findings": None, "spawn_tool_use_ids": [],
                           "scout_accounting": None,
                           "error": f"{type(exc).__name__}: {exc}"[:300]}
                attempts.append({"n": len(attempts) + 1, "status": "failed",
                                 "error": session["error"], "retry_of": None})

            attempt_status = session["status"]
            findings_in = session.get("raw_findings")
            src = session.get("summary_source")
            author = session.get("evidence_author") or "none"
            scout_file = out_dir / f"scout-{label}-{k_idx}.json"
            rec_out = {
                "label": label, "k": k_idx, "group": gkey,
                "group_failed": group["failed_count"],
                "attempt_status": attempt_status, "retried": retried,
                "attempts": attempts,
                "snapshot_head": snap_head, "snapshot_path": str(snap_path),
                "scout_task_id": session.get("task_id"),
                "spawn_tool_use_ids": session.get("spawn_tool_use_ids") or [],
                "harness_spawn_tool_use_id": session.get("harness_spawn_tool_use_id"),
                "summary_chars": len(session.get("summary") or ""),
                # B1-3：结论来源（scout 通知 / scout output / scout 那侧流式文本 / none）
                # 与产出者（scout / none —— 包装会话的文本永远不是候选）。
                "summary_source": src,
                "evidence_author": author,
                # B1：包装模型自己动手的证据（修复后应恒为 0）。
                "wrapper_mcp_calls": session.get("wrapper_mcp_calls"),
                "wrapper_mcp_denied": session.get("wrapper_mcp_denied"),
                "wrapper_mcp_effective": session.get("wrapper_mcp_effective"),
                "wrapper_extra_task_calls": session.get("wrapper_extra_task_calls"),
                "ignored_notifications": session.get("ignored_notifications"),
                "wrapper_background_spawns": session.get("wrapper_background_spawns"),
                "scout_text_msgs": session.get("scout_text_msgs"),
                "scout_tool_results": session.get("scout_tool_results"),
                "scout_tool_errors": session.get("scout_tool_errors") or [],
                # R5：scout 侧用量/成本/耗时随 scout 产物一起留档。
                "scout_accounting": session.get("scout_accounting"),
                "findings": [], "verification": [],
                # B1-3：每条 finding 的来源（按 id 索引，不动 finding 自身的 schema）。
                "finding_sources": {},
                "status": "undiagnosed", "why": "",
            }
            if attempt_status == "failed":
                bg = session.get("wrapper_background_spawns") or 0
                rec_out["why"] = (
                    "scout 会话失败："
                    + (f"包装模型把 scout 派成了**后台任务**（{bg} 次 Async agent launched）"
                       f"⇒ 这条流永远收不到 TaskNotification；该组按 undiagnosed 处理。"
                       if bg else
                       f"{session.get('error') or session.get('result_subtype') or '无通知'}"))
            elif findings_in is None:
                rec_out["why"] = (f"没有拿到 **scout 自己**的 findings JSON"
                                  f"（可用来源 summary/output_file/流式文本都落空，"
                                  f"来源={src or 'none'}）。包装会话的文本不作数。")
            elif len(findings_in) == 0:
                rec_out["why"] = "scout 如实交了空 findings（未定位）"
            else:
                normed = [f for f in
                          (normalize_finding(r, k_idx, i, label)
                           for i, r in enumerate(findings_in, 1))
                          if f]
                # P5：逐条独立进程校验。副本目录 = 快照的干净拷贝
                # （不动 scout 读过的快照本体 —— 「父打补丁期间 scout 仍读到缺陷」的证据链要保住）。
                verdicts = []
                for fi, f in enumerate(normed, 1):
                    copy_dir = Path(str(snap_path) + f"-verify-k{k_idx}-{fi}")
                    v = verify_finding(snapshot=snap_path, copy_dir=copy_dir, finding=f,
                                       group_key=gkey, group_failed=group["failed_count"],
                                       node_ids=group.get("node_ids") or None,
                                       extra_args=pytest_extra_args)
                    shutil.rmtree(copy_dir, ignore_errors=True)
                    verdicts.append(v)
                    f["verification"] = v
                    # B1-3：每条 finding 的来源（同一次抽取 ⇒ 同源）。
                    rec_out["finding_sources"][f["id"]] = src
                rec_out["verification"] = verdicts
                verified = [f for f in normed
                            if f["verification"]["verdict"] == "verified"]
                rec_out["findings"] = normed
                if verified:
                    rec_out["status"] = "completed_verified"
                else:
                    rec_out["why"] = ("全部 findings 未通过独立校验：" +
                                      "; ".join(f"{v['finding_id']}({v['note'][:80]})"
                                                for v in verdicts))[:400]
            scout_file.write_text(json.dumps(rec_out, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
            # R4：终态 = **组的状态**（completed | undiagnosed），不是某一次尝试的状态。
            # 旧实现在「续派之后仍然截断」时写的是 event="truncated" —— 组的最终结论
            # （undiagnosed）反而没进状态记录，读记录的人看不出这一组到底算不算定位到。
            # 现在 event/group_status 一律取组状态，attempt_status 另存，
            # retry_outcome 把「截断 → 续派 → ?」这条链写成人话。
            group_status = ("completed" if rec_out["status"] == "completed_verified"
                            else "undiagnosed")
            retry_outcome = ";".join(
                f"attempt{a['n']}={a['status']}" for a in attempts) or "无尝试记录"
            truncated_retry_succeeded = bool(
                retried and len(attempts) == 2
                and attempts[0]["status"] == "truncated"
                and group_status == "completed")
            # B4：C1 的治疗门槛按**组**落盘（复审实测"每组最后一次尝试的 Task 数"
            # 当门槛是含混的：v2-fix-trunc 里它 =4，而 K=3、scout 会话=6、
            # 全部 attempt 的 Task 数 =9）。
            c1 = {
                "k": k_idx, "group": gkey,
                "dispatched_once": bool(attempts),
                "scout_conclusion_obtained": any(
                    (a.get("summary_source") in FINDINGS_SOURCES) for a in attempts),
                "scout_conclusion_source": next(
                    (a.get("summary_source") for a in attempts
                     if a.get("summary_source") in FINDINGS_SOURCES), None),
                "group_status": group_status,
                "attempts": len(attempts),
                "retried": retried,
                "wrapper_mcp_calls": sum((a.get("wrapper_mcp_calls") or 0)
                                         for a in attempts),
                # 有效调用 = 尝试 - 被拦；门槛判据用它之外的证据（这里只是反证计数）。
                "wrapper_mcp_effective": sum((a.get("wrapper_mcp_effective") or 0)
                                             for a in attempts),
            }
            _append_jsonl(dispatch_log, {
                **base_rec, "event": group_status, "group_status": group_status,
                "attempt_status": attempt_status,
                "retried": retried, "attempts": attempts,
                "retry_outcome": retry_outcome,
                "truncated_retry_succeeded": truncated_retry_succeeded,
                "scout_file": scout_file.name,
                "verified_ids": [
                    f["id"] for f in rec_out["findings"]
                    if f.get("verification", {}).get("verdict") == "verified"],
                "scout_accounting": session.get("scout_accounting"),
                # B1-3：组状态的结论来源（每组都写）+ 每条 finding 的来源分布。
                "summary_source": src,
                "evidence_author": author,
                "finding_sources": rec_out["finding_sources"],
                "wrapper_mcp_calls": c1["wrapper_mcp_calls"],
                "wrapper_mcp_effective": c1["wrapper_mcp_effective"],
                "wrapper_extra_task_calls": session.get("wrapper_extra_task_calls"),
                # B4：这一组的治疗门槛记录（字段名即判据）。
                "c1": c1,
                "why": rec_out["why"]})
            if recorder is not None:
                recorder.note("policy_dispatch", k=k_idx, group=gkey,
                              event=group_status, group_status=group_status,
                              attempt_status=attempt_status,
                              retried=retried, retry_outcome=retry_outcome,
                              truncated_retry_succeeded=truncated_retry_succeeded,
                              scout_file=scout_file.name,
                              verified_ids=[f["id"] for f in rec_out["findings"]
                                            if f.get("verification", {}).get("verdict") == "verified"],
                              why=rec_out["why"])
            records.append(rec_out)

        # P4 不变量检查位：快照从头到尾必须没被改过（scout 只读的反证）。
        clean_at_end = snapshot.status_porcelain()
    finally:
        remove_note = snapshot.remove()

    if clean_at_end:
        metrics["policy_snapshot_dirty"] = clean_at_end[:400]
    index_text, index_chars = build_index(groups, records)
    # B4：Task tool_use 的两种计法（**都不是门槛**，门槛见 policy_c1_gate）：
    #   policy_task_tool_use_count              全部 attempt 的 Task tool_use 总数
    #                                           （含包装模型自己发起的那些）
    #   policy_task_tool_use_count_last_attempt 每组最后一次尝试的 Task 数之和
    #                                           （= 退役键 policy_dispatch_calls 的真实含义）
    task_uses_all = sum((a.get("task_tool_use_count") or 0)
                        for r in records for a in (r.get("attempts") or []))
    task_uses_last = 0
    for r in records:
        atts = r.get("attempts") or []
        if atts:
            task_uses_last += atts[-1].get("task_tool_use_count") or 0
    c1_records = [{"k": r["k"], "group": r["group"],
                   "dispatched_once": bool(r.get("attempts")),
                   "scout_conclusion_obtained": any(
                       (a.get("summary_source") in FINDINGS_SOURCES)
                       for a in (r.get("attempts") or [])),
                   "group_status": ("completed" if r["status"] == "completed_verified"
                                    else "undiagnosed")}
                  for r in records]
    metrics.update({
        "policy_enabled": True,
        "policy_k_groups": len(groups),
        # B4：退役 `policy_dispatch_calls`（含义含混），改名并给出两种明确计法。
        "policy_task_tool_use_count": task_uses_all,
        "policy_task_tool_use_count_last_attempt": task_uses_last,
        # B4：主口径 —— 「一次 harness 派发」= 一个 scout 会话；续派算两次。
        "policy_scout_sessions": sum(len(r.get("attempts") or []) for r in records),
        "policy_gate_metric": PRIMARY_GATE_METRIC,
        "policy_gate_criterion": GATE_CRITERION,
        "policy_groups_with_scout_conclusion": sum(
            1 for c in c1_records if c["scout_conclusion_obtained"]),
        "policy_c1_gate": ("passed" if c1_records and all(
            c["scout_conclusion_obtained"] for c in c1_records) else "failed"),
        "policy_c1_groups": c1_records,
        # B1：包装模型自己动手的三个反证计数（修复后 effective 应为 0）。
        "policy_wrapper_mcp_calls": sum((r.get("wrapper_mcp_calls") or 0)
                                        for r in records),
        "policy_wrapper_mcp_denied": sum((r.get("wrapper_mcp_denied") or 0)
                                         for r in records),
        "policy_wrapper_mcp_effective": sum((r.get("wrapper_mcp_effective") or 0)
                                            for r in records),
        "policy_wrapper_extra_task_calls": sum(
            (r.get("wrapper_extra_task_calls") or 0) for r in records),
        # B1 附带发现：包装模型把 scout 派成后台任务的次数（应恒为 0；不为 0 ⇒ 该组
        # 拿不到通知，只能 undiagnosed）。它不进门槛，只作诊断。
        "policy_wrapper_background_spawns": sum(
            (r.get("wrapper_background_spawns") or 0) for r in records),
        "policy_finding_sources": _source_distribution(records),
        "policy_groups_completed": sum(1 for r in records
                                       if r["status"] == "completed_verified"),
        "policy_groups_truncated": sum(1 for r in records
                                       if r.get("attempt_status") == "truncated"),
        "policy_groups_undiagnosed": sum(1 for r in records
                                         if r["status"] == "undiagnosed"),
        "policy_retries": sum(1 for r in records if r.get("retried")),
        "policy_findings_total": sum(len(r["findings"]) for r in records),
        "policy_findings_verified": sum(
            1 for r in records for f in r["findings"]
            if f.get("verification", {}).get("verdict") == "verified"),
        "policy_findings_refuted": sum(
            1 for r in records for f in r["findings"]
            if f.get("verification", {}).get("verdict") == "refuted"),
        "policy_index_chars": index_chars,
        "policy_index_max": DEPENDENCY_OUTPUT_MAX_CHARS,
        "policy_snapshot_head": snap_head,
        "policy_snapshot_path": str(snap_path),
        "policy_snapshot_removed": remove_note[:200],
        "policy_verify_python": verify_python_note or VERIFY_PYTHON,
        # R4：每组的终态（组状态 + 是否经过续派）压成一行，进 usage.jsonl 也能查。
        "policy_groups_final": ",".join(
            f"k{r['k']}={('completed' if r['status'] == 'completed_verified' else 'undiagnosed')}"
            f"{'(retried)' if r.get('retried') else ''}" for r in records),
        "policy_truncated_retry_succeeded": sum(
            1 for r in records if _truncated_retry_succeeded(r)),
        # R5：scout 侧用量/成本/耗时汇总（每个 scout 的明细在 policy_dispatch.jsonl 与
        # out/policy_scout_usage.jsonl）。拿不到的字段是 None —— 不写 0 冒充。
        **_scout_usage_totals(records),
    })
    return {"index_text": index_text, "index_chars": index_chars,
            "k": len(groups), "records": records, "metrics": metrics,
            "snapshot_path": str(snap_path)}


def _truncated_retry_succeeded(rec: dict) -> bool:
    """R4 的可见性判据：先被截断、续派之后拿到了 verified 结论。"""
    atts = rec.get("attempts") or []
    return bool(rec.get("retried") and len(atts) == 2
                and atts[0].get("status") == "truncated"
                and rec.get("status") == "completed_verified")


def _scout_usage_totals(records: list[dict]) -> dict:
    """把每个 scout 的 scout 侧用量汇总成几个标量（None 不参与求和）。

    ⚠️ 一条明细都没有时给 None 而不是 0 —— 「没拿到数据」和「花了 0 元」必须分得开
    （与 usage_log._f 的同一条纪律）。
    """
    keys = {"input_uncached": "policy_scout_input_uncached",
            "cache_read": "policy_scout_cache_read",
            "cache_creation": "policy_scout_cache_creation",
            "output_tokens": "policy_scout_output_tokens",
            "cost_usd": "policy_scout_cost_usd",
            "elapsed_s": "policy_scout_elapsed_s"}
    acc: dict[str, list] = {k: [] for k in keys}
    for r in records:
        for att in (r.get("attempts") or []):
            a = att.get("scout_accounting") or {}
            for k in keys:
                if isinstance(a.get(k), (int, float)):
                    acc[k].append(a[k])
    out = {keys[k]: (round(sum(v), 6) if v else None) for k, v in acc.items()}
    # policy_scout_sessions 的唯一定义在 run_policy_dispatch 的 B4 段（主口径写在门槛旁边）
    return out


def read_group_finals(out_dir: Path, label: str) -> list[dict]:
    """R4：从 **durable** 的 policy_dispatch.jsonl 里读回本轮的终态记录。

    为什么要读回而不是直接用内存里的 records：内存变量在进程结束时消失，
    「状态记录」这件事的意义就是它**不在内存里**。父层（coding_agent.py）拿到
    dispatch 结果之后再读一次盘，读到的就是下游真能看到的东西；读不到就如实
    标记（policy_durable_finals_read=0），不许悄悄回落到内存副本。

    同一个 label 可能被跑过多次（jsonl 是追加的）⇒ 按组号去重**取最后一条**。
    返回值按组号升序；一行都没有时返回 []。
    """
    path = out_dir / DISPATCH_LOG_NAME
    if not path.is_file():
        return []
    finals: dict[int, dict] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("label") != label:
                continue
            if row.get("group_status") not in ("completed", "undiagnosed"):
                continue
            k = row.get("k")
            if isinstance(k, int):
                finals[k] = row
    except OSError:
        return []
    return [finals[k] for k in sorted(finals)]


def _attempt_record(n: int, session: dict, retry_of: int | None) -> dict:
    """R4：一次尝试的账（进终态记录的 attempts 链）。

    B1/B4 追加：结论来源（summary_source/evidence_author）、包装模型自己动手的计数
    （wrapper_mcp_calls / wrapper_extra_task_calls）、以及这一次尝试里 Task tool_use
    的**真实条数**（task_tool_use_count）—— 旧实现把"每组最后一次尝试的 Task 数"
    当成派发门槛，含义含混（见 B4）。
    """
    return {
        "n": n,
        "retry_of": retry_of,
        "status": session.get("status"),
        "task_id": session.get("task_id"),
        "summary_chars": len(session.get("summary") or ""),
        "elapsed_s": session.get("elapsed_s"),
        "result_subtype": session.get("result_subtype"),
        "spawn_tool_use_ids": session.get("spawn_tool_use_ids") or [],
        "task_tool_use_count": len(session.get("spawn_tool_use_ids") or []),
        "harness_spawn_tool_use_id": session.get("harness_spawn_tool_use_id"),
        "wrapper_extra_task_calls": session.get("wrapper_extra_task_calls"),
        "wrapper_background_spawns": session.get("wrapper_background_spawns"),
        "wrapper_mcp_calls": session.get("wrapper_mcp_calls"),
        "wrapper_mcp_denied": session.get("wrapper_mcp_denied"),
        "wrapper_mcp_effective": session.get("wrapper_mcp_effective"),
        "ignored_notifications": session.get("ignored_notifications"),
        "scout_text_msgs": session.get("scout_text_msgs"),
        "summary_source": session.get("summary_source"),
        "evidence_author": session.get("evidence_author"),
        "scout_accounting": session.get("scout_accounting"),
        "error": session.get("error"),
    }


def _source_distribution(records: list[dict]) -> str:
    """B1-3：每条 finding 的来源分布压成一行（进 usage.jsonl / 报告都好读）。"""
    counts: dict[str, int] = {}
    for r in records:
        for src in (r.get("finding_sources") or {}).values():
            counts[str(src)] = counts.get(str(src), 0) + 1
    none_groups = sum(1 for r in records if not (r.get("finding_sources") or {}))
    parts = [f"{k}={v}" for k, v in sorted(counts.items())]
    parts.append(f"无结论的组={none_groups}")
    return ",".join(parts)
