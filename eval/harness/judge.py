"""判定逻辑（预注册 §5）。

**这个模块是「谁说了算」的唯一答案**：agent 自述、trace 里的 echo、人眼判断
对 verdict 都**零影响**（§5.2）。唯一证据是 harness 自己启动的 pytest 子进程
给出的逐 node 结果 + 退出码 + 超时标记。

三个必须写对的顺序/口径问题，都是踩过或预注册点名过的：

1. **超时先于退出码（§8.4）**：Windows 上被强杀的进程以 exit 1 收尾，与 pytest
   「有测试失败」的 exit 1 不可区分 => 必须先查 `timed_out` 标记再解释退出码，
   否则 timeout 会被误记成「agent 改错了」（not_resolved）。
2. **测试侧必须先剥离（§5.3）**：候选补丁动了测试文件 => 剥掉测试侧再判定，
   防止「改测试骗绿」；剥离后为空 => empty_patch。剥离掉的改动要单列统计。
3. **六分类互斥（§5.4）**：agent_result / empty_patch / patch_conflict /
   infra_error / timeout / harness_bug，各自计入分母与否见 §5.4 表。
"""

from __future__ import annotations

import difflib
import json
import os
import re

from .env import git, write_text

# §5.4 六分类
AGENT_RESULT = "agent_result"
EMPTY_PATCH = "empty_patch"
PATCH_CONFLICT = "patch_conflict"
INFRA_ERROR = "infra_error"
TIMEOUT = "timeout"
HARNESS_BUG = "harness_bug"

ALL_CLASSES = (AGENT_RESULT, EMPTY_PATCH, PATCH_CONFLICT, INFRA_ERROR, TIMEOUT, HARNESS_BUG)

# §5.4：哪些分类计入 resolved 分母、哪些计作 agent 失败
COUNTS_IN_DENOMINATOR = (AGENT_RESULT, EMPTY_PATCH, PATCH_CONFLICT)
COUNTS_AS_AGENT_FAILURE = (EMPTY_PATCH, PATCH_CONFLICT)

# 测试侧文件判据：与构造器 §3.1 的分类规则**同源**（这里复述一遍而不 import 构造器，
# 因为 harness 不该依赖构造期的仓库画像；规则本身是预注册写死的，两边都必须一致）。
_TEST_DIR_RE = re.compile(r"(^|/)(tests|test|testing)(/|$)")
_TEST_BASENAME_RE = re.compile(r"(^|/)(test_[^/]+\.py|[^/]+_test\.py|conftest\.py)$")


def is_test_path(path: str) -> bool:
    p = (path or "").replace("\\", "/")
    return bool(_TEST_DIR_RE.search(p) or _TEST_BASENAME_RE.search(p))


# ---------------------------------------------------------------- 补丁拆分（§5.3）

_DIFF_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.M)
# ⚠ 两种 diff 形态都必须吃：
#   1. `git show` / `git diff` 的完整形态：每个文件以 `diff --git a/x b/x` 开头；
#   2. **裸 unified diff**：直接以 `--- a/x` / `+++ b/x` 开头（构造器 §6.3 的 wrong_patch
#      是 difflib.unified_diff 生成的，就是这个形态）。
# 只认第 1 种会把它整份丢掉（实测：wrong_patch 被剥成空串 => 误判 empty_patch）。
_HUNK_FILE_RE = re.compile(r"^\+\+\+ b/(\S+)", re.M)


def normalize_eol_patch(patch_text: str) -> str:
    """把候选补丁正文里的行尾**归一成 LF**（t8 / D7 的第一道防线）。

    ⚠ **机理更正（t9 独立审查抓出，t10 落地）**：t8 当时写的是「因为 `git diff`
    自己会把行尾归一化后输出」—— **这个说法是错的**。实测（同一 CRLF 工作区）：

        git diff 字节模式（不带 text=True）：254,616 字节，CRLF 3,361 处
        git diff 文本模式（text=True）    ：251,255 字节，CRLF 0 处

    ⇒ **git diff 不归一化行尾**；是 harness 自己的 `E.sh()` 以 `text=True` 读子进程输出
    （Python 的 universal newlines）把 CRLF 折成了 LF。**结论对、理由错**：
    归档件 `t5-smoke/agent_candidate_patch.diff` 的 CRLF=0 正是这个**读法**造成的，
    它是**损失性产物**（独立证据：该归档件里 `pyproject.toml` 212/213 对 `-`/`+` 行
    逐字相同 —— 若采集时正文真的没有 CRLF，git 根本不会产出这些 hunk），
    所以**不能**拿它证明「采集时正文没有 CRLF」。

    真正的问题在**工作树**：agent 的 `apply_patch` 把 `core.py` 写成了 CRLF，
    而 HEAD 是 LF ⇒ git 认为**每一行都变了** ⇒ 输出一个 `@@ -1,3134 +1,3135 @@`
    的整文件 hunk（9 行语义改动被淹没在里面）。

    ⇒ 根治手段是**在采集 diff 之前**把工作树的行尾归一（见 `normalize_worktree_eol`）。
    本函数保留，作为「补丁正文自带 CRLF」场景的第二道防线（幂等、只动行尾）。

    ⚠ 给 P1 的提醒：受上述读法所限，**harness 采集到的补丁文本天然不含 CRLF**
    ⇒ 「补丁正文行尾」这条防线实际上永远不会触发；真正生效的只有 `normalize_worktree_eol`。
    """
    if not patch_text:
        return patch_text or ""
    return patch_text.replace("\r\n", "\n")


