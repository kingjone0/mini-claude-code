"""t8 修复前的**复现**：D1（同名 .pth 并发覆盖）与 D2（空证断言）。

必须在改代码之前跑一次，拿到「修之前是什么样」的基线 —— 否则「修好了」没有对照。

D1 复现：两个不同 run_id 并发跑**同一实例** ⇒ 它们写同一个 `.pth` 文件名
        （`_eval_editable_<sha256(instance_id)[:16]>.pth`），后写者覆盖先写者。
D2 复现：`mechanism_check` 里那条 `len(set(ws)) == len(ws)` 断言恒真 ——
        用两个**不同** instance_id 拼出来的字符串天然互异，与磁盘无关。

只读 + 只跑 harness 的公开入口，不改任何代码。证据落 eval-runs/t8/repro/。
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
OUT = os.path.join(E.RUNS_ROOT, "t8", "repro")


def _worker(args):
    """子进程：跑一次判定，返回结果摘要。

    ⚠ 必须模块级函数（ProcessPool 用 pickle 传函数引用）。
    """
    from eval.harness import run_instance as RI

    instance_id, run_id = args
    try:
        inst = RI.load_instance(instance_id)
        row = RI.run_one(instance_id, inst["patch"], run_id=run_id,
                         label="t8-race", patch_kind="gold")
        return {
            "run_id": run_id,
            "verdict_class": row.get("verdict_class"),
            "resolved": row.get("resolved"),
            "notes": row.get("notes", []),
            "venv": row.get("venv"),
            "workspace": row.get("workspace"),
        }
    except Exception as exc:  # noqa: BLE001
        import traceback
        return {"run_id": run_id, "verdict_class": "WORKER_EXC",
                "resolved": False, "notes": [f"{type(exc).__name__}: {exc}",
                                             traceback.format_exc()[-800:]]}


def pth_state(instance: dict):
    """返回该实例 venv 里所有 .pth 的 (文件名, 内容)。"""
    vd = E.venv_dir(instance)
    sp = os.path.join(vd, "Lib", "site-packages")
    out = {}
    if os.path.isdir(sp):
        for f in sorted(os.listdir(sp)):
            if f.endswith(".pth"):
                try:
                    with open(os.path.join(sp, f), encoding="utf-8") as fh:
                        out[f] = fh.read().strip()
                except OSError as exc:
                    out[f] = f"<读取失败 {exc}>"
    return vd, out


def main():
    os.makedirs(OUT, exist_ok=True)
    inst = RI.load_instance(PROBE)
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 6

    print("=" * 78)
    print(f"t8 复现（修复前）：D1 同名 .pth 并发覆盖   实例={PROBE}  轮数={rounds}")
    print("=" * 78)
    vd, pths = pth_state(inst)
    print(f"  venv = {vd}")
    print(f"  当前 .pth 文件名（注意：**不含 run_id**）:")
    for f in pths:
        print(f"    {f}")

    results = []
    for r in range(rounds):
        run_a = f"t8-repro-r{r}A"
        run_b = f"t8-repro-r{r}B"
        with ProcessPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(_worker, (PROBE, run_a)),
                    pool.submit(_worker, (PROBE, run_b))]
            got = [f.result() for f in futs]
        a, b = got[0], got[1]

        # 关键核对：**每个 worker 自己的判定，其导入应指向自己的工作区**
        vd2, pths2 = pth_state(inst)
        # .pth 内容里的工作区路径属于哪个 run
        owners = {}
        for fname, body in pths2.items():
            if f"_eval_editable_" in fname and "eval-runs" in body:
                for tag in ("A", "B"):
                    if f"{run_a if tag == 'A' else run_b}" in body:
                        owners[fname] = tag
        infra = [x for x in got if x.get("verdict_class") in ("infra_error", "WORKER_EXC")]
        # 静默误判：两边都 agent_result 但 .pth 只剩一个 run 的指向（后写者覆盖先写者）
        silent = False
        if not infra and all(x.get("verdict_class") == "agent_result" for x in got):
            # 检查 .pth 是否只指向其中一个 run（覆盖的证据）
            only_one = len(owners) == 1 if owners else False
            silent = only_one

        rec = {
            "round": r, "run_a": run_a, "run_b": run_b,
            "a": {k: a.get(k) for k in ("verdict_class", "resolved")},
            "b": {k: b.get(k) for k in ("verdict_class", "resolved")},
            "pth_files": list(pths2.keys()),
            "pth_owner_by_run": owners,
            "infra_round": bool(infra),
            "silent_misjudge_round": bool(silent),
        }
        results.append(rec)
        bad = "  <<< 出问题" if (infra or silent) else ""
        print(f"  round {r}: A={a.get('verdict_class')}/{a.get('resolved')}  "
              f"B={b.get('verdict_class')}/{b.get('resolved')}  "
              f".pth={list(pths2.keys())}  归属={owners}{bad}")

    n_infra = sum(1 for x in results if x["infra_round"])
    n_silent = sum(1 for x in results if x["silent_misjudge_round"])
    print()
    print(f"  复现结果：{rounds} 轮里 infra_error {n_infra} 轮、静默误判 {n_silent} 轮")
    print("  （t4 当时的复现率是 6 轮 4 触发；这里若 >0 即证明缺陷仍在）")

    # ---- D2：空证断言
    print()
    print("=" * 78)
    print("D2 复现：mechanism_check 里那条断言恒真（与磁盘无关）")
    print("=" * 78)
    par_id = "t8-repro-vacuous"
    # 完全照抄 mechanism_check.py:194 的写法
    fake_instances = [{"instance_id": "pallets-click__4fd2fea"},
                      {"instance_id": "pytest-dev-pluggy__cbf58d5"}]
    ws = [p["instance_id"] and E.ws_path(par_id, p["instance_id"]) for p in fake_instances]
    print(f"  两个 instance_id 拼出来的 ws = {ws}")
    print(f"  len(set(ws)) == len(ws) -> {len(set(ws)) == len(ws)}   <- 恒真")
    # 这条断言比的是**拼出来的字符串**，根本不看磁盘。两个不同实例天然得到两个不同字符串
    # ⇒ 断言与「工作区真的隔离」之间没有逻辑联系。它无法拦住的典型情形：
    # 有人把两个实例的 ws_path 配成同一个真实目录（或在同一路径上复用工作区）。
    print()
    print("  ⇒ 这条断言只证明「两个字符串不同」，不证明「两个真实工作区不同」。")
    print("     要证明隔离必须比**磁盘上真实存在的路径**（realpath），而不是拼出来的路径。")
    print("     真实核对见 t8_race_verify.py 的 pth 归属检查与 mechanism_check 的修订版断言。")

    summary = {
        "probe_instance": PROBE,
        "rounds": rounds,
        "infra_rounds": n_infra,
        "silent_misjudge_rounds": n_silent,
        "records": results,
        "d2_vacuous_proof": {
            "assertion": "len(set(ws)) == len(ws)",
            "always_true_because": "ws_path() 由 run_id+instance_id 拼字符串，天然互异",
            "two_different_instance_ids": ws,
            "same_real_path_case_still_true": True,
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    dest = os.path.join(OUT, "repro_before_fix.json")
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n  落盘：{dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())