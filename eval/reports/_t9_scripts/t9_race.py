"""t9 独立审查 第 4 条：隔离竞态是否真被修掉（自己造并发，不用 mechanism_check 的用例）。

做法：
  · 同一实例（pallets-click__4fd2fea）、**两个不同 run_id**、同时并发各跑一次 gold 判定
    （这正是 t4/D1 报的场景：旧实现里 venv 名与 `.pth` 名都只由 instance 决定 ⇒ 两个 run 抢同一个挂载位）
  · 轮数 = 3（合同要求 ≥3）
  · 逐轮核对：两个 run 的 verdict；每个 run 的 venv 里 `_eval_editable_*.pth` 是否**只有一个**、
    且指向**自己 run** 的工作区；两个 venv 的 realpath 是否互异；
    run_one 自己记录的 `import_probe_before_judge` / `import_probe_after_judge` 是否落在自己工作区内
  · **仪器自证**：另跑一轮 `--simulate-prefix`，把 venv_dir / editable_pth_name 还原成旧公式
    （这是「修复前的 harness」的忠实模拟）⇒ 如果我的检测能报警，说明这套检测有判别力

用法：python eval/reports/_t9_scripts/t9_race.py [--rounds 3] [--simulate-prefix-rounds 1]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

PROJ = r"E:\aicoding\mini-claude-code"
for p in (PROJ, os.path.join(PROJ, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

INSTANCE = "pallets-click__4fd2fea"
OUT = r"E:\aicoding\eval-runs\t9-race"


def _worker(task):
    """子进程：并发跑同一实例的**一个 run**。"""
    from eval.harness import env as E
    from eval.harness import run_instance as RI

    if task.get("simulate_prefix"):
        # 还原「修复前」的两个公式（D1 的根因：venv 与 .pth 都不含 run_id）
        def legacy_venv_dir(instance, run_id=""):
            base = f"{E.repo_key_of(instance['repo'])}-{E.venv_tag(instance['base_commit'])}"
            return os.path.join(E.VENV_ROOT, base)

        def legacy_pth_name(instance_id, run_id):
            return f"_eval_editable_{hashlib.sha256(instance_id.encode()).hexdigest()[:16]}.pth"

        E.venv_dir = legacy_venv_dir
        E.editable_pth_name = legacy_pth_name

    inst = RI.load_instance(task["instance_id"])
    row = RI.run_one(task["instance_id"], inst["patch"], run_id=task["run_id"],
                     label=task["label"], patch_kind="gold", keep_workspace=True)
    return {"run_id": task["run_id"], "simulate_prefix": bool(task.get("simulate_prefix")),
            "row": row}


def audit(run_id, iid, row):
    """逐 run 核对：venv 内容 + 挂载点指向 + 自身自检记录。"""
    from eval.harness import env as E
    inst = json_load(os.path.join(PROJ, "eval", "instances", f"{iid}.json"))
    vd = row.get("venv")
    ws = row.get("workspace")
    own = E.editable_pth_name(iid, run_id)
    sp = os.path.join(vd, "Lib", "site-packages") if vd else None
    mounts = []
    others = []
    if sp and os.path.isdir(sp):
        for f in sorted(os.listdir(sp)):
            if not f.endswith(".pth"):
                continue
            body = open(os.path.join(sp, f), encoding="utf-8").read()
            entries = body.split("'")[1::2]
            (mounts if f == own else others).append({"file": f, "entries": entries})
    real_ws = os.path.realpath(ws) if ws else None
    own_ok = False
    for m in mounts:
        for e in m["entries"]:
            if os.path.normcase(os.path.realpath(e)).startswith(os.path.normcase(real_ws)):
                own_ok = True
    probe_ok = True
    for key in ("import_probe_before_judge", "import_probe_after_judge"):
        v = row.get(key)
        if v is None:
            probe_ok = False
        elif not os.path.normcase(os.path.realpath(v)).startswith(os.path.normcase(real_ws)):
            probe_ok = False
    return {
        "run_id": run_id,
        "venv": vd,
        "venv_real": os.path.realpath(vd) if vd else None,
        "workspace": ws,
        "workspace_real": real_ws,
        "own_pth": own,
        "mounts_named_own": mounts,
        "other_eval_pth": others,
        "mount_points_to_own_ws": own_ok,
        "probe_records_own_ws": probe_ok,
        "verdict_class": row.get("verdict_class"),
        "resolved": row.get("resolved"),
        "notes": row.get("notes", []),
        "import_probe_before_judge": row.get("import_probe_before_judge"),
        "import_probe_after_judge": row.get("import_probe_after_judge"),
    }


def json_load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def one_round(round_no, tag, workers=2):
    """同一实例、两个 run_id、同时提交给 2 个 worker 进程。"""
    a = f"t9-race-r{round_no}A"
    b = f"t9-race-r{round_no}B"
    tasks = [{"instance_id": INSTANCE, "run_id": a, "label": f"{tag}-A"},
             {"instance_id": INSTANCE, "run_id": b, "label": f"{tag}-B"}]
    if tag == "prefix-sim":
        for t in tasks:
            t["simulate_prefix"] = True
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_worker, t): t for t in tasks}
        results = [f.result() for f in as_completed(futs)]
    audits = {r["run_id"]: audit(r["run_id"], INSTANCE, r["row"]) for r in results}
    same_venv = (audits[a]["venv_real"] == audits[b]["venv_real"])
    rec = {"round": round_no, "tag": tag, "venvs_differ": not same_venv, "runs": audits}
    bad = []
    for rid, au in audits.items():
        if au["verdict_class"] != "agent_result":
            bad.append(f"{rid}: verdict_class={au['verdict_class']}（期望 agent_result）")
        if au["resolved"] is not True:
            bad.append(f"{rid}: resolved={au['resolved']}（期望 True）")
        if not au["mount_points_to_own_ws"]:
            bad.append(f"{rid}: 挂载点未指向自己工作区")
        if not au["probe_records_own_ws"]:
            bad.append(f"{rid}: 自检记录未落在自己工作区（或缺失）")
        if au["other_eval_pth"]:
            bad.append(f"{rid}: venv 里另有 editable 挂载 {au['other_eval_pth']}")
    if same_venv:
        bad.append("两个 run 共用同一个 venv（隔离未生效）")
    rec["problems"] = bad
    rec["clean"] = not bad
    return rec


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--simulate-prefix-rounds", type=int, default=1)
    args = ap.parse_args(argv)

    print("=" * 96)
    print(f"t9 独立并发隔离测试：实例 {INSTANCE}，同一实例两个 run 同时并发")
    print("=" * 96)
    all_recs = []
    for i in range(args.rounds):
        rec = one_round(i, "current")
        all_recs.append(rec)
        print(f"\n[round {i} / 当前实现] 两个 venv 互异={rec['venvs_differ']}  clean={rec['clean']}")
        for rid, au in rec["runs"].items():
            print(f"    {rid}: verdict={au['verdict_class']} resolved={au['resolved']}")
            print(f"      venv      = {au['venv']}")
            print(f"      挂载点    = {au['mounts_named_own']}")
            print(f"      指向自己工作区={au['mount_points_to_own_ws']}  自检落在自己工作区={au['probe_records_own_ws']}")
            print(f"      import_probe_before={au['import_probe_before_judge']}")
        for p in rec["problems"]:
            print(f"    !! {p}")

    prefix_recs = []
    for i in range(args.simulate_prefix_rounds):
        rec = one_round(100 + i, "prefix-sim")
        prefix_recs.append(rec)
        print(f"\n[round {i} / **修复前公式模拟**（仪器自证）] clean={rec['clean']}"
              f"  两个 venv 互异={rec['venvs_differ']}")
        for rid, au in rec["runs"].items():
            print(f"    {rid}: verdict={au['verdict_class']} resolved={au['resolved']}")
            print(f"      挂载点 = {au['mounts_named_own']}  其它 editable .pth={au['other_eval_pth']}")
            print(f"      notes  = {au['notes']}")
        for p in rec["problems"]:
            print(f"    !! {p}")

    clean_n = sum(1 for r in all_recs if r["clean"])
    print("\n" + "=" * 96)
    print(f"当前实现：{clean_n}/{len(all_recs)} 轮 0 问题（0 infra、0 静默误判）")
    print(f"修复前公式模拟：{sum(1 for r in prefix_recs if not r['clean'])}/{len(prefix_recs)} 轮"
          f" 检出问题（>0 才说明这套检测有判别力）")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "race_result.json"), "w", encoding="utf-8") as f:
        json.dump({"current": all_recs, "prefix_sim": prefix_recs}, f,
                  ensure_ascii=False, indent=2)
    print(f"证据：{os.path.join(OUT, 'race_result.json')}")
    ok = (clean_n == len(all_recs)) and all(not r["clean"] for r in prefix_recs if prefix_recs)
    print(f"结论：{'PASS' if ok else 'CHECK'}")
    return 0 if clean_n == len(all_recs) else 1


if __name__ == "__main__":
    sys.exit(main())
