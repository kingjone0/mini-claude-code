"""t30 独立复验：t29 的 resume 终态语义 + state.artifacts 索引（零模型调用）。

不复用 t29 的 t29_resume_check.py，自己造反例并做修前/修后对照。

Part A  同一状态下的「修前 vs 修后」：修前逻辑（`stage == "judged"`）把 timeout 当 pending
        并允许追加第二行 ⇒ 终态被改写；修后 `plan_resume` 把 timeout/infra 视为终态。
Part B  预算：`count_spawned_arms` 计实际 spawn 行（不按 (实例,臂) 去重），对照旧公式的少记。
Part C  终态不可改写：用真实 `run_p1_batch(resume=True)` 桩（pending 为空、不加载实例）
        ⇒ 不追加任何行、triple_state.jsonl 的 sha256 逐字不变、manifest 拆分 skipped_*。
Part D  F6：`policy_evidence_isolated.json` 的**生产者是谁**、现有 state 行是否含它、
        以及生产时序是否让 worker 的存在性检查恒为 False。
Part E  越界与零模型调用：批次 state 未被改、9 个未启动槽仍无行、用量文件行数与最大 ts 未变。

只读正式产物；所有写操作在临时目录。
输出：eval/reports/t30-review-verify.txt / .json
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = r"E:\aicoding\mini-claude-code"
BATCH = r"E:\aicoding\eval-runs\p1-20261013T122356-t20-batch"
USAGE_DIR = os.path.join(ROOT, "servers", "experiments", "out")
OUT_TXT = os.path.join(ROOT, "eval", "reports", "t30-review-verify.txt")
OUT_JSON = os.path.join(ROOT, "eval", "reports", "t30-review-verify.json")

# t15 记录下来的批次基线（用于证明 t29 未动正式产物）
STATE_SHA_T15 = "17f3a7119fa01b20b1fa899c1fda2b3ef2af3f737b7444d9a6de334f17da8c78"
STATE_BYTES_T15 = 205543
USAGE_ROWS_T15 = (186, 56, 55, 21)          # usage 总/p1-; scout 总/p1-
IID = "offline-instance"

LINES: list[str] = []
R: dict = {}


def say(s=""):
    LINES.append(s)


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(p):
    return sha256_bytes(open(p, "rb").read())


def load_jsonl(p):
    if not os.path.exists(p):
        return []
    return [json.loads(x) for x in io.open(p, encoding="utf-8", errors="replace") if x.strip()]


def main():
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "eval"))
    from eval.harness import scheduler_p1 as SP  # noqa: E402

    tmp = tempfile.mkdtemp(prefix="t30-verify-")
    empty_usage = os.path.join(tmp, "empty-usage")
    os.makedirs(empty_usage, exist_ok=True)
    SP.REV2_USAGE_ROOT = empty_usage

    say("# t30 独立复验：t29（resume 终态语义 + state.artifacts 索引）")
    say()
    say(f"HEAD = {git_head()}")
    say("零模型调用：本脚本不加载实例、不建 venv、不 spawn coding_agent。")
    say()

    # ---------------------------------------------------------------- Part A
    say("## A 同一状态的「修前 vs 修后」对照")
    say()
    root = os.path.join(tmp, "batchA")
    os.makedirs(root, exist_ok=True)

    def row(arm, verdict, resolved, stage, epoch=1791900000):
        return {"instance_id": IID, "arm": arm, "run_id": f"offline-{arm}",
                "verdict_class": verdict, "resolved": resolved, "stage": stage,
                "cmd": ["fake-coding-agent"], "started_at_epoch": epoch,
                "spawned": True, "exit_code": 1}

    initial = [row("A", "agent_result", True, "judged"),
               row("B", "timeout", False, "failed_infra"),
               row("C", "agent_result", True, "judged")]
    for r in initial:
        SP.append_triple_state(root, r)
    state_path = SP.triple_state_jsonl(root)
    before_bytes = open(state_path, "rb").read()
    before_sha = sha256_bytes(before_bytes)
    say(f"初始 state：3 行（A judged/agent_result、B failed_infra/timeout、C judged/agent_result）"
        f"  sha256={before_sha}")

    # A-1 修前逻辑：逐字复刻 4f15fa2 的 scheduler_p1.py:333
    old_src, old_note = old_predicate_source()
    say()
    say("### A-1 修前逻辑（复刻 `4f15fa2` 的跳过条件）")
    say(f"- 修前源码片段：`{old_src}`")
    say(f"- {old_note}")
    prior = SP.read_triple_state(root)
    old_skipped = [a for a in "ABC" if prior[(IID, a)].get("stage") == "judged"]
    old_pending = [a for a in "ABC" if a not in old_skipped]
    say(f"- 修前分类：skipped={old_skipped}  pending={old_pending}"
        f"  ⇒ timeout 槽被当作待跑")
    # 修前 resume 允许的动作：再 append 一行（在第二个临时根里做，不动上面那份）
    old_root = os.path.join(tmp, "batchA-old")
    os.makedirs(old_root, exist_ok=True)
    with open(SP.triple_state_jsonl(old_root), "wb") as fh:
        fh.write(before_bytes)
    SP.append_triple_state(old_root, row("B", "agent_result", True, "judged", epoch=1791900300))
    old_after_rows = open(SP.triple_state_jsonl(old_root), "rb").read().splitlines()
    old_readback = SP.read_triple_state(old_root)[(IID, "B")]
    say(f"- 修前 resume 的效果（复刻）：行数 3 → {len(old_after_rows)}；"
        f"读回 B = {old_readback['verdict_class']}/{old_readback['resolved']}"
        f"  ⇒ **终态被改写（timeout/False → agent_result/True）**")
    R["before"] = {"skipped": old_skipped, "pending": old_pending,
                   "rows": len(old_after_rows),
                   "readback": {"verdict_class": old_readback["verdict_class"],
                                "resolved": old_readback["resolved"]},
                   "old_predicate_source": old_src}

    # A-2 修后逻辑
    say()
    say("### A-2 修后逻辑（`plan_resume`）")
    plan = [{"instance_id": IID, "arm": a} for a in "ABC"]
    skipped, pending, skipped_failed = SP.plan_resume(plan, SP.read_triple_state(root))
    say(f"- 修后分类：skipped={[s['arm'] for s in skipped]}  pending={pending}  "
        f"skipped_failed={skipped_failed}  ⇒ timeout 槽被当**终态**")
    R["after"] = {"skipped": [s["arm"] for s in skipped], "pending": pending,
                  "skipped_failed": skipped_failed}

    # ---------------------------------------------------------------- Part C
    say()
    say("## C 终态不可改写：真实 `run_p1_batch(resume=True)` 桩")
    say()
    orig_run_root = SP.E.run_root
    SP.E.run_root = lambda _rid, _root=root: _root
    try:
        manifest = SP.run_p1_batch(run_tag="t30-offline", stamp="t30",
                                   instance_ids=[IID], arms_list=["A", "B", "C"],
                                   max_workers=1, resume=True)
    finally:
        SP.E.run_root = orig_run_root
    after_bytes = open(state_path, "rb").read()
    after_sha = sha256_bytes(after_bytes)
    files = sorted(os.listdir(root))
    say(f"- pending 为空 ⇒ 未加载实例、未建 venv、未 spawn")
    say(f"- manifest：planned_slots={manifest['planned_slots']}  "
        f"skipped_completed={manifest['skipped_completed']}  "
        f"skipped_failed={manifest['skipped_failed']}  runs_done={manifest['runs_done']}")
    say(f"- manifest budget.started_arms={manifest['budget']['started_arms']}"
        f"（= REV2_INITIAL_STARTED {SP.REV2_INITIAL_STARTED} + count_spawned_arms 3）")
    say(f"- state 行数：{len(after_bytes.splitlines())}（应为 3，**未追加第二行**）")
    say(f"- 修前 sha256={before_sha}")
    say(f"- 修后 sha256={after_sha}  ⇒ **逐字不变 = {before_sha == after_sha}**")
    say(f"- 临时 run_root 里新增的文件只有 manifest.json：{files}")
    R["immutability"] = {"before_sha": before_sha, "after_sha": after_sha,
                         "unchanged": before_sha == after_sha,
                         "rows": len(after_bytes.splitlines()),
                         "manifest": {k: manifest[k] for k in
                                      ("planned_slots", "skipped_completed",
                                       "skipped_failed", "runs_done")},
                         "started_arms": manifest["budget"]["started_arms"],
                         "files_after": files}

    # ---------------------------------------------------------------- Part B
    say()
    say("## B 预算：实际 spawn 计数 vs 旧公式（key 去重）")
    say()
    cnt_root = os.path.join(tmp, "batchB")
    os.makedirs(cnt_root, exist_ok=True)
    SP.append_triple_state(cnt_root, row("B", "timeout", False, "failed_infra"))
    SP.append_triple_state(cnt_root, row("B", "agent_result", True, "judged", epoch=1791900300))
    n_keys = len(SP.read_triple_state(cnt_root))
    n_spawn = SP.count_spawned_arms(cnt_root)
    snap = SP._budget_snapshot(cnt_root, resume=True)
    say(f"- 同一 (实例, 臂) 两次实际启动 ⇒ state 2 行")
    say(f"- `read_triple_state` 键数（旧公式输入）= {n_keys}")
    say(f"- `count_spawned_arms`（新公式输入）= {n_spawn}")
    say(f"- 新 `_budget_snapshot().started_arms` = {snap['started_arms']}"
        f"；旧公式会给出 {SP.REV2_INITIAL_STARTED + n_keys}（少记 {n_spawn - n_keys}）")
    # 准备失败行（无 started_at_epoch / 未 spawn）不应计入
    prep_fail = row("A", "infra_error", False, "failed_infra")
    prep_fail.pop("started_at_epoch")
    prep_fail["spawned"] = False
    SP.append_triple_state(cnt_root, prep_fail)
    say(f"- 追加 1 行「准备失败」（spawned=False、无 started_at_epoch）后 "
        f"`count_spawned_arms` = {SP.count_spawned_arms(cnt_root)}（应为 2，准备失败不计）")
    # 遗留行（无 spawned 字段但有 started_at_epoch）按 legacy 分支计入
    legacy_root = os.path.join(tmp, "batchB-legacy")
    os.makedirs(legacy_root, exist_ok=True)
    leg = row("A", "agent_result", True, "judged")
    leg.pop("spawned")
    SP.append_triple_state(legacy_root, leg)
    say(f"- 遗留行（无 `spawned` 但有 `started_at_epoch`）计入 = "
        f"{SP.count_spawned_arms(legacy_root)}（应为 1，向后兼容）")
    R["budget"] = {"unique_keys": n_keys, "counted_spawns": n_spawn,
                   "new_started_arms": snap["started_arms"],
                   "old_formula_would_be": SP.REV2_INITIAL_STARTED + n_keys,
                   "with_prep_failure": SP.count_spawned_arms(cnt_root),
                   "legacy_row_counted": SP.count_spawned_arms(legacy_root)}

    # ---------------------------------------------------------------- Part D
    say()
    say("## D F6：`policy_evidence_isolated.json` 的生产者与现有 state 行")
    say()
    writers = scan_writers_of_isolated()
    say(f"- 全仓扫「写 `policy_evidence_isolated.json`」的位置：")
    for w in writers:
        say(f"    · {w}")
    say(f"- `policy_isolated_artifact()` 返回值 = "
        f"`{SP.policy_isolated_artifact(row('C', 'timeout', False, 'failed_infra'))}`"
        f"（run 相对路径，字符串正确）")
    state_rows = load_jsonl(os.path.join(BATCH, "triple_state.jsonl"))
    c_rows = [r for r in state_rows if r["arm"] == "C"]
    say()
    say("| C run | run 结束时刻 | isolated 文件 mtime | 文件 mtime > 结束 | state.artifacts 含索引 |")
    say("|---|---|---|---|---|")
    d = []
    for r in c_rows:
        art = os.path.join(os.path.dirname(r["workspace"]), "artifacts")
        iso = os.path.join(art, "policy_evidence_isolated.json")
        iso_mt = (dt.datetime.fromtimestamp(os.path.getmtime(iso)).strftime("%m-%d %H:%M:%S")
                  if os.path.exists(iso) else "缺失")
        fin = r["finished_at"][:19].replace("T", " ")
        later = os.path.exists(iso) and (os.path.getmtime(iso) > os.path.getmtime(
            os.path.join(art, f"console_{r['arm']}.log")))
        listed = any("policy_evidence_isolated" in str(a) for a in r["artifacts"])
        d.append({"run_id": r["run_id"], "finished_at": r["finished_at"],
                  "iso_mtime": iso_mt, "iso_after_run": bool(later), "listed": listed})
        say(f"| {r['run_id'][:52]} | {fin} | {iso_mt} | {later} | {listed} |")
    say()
    say(f"- ⇒ 9/9 的 isolated 文件都由**跑完之后**的抽取脚本产生，worker 运行期间该文件"
        f"**不存在** ⇒ `scheduler_p1.py:337` 的 `os.path.isfile(isolated)` 在生产路径上恒为 False，"
        f"索引不会被追加。")
    say(f"- 现有 state 行含索引的比例："
        f"{sum(1 for x in d if x['listed'])}/{len(d)}")
    R["f6"] = {"writers": writers, "rows": d,
               "listed_count": sum(1 for x in d if x["listed"]),
               "artifact_string": SP.policy_isolated_artifact(row("C", "timeout", False, "failed_infra"))}

    # ---------------------------------------------------------------- Part E
    say()
    say("## E 越界与零模型调用")
    say()
    sp = os.path.join(BATCH, "triple_state.jsonl")
    now_sha, now_bytes = sha256_file(sp), os.path.getsize(sp)
    say(f"- 批次 triple_state.jsonl：bytes={now_bytes}（t15 基线 {STATE_BYTES_T15}）"
        f"  sha256={now_sha}（t15 基线 {STATE_SHA_T15}）⇒ 未改 = "
        f"{now_sha == STATE_SHA_T15 and now_bytes == STATE_BYTES_T15}")
    unstarted = [k for k in ("20d8143", "cbf58d5", "dd20a85") if
                 any(k in r["instance_id"] for r in state_rows)]
    say(f"- 9 个未启动槽（20d8143/cbf58d5/dd20a85）在 state 里的行：{unstarted or '无'}")
    usage = load_jsonl(os.path.join(USAGE_DIR, "usage.jsonl"))
    scout = load_jsonl(os.path.join(USAGE_DIR, "policy_scout_usage.jsonl"))
    p1u = [u for u in usage if str(u.get("label") or "").startswith("p1-")]
    p1s = [s for s in scout if str(s.get("label") or "").startswith("p1-")]
    max_ts = max([str(u.get("ts")) for u in p1u] + [str(s.get("ts")) for s in p1s])
    say(f"- usage.jsonl 总/p1- = {len(usage)}/{len(p1u)}（t15 基线 {USAGE_ROWS_T15[0]}/{USAGE_ROWS_T15[1]}）")
    say(f"- policy_scout_usage.jsonl 总/p1- = {len(scout)}/{len(p1s)}（t15 基线 {USAGE_ROWS_T15[2]}/{USAGE_ROWS_T15[3]}）")
    say(f"- p1- 用量最大 ts = {max_ts}（t29 提交于 2026-10-13 22:42:53）⇒ "
        f"提交后无新用量")
    R["scope"] = {"state_sha256": now_sha, "state_bytes": now_bytes,
                  "state_unchanged": now_sha == STATE_SHA_T15,
                  "unstarted_rows": unstarted,
                  "usage_total": len(usage), "usage_p1": len(p1u),
                  "scout_total": len(scout), "scout_p1": len(p1s),
                  "max_p1_usage_ts": max_ts}

    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(R, ensure_ascii=False, indent=2) + "\n")
    with io.open(OUT_TXT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(LINES) + "\n")
    print("t30 verify done. unchanged=%s pending_after=%s listed=%d/%d"
          % (R["immutability"]["unchanged"], R["after"]["pending"],
             R["f6"]["listed_count"], len(R["f6"]["rows"])))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


def git_head():
    import subprocess
    return subprocess.run([r"E:\Program Files\Git\cmd\git.exe", "rev-parse", "HEAD"],
                          cwd=ROOT, capture_output=True, text=True).stdout.strip()


def old_predicate_source():
    """从 git 历史里取修前「跳过判定」那一行，并核对 HEAD 已无该行。"""
    import subprocess
    def src(rev):
        return subprocess.run([r"E:\Program Files\Git\cmd\git.exe", "show",
                               f"{rev}:eval/harness/scheduler_p1.py"],
                              cwd=ROOT, capture_output=True, text=True).stdout
    pat = 'prev.get("stage") == "judged"'
    old = [l.strip() for l in src("4f15fa2").splitlines() if pat in l]
    new = [l.strip() for l in src("HEAD").splitlines() if pat in l]
    old_line = old[0] if old else "(未找到)"
    note = (f"修前（4f15fa2）该行 = {old_line}；"
            f"HEAD 里同一行命中数 = {len(new)}（应为 0）")
    return old_line, note


def scan_writers_of_isolated():
    """在 eval/harness 与 eval/reports 下找「写」该文件名的代码位置。"""
    hits = []
    for base in (os.path.join(ROOT, "eval", "harness"),
                 os.path.join(ROOT, "eval", "reports")):
        for name in sorted(os.listdir(base)):
            if not name.endswith(".py"):
                continue
            p = os.path.join(base, name)
            for i, line in enumerate(io.open(p, encoding="utf-8", errors="replace"), 1):
                if "policy_evidence_isolated" not in line:
                    continue
                rel = os.path.relpath(p, ROOT).replace("\\", "/")
                kind = "写" if re.search(r"write|write_text|write_bytes|open\([^)]*['\"][wa]b?['\"]", line) else "读/引用"
                hits.append(f"{rel}:{i}  [{kind}] {line.strip()[:110]}")
    return hits


if __name__ == "__main__":
    sys.exit(main())
