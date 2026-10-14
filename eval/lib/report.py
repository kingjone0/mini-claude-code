"""观测点结果的唯一来源：pytest 的 JSON report（`--json-report`）。

JUnit XML 在本机 pytest 9 上**不带 `file` 属性**（classname 只有 `module.Class`），无法还原
node id ⇒ 不能当判据。改用 `pytest-json-report`：它逐条给出完整 `nodeid`，与
`--collect-only -q` 的输出同一字符串空间，满足预注册 §4.4「node id 字符串精确匹配」。

- outcome 映射：`passed` → passed；`failed` → failed；`error` → error；
  `skipped` / `xfailed` / `xpassed` **一律不算 passed**（§4.4）。
- 收集期错误（无 nodeid 的 report 条目）计入 meta.errors ⇒ 触发 §3.2 E6。
"""

from __future__ import annotations

import json
import os
import re

PASSED = "passed"
NONPASS = ("failed", "error", "skipped", "xfailed", "xpassed")

# ⚠ pytest-subtests 会把「用 subTest 写的用例」的 outcome 报成 `subtests passed` /
# `subtests failed` 这类带前缀的串（different-tests 插件亦然）。若不归一，这些**实际通过**的
# 用例在 §4.2 的推导里被当成「非 passed」，会把 T 观测点里大量本来就绿的用例误判成红灯，
# 或者反过来让 B 观测点少掉一大批 P2P 候选。⇒ 先按前缀归一，再套 §4.4 的严格取值。
_OUTCOME_ALIASES = {
    "subtests passed": "passed",
    "subtests failed": "failed",
    "subtests error": "error",
    "subtests skipped": "skipped",
    "subtests xfailed": "xfailed",
    "subtests xpassed": "xpassed",
}


def canonical_outcome(outcome: str) -> str:
    o = (outcome or "").strip().lower()
    return _OUTCOME_ALIASES.get(o, o)


def parse_json_report(path: str, ws: str | None = None):
    """返回 ({nodeid: outcome}, meta)。

    ⚠ node id **必须**折回工作区相对形式：`-c <ini>` 把 rootdir 变成 ini 所在目录，
    pytest 的 `nodeid` 随之变成相对 rootdir 的路径（collect 输出 `::test_x`、json report
    里则可能带 `_artifacts/.../repo/` 前缀）。两种形态都不能直接当 F2P/P2P —— 判定 harness
    与 agent 看到的工作区路径必须一致，§4.4 又要求字符串精确匹配。
    传 `ws` 即按 §8.1 工作区根归一，与 `--collect-only -q` 的输出空间对齐。
    """
    results: dict[str, str] = {}
    meta = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "collection_errors": 0}
    if not os.path.exists(path):
        return results, meta
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    for t in (doc.get("tests") or []):
        nid = normalize_nodeid(t.get("nodeid") or "", ws)
        out = canonical_outcome(t.get("outcome") or "unknown")
        if not nid:
            meta["collection_errors"] += 1
            continue
        results[nid] = out
        meta["tests"] += 1
        if out == "failed":
            meta["failures"] += 1
        elif out == "error":
            meta["errors"] += 1
        elif out in ("skipped", "xfailed", "xpassed"):
            meta["skipped"] += 1
    # summary 段更权威（含未挂在 tests 里的 collection error）
    summ = doc.get("summary") or {}
    for k in ("tests", "failures", "errors", "skipped"):
        if isinstance(summ.get(k), int):
            meta[k] = summ[k]
    stats = doc.get("statistics") or {}
    if isinstance(stats.get("error"), int):
        meta["errors"] = max(meta["errors"], int(stats["error"]))
    meta["exitcount"] = doc.get("exitcount")
    meta["duration"] = (doc.get("environment") or {}).get("duration")
    meta["durations"] = _durations(doc, ws)
    return results, meta


def normalize_nodeid(nid: str, ws: str | None = None) -> str:
    """把 pytest 报出的 node id 归一成 `<工作区相对 POSIX 路径>::<符号链>`。

    处理三种形态：
    1. 已经是相对路径（`tests/test_x.py::test_a`）⇒ 原样；
    2. 绝对路径（`E:\\...\\repo\\tests\\test_x.py::test_a`）⇒ 相对工作区；
    3. 相对 rootdir 但 rootdir 在 artifacts/ 下（`../../<iid>/repo/tests/...`）⇒ 折回工作区。
    无文件段（`::test_a`，即 pytest 把 rootdir 当根、路径被吃掉）⇒ 返回空串，
    由调用方按 E6 处理（拿不到可复现的 node id 就不该当判据）。
    """
    nid = (nid or "").replace("\\", "/")
    if not nid:
        return ""
    file_part, sep, sym = nid.partition("::")
    if not sep:
        return ""                       # 没有 `::` ⇒ 不是测试 node id（如 doctest 文件行）
    if not file_part:
        return ""
    if not os.path.isabs(file_part) and ws is None:
        return f"{file_part}{sep}{sym}"
    if ws:
        wsn = ws.replace("\\", "/").rstrip("/") + "/"
        if file_part.startswith(wsn):
            return f"{file_part[len(wsn):]}{sep}{sym}"
        if os.path.isabs(file_part):
            ap = os.path.abspath(file_part)
            if ap.lower().startswith(os.path.abspath(ws).lower() + os.sep):
                return f"{os.path.relpath(ap, ws).replace(chr(92), '/')}{sep}{sym}"
            return ""                   # 落在工作区之外 ⇒ 不是本实例的判据
        # 相对 artifacts/ 的 `../../<iid>/repo/...` 形态
        m = re.match(r"^(?:\.\./)+[^/]*/repo/(.+)$", file_part)
        if m:
            return f"{m.group(1)}{sep}{sym}"
    if os.path.isabs(file_part):
        return ""
    return f"{file_part}{sep}{sym}"


def _durations(doc, ws: str | None = None):
    """每个测试的耗时（秒），用于 §3.2 E7 的单测 > 60 s 判定。"""
    out = {}
    for t in doc.get("tests") or []:
        nid = normalize_nodeid(t.get("nodeid") or "", ws)
        dur = (t.get("call") or {}).get("duration") or t.get("duration")
        if nid and isinstance(dur, (int, float)):
            out[nid] = round(float(dur), 3)
    return out
