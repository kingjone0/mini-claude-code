"""code_tools_server.py —— 面向代码仓库的最小工具集（独立进程 + stdio）。

与 local_tools_server.py 的分工：

    local_tools_server.py   通用小工具（文本统计 / 读工作区文件）  —— 验证用
    code_tools_server.py    代码修复闭环所需的最小工具集         —— 产品用

五个工具，覆盖「读文件 → 定位 → 改代码 → 跑测试」：

    list_files     定位：列目录
    search_code    定位：按正则搜内容
    read_file      读：读文件（可指定行区间，输出带行号）
    apply_patch    改：「唯一匹配」替换，old 必须在文件中只出现一次
    run_tests      跑测试：命令由服务端拼装，agent 只给受校验的参数

━━ 两条设计原则（见 DESIGN.md 第 4 节）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

① 沙箱在这一层，且只有一份实现。所有路径都过 resolve_in_root()，
   agent 拿不到任何「原始命令」入口 —— run_tests 不开 command 参数。

② 拒绝必须说明理由。返回拒绝原因而不是静默失败：agent 要知道「为什么被拒」
   才能换路，这本身就是「失败自省」的一部分。

⚠️ stdout 是协议线，本文件所有日志走 stderr + 文件（仓库 CLAUDE.md 陷阱 8 / 10）。
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

# ── 沙箱根 ────────────────────────────────────────────────────────────────
# 默认指向本仓库内的靶场；AGENT_REPO_ROOT 可覆盖（第三阶段换成 SWE-bench 仓库时用）。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = Path(
    os.environ.get("AGENT_REPO_ROOT") or (PROJECT_ROOT / "fixtures" / "mini_repo")
).resolve()

# ── 限额 ──────────────────────────────────────────────────────────────────
# 这不只是"防呆"——它同时是「上下文管理」层的第一步：
# 工具输出会原样进模型上下文，不设上限等于把上下文预算交给 agent 决定。
MAX_READ_CHARS = 20000
MAX_SEARCH_HITS = 50
MAX_LIST_ENTRIES = 200
MAX_TOOL_OUTPUT = 12000
TEST_TIMEOUT_S = 120

# ── repo_map 默认详细度开关（A/B 实验的唯一变量）───────────────────────────
# 为什么需要它：`detail` 的默认值原先硬编码在函数签名里，外部**无法在不改代码的
# 情况下切换**。而"默认输出换一版"恰恰是要做单变量消融的那个变量 —— 改签名等于
# 每次实验都要编辑源码，实验条件就说不清了（跑的是哪个版本的代码？）。
#
#   未设置          -> "summary"（新行为：每文件一行，全仓 359 字符）
#   "symbols"       -> 展开符号（旧行为：more-itertools 上 46,776 字符，会被截断）
#   其它任何值      -> **明确拒绝这一次调用**，不静默回落到 summary
#
# ⚠️ 三个设计取舍，都是为了让"唯一变量"真的只有一个：
#   ① 读的是**模块级常量**（进程启动时读一次），不是每次调用现读。
#      这样同一次 server 生命周期内取值恒定 —— 不会出现"前半程 summary、后半程
#      symbols"这种无法归因的中间态。
#   ② 非法值**不回落**。静默回落会让"变量设错了"伪装成"这一臂没效果"，
#      正是本项目栽过两次的那类失效（见 CLAUDE.md 陷阱 14 的"消源"思路）。
#   ③ 它**只影响默认值**。显式传 detail 时行为与从前逐字一致 —— 否则实验里
#      agent 一旦显式传参，两臂就没差别了，单变量设计会被悄悄破坏。
REPO_MAP_DETAILS = ("summary", "symbols")
REPO_MAP_DEFAULT_ENV = "AGENT_REPO_MAP_DEFAULT"
_REPO_MAP_DEFAULT_RAW = os.environ.get(REPO_MAP_DEFAULT_ENV)
REPO_MAP_DEFAULT = _REPO_MAP_DEFAULT_RAW or "summary"

# 忽略的目录：不参与定位，也不出现在列表里。
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}

# ── 日志：文件 + stderr 双通道 ────────────────────────────────────────────
# 位置沿用既有的证据目录；基准取 __file__ 而非 cwd（cwd 由拉起 server 的客户端决定）。
LOG_PATH = Path(
    os.environ.get("AGENT_TOOLS_LOG")
    or (PROJECT_ROOT / "servers" / "experiments" / "out" / "code-tools-server.log")
)

LOGGER = logging.getLogger("code-tools")
LOGGER.setLevel(logging.DEBUG)
# 关键：不向 root 传播 —— root 上可能挂着别人装的 handler（甚至指向 stdout），
# 传播过去等于把协议线的处置权交出去。
LOGGER.propagate = False


def _install_handlers() -> None:
    """挂两条互相独立的通道：文件 + stderr。任一条被掐掉，另一条仍在。"""
    LOGGER.handlers.clear()

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s pid=%(process)d %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        LOGGER.addHandler(fh)
    except OSError as exc:
        # 文件日志是附加保障，不能因为它打不开就让 server 起不来；
        # 但也不能静默 —— 否则就成了「以为有日志、其实没有」。
        print(
            f"[code-tools] 文件日志不可用（{LOG_PATH}）：{exc}；本次只有 stderr",
            file=sys.stderr,
            flush=True,
        )

    err = logging.StreamHandler(sys.stderr)  # 显式传 stderr，防止将来被误改成 stdout
    err.setFormatter(logging.Formatter("[code-tools] %(message)s"))
    LOGGER.addHandler(err)


_install_handlers()


def log(msg: str, *args: Any) -> None:
    LOGGER.info(msg, *args)


# ── 沙箱：唯一的路径守卫 ──────────────────────────────────────────────────
class SandboxError(RuntimeError):
    """路径或参数越出沙箱边界。"""


def resolve_in_root(relpath: str) -> Path:
    """把相对路径解析到沙箱内的**真实落点**；越界抛 SandboxError。

    ⚠️ 必须在「拼接 + normalize」**之后**比较真实落点。
    不能只看 relpath 长什么样 —— `Path("C:/x")` 会让 `/` 运算符直接丢弃 root，
    `a/../../b` 也只有 resolve 之后才知道去了哪。
    这条教训来自 local_tools_server.py 的 read_study_file（那里踩过一次）。
    """
    root = REPO_ROOT
    target = (root / relpath).resolve()
    if target != root and not target.is_relative_to(root):
        raise SandboxError(f"路径越出仓库根：{relpath!r} → {target}（只允许 {root} 之内）")
    return target


def _relative(p: Path) -> str:
    """统一用 POSIX 风格输出相对路径 —— 跨平台可读，也便于 agent 直接回填。"""
    return p.relative_to(REPO_ROOT).as_posix()


def _skippable(p: Path) -> bool:
    return any(part in SKIP_DIRS for part in p.parts)


def _clip(text: str, limit: int, note: str = "…（已截断）") -> str:
    return text if len(text) <= limit else text[:limit] + "\n" + note


# ── 「工具描述」实验的两个开关 ────────────────────────────────────────────
#
# 背景：A/B 实测两臂采用率都是 1/6 —— 成本降了 130 倍，采用率没动。AB-REPO-MAP.md
# 提的下一步假设是：**模型根本没"想到"这个工具**。两处文本证据都指向同一件事：
#   ① 服务端 instructions 写的"典型用法"是 search_code -> read_file -> apply_patch
#      -> run_tests，**repo_map 完全不在这个流程里**；
#   ② repo_map 的描述是一段"这个工具是什么 + 我们的设计教训"，**没有一句"什么时候用它"**。
#
# 所以把两个文本通道各做成一个开关，可以单独或一起启用 v2：
#   AGENT_SERVER_INSTRUCTIONS=v2  -> instructions 的"典型用法"里加进 repo_map
#   AGENT_REPO_MAP_DESC=v2        -> repo_map 的工具描述改成"什么时候该调用我"
#
# ⚠️ 与 AGENT_REPO_MAP_DEFAULT 同一条纪律：模块级只读一次、生效取值必须能查到、
#    非法值**不静默回落**——回落到 v1 会让"这一臂到底是哪一臂"变成回忆。
INSTRUCTIONS_ENV = "AGENT_SERVER_INSTRUCTIONS"
REPO_MAP_DESC_ENV = "AGENT_REPO_MAP_DESC"
VARIANTS = ("v1", "v2")

INSTRUCTIONS_V1 = (
    f"代码仓库工具集。所有路径都相对于仓库根 {REPO_ROOT}，"
    "越出该目录的访问一律被拒绝。典型用法：先 search_code 定位，"
    "再 read_file 看清上下文，然后 apply_patch 修改，最后 run_tests 验证。"
)

INSTRUCTIONS_V2 = (
    f"代码仓库工具集。所有路径都相对于仓库根 {REPO_ROOT}，"
    "越出该目录的访问一律被拒绝。\n"
    "\n"
    "面对一个不熟悉的仓库，推荐的工作顺序：\n"
    "1. repo_map —— 先看全局（默认每个文件一行，通常只有几百字符）。"
    "不知道目标在哪个文件时，这一步比逐个 read_file 便宜得多。\n"
    "2. search_code —— 精确定位到行。\n"
    "3. read_file —— 看清上下文。\n"
    "4. apply_patch —— 修改。\n"
    "5. run_tests —— 验证。"
)


def _variant(env_name: str) -> str:
    raw = os.environ.get(env_name)
    value = raw or "v1"
    if value not in VARIANTS:
        raise SystemExit(f"{env_name} 只能是 {VARIANTS} 之一，收到 {raw!r}")
    return value


INSTRUCTIONS_VARIANT = _variant(INSTRUCTIONS_ENV)
REPO_MAP_DESC_VARIANT = _variant(REPO_MAP_DESC_ENV)

# ── MCP server ────────────────────────────────────────────────────────────
server = MCPServer(
    name="code-tools",
    version="1.0.0",
    instructions=INSTRUCTIONS_V2 if INSTRUCTIONS_VARIANT == "v2" else INSTRUCTIONS_V1,
)


@server.tool()
def list_files(subdir: str = ".", pattern: str = "*") -> str:
    """列出仓库内某目录下的条目（相对仓库根的路径）。

    subdir: 相对仓库根的目录，默认 "."（仓库根）
    pattern: 通配符，默认 "*"；例如 "*.py"、"test_*.py"
    """
    log("handler 调用：list_files(subdir=%r, pattern=%r)", subdir, pattern)
    try:
        target = resolve_in_root(subdir)
    except SandboxError as exc:
        log("list_files 拒绝：%s", exc)
        return f"拒绝：{exc}"

    if not target.exists():
        return f"不存在：{subdir}"
    if not target.is_dir():
        return f"不是目录：{subdir}"

    entries: list[str] = []
    truncated = False
    for p in sorted(target.rglob(pattern)):
        if _skippable(p):
            continue
        if len(entries) >= MAX_LIST_ENTRIES:
            truncated = True
            break
        try:
            rel = _relative(p)
        except ValueError:
            continue
        entries.append(rel + "/" if p.is_dir() else rel)

    if not entries:
        return f"{subdir} 下没有匹配 {pattern!r} 的条目"
    body = "\n".join(entries)
    if truncated:
        body += f"\n…（已达 {MAX_LIST_ENTRIES} 条上限，结果被截断）"
    return body


@server.tool()
def search_code(regex: str, glob: str = "*.py", subdir: str = ".") -> str:
    """按正则搜索仓库内文件内容，返回 `相对路径:行号: 内容`。

    regex: Python 正则
    glob:  只搜匹配该通配符的文件，默认 "*.py"
    subdir: 搜索起点，默认仓库根
    """
    log("handler 调用：search_code(regex=%r, glob=%r, subdir=%r)", regex, glob, subdir)
    try:
        pat = re.compile(regex)
    except re.error as exc:
        # 正则编译失败要**如实报错**，不能崩掉整个 server
        return f"正则编译失败：{exc}"

    try:
        base = resolve_in_root(subdir)
    except SandboxError as exc:
        log("search_code 拒绝：%s", exc)
        return f"拒绝：{exc}"

    if not base.exists():
        return f"不存在：{subdir}"

    hits: list[str] = []
    for p in sorted(base.rglob(glob)):
        if not p.is_file() or _skippable(p):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                hits.append(f"{_relative(p)}:{lineno}: {line.strip()[:200]}")
                if len(hits) >= MAX_SEARCH_HITS:
                    hits.append(f"…（已达 {MAX_SEARCH_HITS} 条上限，结果被截断）")
                    return "\n".join(hits)

    return "\n".join(hits) if hits else f"没有匹配 {regex!r} 的行"


@server.tool()
def read_file(path: str, start: int = 0, end: int = 0) -> str:
    """读仓库内的文本文件，输出带行号（便于回填给 apply_patch）。

    path:  相对仓库根的路径
    start: 起始行（1 起算；0 表示从头）
    end:   结束行（含；0 表示到末尾）
    """
    log("handler 调用：read_file(path=%r, start=%r, end=%r)", path, start, end)
    try:
        target = resolve_in_root(path)
    except SandboxError as exc:
        log("read_file 拒绝：%s", exc)
        return f"拒绝：{exc}"

    if not target.is_file():
        return f"不存在或不是文件：{path}"

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"读取失败：{exc}"

    lines = text.splitlines()
    if not lines:
        return f"{path} 是空文件"

    s = max(1, start) if start else 1
    e = min(len(lines), end) if end else len(lines)
    if s > len(lines):
        return f"起始行 {s} 超出文件总行数 {len(lines)}"
    if e < s:
        return f"结束行 {e} 小于起始行 {s}"

    body = "\n".join(f"{i:4d}| {lines[i - 1]}" for i in range(s, e + 1))
    header = f"# {path}（共 {len(lines)} 行，显示 {s}-{e}）\n"
    return header + _clip(body, MAX_READ_CHARS)


@server.tool()
def apply_patch(path: str, old: str, new: str) -> str:
    """把文件里的 old 精确替换成 new。

    ⚠️ old 必须在文件中**唯一出现**，否则拒绝 —— 歧义匹配会改错地方，
       而"改错地方"比"没改"更难排查。需要时请给出更长的上下文片段。
    """
    # 只记长度，不记内容 —— 代码正文不进日志
    log("handler 调用：apply_patch(path=%r, old=%d 字符, new=%d 字符)", path, len(old), len(new))

    try:
        target = resolve_in_root(path)
    except SandboxError as exc:
        log("apply_patch 拒绝：%s", exc)
        return f"拒绝：{exc}"

    if not target.is_file():
        return f"不存在或不是文件：{path}"
    if not old:
        return "拒绝：old 为空字符串（会把 new 插到每个位置之间）"

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return f"读取失败：{exc}"

    occurrences = text.count(old)
    if occurrences == 0:
        return f"未找到要替换的片段：old 在 {path} 中不存在（请先 read_file 确认原文）"
    if occurrences > 1:
        return (
            f"拒绝：old 在 {path} 中出现 {occurrences} 次，无法确定改哪一处。"
            "请给出更长、能唯一确定的片段。"
        )

    try:
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
    except OSError as exc:
        return f"写入失败：{exc}"

    log("handler 成功：apply_patch 已修改 %s", path)
    return f"已修改 {path}（唯一匹配，替换 1 处）"


# ── P2：scout findings 的按需读取（read_findings）─────────────────────────
# 背景：④ 层 Round 1/2 实测，子代理报告 6.8–8.1 万字符**全文**进父层上下文 ——
# 派发买到的是「更贵的自己做」。P2 把全文留在 out/scout-<label>-<k>.json，
# 父层上下文里只有一份 ≤2000 字符的索引；细节由父 agent **按 id 单条**取回。
# ⚠️ 必须按 id 取：一次把全部 findings 倒回去等于把成本从「通知」搬到「读取」，
#    有界报告就白做了。所以这里没有 list_all 形态的参数。
#
# ★ R2（第二轮复审 must-fix）：id 的作用域必须与**目录**的作用域一致。
#   旧实现：driver 把 id 写成 `g<k>-f<n>`（只在单份产物内唯一），而本工具对整个
#   共享 out/ 目录 glob —— 跑批从不清空 out/，于是多轮产物并存时**每个 id 都重名**，
#   第一轮跑批 12 次调用 0 次成功（服务端日志里 g1-f1 出现在 5 份产物里，
#   见 servers/experiments/v2_readfindings_scope_check.py 的对照输出）。
#   现在 id 自带运行 label（driver 侧 agent/policy_dispatch.finding_id 写：
#   `<label>-g<k>-f<n>`），于是：
#     ① 带 label 的 id 直接锁定**唯一一份**产物 scout-<label>-<k>.json（O(1)，不猜）；
#     ② 旧形状 id（g<k>-f<n>）仍能用，但一旦重名就**明确报错**，并把可用的
#        带 label 形状**列出来**给调用方（冲突可解，不是死路）；
#     ③ 旧产物不动（它们是真跑批的证据），因此旧 id 仍能被带 label 形状寻址。
FINDINGS_DIR_ENV = "AGENT_FINDINGS_DIR"
MAX_FINDING_CHARS = 4000   # 单条 finding 序列化后的硬上限（防御性截断）

# 带 label 的 id 形状：`<label>-g<k>-f<n>`。label 用贪婪匹配，取**最后**一个
# `-g<digits>-f<digits>` 尾巴（label 本身可能含 `-`，比如 subagent-ab-v2-D-1）。
QUALIFIED_ID_PAT = re.compile(r"^(?P<label>.+)-g(?P<k>\d+)-f(?P<n>\d+)$")

# 默认目录 = driver（coding_agent.py）落 scout-*.json 的位置。
# 与 AGENT_REPO_ROOT 同理显式传，不靠 cwd 猜（cwd 由拉起 server 的一方决定）。
FINDINGS_DIR = Path(
    os.environ.get(FINDINGS_DIR_ENV)
    or (PROJECT_ROOT / "servers" / "experiments" / "out")
).resolve()


def _qualified_id_of(scout_file_name: str, plain_id: str) -> str:
    """`scout-<label>-<k>.json` + `g<k>-f<n>` → `<label>-g<k>-f<n>`。

    只用于**提示**（把可用的形状列给调用方）—— 产物文件名本身就带着 label，
    所以旧产物也能被带 label 的 id 寻址，一行都不用改。
    id 已经是带 label 形状时原样返回（不重复拼）。
    """
    if QUALIFIED_ID_PAT.match(plain_id):
        return plain_id
    stem = scout_file_name[:-5] if scout_file_name.endswith(".json") else scout_file_name
    if not stem.startswith("scout-"):
        return plain_id
    label, _, k = stem[len("scout-"):].rpartition("-")
    return f"{label}-{plain_id}" if label and k.isdigit() else plain_id


def _load_scout_findings_file(path: Path) -> list[dict]:
    """读一份 scout 产物，返回其 findings 列表（形状不对就当没有）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("findings") if isinstance(data, dict) else None
    return [f for f in (items or []) if isinstance(f, dict)]


