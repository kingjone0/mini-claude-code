"""A/B 实验编排器（t2）：两臂各 6 次，逐次落盘原始产物 + 可核对清单。

只做编排与记账，不做分析、不下结论。

设计要点（都是为了让"清单可核对"而不是"看起来对"）：
  * 每次运行前**先查靶场 git status**，不干净就停（不自己修，如实记录后中止）。
  * 臂取值**只走环境变量**，12 次统一，不混用 --repo-map-default。
  * 每次运行前后各记一次靶场 HEAD + git status。
  * 每次运行后读三处独立证据证明"本臂实际生效"：
      1) 产物 metrics.repo_map_default / metrics.repo_map_default_source
      2) .log 头部
      3) 子进程落盘的 code-tools-server.log 里的 "repo_map 默认详细度" 行
    三者不一致即为异常，记进清单的"偏差"栏。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import agent.config as agent_config  # noqa: E402

agent_config.force_utf8_output()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = r"D:\Anaconda3\envs\aicoding\python.exe"
TARGET = Path(r"E:\aicoding\targets\more-itertools")
OUT_DIR = PROJECT_ROOT / "servers" / "experiments" / "out"
MANIFEST = OUT_DIR / "ab-manifest.txt"
RUNS_JSONL = OUT_DIR / "ab-runs.jsonl"
ENV_KEY = "AGENT_REPO_MAP_DEFAULT"

ARMS = [("A", "symbols"), ("B", "summary")]
N_PER_ARM = 6

lines: list[str] = []


def emit(text: str = "") -> None:
    lines.append(text)
    print(text, flush=True)


def run_git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=TARGET, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return (proc.stdout or "").strip()


def git_state() -> tuple[str, str]:
    return run_git("rev-parse", "HEAD"), run_git("status", "--porcelain")


def server_arm_line(pid_after: int) -> list[str]:
    """读子进程落盘的 code-tools-server.log，取最后几条详细度行。"""
    log = OUT_DIR / "code-tools-server.log"
    if not log.exists():
        return []
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits = [ln for ln in text.splitlines() if "repo_map 默认详细度" in ln]
    return hits[-2:]


def main() -> int:
    head_open, status_open = git_state()
    now = datetime.now()
    emit("=" * 78)
    emit("A/B 实验运行清单（t2）—— 只记录原始事实，不含分析结论")
    emit("=" * 78)
    emit(f"生成时间      : {now.isoformat(timespec='seconds')}")
    emit(f"仓库根        : {PROJECT_ROOT}")
    emit(f"解释器        : {PYTHON}")
    emit(f"靶场          : {TARGET}")
    emit(f"靶场 HEAD(开跑前) : {head_open}")
    emit(f"靶场 git status(开跑前): {status_open!r}  -> {'干净' if status_open == '' else '不干净！'}")
    emit(f"唯一变量      : {ENV_KEY}（臂 A=symbols / 臂 B=summary）")
    emit("机制          : 12 次统一只用环境变量，不使用 --repo-map-default")
    emit(f"每臂次数      : {N_PER_ARM}，共 {N_PER_ARM * 2} 次")
    emit("")
    emit("命令原文模板（每次按此执行，逐次实参见下表）:")
    emit(f"  $env:{ENV_KEY}='<本臂取值>'")
    emit(f"  & {PYTHON} coding_agent.py --fixture more-itertools --label ab-<臂>-<n>")
    emit(f"  $env:{ENV_KEY}=''")
    emit("注意：--repo-map-default 一次都没用；每次运行前显式赋本臂取值，不依赖继承或默认值。")
    emit("")
    emit("-" * 78)
    emit(f"{'#':>2}  {'标签':<8} {'计划臂':<7} {'起(iso)':<20} {'止(iso)':<20} {'耗时s':>6} "
         f"{'产物arm':<9} {'arm来源':<26} {'success':<7} {'rmc':>4} {'退出':>4}")
    emit("-" * 78)

    records: list[dict] = []
    deviations: list[str] = []
    idx = 0

    for arm, value in ARMS:
        for n in range(1, N_PER_ARM + 1):
            idx += 1
            label = f"ab-{arm}-{n}"
            artifact = OUT_DIR / f"agent-{label}.json"
            logfile = OUT_DIR / f"agent-{label}.log"

            # —— 运行前：靶场必须干净 ——
            head_pre, status_pre = git_state()
            if status_pre != "":
                msg = (f"[中止] 第 {idx} 次运行前靶场不干净：{status_pre!r}；"
                       f"不自行修复，停止后续运行。")
                emit(msg)
                deviations.append(msg)
                break
            if head_pre != head_open:
                msg = f"[中止] 第 {idx} 次运行前靶场 HEAD 变成 {head_pre}（开跑前 {head_open}）。"
                emit(msg)
                deviations.append(msg)
                break

            cmd_text = (f"$env:{ENV_KEY}='{value}'; "
                        f"& {PYTHON} coding_agent.py --fixture more-itertools --label {label}; "
                        f"$env:{ENV_KEY}=''")

            # 显式赋本臂取值（两条通道都通，所以必须每次都显式设）
            os.environ[ENV_KEY] = value
            t0 = datetime.now()
            start = time.time()
            proc = subprocess.run(
                [PYTHON, "coding_agent.py", "--fixture", "more-itertools", "--label", label],
                cwd=PROJECT_ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            elapsed = time.time() - start
            t1 = datetime.now()
            # 只清空，不删键
            os.environ[ENV_KEY] = ""

            head_post, status_post = git_state()

            arm_seen = src_seen = None
            success = rmc = None
            metrics_ok = False
            if artifact.exists():
                try:
                    j = json.loads(artifact.read_text(encoding="utf-8", errors="replace"))
                    m = j.get("metrics") or {}
                    metrics_ok = True
                    arm_seen = m.get("repo_map_default")
                    src_seen = m.get("repo_map_default_source")
                    success = m.get("success")
                    rmc = m.get("repo_map_count")
                except Exception as exc:  # noqa: BLE001
                    deviations.append(f"{label}: 产物 JSON 解析失败 {exc!r}")

            if not metrics_ok:
                deviations.append(f"{label}: 产物缺失或 metrics 解析不出（exit={proc.returncode}）")

            # 三处独立证据交叉核对
            srv_hits = server_arm_line(idx)
            srv_line = srv_hits[-1] if srv_hits else "(未取到)"
            if arm_seen != value:
                deviations.append(
                    f"{label}: 计划 {value!r}，但产物 metrics.repo_map_default={arm_seen!r}")
            expected_src = f"env:{ENV_KEY}"
            if src_seen != expected_src:
                deviations.append(
                    f"{label}: 臂来源异常 metrics.repo_map_default_source={src_seen!r}"
                    f"（期望 {expected_src!r}，若为 '--repo-map-default' 说明混用了机制）")
            if f"生效 '{value}'" not in srv_line:
                deviations.append(
                    f"{label}: 子进程日志未显示生效 {value!r}，实际末行={srv_line!r}")

            emit(f"{idx:>2}  {label:<8} {arm:<7} {t0.isoformat(timespec='seconds'):<20} "
                 f"{t1.isoformat(timespec='seconds'):<20} {elapsed:>6.1f} "
                 f"{str(arm_seen):<9} {str(src_seen):<26} {str(success):<7} "
                 f"{str(rmc):>4} {proc.returncode:>4}")

            records.append({
                "idx": idx, "label": label, "planned_arm": arm, "planned_value": value,
                "command": cmd_text,
                "start": t0.isoformat(timespec="seconds"), "end": t1.isoformat(timespec="seconds"),
                "elapsed_s": round(elapsed, 1),
                "head_before": head_pre, "status_before": status_pre,
                "head_after": head_post, "status_after": status_post,
                "artifact": artifact.name, "artifact_exists": artifact.exists(),
                "metrics_parsed": metrics_ok,
                "effective_value": arm_seen, "effective_source": src_seen,
                "server_child_line": srv_line,
                "success": success, "repo_map_count": rmc,
                "process_exit_code": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-400:],
                "stderr_tail": (proc.stderr or "")[-400:],
            })

        if any("中止" in d for d in deviations):
            break

    head_close, status_close = git_state()

    emit("")
    emit("-" * 78)
    emit("逐次核对（靶场状态 / 生效取值证据）")
    emit("-" * 78)
    for r in records:
        emit(f"[{r['idx']:>2}] {r['label']}")
        emit(f"     实际命令: {r['command']}")
        emit(f"     起止: {r['start']} → {r['end']}  ({r['elapsed_s']}s)  进程退出码={r['process_exit_code']}")
        emit(f"     靶场 HEAD 前/后: {r['head_before']} / {r['head_after']}")
        emit(f"     靶场 status 前: {r['status_before']!r}  后: {r['status_after']!r}")
        emit(f"     产物: {r['artifact']}  存在={r['artifact_exists']}  metrics可解析={r['metrics_parsed']}")
        emit(f"     实际生效取值: {r['effective_value']!r}  (来源字段: {r['effective_source']!r})")
        emit(f"     子进程证据: {r['server_child_line']}")
        emit(f"     独立判定: metrics.success={r['success']}  repo_map_count={r['repo_map_count']}")
        emit("")

    emit("-" * 78)
    emit("收尾状态")
    emit("-" * 78)
    emit(f"靶场 HEAD(全部跑完) : {head_close}")
    emit(f"HEAD 是否与开跑前一致: {'是' if head_close == head_open else '否 —— 偏差！'}")
    emit(f"靶场 git status(全部跑完后): {status_close!r}  -> {'干净' if status_close=='' else '不干净！'}")
    emit(f"已完成运行次数      : {len(records)} / {N_PER_ARM * 2}")
    emit("")
    emit("-" * 78)
    emit("偏差记录（与计划不符之处，如实列，不掩盖）")
    emit("-" * 78)
    if deviations:
        for d in deviations:
            emit(f"  * {d}")
    else:
        emit("  (无)")
    emit("")
    emit("说明：本次实验未使用 --repo-map-default；12 次全部走环境变量，机制单一。")
    emit("本清单只含原始事实；『是否被采用』的判定与结论由 analyst 负责。")

    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with RUNS_JSONL.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n[OK] 清单已写: {MANIFEST}")
    print(f"[OK] 逐次记录: {RUNS_JSONL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())