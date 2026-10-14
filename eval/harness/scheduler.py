"""进程池并发调度 + 状态落盘 + 断点续跑（预注册 §8.3 / §8.5）。

三条设计约束：

1. **并发单位 = 实例，用 `ProcessPoolExecutor`**（不是线程池，§8.3）。worker 之间不共享
   内存状态；每个 worker 只读写自己的 `eval-runs/<run_id>/<iid>/` 与对应 venv。
2. **状态落盘 = append-only 的 jsonl**（§8.5 要求断点续跑）。每完成一条就 flush 一行；
   重启后先读 jsonl 建「已完成」集合，`stage == judged` 的**跳过**。
   为什么用 jsonl 而不是只靠 `state.json`：写一行是原子的（一次 write + flush），
   而「改一个 JSON 文件」在并发/中断下会留下半截文件 —— 那会让续跑读到一个假的完成态。
3. **失败重试**：`infra_error` / `timeout` 允许**最多 2 次** infra 重试（§5.4）；
   重试仍失败 => 该实例标 `excluded_infra`，批次规模减 1。**禁止**把它们改标成
   `not_resolved` 来凑分母。

用法：
    python eval/harness/scheduler.py --run-id batch-01 --max-workers 3
    python eval/harness/scheduler.py --run-id batch-01 --patch-kind gold --set pilot
    python eval/harness/scheduler.py --run-id batch-01 --resume          # 断点续跑
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.dirname(HERE)
PROJECT_ROOT = os.path.dirname(EVAL_DIR)
for p in (PROJECT_ROOT, EVAL_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E                      # noqa: E402
from eval.harness import judge as J                    # noqa: E402

# §8.4 写死：并发默认 3（>3 会把 pytest 计时拖长，造成系统性 timeout 偏置）
DEFAULT_WORKERS = 3
# §5.4 写死：infra/timeout 最多 2 次 infra 重试
MAX_INFRA_RETRIES = 2
# §8.4 全批次预算 8 小时
BATCH_BUDGET_SECONDS = 8 * 3600

INSTANCES_DIR = os.path.join(EVAL_DIR, "instances")


# ---------------------------------------------------------------- 状态文件

def state_jsonl(run_id: str) -> str:
    return os.path.join(E.run_root(run_id), "state.jsonl")


def read_state(run_id: str):
    """读回已落盘的状态。返回 {instance_id: 最后一条记录}。

    ⚠ 逐行容错：最后一行可能是被中断截断的半行 JSON —— 跳过它，不让整批续跑失败。
    """
    path = state_jsonl(run_id)
    latest = {}
    if not os.path.exists(path):
        return latest
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue                 # 半行：忽略
            if row.get("instance_id"):
                latest[row["instance_id"]] = row
    return latest


def append_state(run_id: str, row: dict):
    """追加一行状态并**立刻 flush + fsync** —— 这是断点续跑唯一的真相来源。"""
    path = state_jsonl(run_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def load_instances(which_set: str, only=None):
    ids = []
    for name in sorted(os.listdir(INSTANCES_DIR)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        with open(os.path.join(INSTANCES_DIR, name), encoding="utf-8") as f:
            obj = json.load(f)
        m = obj.get("set_membership")
        if which_set == "pilot" and m != "pilot-12":
            continue
        if which_set == "reserve" and m not in ("pilot-12", "reserve"):
            continue
        if only and obj["instance_id"] not in only:
            continue
        ids.append(obj["instance_id"])
    return ids


# ---------------------------------------------------------------- worker

def _worker(task: dict) -> dict:
    """子进程入口。**必须能在子进程里独立 import**（ProcessPool 用 spawn 语义 pickle
    函数引用，模块级函数才可 pickle —— 闭包/局部函数会报 PicklingError）。
    """
    from eval.harness import run_instance as RI

    iid = task["instance_id"]
    try:
        if task.get("patch_kind") in ("gold", "empty", "wrong"):
            inst = RI.load_instance(iid)
            patch_text = {"gold": inst["patch"], "empty": "",
                          "wrong": inst["wrong_patch"]}[task["patch_kind"]]
            kind = task["patch_kind"]
        elif task.get("patch_file"):
            with open(task["patch_file"], encoding="utf-8") as f:
                patch_text = f.read()
            kind = "file:" + os.path.basename(task["patch_file"])
        else:
            patch_text, kind = "", "none"
        row = RI.run_one(iid, patch_text, run_id=task["run_id"], label=task["label"],
                         patch_kind=kind, keep_workspace=task.get("keep_workspace", False))
        return row
    except Exception as exc:  # noqa: BLE001 —— 子进程里任何异常都不能让整个池挂掉
        import traceback
        return {
            "instance_id": iid, "run_id": task["run_id"], "label": task["label"],
            "verdict_class": J.HARNESS_BUG, "resolved": False,
            "notes": [f"worker 异常：{type(exc).__name__}: {exc}",
                      traceback.format_exc()[-1500:]],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }


# ---------------------------------------------------------------- 主流程

def run_batch(run_id: str, ids, max_workers: int, label: str, patch_kind: str,
              resume: bool, keep_workspace: bool) -> dict:
    """跑一批实例。返回汇总 dict，同时把汇总写 `<run_root>/manifest.json`。"""
    prior = read_state(run_id) if resume else {}
    if not resume and os.path.exists(state_jsonl(run_id)):
        # 不续跑 => 该 run_id 的历史作废，但**不删**文件（证据只增不减），改名留档
        stamp = time.strftime("%Y%m%d-%H%M%S")
        os.rename(state_jsonl(run_id), state_jsonl(run_id) + f".superseded-{stamp}")

    pending = []
    skipped = []
    for iid in ids:
        prev = prior.get(iid)
        if prev and prev.get("stage") == "judged":
            skipped.append({"instance_id": iid, "verdict_class": prev.get("verdict_class"),
                            "resolved": prev.get("resolved"),
                            "source": "state.jsonl（已完成，跳过）"})
        else:
            pending.append(iid)

    print("=" * 78)
    print(f"批次 {run_id}  |  实例 {len(ids)}  并发 {max_workers}  "
          f"补丁={patch_kind}  续跑={resume}")
    print(f"已完成跳过 {len(skipped)}  待跑 {len(pending)}")
    print(f"状态文件：{state_jsonl(run_id)}")
    print("=" * 78)
    for s in skipped:
        print(f"  [skip] {s['instance_id']}  已判：{s['verdict_class']} "
              f"resolved={s['resolved']}")

    started = time.time()
    rows = list(skipped)
    if pending:
        tasks = [{"instance_id": iid, "run_id": run_id, "label": label,
                  "patch_kind": patch_kind, "keep_workspace": keep_workspace}
                 for iid in pending]
        # 重试表：infra/timeout 允许 MAX_INFRA_RETRIES 次（§5.4）
        attempts = {iid: 0 for iid in pending}
        queue = list(tasks)
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            while queue:
                if time.time() - started > BATCH_BUDGET_SECONDS:
                    print(f"[中止] 全批次预算 {BATCH_BUDGET_SECONDS}s 用尽，停止派发"
                          f"（§8.4：如实报告已完成部分）")
                    break
                futures = {pool.submit(_worker, t): t for t in queue}
                queue = []
                for fut in as_completed(futures):
                    task = futures[fut]
                    iid = task["instance_id"]
                    try:
                        row = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        row = {"instance_id": iid, "run_id": run_id, "label": label,
                               "verdict_class": J.HARNESS_BUG, "resolved": False,
                               "notes": [f"future 异常：{exc}"]}
                    append_state(run_id, row)
                    rows.append(row)
                    print(f"  [{row.get('verdict_class','?'):<13}] {iid}  "
                          f"resolved={row.get('resolved')}")
                    # §5.4：infra_error / timeout 可重试；超限则标 excluded_infra
                    if row.get("verdict_class") in (J.INFRA_ERROR, J.TIMEOUT):
                        attempts[iid] = attempts.get(iid, 0) + 1
                        if attempts[iid] <= MAX_INFRA_RETRIES:
                            print(f"         ~ infra 重试 {attempts[iid]}/{MAX_INFRA_RETRIES}")
                            queue.append(task)
                        else:
                            excluded = dict(row)
                            excluded["verdict_class"] = "excluded_infra"
                            excluded["notes"] = list(row.get("notes") or []) + [
                                f"infra/timeout 重试 {MAX_INFRA_RETRIES} 次仍失败 => "
                                f"excluded_infra（§5.4：批次规模减 1，不计入分母、不计作 agent 失败）"]
                            append_state(run_id, excluded)
                            rows.append(excluded)
                            print(f"         X excluded_infra（{iid}）")

    # 汇总：同实例取最后一条（重试/排除会追加）
    final = {}
    for r in rows:
        final[r["instance_id"]] = r
    ordered = [final[i] for i in ids if i in final]
    summary = J.summarize(ordered)
    done = time.time() - started
    manifest = {
        "run_id": run_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "max_workers": max_workers,
        "label": label,
        "patch_kind": patch_kind,
        "resume": resume,
        "requested": len(ids),
        "skipped_completed": len(skipped),
        "wall_seconds": round(done, 2),
        "summary": summary,
        "state_jsonl": state_jsonl(run_id),
        "instances": [
            {"instance_id": r["instance_id"],
             "verdict_class": r.get("verdict_class"),
             "resolved": r.get("resolved"),
             "workspace": r.get("workspace"),
             "venv": r.get("venv"),
             "artifacts": r.get("artifacts", []),
             "notes": r.get("notes", [])}
            for r in ordered],
    }
    E.write_text(os.path.join(E.run_root(run_id), "manifest.json"),
                 json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    print("\n" + "=" * 78)
    print(f"批次 {run_id} 完成  wall={done:.1f}s")
    print(f"  分母（agent_result+empty_patch+patch_conflict）={summary['denominator']}")
    print(f"  resolved={summary['resolved']}  "
          f"resolved_rate={summary['resolved_rate']}")
    print(f"  分类计数：{summary['counts']}")
    print(f"  infra/timeout（可重试，不计作 agent 失败）={summary['infra_retryable']}")
    print(f"  清单：{os.path.join(E.run_root(run_id), 'manifest.json')}")
    print("=" * 78)
    return manifest


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="进程池并发 + 断点续跑（预注册 §8.3/§8.5）")
    ap.add_argument("--run-id", required=True, help="批次标识（决定 eval-runs/<run_id>/）")
    ap.add_argument("--set", default="pilot", choices=("pilot", "reserve"))
    ap.add_argument("--instance", action="append", help="只跑指定 instance_id（可重复）")
    ap.add_argument("--max-workers", type=int, default=DEFAULT_WORKERS,
                    help=f"并发实例数（§8.4 默认 {DEFAULT_WORKERS}）")
    ap.add_argument("--label", default="batch")
    ap.add_argument("--patch-kind", default="gold", choices=("gold", "empty", "wrong", "none"),
                    help="批量判定用哪种补丁（gold 用于回归验证 harness 自身）")
    ap.add_argument("--patch-file", help="对全部实例用同一个候选补丁文件（单实例时才有意义）")
    ap.add_argument("--resume", action="store_true", help="断点续跑：跳过 state.jsonl 里已判完的")
    ap.add_argument("--keep-workspace", action="store_true")
    args = ap.parse_args(argv)

    ids = load_instances(args.set, only=set(args.instance) if args.instance else None)
    if not ids:
        print("[中止] 没有匹配的实例")
        return 1
    run_batch(args.run_id, ids, args.max_workers, args.label, args.patch_kind,
              args.resume, args.keep_workspace)
    return 0


if __name__ == "__main__":
    sys.exit(main())