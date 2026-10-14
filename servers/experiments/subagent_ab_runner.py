#!/usr/bin/env python
"""④ 层（subagent 分工）A/B/C/D 跑批器。

四臂（见 AB-SUBAGENT.md §1；D 臂定义在 AB-SUBAGENT-V2.md §1.1）：
  A = 现状（不加 --subagent）
  B = ④ 层（--subagent）
  C = 通道阳性对照（--subagent --subagent-force）
  D = 驱动派发（--delegate=policy，P1–P5：K 组 scout 由 driver 派、
      ≤2000 字符有界索引、截断自动续派、worktree 快照隔离、独立进程校验）

**交错**跑 A1 B1 C1 A2 B2 C2 ... —— 不交错的话，"后跑的那一臂"会把
时间漂移一起吃进去（上一轮「工具描述」实验已经吃过这个亏）。

产物：
  out/agent-subagent-ab-<臂>-<n>.json   运行时状态（被 .gitignore 忽略）
  out/subagent-ab-<臂>-<n>.console.log  控制台原始输出（进仓库：证据留原始字节）
  out/subagent-ab-runs.jsonl            每次一行的指标汇总（进仓库）
  out/subagent-ab-manifest.txt          跑批清单 + 每臂中位数/极差（进仓库）

用法：
  python servers/experiments/subagent_ab_runner.py            # 跑缺的那些
  python servers/experiments/subagent_ab_runner.py --report   # 只汇总，不跑

★ R9（第二轮复审 must-fix）：`--report` 不带 `--arms` 时，臂集与靶场**从 out/ 已有
  产物反推**（`agent-<TAG>-<arm>-<n>.json`），并显式打印推断结果与来源；推不出来就
  报错退出。另有一道闸：本次汇总的 label 集合必须是已有 `runs.jsonl` 的**超集**，
  否则拒绝写入并以非零退出码结束 —— 绝不静默丢臂、绝不写出更短的 runs.jsonl。
  （旧行为：不带 --arms 时用默认 A,B,C，把 v2 的 D 臂丢掉、8 行覆盖成 4 行。）
  跑批（不带 --report）的默认臂集仍是 A,B,C，旧命令逐字同形。
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # servers/experiments
REPO = HERE.parent.parent                       # mini-claude-code
OUT = HERE / "out"
FIXTURE = "more-itertools-multi"   # 可用 --fixture 覆盖
TAG = "subagent-ab"                # 可用 --tag 覆盖；决定 label 与 jsonl/manifest 的文件名前缀
N_PER_ARM = 4
# ARMS 是**默认调度表**（v1 三臂）。v2 的 AB 是 A vs D（各 n=4 交错），
# 用 --arms A,D 显式指定；不动这张表的默认值是为了让旧跑批命令逐字不变。
ARMS = ("A", "B", "C")
ARMS_ALL = ("A", "B", "C", "D")
# 每臂在命令行上多出来的东西。A 是空 —— A 必须与加 ④ 层之前**逐字节相同**。
ARM_FLAGS = {"A": [], "B": ["--subagent"], "C": ["--subagent", "--subagent-force"],
             "D": ["--delegate=policy"]}

# 从产物 metrics 里挑出来进 jsonl 的字段。
METRIC_KEYS = (
    "success", "baseline_exit_code", "final_exit_code",
    "input_tokens", "output_tokens", "cost_usd", "elapsed_s",
    "tool_calls_total", "read_count", "search_count", "apply_patch_count",
    "run_tests_count", "run_tests_exit_codes", "repo_map_count",
    "test_files_touched", "edits_outside_scope", "changed_files",
    "subagent_enabled", "subagent_forced", "subagent_spawn_calls", "subagent_adopted",
    "subagent_tool_calls", "subagent_messages", "subagent_text_chars",
    "subagent_result_chars", "subagent_tokens_estimate", "task_messages_per_type",
)
# P0 三口径 + P1 D 臂的账。**新键不在上面 27 个里** —— 旧 jsonl 的列序逐字不变，
# 新数据追加在后面（跨轮对比脚本按 key 取值，不按下标）。
EXTRA_METRIC_KEYS = (
    "input_uncached", "cache_read", "cache_creation",
    "policy_enabled", "policy_k_groups",
    # ⚠️ B4（修复轮 #3）：`policy_dispatch_calls` **已退役**（含义含混：其实只是
    # 「每组最后一次尝试的 Task 数」，v2-fix-trunc 里 =4，而 K=3、scout 会话=6、
    # 全部 attempt 的 Task 数=9）—— 新产物不再产出它，这里**只保留成历史列**，
    # 好让旧产物重读汇总时数值不丢、列序不变（新产物上它恒为 null）。
    # 取代它的明确计法与治疗门槛在末尾追加。
    "policy_dispatch_calls",
    "policy_groups_completed", "policy_groups_truncated", "policy_groups_undiagnosed",
    "policy_retries", "policy_findings_total", "policy_findings_verified",
    "policy_findings_refuted", "policy_index_chars", "policy_index_max",
    "policy_snapshot_head", "policy_worktrees_after_dispatch",
    # R4/R5 追加（仍在末尾追加，不改上面任何一项的位置）。
    "policy_groups_final", "policy_truncated_retry_succeeded",
    "policy_scout_input_uncached", "policy_scout_cache_read",
    "policy_scout_cache_creation", "policy_scout_output_tokens",
    "policy_scout_cost_usd", "policy_scout_elapsed_s", "policy_scout_sessions",
    "policy_durable_finals_read",
    # B1/B4 追加：主口径 + 治疗门槛 + 包装模型自研的反证计数 + 结论来源分布。
    "policy_task_tool_use_count", "policy_task_tool_use_count_last_attempt",
    "policy_gate_metric", "policy_c1_gate", "policy_groups_with_scout_conclusion",
    "policy_wrapper_mcp_calls", "policy_wrapper_mcp_denied",
    "policy_wrapper_mcp_effective", "policy_wrapper_extra_task_calls",
    "policy_wrapper_background_spawns",
    "policy_finding_sources",
)


def norm_tools(d: dict) -> dict:
    """把 mcp__code-tools__read_file 这种前缀去掉，读起来才是"工具名"。

    注意：**只改展示用的副本**。指标里那个带前缀的字典一个字没动 ——
    前缀是 server 名的证据，改掉就没法证明工具真的走了 MCP。
    """
    return {k.replace("mcp__code-tools__", ""): v for k, v in (d or {}).items()}


def schedule() -> list[tuple[str, int]]:
    """交错顺序：A1 B1 C1 A2 B2 C2 ...（见模块 docstring；--arms 可换臂集）。"""
    return [(arm, i) for i in range(1, N_PER_ARM + 1) for arm in ARMS]


def label_of(arm: str, i: int) -> str:
    return f"{TAG}-{arm}-{i}"


def artifact(label: str) -> Path:
    return OUT / f"agent-{label}.json"


# ── R9：--report 不给 --arms 时的臂集/fixture 推断 ─────────────────────────
# 旧行为（第二轮复审 F3 的实测）：`--report --tag subagent-ab-v2` 不带 --arms 时
# 用的是默认调度表 A,B,C ⇒ 只捞出 A 的 4 份产物，**静默把 D 臂丢掉**，还把
# runs.jsonl 从 8 行覆盖成 4 行、manifest 写「完成 4/12 次」。
# 产物文件名里本来就写着臂号与第几次（`agent-<TAG>-<arm>-<n>.json`），
# 所以汇总阶段的臂集应当**从产物反推**，而不是从默认值拿。
def scan_artifacts(tag: str) -> tuple[tuple[str, ...], int, str | None]:
    """扫 out/ 里的 `agent-<tag>-<arm>-<n>.json`。

    返回 (臂集（按 ARMS_ALL 顺序）, 每臂最大次数 n, 靶场名或 None)。
    认不出的文件名（别的 tag 的产物被 glob 捞进来）直接跳过 —— 靠"臂名必须在
    ARMS_ALL 里"这条判断，而不是靠字符串前缀匹配。
    """
    arms: dict[str, int] = {}
    fixture: str | None = None
    for p in sorted(OUT.glob(f"agent-{tag}-*.json")):
        rest = p.name[len(f"agent-{tag}-"):-len(".json")]
        arm, _, n = rest.rpartition("-")
        if not arm or not n.isdigit() or arm not in ARMS_ALL:
            continue
        arms[arm] = max(arms.get(arm, 0), int(n))
        if fixture is None:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            fixture = (((d.get("trace") or {}).get("run") or {}).get("fixture")
                       or (d.get("metrics") or {}).get("fixture"))
    ordered = tuple(a for a in ARMS_ALL if a in arms)
    return ordered, (max(arms.values()) if arms else 0), fixture


def existing_run_labels(jsonl: Path) -> list[str]:
    """已有 runs.jsonl 里的 label 列表（用于「不许写出更短的」那道闸）。"""
    if not jsonl.is_file():
        return []
    out: list[str] = []
    for line in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            lab = json.loads(line).get("label")
        except ValueError:
            continue
        if lab:
            out.append(lab)
    return out


def console_log(label: str) -> Path:
    return OUT / f"{label}.console.log"


def run_one(arm: str, i: int) -> dict | None:
    label = label_of(arm, i)
    art, log = artifact(label), console_log(label)
    if art.exists():
        print(f"[skip] {label} 已有产物（要重跑先删 {art.name}）", flush=True)
    else:
        cmd = [sys.executable, str(REPO / "coding_agent.py"),
               "--fixture", FIXTURE, "--label", label] + ARM_FLAGS[arm]
        print(f"[run ] {label}  {' '.join(cmd[2:])}", flush=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            proc = subprocess.run(cmd, cwd=REPO, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  encoding="utf-8", errors="replace")
            fh.write(proc.stdout or "")
        print(f"[done] {label}  exit={proc.returncode}", flush=True)
        if not art.exists():
            # 产物没落盘 = 这次不算数，绝不拿控制台文本去凑一个指标出来。
            print(f"[!!  ] {label} 没有产物 {art.name}，本次跳过", flush=True)
            return None
    d = json.loads(art.read_text(encoding="utf-8"))
    m = dict(d.get("metrics") or {})
    rec = {"label": label, "arm": arm, "n": i}
    for k in METRIC_KEYS:
        rec[k] = m.get(k)
    # P0/P1 追加键（旧产物没有就是 None，不猜 0）。
    for k in EXTRA_METRIC_KEYS:
        rec[k] = m.get(k)
    rec["tools"] = norm_tools(m.get("tool_calls_per_tool"))
    return rec


def median_range(vals: list) -> dict:
    vals = [v for v in vals if isinstance(v, (int, float))]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "median": statistics.median(vals),
            "min": min(vals), "max": max(vals),
            "range": [min(vals), max(vals)]}


def report(recs: list[dict]) -> str:
    lines = []
    for arm in ARMS:
        rs = [r for r in recs if r["arm"] == arm]
        if not rs:
            continue
        lines.append(f"── 臂 {arm}（n={len(rs)}）")
        succ = sum(1 for r in rs if r["success"])
        lines.append(f"   成功率 {succ}/{len(rs)}")
        for key in ("input_tokens", "output_tokens", "cost_usd", "elapsed_s",
                    "tool_calls_total", "subagent_spawn_calls"):
            st = median_range([r.get(key) for r in rs])
            if st["n"] == 0:
                continue
            lines.append(f"   {key:24s} 中位数 {st['median']:<10}  "
                         f"极差 [{st['min']}, {st['max']}]")
        # P0 三口径单独打 —— input_tokens 只是未缓存那一份，AB 结论要写明口径。
        for key in ("input_uncached", "cache_read", "cache_creation"):
            st = median_range([r.get(key) for r in rs])
            if st["n"] == 0:
                continue
            lines.append(f"   {key:24s} 中位数 {st['median']:<10}  "
                         f"极差 [{st['min']}, {st['max']}]")
        if arm != "A":
            adopted = sum(1 for r in rs if r.get("subagent_adopted"))
            spawns = sum(r.get("subagent_spawn_calls") or 0 for r in rs)
            lines.append(f"   采用 {adopted}/{len(rs)}　spawn 总次数 {spawns}")
        if arm == "D":
            # B4：D 臂的派发由 driver 做（模型侧 spawn 恒 0）。**主口径 = 一次 harness
            # 派发 = 一个 scout 会话**（policy_scout_sessions，续派算两次）+ K。
            # ⚠️ 不用 policy_dispatch_calls（已退役：那只是"每组最后一次尝试的 Task 数"）。
            sessions = sum(r.get("policy_scout_sessions") or 0 for r in rs)
            kmed = median_range([r.get("policy_k_groups") for r in rs])
            ttu = median_range([r.get("policy_task_tool_use_count") for r in rs])
            comp = sum(1 for r in rs if (r.get("policy_groups_completed") or 0) > 0)
            idxc = median_range([r.get("policy_index_chars") for r in rs])
            lines.append(f"   主口径：scout 会话 {sessions} 次（=Σ每组 attempts，含 driver 续派）"
                         f"　K 中位数 {kmed.get('median', '-')}"
                         f"　Task tool_use 中位 {ttu.get('median', '-')}"
                         f"（含包装模型自发的，**不作门槛**）")
            lines.append(f"   有 verified 结论的运行 {comp}/{len(rs)}"
                         f"　索引字符 中位 {idxc.get('median', '-')} "
                         f"极差 [{idxc.get('min', '-')}, {idxc.get('max', '-')}]")
            # B4：治疗门槛（C1）—— 按「每组至少有一次 scout 结论」判定，逐轮给结果。
            gates = [r.get("policy_c1_gate") for r in rs]
            gate_txt = ", ".join(str(g) if g is not None else "无数据（旧产物）" for g in gates)
            lines.append(f"   C1 治疗门槛（每组至少一次 scout 结论）：{gate_txt}")
            # B1：包装模型自己动手的反证（修复后 effective 应恒为 0）。
            wmcp = sum(r.get("policy_wrapper_mcp_calls") or 0 for r in rs)
            wden = sum(r.get("policy_wrapper_mcp_denied") or 0 for r in rs)
            weff = sum(r.get("policy_wrapper_mcp_effective") or 0 for r in rs)
            wextra = sum(r.get("policy_wrapper_extra_task_calls") or 0 for r in rs)
            lines.append(f"   包装会话自研反证（B1）：mcp__code-tools__* 尝试 {wmcp} 次"
                         f"（被权限拦下 {wden}，**有效 {weff}**）　包装模型自发 Task {wextra} 次")
            srcs = [r.get("policy_finding_sources") for r in rs
                    if r.get("policy_finding_sources")]
            if srcs:
                lines.append(f"   每条 finding 的来源分布（B1-3）：{srcs[0]}"
                             + (f"　（另 {len(srcs) - 1} 次见 runs.jsonl）" if len(srcs) > 1 else ""))
            # R4：每组的终态记录（completed / undiagnosed + 是否经过续派）就在这里，
            # 不用回去翻 policy_dispatch.jsonl 才能知道「这一轮到底定位到几组」。
            gf = [r.get("policy_groups_final") for r in rs if r.get("policy_groups_final")]
            if gf:
                lines.append(f"   终态记录（R4）：{gf[0]}"
                             + (f"　（另 {len(gf) - 1} 次见 runs.jsonl）" if len(gf) > 1 else ""))
            retry_ok = sum(r.get("policy_truncated_retry_succeeded") or 0 for r in rs)
            lines.append(f"   截断续派后成功（R4 可见）：{retry_ok} 组")
            # R5：scout 侧成本单独一行 —— 它不在父层 usage 里（独立会话），
            # 不加这一行「端到端成本」就是少算的。
            sc = sum(r.get("policy_scout_cost_usd") or 0 for r in rs)
            par = sum(r.get("cost_usd") or 0 for r in rs)
            scel = median_range([r.get("policy_scout_elapsed_s") for r in rs])
            if any(r.get("policy_scout_cost_usd") is not None for r in rs):
                lines.append(f"   scout 侧成本 合计 {sc:.6f}　父层成本 合计 {par:.6f}"
                             f"　端到端（父+scout）合计 {sc + par:.6f}"
                             f"　scout 墙钟中位 {scel.get('median', '-')}s")
            else:
                lines.append("   scout 侧成本：无数据（policy_scout_cost_usd 全为 null）")
    return "\n".join(lines)


def main() -> int:
    # ⚠️ `global` 必须出现在函数里**第一次用到这个名字之前** ——
    # 放在 argparse 之后会直接 SyntaxError: name 'FIXTURE' is used prior to global declaration。
    global FIXTURE, TAG, ARMS, N_PER_ARM
    # 本仓库的老教训：任何新程序第一次 print 之前必须先把标准输出掰成 UTF-8，
    # 否则中文在 GBK 控制台上会变成乱码，而且**是部分损坏**、看起来像"跑通了"。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只汇总已跑完的，不跑新的")
    # ⚠️ 两个 default 故意是 None 而不是各自的模块常量：本脚本需要区分
    #    「调用方显式给了一个值」与「没给」。给了就用；没给时 —— 跑批走默认调度表，
    #    而 --report 从产物反推（R9）。写成 default=FIXTURE 会让"显式传了默认值"
    #    与"没传"完全同形，就没法做这个区分了。
    ap.add_argument("--fixture", default=None, help="靶场名（默认 more-itertools-multi；"
                                                   "--report 且不给时从产物里读）")
    ap.add_argument("--tag", default=TAG, help="文件名/标签前缀（默认 subagent-ab）")
    ap.add_argument("--arms", default=None,
                    help=f"逗号分隔的臂集（可选 {','.join(ARMS_ALL)}；"
                         f"v2 的 A vs D 用 --arms A,D）。跑批不给时为默认 A,B,C；"
                         f"**--report 不给时从 out/ 已有产物反推**，推不出来直接报错退出"
                         f"（绝不写出比已有更短的 runs.jsonl）")
    args = ap.parse_args()

    TAG = args.tag
    if args.fixture is not None:
        FIXTURE = args.fixture
    arms_explicit = args.arms is not None
    fixture_explicit = args.fixture is not None
    arm_source = "--arms（显式）"

    if arms_explicit:
        sel = tuple(a.strip().upper() for a in args.arms.split(",") if a.strip())
        bad = [a for a in sel if a not in ARMS_ALL]
        if bad or not sel:
            print(f"[中止] --arms 里有不认识的臂：{bad or '(空)'}，可选 {ARMS_ALL}",
                  file=sys.stderr)
            return 2
        ARMS = sel
    elif args.report:
        # R9：汇总阶段的臂集从**产物**反推，不从默认调度表拿。
        infer_arms, infer_n, infer_fixture = scan_artifacts(TAG)
        if not infer_arms:
            print(f"[中止] --report 没给 --arms，且从 out/ 里推不出臂集："
                  f"没有 agent-{TAG}-<臂>-<n>.json（可选臂 {','.join(ARMS_ALL)}）。"
                  f" 拒绝写出空/更短的 {TAG}-runs.jsonl。", file=sys.stderr)
            return 2
        ARMS = infer_arms
        N_PER_ARM = max(N_PER_ARM, infer_n)
        arm_source = f"从产物推断（{len(infer_arms)} 臂，每臂 n<={infer_n}）"
        if not fixture_explicit and infer_fixture:
            FIXTURE = infer_fixture
        print(f"[推断] --arms 未给：臂集 {''.join(ARMS)}　每臂 n={N_PER_ARM}　"
              f"靶场 {FIXTURE}（来源：{arm_source}"
              f"{'，靶场从产物 trace.run.fixture 读' if not fixture_explicit else ''}）")

    recs: list[dict] = []
    if not args.report:
        for arm, i in schedule():
            rec = run_one(arm, i)
            if rec:
                recs.append(rec)
    # 汇总阶段总是从产物重读一遍：跑到一半中断也能拿到已有数据。
    if not recs:
        for arm, i in schedule():
            if artifact(label_of(arm, i)).exists():
                rec = run_one(arm, i)
                if rec:
                    recs.append(rec)

    OUT.mkdir(parents=True, exist_ok=True)
    jsonl = OUT / f"{TAG}-runs.jsonl"
    # ── R9 的闸：绝不写出比已有更短的 runs.jsonl ────────────────────────────
    # 判据用 label 集合的**超集**关系（不是行数）：行数相同但换了集合一样是丢数据。
    # 想真的要缩减，得先把 runs.jsonl 删掉/备份走 —— 那是一个显式的动作，
    # 不会被一次忘了带 --arms 的汇总顺手做掉（那次实测就是这么把 8 行写成 4 行的）。
    old_labels = existing_run_labels(jsonl)
    new_labels = {r["label"] for r in recs}
    lost = [lab for lab in old_labels if lab not in new_labels]
    if lost:
        print(f"[中止] **拒绝写入**：本次汇总只有 {len(recs)} 条，而 {jsonl.name} 已有 "
              f"{len(old_labels)} 条；这 {len(lost)} 条会丢：{lost[:8]}"
              f"{' …' if len(lost) > 8 else ''}\n"
              f"  宁可什么都不写，也不静默丢臂/丢轮次。"
              f"（跑批器未跑的那些：{'、'.join(a for a in ARMS_ALL if a not in ARMS) or '无'}）",
              file=sys.stderr)
        return 3
    with jsonl.open("w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    body = report(recs)
    text = (f"④ 层 A/B/C/D 跑批清单\n靶场 {FIXTURE}　每臂 n={N_PER_ARM}　"
            f"臂集 {''.join(ARMS)} 交错（来源：{arm_source}）\n"
            f"完成 {len(recs)}/{N_PER_ARM * len(ARMS)} 次\n\n{body}\n")
    (OUT / f"{TAG}-manifest.txt").write_text(text, encoding="utf-8")
    print("\n" + text)
    print(f"落盘：{jsonl.name} / {TAG}-manifest.txt"
          f"（{len(recs)} 行；写之前已有 {len(old_labels)} 行，全部保留）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
