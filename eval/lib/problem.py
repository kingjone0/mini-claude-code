"""§3.4 problem_statement 生成与泄漏检查（构造器自动执行，人工不得豁免）。

判据：提交信息里出现 F2P 任一 node id、或 gold patch 里被改动的**符号名**（函数/类名）
⇒ 违规。允许「删除该句之后的文本」；删完不足 20 个非空白字符 ⇒ 排除该候选（E9 同级纪律）。

node id 的匹配用「完整 id」或其「:: 分段后缀」（``test_bucket_falsy_validator`` 这类叶名
也算 node id 的一部分），因此提交信息里直接点名测试函数即命中。
"""

from __future__ import annotations

import re

# §3.2 E9 黑名单（写死）
_E9_BLACKLIST = re.compile(r"^(wip|typo|bump|format|lint|update|fix(up)?\.?$)", re.I)
MIN_CHARS = 20


def _nonws_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def symbols_from_patch(patch_text: str):
    """gold patch 里出现的 def/class 名（hunk header + 增删行）。"""
    syms = set()
    for line in patch_text.splitlines():
        if line.startswith("@@"):
            tail = line.rsplit("@@", 1)[-1]
            for m in re.finditer(r"\b(?:def|class)\s+([A-Za-z_]\w*)", tail):
                syms.add(m.group(1))
        elif line[:1] in "+-":
            m = re.match(r"\s*(?:@\w+\s+)*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", line[1:])
            if m:
                syms.add(m.group(1))
    return syms


def _nid_variants(nid: str):
    parts = [p for p in nid.split("::") if p]
    out = {nid}
    for i in range(len(parts)):
        out.add("::".join(parts[i:]))
    return out


def leaked_tokens(text: str, fail_to_pass, patch_text: str):
    """返回命中的泄漏项（node id / symbol:NAME）。"""
    hits = []
    for nid in fail_to_pass or []:
        for v in sorted(_nid_variants(nid), key=len, reverse=True):
            if v and v in text:
                hits.append(nid)
                break
    for sym in sorted(symbols_from_patch(patch_text)):
        if len(sym) < 4 or sym.isdigit():
            continue
        if re.search(r"(?<![\w.])" + re.escape(sym) + r"(?![\w])", text):
            hits.append(f"symbol:{sym}")
    return hits


def strip_leaking_sentences(problem_statement: str, fail_to_pass, patch_text: str):
    """按段/句剔除命中泄漏的句子，返回 (kept_text, removed_list)。"""
    kept_paras, removed = [], []
    for para in problem_statement.split("\n\n"):
        if not leaked_tokens(para, fail_to_pass, patch_text):
            kept_paras.append(para)
            continue
        sentences = re.split(r"(?<=[.!?:])\s+", para)
        keep_sents = []
        for s in sentences:
            if leaked_tokens(s, fail_to_pass, patch_text):
                removed.append(s)
            else:
                keep_sents.append(s)
        if keep_sents:
            kept_paras.append("\n".join(keep_sents))
    return "\n\n".join(kept_paras), removed


def build_problem_statement(subject: str, body: str, fail_to_pass, patch_text: str):
    """返回 (problem_statement, status, detail)。status ∈ ok / trimmed / rejected。"""
    subject = (subject or "").strip()
    body = (body or "").strip()
    raw = f"{subject}\n\n{body}".strip() if body else subject
    if _E9_BLACKLIST.match(subject):
        return "", "rejected", "E9: subject 命中零信息量黑名单正则"
    if _nonws_len(raw) < MIN_CHARS:
        return "", "rejected", f"E9: 全文非空白字符 {_nonws_len(raw)} < {MIN_CHARS}"
    hits = leaked_tokens(raw, fail_to_pass, patch_text)
    if not hits:
        return raw, "ok", ""
    trimmed, removed = strip_leaking_sentences(raw, fail_to_pass, patch_text)
    if _nonws_len(trimmed) < MIN_CHARS:
        return "", "rejected", (
            f"E9/§3.4: 删除 {len(removed)} 句泄漏文本后非空白字符 {_nonws_len(trimmed)} < {MIN_CHARS}；"
            f"泄漏项={hits}"
        )
    return trimmed, "trimmed", f"删除 {len(removed)} 句泄漏文本；泄漏项={hits}"
