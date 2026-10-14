"""t4 对抗性复核：独立重算 AB-REPO-MAP.md 的数字（不依赖 t3 的 ab_adoption_audit.py）。

刻意不复用 context_report.py / ab_adoption_audit.py —— 那两条路径是 t3 自己的，
万一它们共享同一个错误假设，交叉核对就变成自证。这里从 12 份 JSON 直接重算。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
import agent.config  # noqa: E402

agent.config.force_utf8_output()

OUT = PROJECT_ROOT / "servers" / "experiments" / "out"

# 窄 glob：只取正式 12 份，绝不碰 t1 试跑与旧数据
files = sorted([p for p in OUT.glob("agent-ab-[AB]-*.json")])
assert len(files) == 12, f"期望 12 份，实得 {len(files)}"

REPO_MAP_TOOL = "mcp__code-tools__repo_map"

adopted = defaultdict(list)      # arm -> [label]
rm_calls = defaultdict(int)      # arm -> repo_map tool_use 数
tool_calls = defaultdict(int)    # arm -> tool_use 总数
chars = defaultdict(int)         # arm -> tool_result chars 合计
chars_by_tool = defaultdict(lambda: defaultdict(int))
in_tok = defaultdict(int)
out_tok = defaultdict(int)
cost = defaultdict(float)
ok = defaultdict(int)
rm_count_metrics = defaultdict(int)
consistency = []

# 单变量性核对
single_var = []

for f in files:
    d = json.loads(f.read_text(encoding="utf-8"))
    m = d["metrics"]
    label = m["label"]
    arm = label.split("-")[1]                       # ab-A-1 -> A
    ev = d["events"]

    tu = [e for e in ev if e.get("type") == "tool_use"]
    tr = [e for e in ev if e.get("type") == "tool_result"]
    rm = [e for e in tu if e.get("tool") == REPO_MAP_TOOL]

    if rm:
        adopted[arm].append(label)
    rm_calls[arm] += len(rm)
    tool_calls[arm] += len(tu)
    for e in tr:
        c = e.get("chars", 0)
        chars[arm] += c
        chars_by_tool[arm][e.get("tool", "?")] += c
    in_tok[arm] += m.get("input_tokens") or 0
    out_tok[arm] += m.get("output_tokens") or 0
    cost[arm] += m.get("cost_usd") or 0.0
    ok[arm] += 1 if m.get("success") else 0
    rm_count_metrics[arm] += m.get("repo_map_count") or 0

    # 一致性：metrics 拷贝 vs events 口径
    if (m.get("repo_map_count") or 0) != len(rm):
        consistency.append(f"{label}: repo_map_count {m.get('repo_map_count')} != events {len(rm)}")
    if (m.get("tool_calls_total") or 0) != len(tu):
        consistency.append(f"{label}: tool_calls_total {m.get('tool_calls_total')} != events {len(tu)}")
    if len(tu) != len(tr):
        consistency.append(f"{label}: tool_use {len(tu)} != tool_result {len(tr)}")

    single_var.append({
        "label": label, "arm": arm,
        "repo_map_default": m.get("repo_map_default"),
        "repo_map_default_source": m.get("repo_map_default_source"),
        "baseline_exit_code": m.get("baseline_exit_code"),
        "final_exit_code": m.get("final_exit_code"),
        "has_fixture_field": "fixture" in m,
        "has_max_turns_field": "max_turns" in m,
        "changed_files": m.get("changed_files"),
    })

print("=" * 78)
print("A) 单变量性核对（每份产物 metrics 实读）")
print("=" * 78)
print(f"{'label':<10}{'arm':<5}{'rm_default':<12}{'source':<30}{'base':<6}{'final':<7}{'fixture字段':<12}{'max_turns字段'}")
for s in single_var:
    print(f"{s['label']:<10}{s['arm']:<5}{str(s['repo_map_default']):<12}"
          f"{str(s['repo_map_default_source']):<30}{str(s['baseline_exit_code']):<6}"
          f"{str(s['final_exit_code']):<7}{str(s['has_fixture_field']):<12}{s['has_max_turns_field']}")

print()
print("baseline_exit_code 全为 1 ?",
      all(s["baseline_exit_code"] == 1 for s in single_var))
print("final_exit_code 全为 0 ?",
      all(s["final_exit_code"] == 0 for s in single_var))
print("source 全为 env:AGENT_REPO_MAP_DEFAULT ?",
      all(s["repo_map_default_source"] == "env:AGENT_REPO_MAP_DEFAULT" for s in single_var))
print("有任何一份含 fixture 字段 ?", any(s["has_fixture_field"] for s in single_var))
print("有任何一份含 max_turns 字段 ?", any(s["has_max_turns_field"] for s in single_var))
print("changed_files 是否 12/12 都是单文件 more_itertools/more.py ?",
      all(s["changed_files"] == ["more_itertools/more.py"] for s in single_var))

print()
print("=" * 78)
print("B) 采用率（判据：events 里 tool_use 且 tool == repo_map）")
print("=" * 78)
for arm in ("A", "B"):
    n = len([s for s in single_var if s["arm"] == arm])
    print(f"臂 {arm}: {len(adopted[arm])}/{n} = {len(adopted[arm])/n*100:.1f}%   采用于 {adopted[arm]}")

print()
print("=" * 78)
print("C) 成本总量（按臂分组）")
print("=" * 78)
print(f"{'指标':<14}{'A(symbols)':>14}{'B(summary)':>14}{'B/A':>10}")
for name, a, b in (
    ("工具字符", chars["A"], chars["B"]),
    ("工具调用", tool_calls["A"], tool_calls["B"]),
    ("repo_map调用", rm_calls["A"], rm_calls["B"]),
    ("input tokens", in_tok["A"], in_tok["B"]),
    ("output tokens", out_tok["A"], out_tok["B"]),
):
    pct = f"{(b/a-1)*100:+.1f}%" if a else "n/a"
    print(f"{name:<14}{a:>14,}{b:>14,}{pct:>10}")
print(f"{'cost_usd':<14}{cost['A']:>14.4f}{cost['B']:>14.4f}{(cost['B']/cost['A']-1)*100:>9.1f}%")
print(f"{'success':<14}{ok['A']:>14}{ok['B']:>14}")

print()
print("=" * 78)
print("D) 工具返回字符按工具拆分")
print("=" * 78)
allt = sorted(set(chars_by_tool["A"]) | set(chars_by_tool["B"]))
print(f"{'tool':<32}{'A':>12}{'B':>12}{'B-A':>12}")
for t in allt:
    a, b = chars_by_tool["A"][t], chars_by_tool["B"][t]
    print(f"{t:<32}{a:>12,}{b:>12,}{b-a:>12,}")
print(f"{'合计':<32}{chars['A']:>12,}{chars['B']:>12,}{chars['B']-chars['A']:>12,}")

print()
print("=" * 78)
print("E) 一致性自检")
print("=" * 78)
print("不一致项数:", len(consistency))
for c in consistency:
    print("  *", c)
print("metrics.repo_map_count 按臂合计: A=%d B=%d（events 口径 A=%d B=%d）"
      % (rm_count_metrics["A"], rm_count_metrics["B"], rm_calls["A"], rm_calls["B"]))

print()
print("=" * 78)
print("F) 报告未主张、但值得复核的一个算术疑点：调用次数")
print("=" * 78)
for arm in ("A", "B"):
    per = defaultdict(int)
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        if d["metrics"]["label"].split("-")[1] != arm:
            continue
        for e in d["events"]:
            if e.get("type") == "tool_use":
                per[e.get("tool", "?")] += 1
    print(f"臂 {arm} 逐工具调用次数:", dict(sorted(per.items())))
    print(f"       工具调用合计 = {sum(per.values())}")