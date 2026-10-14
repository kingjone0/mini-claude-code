"""desc_ab_runner.py —— 「工具描述」实验的编排器：3 臂 × 8 次 = 24 次，交错执行。

**只做编排与记账，不做分析、不下结论。** 采用率的判定由独立的
`desc_adoption_audit.py` 从 `events` 现算 —— 两条代码路径算同一件事，
一致才说明不是某个脚本的 bug 造出了一个假结论。

与上一轮 `ab_runner.py` 的三处**有意的改动**：

  1. **交错执行**。上一轮是按臂分段跑的（A 六次跑完再跑 B），于是"臂"与"时段"
     混在一起，任何随时间漂移的东西都会被算进臂差。这一轮按 n 轮转：
     A,B,C,A,B,C,… 每个 n 里三臂各跑一次。
  2. **三个臂**，两个文本通道各是一个单变量，A 是共同对照。
  3. **三处独立证据**证明本臂实际生效，且第三处是**新的**：server 自己落盘的
     "文本通道"行，里面带着它实际发出去的 instructions 与描述**字符数** ——
     这比"环境变量设对了"强，因为它证明的是**服务端真的换了那份文本**。

三处证据（三者不一致即为异常，记进清单的"偏差"栏）：
    1) 产物 `trace.run.arm` 里两个 *_raw 字段
    2) 产物 `metrics` 里同名的两个字段（第二份拷贝，故意各记各的）
    3) `out/code-tools-server.log` 最后一次 "文本通道" 行（子进程自己写的）

用法：
    python desc_ab_runner.py
"""

from __future__ import annotations

import json
import os
import re
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
MANIFEST = OUT_DIR / "desc-ab-manifest.txt"
RUNS_JSONL = OUT_DIR / "desc-ab-runs.jsonl"
SERVER_LOG = OUT_DIR / "code-tools-server.log"

# 靶场必须停在这个提交上：注入缺陷之后的 HEAD。跑之前先断言，不对就不开跑。
EXPECTED_TARGET_HEAD = "d21dd31c3f1bde3a9a5de9e044bff7b2045af465"

DEFAULT_ENV_KEY = "AGENT_REPO_MAP_DEFAULT"
INSTR_ENV_KEY = "AGENT_SERVER_INSTRUCTIONS"
DESC_ENV_KEY = "AGENT_REPO_MAP_DESC"

# (臂, 服务端 instructions 档, repo_map 描述档)
ARMS = [("A", "v1", "v1"), ("B", "v2", "v1"), ("C", "v1", "v2")]
N_PER_ARM = 8

# ⚠️ 各档文本的字符数**不是常量**：instructions 里嵌了 `REPO_ROOT`
#    （`servers/code_tools_server.py:183`、`:189` 的 f-string），而它随
#    `AGENT_REPO_ROOT` 变。探针跑的是默认靶场路径，实验跑的是 more-itertools，
#    所以 v2 在探针里量到 268 字符、在真实运行里是 255 字符。
#
#    **这个差不是问题，把它硬编码成常量才是问题**：硬编码会让每一次 B 臂运行
#    都误报一条偏差，而一份混着假偏差的清单会让真正的异常看不见。
#
#    改用跨档比较，与具体路径长度无关：
#      ① 每次解析出的档位必须等于计划档位（最强的一条，与环境无关）；
#      ② 同一档位在不同运行里的字符数必须相同（变了说明文本不是常量）；
#      ③ 实验结束时 v1 与 v2 必须量到**不同**的字符数（相同说明干预没改到文本）。
_OBSERVED_LEN: dict[str, dict[str, int]] = {"instr": {}, "desc": {}}


def note_length(kind: str, variant: str, length: int | None) -> str | None:
    """记下某档文本本次量到的字符数；返回一条异常描述，或 None。"""
    if length is None:
        return f"{kind} 档 {variant} 没解析出字符数"
    seen = _OBSERVED_LEN[kind]
    if variant in seen and seen[variant] != length:
        return (f"{kind} 档 {variant} 的字符数从 {seen[variant]} 变成 {length}"
                " —— 同一档位的文本不是常量")
    seen.setdefault(variant, length)
    return None

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


def last_text_channel_line() -> str:
    """取子进程落盘的 server 日志里最后一条"文本通道"行。"""
    if not SERVER_LOG.exists():
        return ""
    try:
        text = SERVER_LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    hits = [ln for ln in text.splitlines() if "文本通道" in ln]
    return hits[-1] if hits else ""


