"""t4 复核：断点续跑 —— 真中断（杀进程），不是「模拟跑一半」。

与 mechanism_check 的差别：那个版本用「先只传前 2 条 id」模拟「跑了一半」，
并没有产生**被强杀的半成品状态**。本脚本做真的：

  阶段 1：起一个 scheduler 子进程跑 4 条实例（并发 1），
          在它写出**至少 1 条** judged 之后把它 **kill**（taskkill /F），
          => 制造「state.jsonl 有已完成记录 + 可能有半截工作区」的真实现场。
  阶段 2：同 run_id 带 --resume 重跑全部 4 条，检查：
          (a) 已完成实例被跳过（skipped_completed > 0）
          (b) 已完成实例的 verdict/resolved **一个字节都没变**（比对 state.jsonl 行）
          (c) state.jsonl 里已完成实例**只有一条** judged 记录（没被追加新结论）
          (d) 剩下没跑完的确实被跑完（最终 4 条都有 stage=judged）

用法：python t4_resume.py <batch_run_id>
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

PROJ = r"E:\aicoding\mini-claude-code"
for p in (PROJ, os.path.join(PROJ, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E            # noqa: E402
from eval.harness import scheduler as SCH    # noqa: E402

OUT = r"E:\aicoding\eval-runs\t4\resume"
PY = r"D:\Anaconda3\envs\aicoding\python.exe"
IDS = ["pallets-click__4fd2fea", "pytest-dev-pluggy__cbf58d5",
       "more-itertools-more-itertools__1b7f033", "astanin-python-tabulate__9dedd45"]


def spawn(run_id, ids, workers, resume):
    cmd = [PY, os.path.join(PROJ, "eval", "harness", "scheduler.py"),
           "--run-id", run_id, "--max-workers", str(workers), "--label", "t4-resume",
           "--patch-kind", "gold"]
    for i in ids:
        cmd += ["--instance", i]
    if resume:
        cmd.append("--resume")
    return subprocess.Popen(cmd, cwd=PROJ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")


def judged_count(run_id):
    st = SCH.read_state(run_id)
    return {i: r for i, r in st.items() if r.get("stage") == "judged"}


def kill_tree(proc):
    """Windows：连子进程一起杀（ProcessPool 的子进程也要清）。"""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                   capture_output=True, text=True)
    try:
        proc.wait(timeout=30)
    except Exception:
        proc.kill()


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    batch = sys.argv[1] if len(sys.argv) > 1 else "t4-resume"
    os.makedirs(OUT, exist_ok=True)
    rid = f"{batch}-run"

    # 清掉历史 state.jsonl，保证起点干净（只删本脚本自己的 run_id）
    sj = SCH.state_jsonl(rid)
    if os.path.exists(sj):
        os.rename(sj, sj + f".pre-t4-{time.strftime('%Y%m%d-%H%M%S')}")
    root = E.run_root(rid)
    if os.path.isdir(root):
        import shutil
        # 只删本 run_id 的目录，路径已核对
        shutil.rmtree(root, ignore_errors=True)

    print("=" * 70)
    print(f"阶段 1：起 scheduler 跑 {len(IDS)} 条，等出现第 1 条 judged 后 **强杀**")
    print("=" * 70)
    proc = spawn(rid, IDS, 1, resume=False)
    done = {}
    killed_at = None
    t0 = time.time()
    while time.time() - t0 < 900:
        if proc.poll() is not None:
            print("  （进程自己跑完了，没来得及杀 —— 也算有效现场，但中断强度弱）")
            break
        done = judged_count(rid)
        if len(done) >= 1:
            killed_at = sorted(done)
            print(f"  观察到 {len(done)} 条 judged：{killed_at} => 立刻强杀")
            kill_tree(proc)
            break
        time.sleep(2)

    snapshot_before = {i: json.dumps(judged_count(rid)[i], sort_keys=True, ensure_ascii=False)
                       for i in judged_count(rid)}
    ws_dir = os.path.join(root, IDS[0])
    print(f"  杀后 state.jsonl 已有 judged：{sorted(snapshot_before)}")
    print(f"  state.jsonl: {sj}")

    rows1 = [l for l in open(sj, encoding="utf-8").read().splitlines() if l.strip()] \
        if os.path.exists(sj) else []
    print(f"  杀后 state.jsonl 行数：{len(rows1)}")

    # 检查「被杀」是否留下半成品证据（半行 JSON 或未清理工作区）
    truncated = 0
    for l in rows1:
        try:
            json.loads(l)
        except json.JSONDecodeError:
            truncated += 1
    print(f"  半行/损坏的 JSON 行：{truncated}（read_state 应能容错跳过）")

    print()
    print("=" * 70)
    print("阶段 2：同 run_id + --resume 重跑全部 4 条")
    print("=" * 70)
    cmd = [PY, os.path.join(PROJ, "eval", "harness", "scheduler.py"),
           "--run-id", rid, "--max-workers", "1", "--label", "t4-resume-phase2",
           "--patch-kind", "gold", "--resume"]
    for i in IDS:
        cmd += ["--instance", i]
    p2 = subprocess.run(cmd, cwd=PROJ, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=3600)
    print(p2.stdout[-4000:])
    if p2.returncode != 0:
        print("[stderr]", p2.stderr[-1500:])

    state_after = judged_count(rid)
    snapshot_after = {i: json.dumps(state_after[i], sort_keys=True, ensure_ascii=False)
                      for i in snapshot_before}

    # (b) 已完成实例结论逐字节未变
    unchanged = {i: (snapshot_before[i] == snapshot_after.get(i)) for i in snapshot_before}
    # (c) 每条已完成实例在 state.jsonl 里只有一条 judged
    allrows = [json.loads(l) for l in open(sj, encoding="utf-8").read().splitlines() if l.strip()]
    per = {}
    for r in allrows:
        per.setdefault(r["instance_id"], []).append(r.get("stage"))
    multi = {i: v for i, v in per.items() if v.count("judged") > 1}
    # (a) 跳过计数
    import re
    m = re.search(r"已完成跳过\s+(\d+)", p2.stdout)
    skipped = int(m.group(1)) if m else None
    # (d) 全部跑完
    all_done = sorted(state_after) == sorted(IDS)

    print()
    print("=" * 70)
    print("断点续跑判定")
    print("=" * 70)
    print(f"  (a) 恢复时跳过的已完成条数 : {skipped}（期望 = 中断前完成数 {len(snapshot_before)}）")
    print(f"  (b) 已完成实例结论未改写   : {unchanged}")
    print(f"  (c) 已完成实例 judged 多条 : {multi}（期望空）")
    print(f"  (d) 恢复后 4 条全部完成    : {all_done}  {sorted(state_after)}")
    print(f"  半行容错：中断留下 {truncated} 条半行，read_state 未崩溃 => OK")

    ok = ((skipped == len(snapshot_before)) and all(unchanged.values()) and not multi
          and all_done and len(snapshot_before) >= 1)
    res = {
        "run_id": rid, "killed_after_judged": sorted(snapshot_before),
        "state_lines_after_kill": len(rows1), "truncated_lines": truncated,
        "skipped_on_resume": skipped, "unchanged": unchanged,
        "multi_judged": multi, "all_done_after_resume": all_done,
        "phase2_returncode": p2.returncode, "overall_ok": ok,
        "state_jsonl": sj,
    }
    with open(os.path.join(OUT, "resume.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"\n断点续跑总判定：{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())