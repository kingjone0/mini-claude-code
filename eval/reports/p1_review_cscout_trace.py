"""t15 追因：C 臂 scout 中位数不一致（declared 149632/0.415 vs 重算 81024/0.2505）。

只读。逐条列出：
  ① 每个 C run 在 run 窗口内的 scout 记录明细（task_id/k/group/input_uncached/cache_read/cost）
  ② 每个 C run 的 parent usage 行里 policy_scout_* 字段的值
  ③ 两种口径下单 run 的 scout 汇总与 5 值集合
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import statistics

ROOT = r"E:\aicoding\mini-claude-code"
BATCH = r"E:\aicoding\eval-runs\p1-20261013T122356-t20-batch"
USAGE_DIR = os.path.join(ROOT, "servers", "experiments", "out")
OUT = os.path.join(ROOT, "eval", "reports", "p1-review-cscout-trace.txt")
LINES: list[str] = []


def say(s=""):
    LINES.append(s)


def load_jsonl(p):
    if not os.path.exists(p):
        return []
    return [json.loads(x) for x in io.open(p, encoding="utf-8", errors="replace") if x.strip()]


def ts_epoch(ts):
    try:
        return dt.datetime.fromisoformat(ts).timestamp()
    except Exception:  # noqa: BLE001
        return None


def med(xs):
    return round(statistics.median(xs), 4) if xs else None


state = load_jsonl(os.path.join(BATCH, "triple_state.jsonl"))
usage = [u for u in load_jsonl(os.path.join(USAGE_DIR, "usage.jsonl"))
         if str(u.get("label") or "").startswith("p1-")]
scout = [s for s in load_jsonl(os.path.join(USAGE_DIR, "policy_scout_usage.jsonl"))
         if str(s.get("label") or "").startswith("p1-")]
by_label_u, by_label_s = {}, {}
for u in usage:
    by_label_u.setdefault(u["label"], []).append(u)
for s in scout:
    by_label_s.setdefault(s["label"], []).append(s)

C_POLICY_KEYS = ["policy_scout_input_uncached", "policy_scout_cache_read",
                 "policy_scout_cache_creation", "policy_scout_cost_usd",
                 "policy_scout_sessions", "policy_scout_elapsed_s",
                 "policy_k_groups", "policy_groups_final", "policy_groups_completed",
                 "policy_groups_truncated", "policy_groups_undiagnosed",
                 "policy_index_chars", "policy_worktrees_after_dispatch"]

say("=" * 100)
say("# C 臂 scout 中位数追因（t15 reviewer）")
say("=" * 100)
rows = [r for r in state if r["arm"] == "C"]
for r in rows:
    st = r["started_at_epoch"]
    say()
    say(f"### {r['run_id']}  verdict={r['verdict_class']} resolved={r['resolved']} "
        f"started_at_epoch={st} ({dt.datetime.fromtimestamp(st).isoformat()})")
    # ① run 窗口内 scout 记录明细
    recs = [s for s in by_label_s.get(r["label"], []) if (ts_epoch(s.get("ts")) or 0) >= st]
    allrecs = by_label_s.get(r["label"], [])
    say(f"  scout 记录：label 全量 {len(allrecs)} 条，run 窗口内 {len(recs)} 条")
    for s in recs:
        say(f"    WINDOW ts={s.get('ts')} task_id={s.get('task_id')} k={s.get('k')} "
            f"group={s.get('group')} iu={s.get('input_uncached')} cr={s.get('cache_read')} "
            f"cost={s.get('total_cost_usd')} turns={s.get('num_turns')} "
            f"elapsed={s.get('elapsed_s')} scope={str(s.get('scope'))[:24]}")
    for s in allrecs:
        if s in recs:
            continue
        say(f"    (窗口外) ts={s.get('ts')} task_id={s.get('task_id')} k={s.get('k')} "
            f"group={s.get('group')}")
    wi = sum(int(x.get("input_uncached") or 0) for x in recs)
    wc = sum(int(x.get("cache_read") or 0) for x in recs)
    wco = round(sum(float(x.get("total_cost_usd") or 0) for x in recs), 6)
    say(f"  窗口内汇总: iu={wi} cr={wc} cost={wco}")
    # ② parent usage 行的 policy_scout_* 字段
    pu = [u for u in by_label_u.get(r["label"], []) if (ts_epoch(u.get("ts")) or 0) >= st]
    if pu:
        u = pu[-1]
        say(f"  parent usage 行 ts={u.get('ts')} input_uncached={u.get('input_uncached')} "
            f"cache_read={u.get('cache_read')} cost={u.get('total_cost_usd')} "
            f"turns={u.get('num_turns')}")
        for k in C_POLICY_KEYS:
            if k in u:
                say(f"    {k}={u.get(k)}")
    else:
        say("  parent usage 行：run 窗口内无（超时行）")

# ③ 两种口径的 5 值集合
say()
say("=" * 100)
say("# 5 值集合（C 非 timeout 行）")
valid = [r for r in rows if r["verdict_class"] != "timeout"]
for field, key in (("input_uncached", "input_uncached"), ("cache_read", "cache_read"),
                   ("total_cost_usd", "total_cost_usd")):
    vals = []
    for r in valid:
        st = r["started_at_epoch"]
        recs = [s for s in by_label_s.get(r["label"], []) if (ts_epoch(s.get("ts")) or 0) >= st]
        v = (sum(int(x.get(key) or 0) for x in recs) if field != "total_cost_usd"
             else round(sum(float(x.get(key) or 0) for x in recs), 6))
        vals.append((r["run_id"].split("-", 2)[2], v))
    nums = [v for _n, v in vals]
    say(f"- scout {field}: {vals}")
    say(f"    sorted={sorted(nums)}  median={med(nums)}  range={(min(nums), max(nums))}")

# ④ 父层 policy_scout_* 口径的 5 值集合（另一种可能的 analyst 口径）
say()
for k in ("policy_scout_input_uncached", "policy_scout_cache_read", "policy_scout_cost_usd"):
    vals = []
    for r in valid:
        st = r["started_at_epoch"]
        pu = [u for u in by_label_u.get(r["label"], []) if (ts_epoch(u.get("ts")) or 0) >= st]
        vals.append((r["run_id"].split("-", 2)[2], (pu[-1].get(k) if pu else None)))
    nums = [v for _n, v in vals if isinstance(v, (int, float))]
    say(f"- parent 行 {k}: {vals}")
    if nums:
        say(f"    sorted={sorted(nums)}  median={med(nums)}  range={(min(nums), max(nums))}")

io.open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(LINES) + "\n")
print("c-scout trace written:", OUT)
