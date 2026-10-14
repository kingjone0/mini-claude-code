"""P1 三臂接线自检（预注册 §1.3 W1–W4 + §5.7）。

**这个脚本是「判据」而不是「实现」**：它只读 `coding_agent.arm_report_row()` 落盘的 JSON，
按预注册钉死的四条 W 检查逐条核对。**不允许**在这里重新算一遍 sandbox_spec / env ——
那会让「检查用的副本」与「真正生效的值」分叉（陷阱 14），正是本仓库反复栽过的失效模式。

用法：
    python eval/harness/arm_check.py --arm-report <path-to-arm-report.jsonl> \
        --instance-id <iid> [--expected-k N]

退出码：四条全过 = 0；任一不过 = 2（停批信号）。

产物：同目录下的 `arm_check_result.json`（结构化证据，供结果节引用）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.dirname(HERE)
PROJECT_ROOT = os.path.dirname(EVAL_DIR)
for p in (PROJECT_ROOT, EVAL_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import arms as A          # noqa: E402


def load_rows(path: str, instance_id: str | None = None) -> dict[str, dict]:
    """读 arm_report.jsonl，按臂字母分组。若给 instance_id，则只取 label 匹配的行。"""
    by_arm: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            label = row.get("argv", [])
            # 从 argv 里找 --label 的值
            lab_val = None
            for i, tok in enumerate(row.get("argv") or []):
                if tok == "--label" and i + 1 < len(row["argv"]):
                    lab_val = row["argv"][i + 1]
                    break
            if instance_id and lab_val and not lab_val.endswith(f"-{instance_id}-A") \
                    and not lab_val.endswith(f"-{instance_id}-B") \
                    and not lab_val.endswith(f"-{instance_id}-C"):
                # label 形如 p1-<iid>-<arm>；这里宽松匹配
                if instance_id not in (lab_val or ""):
                    continue
            arm_letter = None
            if lab_val:
                for letter in ("A", "B", "C"):
                    if lab_val.endswith(f"-{letter}"):
                        arm_letter = letter
                        break
            if arm_letter is None:
                continue
            # 同臂多行时取最后一行（重跑覆盖）
            by_arm[arm_letter] = row
    return by_arm


# ---------------------------------------------------------------- W1 argv diff

def check_w1(rows: dict[str, dict]) -> dict:
    """W1：三臂实际命令行逐 token diff 相对 A **只多出臂开关那几个 token**。"""
    commands = {arm: row["argv"] for arm, row in rows.items()}
    labels = {}
    for arm, row in rows.items():
        argv = row.get("argv") or []
        for i, tok in enumerate(argv):
            if tok == "--label" and i + 1 < len(argv):
                labels[arm] = argv[i + 1]
    cmp = A.compare_commands(commands, labels=labels)
    # 额外核：三臂都不得出现禁止参数
    forbidden_hits = {}
    for arm, row in rows.items():
        flags = set(row.get("flags_present") or [])
        bad = sorted(flags & set(A.FORBIDDEN_FLAGS))
        if bad:
            forbidden_hits[arm] = bad
    cmp["forbidden_hits"] = forbidden_hits
    cmp["pass"] = cmp["pass"] and not forbidden_hits
    return cmp


# ---------------------------------------------------------------- W2 options 装配

def check_w2(rows: dict[str, dict]) -> dict:
    """W2：A/B sandbox 逐项相等；C 相对 A 只允许多出 read_findings。"""
    specs = {arm: row.get("sandbox") for arm, row in rows.items()}
    missing = [a for a in A.ARM_ORDER if a not in specs]
    if missing:
        return {"pass": False, "reasons": [f"缺臂 sandbox：{missing}"]}
    keys = ("mcp_tools", "builtin_tools", "allowed_tools", "disallowed_tools", "agents")
    reasons = []
    ab_equal = True
    for k in keys:
        va = specs["A"].get(k)
        vb = specs["B"].get(k)
        vc = specs["C"].get(k)
        if va != vb:
            ab_equal = False
            reasons.append(f"A.{k} != B.{k}：{va} vs {vb}")
        if k == "mcp_tools":
            extra_c = sorted(set(vc or []) - set(va or []))
            missing_c = sorted(set(va or []) - set(vc or []))
            if extra_c != ["mcp__code-tools__read_findings"]:
                reasons.append(f"C.mcp_tools 相对 A 多出的不是仅 read_findings："
                               f"extra={extra_c}, missing={missing_c}")
            if missing_c:
                reasons.append(f"C.mcp_tools 少了 A 的工具：{missing_c}")
        elif k == "builtin_tools":
            if (va or []) != [] or (vc or []) != []:
                reasons.append(f"builtin_tools 必须都为空：A={va}, C={vc}")
        elif k == "allowed_tools":
            # W2 原文：「C 相对 A **只允许多出** mcp__code-tools__read_findings」。
            # allowed_tools 是 mcp_tools + builtin_tools 的并集，因此 C 相对 A 恰好多出
            # read_findings 是预期行为（不是违规）。这里只核「多出的是否恰好是它」。
            extra_c = sorted(set(vc or []) - set(va or []))
            missing_c = sorted(set(va or []) - set(vc or []))
            if extra_c != ["mcp__code-tools__read_findings"]:
                reasons.append(f"C.allowed_tools 相对 A 多出的不是仅 read_findings："
                               f"extra={extra_c}")
            if missing_c:
                reasons.append(f"C.allowed_tools 少了 A 的工具：{missing_c}")
        elif k == "disallowed_tools":
            # policy_mode 下 builtin=[] ⇒ disallowed 应逐字同 A（Task 仍在内）
            if va != vc:
                reasons.append(f"A.disallowed_tools != C.disallowed_tools：{va} vs {vc}")
        elif k == "agents":
            # policy 不放 spawn ⇒ agents 应为空（与 A 一致）
            if va != vc:
                reasons.append(f"A.agents != C.agents：{va} vs {vc}")
    return {"pass": ab_equal and not reasons, "reasons": reasons,
            "specs": {arm: {k: specs[arm].get(k) for k in keys} for arm in A.ARM_ORDER}}


# ---------------------------------------------------------------- W3 题面 SHA

def check_w3(rows: dict[str, dict]) -> dict:
    """W3：A/B 题面 SHA-256 相同；C 相对 A 不同（多了 policy_task_suffix）。"""
    shas = {arm: row.get("task_sha256") for arm, row in rows.items()}
    chars = {arm: row.get("task_chars") for arm, row in rows.items()}
    missing = [a for a in A.ARM_ORDER if not shas.get(a)]
    if missing:
        return {"pass": False, "reasons": [f"缺臂 task_sha256：{missing}"],
                "shas": shas, "chars": chars}
    reasons = []
    if shas["A"] != shas["B"]:
        reasons.append(f"A.task_sha256 != B.task_sha256：{shas['A']} vs {shas['B']}")
    if shas["A"] == shas["C"]:
        reasons.append("A.task_sha256 == C.task_sha256（C 应多出 policy_task_suffix）")
    return {"pass": not reasons, "reasons": reasons,
            "shas": shas, "chars": chars}


# ---------------------------------------------------------------- W4 MCP server env

def check_w4(rows: dict[str, dict]) -> dict:
    """W4：A/B 只有 AGENT_REPO_MAP_DEFAULT 不同；C 相对 A 只多出 AGENT_FINDINGS_DIR。"""
    envs = {arm: (row.get("mcp_server") or {}).get("env") for arm, row in rows.items()}
    missing = [a for a in A.ARM_ORDER if not envs.get(a)]
    if missing:
        return {"pass": False, "reasons": [f"缺臂 mcp_server.env：{missing}"]}
    reasons = []
    a_keys = set(envs["A"])
    b_keys = set(envs["B"])
    c_keys = set(envs["C"])
    # A/B 键集合应相同
    if a_keys != b_keys:
        reasons.append(f"A.env.keys != B.env.keys：symdiff="
                       f"{sorted((a_keys | b_keys) - (a_keys & b_keys))}")
    # A/B 之间只有 AGENT_REPO_MAP_DEFAULT 的值不同；AGENT_REPO_ROOT **允许**不同
    # （§5.5 每臂独立工作区，W4 原文也只要求「各自指向自己的实例工作区且值等于 realpath」，
    # 不要求三臂相同）。
    shared = a_keys & b_keys
    for k in shared:
        if k == "AGENT_REPO_MAP_DEFAULT":
            if envs["A"].get(k) == envs["B"].get(k):
                reasons.append(f"A/B 的 {k} 应该不同（summary vs symbols），"
                               f"实际都是 {envs['A'].get(k)!r}")
        elif k == "AGENT_REPO_ROOT":
            # 三臂各自指向自己的工作区，值互异是预期行为（下面单独核）
            pass
        else:
            if envs["A"].get(k) != envs["B"].get(k):
                reasons.append(f"A.{k} != B.{k}：{envs['A'].get(k)!r} vs {envs['B'].get(k)!r}")
    # C 相对 A 只多出 AGENT_FINDINGS_DIR
    extra_c = sorted(c_keys - a_keys)
    missing_c = sorted(a_keys - c_keys)
    if extra_c != ["AGENT_FINDINGS_DIR"]:
        reasons.append(f"C.env 相对 A 多出的不是仅 AGENT_FINDINGS_DIR：extra={extra_c}")
    if missing_c:
        reasons.append(f"C.env 缺了 A 的键：{missing_c}")
    # AGENT_REPO_ROOT 三臂各自指向自己的工作区（值互异且为 realpath）
    roots = {arm: envs[arm].get("AGENT_REPO_ROOT") for arm in A.ARM_ORDER}
    if len(set(roots.values())) != len(A.ARM_ORDER):
        reasons.append(f"AGENT_REPO_ROOT 三臂不互异：{roots}")
    for arm, root in roots.items():
        if root and os.path.exists(root):
            real = os.path.realpath(root)
            if root != real:
                reasons.append(f"{arm}.AGENT_REPO_ROOT 不是 realpath：{root} vs {real}")
    return {"pass": not reasons, "reasons": reasons,
            "envs": {arm: dict(envs[arm]) for arm in A.ARM_ORDER},
            "repo_roots": roots}


# ---------------------------------------------------------------- §1.2 G-K

def check_gk(rows: dict[str, dict], expected_k: int | None) -> dict:
    """§1.2：C 臂的派发分组数 == K_expected、index_chars ≤ 2000、worktree 只剩主工作区。"""
    c = rows.get("C")
    if not c:
        return {"pass": False, "reasons": ["缺 C 臂记录"]}
    pol = c.get("policy") or {}
    if not pol:
        return {"pass": False, "reasons": ["C 臂没有 policy 字段（--delegate=policy 未生效？）"]}
    observable = pol.get("observable", True)
    groups_obs = pol.get("groups_observed") or []
    k_obs = pol.get("k_observed", len(groups_obs))
    index_chars = pol.get("index_chars")
    wt_after = pol.get("worktrees_after_dispatch")
    reasons = []
    if not observable:
        reasons.append("dispatch_grouping_unobservable：基线 pytest 输出里取不到任何 "
                       "F2P 形状的失败行 ⇒ C 臂按 §1.2/§4.4 如实报不可测")
    if expected_k is not None and k_obs != expected_k:
        reasons.append(f"k_observed {k_obs} != K_expected {expected_k}")
    if index_chars is not None and index_chars > 2000:
        reasons.append(f"index_chars {index_chars} > 2000")
    if wt_after is not None and wt_after != 1:
        reasons.append(f"worktrees_after_dispatch {wt_after} != 1")
    return {"pass": not reasons, "reasons": reasons,
            "policy": pol, "expected_k": expected_k}


# ---------------------------------------------------------------- 臂身份自证

def check_identity(rows: dict[str, dict]) -> dict:
    """I-8：console 自证行的机器版（arm_report 里的 repo_map_default/source/delegate）。"""
    reasons = []
    for arm, row in rows.items():
        spec = A.ARM_IDENTITY.get(arm)
        if not spec:
            continue
        got_rmd = row.get("repo_map_default")
        got_src = row.get("repo_map_default_source")
        got_del = row.get("delegate")
        exp_rmd = spec["repo_map_default"]
        exp_src = spec["repo_map_default_source"]
        exp_del = spec["delegate"]
        if got_rmd != exp_rmd:
            reasons.append(f"{arm}: repo_map_default={got_rmd!r} != 期望 {exp_rmd!r}")
        if got_src != exp_src:
            reasons.append(f"{arm}: repo_map_default_source={got_src!r} != 期望 {exp_src!r}")
        if got_del != exp_del:
            reasons.append(f"{arm}: delegate={got_del!r} != 期望 {exp_del!r}")
    return {"pass": not reasons, "reasons": reasons}


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="P1 三臂接线自检（§1.3 W1–W4 + §5.7）")
    ap.add_argument("--arm-report", required=True, help="arm_report.jsonl 路径")
    ap.add_argument("--instance-id", default=None, help="只核某条实例（label 含此子串）")
    ap.add_argument("--expected-k", type=int, default=None,
                    help="§1.2 的 K_expected（不给就跳过 G-K 数量核对）")
    ap.add_argument("--out", default=None, help="结果 JSON 输出路径（缺省=同目录 arm_check_result.json）")
    args = ap.parse_args(argv)

    if not os.path.exists(args.arm_report):
        print(f"[中止] arm_report 不存在：{args.arm_report}", file=sys.stderr)
        return 2
    rows = load_rows(args.arm_report, instance_id=args.instance_id)
    missing = [a for a in A.ARM_ORDER if a not in rows]
    if missing:
        print(f"[中止] arm_report 缺臂：{missing}（文件={args.arm_report}）", file=sys.stderr)
        return 2

    w1 = check_w1(rows)
    w2 = check_w2(rows)
    w3 = check_w3(rows)
    w4 = check_w4(rows)
    gk = check_gk(rows, args.expected_k)
    ident = check_identity(rows)

    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "arm_report": args.arm_report,
        "instance_id": args.instance_id,
        "expected_k": args.expected_k,
        "W1_argv_diff": w1,
        "W2_options": w2,
        "W3_task_sha": w3,
        "W4_mcp_env": w4,
        "GK_dispatch": gk,
        "identity": ident,
        "pass": all(x["pass"] for x in (w1, w2, w3, w4, gk, ident)),
    }
    out_path = args.out or os.path.join(os.path.dirname(args.arm_report), "arm_check_result.json")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")

    banner = "[PASS]" if result["pass"] else "[FAIL]"
    print(f"\n{banner} P1 臂接线自检  ({args.arm_report})")
    for name, chk in (("W1 argv diff", w1), ("W2 options", w2), ("W3 task sha", w3),
                      ("W4 mcp env", w4), ("G-K dispatch", gk), ("identity", ident)):
        mark = "PASS" if chk["pass"] else "FAIL"
        print(f"  [{mark}] {name}")
        for r in chk.get("reasons", []):
            print(f"       · {r}")
    print(f"\n结果 JSON：{out_path}")
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
