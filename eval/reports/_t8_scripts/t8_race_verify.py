"""t8 验收证据：D1 竞态修复的**反向验证**（同一实例并发跑两次，≥6 轮）。

修复形状（见 `env.venv_dir` 的长注释）：venv 按 **(实例, run)** 隔离 ⇒ 两个并发 run
各有自己的 venv 与自己的唯一挂载点，**共享可变状态被消掉**。

判定标准（合同第 1 条）：
- **0 轮** infra_error、**0 轮**静默误判；
- 每轮结束后给出各 run 的 venv 里 `.pth` 的**指向**，逐个与其**所属 run 的工作区**一致；
- 且每个 venv 里**只有本 run 一个** editable 挂载点（多一个就说明共享没消干净）。

「静默误判」不看「跑通了」，而看：该 run 的判定**前后** import 探针是否都落在
**它自己的工作区**内（这正是 t4 round 4 失守的地方：跑完了，但判的是别人的工作区）。

用法：python t8_race_verify.py [轮数]
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
for p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E            # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402

PROBE = "pallets-click__4fd2fea"
OUT = os.path.join(E.RUNS_ROOT, "t8", "race")


def _worker(args):
    """子进程跑一次完整判定（模块级函数，ProcessPool 需可 pickle）。"""
    from eval.harness import run_instance as RI

    instance_id, run_id = args
    try:
        inst = RI.load_instance(instance_id)
        row = RI.run_one(instance_id, inst["patch"], run_id=run_id,
                         label="t8-race", patch_kind="gold")
        return {"run_id": run_id, "verdict_class": row.get("verdict_class"),
                "resolved": row.get("resolved"), "notes": row.get("notes", []),
                "workspace": row.get("workspace"), "venv": row.get("venv"),
                "import_probe_before_judge": row.get("import_probe_before_judge"),
                "import_probe_after_judge": row.get("import_probe_after_judge")}
    except Exception as exc:  # noqa: BLE001
        import traceback
        return {"run_id": run_id, "verdict_class": "WORKER_EXC", "resolved": False,
                "notes": [f"{type(exc).__name__}: {exc}"], "workspace": None,
                "venv": None, "traceback": traceback.format_exc()[-600:]}


def norm(p):
    return os.path.normcase(os.path.realpath(p)).replace("\\", "/").rstrip("/")


def venv_mount_inventory(inst: dict, run_id: str):
    """本 run 的 venv 里有哪些 `.pth`，各自指向谁的路径。"""
    vd = E.venv_dir(inst, run_id)
    sp = os.path.join(vd, "Lib", "site-packages")
    own = E.editable_pth_name(inst["instance_id"], run_id)
    inv = {"venv": vd, "own_mount": own, "own_targets": [], "other_editable": [],
           "venv_exists": os.path.isdir(vd)}
    if not os.path.isdir(sp):
        return inv
    for f in sorted(os.listdir(sp)):
        if not f.endswith(".pth"):
            continue
        try:
            with open(os.path.join(sp, f), encoding="utf-8") as fh:
                body = fh.read()
        except OSError:
            continue
        targets = [t for t in body.split("'")[1::2]]
        if f == own:
            inv["own_targets"] = targets
        elif f.startswith("_eval_editable_"):
            inv["other_editable"].append(f)
    return inv


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    os.makedirs(OUT, exist_ok=True)
    inst = RI.load_instance(PROBE)

    print("=" * 78)
    print(f"t8 验收：D1 竞态修复验证（同一实例并发两次）  实例={PROBE}  轮数={rounds}")
    print("=" * 78)

    records = []
    for r in range(rounds):
        run_a, run_b = f"t8-race-r{r}A", f"t8-race-r{r}B"
        with ProcessPoolExecutor(max_workers=2) as pool:
            got = [f.result() for f in
                   [pool.submit(_worker, (PROBE, run_a)),
                    pool.submit(_worker, (PROBE, run_b))]]
        a, b = got[0], got[1]
        ws_a, ws_b = E.ws_path(run_a, PROBE), E.ws_path(run_b, PROBE)

        inv_a = venv_mount_inventory(inst, run_a)
        inv_b = venv_mount_inventory(inst, run_b)

        # ① 每个 run 的 venv 挂载点必须指向**自己**的工作区
        def mount_ok(inv, ws_own):
            want = norm(ws_own)
            return any(norm(t) == want or norm(t).startswith(want + "/")
                       for t in inv["own_targets"])

        ok_a, ok_b = mount_ok(inv_a, ws_a), mount_ok(inv_b, ws_b)
        # ② 每个 venv 里不应有其它同 harness 的 editable 挂载
        clean_a = not inv_a["other_editable"]
        clean_b = not inv_b["other_editable"]
        # ③ venv 本身必须互异（隔离的根）
        venv_distinct = norm(inv_a["venv"]) != norm(inv_b["venv"])

        # ④ 静默误判：结果是 agent_result 但探针不落在自己的工作区
        silent = []
        for tag, res, ws_own in (("A", a, ws_a), ("B", b, ws_b)):
            if res.get("verdict_class") != "agent_result":
                continue
            wn = norm(ws_own)
            pb, pa = res.get("import_probe_before_judge") or "", res.get("import_probe_after_judge") or ""
            good = (pb and (norm(pb) == wn or norm(pb).startswith(wn + "/"))
                    and pa and (norm(pa) == wn or norm(pa).startswith(wn + "/")))
            if not good:
                silent.append({"run": tag, "probe_before": pb, "probe_after": pa})
        infra = [x for x in got if x.get("verdict_class") in ("infra_error", "WORKER_EXC")]

        ok = (not infra and not silent and ok_a and ok_b and clean_a and clean_b
              and venv_distinct)
        rec = {
            "round": r, "run_a": run_a, "run_b": run_b,
            "a": {k: a.get(k) for k in ("verdict_class", "resolved")},
            "b": {k: b.get(k) for k in ("verdict_class", "resolved")},
            "venv_a": inv_a["venv"], "venv_b": inv_b["venv"],
            "venv_distinct": venv_distinct,
            "mount_a_ok": ok_a, "mount_b_ok": ok_b,
            "mount_a_clean": clean_a, "mount_b_clean": clean_b,
            "mount_a_targets": inv_a["own_targets"],
            "mount_b_targets": inv_b["own_targets"],
            "infra_round": bool(infra),
            "silent_misjudge": silent,
        }
        records.append(rec)
        print(f"\n  round {r}: {'OK  ' if ok else 'BAD '}"
              f"A={a.get('verdict_class')}/{a.get('resolved')}  "
              f"B={b.get('verdict_class')}/{b.get('resolved')}")
        print(f"      venv_A = {inv_a['venv']}   存在={inv_a['venv_exists']}")
        print(f"      venv_B = {inv_b['venv']}   存在={inv_b['venv_exists']}")
        print(f"      两 venv 互异 = {venv_distinct}")
        print(f"      挂载 A -> {inv_a['own_targets']}  (指向自己工作区={ok_a})")
        print(f"      挂载 B -> {inv_b['own_targets']}  (指向自己工作区={ok_b})")
        print(f"      其它 editable 挂载  A={inv_a['other_editable'] or '无'}  "
              f"B={inv_b['other_editable'] or '无'}")
        print(f"      A 判定前/后导入 = {a.get('import_probe_before_judge')} / "
              f"{a.get('import_probe_after_judge')}")
        print(f"      B 判定前/后导入 = {b.get('import_probe_before_judge')} / "
              f"{b.get('import_probe_after_judge')}")
        if silent:
            print(f"      !! 静默误判：{silent}")
        if infra:
            print(f"      !! infra：{[x.get('verdict_class') for x in infra]}")

    n_infra = sum(1 for x in records if x["infra_round"])
    n_silent = sum(1 for x in records if x["silent_misjudge"])
    n_mount = sum(1 for x in records if x["mount_a_ok"] and x["mount_b_ok"])
    n_clean = sum(1 for x in records if x["mount_a_clean"] and x["mount_b_clean"])
    n_distinct = sum(1 for x in records if x["venv_distinct"])
    print()
    print("=" * 78)
    print(f"结果：{rounds} 轮")
    print(f"  infra_error                    = {n_infra}   （要求 0）")
    print(f"  静默误判                        = {n_silent}   （要求 0）")
    print(f"  两 run 挂载各自指向自己工作区      = {n_mount}/{rounds}   （要求全对）")
    print(f"  每个 venv 里只有本 run 一个挂载    = {n_clean}/{rounds}   （要求全对）")
    print(f"  两 run 的 venv 互异               = {n_distinct}/{rounds}   （要求全对）")
    print("=" * 78)

    summary = {
        "probe_instance": PROBE, "rounds": rounds,
        "infra_rounds": n_infra, "silent_misjudge_rounds": n_silent,
        "rounds_mounts_correct": n_mount, "rounds_mounts_clean": n_clean,
        "rounds_venv_distinct": n_distinct,
        "pass": (n_infra == 0 and n_silent == 0 and n_mount == rounds
                 and n_clean == rounds and n_distinct == rounds),
        "records": records, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    dest = os.path.join(OUT, "race_after_fix.json")
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"落盘：{dest}")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())