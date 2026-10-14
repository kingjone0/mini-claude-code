"""t6 复核：修正配对 bug 后的 README.rst 计数 + 成本口径复核。

关键：按“并行批”模型配对 —— 连续的 N 个 tool_use 后面跟的 M 个 tool_result
需要按内容/长度对应，而不能“一个 use 吃一个 result 就清空 pending”。
本轮用更稳的办法：tool_result 的 text 里带结果特征，按特征归属工具。
"""

from __future__ import annotations

import json
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

print("=" * 78)
print("1) 按并行批模型重新配对（模拟 SDK 真实行为）")
print("=" * 78)


def attribute(files):
    """按批处理：收集一批连续 tool_use，遇到 tool_result 依次消耗该批。

    这不是为了“更准”，而是为了暴露“并行批”下两种消费顺序的差别。
    同时用内容特征做独立归属（见 attribute_by_content）。
    """
    per_run = {}
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        lab = d["metrics"]["label"]
        batch = []
        pairs = []
        for e in d["events"]:
            if e.get("type") == "tool_use":
                batch.append(e)
            elif e.get("type") == "tool_result":
                if batch:
                    use = batch.pop(0)
                    pairs.append((use, e))
                else:
                    pairs.append((None, e))
        per_run[lab] = pairs
    return per_run


def attribute_by_content(files):
    """不依赖配对顺序：用 tool_result.text 的内容特征推断它属于哪个工具。

    特征（来自 servers/code_tools_server.py 的输出格式）：
      read_file    -> '# <path>（共 N 行，显示 a-b）' 或 '不存在或不是文件：'
      list_files   -> 目录清单（无 '# ' 头，无 '退出码'）
      run_tests    -> '退出码 = N'
      search_code  -> '<path>:<lineno>: ' 形式
      repo_map     -> '# 符号索引' 或 '# N 个 *.py 文件'
      apply_patch  -> '已修改'
    """
    per_run = {}
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        lab = d["metrics"]["label"]
        cur = None
        seq = []
        for e in d["events"]:
            if e.get("type") == "tool_use":
                cur = e
            elif e.get("type") == "tool_result":
                txt = e.get("text") or ""
                guess = "?"
                if "退出码 =" in txt:
                    guess = "run_tests"
                elif "已修改" in txt:
                    guess = "apply_patch"
                elif "不存在或不是文件" in txt or "（共" in txt:
                    guess = "read_file"
                elif "符号索引" in txt or "个 *.py 文件" in txt:
                    guess = "repo_map"
                elif "窗口已截断" in txt or "已截断" in txt:
                    guess = "?"
                seq.append((guess, e, cur))
        per_run[lab] = seq
    return per_run


# 方法 A：并行批（按顺序消耗）
pa = attribute(files)
rst_a = Counter()
for lab, pairs in pa.items():
    for use, res in pairs:
        if use is not None and use.get("tool", "").endswith("read_file"):
            p = str((use.get("input") or {}).get("path", ""))
            if p.endswith("README.rst"):
                rst_a[lab] += 1
print("方法 A —— 并行批按序消耗:")
print("  ", dict(rst_a), " 合计 =", sum(rst_a.values()))

# 方法 B：先看每份里“有几个 README.rst 的 read_file tool_use”——这是最直接、不依赖配对的答案
rst_b = Counter()
for f in files:
    d = json.loads(f.read_text(encoding="utf-8"))
    lab = d["metrics"]["label"]
    for e in d["events"]:
        if e.get("type") == "tool_use" and e.get("tool", "").endswith("read_file"):
            if str((e.get("input") or {}).get("path", "")).endswith("README.rst"):
                rst_b[lab] += 1
print()
print("方法 B —— 直接数 read_file 且 path==README.rst 的 tool_use（不涉及配对）:")
print("  ", dict(rst_b), " 合计 =", sum(rst_b.values()))

# 方法 C：数内容确实是 README.rst 的 tool_result（不看 tool_use）
rst_c = Counter()
for f in files:
    d = json.loads(f.read_text(encoding="utf-8"))
    lab = d["metrics"]["label"]
    for e in d["events"]:
        if e.get("type") == "tool_result":
            t = e.get("text") or ""
            if "README.rst（共" in t or ("README.rst" in t and "共" in t):
                rst_c[lab] += 1
print()
print("方法 C —— 数内容含 'README.rst（共' 的 tool_result:")
print("  ", dict(rst_c), " 合计 =", sum(rst_c.values()))

print()
print("=" * 78)
print("2) README.md 计数（不受配对影响，作对照）")
print("=" * 78)
md_calls = 0
for f in files:
    d = json.loads(f.read_text(encoding="utf-8"))
    for e in d["events"]:
        if e.get("type") == "tool_use" and e.get("tool", "").endswith("read_file"):
            if str((e.get("input") or {}).get("path", "")).endswith("README.md"):
                md_calls += 1
print("README.md tool_use 总数 =", md_calls, "(报告说 12)")

print()
print("=" * 78)
print("3) 成本 raw 口径复核（t5 改为 raw 主口径后的对账）")
print("=" * 78)
raw = defaultdict(int)
paired_old = defaultdict(int)
for f in files:
    d = json.loads(f.read_text(encoding="utf-8"))
    arm = d["metrics"]["label"].split("-")[1]
    pending = None
    for e in d["events"]:
        if e.get("type") == "tool_use":
            pending = e
        elif e.get("type") == "tool_result":
            c = e.get("chars") or 0
            raw[arm] += c
            if pending is not None:
                paired_old[arm] += c
                pending = None
print("raw（全部 tool_result.chars）:  A=%s  B=%s  差=%s  B/A=%.2f%%"
      % (f"{raw['A']:,}", f"{raw['B']:,}", f"{raw['B']-raw['A']:,}", (raw['B']/raw['A']-1)*100))
print("旧 paired 口径:               A=%s  B=%s" % (f"{paired_old['A']:,}", f"{paired_old['B']:,}"))
print()
print("t5 报告声称: A=237,210 / B=201,707 / -15.0%")
print("实测 raw 是否 == 237,210 ?", raw["A"] == 237210)
print("实测 raw 是否 == 201,707 ?", raw["B"] == 201707)
print(f"raw 总差 = {raw['B']-raw['A']:,}  (t5 说 35,503 ?", (raw["B"] - raw["A"]) == -35503, ")")

print()
print("=" * 78)
print("4) input tokens / 采用率 复核")
print("=" * 78)
it = defaultdict(int)
adopt = defaultdict(int)
tot = defaultdict(int)
for f in files:
    d = json.loads(f.read_text(encoding="utf-8"))
    arm = d["metrics"]["label"].split("-")[1]
    it[arm] += d["metrics"].get("input_tokens") or 0
    tot[arm] += 1
    if any(e.get("type") == "tool_use" and e.get("tool") == "mcp__code-tools__repo_map" for e in d["events"]):
        adopt[arm] += 1
print(f"input tokens: A={it['A']:,}  B={it['B']:,}   (报告 82,397 / 72,819)")
print(f"采用率: A={adopt['A']}/{tot['A']}   B={adopt['B']}/{tot['B']}")