@server.tool()
def read_findings(id: str) -> str:
    """按 id 读取**一条**侦察结论（driver 派发的 scout findings）。

    id: scout 报告的 finding 编号。**带上运行 label 才能全局唯一**，形如
        `subagent-ab-v2-D-1-g1-f1`（运行 label - 组号 - 条目号）；
        旧形状 `g1-f1` 也接受，但它只在单份产物内唯一 —— 一旦重名会明确报错，
        并把可用的带 label 形状列出来。
        一次只取一条 —— 这个工具的存在意义就是**不让**全部结论一起进上下文。

    返回该条的 file/line/symptom/rootCause/minimalFix/evidence/predictedFailureCount。
    minimalFix 里的 old/new 就是建议的最小改法（改动仍由你自己用 apply_patch 执行）。
    """
    log("handler 调用：read_findings(id=%r)", id)
    ident = (id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", ident):
        return f"拒绝：id 只允许字母数字与 _ -，收到 {id!r}"
    try:
        files = sorted(FINDINGS_DIR.glob("scout-*.json"))
    except OSError as exc:
        return f"无法列出 findings 目录 {FINDINGS_DIR}：{exc}"
    q = QUALIFIED_ID_PAT.match(ident)
    hits: list[dict] = []
    if q:
        # ① 带 label 的 id：直接算它该在哪份产物里（`scout-<label>-<k>.json`），
        #    不依赖目录里有多少份别的产物 —— 这就是「作用域一致」。
        label, k, n = q.group("label"), int(q.group("k")), int(q.group("n"))
        exact = FINDINGS_DIR / f"scout-{label}-{k}.json"
        if exact.is_file():
            for f in _load_scout_findings_file(exact):
                # 新产物里 id 就是带 label 的形状；旧产物里是 g<k>-f<n> 旧形状
                # （产物文件是历史证据，不改）—— 两种都认，且都只落在这一份文件里。
                if str(f.get("id")) in (ident, f"g{k}-f{n}"):
                    hits.append({**f, "_source": exact.name})
        if not hits:
            # 兜底：万一有人把产物改名/搬过目录，仍按精确 id 全局找一次。
            for p in files:
                if p.name == exact.name:
                    continue
                for f in _load_scout_findings_file(p):
                    if str(f.get("id")) == ident:
                        hits.append({**f, "_source": p.name})
    else:
        for p in files:
            for f in _load_scout_findings_file(p):
                if str(f.get("id")) == ident:
                    hits.append({**f, "_source": p.name})
    if not hits:
        hint = ""
        if not q and files:
            hint = (" 该 id 是旧形状（`g<组号>-f<条目号>`），只在单份产物内唯一 —— "
                    "请改用带运行 label 的 id：产物 `scout-<label>-<k>.json` 里的条目 "
                    f"就是 `<label>-{ident}`。")
        return (f"未找到 id={ident!r} 的 finding（{FINDINGS_DIR.name} 下有 "
                f"{len(files)} 份 scout 产物）。父层索引里给出的 id 才能取到。{hint}")
    if len(hits) > 1:
        labels = ", ".join(h["_source"] for h in hits)
        usable = ", ".join(dict.fromkeys(
            _qualified_id_of(h["_source"], str(h.get("id"))) for h in hits))
        return (f"id={ident!r} 在多份产物里重名（{labels}），无法确定取哪一条。"
                f"请改用带运行 label 的 id（每个都唯一）：{usable}")
    body = json.dumps(hits[0], ensure_ascii=False, indent=1)
    log("read_findings 成功：id=%s → %d 字符（来源 %s）",
        ident, len(body), hits[0].get("_source"))
    return _clip(body, MAX_FINDING_CHARS, "…（该条过长，已截断）")


# keyword 表达式的字符白名单：字母数字、下划线、连字符、点、空格。
# 目的不是"过滤危险字符"，而是**根本不提供拼接 shell 命令的入口**。
ALLOWED_KEYWORD_PAT = re.compile(r"^[A-Za-z0-9_\-\. ]{0,80}$")


# ── 符号索引（给 repo_map 用）─────────────────────────────────────────────
def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """把函数的参数表拼成紧凑的一行（不含默认值 —— 索引要便宜）。"""
    a = node.args
    names = [x.arg for x in (*a.posonlyargs, *a.args)]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        names.append("*")
    names += [x.arg for x in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    ret = ""
    if node.returns is not None:
        with suppress(Exception):  # 某些注解 unparse 会失败，失败就不显示返回类型
            ret = " -> " + ast.unparse(node.returns)
    return "(" + ", ".join(names) + ")" + ret


def _symbols(source: str) -> list[tuple[int, str]]:
    """用 ast 解析出 (行号, 描述) 列表。类 / 函数 / 模块级常量。

    用 ast 而不是正则：正则分不清 `def f():` 出现在字符串里还是代码里，
    而这些靶场故意写了注释和 docstring 提到函数名。
    """
    tree = ast.parse(source)
    out: list[tuple[int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out.append((node.lineno, f"class {node.name}"))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((sub.lineno, f"  def {sub.name}{_signature(sub)}"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.lineno, f"def {node.name}{_signature(node)}"))
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                # 只列模块级**常量**（全大写）—— 小写变量是内部细节，列出来是噪音
                if isinstance(tgt, ast.Name) and tgt.id.isupper():
                    out.append((node.lineno, tgt.id))
    return out


# ⚠️ repo_map 刻意**不用** @server.tool() 装饰器：它的描述是本实验的一个变量，
#    必须在注册时按 AGENT_REPO_MAP_DESC 现选（v1 = 下面这个 docstring 原文，
#    v2 = 文件末尾的 REPO_MAP_DESC_V2）。注册统一在文件末尾做。
def repo_map(subdir: str = ".", pattern: str = "*.py", detail: str = "") -> str:
    """仓库结构：文件清单 + 行数 + 符号数；可选展开符号与行号。

    detail —— 省略时取环境变量 `AGENT_REPO_MAP_DEFAULT` 的值（未设置等价于 "summary"）。
        ⚠️ **不要写默认值到参数上**：写成 `detail="summary"` 会让环境变量永远被
        Python 的默认值遮住，开关就失效了。空串表示"没传"，由这里现补默认值。

        "summary" —— 每个文件**一行**。很便宜，用来"先看全局再决定读哪里"。

        "symbols" —— 给出每个文件里的类 / 函数 / 常量 + 行号。
            ⚠️ **大仓库上会很长**：more-itertools 靶场全量要 **46,776 字符**，
            超过 `read_file` 的 20000 上限、会被截断（其中 76% 来自测试文件 ——
            测试文件的符号数比源码还多）。**要展开符号就配 subdir 缩小范围**，
            例如 `subdir="more_itertools"` 只要 ~11k 字符。

    ⚠️ 设计教训（实测得来）：本工具最初只有 `symbols` 一种输出，
        结果在真实仓库上**没人用** —— 一次要 4.7 万字符，
        而 `search_code` 查一次平均只要 **171 字符**，贵 274 倍。
        默认改成摘要后才是"便宜的全局视图"。
    """
    # 空串 = 调用方没传 detail -> 用环境变量决定的默认值。
    #
    # ⚠️ 两个判断的**顺序不能换**，而且非法值只在"真的要靠默认值"时才拦：
    #    显式传了 detail 的调用**完全不受这个环境变量影响** —— 这是本开关作为
    #    "唯一变量"的前提。第一版把非法值检查放在最前面、无条件执行，结果是
    #    「环境变量设错 -> 连显式传 detail 的正常调用也被拒」，
    #    等于把开关的影响面从"默认值"扩大到了整个工具（已实测复现并修正）。
    using_default = not detail
    if using_default:
        detail = REPO_MAP_DEFAULT

    log("handler 调用：repo_map(subdir=%r, pattern=%r, detail=%r) [%s默认=%r，生效=%r]",
        subdir, pattern, detail,
        "用" if using_default else "未用", _REPO_MAP_DEFAULT_RAW, detail)

    # 非法的**环境变量**要明确报错，不能静默回落 —— 否则"变量设错了"会伪装成
    # "这一臂没效果"，正是本实验最怕的失效模式。
    if using_default and _REPO_MAP_DEFAULT_RAW and REPO_MAP_DEFAULT not in REPO_MAP_DETAILS:
        return (
            f"拒绝：环境变量 {REPO_MAP_DEFAULT_ENV}={_REPO_MAP_DEFAULT_RAW!r} 不是合法值。"
            f"只支持 {REPO_MAP_DETAILS} 之一（不设置 = 'summary'）。"
            "这里**不做静默回落** —— 取值不明时按配置错误处理。"
            "（显式传 detail 的调用不受影响。）"
        )
    if detail not in REPO_MAP_DETAILS:
        return f"拒绝：detail 只支持 'summary' 或 'symbols'，收到 {detail!r}"

    try:
        base = resolve_in_root(subdir)
    except SandboxError as exc:
        return f"拒绝：{exc}"
    if not base.exists():
        return f"不存在：{subdir}"

    files = [p for p in sorted(base.rglob(pattern))
             if p.is_file() and not _skippable(p)]
    truncated_files = len(files) > MAX_LIST_ENTRIES
    files = files[:MAX_LIST_ENTRIES]

    # 先解析一遍，两种 detail 共用
    parsed: list[tuple[Path, int, list[tuple[int, str]], str]] = []
    for p in files:
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            parsed.append((p, 0, [], "读取失败"))
            continue
        try:
            parsed.append((p, src.count("\n") + 1, _symbols(src), ""))
        except SyntaxError as exc:
            parsed.append((p, src.count("\n") + 1, [], f"语法错误：第 {exc.lineno} 行 {exc.msg}"))

    if detail == "summary":
        width = max((len(_relative(p)) for p, *_ in parsed), default=20)
        lines = [f"# {len(parsed)} 个 {pattern} 文件（subdir={subdir}）"]
        for p, n_lines, syms, err in parsed:
            note = f"  <{err}>" if err else ""
            lines.append(f"  {_relative(p):<{width}}  {n_lines:>6} 行  {len(syms):>4} 符号{note}")
        body = "\n".join(lines)
        log("handler 成功：repo_map(summary) → %d 字符", len(body))
        return body  # 摘要天然很小，不做截断

    lines: list[str] = [f"# 符号索引：{len(parsed)} 个 {pattern} 文件（subdir={subdir}）"]
    for p, n_lines, syms, err in parsed:
        lines.append(f"\n{_relative(p)}  ({n_lines} 行)")
        if err:
            lines.append(f"  <{err}>")
            continue
        for lineno, desc in syms:
            lines.append(f"  {lineno:>4}  {desc}")

    if truncated_files:
        lines.append(f"\n…（已达 {MAX_LIST_ENTRIES} 个文件上限）")

    body = "\n".join(lines)
    omitted = max(0, len(body) - MAX_READ_CHARS)
    hint = (
        f"…（已截断，省略约 {omitted} 字符。"
        f"要拿到完整符号表，请用 subdir 缩小范围，例如 subdir=\"<包目录>\"）"
    )
    out = _clip(body, MAX_READ_CHARS, hint)
    log("handler 成功：repo_map(symbols) → %d 字符（%s）",
        len(out), "截断" if omitted else "完整")
    return out


@server.tool()
def run_tests(test_path: str = "", keyword: str = "", detail: str = "brief",
              python_exe: str = "", cwd_rel: str = "") -> str:
    """在仓库根运行 pytest，返回真实退出码与输出。

    test_path: 可选的测试文件/目录（相对仓库根，必须在仓库内）
    keyword:   可选的 pytest -k 表达式（只允许字母数字与 _ - . 空格）
    detail:    "brief"（默认，每个失败只给一行）或 "full"（完整堆栈）
    python_exe: 可选解释器绝对路径（P5 独立校验由 driver 注入；缺省用本进程的
                sys.executable —— 不传时与改动前逐字同形）
    cwd_rel:   可选，在沙箱根之内的哪个子目录跑（P5 校验在 worktree 副本里；
                父 agent 的正常调用不传 = 仓库根，行为不变）

    ⚠️ 本工具**不接收命令字符串** —— 命令由服务端拼装，agent 只给受校验的参数。
       这是「权限与沙箱」层的核心设计：不给入口，比过滤参数可靠。
       python_exe / cwd_rel 两个新参数同样只是**受校验的参数**：python_exe 必须是
       存在的可执行文件，cwd_rel 必须解析到沙箱根之内（resolve_in_root 拦越界）。
    """
    log("handler 调用：run_tests(test_path=%r, keyword=%r, detail=%r)",
        test_path, keyword, detail)

    if detail not in ("brief", "full"):
        return f"拒绝：detail 只支持 'brief' 或 'full'，收到 {detail!r}"

    # brief 用 pytest 的 `--tb=line`：每个失败**一行**。
    #
    # 为什么默认 brief：实测（context_report.py，4 次取样）run_tests 的输出
    # 占全部工具返回字符的 **29%**，平均 3378 字符/次 —— 而它返回的是
    # 13 个失败的**完整堆栈**。agent 多数时候只需要「哪几个失败了 + 断言行」。
    # 需要完整堆栈时显式传 detail="full"，把成本变成**主动选择**而不是默认。
    tb = "--tb=line" if detail == "brief" else "--tb=short"

    exe = (python_exe or "").strip() or sys.executable
    if python_exe and not Path(exe).is_file():
        return f"拒绝：python_exe 不存在：{python_exe!r}"

    cmd: list[str] = [exe, "-m", "pytest", "-q", "--no-header",
                      tb, "-p", "no:cacheprovider"]

    run_cwd = str(REPO_ROOT)
    if cwd_rel:
        try:
            t = resolve_in_root(cwd_rel)
        except SandboxError as exc:
            log("run_tests 拒绝（cwd_rel）：%s", exc)
            return f"拒绝：{exc}"
        if not t.is_dir():
            return f"cwd_rel 不是目录：{cwd_rel}"
        run_cwd = str(t)

    if test_path:
        try:
            t = resolve_in_root(test_path)
        except SandboxError as exc:
            log("run_tests 拒绝（test_path）：%s", exc)
            return f"拒绝：{exc}"
        if not t.exists():
            return f"测试路径不存在：{test_path}"
        cmd.append(_relative(t))

    if keyword:
        if not ALLOWED_KEYWORD_PAT.match(keyword):
            log("run_tests 拒绝（keyword）：%r", keyword)
            return (
                f"拒绝：keyword 含不允许的字符：{keyword!r}。"
                "只允许字母、数字、下划线、连字符、点与空格。"
            )
        cmd += ["-k", keyword]

    log("run_tests 执行：cwd=%s cmd=%s", run_cwd, " ".join(cmd[-3:]))

    try:
        proc = subprocess.run(
            cmd,
            cwd=run_cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        log("run_tests 超时（>%ds）", TEST_TIMEOUT_S)
        return f"测试超时（>{TEST_TIMEOUT_S}s），已中止"
    except OSError as exc:
        return f"无法启动测试进程：{exc}"

    output = (proc.stdout or "") + (proc.stderr or "")
    output = _clip(output.strip(), MAX_TOOL_OUTPUT, "…（输出已截断）")

    log("run_tests 完成：退出码=%d", proc.returncode)
    return f"退出码 = {proc.returncode}\n{'─' * 40}\n{output}"


# ── repo_map 的两种描述，由 AGENT_REPO_MAP_DESC 选一个 ────────────────────
#
# v1 = 上面 repo_map 的 docstring 原文（"这个工具是什么 + 我们的设计教训"）。
#      直接取 __doc__，这样"v1 就是改动前那一份"是结构上成立的，
#      而不是靠两份手抄文本互相对齐。
# v2 = 写着给**模型**看的："什么时候该调用我"。同时删掉了 v1 里那段设计教训 ——
#      那段话是写给人看的，对模型是噪声（"描述是给谁看的"也是这个假设的一部分）。
REPO_MAP_DESC_V2 = (
    "仓库结构总览：文件清单 + 行数 + 符号数。用来在一个不熟悉的仓库里先看全局。\n"
    "\n"
    "**什么时候调用我**：刚接手一个仓库、或者不知道某个函数/类在哪个文件里时，"
    "先调用我一次。默认输出（summary）每个文件一行，通常只有几百字符 —— "
    "比 list_files 信息量更大（带行数和符号数），比 search_code 更全局"
    "（不用先猜关键词），比逐个 read_file 便宜几个数量级。\n"
    "\n"
    "detail —— 省略时取环境变量 `AGENT_REPO_MAP_DEFAULT` 的值"
    "（未设置等价于 \"summary\"）。空串表示\"没传\"。\n"
    "    \"summary\" —— 每个文件一行。便宜，用来\"先看全局、再决定读哪里\"。\n"
    "    \"symbols\" —— 每个文件里的类 / 函数 / 常量 + 行号。\n"
    "        ⚠️ 大仓库上会很长：more-itertools 靶场全量 46,776 字符，"
    "超过 read_file 的 20000 上限、会被截断。要展开符号就配 subdir 缩小范围，"
    "例如 subdir=\"more_itertools\" 只要 ~11k 字符。"
)

server.add_tool(
    repo_map,
    name="repo_map",
    description=REPO_MAP_DESC_V2 if REPO_MAP_DESC_VARIANT == "v2" else (repo_map.__doc__ or ""),
)


if __name__ == "__main__":
    log("启动：transport=stdio，REPO_ROOT=%s", REPO_ROOT)
    # ⚠️ 这一行是给 A/B 实验看的：**实际生效的取值**必须能在产物/日志里查到，
    #    否则"这一臂到底是哪一臂"只能靠回忆。（陷阱 14 的同一条纪律。）
    log("repo_map 默认详细度：%s=%r -> 生效 %r",
        REPO_MAP_DEFAULT_ENV, _REPO_MAP_DEFAULT_RAW, REPO_MAP_DEFAULT)
    log("文本通道：%s=%r，%s=%r -> instructions %d 字符 / repo_map 描述 %d 字符",
        INSTRUCTIONS_ENV, INSTRUCTIONS_VARIANT, REPO_MAP_DESC_ENV, REPO_MAP_DESC_VARIANT,
        len(server.instructions or ""), len(repo_map.__doc__ or "")
        if REPO_MAP_DESC_VARIANT == "v1" else len(REPO_MAP_DESC_V2))
    log("cwd=%s", Path.cwd())
    log("python=%s", sys.executable)
    log("日志文件：%s", LOG_PATH)
    server.run()  # 默认 transport="stdio"
