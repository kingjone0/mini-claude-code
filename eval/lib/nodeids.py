"""JUnit XML 的 testcase 属性 → pytest node id，并与 --collect-only -q 的形式对齐。

pytest 的 `--collect-only -q` 输出形如：
    tests/test_core.py::TestFoo::test_bar[baz]
    src/pluggy/_manager.py::some_doctest          （doctest 用文件路径）
JUnit XML 给出 file / classname / name 三个属性，classname 是「模块 + 可选类」的点分串。
本模块把两者归一到同一个字符串空间，保证 F2P/P2P 的 node id **在 harness 内自洽**
（预注册 §4.4：字符串精确匹配）。
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET


def node_id_from_junit(file_attr: str, classname: str, name: str) -> str:
    """JUnit (file, classname, name) → `<相对路径>::[类名::]函数名`。

    JUnit 的 classname 是「模块点分路径 [+ 测试类名]」。用 file 的 stem 判断最后一段
    属于模块还是类，从而还原 pytest 的 node id 写法。
    """
    rel = (file_attr or "").replace("\\", "/")
    cls_chain = ""
    if classname and rel:
        parts = classname.split(".")
        stem = os.path.basename(rel).rsplit(".", 1)[0]
        if len(parts) >= 2 and parts[-1] != stem:
            # 末段不是模块名 ⇒ 它是测试类（可能还有嵌套，取末段）
            cls_chain = "::" + parts[-1]
    return f"{rel}{cls_chain}::{name}"


def parse_junit(xml_path: str):
    """返回 ({node_id: outcome}, meta)。outcome ∈ passed/failed/error/skipped。"""
    results: dict[str, str] = {}
    meta = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "suites": []}
    if not os.path.exists(xml_path):
        return results, meta
    root = ET.parse(xml_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        meta["suites"].append(suite.get("name"))
        for key in ("tests", "failures", "errors"):
            meta[key] += int(suite.get(key, "0") or 0)
        for tc in suite.iter("testcase"):
            nid = node_id_from_junit(tc.get("file") or "", tc.get("classname") or "", tc.get("name") or "")
            if tc.find("failure") is not None:
                outcome = "failed"
            elif tc.find("error") is not None:
                outcome = "error"
            elif tc.find("skipped") is not None:
                outcome = "skipped"
                meta["skipped"] += 1
            else:
                outcome = "passed"
            results[nid] = outcome
    return results, meta


def normalize_against_collection(results: dict[str, str], collected: list[str]) -> dict[str, str]:
    """把 JUnit 的 node id 映射到 collect-only 的写法（同一路径 + 符号链）。

    规则：对每个 collected id 拆成 (file_part, symbol_chain)。JUnit 键若与之同 file_part，
    且「末两段」一致（pytest 9 的 JUnit classname 会带上参数化 fixture 串，如
    ``test_x(py_code='...')``，而 collect 输出是 ``test_x[py_code]``），则替换为 collected 写法。
    未匹配上的 JUnit 键原样保留（doctest、collect 范围外的项）。
    """
    by_file: dict[str, list[str]] = {}
    for cid in collected:
        f, _, sym = cid.partition("::")
        by_file.setdefault(f.replace("\\", "/"), []).append(sym)
    out: dict[str, str] = {}
    for nid, outcome in results.items():
        f, _, sym = nid.partition("::")
        chain = [s for s in sym.split("::") if s]
        match = None
        for c in by_file.get(f.replace("\\", "/"), []):
            cchain = [s for s in c.split("::") if s]
            if c == sym:
                match = c
                break
            if len(cchain) == len(chain):
                if cchain[:-1] == chain[:-1] and _leaf_eq(cchain[-1], chain[-1]):
                    match = c
                    break
                if cchain[1:] == chain[1:] and _leaf_eq(cchain[0], chain[0]):
                    match = c
                    break
        if match is not None:
            out[f"{f}::{match}" if match else f] = outcome
        else:
            out[nid] = outcome
    return out


def _leaf_eq(a: str, b: str) -> bool:
    """比较 collect 的参数化叶 id 与 JUnit 的函数名（含参数文本）。"""
    if a == b:
        return True
    base_a = a.split("[", 1)[0]
    if base_a != b.split("(", 1)[0].split("[", 1)[0]:
        return False
    params_a = a[len(base_a):].strip("[]") if len(a) > len(base_a) else ""
    params_b = b[len(base_a):].strip("()") if len(b) > len(base_a) else ""
    if not params_a or not params_b:
        return True
    toks_a = sorted(t.strip() for t in params_a.split(",") if t.strip())
    toks_b = sorted(re.sub(r"^.*?=", "", t).strip() for t in params_b.split(",") if t.strip())
    if toks_a == toks_b:
        return True
    # JUnit 常把参数值整体 repr（含换行）；退化为「前缀匹配」
    joined_a = ",".join(toks_a)
    joined_b = ",".join(toks_b)
    return joined_a[:40] == joined_b[:40]


import re  # noqa: E402
