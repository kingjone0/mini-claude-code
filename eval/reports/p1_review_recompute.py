"""t15 独立审查：从产物重算 P1 关键数字 + C 臂证据 per-run 可复算性审计（v2）。

只读；不复用 analyst 的 p1_recompute.py。输出：
  eval/reports/p1-review-recompute.txt   人类可读
  eval/reports/p1-review-recompute.json  机器可读
控制台只打印 ASCII 摘要。

v2 相比 v1 的修正：
  · parent 的 input_uncached 用 usage 行里的显式字段（v1 误用 input-cache_read）
  · scout 侧同时给出「label 全量」与「run 窗口内」两种口径，暴露 label 复用导致的串行风险
  · index_chars / worktrees_after_dispatch 用递归搜索（v1 只查顶层 policy_* 名字，漏了）
  · 增加 declared-vs-recomputed 对照表（§4 要求 ≥8 个数字）
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import statistics
import sys
from collections import Counter

ROOT = r"E:\aicoding\mini-claude-code"
BATCH = r"E:\aicoding\eval-runs\p1-20261013T122356-t20-batch"
USAGE_DIR = os.path.join(ROOT, "servers", "experiments", "out")
OUT_TXT = os.path.join(ROOT, "eval", "reports", "p1-review-recompute.txt")
OUT_JSON = os.path.join(ROOT, "eval", "reports", "p1-review-recompute.json")

# ---- 从 eval/reports/p1-analysis.md 逐字转录的「被审数字」（§2/§3 表） -------------
DECLARED = {
    "A.rate": "6/9", "B.rate": "8/9", "C.rate": "3/5",
    "A.parent.input_uncached.med": 36681, "A.parent.input_uncached.range": (13877, 50097),
    "B.parent.input_uncached.med": 38283, "B.parent.input_uncached.range": (23515, 113906),
    "C.parent.input_uncached.med": 25005, "C.parent.input_uncached.range": (8581, 113077),
    "A.parent.cache_read.med": 1848704, "A.parent.cache_read.range": (110464, 2915200),
    "B.parent.cache_read.med": 2138752, "B.parent.cache_read.range": (218368, 9045504),
    "C.parent.cache_read.med": 899072, "C.parent.cache_read.range": (34944, 12958848),
    "A.parent.cost.med": 1.984, "B.parent.cost.med": 2.254, "C.parent.cost.med": 1.344,
    "A.turns.med": 68, "A.turns.range": (17, 107),
    "B.turns.med": 70, "B.turns.range": (26, 136),
    "C.turns.med": 49, "C.turns.range": (10, 155),
    "A.wall.med": 247.81, "A.wall.range": (42.49, 622.20),
    "B.wall.med": 304.64, "B.wall.range": (67.14, 641.52),
    "C.wall.med": 357.33, "C.wall.range": (93.43, 849.83),
    "C.scout.input_uncached.med": 23003, "C.scout.input_uncached.range": (18550, 29324),
    "C.scout.cache_read.med": 149632, "C.scout.cache_read.range": (44544, 302464),
    "C.scout.cost.med": 0.415, "C.scout.cost.range": (0.209, 0.743),
    "C.scout.sample": "n=5（C 非 timeout 行）",
}

LINES: list[str] = []


def say(s=""):
    LINES.append(s)


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 4) if xs else None


def rng(xs):
    xs = [x for x in xs if x is not None]
    return [min(xs), max(xs)] if xs else None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ts_epoch(ts):
    try:
        return dt.datetime.fromisoformat(ts).timestamp()
    except Exception:  # noqa: BLE001
        return None


def find_keys(obj, names, path="$"):
    """递归找出 obj 里所有名为 names 的键，返回 [(path, value)]。"""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names:
                found.append((f"{path}.{k}", v))
            found.extend(find_keys(v, names, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(find_keys(v, names, f"{path}[{i}]"))
    return found


def main():
    state_path = os.path.join(BATCH, "triple_state.jsonl")
    state = load_jsonl(state_path)
    say("# t15 独立重算与审计（reviewer 自写脚本，不复用 p1_recompute.py）")
    say()
    say(f"批次：{BATCH}")
    say(f"triple_state.jsonl：{len(state)} 行  bytes={os.path.getsize(state_path)}  "
        f"sha256={sha256_file(state_path)}")

    inst_by_id = {}
    for r in load_jsonl(os.path.join(ROOT, "eval", "instances", "pilot-12.jsonl")):
        inst_by_id[r["instance_id"]] = r

    arms = ["A", "B", "C"]
    DENOM = ("agent_result", "empty_patch", "patch_conflict")

    # ---------------------------------------------------------------- 1 主口径
    say()
    say("## 1 逐臂主口径（分母 = agent_result + empty_patch + patch_conflict）")
    say()
    say("| arm | rows | agent_result | timeout | infra | 分母 | resolved | rate |")
    say("|---|---:|---:|---:|---:|---:|---:|---:|")
    main_tbl = {}
    for arm in arms:
        rows = [r for r in state if r["arm"] == arm]
        cnt = Counter(r["verdict_class"] for r in rows)
        den = sum(cnt.get(k, 0) for k in DENOM)
        res = sum(1 for r in rows if r.get("resolved"))
        main_tbl[arm] = {"rows": len(rows), "counts": dict(cnt), "denominator": den,
                         "resolved": res, "rate": round(res / den, 4) if den else None}
        say(f"| {arm} | {len(rows)} | {cnt.get('agent_result',0)} | {cnt.get('timeout',0)} | "
            f"{cnt.get('infra_error',0)} | {den} | {res} | {main_tbl[arm]['rate']} |")

    say()
    say("逐实例配对（verdict_class/resolved）：")
    pairs = {}
    for r in state:
        pairs.setdefault(r["instance_id"], {})[r["arm"]] = r
    say("| instance | A | B | C |")
    say("|---|---|---|---|")
    for iid in sorted(pairs):
        d = pairs[iid]
        cells = []
        for arm in arms:
            row = d.get(arm)
            cells.append("缺失" if row is None else
                         f"{row['verdict_class']}/{'T' if row.get('resolved') else 'F'}")
        say(f"| {iid} | {cells[0]} | {cells[1]} | {cells[2]} |")

    # ---------------------------------------------------------------- 2 用量池
    usage = load_jsonl(os.path.join(USAGE_DIR, "usage.jsonl"))
    scout = load_jsonl(os.path.join(USAGE_DIR, "policy_scout_usage.jsonl"))
    p1_usage = [u for u in usage if str(u.get("label") or "").startswith("p1-")]
    p1_scout = [s for s in scout if str(s.get("label") or "").startswith("p1-")]
    usage_by_label = {}
    for u in p1_usage:
        usage_by_label.setdefault(u["label"], []).append(u)
    scout_by_label = {}
    for s in p1_scout:
        scout_by_label.setdefault(s["label"], []).append(s)

    say()
    say("## 2 用量池与 label 复用风险")
    say()
    ctr = Counter(u["label"] for u in p1_usage)
    multi = {k: v for k, v in ctr.items() if v > 1}
    say(f"usage.jsonl 总 {len(usage)} 行 / p1- {len(p1_usage)} 行；"
        f"label 出现 >1 次的 {len(multi)} 个：{json.dumps(multi, ensure_ascii=False)}")
    sctr = Counter(s["label"] for s in p1_scout)
    smulti = {k: v for k, v in sctr.items() if v > 1}
    say(f"policy_scout_usage.jsonl 总 {len(scout)} 行 / p1- {len(p1_scout)} 行；"
        f"label 出现 >1 次的 {len(smulti)} 个：{json.dumps(smulti, ensure_ascii=False)}")
    say("⇒ 同一 label 被历次接线探针与正式批次共用，**任何 label-only 匹配都会串入探针行**；"
        "必须再按 run 窗口（ts >= started_at_epoch）过滤。")

    # parent usage 的三种选择口径
    def select_parent(row, mode):
        recs = usage_by_label.get(row["label"], [])
        if not recs:
            return None
        if mode == "label_last":
            return recs[-1]
        if mode == "label_first":
            return recs[0]
        if mode == "window":
            st = row.get("started_at_epoch")
            inw = [u for u in recs if (ts_epoch(u.get("ts")) or 0) >= st]
            return inw[-1] if inw else None
        if mode == "label_all":
            return recs
        raise ValueError(mode)

    say()
    say("## 3 parent 层指标：三种匹配口径对照")
    say()
    say("| mode | arm | n | input_uncached 中位(极差) | cache_read 中位(极差) | cost 中位 | turns 中位 |")
    say("|---|---|---:|---|---|---|---|")
    sel_modes = {}
    for mode in ("window", "label_last", "label_first"):
        tbl = {}
        for arm in arms:
            rows = [r for r in state if r["arm"] == arm]
            iu, cr, co, tu = [], [], [], []
            n = 0
            for r in rows:
                u = select_parent(r, mode)
                if not u:
                    continue
                n += 1
                iu.append(u.get("input_uncached"))
                cr.append(u.get("cache_read"))
                co.append(u.get("total_cost_usd"))
                tu.append(u.get("num_turns"))
            tbl[arm] = {"n": n, "iu": med(iu), "iu_r": rng(iu), "cr": med(cr),
                        "cr_r": rng(cr), "co": med(co), "co_r": rng(co),
                        "tu": med(tu), "tu_r": rng(tu)}
            say(f"| {mode} | {arm} | {n} | {tbl[arm]['iu']} {tbl[arm]['iu_r']} | "
                f"{tbl[arm]['cr']} {tbl[arm]['cr_r']} | {tbl[arm]['co']} | {tbl[arm]['tu']} |")
        sel_modes[mode] = tbl

    # window 口径下逐行明细（用于人工核对）
    say()
    say("window 口径逐行 parent usage：")
    say("| run | verdict | input_uncached | cache_read | cost | turns | wall_s |")
    say("|---|---|---:|---:|---:|---:|---:|")
    per_row_parent = {}
    for r in state:
        u = select_parent(r, "window")
        per_row_parent[r["run_id"]] = u
        if u:
            say(f"| {r['run_id'][:55]} | {r['verdict_class']} | {u.get('input_uncached')} | "
                f"{u.get('cache_read')} | {u.get('total_cost_usd')} | {u.get('num_turns')} | "
                f"{r.get('wall_s')} |")
        else:
            say(f"| {r['run_id'][:55]} | {r['verdict_class']} | (无) | (无) | (无) | (无) | "
                f"{r.get('wall_s')} |")
    wall_tbl = {}
    for arm in arms:
        ws = [r.get("wall_s") for r in state if r["arm"] == arm]
        wv = [r.get("wall_s") for r in state
              if r["arm"] == arm and r["verdict_class"] in DENOM]
        wall_tbl[arm] = {"med_all": med(ws), "range_all": rng(ws),
                         "med": med(wv), "range": rng(wv), "n_valid": len(wv)}
    say()
    say("wall_s（row=全部行 / valid=仅计入分母的行）：")
    say("| arm | 全部行 中位(极差) | valid 中位(极差) | valid n |")
    say("|---|---|---|---:|")
    for arm in arms:
        t = wall_tbl[arm]
        say(f"| {arm} | {t['med_all']} {t['range_all']} | {t['med']} {t['range']} | {t['n_valid']} |")

    # e2e（parent + scout 单列加总）
    say()
    say("## 3b 端到端（parent + scout）")
    say()
    say("| arm | n | e2e input_uncached 中位(极差) | e2e cost 中位(极差) |")
    say("|---|---:|---|---|")
    e2e_tbl = {}
    for arm in arms:
        rows = [r for r in state if r["arm"] == arm]
        iu, co = [], []
        for r in rows:
            u = select_parent(r, "window")
            if not u:
                continue
            st = r.get("started_at_epoch")
            srecs = [s for s in scout_by_label.get(r["label"], [])
                     if (ts_epoch(s.get("ts")) or 0) >= st] if arm == "C" else []
            si = sum(int(x.get("input_uncached") or 0) for x in srecs)
            sco = sum(float(x.get("total_cost_usd") or 0) for x in srecs)
            iu.append(int(u.get("input_uncached") or 0) + si)
            co.append(round(float(u.get("total_cost_usd") or 0) + sco, 6))
        e2e_tbl[arm] = {"n": len(iu), "iu": med(iu), "iu_r": rng(iu),
                        "co": med(co), "co_r": rng(co)}
        say(f"| {arm} | {len(iu)} | {e2e_tbl[arm]['iu']} {e2e_tbl[arm]['iu_r']} | "
            f"{e2e_tbl[arm]['co']} {e2e_tbl[arm]['co_r']} |")

    # ---------------------------------------------------------------- 4 scout
    say()
    say("## 4 C 臂 scout 侧：label 全量 vs run 窗口（单列）")
    say()
    say("| C run | verdict | 口径 | scout 条数 | sum input_uncached | sum cache_read | sum cost |")
    say("|---|---|---|---:|---:|---:|---:|")
    scout_tbl = {}
    for r in state:
        if r["arm"] != "C":
            continue
        recs_all = scout_by_label.get(r["label"], [])
        st = r.get("started_at_epoch")
        recs_win = [s for s in recs_all if (ts_epoch(s.get("ts")) or 0) >= st]
        entry = {}
        for mode, recs in (("label_all", recs_all), ("window", recs_win)):
            si = sum(int(x.get("input_uncached") or 0) for x in recs)
            sc = sum(int(x.get("cache_read") or 0) for x in recs)
            sco = round(sum(float(x.get("total_cost_usd") or 0) for x in recs), 6)
            entry[mode] = {"n": len(recs), "iu": si, "cr": sc, "cost": sco}
            say(f"| {r['run_id'][:55]} | {r['verdict_class']} | {mode} | {len(recs)} | {si} | "
                f"{sc} | {sco} |")
        scout_tbl[r["run_id"]] = {"verdict_class": r["verdict_class"], **entry}

    say()
    for mode in ("window", "label_all"):
        cvalid = [v[mode] for v in scout_tbl.values() if v["verdict_class"] != "timeout"]
        say(f"- C 非 timeout 行（{mode}） scout 中位：input_uncached={med([v['iu'] for v in cvalid])} "
            f"极差={rng([v['iu'] for v in cvalid])}；cache_read={med([v['cr'] for v in cvalid])} "
            f"极差={rng([v['cr'] for v in cvalid])}；cost={med([v['cost'] for v in cvalid])} "
            f"极差={rng([v['cost'] for v in cvalid])}")
    say()
    say("被审报告声明的 C scout：中位 input_uncached="
        f"{DECLARED['C.scout.input_uncached.med']} 极差={DECLARED['C.scout.input_uncached.range']}；"
        f"cache_read={DECLARED['C.scout.cache_read.med']} 极差={DECLARED['C.scout.cache_read.range']}；"
        f"cost={DECLARED['C.scout.cost.med']} 极差={DECLARED['C.scout.cost.range']}（{DECLARED['C.scout.sample']}）")

    # ---------------------------------------------------------------- 5 C 证据
    say()
    say("## 5 C 臂 per-run 证据可复算性（只读该 run 的 artifacts 目录）")
    say()
    say("| C run | verdict | K_expected | 证据 | terminal 组数 | 组名 | k | index_chars | worktrees |")
    say("|---|---|---:|---|---:|---|---:|---:|---:|")
    c_audit = []
    for r in state:
        if r["arm"] != "C":
            continue
        art = os.path.join(os.path.dirname(r["workspace"]), "artifacts")
        iso = os.path.join(art, "policy_evidence_isolated.json")
        inst = inst_by_id.get(r["instance_id"], {})
        k_expected = len({n.split("::")[0] for n in (inst.get("FAIL_TO_PASS") or [])})
        rec = {"run_id": r["run_id"], "iso_path": iso, "iso_exists": os.path.exists(iso),
               "k_expected": k_expected, "artifacts_dir": art,
               "iso_bytes": os.path.getsize(iso) if os.path.exists(iso) else None,
               "iso_sha256": sha256_file(iso) if os.path.exists(iso) else None}
        if not os.path.exists(iso):
            c_audit.append(rec)
            say(f"| {r['run_id'][:55]} | {r['verdict_class']} | {k_expected} | 缺 | - | - | - | - | - |")
            continue
        d = json.load(io.open(iso, encoding="utf-8"))
        drows = d.get("dispatch_rows") or []
        terminal = [x for x in drows if x.get("event") in ("completed", "undiagnosed", "failed")]
        groups = sorted({x.get("group") for x in terminal if x.get("group")})
        ks = sorted({x.get("k") for x in terminal if x.get("k") is not None})
        # 递归搜 index_chars / worktrees_after_dispatch（不只顶层 policy_* 名）
        idx_hits = find_keys(d, {"index_chars", "policy_index_chars"})
        wt_hits = find_keys(d, {"worktrees_after_dispatch", "policy_worktrees_after_dispatch"})
        idx = idx_hits[0][1] if idx_hits else None
        wt = wt_hits[0][1] if wt_hits else None
        rec.update({"terminal_rows": len(terminal), "groups": groups,
                    "group_count": len(groups), "k_values": ks,
                    "index_chars": idx, "index_chars_paths": [p for p, _ in idx_hits],
                    "worktrees_after_dispatch": wt,
                    "worktrees_paths": [p for p, _ in wt_hits],
                    "dispatch_rows": len(drows),
                    "arm_report_rows": len(d.get("arm_report_rows") or []),
                    "gate": {"group_count==K_expected": len(groups) == k_expected,
                             "index_chars<=2000": (idx is not None and idx <= 2000),
                             "worktrees==1": (wt == 1)}})
        g = rec["gate"]
        say(f"| {r['run_id'][:55]} | {r['verdict_class']} | {k_expected} | 有 | {len(groups)} | "
            f"{','.join(groups)} | {ks} | {idx} | {wt} |")
        c_audit.append(rec)

    # ---------------------------------------------------------------- 6 保真
    say()
    say("## 6 抽取保真性（isolated vs 共享 policy_dispatch.jsonl）")
    say()
    shared = load_jsonl(os.path.join(USAGE_DIR, "policy_dispatch.jsonl"))
    shared_by_label = {}
    for s in shared:
        shared_by_label.setdefault(s.get("label"), []).append(s)
    fidelity = []
    for r in state:
        if r["arm"] != "C":
            continue
        iso = os.path.join(os.path.dirname(r["workspace"]), "artifacts",
                           "policy_evidence_isolated.json")
        if not os.path.exists(iso):
            continue
        d = json.load(io.open(iso, encoding="utf-8"))
        iso_rows = d.get("dispatch_rows") or []
        sh = shared_by_label.get(r["label"], [])
        iso_keys = sorted((x.get("ts"), x.get("k"), x.get("group"), x.get("event"))
                          for x in iso_rows)
        sh_keys = sorted((x.get("ts"), x.get("k"), x.get("group"), x.get("event")) for x in sh)
        subset = all(k in sh_keys for k in iso_keys)
        fidelity.append({"run_id": r["run_id"], "iso_rows": len(iso_keys),
                         "shared_label_rows": len(sh_keys), "iso_is_subset_of_shared": subset})
        say(f"- {r['run_id'][:55]}: isolated {len(iso_keys)} 行 ⊆ 共享同 label {len(sh_keys)} 行 "
            f"= {subset}")

    # ---------------------------------------------------------------- 7 超时
    say()
    say("## 7 超时与分类顺序")
    say()
    for r in state:
        if r["verdict_class"] == "timeout":
            say(f"- {r['instance_id']}/{r['arm']} wall_s={r['wall_s']} "
                f"timed_out={r.get('timed_out')} exit_code={r.get('exit_code')} "
                f"judge_exit_code={r.get('judge_exit_code')} stage={r.get('stage')} "
                f"notes={r.get('notes')}")

    # ---------------------------------------------------------------- 8 对照
    say()
    say("## 8 declared vs recomputed（≥8 个关键数字）")
    say()
    say("| # | 数字 | p1-analysis.md | 本次重算（window 口径） | 一致 |")
    say("|---:|---|---|---|---|")
    w = sel_modes["window"]
    checks = []

    def close(a, b, tol=0.0011):
        try:
            return abs(float(a) - float(b)) <= tol
        except (TypeError, ValueError):
            return str(a) == str(b)

    def cmp_row(no, name, declared, got, tol=0.0011):
        ok = close(declared, got, tol)
        checks.append({"no": no, "name": name, "declared": declared, "recomputed": got,
                       "match": ok})
        say(f"| {no} | {name} | {declared} | {got} | {'YES' if ok else 'NO'} |")

    def cmp_range(no, name, declared, got, tol=0.011):
        ok = (len(declared) == len(got)
              and all(close(d, g, tol) for d, g in zip(declared, got)))
        checks.append({"no": no, "name": name, "declared": list(declared),
                       "recomputed": list(got), "match": ok})
        say(f"| {no} | {name} | {list(declared)} | {list(got)} | {'YES' if ok else 'NO'} |")

    cmp_row(1, "A resolved/分母", "6/9", f"{main_tbl['A']['resolved']}/{main_tbl['A']['denominator']}")
    cmp_row(2, "B resolved/分母", "8/9", f"{main_tbl['B']['resolved']}/{main_tbl['B']['denominator']}")
    cmp_row(3, "C resolved/分母", "3/5", f"{main_tbl['C']['resolved']}/{main_tbl['C']['denominator']}")
    cmp_row(4, "A parent input_uncached 中位", 36681, w["A"]["iu"])
    cmp_row(5, "B parent input_uncached 中位", 38283, w["B"]["iu"])
    cmp_row(6, "C parent input_uncached 中位", 25005, w["C"]["iu"])
    cmp_range(7, "A/B/C parent input_uncached 极差", (13877, 50097), w["A"]["iu_r"])
    cmp_range(8, "B parent input_uncached 极差", (23515, 113906), w["B"]["iu_r"])
    cmp_range(9, "C parent input_uncached 极差", (8581, 113077), w["C"]["iu_r"])
    cmp_row(10, "A parent cache_read 中位", 1848704, w["A"]["cr"])
    cmp_row(11, "B parent cache_read 中位", 2138752, w["B"]["cr"])
    cmp_row(12, "C parent cache_read 中位", 899072, w["C"]["cr"])
    cmp_range(13, "A parent cache_read 极差", (110464, 2915200), w["A"]["cr_r"])
    cmp_range(14, "B parent cache_read 极差", (218368, 9045504), w["B"]["cr_r"])
    cmp_range(15, "C parent cache_read 极差", (34944, 12958848), w["C"]["cr_r"])
    cmp_row(16, "A parent cost 中位", 1.984, w["A"]["co"])
    cmp_row(17, "B parent cost 中位", 2.254, w["B"]["co"])
    cmp_row(18, "C parent cost 中位", 1.344, w["C"]["co"])
    cmp_row(19, "A turns 中位", 68, w["A"]["tu"])
    cmp_row(20, "B turns 中位", 70, w["B"]["tu"])
    cmp_row(21, "C turns 中位", 49, w["C"]["tu"])
    cmp_range(22, "A turns 极差", (17, 107), w["A"]["tu_r"])
    cmp_row(23, "A wall 中位（valid）", 247.81, wall_tbl["A"]["med"], 0.011)
    cmp_row(24, "B wall 中位（valid）", 304.64, wall_tbl["B"]["med"], 0.011)
    cmp_row(25, "C wall 中位（valid）", 357.33, wall_tbl["C"]["med"], 0.011)
    cmp_range(26, "A wall 极差（valid）", (42.49, 622.20), wall_tbl["A"]["range"], 0.011)
    cmp_range(27, "B wall 极差（valid）", (67.14, 641.52), wall_tbl["B"]["range"], 0.011)
    cmp_range(28, "C wall 极差（valid）", (93.43, 849.83), wall_tbl["C"]["range"], 0.011)
    cwin = [v["window"] for v in scout_tbl.values() if v["verdict_class"] != "timeout"]
    cmp_row(29, "C scout input_uncached 中位", 23003, med([v["iu"] for v in cwin]))
    cmp_range(30, "C scout input_uncached 极差", (18550, 29324), rng([v["iu"] for v in cwin]))
    cmp_row(31, "C scout cache_read 中位", 149632, med([v["cr"] for v in cwin]))
    cmp_range(32, "C scout cache_read 极差", (44544, 302464), rng([v["cr"] for v in cwin]))
    cmp_row(33, "C scout cost 中位", 0.415, med([v["cost"] for v in cwin]), 0.0011)
    cmp_range(34, "C scout cost 极差", (0.209, 0.743), rng([v["cost"] for v in cwin]), 0.0011)
    cmp_row(35, "C e2e input_uncached 中位", 48831, e2e_tbl["C"]["iu"])
    cmp_range(36, "C e2e input_uncached 极差", (34635, 136080), e2e_tbl["C"]["iu_r"])
    cmp_row(37, "C e2e cost 中位", 1.584, e2e_tbl["C"]["co"], 0.0011)
    cmp_range(38, "C e2e cost 极差", (0.532, 9.902), e2e_tbl["C"]["co_r"], 0.0011)
    cmp_row(39, "A e2e input_uncached 中位", 36681, e2e_tbl["A"]["iu"])
    cmp_row(40, "A e2e cost 中位", 1.984, e2e_tbl["A"]["co"])
    cmp_row(41, "timeout 行数", 4,
            sum(1 for r in state if r["verdict_class"] == "timeout"))


    say()
    bad = [c for c in checks if not c["match"]]
    say(f"不一致项：{len(bad)} / {len(checks)} ⇒ "
        f"{json.dumps(bad, ensure_ascii=False) if bad else '无'}")

    # ---------------------------------------------------------------- 写出
    result = {"batch": BATCH, "state_sha256": sha256_file(state_path), "rows": len(state),
              "main": main_tbl, "parent_window": w, "parent_modes": sel_modes,
              "wall": wall_tbl, "scout": scout_tbl, "c_evidence": c_audit,
              "fidelity": fidelity, "declared": DECLARED, "checks": checks}
    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    with io.open(OUT_TXT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(LINES) + "\n")
    print("recompute v2 done: rows=%d mismatches=%d" % (len(state), len(bad)))
    for c in bad:
        print("  MISMATCH %s declared=%s got=%s" % (c["name"], c["declared"], c["recomputed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