def normalize_worktree_eol(ws: str, log_path: str | None = None) -> dict:
    """把工作树里**已跟踪的文本文件**的行尾归一成 LF（t8 / D7 的根治手段）。

    ⚠ 为什么必须在 `git diff` **之前**做：agent 的 `apply_patch` 写出 CRLF，
    而基线 commit 是 LF ⇒ git 认为每行都变了 ⇒ `git diff` 给出整文件单 hunk，
    9 行语义改动变成 251,255 字节（放大 ~2.7 万倍）。补丁正文里的行尾再怎么归一
    都救不了 —— 差异本身就在内容里。

    做法：只处理 `git status --porcelain` 里**被修改的已跟踪 `.py`/文本文件**，
    把 CRLF 换成 LF。**不碰**未跟踪文件（那是 agent 新建的，不该被我们改写）、
    不碰二进制（有 NUL 字节的跳过）。

    只动行尾、不动代码 → 判定语义（F2P 全过 ∧ P2P 全过）不变。
    返回统计（供证据留档）。
    """
    out = {"scanned": 0, "normalized": [], "skipped_binary": [], "skipped_untracked": 0}
    r = git(ws, "status", "--porcelain", timeout=180)
    for line in (r.stdout or "").splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip().strip('"').replace("\\", "/")
        if "?" in code:                      # 未跟踪：不碰
            out["skipped_untracked"] += 1
            continue
        full = os.path.join(ws, *path.split("/"))
        if not os.path.isfile(full):
            continue
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            continue
        out["scanned"] += 1
        if b"\x00" in data[:8192]:           # 二进制：跳过
            out["skipped_binary"].append(path)
            continue
        if b"\r\n" not in data:
            continue
        new = data.replace(b"\r\n", b"\n")
        try:
            with open(full, "wb") as f:
                f.write(new)
            out["normalized"].append({"path": path, "bytes_before": len(data),
                                      "bytes_after": len(new),
                                      "crlf_before": data.count(b"\r\n")})
        except OSError:
            continue
    if log_path:
        write_text(log_path, json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return out


def eol_stats(patch_text: str) -> dict:
    """补丁的行尾统计与 hunk 数 —— 供 D7 的「归一化前后对照」取证。"""
    text = patch_text or ""
    return {
        "bytes": len(text.encode("utf-8")),
        "crlf_lines": text.count("\r\n"),
        "lf_lines": text.count("\n") - text.count("\r\n"),
        "hunks": len(re.findall(r"^@@", text, flags=re.M)),
        "files": len(re.findall(r"^diff --git ", text, flags=re.M))
        or len(re.findall(r"^\+\+\+ b/", text, flags=re.M)),
    }


def _split_blocks(patch_text: str):
    """把补丁切成「每文件一块」。返回 [(path, block_text), ...]。"""
    text = patch_text or ""
    if not text.strip():
        return []
    # 形态 1：有 diff --git 行 => 按它切
    if text.lstrip().startswith("diff --git ") or "\ndiff --git " in text:
        blocks, cur = [], None
        for line in text.splitlines(keepends=True):
            if line.startswith("diff --git "):
                if cur is not None:
                    blocks.append(cur)
                cur = [line]
            elif cur is not None:
                cur.append(line)
        if cur is not None:
            blocks.append(cur)
        out = []
        for blk in blocks:
            head = "".join(blk)
            m = _DIFF_HEADER_RE.match(head)
            out.append(((m.group(2) if m else ""), head))
        return out
    # 形态 2：裸 unified diff => 按 `--- a/...` 起的块切（每个块以 --- 开头，后跟 +++）
    blocks, cur = [], None
    for line in text.splitlines(keepends=True):
        if line.startswith("--- "):
            if cur is not None:
                blocks.append(cur)
            cur = [line]
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        blocks.append(cur)
    out = []
    for blk in blocks:
        head = "".join(blk)
        m = _HUNK_FILE_RE.search(head)
        path = m.group(1) if m else ""
        out.append((path.replace("\\", "/"), head))
    return out


def split_patch_by_test_side(patch_text: str):
    """把候选补丁按文件切成 (source_side, test_side, dropped)。

    `dropped` 是既不是测试侧、也不是 `.py` 的文件（文档/changelog）—— 保留它们不影响判定，
    但单列出来便于解释。按「每文件一块」切分（见 _split_blocks），不是按行 ——
    否则一个文件的 header 会被算到上一个文件头上。
    """
    src, test, dropped = [], [], []
    for path, blk in _split_blocks(patch_text):
        if is_test_path(path):
            test.append(blk)
        elif path.endswith(".py"):
            src.append(blk)
        else:
            dropped.append(blk)
    # 每块自带末尾换行；拼回去时保证以 \n 结尾，否则 git apply 可能吃掉最后一行
    def join(parts):
        text = "".join(parts)
        return text if (not text or text.endswith("\n")) else text + "\n"
    return join(src), join(test), dropped


def stripped_patch(patch_text: str):
    """§5.3 的返回值：`(可判定用的补丁, 被剥离的测试侧补丁, 非 .py 被丢的路径, 是否动了测试, 测试侧路径)`。

    ⚠ 只剥「测试侧」；非 .py 的改动（changelog 等）**不算作弊**，留着一起 apply
    ——它们不影响任何测试行为，剥掉反而会让「补丁能不能干净应用」的判断偏离真实。
    """
    src, test, dropped = split_patch_by_test_side(patch_text)
    kept = src + "".join(dropped)
    touched_tests = bool(test.strip())
    dropped_paths = [p for p, _b in _split_blocks(patch_text) if not is_test_path(p)
                     and not p.endswith(".py")]
    test_paths = [p for p, _b in _split_blocks(patch_text) if is_test_path(p)]
    return kept, test, dropped_paths, touched_tests, test_paths


def patch_touched_paths(patch_text: str):
    return [p for p, _b in _split_blocks(patch_text)]


def make_unified_diff(rel_path: str, old: str, new: str) -> str:
    """对单个文本文件生成 unified diff（工作区采集与测试注入用）。"""
    lo = old.splitlines(keepends=True)
    ln = new.splitlines(keepends=True)
    if lo and not lo[-1].endswith("\n"):
        lo[-1] += "\n"
        ln = (new + "\n").splitlines(keepends=True)
    d = list(difflib.unified_diff(lo, ln, fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}", n=3))
    return "".join(d)


# ---------------------------------------------------------------- 判定

def classify_run(run: dict) -> str:
    """由**一次独立 pytest 运行**的原始记录得出 verdict_class 的「运行侧」部分。

    只处理「这次运行本身健不健康」，不涉及补丁是否存在（那由调用方先判）。

    ⚠ 顺序写死：超时 -> 退出码/收集错误 -> 逐 node 结果是否有意义。
    """
    if run.get("timed_out"):
        return TIMEOUT
    rc = run.get("returncode")
    meta = run.get("meta") or {}
    # 退出码 ≥2 = usage error / 内部错误 / 收集错误；收集期 error 同样算链路故障
    if isinstance(rc, int) and rc >= 2:
        return INFRA_ERROR
    if meta.get("errors") or meta.get("collection_errors"):
        return INFRA_ERROR
    if not run.get("results"):
        # 没有任何逐 node 结果 = 判定拿不到证据 => infra，不是「改错了」
        return INFRA_ERROR
    return AGENT_RESULT


def judge(f2p, p2p, results: dict) -> dict:
    """§5.1：F2P 全过 ∧ P2P 全过 => resolved。**逐 node 精确匹配**。"""
    missing_f = [n for n in f2p if n not in results]
    missing_p = [n for n in p2p if n not in results]
    bad_f = [n for n in f2p if results.get(n) != "passed"]
    bad_p = [n for n in p2p if results.get(n) != "passed"]
    # 「消失」也是不通过（§4.4：改名/参数空间变化视为未通过）
    ok = not bad_f and not bad_p
    return {
        "resolved": ok,
        "f2p_total": len(f2p),
        "f2p_not_passed": bad_f,
        "f2p_missing": missing_f,
        "p2p_total": len(p2p),
        "p2p_not_passed": bad_p,
        "p2p_missing": missing_p,
    }


def verdict_from(run_class: str, judged: dict) -> tuple[str, bool]:
    """把「运行侧分类」与「逐 node 判定」合成最终 (verdict_class, resolved)。

    - 运行侧不是 agent_result（timeout / infra）=> 直接照抄，**resolved=False 但不计分母**
      （timeout/infra 不属于 agent 的能力表现，§5.4）。
    - 运行侧健康 => 由逐 node 结果二分（§5.1）。
    """
    if run_class in (TIMEOUT, INFRA_ERROR, HARNESS_BUG):
        return run_class, False
    return AGENT_RESULT, bool(judged.get("resolved"))


def summarize(rows) -> dict:
    """§5.5 报告口径：分母 = agent_result + empty_patch + patch_conflict。"""
    counts = {c: 0 for c in ALL_CLASSES}
    for r in rows:
        counts[r.get("verdict_class", INFRA_ERROR)] = counts.get(r.get("verdict_class"), 0) + 1
    denominator = sum(counts[c] for c in COUNTS_IN_DENOMINATOR)
    resolved_n = sum(1 for r in rows if r.get("resolved"))
    return {
        "total": len(rows),
        "counts": counts,
        "resolved": resolved_n,
        "denominator": denominator,
        "resolved_rate": (resolved_n / denominator) if denominator else None,
        "infra_retryable": counts[INFRA_ERROR] + counts[TIMEOUT],
    }