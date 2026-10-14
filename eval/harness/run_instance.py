"""单实例判定：造工作区 -> 应用 test_patch -> 应用候选补丁 -> 跑 F2P/P2P -> 判定 -> 证据落盘。

这是一条**命令**而不是一个库（`main()` 走 argparse）；`run_one()` 是给 scheduler 复用的
纯函数入口。两者共用同一条链路 —— 否则「命令行跑出来的结论」与「批量跑出来的结论」
可能不是同一套判据（那是评测里最贵的一类 bug）。

用法：
    python eval/harness/run_instance.py --instance pallets-click__4fd2fea \
        --patch gold            --run-id demo
    python eval/harness/run_instance.py --instance ... --patch-file candidate.diff --label agent-A
    python eval/harness/run_instance.py --instance ... --patch wrong --run-id demo

`--patch {gold,empty,wrong}` 是给三向自校验用的：gold 取实例自己的 `patch` 字段，
empty 是零字节补丁，wrong 取实例冻结的 `wrong_patch`。三者都不经过 agent。
"""

from __future__ import annotations

import argparse
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

from eval.harness import env as E          # noqa: E402
from eval.harness import judge as J        # noqa: E402
from eval.lib import report as R           # noqa: E402

# §8.4 的写死数值
JUDGE_TIMEOUT = 600          # 单实例判定 pytest 墙钟
DEFAULT_RUN_ID = "harness"

INSTANCES_DIR = os.path.join(EVAL_DIR, "instances")


def log(msg):
    """GBK 控制台兜底：编不出来的字符用 '?'，不影响文件读写（UTF-8 一律走 write/read）。"""
    text = f"[run] {msg}"
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", "replace") + b"\n")
        sys.stdout.buffer.flush()


# ---------------------------------------------------------------- 实例装载

def collect_agent_patch(ws: str, art: str, log_name: str = "collect_eol.log"):
    """从工作区采集候选补丁 —— **先归一化工作树行尾，再 diff**（D7 根治）。

    §5.1 规定候选补丁由 harness 从工作区采集（不接受 agent 自贴的 diff）。
    但 agent 的 `apply_patch` 写 CRLF、基线是 LF ⇒ 直接 `git diff` 会输出整文件单 hunk
    （实测 9 行语义改动 → 251,255 字节）。所以采集前先把工作树的行尾归一。

    返回 `(patch_text, eol_stats_before, eol_stats_after, normalize_report)`。
    """
    raw_before = E.git(ws, "diff", "--binary").stdout or ""
    report = J.normalize_worktree_eol(ws, log_path=os.path.join(art, log_name))
    patch = E.git(ws, "diff", "--binary").stdout or ""
    # 第二道防线：补丁正文若自带 CRLF，也归一（幂等）
    patch = J.normalize_eol_patch(patch)
    return patch, J.eol_stats(raw_before), J.eol_stats(patch), report


