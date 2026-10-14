"""t4 复核 (2)：核 captain 的 A/B 项 —— 残留旧数字扫描 + 成本拆解复算。"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
import agent.config  # noqa: E402

agent.config.force_utf8_output()

OUT = PROJECT_ROOT / "servers" / "experiments" / "out"
files = sorted(OUT.glob("agent-ab-[AB]-*.json"))
assert len(files) == 12

# ── 复算：把 tool_use 与紧随 tool_result 配对，同时记录被丢弃的孤儿 ────────────
per_tool = defaultdict(lambda: defaultdict(int))
per_tool_calls = defaultdict(lambda: defaultdict(int))
raw = defaultdict(int)
paired = defaultdict(int)
orphans = []

for f in files:
    d = json.loads(f.read_text(encoding="utf-8"))
    arm = d["metrics"]["label"].split("-")[1]
    pending = None
    for e in d["events"]:
        if e.get("type") == "tool_use":
            pending = e.get("tool", "?").replace("mcp__code-tools__", "")
            per_tool_calls[arm][pending] += 1
        elif e.get("type") == "tool_result":
            c = e.get("chars") or 0
            raw[arm] += c
            if pending is not None:
                per_tool[arm][pending] += c
                paired[arm] += c
                pending = None
            else:
                orphans.append((d["metrics"]["label"], e.get("i"), c))

print("=" * 78)
print("A) 孤儿 tool_result（pair_calls 会静默丢弃它们）")
print("=" * 78)
for o in orphans:
    print("  ", o)
print(f"  孤儿总数 {len(orphans)}，字符合计 {sum(o[2] for o in orphans)}（全部在臂 A）")

print()
print("=" * 78)
print("B) 两种「工具返回字符总量」口径对照")
print("=" * 78)
print(f"{'metric':<28}{'A':>12}{'B':>12}{'B-A':>12}{'B/A':>10}")
print(f"{'raw 全部 tool_result':<28}{raw['A']:>12,}{raw['B']:>12,}"
      f"{raw['B']-raw['A']:>12,}{(raw['B']/raw['A']-1)*100:>9.2f}%")
print(f"{'配对后（报告所用）':<28}{paired['A']:>12,}{paired['B']:>12,}"
      f"{paired['B']-paired['A']:>12,}{(paired['B']/paired['A']-1)*100:>9.2f}%")
print(f"{'差值（被丢弃的）':<28}{raw['A']-paired['A']:>12,}{raw['B']-paired['B']:>12,}")

print()
print("=" * 78)
print("C) 报告 §3.2 每个数字的独立复算")
print("=" * 78)
claims = {
    "read_file": (168609, 168559),
    "run_tests": (38263, 19626),
    "repo_map": (20603, 379),
    "search_code": (2136, 6963),
    "list_files": (4890, 5868),
    "apply_patch": (312, 312),
}
print(f"{'tool':<14}{'报告A':>10}{'实测A':>10}{'A?':>5}{'报告B':>10}{'实测B':>10}{'B?':>5}")
for t, (ca, cb) in claims.items():
    ma, mb = per_tool["A"].get(t, 0), per_tool["B"].get(t, 0)
    print(f"{t:<14}{ca:>10,}{ma:>10,}{'OK' if ca==ma else 'MISMATCH':>5}"
          f"{cb:>10,}{mb:>10,}{'OK' if cb==mb else 'MISMATCH':>5}")
print(f"{'合计':<14}{234813:>10,}{paired['A']:>10,}"
      f"{'OK' if paired['A']==234813 else 'MISMATCH':>5}"
      f"{201707:>10,}{paired['B']:>10,}{'OK' if paired['B']==201707 else 'MISMATCH':>5}")

print()
print("=" * 78)
print("D) 报告 §3.2 的两个差值与占比复算")
print("=" * 78)
diff_total = paired["B"] - paired["A"]
d_rt = per_tool["B"].get("run_tests", 0) - per_tool["A"].get("run_tests", 0)
d_rm = per_tool["B"].get("repo_map", 0) - per_tool["A"].get("repo_map", 0)
d_rf = per_tool["B"].get("read_file", 0) - per_tool["A"].get("read_file", 0)
print(f"总差 (B-A)                      = {diff_total:,}   报告: -33,106  {'OK' if diff_total==-33106 else 'MISMATCH'}")
print(f"read_file 差                    = {d_rf:,}         报告: -50      {'OK' if d_rf==-50 else 'MISMATCH'}")
print(f"run_tests 差                    = {d_rt:,}        报告: -18,637  {'OK' if d_rt==-18637 else 'MISMATCH'}")
print(f"repo_map 差                     = {d_rm:,}        报告: -20,224  {'OK' if d_rm==-20224 else 'MISMATCH'}")
print(f"run_tests 占总差比例            = {d_rt/diff_total*100:.1f}%   报告: 56%")
print(f"run_tests 调用次数 A/B          = {per_tool_calls['A'].get('run_tests')}/{per_tool_calls['B'].get('run_tests')}   报告: 16/12  "
      f"{'OK' if (per_tool_calls['A'].get('run_tests'), per_tool_calls['B'].get('run_tests'))==(16,12) else 'MISMATCH'}")
print(f"read_file 调用次数 A/B          = {per_tool_calls['A'].get('read_file')}/{per_tool_calls['B'].get('read_file')}   报告: 27/24  "
      f"{'OK' if (per_tool_calls['A'].get('read_file'), per_tool_calls['B'].get('read_file'))==(27,24) else 'MISMATCH'}")
print(f"read_file 差占比                = {d_rf/diff_total*100:.2f}%")

print()
print("=" * 78)
print("E) 残留旧数字扫描：把报告里出现的数字与实测值对表")
print("=" * 78)
report = (PROJECT_ROOT / "AB-REPO-MAP.md").read_text(encoding="utf-8")

# 报告里所有独立出现的整数（>=100，带千分位或裸数）
nums = set()
for m in re.finditer(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d{3,})(?![\d.,%])", report):
    nums.add(m.group(1))

known = {
    "234,813": paired["A"], "201,707": paired["B"],
    "168,609": per_tool["A"].get("read_file"), "168,559": per_tool["B"].get("read_file"),
    "38,263": per_tool["A"].get("run_tests"), "19,626": per_tool["B"].get("run_tests"),
    "20,603": per_tool["A"].get("repo_map"), "379": per_tool["B"].get("repo_map"),
    "2,136": per_tool["A"].get("search_code"), "6,963": per_tool["B"].get("search_code"),
    "4,890": per_tool["A"].get("list_files"), "5,868": per_tool["B"].get("list_files"),
    "312": per_tool["A"].get("apply_patch"),
    "82,397": 82397, "72,819": 72819,
    "20,224": 20224, "33,106": -33106, "18,637": -18637,
    "46,776": 46776, "41,709": 41709,
}
print("报告中出现但不在已核对清单里的数字（需人工判读是否陈旧）：")
for n in sorted(nums, key=lambda s: -int(s.replace(",", ""))):
    if n not in known:
        print("   ", n, "  <-- 未在本次复算清单内")
print()
print("已核对且吻合的:", len([k for k in known if k in nums]), "/", len(known))

print()
print("=" * 78)
print("F) input tokens / cost 复算（报告 §3.1）")
print("=" * 78)
it = defaultdict(int); ot = defaultdict(int); co = defaultdict(float); el = defaultdict(list)
for f in files:
    d = json.loads(f.read_text(encoding="utf-8"))
    arm = d["metrics"]["label"].split("-")[1]
    m = d["metrics"]
    it[arm] += m.get("input_tokens") or 0
    ot[arm] += m.get("output_tokens") or 0
    co[arm] += m.get("cost_usd") or 0.0
    el[arm].append(m.get("elapsed_s") or 0)
print(f"input tokens  A={it['A']:,} (报告 82,397 {'OK' if it['A']==82397 else 'MISMATCH'})  "
      f"B={it['B']:,} (报告 72,819 {'OK' if it['B']==72819 else 'MISMATCH'})")
print(f"output tokens A={ot['A']:,} (报告 11,264 {'OK' if ot['A']==11264 else 'MISMATCH'})  "
      f"B={ot['B']:,} (报告 11,236 {'OK' if ot['B']==11236 else 'MISMATCH'})")
print(f"cost_usd      A={co['A']:.4f} (报告 0.8983 {'OK' if abs(co['A']-0.8983)<1e-3 else 'MISMATCH'})  "
      f"B={co['B']:.4f} (报告 0.7998 {'OK' if abs(co['B']-0.7998)<1e-3 else 'MISMATCH'})")
print(f"elapsed_s 均值 A={sum(el['A'])/6:.1f} (报告 54.3)  B={sum(el['B'])/6:.1f} (报告 53.6)")

print()
print("=" * 78)
print("G) 报告 §4.1 第4条：「12 次共 30 次 search_code 调用，平均 303 字符/次」")
print("=" * 78)
sc_calls = sum(per_tool_calls[a].get("search_code", 0) for a in ("A", "B"))
sc_chars = sum(per_tool[a].get("search_code", 0) for a in ("A", "B"))
print(f"search_code 总调用 = {sc_calls} (报告 30)  总字符 = {sc_chars:,}  均值 = {sc_chars/sc_calls:.0f} (报告 303)")

print()
print("=" * 78)
print("H) repo_map 占各臂总量比例（报告 §4.1 第5条 8.8% / 0.2%）")
print("=" * 78)
for a in ("A", "B"):
    r = per_tool[a].get("repo_map", 0)
    print(f"  臂 {a}: {r:,} / {paired[a]:,} = {r/paired[a]*100:.1f}%")
    print(f"        若用 raw 口径: {r:,} / {raw[a]:,} = {r/raw[a]*100:.1f}%")