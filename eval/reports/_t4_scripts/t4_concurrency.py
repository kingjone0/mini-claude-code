"""t4 复核：并发隔离 —— 比 mechanism_check 的版本更严。

mechanism_check.check_concurrency_isolation 断言的是
    ws = [E.ws_path(par_id, r["instance_id"]) ...]; len(set(ws)) == len(ws)
这是**恒真**的（路径按 instance_id 拼出来，天然互异），所以它证明不了任何隔离性质。

本脚本做三件它没做的事：
  (1) ≥4 并发 vs 串行各跑**同一批实例**，逐条比对 verdict_class / resolved / F2P / P2P；
  (2) **同一实例并发触发两次**（两个不同 run_id，同时跑），检查：
        - 两边 verdict 一致
        - 两个工作区/证据目录真的分属不同路径
        - venv 是共享的（同一 base_commit）=> 检查 pth 挂载点没有被对方改坏
        - 两边跑完后，各自 artifacts 的 judged.json 结论一致
  (3) 逐条记录实际磁盘路径，证明不是「同一目录被两个进程写」。

用法：python t4_concurrency.py <batch_run_id>
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

PROJ = r"E:\aicoding\mini-claude-code"
for p in (PROJ, os.path.join(PROJ, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E            # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402

OUT = r"E:\aicoding\eval-runs\t4\concurrency"
# 4 个仓库各一条，确保并发时 venv / 工作区都不同
IDS = ["pallets-click__4fd2fea", "pytest-dev-pluggy__cbf58d5",
       "more-itertools-more-itertools__1b7f033", "astanin-python-tabulate__9dedd45"]


def _one(task):
    """子进程入口：跑一条实例，返回精简结论（必须模块级函数，才可 pickle）。"""
    iid = task["iid"]
    inst = RI.load_instance(iid)
    row = RI.run_one(iid, inst["patch"], run_id=task["run_id"], label=task["label"],
                     patch_kind="gold", keep_workspace=False)
    j = row.get("judged") or {}
    return {
        "iid": iid, "run_id": task["run_id"], "label": task["label"],
        "verdict_class": row["verdict_class"], "resolved": row["resolved"],
        "f2p_not_passed": j.get("f2p_not_passed"), "f2p_total": j.get("f2p_total"),
        "p2p_not_passed_count": len(j.get("p2p_not_passed") or []),
        "p2p_total": j.get("p2p_total"),
        "workspace": row.get("workspace"), "venv": row.get("venv"),
        "venv_python": row.get("venv_python"),
        "artifacts_dir": E.art_dir(task["run_id"], iid),
        "exit_code": row.get("exit_code"), "timed_out": row.get("timed_out"),
        "wall_seconds": (row.get("pytest") or {}).get("wall_seconds"),
        "notes": row.get("notes", []),
    }


def run_batch(run_id, ids, workers, label):
    tasks = [{"iid": i, "run_id": run_id, "label": label} for i in ids]
    out = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, t): t for t in tasks}
        for fut in as_completed(futs):
            out.append(fut.result())
    return out


def main():
    batch = sys.argv[1] if len(sys.argv) > 1 else "t4-conc"
    os.makedirs(OUT, exist_ok=True)
    report = {}

    # ---------- (1) 串行 vs 4 并发 ----------
    print("=" * 70)
    print(f"(1) 串行 vs 4 并发，同一批 {len(IDS)} 条实例")
    print("=" * 70)
    ser = run_batch(f"{batch}-serial", IDS, 1, "serial")
    par = run_batch(f"{batch}-parallel", IDS, 4, "parallel")
    s_map = {r["iid"]: r for r in ser}
    p_map = {r["iid"]: r for r in par}
    diff = []
    rows = []
    for iid in IDS:
        s, p = s_map[iid], p_map[iid]
        same = (s["verdict_class"] == p["verdict_class"] and s["resolved"] == p["resolved"]
                and s["f2p_not_passed"] == p["f2p_not_passed"]
                and s["p2p_not_passed_count"] == p["p2p_not_passed_count"])
        if not same:
            diff.append(iid)
        rows.append({"instance_id": iid, "serial": s, "parallel": p, "same": same})
        print(f"  {'OK  ' if same else 'DIFF'} {iid}")
        print(f"       串行 {s['verdict_class']}/{s['resolved']} "
              f"F2P未过={s['f2p_not_passed']} P2P未过={s['p2p_not_passed_count']} "
              f"exit={s['exit_code']}")
        print(f"       并发 {p['verdict_class']}/{p['resolved']} "
              f"F2P未过={p['f2p_not_passed']} P2P未过={p['p2p_not_passed_count']} "
              f"exit={p['exit_code']}")
    report["serial_vs_parallel"] = {"rows": rows, "diff": diff}

    # ---------- (2) 同一实例并发触发两次 ----------
    print()
    print("=" * 70)
    print("(2) 同一实例被并发触发两次（两个不同 run_id 同时跑）")
    print("=" * 70)
    dup_iid = IDS[0]
    dup_ids = [dup_iid, dup_iid]
    with ProcessPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_one, {"iid": dup_iid, "run_id": f"{batch}-dupA", "label": "dupA"})
        f2 = pool.submit(_one, {"iid": dup_iid, "run_id": f"{batch}-dupB", "label": "dupB"})
        ra, rb = f1.result(), f2.result()
    print(f"  dupA {ra['verdict_class']}/{ra['resolved']} ws={ra['workspace']}")
    print(f"  dupB {rb['verdict_class']}/{rb['resolved']} ws={rb['workspace']}")
    same_verdict = (ra["verdict_class"] == rb["verdict_class"]
                    and ra["resolved"] == rb["resolved"]
                    and ra["f2p_not_passed"] == rb["f2p_not_passed"]
                    and ra["p2p_not_passed_count"] == rb["p2p_not_passed_count"])
    distinct_ws = ra["workspace"] != rb["workspace"]
    distinct_art = ra["artifacts_dir"] != rb["artifacts_dir"]
    shared_venv = ra["venv"] == rb["venv"]
    # venv 共享时：两边 artifacts 里的 judged.json 必须一致
    ja = os.path.join(ra["artifacts_dir"], "judged.json")
    jb = os.path.join(rb["artifacts_dir"], "judged.json")
    judged_same = (os.path.exists(ja) and os.path.exists(jb)
                   and json.load(open(ja, encoding="utf-8"))
                   == json.load(open(jb, encoding="utf-8")))
    print(f"  两次结论一致        : {same_verdict}")
    print(f"  工作区路径互异      : {distinct_ws}  ({ra['workspace']} vs {rb['workspace']})")
    print(f"  证据目录互异        : {distinct_art}")
    print(f"  venv 共享（同 base）: {shared_venv}  ({ra['venv']})")
    print(f"  两边 judged.json 一致: {judged_same}")
    # 两边工作区必须都已清理（keep_workspace=False）
    gone = (not os.path.isdir(ra["workspace"])) and (not os.path.isdir(rb["workspace"]))
    print(f"  两边工作区均已清理  : {gone}")
    report["same_instance_twice"] = {
        "dupA": ra, "dupB": rb, "same_verdict": same_verdict,
        "distinct_workspaces": distinct_ws, "distinct_artifacts": distinct_art,
        "shared_venv": shared_venv, "judged_same": judged_same,
        "workspaces_cleaned": gone,
    }

    # ---------- (3) 路径普查：并发批内每个实例的 ws/venv 唯一性 ----------
    print()
    print("=" * 70)
    print("(3) 并发批内路径普查（真实磁盘路径，不是拼出来的）")
    print("=" * 70)
    wss = [r["workspace"] for r in par]
    vvs = [r["venv"] for r in par]
    print(f"  工作区唯一：{len(set(wss))}/{len(wss)}   {wss}")
    print(f"  venv 唯一  ：{len(set(vvs))}/{len(vvs)}   {vvs}")
    report["path_uniqueness"] = {"workspaces": wss, "venvs": vvs,
                                 "ws_unique": len(set(wss)) == len(wss),
                                 "venv_unique": len(set(vvs)) == len(vvs)}

    ok = (not diff and same_verdict and distinct_ws and distinct_art and judged_same
          and report["path_uniqueness"]["ws_unique"])
    report["overall_ok"] = ok
    with open(os.path.join(OUT, "concurrency.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print()
    print(f"并发隔离总判定：{'PASS' if ok else 'FAIL'}   -> {os.path.join(OUT, 'concurrency.json')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())