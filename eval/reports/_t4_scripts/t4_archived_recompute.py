"""t4 复核：从 t2 归档的**原始 pytest json report** 重算红/绿（不读任何自述文本）。

对每条实例读 G1.json（test_patch + gold 观测点）与 T1.json（test_patch-only 观测点），
按预注册 §5.1 自己算 F2P/P2P，并与实例 JSON 里的 FAIL_TO_PASS/PASS_TO_PASS 逐 node 比对。
"""

from __future__ import annotations

import json
import os
import re
import sys

INST = r"E:\aicoding\mini-claude-code\eval\instances"
ART = r"E:\aicoding\eval-cache\_artifacts"
OUT = r"E:\aicoding\eval-runs\t4\archived_recompute"

_ALIAS = {"subtests passed": "passed", "subtests failed": "failed",
          "subtests error": "error", "subtests skipped": "skipped"}


def norm(nid):
    nid = (nid or "").replace("\\", "/")
    return re.sub(r"^(?:\.\./)+[^/]*/repo/", "", nid)


def load(path):
    if not os.path.exists(path):
        return None
    doc = json.load(open(path, encoding="utf-8"))
    res = {}
    for t in doc.get("tests") or []:
        o = (t.get("outcome") or "").strip().lower()
        res[norm(t.get("nodeid") or "")] = _ALIAS.get(o, o)
    return res


def judge(f2p, p2p, res):
    return {
        "resolved": (all(res.get(n) == "passed" for n in f2p)
                     and all(res.get(n) == "passed" for n in p2p)),
        "f2p_not_passed": [n for n in f2p if res.get(n) != "passed"],
        "p2p_not_passed": [n for n in p2p if res.get(n) != "passed"],
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for f in sorted(os.listdir(INST)):
        if not f.endswith(".json") or f.startswith("_"):
            continue
        inst = json.load(open(os.path.join(INST, f), encoding="utf-8"))
        if inst.get("set_membership") != "pilot-12":
            continue
        iid = inst["instance_id"]
        d = os.path.join(ART, inst["repo"].replace("/", "__"), iid)
        f2p, p2p = inst["FAIL_TO_PASS"], inst["PASS_TO_PASS"]
        rec = {"instance_id": iid, "artifacts_dir": d,
               "files_present": sorted(os.listdir(d)) if os.path.isdir(d) else None}
        for tag, fname in (("T1", "T1.json"), ("G1", "G1.json"),
                           ("T2", "T2.json"), ("G2", "G2.json")):
            res = load(os.path.join(d, fname))
            if res is None:
                rec[tag] = None
                continue
            j = judge(f2p, p2p, res)
            rec[tag] = {
                "nodes": len(res),
                "f2p_outcomes": {n: res.get(n, "<missing>") for n in f2p},
                "f2p_all_red": all(res.get(n) != "passed" for n in f2p),
                "p2p_not_passed_count": len(j["p2p_not_passed"]),
                "resolved": j["resolved"],
                "p2p_not_passed_sample": j["p2p_not_passed"][:5],
            }
        # 与实例自述的 gold_selfcheck 对照
        gs = inst.get("gold_selfcheck") or {}
        rec["declared_gold_selfcheck"] = {k: gs.get(k) for k in ("gold", "empty", "wrong")}
        rec["recomputed_matches_declared"] = (
            rec.get("G1") is not None and rec["G1"]["resolved"]
            and rec.get("T1") is not None and rec["T1"]["f2p_all_red"]
            and rec["G1"]["p2p_not_passed_count"] == 0
            and rec["T1"]["p2p_not_passed_count"] == 0
            and gs.get("gold") == "resolved")
        rows.append(rec)

    with open(os.path.join(OUT, "archived_recompute.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)

    lines = []
    for r in rows:
        t1, g1 = r.get("T1"), r.get("G1")
        lines.append(f"[{r['instance_id']}]")
        if t1 and g1:
            lines.append(f"  T1(仅 test_patch): F2P全部非passed={t1['f2p_all_red']}  "
                         f"P2P未过={t1['p2p_not_passed_count']}  resolved={t1['resolved']}  "
                         f"nodes={t1['nodes']}")
            lines.append(f"     T1 F2P outcomes: {t1['f2p_outcomes']}")
            lines.append(f"  G1(+gold):         F2P全部passed={not g1['f2p_not_passed'] if 'f2p_not_passed' in g1 else g1['resolved']}  "
                         f"P2P未过={g1['p2p_not_passed_count']}  resolved={g1['resolved']}  "
                         f"nodes={g1['nodes']}")
            lines.append(f"  T2/G2 复现: T2={'有' if r.get('T2') else '无'} "
                         f"G2={'有' if r.get('G2') else '无'}  "
                         f"G2.resolved={r['G2']['resolved'] if r.get('G2') else None}")
            lines.append(f"  declared_gold_selfcheck={r['declared_gold_selfcheck']}  "
                         f"recomputed_consistent={r['recomputed_matches_declared']}")
        else:
            lines.append(f"  ⚠ 归档缺失：files={r['files_present']}")
    n_bad = sum(1 for r in rows if not r["recomputed_matches_declared"])
    lines.append("")
    lines.append(f"重算一致：{len(rows) - n_bad}/{len(rows)}；不一致 {n_bad}")
    for r in rows:
        if not r["recomputed_matches_declared"]:
            lines.append(f"  - {r['instance_id']}")
    with open(os.path.join(OUT, "archived_recompute.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()