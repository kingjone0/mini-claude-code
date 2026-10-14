"""t9 独立审查 第 2、3 条：从**归档的原始 pytest json report** 独立重算。

只读证据，不跑 pytest：
  · `eval-cache/_artifacts/<repo_key>/<iid>/B1.json|T1.json|G1.json`（构造期三观测点原始报告）
  · `eval/instances/<iid>.json`（冻结的 F2P / wrong_patch）

第 2 条（§4.2 口径）：
  对每条 F2P node id，读出它在 B/T/G 三点的结果，并分类：
    - `not_in_B`   ：B 里根本没有这个 node（= 本提交新增的测试）
    - `in_B_passed`：B 里存在且绿
    - `in_B_red`   ：B 里存在但非绿（§4.2 真正要排除的那一类）
  再**按字面读法**重算一遍 F2P_literal = {n ∈ T 红 | n ∈ B 且 passed | n ∈ G 且 passed}，
  若为空 ⇒ 该候选在字面读法下会被 E5 淘汰。逐条给出「字面读法会不会误杀」。

第 3 条（第 3 向判别力）：
  · 核对 3 条 tabulate 实例的 `wrong_patch` 与 `patch` 是否**逐字节相同**
  · 从归档的 `selfcheck_wrong.json` / `SELFWRONGLY_*.txt` 看第三向的实际状态
  · 对非 tabulate 实例核对第三向是否「补丁干净 apply + 结果 unresolved」

用法：python eval/reports/_t9_scripts/t9_baseline_and_wrong.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

PROJ = r"E:\aicoding\mini-claude-code"
INST = os.path.join(PROJ, "eval", "instances")
ART = r"E:\aicoding\eval-cache\_artifacts"

PILOT = ["astanin-python-tabulate__5373ebf", "astanin-python-tabulate__87a9a4e",
         "astanin-python-tabulate__9dedd45",
         "more-itertools-more-itertools__1b7f033", "more-itertools-more-itertools__ccf96ac",
         "more-itertools-more-itertools__f8ccab2",
         "pallets-click__1b0e19f", "pallets-click__4fd2fea", "pallets-click__ac6a2ac",
         "pytest-dev-pluggy__20d8143", "pytest-dev-pluggy__cbf58d5",
         "pytest-dev-pluggy__dd20a85"]


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def outcomes(json_path):
    """pytest-json-report -> {nodeid: outcome}（不做任何归一化，原样读）。"""
    if not os.path.exists(json_path):
        return None
    d = load(json_path)
    out = {}
    for t in d.get("tests", []):
        out.setdefault(t.get("nodeid"), t.get("outcome"))
    return out


def art_dir(iid, repo):
    return os.path.join(ART, repo.replace("/", "__"), iid)


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    print("=" * 100)
    print("第 2 条：§4.2 实现口径 vs 字面读法（基于归档 B1/T1/G1 原始 json report 独立重算）")
    print("=" * 100)
    summary = {}
    literal_kill = []
    for iid in PILOT:
        inst = load(os.path.join(INST, f"{iid}.json"))
        d = art_dir(iid, inst["repo"])
        B = outcomes(os.path.join(d, "B1.json"))
        T = outcomes(os.path.join(d, "T1.json"))
        G = outcomes(os.path.join(d, "G1.json"))
        f2p = inst["FAIL_TO_PASS"]
        if B is None or T is None or G is None:
            print(f"\n[{iid}] 缺归档报告（B={B is not None} T={T is not None} G={G is not None}）")
            continue
        cls = {"not_in_B": 0, "in_B_passed": 0, "in_B_red": 0}
        detail = []
        for n in f2p:
            if n not in B:
                cls["not_in_B"] += 1
                k = "not_in_B"
            elif B[n] == "passed":
                cls["in_B_passed"] += 1
                k = "in_B_passed"
            else:
                cls["in_B_red"] += 1
                k = "in_B_red"
            detail.append((n, k, B.get(n), T.get(n), G.get(n)))
        # 字面读法
        lit = [n for n in f2p if T.get(n) in ("failed", "error")
               and B.get(n) == "passed" and G.get(n) == "passed"]
        killed = len(lit) == 0
        if killed:
            literal_kill.append(iid)
        summary[iid] = {"classes": cls, "literal_f2p": len(lit),
                        "literal_would_kill": killed}
        print(f"\n[{iid}]  F2P={len(f2p)}  分类={cls}")
        print(f"    字面读法（B 中存在且 passed）得到的 F2P = {len(lit)} ⇒ "
              f"{'**字面读法下会被 E5 淘汰（F2P 空）**' if killed else '字面读法也能活'}")
        for n, k, b, t, g in detail[:6]:
            print(f"      {k:<12} B={str(b):<8} T={str(t):<8} G={str(g):<8} {n}")
        if len(detail) > 6:
            print(f"      ...（其余 {len(detail)-6} 条同类）")
        # 冻结的 known_limits 是否如实写了这件事
        kl = " || ".join(inst.get("known_limits") or [])
        if cls["not_in_B"]:
            has = f"{cls['not_in_B']}/{len(f2p)}" in kl
            print(f"    known_limits 是否写了「F2P 部分为新增测试 {cls['not_in_B']}/{len(f2p)}」：{has}")

    print("\n" + "-" * 100)
    print(f"依赖该细化的实例数：{len(PILOT) - len([i for i, s in summary.items() if not s['literal_would_kill']])}"
          f" / {len(PILOT)} 在字面读法下会因 F2P 为空被 E5 淘汰")
    print(f"其中 F2P **全部**是新增测试的实例："
          f"{[i for i, s in summary.items() if s['classes']['not_in_B'] == sum(s['classes'].values())]}")

    print("\n" + "=" * 100)
    print("第 3 条：3 条 tabulate 实例的第 3 向判别力（wrong_patch vs patch 逐字节）")
    print("=" * 100)
    for iid in PILOT:
        inst = load(os.path.join(INST, f"{iid}.json"))
        wp = (inst.get("wrong_patch") or "")
        gp = (inst.get("patch") or "")
        same = wp.strip() == gp.strip()
        method = (inst.get("gold_selfcheck") or {}).get("wrong_patch_method")
        d = art_dir(iid, inst["repo"])
        wj = os.path.join(d, "selfcheck_wrong.json")
        w_out = outcomes(wj)
        n_failed = None
        if w_out is not None:
            n_failed = sum(1 for v in w_out.values() if v != "passed")
        print(f"  {iid:<46} method={str(method):<22} wrong==gold={same}"
              f"  第三向归档结果（非 passed 数）={n_failed}")
        if same:
            print("      ⇒ 链式『先叠 gold 再 -R』净效果 = 回归基线 = 与第 2 向同状态："
                  "该向不提供额外判别力")
    return 0


if __name__ == "__main__":
    sys.exit(main())