def load_instance(instance_id: str) -> dict:
    path = os.path.join(INSTANCES_DIR, f"{instance_id}.json")
    if not os.path.exists(path):
        raise SystemExit(f"找不到实例定义：{path}")
    with open(path, encoding="utf-8") as f:
        inst = json.load(f)
    # §1.3：下游读取**必须**先重算 schema_sha256，不一致即拒绝使用。
    # 这条不是形式主义：实例是冻结的，哈希对不上说明文件被改过，用它跑出来的数字
    # 不能与预注册对话。
    got = E.sha256_text(json.dumps({k: v for k, v in inst.items() if k != "schema_sha256"},
                                   sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    if got != inst.get("schema_sha256"):
        raise SystemExit(
            f"实例 {instance_id} 的 schema_sha256 不匹配（期望 {inst.get('schema_sha256')}，"
            f"实算 {got}）=> 文件被改过，拒绝使用（§1.3）。")
    return inst


def deps_from_env_spec(inst: dict):
    """从 `env_spec.install_cmd` 里取 pip 依赖列表 + 推导 import 名。

    harness 不硬编码仓库画像（那是构造期的东西）：依赖与包名都从实例自带的
    `install_cmd` 读出来，实例怎么冻结的，评测就怎么装。

    ⚠ `install_cmd` 在实例里是**单个字符串**（整条命令行），不是 token 列表
    （构造器写成 `[f"{py} -m pip install ... " + " ".join(deps)]`）。
    两种形态都得吃：按 shell 词法切分后再找 `install` 之后的位置。
    踩过一次：只按列表处理 => deps=[] => venv 里没装 pytest => 判定全空被记成 infra_error。
    """
    raw = (inst.get("env_spec") or {}).get("install_cmd") or []
    if isinstance(raw, str):
        parts = [raw]
    else:
        parts = [str(x) for x in raw]
    # 逐段做 shell 风格切分（处理引号），再拼成 token 流
    import shlex
    toks = []
    for seg in parts:
        try:
            toks += shlex.split(seg, posix=False)
        except ValueError:
            toks += seg.split()
    toks = [t.strip('"').strip("'") for t in toks]
    deps = []
    if "install" in toks:
        i = toks.index("install")
        for tok in toks[i + 1:]:
            if tok.startswith("-"):
                continue
            deps.append(tok)
    return deps


def infer_import_name(ws: str):
    """从工作区布局推断被测包的 import 名（`src/<pkg>` 或 `<pkg>/` 或 `<pkg>.py`）。"""
    for base in (os.path.join(ws, "src"), ws):
        if not os.path.isdir(base):
            continue
        cands = []
        for name in sorted(os.listdir(base)):
            full = os.path.join(base, name)
            if os.path.isdir(full) and os.path.exists(os.path.join(full, "__init__.py")):
                cands.append(name)
            elif name.endswith(".py") and name not in ("setup.py", "conftest.py"):
                cands.append(name[:-3])
        # 排除明显不是主包的目录
        blocked = {"test", "tests", "testing", "docs", "doc", "examples", "benchmarks",
                   "scripts", "build", "dist", "__pycache__"}
        cands = [c for c in cands if c.lower() not in blocked and not c.startswith(".")]
        if cands:
            return cands[0]
    return None


# ---------------------------------------------------------------- pytest 运行

def pytest_run(ws, py, art, node_ids, scope, timeout=JUDGE_TIMEOUT):
    """一次独立 pytest 判定运行。

    三个形态要点（与构造器同源，缺一就判错）：
      · `-c <临时 ini>`：仓库自带 ini 的旧选项在 pytest 9 下 INTERNALERROR(exit 3)；
      · `--rootdir <ws>`：否则 `-c` 会把 rootdir 移到 artifacts/，nodeid 丢掉目录前缀；
      · `--json-report`（**和** `--json-report-file` 一起）：只给 file 不启用插件 => 结果全空。
    临时文件全写 `art/`（实例目录下），不进 `repo/`（§9.1）。
    """
    cfg = os.path.join(art, E.INI_NAME)
    E.write_text(cfg, "[pytest]\n")
    cmd = [py, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--timeout=120",
           "-c" + cfg, "-o", "cache_dir=" + os.path.join(E.RUNS_ROOT, "_pcache"),
           "--rootdir=" + ws,
           "--json-report", "--json-report-file=" + os.path.join(art, "verdict.json")]
    cmd += list(node_ids) if node_ids is not None else list(scope or [])
    res = E.sh(cmd, cwd=ws, timeout=timeout)
    results, meta = R.parse_json_report(os.path.join(art, "verdict.json"), ws)
    return {
        "cmd": cmd, "returncode": res.rc, "timed_out": res.timed_out,
        "wall_seconds": res.wall_seconds, "stdout": res.stdout, "stderr": res.stderr,
        "results": results, "meta": meta,
        "report_path": os.path.join(art, "verdict.json"),
    }


def save_run_evidence(art, name, run):
    """落盘**原始证据**：命令行、退出码、超时标记、逐 node 结果、pytest 原文。

    注意 durations 全表不写进日志（上千条会把证据撑得没法读）；完整的逐 node 结果
    就在同目录的 json report 里，需要时直接取。
    """
    meta = {k: v for k, v in (run.get("meta") or {}).items() if k != "durations"}
    body = (
        "$ " + " ".join(run["cmd"]) + "\n"
        f"exit_code={run['returncode']}  timed_out={run['timed_out']}  "
        f"wall_seconds={run['wall_seconds']}\n"
        f"json_report_meta={json.dumps(meta, ensure_ascii=False)}\n"
        f"json_report_file={run.get('report_path')}\n"
        "---- per-node outcome (工作区相对 node id) ----\n"
        + "".join(f"{k}\t{v}\n" for k, v in sorted(run["results"].items()))
        + "---- failed/error nodes ----\n"
        + "".join(f"{k}\t{v}\n" for k, v in sorted(run["results"].items()) if v != "passed")
        + "\n---- pytest stdout ----\n" + run["stdout"]
        + "\n---- pytest stderr ----\n" + run["stderr"] + "\n"
    )
    return E.write_text(os.path.join(art, f"{name}.txt"), body)


# ---------------------------------------------------------------- 主链路

def wrong_patch_plan(inst: dict):
    """§6.3 第 3 向的正确施加方式 => `(patch_text, reverse, chain_gold_first, note)`。

    冻结语义由 `gold_selfcheck.wrong_patch_method` 决定（构造期写进去的）：

      · `reverse_of_gold` => **正文就是 gold**（实测：3 条 tabulate 实例的 `wrong_patch`
        与 `patch` 的 SHA-256 逐字节相同），预注册 §6.3 第 3 向说的是「把 `patch`
        **反向应用**得到…的补丁」。反向应用的前提是它先被应用过 => 必须先叠 gold、
        再 `git apply -R`，两步合起来才是「反向法」。链式施加后工作区回到基线缺陷态。
      · 其它（`unrelated_file_comment` / `wrong_function_hunk`）=> 正文本身就是错补丁，正着施加。

    ⚠ 必须如实报告的一件事：链式反向的**净效果 = 回到基线**，与第 2 向（空补丁）落在
    同一个状态 => 这一向对这类实例**不提供额外的判别力信息**。`gold_check.py` 会把这个
    等价性显式打出来，而不是假装第 3 向独立通过了。这属于**构造侧**的遗留（§6.3 第 3 向
    本意是「缺陷更深一层」，而冻结下来的正文表达不了「更深」），不是 harness 判定错误；
    实例 JSON 已冻结（§9.5），因此不原地改，只在结果里如实标注。
    """
    method = (inst.get("gold_selfcheck") or {}).get("wrong_patch_method")
    text = inst.get("wrong_patch") or ""
    if method == "reverse_of_gold":
        same_as_gold = text.strip() == (inst.get("patch") or "").strip()
        note = ("wrong_patch_method=reverse_of_gold => 先叠 gold 再 `git apply -R`（§6.3 第 3 向）")
        if same_as_gold:
            note += "；⚠ 冻结的 wrong_patch 与 gold **逐字节相同**，链式反向的净效果 = 基线" \
                    "（与第 2 向同状态），该向不提供额外判别力 —— 构造侧遗留，如实标注"
        return text, True, True, note
    return text, False, False, f"wrong_patch_method={method} => 正着施加错补丁正文"


def run_one(instance_id: str, patch_text: str | None, run_id: str = DEFAULT_RUN_ID,
            label: str = "local", patch_kind: str | None = None,
            keep_workspace: bool = False, judge_timeout: int = JUDGE_TIMEOUT,
            timeout_probe: bool = False, apply_reverse: bool = False,
            chain_gold_first: bool = False) -> dict:
    """判定一条实例，返回结果 dict（同时把证据落盘到 `<instance>/artifacts/`）。

    参数：
      patch_text      候选补丁正文（None/空串 = 空补丁，走 empty_patch 分支）
      patch_kind      仅用于**证据标注**（gold/empty/wrong/agent），不参与判定
      timeout_probe   测试用：把判定超时压到很小，用来实证「超时优先于退出码」
      apply_reverse   施加时用 `git apply -R`（仅 §6.3 第 3 向「反向法」用，见下）

    ⚠ `wrong_patch` 的两种冻结语义（§6.3 第 3 向）：
      构造器在建「反向法」的 wrong_patch 时，按预注册写的是「把 patch 反向应用得到的补丁」。
      但当时实际落盘的是**gold 正文本身** + `gold_selfcheck.wrong_patch_method ==
      "reverse_of_gold"` 这个标记 —— 即「怎么施加」编码在元数据里，而不是补丁正文里。
      实例 JSON 一经纳入不得原地改（§9.5），所以 harness 必须**按冻结的语义**施加：
      见到 `reverse_of_gold` 就用 `git apply -R`。
      不这么做的后果是实测过的：顺着施加 gold 正文 => 缺陷被修好 => 第 3 向被判 resolved =>
      三向 FAIL（3 条 tabulate 实例全中）。那是「读错了冻结语义」，不是判别力不足。
    """
    inst = load_instance(instance_id)
    iid = inst["instance_id"]
    art = E.art_dir(run_id, iid)
    os.makedirs(art, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    row = {
        "instance_id": iid,
        "run_id": run_id,
        "label": label,
        "patch_kind": patch_kind,
        "started_at": started,
        "verdict_class": E.InfraError.__name__,   # 占位，异常路径会覆盖它
        "resolved": False,
        "schema_sha256": inst.get("schema_sha256"),
        "artifacts": [],
        "f2p_total": len(inst.get("FAIL_TO_PASS") or []),
        "p2p_total": len(inst.get("PASS_TO_PASS") or []),
        "touched_tests": False,
        "test_side_paths": [],
        "stripped_test_patch_sha256": None,
        "notes": [],
    }

    ws = None
    try:
        # ---- 1) 独立工作区（§8.1：目录副本，不是 worktree）
        ws = E.fresh_workspace(inst, run_id, log_path=os.path.join(art, "workspace.log"))
        inst["_ws"] = ws
        row["workspace"] = ws
        row["artifacts"].append(os.path.join(art, "workspace.log"))

        # ---- 2) 独立 venv（§8.2）—— **按 (实例, run) 隔离**（D1 修复）。
        # venv 曾按 `<repo>-<base_commit[:8]>` 共享 ⇒ 两个 run 跑同一实例会抢同一个
        # editable 挂载位，触发静默误判。现在每个 run 有自己的 venv，
        # 共享可变状态被消掉，因此**不需要**任何锁/让位逻辑（试过，会对称死锁）。
        deps = deps_from_env_spec(inst)
        pkg = infer_import_name(ws)
        if not pkg:
            raise E.InfraError("无法从工作区布局推断被测包的 import 名")
        row["import_name"] = pkg
        row["install_deps"] = deps

        py = E.ensure_venv(inst, os.path.join(art, "venv.log"), deps, pkg, run_id=run_id)
        row["venv"] = E.venv_dir(inst, run_id)
        row["venv_python"] = py
        row["mount_pth"] = os.path.basename(E.editable_pth_path(inst, run_id))
        row["artifacts"].append(os.path.join(art, "venv.log"))

        # ---- 3) 施加 test_patch（判定的起点状态 = 基线 + test_patch）
        tp_path = os.path.join(art, "_test_patch.diff")
        E.write_text(tp_path, inst["test_patch"])
        r = apply(ws, inst["test_patch"], art)
        if r.rc != 0:
            raise E.InfraError(f"test_patch 无法应用（实例自身的问题）：{r.stderr[-300:]}")

        # ---- 4) 候选补丁：先**归一化行尾**（D7），再剥测试侧（§5.3）
        raw_patch = patch_text or ""
        # ⚠ D7：agent 的写文件工具产 CRLF，基线是 LF ⇒ `git diff` 会把整份文件报成一个
        # hunk（9 行语义改动能变成 251 KB 的补丁，放大 ~2.7 万倍）。归一化**只动行尾**，
        # 不动代码内容，判定语义不变；但补丁体积、hunk 数与「能否干净 apply」都恢复正常。
        norm_patch = J.normalize_eol_patch(raw_patch)
        row["eol_before"] = J.eol_stats(raw_patch)
        row["eol_after"] = J.eol_stats(norm_patch)
        row["eol_normalized"] = (norm_patch != raw_patch)
        if row["eol_normalized"]:
            row["notes"].append(
                f"候选补丁行尾已归一化（D7）：{row['eol_before']['bytes']} -> "
                f"{row['eol_after']['bytes']} 字节，hunk {row['eol_before']['hunks']} -> "
                f"{row['eol_after']['hunks']}，CRLF 行 {row['eol_before']['crlf_lines']} -> "
                f"{row['eol_after']['crlf_lines']}")
        kept, test_side, dropped_paths, touched_tests, test_paths = J.stripped_patch(norm_patch)
        row["touched_tests"] = touched_tests
        row["test_side_paths"] = test_paths
        row["dropped_non_py_paths"] = dropped_paths
        if touched_tests:
            # 「试图修改测试」**单列统计**（§5.3）——这是 agent 行为的一列，不是判定输入。
            row["stripped_test_patch_sha256"] = E.sha256_text(test_side)
            E.write_text(os.path.join(art, "candidate_test_side_stripped.diff"), test_side)
            row["artifacts"].append(os.path.join(art, "candidate_test_side_stripped.diff"))
            row["notes"].append(
                f"候选补丁改动了 {len(test_paths)} 个测试侧文件，已按 §5.3 剥离后再判定")
        E.write_text(os.path.join(art, "candidate_patch_raw.diff"), raw_patch)
        E.write_text(os.path.join(art, "candidate_patch_judged.diff"), kept)
        row["artifacts"] += [os.path.join(art, "candidate_patch_raw.diff"),
                             os.path.join(art, "candidate_patch_judged.diff")]
        row["candidate_patch_sha256"] = E.sha256_text(raw_patch)
        row["normalized_patch_sha256"] = E.sha256_text(norm_patch)
        row["judged_patch_sha256"] = E.sha256_text(kept)
        row["judged_patch_paths"] = J.patch_touched_paths(kept)
        row["apply_reverse"] = bool(apply_reverse)
        row["chain_gold_first"] = bool(chain_gold_first)

        if not kept.strip():
            # §5.4：agent 什么都没改（含「只改了测试被剥光」）=> empty_patch
            row["verdict_class"] = J.EMPTY_PATCH
            row["resolved"] = False
            row["notes"].append("可判定补丁为空（零字节，或仅测试侧改动被剥离）=> empty_patch"
                                "（§5.3/§5.4，实质失败，计入分母）")
            return finish(row, inst, ws, keep_workspace)

        if chain_gold_first:
            # §6.3 第 3 向「反向法」：先叠 gold，再反向它（净效果 = 回到基线缺陷态）
            g = apply(ws, inst["patch"], art)
            row["chain_gold_apply_exit_code"] = g.rc
            if g.rc != 0:
                row["verdict_class"] = J.INFRA_ERROR
                row["resolved"] = False
                row["notes"].append(f"反向法链式的第一步（叠 gold）失败，链路故障：{g.stderr[-200:]}")
                return finish(row, inst, ws, keep_workspace)

        ap = apply(ws, kept, art, reverse=apply_reverse)
        row["patch_apply_exit_code"] = ap.rc
        if ap.rc != 0:
            # §5.3：补丁无法应用 => not_resolved，verdict_class=patch_conflict（计入分母）
            row["verdict_class"] = J.PATCH_CONFLICT
            row["resolved"] = False
            row["notes"].append(f"候选补丁无法应用 => patch_conflict（§5.3）：{ap.stderr[-200:]}")
            save_run_evidence(art, "patch_apply_failure", {
                "cmd": ap.cmd, "returncode": ap.rc, "timed_out": ap.timed_out,
                "wall_seconds": ap.wall_seconds, "results": {}, "meta": {},
                "stdout": ap.stdout, "stderr": ap.stderr, "report_path": None})
            row["artifacts"].append(os.path.join(art, "patch_apply_failure.txt"))
            return finish(row, inst, ws, keep_workspace)

        # ---- 5) 独立 pytest 判定（唯一证据来源，§5.2）
        # ⚠⚠ **判定前的隔离再核对**（D3，t4 报的静默误判）。
        # `ensure_venv` 的自检发生在挂载之后、**判定之前的一段时间**；并发场景下另一个 run
        # 完全可以在那次自检通过之后把挂载点改掉 ⇒ 判定时 `import <pkg>` 已指向别人的工作区，
        # 而 pytest 照常跑完、给出一个**看起来正常**的 verdict（t4 round 4 / t8 repro round 4）。
        # ⇒ 在真正跑 pytest 之前，用**当前磁盘状态**再核一次；不符即 infra_error，
        # 绝不允许「判定用的不是自己的工作区」这种结果流出去。
        try:
            row["import_probe_before_judge"] = E.assert_pkg_from_workspace(py, ws, pkg)
        except E.InfraError as exc:
            row["verdict_class"] = J.INFRA_ERROR
            row["resolved"] = False
            row["notes"].append(f"判定前隔离失败（D3）：{exc}")
            row["artifacts"].append(_write_failure(art, "isolation_failure.txt", exc))
            return finish(row, inst, ws, keep_workspace)

        sel = list(inst["FAIL_TO_PASS"]) + list(inst["PASS_TO_PASS"])
        if timeout_probe:
            # ⚠ 必须压到**低于 pytest 启动耗时**（实测本机一次判定约 0.7s）——
            # 只压到 1s 的话，判定会在超时前跑完，于是「什么都没验证到」却显示通过
            # （这是本脚本第二次踩到的坑）。0.2s 足够短，且不影响任何判定口径。
            judge_timeout = min(judge_timeout, 0.2)
            row["notes"].append(f"timeout_probe：判定超时被人为压到 {judge_timeout}s"
                                "（机制验证用，不参与任何评测结论）")
        run = pytest_run(ws, py, art, sel, inst.get("collection_scope"),
                         timeout=judge_timeout)
        save_run_evidence(art, "verdict_run", run)
        row["artifacts"].append(os.path.join(art, "verdict_run.txt"))
        row["pytest"] = {"cmd": run["cmd"], "exit_code": run["returncode"],
                         "timed_out": run["timed_out"], "wall_seconds": run["wall_seconds"]}
        row["exit_code"] = run["returncode"]
        row["timed_out"] = run["timed_out"]

        # ⚠ §8.4：**先查超时标记，再看退出码**。顺序颠倒会把 timeout 记成 not_resolved。
        run_class = J.classify_run(run)
        judged = J.judge(inst["FAIL_TO_PASS"], inst["PASS_TO_PASS"], run["results"])
        row["judged"] = judged
        row["verdict_class"], row["resolved"] = J.verdict_from(run_class, judged)

        # ⚠ 判定**之后**再核一次隔离：整个 pytest 期间挂载点没被别人改过，才能说
        # 「这次的结果属于本工作区」。若两次核对不一致 ⇒ 判定期间被并发改动 ⇒ harness_bug
        # （§9 不变量被破坏，该批次整体作废重跑），不允许当成 agent 结果。
        try:
            row["import_probe_after_judge"] = E.assert_pkg_from_workspace(py, ws, pkg)
        except E.InfraError as exc:
            row["verdict_class"] = J.INFRA_ERROR
            row["resolved"] = False
            row["notes"].append(
                f"判定**期间**隔离被破坏（挂载点被并发改动）：{exc}；"
                "本次判定结果不可信，按 infra_error 处理（不计入分母）")
            row["artifacts"].append(_write_failure(art, "isolation_broken_during_judge.txt", exc))
            return finish(row, inst, ws, keep_workspace)
        if row["import_probe_before_judge"] != row["import_probe_after_judge"]:
            row["verdict_class"] = J.HARNESS_BUG
            row["resolved"] = False
            row["notes"].append(
                "判定前后导入路径发生变化 ⇒ 判定期间隔离被破坏（§9 不变量），按 harness_bug 处理")

        if row["verdict_class"] == J.TIMEOUT:
            row["notes"].append(
                "判定超时（timed_out 标记先于退出码判定，§8.4）=> verdict_class=timeout，"
                "不计入 resolved 分母、不计作 agent 失败")
        elif row["verdict_class"] == J.INFRA_ERROR:
            row["notes"].append(
                f"判定链路故障（exit={run['returncode']}, errors={run['meta'].get('errors')}, "
                f"collection_errors={run['meta'].get('collection_errors')}）=> infra_error")
        # 逐 node 明细落盘，便于人工复核
        E.write_text(os.path.join(art, "judged.json"),
                     json.dumps(judged, ensure_ascii=False, indent=2) + "\n")
        row["artifacts"].append(os.path.join(art, "judged.json"))
        return finish(row, inst, ws, keep_workspace)

    except E.InfraError as exc:
        row["verdict_class"] = J.INFRA_ERROR
        row["resolved"] = False
        row["notes"].append(f"基础设施错误：{exc}")
        row["artifacts"].append(_write_failure(art, "infra_error.txt", exc))
        return finish(row, inst, ws, keep_workspace)
    except Exception as exc:  # noqa: BLE001
        row["verdict_class"] = J.HARNESS_BUG
        row["resolved"] = False
        row["notes"].append(f"harness 自身异常：{type(exc).__name__}: {exc}")
        row["artifacts"].append(_write_failure(art, "harness_bug.txt", exc))
        return finish(row, inst, ws, keep_workspace)


def _write_failure(art, name, exc):
    import traceback
    return E.write_text(os.path.join(art, name),
                        f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}\n")


def apply(ws, patch_text, art, reverse=False):
    """在 `ws` 应用补丁。临时 diff 写 `art/`（§9.1：repo/ 内不留判定用临时文件）。"""
    if not (patch_text or "").strip():
        return E.Res(["git", "apply", "(empty)"], 0, "", "", 0.0, False)
    tmp = os.path.join(art, "_apply.diff")
    E.write_text(tmp, patch_text)
    cmd = E.GIT_BASE + ["-C", ws, "apply", "--whitespace=nowarn"]
    if reverse:
        cmd.append("-R")
    cmd.append(tmp.replace("\\", "/"))
    return E.sh(cmd, cwd=ws, timeout=300)


def finish(row, inst, ws, keep_workspace):
    """收尾：清洁检查（§9.1）、写 state.json、清理工作区。"""
    row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if ws and os.path.isdir(ws):
        row["workspace_garbage_free"] = E.garbage_free(ws)
        if not row["workspace_garbage_free"]:
            # §9.1：跑后 repo/ 里留了未跟踪垃圾 => harness_bug（该实例重跑）
            row["notes"].append("§9.1 跑后工作区留有未跟踪文件 => harness_bug")
            row["verdict_class"] = J.HARNESS_BUG
            row["resolved"] = False
        if not keep_workspace:
            E.clean_workspace(row["run_id"], row["instance_id"], keep_artifacts=True)
            row["workspace_cleaned"] = True
            # ⚠ workspace 字段在清理后仍保留原路径，供人复核；但明确标注已删
            row["workspace_removed"] = True
    art = E.art_dir(row["run_id"], row["instance_id"])
    # ⚠ `stage` 必须写进 **row 本身**（不只是 state.json）：scheduler 的断点续跑是读
    # `state.jsonl` 判「这条做完了没」，而 state.jsonl 里存的就是这个 row。
    # 只在 state.json 里放 stage的话，续跑永远匹配不上 ⇒ 已完成实例被重跑
    # （实测踩过：--resume 的 skipped_completed 恒为 0）。
    row["stage"] = _stage_of(row["verdict_class"])
    E.write_text(os.path.join(art, "result.json"),
                 json.dumps(row, ensure_ascii=False, indent=2) + "\n")
    state = {
        "instance_id": row["instance_id"],
        "stage": row["stage"],
        "verdict_class": row["verdict_class"],
        "resolved": row["resolved"],
        "attempts": 1,
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "workspace": row.get("workspace"),
        "venv": row.get("venv"),
        "artifacts": row.get("artifacts", []),
        "schema_sha256": row.get("schema_sha256"),
    }
    E.write_text(os.path.join(E.instance_dir(row["run_id"], row["instance_id"]), "state.json"),
                 json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return row


def _stage_of(verdict_class):
    if verdict_class in (J.TIMEOUT, J.INFRA_ERROR):
        return "failed_infra"
    return "judged"


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="单实例判定（预注册 §5/§8）")
    ap.add_argument("--instance", required=True, help="instance_id（见 eval/instances/*.json）")
    ap.add_argument("--patch", choices=("gold", "empty", "wrong"),
                    help="用实例里冻结的补丁：gold=patch / empty=零字节 / wrong=wrong_patch")
    ap.add_argument("--patch-file", help="候选补丁文件（agent 产物）")
    ap.add_argument("--patch-inline", help="候选补丁内联文本（调试用）")
    ap.add_argument("--run-id", default=DEFAULT_RUN_ID, help="批次标识（决定 eval-runs/<run_id>/）")
    ap.add_argument("--label", default="local", help="本次运行的标签")
    ap.add_argument("--keep-workspace", action="store_true", help="判定后不删工作区（调试）")
    ap.add_argument("--judge-timeout", type=int, default=JUDGE_TIMEOUT)
    ap.add_argument("--timeout-probe", action="store_true",
                    help="把判定超时压到 0.2s，用于实证「超时先于退出码」"
                         "（必须低于 pytest 启动耗时，否则判定会在超时前跑完、什么都验证不到）")
    args = ap.parse_args(argv)

    inst = load_instance(args.instance)
    apply_reverse = False
    chain_gold_first = False
    if args.patch:
        if args.patch == "wrong":
            patch_text, apply_reverse, chain_gold_first, note = wrong_patch_plan(inst)
        else:
            patch_text = {"gold": inst["patch"], "empty": ""}[args.patch]
            note = None
        kind = args.patch
    elif args.patch_file:
        with open(args.patch_file, encoding="utf-8") as f:
            patch_text = f.read()
        kind = "file:" + os.path.basename(args.patch_file)
        note = None
    elif args.patch_inline is not None:
        patch_text = args.patch_inline
        kind = "inline"
        note = None
    else:
        raise SystemExit("必须给 --patch {gold,empty,wrong} 或 --patch-file 或 --patch-inline")
    if note:
        log(f"wrong_patch 施加方式：{note}")

    row = run_one(args.instance, patch_text, run_id=args.run_id, label=args.label,
                  patch_kind=kind, keep_workspace=args.keep_workspace,
                  judge_timeout=args.judge_timeout, timeout_probe=args.timeout_probe,
                  apply_reverse=apply_reverse, chain_gold_first=chain_gold_first)
    log(f"{row['instance_id']}  kind={kind}  verdict={row['verdict_class']}  "
        f"resolved={row['resolved']}")
    if row.get("judged"):
        j = row["judged"]
        log(f"  F2P {j['f2p_total'] - len(j['f2p_not_passed'])}/{j['f2p_total']}  "
            f"P2P {j['p2p_total'] - len(j['p2p_not_passed'])}/{j['p2p_total']}")
    for n in row.get("notes", []):
        log(f"  note: {n}")
    log(f"  证据目录：{E.art_dir(args.run_id, row['instance_id'])}")
    # 退出码：resolved 才 0；其余非 0 —— 但**分类**看 verdict_class，不看退出码
    return 0 if row["resolved"] else 1


if __name__ == "__main__":
    sys.exit(main())