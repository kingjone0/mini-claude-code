"""**三臂定义的单一真值源**（P1 预注册 §1.1 / §1.3 / §5.5）。

规则出处：`eval/P1-PREREGISTRATION.md` §1–§5。本模块只做一件事 ——
把「A / B / C 各是什么命令行」写在一处，供 **调度器、接线自检、产物行** 三方共用。

为什么必须是「一处」（而不是三份拷贝）：
P1 的全部结论建立在「B 与 A 只差一个开关、C 与 A 只差一个开关」这句话上。
一旦这个映射在两个地方各写一遍，早晚会分叉；而分叉的表现**不是报错**，
是「某一臂偷偷多带了一个参数」⇒ 那条臂的数字变成另一套干预的测量值，
却仍然被写进对比表（预注册 §5.3：变量设错最坏的表现是**伪装成「这一臂没效果」**）。

三条硬约束（逐条对应预注册）：

1. **臂开关只有 extra_args 一项差异**（§1.1 / W1）：
   A = `[]`（现状，**禁止**出现任何 `--repo-map-default`）
   B = `["--repo-map-default", "symbols"]`
   C = `["--delegate", "policy"]`
2. **共享常量一律不传**（§1.3 K4/K18/K19）：`--max-turns` / `--no-write` /
   `--subagent*` / `--fixture` / `--scout-turn-cap` 在三臂命令行里都不得出现。
   本模块用 `FORBIDDEN_FLAGS` 把它写成**数据**，自检据此当场拦。
3. **每臂一个 run_id、一份独立工作区**（§5.5）：run_id 形如
   `p1-<UTC时间戳>-<instance_id>-<A|B|C>`，venv 沿用 P0 已修的 (实例, run) 隔离。

⚠️ 两处「让 C 臂可跑的必要接线」（不在本文件，但由本文件的判据守着）：
  · `coding_agent.py` 原先硬拦 `--delegate=policy` 只允许 `--fixture more-itertools-hard`
    ⇒ 实例模式（`--repo-root`）被放行；
  · `agent/policy_dispatch.group_failures()` 原先按 `<Name>Tests::` 类名分组
    ⇒ pilot-12 的 F2P node id 不含该形状，会整批回落成单组 `OTHER`（K 恒为 1）。
    现在按 §1.2 的 G-K 判据**以测试文件路径为组键**。
  这两处属于「让既有开关在实例模式下可达」，**不构成第二个变量**：
  W1 的 argv diff 仍然只允许多出 `[臂开关]` 那几个 token。
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

# ---------------------------------------------------------------- 臂定义（唯一真值源）

ARM_ORDER: tuple[str, ...] = ("A", "B", "C")

#: 臂名 -> 人读名称（§1.1 表）
ARM_NAME: dict[str, str] = {
    "A": "现状 / baseline",
    "B": "仓库地图检索（repo_map symbols）",
    "C": "driver 强制派发子代理（delegate=policy）",
}

#: 臂名 -> **相对 A 只多出来的那一个开关**（§1.1 写死；不许自行增减）
ARM_EXTRA_ARGS: dict[str, list[str]] = {
    "A": [],
    "B": ["--repo-map-default", "symbols"],
    "C": ["--delegate", "policy"],
}

#: 每条臂在 agent 侧应当落盘的「生效取值 + 来源」自证（§1.1 那张表的机器版）。
#: `expected_console_markers` 是 I-8 臂身份自证的 grep 判据；
#: `forbidden_console_markers` 用来抓「偷偷多带了开关」这类污染。
ARM_IDENTITY: dict[str, dict[str, Any]] = {
    "A": {
        "repo_map_default": "summary",
        "repo_map_default_source": "默认（未设置）",
        "delegate": None,
        "expected_console_markers": [
            "repo_map 默认详细度 = 'summary'  （来源：默认（未设置））",
            "④ 子代理 = 关（现状）",
            "回合上限 = 120  （来源：实例模式默认(120)）",
        ],
        "forbidden_console_markers": ["P1 驱动派发 = 开"],
    },
    "B": {
        "repo_map_default": "symbols",
        "repo_map_default_source": "--repo-map-default",
        "delegate": None,
        "expected_console_markers": [
            "repo_map 默认详细度 = 'symbols'  （来源：--repo-map-default）",
            "④ 子代理 = 关（现状）",
            "回合上限 = 120  （来源：实例模式默认(120)）",
        ],
        "forbidden_console_markers": ["P1 驱动派发 = 开"],
    },
    "C": {
        "repo_map_default": "summary",
        "repo_map_default_source": "默认（未设置）",
        "delegate": "policy",
        "expected_console_markers": [
            "repo_map 默认详细度 = 'summary'  （来源：默认（未设置））",
            "P1 驱动派发 = 开（--delegate=policy；scout 回合上限 24）",
            "回合上限 = 120  （来源：实例模式默认(120)）",
        ],
        "forbidden_console_markers": [],
    },
}

#: 三臂命令行里**一律不得出现**的参数（§1.3 K4 / K18 / K19）。
#: 这是数据而不是注释 —— `check_argv_unique_variable()` 会逐条查它。
FORBIDDEN_FLAGS: dict[str, str] = {
    "--max-turns": "K4：三臂一律不传 ⇒ 走实例模式默认 INSTANCE_MAX_TURNS=120。"
                   "回合上限必须是**常量而不是变量**：① D8 实证 40 会在 agent 已改对后截断；"
                   "② 上限做成臂内差异会让低上限的臂堆满「撞回合上限」归因桶，那个数就没意义了",
    "--no-write": "K18：不启用 ⇒ 三臂都给写权限，否则 B/C 的补丁根本产不出来",
    "--subagent": "K19：模型自愿派发是另一套干预，与 --delegate 互斥",
    "--subagent-force": "K19：通道阳性对照专用，不参与 P1 三臂",
    "--fixture": "K19：与实例入口 --repo-root 互斥",
    "--scout-turn-cap": "K19：正式跑批一律用缺省 SCOUT_MAX_TURNS=24",
}

#: 父进程环境里不得预留的键（§5.2 I-6）。A 臂尤其要求 `AGENT_REPO_MAP_DEFAULT` **未设置**，
#: 否则 §1.1 的来源自证会变成假的（显式 env 与 CLI 开关是两条不同的来源链）。
ENV_CLEANLINESS_KEYS: tuple[str, ...] = (
    "AGENT_REPO_MAP_DEFAULT",
    "AGENT_FINDINGS_DIR",
    "AGENT_REPO_ROOT",
    "AGENT_SERVER_INSTRUCTIONS",
    "AGENT_REPO_MAP_DESC",
)

#: §1.3 K10 / K11：agent 阶段墙钟与单实例总墙钟（含建 venv）。
AGENT_PHASE_TIMEOUT_S = 900
TOTAL_RUN_TIMEOUT_S = 2400
#: §1.3 K12：并发上限（>3 会把 pytest 计时拖长，造成系统性 timeout 偏置）。
MAX_CONCURRENCY = 3
#: §1.3 K12 的机器校验：实测并发峰值 > 此值 ⇒ harness_bug。
CONCURRENCY_PEAK_LIMIT = 3


class ArmSpecError(RuntimeError):
    """臂定义/命令行违反预注册（W1 不过 => 停批）。"""


# ---------------------------------------------------------------- 题面（K5：三臂共用同一份）

def problem_path(run_root: str, instance_id: str) -> str:
    """每个实例一份 `problem.txt`，**三臂共用同一个文件名与内容**（§1.3 K5 / I-2）。

    注意「共用」的含义是：**同一份内容 + 同一条构造路径**，不是「复用别的臂的工作区」
    （§5.5 明确禁止跨臂引用产物）。每个 run 目录里都有一份**逐字节相同**的文件，
    路径随 run_id 变，SHA-256 不随 run_id 变 —— 这样「同一份题面」是可查的事实，
    而不是靠人回忆。
    """
    return os.path.join(run_root, instance_id, "problem.txt")


def write_problem_file(path: str, problem_statement: str) -> dict:
    """写题面并返回溯源（sha256 / bytes / 原文）。空题面 => 该实例三臂全部作废（K5）。"""
    text = (problem_statement or "").strip() + "\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {"path": path, "sha256": sha, "bytes": len(text.encode("utf-8"))}


def check_problem_identical(rows: list[dict]) -> dict:
    """I-2：同一实例三臂的 problem.txt SHA-256 必须逐字相同。"""
    shas = sorted({r.get("problem_sha256") for r in rows})
    return {"identical": len(shas) == 1 and shas[0] is not None,
            "distinct_sha256": shas,
            "per_arm": {r.get("arm"): r.get("problem_sha256") for r in rows}}


# ---------------------------------------------------------------- 命令行装配

def agent_python(project_root: str, base_python: str) -> list[str]:
    """统一入口的前两段：`<python> -X utf8 coding_agent.py`（§1.1 / K17）。"""
    return [base_python, "-X", "utf8", os.path.join(project_root, "coding_agent.py")]


def build_agent_cmd(*, python_head: list[str], workspace: str, problem_file: str,
                    label: str, arm: str) -> list[str]:
    """按 §1.1 的命令骨架拼出**某一条臂的实际命令行**（token 列表）。

    这条函数是三臂命令行的**唯一**产生处：调度器实跑用它，接线自检打印也用它。
    两边不是两份代码，因此「自检通过」与「实跑的命令行」天然是同一件事。

    参数顺序写死（`--repo-root` / `--problem-file` / `--label` / [臂开关]），
    臂开关永远在最后 ⇒ 逐 token diff 时它是唯一可能出现的尾巴。
    """
    if arm not in ARM_ORDER:
        raise ArmSpecError(f"未知臂：{arm!r}（只支持 {ARM_ORDER}）")
    cmd = list(python_head) + [
        "--repo-root", workspace,
        "--problem-file", problem_file,
        "--label", label,
    ] + list(ARM_EXTRA_ARGS[arm])
    check_argv_unique_variable(cmd, arm=arm)
    return cmd


#: 值随 run 变化的**隔离性路径参数**（§5.5：一次运行一个 run_id ⇒ 工作区/题面路径必然不同）。
#: 它们不是实验变量，所以逐 token diff 时把**值**折成占位符；参数名本身照样参与比较，
#: 因此「少了一个 --repo-root」仍然会被抓出来。label 不在此列 —— 它带着臂字母，
#: 单独按 `label_pattern` 校验（见 compare_commands）。
ISOLATION_VALUE_FLAGS = ("--repo-root", "--problem-file", "--arm-report-file")


def normalize_argv(argv: list[str], *, label_pattern: str | None = None) -> list[str]:
    """把命令行折成「可跨臂比较的形状」：

    · `--repo-root` / `--problem-file` / `--arm-report-file` 的**绝对路径值** → `<PATH>`
      （隔离差异，非变量；arm-report-file 由 harness 按 (instance, arm) 注入，三臂互异）；
    · `--label` 的值 → `<LABEL>`（值里含臂字母，形状另查）；
    · 其余 token **逐字保留** ⇒ 「不允许少 token、不允许其它 token 变化」是真判据。
    """
    out: list[str] = []
    skip_next = False
    for tok in argv:
        if skip_next:
            skip_next = False
            continue
        if tok in ISOLATION_VALUE_FLAGS:
            out += [tok, "<PATH>"]
            skip_next = True
            continue
        if tok == "--label":
            out += [tok, "<LABEL>"]
            skip_next = True
            continue
        out.append(tok)
    return out


def _token_diff(ref: list[str], got: list[str]) -> tuple[list[str], list[str]]:
    """两个 token 序列的差异：(只出现在 got 的, 只出现在 ref 的)。"""
    from difflib import SequenceMatcher
    sm = SequenceMatcher(a=ref, b=got, autojunk=False)
    added: list[str] = []
    removed: list[str] = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace"):
            added.extend(got[j1:j2])
        if tag in ("delete", "replace"):
            pass
    for tag, i1, i2, _j1, _j2 in sm.get_opcodes():
        if tag == "delete":
            removed.extend(ref[i1:i2])
    return added, removed


def compare_commands(commands: dict[str, list[str]], *,
                     labels: dict[str, str] | None = None) -> dict:
    """W1 的机器判据：逐 token diff 相对 A **只多出臂开关那几个 token**。

    三条一起成立才算过：
      ① 归一化后的 token 多重集合相等：`set(B) - set(A) == 该臂开关`；
      ② 没有**减少**任何 token（漏传共享参数同样是污染）；
      ③ 位置：臂开关永远在尾巴上（拼装顺序由 `build_agent_cmd` 保证）。
    另外单独核 label 形状（`p1-<iid>-<arm>`）与隔离路径确实互异。
    """
    missing = [a for a in ARM_ORDER if a not in commands]
    if missing:
        raise ArmSpecError(f"compare_commands 缺少臂：{missing}")
    ref_raw = commands["A"]
    ref = normalize_argv(ref_raw)
    pairs: dict[str, dict] = {}
    for arm in ARM_ORDER:
        got_raw = commands[arm]
        got = normalize_argv(got_raw)
        expected_extra = list(ARM_EXTRA_ARGS[arm])
        added, removed = _token_diff(ref, got)
        tail_ok = (got == ref + expected_extra)
        pairs[arm] = {
            "arm": arm,
            "argv_normalized": got,
            "extra_expected": expected_extra,
            "extra_actual": added,
            "removed_tokens": removed,
            "arm_switch_at_tail": tail_ok,
            "pass": (added == expected_extra and not removed and tail_ok),
        }
    label_check = {}
    if labels:
        shared_iid = None
        for a in ARM_ORDER:
            m = re.match(r"^p1-(.+)-([ABC])$", labels.get(a, "") or "")
            ok = bool(m) and m.group(2) == a
            if m and shared_iid is None:
                shared_iid = m.group(1)
            if m and shared_iid != m.group(1):
                ok = False
            label_check[a] = ok
    iso_paths_unused = None
    ws_values = []
    pf_values = []
    for a in ARM_ORDER:
        c = commands[a]
        if "--repo-root" in c:
            ws_values.append(c[c.index("--repo-root") + 1])
        if "--problem-file" in c:
            pf_values.append(c[c.index("--problem-file") + 1])
    all_ok = all(p["pass"] for p in pairs.values())
    if labels and not all(label_check.values()):
        all_ok = False
    return {"pass": all_ok, "pairs": pairs,
            "labels": labels or {}, "label_shape_ok": label_check,
            "isolation_paths_distinct": (len(set(ws_values)) == len(ARM_ORDER)
                                         and len(set(pf_values)) == len(ARM_ORDER)),
            "workspace_values": ws_values, "problem_values": pf_values,
            "raw_commands": {a: " ".join(commands[a]) for a in ARM_ORDER},
            "normalized_commands": {a: " ".join(normalize_argv(commands[a]))
                                    for a in ARM_ORDER},
            "note": ("归一化只折叠 --repo-root/--problem-file 的绝对路径值与 --label 的值"
                     "（它们是 §5.5 要求的隔离差异，不是实验变量）；"
                     "其余 token 逐字参与比较 => 『不允许少 token、不允许其它 token 变化』成立")}


def check_argv_unique_variable(argv: list[str], *, arm: str) -> None:
    """拼装**当场**自查：禁用参数不得出现；臂开关必须与定义逐字一致。"""
    flags = {t for t in argv if t.startswith("--")}
    bad = sorted(flags & set(FORBIDDEN_FLAGS))
    if bad:
        raise ArmSpecError(
            f"臂 {arm} 的命令行出现了禁止参数 {bad}（§1.3："
            + "；".join(FORBIDDEN_FLAGS[b] for b in bad) + "）=> W1 不过 => 停批")
    tail = list(argv[len(argv) - len(ARM_EXTRA_ARGS[arm]):]) if ARM_EXTRA_ARGS[arm] else []
    if ARM_EXTRA_ARGS[arm] and tail != ARM_EXTRA_ARGS[arm]:
        raise ArmSpecError(f"臂 {arm} 的臂开关尾巴与定义不符：{tail} != {ARM_EXTRA_ARGS[arm]}")
    if not ARM_EXTRA_ARGS[arm] and any(t in ("--repo-map-default", "--delegate") for t in argv):
        raise ArmSpecError(f"臂 A 禁止出现任何 --repo-map-default / --delegate（§1.1：A 是现状）")


# ---------------------------------------------------------------- run_id / label（§5.5）

def make_run_id(instance_id: str, arm: str, *, stamp: str | None = None) -> str:
    """`p1-<UTCyyyymmddTHHMMSS>-<instance_id>-<A|B|C>`（§5.5 写死的命名）。"""
    if arm not in ARM_ORDER:
        raise ArmSpecError(f"未知臂：{arm!r}")
    ts = stamp or time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    return f"p1-{ts}-{instance_id}-{arm}"


def make_label(instance_id: str, arm: str) -> str:
    """`p1-<instance_id>-<arm>` —— 决定 usage.jsonl / scout 用量行的归属（§2.4）。"""
    if arm not in ARM_ORDER:
        raise ArmSpecError(f"未知臂：{arm!r}")
    return f"p1-{instance_id}-{arm}"


def parse_run_id(run_id: str) -> dict | None:
    """从 run_id 反解 (instance_id, arm)；不是 p1 run 时返回 None。"""
    m = re.match(r"^p1-(\d{8}T\d{6})-(.+)-([ABC])$", run_id or "")
    if not m:
        return None
    return {"stamp": m.group(1), "instance_id": m.group(2), "arm": m.group(3)}


def triple_key(run_id_or_label: str) -> str | None:
    """配对单位：三元组的 key = instance_id（§3.2 实例内交错）。"""
    parsed = parse_run_id(run_id_or_label)
    return parsed["instance_id"] if parsed else None


def arms_arg(spec: str) -> list[str]:
    """解析 `--arms A,B,C`：保序、去重、非法值当场报错（不静默忽略）。"""
    out: list[str] = []
    for tok in (spec or "").split(","):
        a = tok.strip().upper()
        if not a:
            continue
        if a not in ARM_ORDER:
            raise ArmSpecError(f"--arms 里有未知臂 {a!r}（只支持 {ARM_ORDER}）")
        if a not in out:
            out.append(a)
    if not out:
        raise ArmSpecError("--arms 解析后为空（例：--arms A,B,C）")
    return out


# ---------------------------------------------------------------- 计划（§3.2 实例内交错）

def interleaved_plan(instance_ids: list[str], arms: list[str]) -> list[dict]:
    """**实例内三臂交错**的执行计划（§3.2 写死的循环顺序）。

    ```
    for instance in <pilot-12 行序>:
        for arm in [A, B, C]:
            run(instance, arm)
    ```

    返回的每一项是一个「运行槽」：`(instance_seq, arm_seq)` 让它自带交错证据 ——
    调度日志里 `instance_seq` 必然是 `1,1,1,2,2,2,...`，
    而**按臂分批**的错误顺序会长成 `1,2,3,...,1,2,3,...`。
    ⚠️ 顺序一旦开始跑就冻结（§3.2「不得中途换序」）。
    """
    plan: list[dict] = []
    for i, iid in enumerate(instance_ids, start=1):
        for j, arm in enumerate(arms, start=1):
            plan.append({"instance_seq": i, "arm_seq": j,
                         "instance_id": iid, "arm": arm,
                         "label": make_label(iid, arm)})
    return plan


def plan_order_signature(plan: list[dict]) -> str:
    """计划顺序的哈希 —— 冻结性的可核对指纹（§3.2 不得中途换序）。"""
    body = "|".join(f"{p['instance_seq']}.{p['arm']}:{p['instance_id']}" for p in plan)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def group_by_triple(plan: list[dict]) -> dict[str, list[dict]]:
    """按计划顺序切成三元组（保持首次出现的实例顺序）。"""
    out: dict[str, list[dict]] = {}
    for slot in plan:
        out.setdefault(slot["instance_id"], []).append(slot)
    return out


def triple_drift_check(rows: list[dict], limit_s: int = 2 * 3600) -> dict:
    """§3.2 的机器校验：同一三元组三次运行的 started_at 极差 ≤ 2 小时。"""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("instance_id", "?"), []).append(r)
    res = {}
    for iid, grp in by.items():
        stamps = sorted(x for x in (g.get("started_at_epoch") for g in grp) if x)
        spread = round(stamps[-1] - stamps[0], 1) if len(stamps) >= 2 else None
        res[iid] = {"n_runs": len(grp), "spread_s": spread,
                    "cross_drift_pairing": (spread is not None and spread > limit_s)}
    return res


# ---------------------------------------------------------------- §1.2 的 G-K 判据

def k_expected_from_instance(instance: dict) -> dict:
    """G-K：`K_expected(i) = |{ n.split('::')[0] for n in FAIL_TO_PASS }|`。

    组键 = **测试文件路径**（与 pilot-12 的 F2P node id 形状对齐；
    旧实现按 `<Name>Tests::` 类名分组，pilot-12 全批命中不到 ⇒ 回落单组 OTHER）。
    """
    f2p = list(instance.get("FAIL_TO_PASS") or [])
    groups = sorted({str(n).split("::")[0] for n in f2p if str(n).split("::")[0]})
    return {"instance_id": instance.get("instance_id"),
            "f2p_total": len(f2p),
            "k_expected": len(groups),
            "group_keys": groups,
            "rule": "K_expected = |{ n.split('::')[0] for n in FAIL_TO_PASS }|（§1.2）"}


def check_gk(groups_observed: list[str], k_expected: int, index_chars: int,
             worktrees_after_dispatch: int, *, observable: bool = True) -> dict:
    """§1.2 的落盘判据：组数 == K_expected、index_chars ≤ 2000、worktree 只剩主工作区。

    `observable=False`（基线里取不到任何 F2P 形状的失败行）⇒
    `dispatch_grouping_unobservable`，该次运行标 harness_bug（治疗未按设计施加）。
    **不许**改用「随便分 K 组」继续（§1.2 最后一行是门槛，不是免责声明）。
    """
    distinct = sorted(set(groups_observed))
    reasons: list[str] = []
    if not observable:
        reasons.append("dispatch_grouping_unobservable：基线 pytest 输出里取不到任何 "
                       "F2P 形状的失败行 ⇒ 分组不可观测（§1.2）")
    if len(distinct) != k_expected:
        reasons.append(f"不同 group 终态记录数 {len(distinct)} != K_expected {k_expected}")
    if index_chars > 2000:
        reasons.append(f"index_chars {index_chars} > 2000（§1.2 / DEPENDENCY_OUTPUT_MAX_CHARS）")
    if worktrees_after_dispatch != 1:
        reasons.append(f"worktrees_after_dispatch {worktrees_after_dispatch} != 1（P4 无残留）")
    return {"pass": not reasons, "reasons": reasons,
            "k_expected": k_expected, "groups_observed": distinct,
            "verdict_class_on_fail": "harness_bug"}


# ---------------------------------------------------------------- 产物 schema（一行一 (instance, arm)）

#: 每 `(instance_id, arm)` 一行的必备字段（合同第 3 项 + §2 指标 + §5.2 I-10）。
RESULT_ROW_FIELDS: tuple[str, ...] = (
    "instance_id", "arm", "run_id", "label",
    "resolved", "verdict_class",
    "judged",                      # F2P / P2P 明细
    "tokens_parent", "tokens_scout", "tokens_e2e",       # 三 token 口径 ×两成本口径
    "cost_usd_parent", "cost_usd_scout", "cost_usd_e2e",
    "num_turns", "tool_calls_by_name", "tool_calls_total_trace",
    "wall_s", "timed_out",
    "cmd", "cmd_str", "arm_extra_args",
    "workspace", "venv", "artifacts",
    "schema_sha256", "base_commit", "problem_sha256",
    "started_at", "started_at_epoch", "finished_at",
    "notes",
)


def empty_row(*, instance_id: str, arm: str, run_id: str, label: str) -> dict:
    row: dict[str, Any] = {
        "instance_id": instance_id, "arm": arm, "run_id": run_id, "label": label,
        "resolved": False, "verdict_class": "infra_error",
        "arm_extra_args": list(ARM_EXTRA_ARGS[arm]),
        "notes": [], "artifacts": [],
        "tokens_scout": None, "cost_usd_scout": None,
        "scout_present": False,
    }
    return row


def finalize_row(row: dict) -> dict:
    """补齐缺失键（**显式 null / 0，不留「缺字段」**——§2.4 第 3 条）。"""
    for k in RESULT_ROW_FIELDS:
        row.setdefault(k, None)
    if row.get("arm") in ("A", "B") and row.get("tokens_scout") is None:
        # A/B 没有 scout 通道 ⇒ 显式记 0（不是 null、不是缺字段）
        row["tokens_scout"] = {"input_uncached": 0, "cache_read": 0,
                               "cache_creation": 0, "output": 0,
                               "sessions": 0, "source": "该臂无子代理通道"}
        row["cost_usd_scout"] = 0.0
    return row
