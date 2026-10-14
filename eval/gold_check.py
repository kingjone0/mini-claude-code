"""黄金三向自校验（预注册 §6）：一条命令跑完全部实例，给出逐条结论。

    gold  => 期望 resolved    / agent_result
    empty => 期望 unresolved  / empty_patch
    wrong => 期望 unresolved  / agent_result（补丁能干净 apply，只是没修好）

**这是 harness 的可用性门槛**（§6.4）：三向全对 => harness 对该实例可用；
任一向不符 => 停机修 harness，**不是**回头改判据或改 F2P/P2P。

用法：
    python eval/gold_check.py                       # 全部 pilot-12
    python eval/gold_check.py --instance <iid>      # 单条（调试用）
    python eval/gold_check.py --set reserve         # 也跑 reserve 池
    python eval/gold_check.py --run-id <id>         # 指定批次目录名

退出码：全部通过 = 0；有任一实例任一向不符 = 1。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
for p in (PROJECT_ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E                      # noqa: E402
from eval.harness import judge as J                    # noqa: E402
from eval.harness import run_instance as RI            # noqa: E402

# §6.2 的三向期望（写死，不许按结果调）
EXPECT = {
    "gold":  {"resolved": True,  "verdict_class": J.AGENT_RESULT},
    "empty": {"resolved": False, "verdict_class": J.EMPTY_PATCH},
    "wrong": {"resolved": False, "verdict_class": J.AGENT_RESULT},
}
DIRECTIONS = ("gold", "empty", "wrong")


def pilot_ids(which: str):
    """要校验的实例清单。默认只跑 pilot-12（正式集）。"""
    ids = []
    for name in sorted(os.listdir(RI.INSTANCES_DIR)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        with open(os.path.join(RI.INSTANCES_DIR, name), encoding="utf-8") as f:
            obj = json.load(f)
        m = obj.get("set_membership")
        if which == "pilot" and m != "pilot-12":
            continue
        if which == "reserve" and m not in ("pilot-12", "reserve"):
            continue
        ids.append(obj["instance_id"])
    return ids


def run_direction(iid: str, direction: str, run_id: str) -> dict:
    inst = RI.load_instance(iid)
    apply_reverse = chain_gold_first = False
    if direction == "wrong":
        # §6.3 第 3 向的施加方式**由实例冻结的元数据决定**（见 run_instance.wrong_patch_plan）：
        # method=reverse_of_gold 的实例，正文是 gold => 先叠 gold 再 `git apply -R`。
        patch_text, apply_reverse, chain_gold_first, _note = RI.wrong_patch_plan(inst)
    elif direction == "empty":
        patch_text = ""
    else:
        patch_text = inst["patch"]
    return RI.run_one(iid, patch_text, run_id=run_id, label="gold_check",
                      patch_kind=direction, keep_workspace=False,
                      apply_reverse=apply_reverse, chain_gold_first=chain_gold_first)


def wrong_direction_equivalence(inst: dict) -> str | None:
    """第 3 向是否只是第 2 向的换装 => 返回**如实说明**，否则 None。

    3 条 tabulate 实例的 `wrong_patch` 与 `patch` 逐字节相同（构造器 §6.3 第 3 向
    落盘的是 gold 正文 + `method=reverse_of_gold` 标记）=>「先叠 gold 再反向」的净效果
    就是回到基线，与空补丁同状态。这一向于是**只验证了链路能跑通**，
    没有额外地检验「判别力」。§6.4 的三种后果里，这一档不对应「harness 失效」
    （空补丁仍被判 unresolved，判别力有实证），但**必须在报告里显形**，不许当成
    「第 3 向独立通过」蒙混过去。
    """
    method = (inst.get("gold_selfcheck") or {}).get("wrong_patch_method")
    if method != "reverse_of_gold":
        return None
    if (inst.get("wrong_patch") or "").strip() != (inst.get("patch") or "").strip():
        return None
    return ("wrong_patch 与 gold 逐字节相同 => 第 3 向净效果 = 基线（与第 2 向同状态），"
            "该向不提供额外判别力（构造侧遗留，见 eval/harness/run_instance.py:wrong_patch_plan）")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(description="黄金三向自校验（预注册 §6）")
    ap.add_argument("--instance", action="append", help="只跑指定 instance_id（可重复）")
    ap.add_argument("--set", default="pilot", choices=("pilot", "reserve"),
                    help="pilot=正式集 12 条（默认）；reserve=连 reserve 一起")
    ap.add_argument("--run-id", default=None, help="批次目录名（默认 goldcheck-<时间戳>）")
    ap.add_argument("--json-out", default=None, help="把逐条结论写成 JSON 的路径")
    args = ap.parse_args(argv)

    run_id = args.run_id or ("goldcheck-" + time.strftime("%Y%m%d-%H%M%S"))
    ids = args.instance or pilot_ids(args.set)

    print("=" * 78)
    print(f"黄金三向自校验  |  run_id={run_id}  实例数={len(ids)}  对应规则节：§6")
    print(f"运行工作区：{E.run_root(run_id)}")
    print("=" * 78)

    rows = []
    failures = []
    for iid in ids:
        got = {}
        for direction in DIRECTIONS:
            r = run_direction(iid, direction, run_id)
            exp = EXPECT[direction]
            ok = (r["resolved"] == exp["resolved"]
                  and r["verdict_class"] == exp["verdict_class"])
            got[direction] = {
                "verdict_class": r["verdict_class"],
                "resolved": r["resolved"],
                "expected_class": exp["verdict_class"],
                "expected_resolved": exp["resolved"],
                "ok": ok,
                "apply_reverse": bool(r.get("apply_reverse")),
                "f2p": (None if not r.get("judged") else
                        f"{r['judged']['f2p_total'] - len(r['judged']['f2p_not_passed'])}"
                        f"/{r['judged']['f2p_total']}"),
                "p2p": (None if not r.get("judged") else
                        f"{r['judged']['p2p_total'] - len(r['judged']['p2p_not_passed'])}"
                        f"/{r['judged']['p2p_total']}"),
                "evidence": E.art_dir(run_id, iid),
            }
            if not ok:
                failures.append((iid, direction, got[direction]))
        all_ok = all(g["ok"] for g in got.values())
        caveat = wrong_direction_equivalence(RI.load_instance(iid))
        flag = "OK  " if all_ok else "FAIL"
        print(f"\n[{flag}] {iid}")
        for direction in DIRECTIONS:
            g = got[direction]
            print(f"    {direction:<5} -> {g['verdict_class']:<13} resolved={str(g['resolved']):<5}"
                  f" (期望 {g['expected_class']}/{g['expected_resolved']})"
                  f"{'' if g['ok'] else '   <<< 不符'}"
                  f"  F2P={g['f2p']} P2P={g['p2p']}")
        if caveat:
            print(f"    ⚠ 判别力说明：{caveat}")
        print(f"    证据：{got['gold']['evidence']}")
        rows.append({"instance_id": iid, "ok": all_ok, "directions": got,
                     "wrong_direction_caveat": caveat})

    total = len(ids)
    passed = sum(1 for r in rows if r["ok"])
    print("\n" + "=" * 78)
    print(f"三向自校验结果：{passed}/{total} 实例三向全对")
    print(f"逐向统计：gold 通过 {sum(1 for r in rows if r['directions']['gold']['ok'])}/{total}，"
          f"empty 通过 {sum(1 for r in rows if r['directions']['empty']['ok'])}/{total}，"
          f"wrong 通过 {sum(1 for r in rows if r['directions']['wrong']['ok'])}/{total}")
    caveats = [r for r in rows if r.get("wrong_direction_caveat")]
    if caveats:
        print(f"\n⚠ 第 3 向判别力说明（{len(caveats)}/{total} 条命中，扣分项必须进结果节）：")
        for r in caveats:
            print(f"  - {r['instance_id']}: {r['wrong_direction_caveat']}")
    if failures:
        print("\n不符明细（§6.4：任一向不符 => 修 harness，不是改判据）：")
        for iid, direction, g in failures:
            print(f"  - {iid} / {direction}: 实得 {g['verdict_class']}/{g['resolved']}，"
                  f"期望 {g['expected_class']}/{g['expected_resolved']}")
    else:
        print("\n三向全对 => harness 对本批实例可用（§6.4）。")
    print(f"完整证据：{E.run_root(run_id)}")
    print("=" * 78)

    summary = {
        "run_id": run_id, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "prereg": "eval/PREREGISTRATION.md §6",
        "total": total, "passed": passed,
        "directions_expected": {k: v for k, v in EXPECT.items()},
        "results": rows,
    }
    out = args.json_out or os.path.join(E.run_root(run_id), "gold_check_summary.json")
    E.write_text(out, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"结论 JSON：{out}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())