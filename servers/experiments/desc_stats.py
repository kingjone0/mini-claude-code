"""「工具描述」实验的统计量：采用率、Fisher 精确检验、工具构成。

只算 §4 判定规则用得到的量，外加两组事后观察（在输出里明确标注为事后）。

用法：
    python desc_stats.py
"""
import json
import math
import pathlib
import re
import sys

# ⚠️ 必须在任何 print 之前把输出切到 UTF-8（CLAUDE.md 陷阱 16）。
# 本脚本第一版就是漏了这一行：数字全对，中文全变成 U+FFFD —— 落盘的证据里标签没了。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
from agent.config import force_utf8_output  # noqa: E402

force_utf8_output()

OUT = pathlib.Path(__file__).resolve().parent / "out"
PAT = re.compile(r"agent-desc-ab-([ABC])-([0-9])\.json$")
ARMS = ("A", "B", "C")
REPO_MAP = "mcp__code-tools__repo_map"


def fisher_two_sided(a, b, c, d):
    """2x2 表的 Fisher 精确检验（双侧），用 math.comb 精确算，不依赖 scipy。

    表：
        a  b
        c  d
    """
    n = a + b + c + d
    r1, r2 = a + b, c + d
    c1 = a + c

    def p_of(k):
        # 第 1 行取到 k 个"命中"的概率
        return math.comb(r1, k) * math.comb(r2, c1 - k) / math.comb(n, c1)

    lo = max(0, c1 - r2)
    hi = min(r1, c1)
    p_obs = p_of(a)
    total = 0.0
    for k in range(lo, hi + 1):
        p = p_of(k)
        if p <= p_obs * (1 + 1e-9):
            total += p
    return min(total, 1.0)


def fisher_one_sided_greater(a, b, c, d):
    """单侧：第 1 组命中率 > 第 2 组。"""
    n = a + b + c + d
    r1, r2 = a + b, c + d
    c1 = a + c
    lo = max(0, c1 - r2)
    return sum(math.comb(r1, k) * math.comb(r2, c1 - k) / math.comb(n, c1)
               for k in range(a, min(r1, c1) + 1))


def load():
    runs = []
    for p in sorted(OUT.glob("agent-desc-ab-*.json")):
        m = PAT.search(p.name)
        if not m:
            continue
        arm, idx = m.group(1), int(m.group(2))
        data = json.loads(p.read_text(encoding="utf-8"))
        events = data.get("events") or []
        steps = ((data.get("trace") or {}).get("steps")) or []
        metrics = data.get("metrics") or {}

        # 口径一：events 的 tool_use（§3 的主口径）
        ev_uses = [e for e in events if e.get("type") == "tool_use"]
        ev_repo = [e for e in ev_uses if e.get("tool") == REPO_MAP]
        # 口径二：trace 的 tool_call（§3 的字面写法）
        tr_calls = [s for s in steps if s.get("type") == "tool_call"]
        tr_repo = [c for c in tr_calls if c.get("tool") == REPO_MAP]
        # 口径三：metrics 自报
        mt_repo = metrics.get("repo_map_count")

        # repo_map 的返回结果（按 tool_use_id 配对，不能按顺序猜）
        by_id = {s.get("tool_use_id"): s for s in steps if s.get("type") == "tool_result"}
        outcomes = []
        for c in tr_repo:
            r = by_id.get(c.get("tool_use_id"))
            outcomes.append({
                "found": r is not None,
                "is_error": None if r is None else r.get("is_error"),
                "chars": None if r is None else r.get("chars"),
            })

        # 每次运行实际调过的工具（按 trace 的 tool_call 计）
        counts = {}
        for c in tr_calls:
            name = (c.get("tool") or "").replace("mcp__code-tools__", "")
            counts[name] = counts.get(name, 0) + 1

        runs.append({
            "file": p.name, "arm": arm, "n": idx,
            "ev_repo": len(ev_repo), "tr_repo": len(tr_repo), "mt_repo": mt_repo,
            "adopted": len(ev_repo) > 0,
            "outcomes": outcomes, "counts": counts,
            "tool_calls": len(tr_calls),
            "success": metrics.get("success"),
        })
    return runs


