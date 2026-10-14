"""P1 三臂交错调度器（预注册 §3.2 / §5.5）。

**与 P0 scheduler.py 的关系**：P0 的 `scheduler.py` 是「一批实例 × 一种补丁」的并发调度；
P1 在它之上加一层「每实例连跑 A→B→C」的交错循环，并把「agent 运行」委托给
`coding_agent.py --repo-root ... --problem-file ... --label p1-<iid>-<arm> [臂开关]`。

⚠️ **本脚本不直接 import coding_agent**：它只是按 `arms.ARM_EXTRA_ARGS` 拼命令行、
spawn 子进程、采集产物。这样「臂定义」只有 `arms.py` 一个真值源（§1.3 / W1）。

用法：
    python eval/harness/scheduler_p1.py --run-tag p1-batch1 \
        --set pilot --arms A,B,C --max-workers 3
    python eval/harness/scheduler_p1.py --run-tag p1-batch1 --resume   # 断点续跑

产物目录：`E:\\aicoding\\eval-runs\\p1-<UTCyyyymmddTHHMMSS>-<tag>/`
每条 (instance, arm) 一个 run_id（§5.5），各自有独立工作区与 venv。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.dirname(HERE)
PROJECT_ROOT = os.path.dirname(EVAL_DIR)
for p in (PROJECT_ROOT, EVAL_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import arms as A          # noqa: E402
from eval.harness import env as E           # noqa: E402
from eval.harness import judge as J         # noqa: E402
from eval.harness import scheduler as S     # noqa: E402  (复用 load_instances / state io)
from eval.harness import run_instance as RI # noqa: E402  (复用实例准备链)

PYTHON = r"D:\Anaconda3\envs\aicoding\python.exe"
CODING_AGENT = os.path.join(PROJECT_ROOT, "coding_agent.py")

# REV2 §2：集中预算配置（不把 wiring 自检旧常数带入正式跑批）。
REV2_MAX_STARTED = 75
REV2_WALL_SECONDS = 12 * 3600
REV2_INPUT_UNCACHED = 6_300_000
REV2_CACHE_READ = 126_000_000
REV2_COST_USD = 225.0
REV2_INITIAL_STARTED = int(os.environ.get("P1_REV2_INITIAL_STARTED", "35"))
REV2_USAGE_ROOT = os.environ.get("P1_USAGE_ROOT", os.path.join(PROJECT_ROOT, "servers", "experiments", "out"))
INFRA_STOP_CONSECUTIVE = 2


def log(msg):
    text = f"[p1-sched] {msg}"
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", "replace") + b"\n")
        sys.stdout.buffer.flush()


# ---------------------------------------------------------------- 状态（按 (instance, arm) 粒度）

def triple_state_jsonl(run_root: str) -> str:
    return os.path.join(run_root, "triple_state.jsonl")


def read_triple_state(run_root: str) -> dict[tuple[str, str], dict]:
    """读回已完成槽位：{(instance_id, arm): last_row}。"""
    path = triple_state_jsonl(run_root)
    out: dict[tuple[str, str], dict] = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = row.get("instance_id")
            arm = row.get("arm")
            if iid and arm:
                out[(iid, arm)] = row
    return out


def count_spawned_arms(run_root: str) -> int:
    """Count physical subprocess attempts, not distinct (instance, arm) keys.

    Preparation failures are state rows without started_at_epoch and never
    spawned coding_agent; malformed/incomplete JSONL tail is not an attempt.
    """
    path = triple_state_jsonl(run_root)
    if not os.path.isfile(path):
        return 0
    count = 0
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and (row.get("spawned") is True or
                                          ("spawned" not in row and
                                           row.get("started_at_epoch") is not None)):
                # Legacy rows predate the explicit spawn marker; started_at_epoch
                # is their only durable subprocess-attempt proxy.
                count += 1
    return count


def policy_isolated_artifact(row: dict) -> str:
    """Return the run-relative C policy evidence index for state artifacts.

    State ``artifacts`` entries are absolute paths except this one deliberate
    index. Resolve it against the batch ``run_root`` (not the worktree).
    """
    iid = row.get("instance_id", "")
    return f"{iid}/artifacts/policy_evidence_isolated.json"


def _write_policy_evidence_for_run(row: dict, art: str) -> str | None:
    """Materialize isolated C evidence before the worker returns its state row.

    The durable dispatch log is read-only input; this creates only the current
    run's artifact and never edits an existing state row or verdict fields.
    """
    if row.get("arm") != "C":
        return None
    target = os.path.join(art, "policy_evidence_isolated.json")
    dispatch_log = os.path.join(PROJECT_ROOT, "servers", "experiments", "out",
                                "policy_dispatch.jsonl")
    dispatch_rows = []
    if os.path.isfile(dispatch_log):
        with open(dispatch_log, encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("label") != row.get("label"):
                    continue
                try:
                    event_epoch = datetime.fromisoformat(item["ts"]).timestamp()
                except (KeyError, TypeError, ValueError):
                    continue
                if event_epoch >= row["started_at_epoch"]:
                    dispatch_rows.append(item)
    payload = {"run_id": row.get("run_id"), "label": row.get("label"),
               "started_at_epoch": row.get("started_at_epoch"),
               "dispatch_rows": dispatch_rows,
               "note": "worker-generated isolated evidence; no inference"}
    E.write_text(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return target


def plan_resume(plan: list[dict], prior: dict):
    """Treat judged, timeout and infra rows as immutable terminal state."""
    skipped, pending, skipped_failed = [], [], 0
    terminal = {"judged", "failed_infra"}
    for slot in plan:
        prev = prior.get((slot["instance_id"], slot["arm"]))
        if prev and (prev.get("stage") in terminal or
                     prev.get("verdict_class") in (J.TIMEOUT, J.INFRA_ERROR, J.HARNESS_BUG)):
            item = dict(slot, prev_verdict=prev.get("verdict_class"),
                        prev_resolved=prev.get("resolved"))
            skipped.append(item)
            if prev.get("verdict_class") in (J.TIMEOUT, J.INFRA_ERROR, J.HARNESS_BUG):
                skipped_failed += 1
        else:
            pending.append(slot)
    return skipped, pending, skipped_failed


def append_triple_state(run_root: str, row: dict):
    path = triple_state_jsonl(run_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _usage_totals(root: str = REV2_USAGE_ROOT) -> dict:
    """汇总 usage.jsonl，字段缺失按零；不触发任何模型调用。"""
    totals = {"input_uncached": 0, "cache_read": 0, "cost_usd": 0.0}
    if not os.path.isdir(root):
        return totals
    for name in ("usage.jsonl", "policy_scout_usage.jsonl"):
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not str(row.get("label") or "").startswith("p1-"):
                    continue
                for key in ("input_uncached", "cache_read"):
                    try:
                        totals[key] += int(row.get(key) or 0)
                    except (TypeError, ValueError):
                        pass
                try:
                    totals["cost_usd"] += float(row.get("cost_usd") or row.get("total_cost_usd") or 0)
                except (TypeError, ValueError):
                    pass
    return totals


def _budget_snapshot(run_root: str, *, resume: bool, in_flight: int = 0) -> dict:
    started = REV2_INITIAL_STARTED + count_spawned_arms(run_root) + in_flight
    usage = _usage_totals()
    return {"started_arms": started, "input_uncached": usage["input_uncached"],
            "cache_read": usage["cache_read"], "cost_usd": round(usage["cost_usd"], 6)}


def _stop_reason(snapshot: dict, started_at: float, consecutive: dict[str, int]) -> str | None:
    if snapshot["started_arms"] >= REV2_MAX_STARTED:
        return f"started_arms {snapshot['started_arms']} >= {REV2_MAX_STARTED}"
    if time.time() - started_at >= REV2_WALL_SECONDS:
        return f"wall_seconds >= {REV2_WALL_SECONDS}"
    if snapshot["input_uncached"] >= REV2_INPUT_UNCACHED:
        return f"input_uncached >= {REV2_INPUT_UNCACHED}"
    if snapshot["cache_read"] >= REV2_CACHE_READ:
        return f"cache_read >= {REV2_CACHE_READ}"
    if snapshot["cost_usd"] >= REV2_COST_USD:
        return f"cost_usd >= {REV2_COST_USD}"
    for arm, count in consecutive.items():
        if count >= INFRA_STOP_CONSECUTIVE:
            return f"{arm} consecutive infra/timeout >= {INFRA_STOP_CONSECUTIVE}"
    return None


# ---------------------------------------------------------------- worker

def judge_arm_workspace(row: dict, inst: dict, *, patch_override: str | None = None) -> dict:
    """P1 candidate -> P0-compatible independent pytest verdict and artifacts.

    patch_override is reserved for offline gold/empty mechanism checks; production
    always collects the candidate from the agent's workspace.
    """
    iid, run_id, ws = row["instance_id"], row["run_id"], row["workspace"]
    art = E.art_dir(run_id, iid)
    if patch_override is None:
        patch, before_eol, after_eol, _ = RI.collect_agent_patch(ws, art)
    else:
        patch = patch_override
        before_eol = J.eol_stats(patch)
        patch = J.normalize_eol_patch(patch)
        after_eol = J.eol_stats(patch)
    kept, test_side, dropped, touched_tests, test_paths = J.stripped_patch(patch)
    E.write_text(os.path.join(art, "candidate_patch_raw.diff"), patch)
    E.write_text(os.path.join(art, "candidate_patch_judged.diff"), kept)
    row["candidate_patch_sha256"] = E.sha256_text(patch)
    row["judged_patch_sha256"] = E.sha256_text(kept)
    row["eol_before"], row["eol_after"] = before_eol, after_eol
    row["dropped_non_py_paths"] = dropped
    row["touched_tests"], row["test_side_paths"] = touched_tests, test_paths
    row["stripped_test_patch_sha256"] = E.sha256_text(test_side) if touched_tests else None
    row["artifacts"] += [os.path.join(art, "candidate_patch_raw.diff"),
                         os.path.join(art, "candidate_patch_judged.diff")]

    # The agent may have altered tests. Reset, then apply only the source-side
    # candidate on the frozen test_patch, as run_instance.run_one does.
    E.reset_ws(ws)
    applied_test = RI.apply(ws, inst.get("test_patch") or "", art)
    if applied_test.rc != 0:
        raise E.InfraError(f"test_patch 无法应用：{applied_test.stderr[-300:]}")
    if not kept.strip():
        row["notes"].append("测试侧剥离后候选补丁为空 => empty_patch")
    applied_candidate = RI.apply(ws, kept, art)
    row["patch_apply_exit_code"] = applied_candidate.rc
    if applied_candidate.rc != 0:
        row["verdict_class"], row["resolved"] = J.PATCH_CONFLICT, False
        row["notes"].append("候选补丁无法应用 => patch_conflict")
    else:
            py = E.venv_py(inst, run_id)
            pkg = RI.infer_import_name(ws)
            if not pkg:
                raise E.InfraError("判定前无法推断 import 名")
            row["import_probe_before_judge"] = E.assert_pkg_from_workspace(py, ws, pkg)
            node_ids = list(inst.get("FAIL_TO_PASS") or []) + list(inst.get("PASS_TO_PASS") or [])
            run = RI.pytest_run(ws, py, art, node_ids, inst.get("collection_scope"))
            RI.save_run_evidence(art, "verdict_run", run)
            judged = J.judge(inst["FAIL_TO_PASS"], inst["PASS_TO_PASS"], run["results"])
            row["judged"] = judged
            row["pytest"] = {"cmd": run["cmd"], "exit_code": run["returncode"],
                             "timed_out": run["timed_out"], "wall_seconds": run["wall_seconds"]}
            row["judge_exit_code"] = run["returncode"]
            row["judge_timed_out"] = run["timed_out"]
            row["verdict_class"], row["resolved"] = J.verdict_from(J.classify_run(run), judged)
            row["import_probe_after_judge"] = E.assert_pkg_from_workspace(py, ws, pkg)
            if row["import_probe_after_judge"] != row["import_probe_before_judge"]:
                row["verdict_class"], row["resolved"] = J.HARNESS_BUG, False
                row["notes"].append("判定前后 import 路径不一致")
            E.write_text(os.path.join(art, "judged.json"),
                         json.dumps(judged, ensure_ascii=False, indent=2) + "\n")
            row["artifacts"] += [os.path.join(art, "verdict_run.txt"),
                                 os.path.join(art, "judged.json"), run["report_path"]]
            row["notes"].append("P0 同源独立 pytest 判定完成（测试侧改动已剥离）")
    row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    row["stage"] = "failed_infra" if row["verdict_class"] in (J.TIMEOUT, J.INFRA_ERROR) else "judged"
    result_path = os.path.join(art, "result.json")
    row["artifacts"].append(result_path)
    E.write_text(result_path, json.dumps(row, ensure_ascii=False, indent=2) + "\n")
    return row


def _run_one_arm(task: dict) -> dict:
    """子进程入口：按 task 拼 coding_agent.py 命令行并 spawn。

    ⚠️ 必须能在子进程里 pickle（ProcessPool spawn 语义）⇒ 模块级函数。
    """
    iid = task["instance_id"]
    arm = task["arm"]
    run_id = task["run_id"]
    label = task["label"]
    ws = task["workspace"]
    problem_file = task["problem_file"]
    extra_args = list(task["extra_args"])
    arm_report_file = task["arm_report_file"]

    started = time.time()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    row = A.empty_row(instance_id=iid, arm=arm, run_id=run_id, label=label)
    row["started_at"] = started_at
    row["started_at_epoch"] = int(started)
    row["spawned"] = False
    row["workspace"] = ws
    row["venv"] = E.venv_dir({"repo": task["repo"], "base_commit": task["base_commit"]},
                             run_id=run_id)

    cmd = [PYTHON, "-X", "utf8", CODING_AGENT,
           "--repo-root", ws,
           "--problem-file", problem_file,
           "--label", label,
           "--arm-report-file", arm_report_file] + extra_args
    row["cmd"] = cmd
    row["cmd_str"] = " ".join(cmd)

    # I-6：跑前环境洁净（摘掉父进程的臂变量，避免泄漏到 agent）
    env = E.minimal_env()
    for k in A.ENV_CLEANLINESS_KEYS:
        env.pop(k, None)
    env["PYTHONIOENCODING"] = "utf-8"

    log(f"▶ {arm} {iid}  run_id={run_id}")
    try:
        p = subprocess.Popen(cmd, cwd=PROJECT_ROOT, stdout=subprocess.PIPE,
                                                           stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", env=env)
        row["spawned"] = True
        timed_out = False
        try:
            stdout, _ = p.communicate(timeout=A.AGENT_PHASE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            timed_out = True
            p.kill()
            stdout, _ = p.communicate()
        wall = round(time.time() - started, 2)
        console_log = E.art_dir(run_id, iid)
        os.makedirs(console_log, exist_ok=True)
        console_path = os.path.join(console_log, f"console_{arm}.log")
        E.write_text(console_path, stdout or "")
        row["artifacts"].append(console_path)
        if arm == "C":
            isolated = _write_policy_evidence_for_run(row, E.art_dir(run_id, iid))
            if isolated:
                row["artifacts"].append(policy_isolated_artifact(row))
        row["wall_s"] = wall
        row["timed_out"] = timed_out
        row["exit_code"] = p.returncode
        row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if timed_out:
            row["verdict_class"] = J.TIMEOUT
            row["resolved"] = False
            row["notes"].append(f"agent 阶段超时（>{A.AGENT_PHASE_TIMEOUT_S}s）=> timeout")
        elif p.returncode != 0:
            # coding_agent 退出码非 0 不一定代表 agent 失败（可能是 infra/配置问题）。
            # 启动/配置异常不是可判定候选补丁；保持 infra 分类，不运行 pytest。
            row["verdict_class"] = J.INFRA_ERROR
            row["resolved"] = False
            row["notes"].append(f"coding_agent exit={p.returncode}（详见 console_{arm}.log）")
        else:
            judge_arm_workspace(row, RI.load_instance(iid))
    except E.InfraError as exc:
        row["verdict_class"] = J.INFRA_ERROR
        row["resolved"] = False
        row["notes"].append(f"独立判定基础设施故障：{exc}")
        row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    except Exception as exc:  # noqa: BLE001
        row["verdict_class"] = J.HARNESS_BUG
        row["resolved"] = False
        row["notes"].append(f"scheduler worker 异常：{type(exc).__name__}: {exc}")
        row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    row["stage"] = "judged" if row["verdict_class"] not in (J.TIMEOUT, J.INFRA_ERROR, J.HARNESS_BUG) else "failed_infra"
    return row


# ---------------------------------------------------------------- 主流程

def run_p1_batch(*, run_tag: str, stamp: str, instance_ids: list[str], arms_list: list[str],
                 max_workers: int, resume: bool) -> dict:
    batch_run_id = f"p1-{stamp}-{run_tag}"
    run_root = E.run_root(batch_run_id)
    os.makedirs(run_root, exist_ok=True)

    plan = A.interleaved_plan(instance_ids, arms_list)
    plan_sig = A.plan_order_signature(plan)
    prior = read_triple_state(run_root) if resume else {}

    # 补跑必须走新批次（意味着新的预注册与预算）；--resume 不得重跑已有终态行。
    skipped, pending, skipped_failed = plan_resume(plan, prior)

    log("=" * 78)
    log(f"P1 批次 {batch_run_id}  |  实例 {len(instance_ids)}  臂 {arms_list}  "
        f"并发 {max_workers}  续跑={resume}")
    log(f"计划 {len(plan)} 槽  已完成跳过 {len(skipped)}  待跑 {len(pending)}")
    log(f"计划顺序签名：{plan_sig[:16]}…")
    log(f"状态文件：{triple_state_jsonl(run_root)}")
    log("=" * 78)
    for s in skipped:
        log(f"  [skip] {s['instance_id']}/{s['arm']}  已判：{s['prev_verdict']} "
            f"resolved={s['prev_resolved']}")

    started = time.time()
    previous_manifest = os.path.join(run_root, "manifest.json")
    previous_wall = 0.0
    if resume and os.path.isfile(previous_manifest):
        with open(previous_manifest, encoding="utf-8") as stream:
            previous_wall = float(json.load(stream).get("wall_seconds", 0))
    budget_started = started - previous_wall
    rows = []
    runs_done = len(skipped)
    if pending:
        # 准备每个槽的任务参数（包括工作区/题面/arm_report 路径）
        tasks = []
        for slot in pending:
            iid = slot["instance_id"]
            arm = slot["arm"]
            run_id = A.make_run_id(iid, arm, stamp=stamp)
            label = A.make_label(iid, arm)
            inst = _load_instance(iid)
            art = E.art_dir(run_id, iid)
            os.makedirs(art, exist_ok=True)
            # ⚠️ **关键修复**：必须用 fresh_workspace() 从 eval-cache 做目录副本 + git init，
            # 而不是只 mkdir。coding_agent.py 入口会检查 `--repo-root` 下有 .git，
            # 没有就 exit 2 ⇒ 整批 infra_error、arm_report.jsonl 不会被写入。
            # 这与 P0 run_instance.run_one() 的做法一致（§8.1）。
            try:
                # 复用 run_instance 的同源准备函数，保证 P1 与 P0 的环境语义一致：
                # fresh_workspace -> ensure_venv -> test_patch。coding_agent 随后会在
                # 自己的入口 reset_fixture 回到基线；C 臂分组阶段再按需施加 test_patch。
                ws = E.fresh_workspace(inst, run_id,
                                       log_path=os.path.join(art, "workspace.log"))
                inst["_ws"] = ws
                deps = RI.deps_from_env_spec(inst)
                pkg = RI.infer_import_name(ws)
                if not pkg:
                    raise E.InfraError("无法从工作区布局推断被测包的 import 名")
                py = E.ensure_venv(inst, os.path.join(art, "venv.log"), deps, pkg,
                                   run_id=run_id)
                patch_path = os.path.join(art, "_test_patch.diff")
                E.write_text(patch_path, inst.get("test_patch") or "")
                applied = RI.apply(ws, inst.get("test_patch") or "", art)
                if applied.rc != 0:
                    raise E.InfraError(
                        f"test_patch 无法应用：{applied.stderr[-300:]}")
            except E.InfraError as exc:
                # 工作区建不起来 ⇒ 该槽直接标 infra_error，不提交给子进程
                row = A.empty_row(instance_id=iid, arm=arm, run_id=run_id, label=label)
                row["verdict_class"] = J.INFRA_ERROR
                row["resolved"] = False
                row["notes"].append(f"fresh_workspace 失败：{exc}")
                row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                row["stage"] = "failed_infra"
                append_triple_state(run_root, row)
                rows.append(row)
                runs_done += 1
                log(f"  [infra_error ] {iid}/{arm}  fresh_workspace 失败：{exc}")
                continue
            prob = A.problem_path(E.run_root(run_id), iid)
            A.write_problem_file(prob, inst.get("problem_statement") or "")
            arm_report = os.path.join(art, "arm_report.jsonl")
            tasks.append({
                "instance_id": iid, "arm": arm, "run_id": run_id, "label": label,
                "workspace": ws, "problem_file": prob,
                "extra_args": list(A.ARM_EXTRA_ARGS[arm]),
                "arm_report_file": arm_report,
                "repo": inst["repo"], "base_commit": inst["base_commit"],
            })

        # 受控滑动窗口：只保留 max_workers 个 future，下一批提交前先检查止损。
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            next_index = 0
            consecutive = {}
            stop_reason = None
            while next_index < len(tasks) or futures:
                while next_index < len(tasks) and len(futures) < max_workers:
                    snap = _budget_snapshot(run_root, resume=resume, in_flight=len(futures))
                    stop_reason = _stop_reason(snap, budget_started, consecutive)
                    if stop_reason:
                        break
                    task = tasks[next_index]
                    futures[pool.submit(_run_one_arm, task)] = task
                    next_index += 1
                if stop_reason:
                    log(f"[中止] 停止提交新臂：{stop_reason}")
                    next_index = len(tasks)  # 不再提交；继续收割已启动的 future
                if not futures:
                    break
                done_futures, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                for fut in done_futures:
                    task = futures.pop(fut)
                    try:
                        row = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        row = A.empty_row(instance_id=task["instance_id"], arm=task["arm"],
                                          run_id=task["run_id"], label=task["label"])
                        row["verdict_class"] = J.HARNESS_BUG
                        row["notes"].append(f"future 异常：{exc}")
                        row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                        row["stage"] = "failed_infra"
                    row["budget_after"] = _budget_snapshot(run_root, resume=resume,
                                                             in_flight=len(futures) + 1)
                    append_triple_state(run_root, row)
                    rows.append(row)
                    runs_done += 1
                    mark = row.get("verdict_class", "?")
                    if mark in (J.INFRA_ERROR, J.TIMEOUT):
                        consecutive[row.get("arm", "?")] = consecutive.get(row.get("arm", "?"), 0) + 1
                    else:
                        consecutive[row.get("arm", "?")] = 0
                    log(f"  [{mark:<13}] {row['instance_id']}/{row['arm']}  "
                        f"wall={row.get('wall_s')}s  resolved={row.get('resolved')}")
                snap = _budget_snapshot(run_root, resume=resume)
                stop_reason = _stop_reason(snap, budget_started, consecutive)
            if stop_reason:
                log(f"[止损] {stop_reason}；未提交任务={len(tasks) - next_index}")

    done = time.time() - started
    manifest = {
        "run_id": batch_run_id, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "run_tag": run_tag, "stamp": stamp,
        "arms": arms_list, "instances": instance_ids,
        "max_workers": max_workers, "resume": resume,
        "plan_order_signature": plan_sig,
        "planned_slots": len(plan), "skipped_completed": len(skipped) - skipped_failed,
        "skipped_failed": skipped_failed,
        "runs_done": runs_done, "wall_seconds": round(done, 2),
        "state_jsonl": triple_state_jsonl(run_root),
        "budget": {"max_started_arms": REV2_MAX_STARTED,
                   "initial_started_arms": REV2_INITIAL_STARTED,
                   "started_arms": REV2_INITIAL_STARTED + count_spawned_arms(run_root),
                   "usage": _usage_totals(),
                   "wall_seconds_limit": REV2_WALL_SECONDS,
                   "infra_stop_consecutive": INFRA_STOP_CONSECUTIVE},
    }
    E.write_text(os.path.join(run_root, "manifest.json"),
                 json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    log("\n" + "=" * 78)
    log(f"P1 批次 {batch_run_id} 完成  wall={done:.1f}s  runs={runs_done}/{len(plan)}")
    log(f"清单：{os.path.join(run_root, 'manifest.json')}")
    log("=" * 78)
    return manifest


def _load_instance(iid: str) -> dict:
    from eval.harness import run_instance as RI
    return RI.load_instance(iid)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="P1 三臂交错调度器（§3.2 / §5.5）")
    ap.add_argument("--run-tag", required=True, help="批次标签（run_id = p1-<UTC>-<tag>）")
    ap.add_argument("--set", default="pilot", choices=("pilot", "reserve"))
    ap.add_argument("--instance", action="append", help="只跑指定 instance_id（可重复）")
    ap.add_argument("--arms", default="A,B,C", help="臂列表（逗号分隔，默认 A,B,C）")
    ap.add_argument("--max-workers", type=int, default=A.MAX_CONCURRENCY,
                    help=f"并发槽位（§1.3 K12 默认 {A.MAX_CONCURRENCY}）")
    ap.add_argument("--resume", action="store_true", help="断点续跑：跳过 triple_state.jsonl 里已判完的槽")
    ap.add_argument("--stamp", default=None, help="自定义 UTC 时间戳（缺省=当前 UTC）")
    args = ap.parse_args(argv)

    try:
        arms_list = A.arms_arg(args.arms)
    except A.ArmSpecError as e:
        print(f"[中止] {e}", file=sys.stderr)
        return 2

    ids = S.load_instances(args.set, only=set(args.instance) if args.instance else None)
    if not ids:
        print("[中止] 没有匹配的实例", file=sys.stderr)
        return 1
    stamp = args.stamp or time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    run_p1_batch(run_tag=args.run_tag, stamp=stamp, instance_ids=ids, arms_list=arms_list,
                 max_workers=args.max_workers, resume=args.resume)
    return 0


if __name__ == "__main__":
    sys.exit(main())
