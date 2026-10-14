"""t4 复核：把并发隔离缺陷钉死在**真实批跑场景**下。

已证实的缺陷：`.pth` 文件名只由 `instance_id` 决定
    `_eval_editable_<sha256(instance_id)[:16]>.pth`
而 venv 由 `<repo>-<base_commit[:8]>` 决定 => **同一 base_commit 的不同实例共享 venv**，
却各自往同一个 `.pth` 路径写（同一实例不同 run_id 也一样）。

本脚本验证三种真实场景，看哪一种会踩到：
  S1 同一 run_id 内、**同 base 的不同实例**并发（真实批跑：reserve 池里同 base 的多条）
  S2 不同 run_id、同一实例并发（已实测踩中）
  S3 同一 run_id 内、**不同 base** 的实例并发（venv 不同 => 应安全）

判定：每种场景下，所有实例的 verdict_class 都必须是 agent_result 且 resolved=True；
任一条变 infra_error => 隔离缺陷在该场景真实发生。
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

PROJ = r"E:\aicoding\mini-claude-code"
for p in (PROJ, os.path.join(PROJ, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E            # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402

OUT = r"E:\aicoding\eval-runs\t4\isolation_defect"

# 找同 base_commit 的不同实例：base 相同 => venv 相同 => .pth 同目录
# 从 23 条实例里按 base_commit 分组
def group_by_venv():
    """按**实际 venv 路径**分组（这才是冲突的判定依据），而不是按 base_commit 字符串。

    注意：venv 名是 `<repo_key>-<base_commit[:8]>`，所以「不同 repo 但 base 前 8 位相同」
    也会共享 venv —— 这个边界比「同 base」更宽，必须用它来判。
    """
    groups = {}
    for f in sorted(os.listdir(RI.INSTANCES_DIR)):
        if not f.endswith(".json") or f.startswith("_"):
            continue
        o = json.load(open(os.path.join(RI.INSTANCES_DIR, f), encoding="utf-8"))
        v = os.path.basename(E.venv_dir(o))
        groups.setdefault(v, []).append(o["instance_id"])
    return groups


def _one(task):
    iid = task["iid"]
    inst = RI.load_instance(iid)
    row = RI.run_one(iid, inst["patch"], run_id=task["run_id"], label=task["label"],
                     patch_kind="gold", keep_workspace=False)
    return {"iid": iid, "run_id": task["run_id"], "verdict_class": row["verdict_class"],
            "resolved": row["resolved"], "notes": row.get("notes", []),
            "venv": row.get("venv"), "import_name": row.get("import_name"),
            "pth_hint": os.path.join(row.get("venv") or "", "Lib", "site-packages"),
            "workspace": row.get("workspace")}


def run(tasks, workers):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return [f.result() for f in [pool.submit(_one, t) for t in tasks]]


def main():
    os.makedirs(OUT, exist_ok=True)
    groups = group_by_venv()
    shared = {v: ids for v, ids in groups.items() if len(ids) >= 2}
    # 场景 S1：共享 venv 的不同实例（>1 条的 venv 才有效）
    if shared:
        s1_venv, s1_ids = next(iter(shared.items()))
    else:
        s1_venv, s1_ids = None, []
    print("=" * 70)
    print("场景 S1：同一 run_id、**共享 venv 的不同实例**并发（真实批跑形态）")
    print(f"  venv={s1_venv}  实例={s1_ids}  （全部共享 venv 分组：{shared}）")
    if not s1_ids:
        print("  [注意] 本实例集里不存在共享 venv 的两条实例 => S1 在本集内不可能发生")
        print("    （每条实例的 repo+base 都唯一；23 条全部一一对应不同 venv）")
    print("=" * 70)
    s1 = run([{"iid": i, "run_id": "t4-iso-s1", "label": "s1"} for i in s1_ids], 3) if s1_ids else []
    for r in s1:
        print(f"  {r['iid']:42s} {r['verdict_class']:13s} resolved={r['resolved']!s:5s} "
              f"venv={os.path.basename(r['venv'] or '')}")
        for n in r["notes"]:
            print(f"      note: {n}")

    # 场景 S2：不同 run_id、同一实例
    dup = s1_ids[0] if s1_ids else "pallets-click__4fd2fea"
    print()
    print("=" * 70)
    print(f"场景 S2：不同 run_id、**同一实例**并发  {dup}")
    print("=" * 70)
    s2 = run([{"iid": dup, "run_id": "t4-iso-s2A", "label": "s2A"},
              {"iid": dup, "run_id": "t4-iso-s2B", "label": "s2B"}], 2)
    for r in s2:
        print(f"  {r['run_id']:12s} {r['verdict_class']:13s} resolved={r['resolved']!s:5s}")
        for n in r["notes"]:
            print(f"      note: {n}")

    # 场景 S3：不同 base 的实例并发（venv 不同）
    seen, s3_ids = set(), []
    for base, ids in groups.items():
        if base not in seen:
            seen.add(base)
            s3_ids.append(ids[0])
    s3_ids = s3_ids[:4]
    print()
    print("=" * 70)
    print(f"场景 S3：同一 run_id、**不同 base** 的实例并发（venv 不同）{s3_ids}")
    print("=" * 70)
    s3 = run([{"iid": i, "run_id": "t4-iso-s3", "label": "s3"} for i in s3_ids], 4)
    for r in s3:
        print(f"  {r['iid']:42s} {r['verdict_class']:13s} resolved={r['resolved']!s:5s}")

    def bad(rows):
        return [r for r in rows if r["verdict_class"] == "infra_error" or not r["resolved"]]

    res = {
        "defect_hypothesis": "editable .pth 文件名只由 instance_id 决定，而 venv 由 "
                             "<repo>-<base_commit[:8]> 决定 => 同 base 的不同实例 / 同实例不同 run_id "
                             "会并发写同一个 .pth，后写者覆盖先写者",
        "s1_same_venv_diff_instances": {"ids": s1_ids, "venv": s1_venv,
                                        "rows": s1, "bad": [r["iid"] for r in bad(s1)],
                                        "applicable": bool(s1_ids),
                                        "all_venv_groups": shared},
        "s2_same_instance_diff_runs": {"iid": dup, "rows": s2,
                                       "bad": [r["run_id"] for r in bad(s2)]},
        "s3_diff_base": {"ids": s3_ids, "rows": s3, "bad": [r["iid"] for r in bad(s3)]},
    }
    res["s1_defect_reproduced"] = bool(res["s1_same_venv_diff_instances"]["bad"])
    res["s2_defect_reproduced"] = bool(res["s2_same_instance_diff_runs"]["bad"])
    res["s3_safe"] = not res["s3_diff_base"]["bad"]
    with open(os.path.join(OUT, "isolation_defect.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 70)
    print("结论")
    print("=" * 70)
    print(f"  S1（共享 venv 不同实例并发）缺陷复现：{res['s1_defect_reproduced']}  "
          f"坏实例={res['s1_same_venv_diff_instances']['bad']}  "
          f"适用={res['s1_same_venv_diff_instances']['applicable']}")
    print(f"  S2（同实例不同 run_id 并发）缺陷复现：{res['s2_defect_reproduced']}  "
          f"坏run={res['s2_same_instance_diff_runs']['bad']}")
    print(f"  S3（不同 base 并发）安全：{res['s3_safe']}")
    print(f"  -> {os.path.join(OUT, 'isolation_defect.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())