def main():
    runs = load()
    print(f"命中产物: {len(runs)} 份")
    if len(runs) != 24:
        print("!! 不是 24 份，数字不可用")
        return 1

    # --- 三口径是否一致 ---
    print("\n=== 三口径一致性（events / trace / metrics）===")
    bad = 0
    for r in runs:
        if not (r["ev_repo"] == r["tr_repo"] == (r["mt_repo"] or 0)):
            print(f"  不一致 {r['file']}: events={r['ev_repo']} trace={r['tr_repo']} metrics={r['mt_repo']}")
            bad += 1
    print(f"  不一致条数: {bad}")

    # --- 采用率 ---
    print("\n=== 采用率（主口径 = events 的 repo_map tool_use > 0）===")
    adopt = {}
    for arm in ARMS:
        rs = [r for r in runs if r["arm"] == arm]
        k = sum(1 for r in rs if r["adopted"])
        adopt[arm] = (k, len(rs))
        print(f"  臂 {arm}: {k}/{len(rs)}  = {k / len(rs):.1%}   "
              f"（trace 口径 {sum(1 for r in rs if r['tr_repo'] > 0)}/{len(rs)}，"
              f"metrics 口径 {sum(1 for r in rs if (r['mt_repo'] or 0) > 0)}/{len(rs)}）")

    print("\n=== 两两 Fisher 精确检验（双侧 / 单侧『前者更高』）===")
    for x, y in (("A", "B"), ("A", "C"), ("B", "C")):
        ax, an = adopt[x]
        ay, an_ = adopt[y]
        table = (ax, an - ax, ay, an_ - ay)
        two = fisher_two_sided(*table)
        one_xy = fisher_one_sided_greater(*table)
        one_yx = fisher_one_sided_greater(ay, an_ - ay, ax, an - ax)
        print(f"  {x}({ax}/{an}) vs {y}({ay}/{an_}):  双侧 p={two:.4f}   "
              f"「{x} 更高」单侧 p={one_xy:.4f}   「{y} 更高」单侧 p={one_yx:.4f}")

    # --- repo_map 的返回结果 ---
    print("\n=== 每一次 repo_map 调用的返回结果（按 trace 的 tool_use_id 配对）===")
    allcalls = [o for r in runs for o in r["outcomes"]]
    print(f"  调用总数: {len(allcalls)}")
    print(f"  配对到结果: {sum(1 for o in allcalls if o['found'])}")
    print(f"  is_error=True: {sum(1 for o in allcalls if o['is_error'])}")
    chars = sorted({o["chars"] for o in allcalls if o["chars"] is not None})
    print(f"  返回字符数取值: {chars}")

    # --- 各臂工具构成 + 事后观察 ---
    print("\n=== 工具构成（每轮均值）===")
    names = ["list_files", "search_code", "read_file", "repo_map", "apply_patch", "run_tests"]
    print(f"  {'组':<14} {'n':>3} " + " ".join(f"{n:>11}" for n in names) + f" {'总调用':>8}")
    for arm in ARMS:
        rs = [r for r in runs if r["arm"] == arm]
        row = [sum(r["counts"].get(n, 0) for r in rs) / len(rs) for n in names]
        tot = sum(r["tool_calls"] for r in rs) / len(rs)
        print(f"  臂 {arm:<11} {len(rs):>3} " + " ".join(f"{v:>11.2f}" for v in row) + f" {tot:>8.2f}")

    print("\n--- 事后观察（§6.4 说成本另行记录；此处分组**不是随机分组**，采用它的运行本身可能就不同）---")
    for label, sel in (("采用了 repo_map", lambda r: r["adopted"]),
                       ("没采用", lambda r: not r["adopted"])):
        rs = [r for r in runs if sel(r)]
        row = [sum(r["counts"].get(n, 0) for r in rs) / len(rs) for n in names]
        tot = sum(r["tool_calls"] for r in rs) / len(rs)
        print(f"  {label:<14} {len(rs):>3} " + " ".join(f"{v:>11.2f}" for v in row) + f" {tot:>8.2f}")

    print("\n=== 成功率 ===")
    for arm in ARMS:
        rs = [r for r in runs if r["arm"] == arm]
        print(f"  臂 {arm}: 成功 {sum(1 for r in rs if r['success'])}/{len(rs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
