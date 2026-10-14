"""t6 复核 (2)：定位 AB-REPO-MAP.md §1.6 与并行批表的索引引用错误。

只做只读核对，不改报告。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
import agent.config  # noqa: E402

agent.config.force_utf8_output()

OUT = PROJECT_ROOT / "servers" / "experiments" / "out"

print("=" * 78)
print("报告声明的引用 vs 实测事件")
print("=" * 78)

claims = [
    # (报告位置, 报告说的事, 产物, 报告写的索引, 期望类型)
    ("L115 §1.6 对账表", "ab-A-1 被丢弃的 tool_result，153 字符", "ab-A-1", 8, "tool_result"),
    ("L116 §1.6 对账表", "ab-A-5 被丢弃的 tool_result，2244 字符", "ab-A-5", 7, "tool_result"),
    ("L132 并行批表", "ab-A-1 并行批 i=5,i=6 = list_files+search_code", "ab-A-1", 5, "tool_use:list_files"),
    ("L132 并行批表", "ab-A-1 并行批 i=5,i=6 = list_files+search_code", "ab-A-1", 6, "tool_use:search_code"),
    ("L133 并行批表", "ab-A-5 并行批 i=4,i=5 = read_file+run_tests", "ab-A-5", 4, "tool_use:read_file"),
    ("L133 并行批表", "ab-A-5 并行批 i=4,i=5 = read_file+run_tests", "ab-A-5", 5, "tool_use:run_tests"),
]

for where, what, lab, idx, expect in claims:
    d = json.loads((OUT / f"agent-{lab}.json").read_text(encoding="utf-8"))
    ev = next((e for e in d["events"] if e.get("i") == idx), None)
    if ev is None:
        actual = "(不存在)"
    elif ev.get("type") == "tool_use":
        actual = "tool_use:" + ev.get("tool", "").replace("mcp__code-tools__", "")
    else:
        actual = f"{ev.get('type')} chars={ev.get('chars')}"
    ok = "OK" if actual.startswith(expect) else "WRONG"
    print(f"  [{ok}] {where}")
    print(f"        描述: {what}")
    print(f"        {lab} i={idx}  期望={expect}")
    print(f"        实测: {actual}")
    print()

print("=" * 78)
print("正确的索引（供修复引用）")
print("=" * 78)
for lab in ("ab-A-1", "ab-A-5"):
    d = json.loads((OUT / f"agent-{lab}.json").read_text(encoding="utf-8"))
    print(f"--- {lab}")
    batch = []
    for e in d["events"]:
        if e.get("type") == "tool_use":
            batch.append(e)
        elif e.get("type") == "tool_result":
            if len(batch) >= 2:
                print(f"    并行批 tool_use 索引: i={[b['i'] for b in batch]}"
                      f" 工具={[b['tool'].replace('mcp__code-tools__','') for b in batch]}")
            batch = []
    # 找被丢弃的那条 result
    pend = None
    for e in d["events"]:
        if e.get("type") == "tool_use":
            if pend is not None:
                pass
            pend = e
        elif e.get("type") == "tool_result":
            if pend is None:
                print(f"    被丢弃的 tool_result: i={e.get('i')} chars={e.get('chars')}")
            else:
                pend = None
    print(f"    被丢弃的那条的完整字符数: "
          f"{[e.get('chars') for e in d['events'] if e.get('type')=='tool_result' and e.get('chars') in (153,2244)]}")