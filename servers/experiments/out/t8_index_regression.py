"""t8 复核：从 AB-REPO-MAP.md 正文解析索引断言并对照产物验证（可回归）。

修正 t6_index_check.py 的缺陷：那个脚本把断言硬编码，不读报告，因此修前修后都报同一结果。
本脚本**解析报告正文**里 §1.6 的两个表，再逐条对照产物 —— 报告改了，结论就会跟着变。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
import agent.config  # noqa: E402

agent.config.force_utf8_output()

OUT = PROJECT_ROOT / "servers" / "experiments" / "out"
REPORT = PROJECT_ROOT / "AB-REPO-MAP.md"
text = REPORT.read_text(encoding="utf-8")
lines = text.split("\n")


def art(lab: str) -> Path:
    """lab 形如 'ab-A-1' 或 'agent-ab-A-1'；统一映射到 agent-ab-A-1.json。"""
    lab = lab.removeprefix("agent-")
    return OUT / f"agent-{lab}.json"


def event(lab: str, idx: int):
    d = json.loads(art(lab).read_text(encoding="utf-8"))
    return next((e for e in d["events"] if e.get("i") == idx), None)


def kind(e) -> str:
    if e is None:
        return "(不存在)"
    if e.get("type") == "tool_use":
        return "tool_use:" + e.get("tool", "").replace("mcp__code-tools__", "")
    return f"{e.get('type')} chars={e.get('chars')}"


print("=" * 78)
print("1) §1.6 对账表 —— 从报告正文解析「被丢弃的 tool_result」索引")
print("=" * 78)
bad = 0
# 形如： | `agent-ab-A-1.json` | 1 条 | `i=11` | 153 |
for m in re.finditer(r"\|\s*`(agent-ab-[AB]-\d+)\.json`\s*\|\s*\d+\s*条\s*\|\s*`i=(\d+)`\s*\|\s*([\d,]+)\s*\|", text):
    lab, idx, chars = m.group(1), int(m.group(2)), int(m.group(3).replace(",", ""))
    e = event(lab, idx)
    good = e is not None and e.get("type") == "tool_result" and (e.get("chars") or 0) == chars
    print(f"  [{'OK' if good else 'WRONG'}] {lab} i={idx} 声称 {chars} 字")
    print(f"        实测: {kind(e)}")
    bad += 0 if good else 1

print()
print("=" * 78)
print("2) §1.6 并行批表 —— 从报告正文解析「批内 tool_use」索引")
print("=" * 78)
# 形如： | `agent-ab-A-1.json` | `i=8, i=9` | `list_files` + `search_code` |
for m in re.finditer(r"\|\s*`(agent-ab-[AB]-\d+)\.json`\s*\|\s*`i=(\d+),\s*i=(\d+)`\s*\|\s*`([a-z_]+)`\s*\+\s*`([a-z_]+)`\s*\|", text):
    lab, a, b, t1, t2 = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4), m.group(5)
    ea, eb = event(lab, a), event(lab, b)
    ga = ea is not None and ea.get("type") == "tool_use" and ea.get("tool", "").endswith(t1)
    gb = eb is not None and eb.get("type") == "tool_use" and eb.get("tool", "").endswith(t2)
    good = ga and gb
    print(f"  [{'OK' if good else 'WRONG'}] {lab} i={a},i={b} 声称 {t1}+{t2}")
    print(f"        实测: {kind(ea)} / {kind(eb)}")
    bad += 0 if good else 1

print()
print("=" * 78)
print("3) §2.2 「events[N]（即 i=M）」两坐标并列 —— 解析并验证")
print("=" * 78)
for m in re.finditer(r"`events\[(\d+)\]`（即 `i=(\d+)`）[^|]*\|[^|]*\|\s*`mcp__code-tools__repo_map`", text):
    pass
# 更稳：抓 events[N]（即 i=M） 出现在表格行且该行含 repo_map
for line in lines:
    if "events[" in line and "即 `i=" in line and "repo_map" in line:
        mm = re.search(r"`(agent-ab-[AB]-\d+)\.json`.*?`events\[(\d+)\]`（即 `i=(\d+)`）", line)
        if not mm:
            continue
        lab, pos, idx = mm.group(1), int(mm.group(2)), int(mm.group(3))
        d = json.loads(art(lab).read_text(encoding="utf-8"))
        arr = d["events"][pos]
        good = arr.get("i") == idx and "repo_map" in str(arr.get("tool"))
        print(f"  [{'OK' if good else 'WRONG'}] {lab} events[{pos}] 即 i={idx}")
        print(f"        实测: events[{pos}].i={arr.get('i')}  tool={kind(arr)}")
        bad += 0 if good else 1

print()
print("=" * 78)
print("4) 勘误段引用（L142-144）—— 应只作为「原写错了什么」出现")
print("=" * 78)
for i, l in enumerate(lines, 1):
    if "索引勘误" in l or ("数组下标" in l and "不是" in l):
        print(f"  L{i}: {l.strip()[:150]}")

print()
print("=" * 78)
print(f"结论：从报告正文解析出的索引断言中，不符项 = {bad}")
print("=" * 78)