def parse_text_channel(line: str) -> tuple[str | None, str | None, int | None, int | None]:
    """从 server 日志行里抠出 (instructions 档, 描述档, instructions 字符数, 描述字符数)。

    注意档位在日志里是 `%r` 打出来的，**带单引号**（`'v2'`）—— 正则必须容忍引号。
    这个坑由 `--self-test` 对着真实日志行抓出来过；不要改成只匹配裸值，
    否则自检会失败（失败是好事，静默不匹配才致命）。
    """
    m = re.search(
        rf"{INSTR_ENV_KEY}='?(\w+)'?.*?{DESC_ENV_KEY}='?(\w+)'?"
        r".*?instructions (\d+) 字符 / repo_map 描述 (\d+) 字符",
        line,
    )
    if not m:
        return None, None, None, None
    return m.group(1), m.group(2), int(m.group(3)), int(m.group(4))


def self_test() -> int:
    """起飞前自检：确认能读懂 server **真正写出来的**那行日志。

    刻意不用合成字符串当唯一证据 —— 我写的正则如果能匹配我自己造的字符串，
    只能说明我前后一致，**不能说明它能读懂真实日志**（陷阱 22：核对脚本的输入
    决定它是断言还是快照）。

    这里**不**核对"字符数等于多少"：那个数依赖靶场路径长度，不是常量
    （见 `_OBSERVED_LEN` 上面的说明）。
    """
    ok = True
    print(f"[INFO] 计划三臂: {ARMS}")
    print(f"[INFO] 预期靶场 HEAD: {EXPECTED_TARGET_HEAD}")

    line = last_text_channel_line()
    if not line:
        print("[WARN] 还没有真实日志行可核对（server 尚未在本开关下启动过）")
        print("       合成串不作为通过依据；真实运行时每次都会核对，对不上会记偏差。")
    else:
        parsed = parse_text_channel(line)
        print(f"[INFO] 真实日志行: {line}")
        print(f"[INFO] 解析结果  : {parsed}")
        if parsed[0] is None:
            print("[FAIL] 解析失败 —— 正则与 server 实际格式不一致")
            ok = False
        else:
            print(f"[OK] 真实日志行解析成功：档位 {(parsed[0], parsed[1])}，"
                  f"instructions {parsed[2]} 字符 / repo_map 描述 {parsed[3]} 字符")

    print("[OK] 自检通过" if ok else "[FAIL] 自检未通过")
    return 0 if ok else 1


