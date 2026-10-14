"""t4 复核：把「.pth 竞态」证成**可复现的**（而不是一次性偶然）。

做法：对同一实例、两个不同 run_id，反复并发触发 N 轮，统计有多少轮出现 infra_error。
并在每轮结束后**检查 .pth 的实际内容**，证明它指向了「后写者」的工作区。

这证明的是：**venv 共享 + .pth 文件名只依赖 instance_id** 这个组合下，
并发写同一个 .pth 是真实存在的竞态；是否触发取决于时序。
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

OUT = r"E:\aicoding\eval-runs\t4\pth_race"
IID = "pallets-click__4fd2fea"
ROUNDS = 6


def _one(task):
    iid = task["iid"]
    inst = RI.load_instance(iid)
    row = RI.run_one(iid, inst["patch"], run_id=task["run_id"], label=task["label"],
                     patch_kind="gold", keep_workspace=False)
    return {"run_id": task["run_id"], "verdict_class": row["verdict_class"],
            "resolved": row["resolved"], "notes": row.get("notes", []),
            "venv": row.get("venv")}


def pth_files(venv):
    sp = os.path.join(venv, "Lib", "site-packages")
    if not os.path.isdir(sp):
        return []
    out = []
    for n in sorted(os.listdir(sp)):
        if n.startswith("_eval_editable_") and n.endswith(".pth"):
            with open(os.path.join(sp, n), encoding="utf-8", errors="replace") as f:
                out.append({"name": n, "body": f.read()})
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    inst = RI.load_instance(IID)
    venv = E.venv_dir(inst)
    rounds = []
    for k in range(ROUNDS):
        ra = f"t4-race-{k}A"
        rb = f"t4-race-{k}B"
        with ProcessPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(_one, {"iid": IID, "run_id": ra, "label": f"race{k}A"})
            fb = pool.submit(_one, {"iid": IID, "run_id": rb, "label": f"race{k}B"})
            a, b = fa.result(), fb.result()
        pf = pth_files(venv)
        infra = [x["run_id"] for x in (a, b) if x["verdict_class"] == "infra_error"]
        rounds.append({"round": k, "A": a, "B": b, "infra_runs": infra, "pth_after": pf})
        print(f"round {k}: A={a['verdict_class']}/{a['resolved']} "
              f"B={b['verdict_class']}/{b['resolved']}  infra={infra}", flush=True)
        for x in (a, b):
            for n in x["notes"]:
                print(f"    note({x['run_id']}): {n}", flush=True)

    total_infra = sum(len(r["infra_runs"]) for r in rounds)
    # .pth 数量：只应由 instance_id 决定 => 永远是 1 个
    pth_counts = sorted({len(r["pth_after"]) for r in rounds})
    # 指向：每轮结束时 pth 指向哪个 run_id
    targets = []
    for r in rounds:
        for f in r["pth_after"]:
            import re
            m = re.findall(r"eval-runs\\\\([^\\\\]+)", f["body"])
            targets.append(sorted(set(m)))
    res = {
        "instance_id": IID, "venv": venv, "rounds": ROUNDS,
        "rounds_with_infra": [r["round"] for r in rounds if r["infra_runs"]],
        "total_infra_observations": total_infra,
        "pth_file_counts_seen": pth_counts,
        "pth_targets_per_round": targets,
        "detail": rounds,
    }
    with open(os.path.join(OUT, "pth_race.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print()
    print(f"共 {ROUNDS} 轮并发（每轮 2 个 run 跑同一实例）")
    print(f"出现 infra_error 的轮：{res['rounds_with_infra']}  合计 {total_infra} 次")
    print(f".pth 文件个数（各轮）：{pth_counts}  => 恒为 1 个同名文件（文件名只依赖 instance_id）")
    print(f".pth 各轮指向的 run_id：")
    for i, t in enumerate(targets):
        print(f"    round {i}: {t}")
    print(f"-> {os.path.join(OUT, 'pth_race.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())