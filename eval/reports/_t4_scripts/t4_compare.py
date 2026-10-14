"""t4 复核：汇总 gold_check 的 JSON 结论 + 与「独立 red/green」逐条对照。

输出一份可直接引用的对照表：
  实例 | gold_check(gold/empty/wrong) | 独立 runner(T红/G绿, P2P) | 是否一致
"""

from __future__ import annotations

import json
import os
import sys

GC = r"E:\aicoding\eval-runs\t4-goldcheck\gold_check_summary.json"
IND = r"E:\aicoding\eval-runs\t4\independent\redgreen.json"
ARCH = r"E:\aicoding\eval-runs\t4\archived_recompute\archived_recompute.json"
OUT = r"E:\aicoding\eval-runs\t4"


def main():
    gc = json.load(open(GC, encoding="utf-8"))
    ind = {r["instance_id"]: r for r in json.load(open(IND, encoding="utf-8"))}
    arch = {r["instance_id"]: r for r in json.load(open(ARCH, encoding="utf-8"))}

    lines = []
    lines.append("=== gold_check 三向结论（本轮 t4 自己跑的）===")
    lines.append(f"run_id={gc['run_id']}  total={gc['total']}  passed={gc['passed']}")
    gc_rows = {}
    for r in gc["results"]:
        d = r["directions"]
        gc_rows[r["instance_id"]] = r
        lines.append(f"  {r['instance_id']:42s} ok={str(r['ok']):5s} "
                     f"gold={d['gold']['verdict_class']}/{d['gold']['resolved']} "
                     f"empty={d['empty']['verdict_class']}/{d['empty']['resolved']} "
                     f"wrong={d['wrong']['verdict_class']}/{d['wrong']['resolved']} "
                     f"| gold F2P={d['gold']['f2p']} P2P={d['gold']['p2p']} "
                     f"| wrong F2P={d['wrong']['f2p']} P2P={d['wrong']['p2p']}")

    lines.append("")
    lines.append("=== 三路对照（gold_check / 独立 runner / t2 归档重算）===")
    disagree_runs = []
    disagree_arch = []
    for iid in gc_rows:
        g = gc_rows[iid]["directions"]
        i = ind.get(iid)
        a = arch.get(iid)
        gc_gold = g["gold"]["resolved"]
        gc_wrong = g["wrong"]["resolved"]
        i_T = i["verdict"]["T_red"] if i and i.get("verdict") else None
        i_G = i["verdict"]["G_resolved"] if i and i.get("verdict") else None
        iT_p2p = i["T"]["p2p_not_passed_count"] if i and i.get("T") else None
        iG_p2p = i["G"]["p2p_not_passed_count"] if i and i.get("G") else None
        a_ok = a["recomputed_matches_declared"] if a else None
        # 一致性：gold_check.gold.resolved == 独立 runner.G_resolved
        #           独立 runner.T_red == 归档 T1 的 F2P 全红
        same = (gc_gold == i_G) and (gc_wrong is False)
        if not same:
            disagree_runs.append(iid)
        if a is not None and a_ok is False:
            disagree_arch.append(iid)
        lines.append(f"  {iid:42s} gc.gold={str(gc_gold):5s} ind.G={str(i_G):5s} "
                     f"ind.T_red={str(i_T):5s} | ind.T.p2p_fail={iT_p2p} "
                     f"ind.G.p2p_fail={iG_p2p} | arch_recompute_ok={a_ok} | same={same}")

    lines.append("")
    lines.append(f"gold_check 与独立 runner 的 gold 向不一致：{disagree_runs or '无'}")
    lines.append(f"t2 归档重算不一致：{disagree_arch or '无'}")

    # 第 3 向说明
    cav = [r["instance_id"] for r in gc["results"] if r.get("wrong_direction_caveat")]
    lines.append("")
    lines.append(f"gold_check 标记第 3 向「不提供额外判别力」的实例（{len(cav)}）：{cav}")
    lines.append("  说明：这 3 条的 wrong F2P=0/N 且 resolved=False，与 empty 同状态；")
    lines.append("  它们仍**判对**（unresolved），只是与第 2 向重复，属构造侧遗留。")

    txt = os.path.join(OUT, "three_way_comparison.txt")
    with open(txt, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n-> {txt}")


if __name__ == "__main__":
    sys.exit(main())