def main() -> int:
    head_open, status_open = git_state()
    now = datetime.now()
    emit("=" * 84)
    emit("「工具描述」实验运行清单 —— 只记录原始事实，不含分析结论")
    emit("=" * 84)
    emit(f"生成时间            : {now.isoformat(timespec='seconds')}")
    emit(f"仓库根              : {PROJECT_ROOT}")
    emit(f"解释器              : {PYTHON}")
    emit(f"靶场                : {TARGET}")
    emit(f"靶场 HEAD(开跑前)   : {head_open}")
    emit(f"                     {'与预期一致' if head_open == EXPECTED_TARGET_HEAD else '⚠️ 与预期不符！'}")
    emit(f"靶场 status(开跑前) : {status_open!r}  -> {'干净' if status_open == '' else '不干净！'}")
    emit(f"设计                : 3 臂 × {N_PER_ARM} 次 = {N_PER_ARM * 3} 次，**交错执行**（按 n 轮转）")
    for arm, instr, desc in ARMS:
        emit(f"  臂 {arm}: {INSTR_ENV_KEY}={instr}, {DESC_ENV_KEY}={desc}")
    emit(f"三臂共同的            : {DEFAULT_ENV_KEY}=summary（机制单一：只走环境变量，不用命令行开关）")
    emit("")
    emit(f"命令原文模板：$env:{INSTR_ENV_KEY}='<档>'; $env:{DESC_ENV_KEY}='<档>'")
    emit(f"              & {PYTHON} coding_agent.py --fixture more-itertools --label desc-ab-<臂>-<n>")
    emit("")

    if head_open != EXPECTED_TARGET_HEAD:
        emit(f"[中止] 靶场 HEAD 不是预期的 {EXPECTED_TARGET_HEAD}；不自行修改，如实中止。")
        MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 2
    if status_open != "":
        emit("[中止] 靶场工作区不干净；不自行修改，如实中止。")
        MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 2

    emit("-" * 84)
    emit(f"{'#':>3} {'标签':<14} {'计划':<7} {'起(iso)':<20} {'耗时s':>7} {'基线':>5} {'终':>4} "
         f"{'success':>8} {'rmc':>4} {'turns':>6} {'in_tok':>7}")
    emit("-" * 84)

    records: list[dict] = []
    deviations: list[str] = []
    idx = 0

    for n in range(1, N_PER_ARM + 1):
        for arm, instr, desc in ARMS:
            idx += 1
            label = f"desc-ab-{arm}-{n}"
            artifact_path = OUT_DIR / f"agent-{label}.json"
            console = OUT_DIR / f"{label}.console.log"

            head_pre, status_pre = git_state()
            if status_pre != "":
                deviations.append(f"[{label}] 运行前靶场不干净：{status_pre!r}")
                emit(f"{idx:>3} {label:<14} 中止：运行前靶场不干净 {status_pre!r}")
                break

            child_env = dict(os.environ)
            child_env[DEFAULT_ENV_KEY] = "summary"
            child_env[INSTR_ENV_KEY] = instr
            child_env[DESC_ENV_KEY] = desc

            started = datetime.now()
            t0 = time.time()
            with console.open("w", encoding="utf-8", errors="replace") as fh:
                proc = subprocess.run(
                    [PYTHON, "coding_agent.py", "--fixture", "more-itertools", "--label", label],
                    cwd=PROJECT_ROOT, env=child_env,
                    stdout=fh, stderr=subprocess.STDOUT,
                )
            elapsed = time.time() - t0
            finished = datetime.now()

            head_post, status_post = git_state()
            if status_post != "":
                deviations.append(f"[{label}] 运行后靶场不干净：{status_post!r}")
            if head_post != head_pre:
                deviations.append(f"[{label}] 靶场 HEAD 变了：{head_pre} -> {head_post}")

            rec = {
                "idx": idx, "label": label, "arm": arm,
                "planned": {"instructions": instr, "repo_map_desc": desc,
                            "repo_map_default": "summary"},
                "started": started.isoformat(timespec="seconds"),
                "finished": finished.isoformat(timespec="seconds"),
                "elapsed_s": round(elapsed, 1),
                "runner_exit_code": proc.returncode,
                "target_head_pre": head_pre, "target_head_post": head_post,
                "target_status_pre": status_pre, "target_status_post": status_post,
                "artifact": artifact_path.name,
                "console_log": console.name,
            }

            if artifact_path.exists():
                data = json.loads(artifact_path.read_text(encoding="utf-8"))
                metrics = data.get("metrics", {}) or {}
                trace_arm = ((data.get("trace") or {}).get("run") or {}).get("arm") or {}
                # `metrics` 里**没有** num_turns 这个键（实测键名列过），回合数只能
                # 从 trace 的 turn 事件数出来。记 None 会让清单里整列看起来像没数据。
                steps = ((data.get("trace") or {}).get("steps")) or []
                turns = sum(1 for s in steps if s.get("type") == "turn")
                rec.update({
                    "success": metrics.get("success"),
                    "baseline_exit_code": metrics.get("baseline_exit_code"),
                    "final_exit_code": metrics.get("final_exit_code"),
                    "repo_map_count_metric": metrics.get("repo_map_count"),
                    "used_repo_map_metric": metrics.get("used_repo_map"),
                    "tool_calls_total": metrics.get("tool_calls_total"),
                    "input_tokens": metrics.get("input_tokens"),
                    "output_tokens": metrics.get("output_tokens"),
                    "num_turns": turns or None,
                    "changed_files": metrics.get("changed_files"),
                    "artifact_arm": {
                        "trace_server_instructions_raw": trace_arm.get("server_instructions_raw"),
                        "trace_repo_map_desc_raw": trace_arm.get("repo_map_desc_raw"),
                        "metrics_server_instructions_raw": metrics.get("server_instructions_raw"),
                        "metrics_repo_map_desc_raw": metrics.get("repo_map_desc_raw"),
                    },
                })
            else:
                deviations.append(f"[{label}] 产物不存在：{artifact_path.name}")

            # 第三处证据：server 自己落盘的那一行
            tc_line = last_text_channel_line()
            s_instr, s_desc, s_instr_len, s_desc_len = parse_text_channel(tc_line)
            rec["server_log_text_channel"] = tc_line
            rec["server_log_parsed"] = {"instructions": s_instr, "desc": s_desc,
                                        "instructions_chars": s_instr_len,
                                        "desc_chars": s_desc_len}

            if (s_instr, s_desc) != (instr, desc):
                deviations.append(
                    f"[{label}] server 日志里的生效档位 ({(s_instr, s_desc)}) != 计划 ({(instr, desc)})")
            else:
                # 只有档位对上了才把长度记进对照表 —— 档位都没对上时记长度，
                # 等于把别人的数记到自己的档位上。
                for kind, variant, length in (("instr", s_instr, s_instr_len),
                                              ("desc", s_desc, s_desc_len)):
                    problem = note_length(kind, variant, length)
                    if problem:
                        deviations.append(f"[{label}] {problem}")

            art = rec.get("artifact_arm") or {}
            for key, want in (("trace_server_instructions_raw", instr),
                              ("trace_repo_map_desc_raw", desc),
                              ("metrics_server_instructions_raw", instr),
                              ("metrics_repo_map_desc_raw", desc)):
                if art.get(key) != want:
                    deviations.append(f"[{label}] 产物自证 {key}={art.get(key)!r} != 计划 {want!r}")

            if rec.get("baseline_exit_code") != 1:
                deviations.append(
                    f"[{label}] 基线 pytest 退出码 {rec.get('baseline_exit_code')!r} != 1"
                    "（陷阱 18：靶场的『坏』必须是不变量）")

            records.append(rec)
            emit(f"{idx:>3} {label:<14} I{instr}/D{desc:<4} {started.isoformat(timespec='seconds'):<20} "
                 f"{elapsed:>7.1f} {str(rec.get('baseline_exit_code')):>5} "
                 f"{str(rec.get('final_exit_code')):>4} {str(rec.get('success')):>8} "
                 f"{str(rec.get('repo_map_count_metric')):>4} {str(rec.get('num_turns')):>6} "
                 f"{str(rec.get('input_tokens')):>7}")

    head_close, status_close = git_state()
    emit("")
    emit("-" * 84)
    emit("收尾核对")
    emit("-" * 84)
    emit(f"靶场 HEAD(全部跑完后)  : {head_close}  -> {'与开跑前一致' if head_close == head_open else '变了！'}")
    emit(f"靶场 status(全部跑完后): {status_close!r}  -> {'干净' if status_close == '' else '不干净！'}")
    emit(f"已完成运行次数          : {len(records)} / {N_PER_ARM * 3}")
    emit("")
    emit("-" * 84)
    emit("各档文本实测字符数（**不是常量**：instructions 里嵌了靶场路径）")
    emit("-" * 84)
    for kind, name in (("instr", "instructions"), ("desc", "repo_map 描述")):
        seen = _OBSERVED_LEN[kind]
        if not seen:
            emit(f"  {name:<16} 一次都没量到")
            deviations.append(f"{name} 一次都没量到字符数")
            continue
        for v in sorted(seen):
            emit(f"  {name:<16} {v}: {seen[v]} 字符")
        if len(seen) < 2:
            deviations.append(f"{name} 只观察到 {len(seen)} 档，无法确认两档文本不同")
        elif len(set(seen.values())) == 1:
            deviations.append(f"两档 {name} 量到**相同**的字符数（{sorted(set(seen.values()))}）"
                              " —— 干预没有改变文本")
    emit("")
    emit("-" * 84)
    emit("偏差记录（与计划不符之处，如实列，不掩盖）")
    emit("-" * 84)
    if deviations:
        for d in deviations:
            emit(f"  * {d}")
    else:
        emit("  (无)")
    emit("")
    emit("本清单只含原始事实；『是否被采用』的判定与结论由 desc_adoption_audit.py 与")
    emit("AB-TOOL-DESC.md §7 负责。")

    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with RUNS_JSONL.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n[OK] 清单已写: {MANIFEST}")
    print(f"[OK] 逐次记录: {RUNS_JSONL}")
    return 0 if not deviations and len(records) == N_PER_ARM * 3 else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(main())
