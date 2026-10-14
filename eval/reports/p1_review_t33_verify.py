"""t33 独立复验：t32 的 ②（生产路径生成证据+索引）与 ③（归档回填）。

零模型调用、只读验证（唯一写入：本报告目录 + 临时目录）。
不复用 t32 的断言结论：sha256 / 字段级 diff / 生产路径桩都由本脚本自己算。

Part 1  ③ 回填完整性：备份 sha256（应等于我 t15/t30 的独立基线 17f3a711…）、
        当前 sha256、自算字段级 diff（只允许 artifacts 变）、9/9 C 行含索引、A/B 18 行逐字节不变。
Part 2  ② 生效性：直接调用生产函数 `_run_one_arm`（假 Popen，无模型调用），把返回行
        经 `append_triple_state` 落盘后重读，断言落盘行含索引；并覆盖 timeout 分支。
Part 3  两个生产者的 payload schema 对照（归档=抽取脚本 vs 新=worker）。
Part 4  G2/G3 文本落实。
Part 5  越界与零模型调用。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = r"E:\aicoding\mini-claude-code"
BATCH = r"E:\aicoding\eval-runs\p1-20261013T122356-t20-batch"
STATE = os.path.join(BATCH, "triple_state.jsonl")
BACKUP = STATE + ".pre-t32"
USAGE_DIR = os.path.join(ROOT, "servers", "experiments", "out")
OUT_TXT = os.path.join(ROOT, "eval", "reports", "t33-review-verify.txt")
OUT_JSON = os.path.join(ROOT, "eval", "reports", "t33-review-verify.json")

T15_BASELINE_SHA = "17f3a7119fa01b20b1fa899c1fda2b3ef2af3f737b7444d9a6de334f17da8c78"
CLAIMED_AFTER_SHA = "bb89825ada4d1eace311376736ab5a2d5cff01a86389bc58cbc623a01d12bbff"
LINES: list[str] = []
R: dict = {}


def say(s=""):
    LINES.append(s)


def sha_b(b):
    return hashlib.sha256(b).hexdigest()


def sha_f(p):
    return sha_b(open(p, "rb").read())


def main():
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "eval"))
    from eval.harness import scheduler_p1 as SP  # noqa: E402

    say("# t33 独立复验：t32（② 生产路径索引 + ③ 归档回填）")
    say()
    say(f"HEAD = {git('rev-parse', 'HEAD')}")
    say("零模型调用：Part 2 用假 Popen 直接调用生产函数，不 spawn 任何 agent。")
    say()

    # ------------------------------------------------------------ Part 1
    say("## Part 1 · ③ 回填完整性（自算）")
    say()
    say(f"- 备份文件存在 = {os.path.isfile(BACKUP)}；路径 = {BACKUP}")
    assert os.path.isfile(BACKUP), "备份不存在"
    before_bytes = open(BACKUP, "rb").read()
    after_bytes = open(STATE, "rb").read()
    before_sha, after_sha = sha_b(before_bytes), sha_b(after_bytes)
    say(f"- 回填前 sha256 = {before_sha}")
    say(f"  与我 t15/t30 独立记录的基线一致 = {before_sha == T15_BASELINE_SHA}")
    say(f"- 回填后 sha256 = {after_sha}")
    say(f"  与 t32 报告声称的 {CLAIMED_AFTER_SHA} 一致 = {after_sha == CLAIMED_AFTER_SHA}")
    b_lines = before_bytes.splitlines(keepends=True)
    a_lines = after_bytes.splitlines(keepends=True)
    say(f"- 物理行数：前 {len(b_lines)} / 后 {len(a_lines)}")

    field_diffs = []
    ab_byte_identical = 0
    c_indexed = 0
    c_missing = []
    for i, (bl, al) in enumerate(zip(b_lines, a_lines), 1):
        bro, aro = json.loads(bl), json.loads(al)
        assert bro["instance_id"] == aro["instance_id"] and bro["arm"] == aro["arm"], i
        # 非 artifacts 字段必须逐字相同
        b_wo = {k: v for k, v in bro.items() if k != "artifacts"}
        a_wo = {k: v for k, v in aro.items() if k != "artifacts"}
        same_wo = b_wo == a_wo
        arts_changed = bro.get("artifacts") != aro.get("artifacts")
        if aro["arm"] == "C":
            expect = f"{aro['instance_id']}/artifacts/policy_evidence_isolated.json"
            ok_index = expect in aro["artifacts"] and aro["artifacts"][:-1] == bro["artifacts"]
            c_indexed += 1 if ok_index else 0
            if not ok_index:
                c_missing.append(aro["run_id"])
            field_diffs.append({"line": i, "arm": "C", "run_id": aro["run_id"],
                                "non_artifacts_same": same_wo, "artifacts_changed": arts_changed,
                                "index_appended": ok_index, "appended": expect})
        else:
            if bl == al:
                ab_byte_identical += 1
            field_diffs.append({"line": i, "arm": aro["arm"], "run_id": aro["run_id"],
                                "non_artifacts_same": same_wo, "artifacts_changed": arts_changed,
                                "byte_identical": bl == al})
    all_wo_same = all(d["non_artifacts_same"] for d in field_diffs)
    say()
    say("| 行 | 臂 | 非 artifacts 字段逐字相同 | artifacts 变化 | 追加的索引正确 | 整行逐字节相同 |")
    say("|---:|:---:|:---:|:---:|:---:|:---:|")
    for d in field_diffs:
        say(f"| {d['line']} | {d['arm']} | {d['non_artifacts_same']} | {d['artifacts_changed']} | "
            f"{d.get('index_appended', '—')} | {d.get('byte_identical', '—')} |")
    c_rows = [d for d in field_diffs if d["arm"] == "C"]
    ab_rows = [d for d in field_diffs if d["arm"] != "C"]
    say()
    say(f"- 非 artifacts 字段**全部**行逐字相同 = {all_wo_same}")
    say(f"- C 行含索引 = {c_indexed}/{len(c_rows)}（缺失：{c_missing or '无'}）")
    say(f"- A/B 行整行逐字节不变 = {ab_byte_identical}/{len(ab_rows)}")
    say(f"- 结论字段（resolved/verdict_class/stage/notes/judged/pytest/tokens_*…）"
        f"零变化 = {all_wo_same}")
    R["backfill"] = {"backup_exists": True, "before_sha": before_sha, "after_sha": after_sha,
                     "before_matches_my_baseline": before_sha == T15_BASELINE_SHA,
                     "after_matches_claim": after_sha == CLAIMED_AFTER_SHA,
                     "rows_before": len(b_lines), "rows_after": len(a_lines),
                     "non_artifacts_same_all": all_wo_same,
                     "c_indexed": c_indexed, "c_total": len(c_rows),
                     "ab_byte_identical": ab_byte_identical, "ab_total": len(ab_rows),
                     "field_diffs": field_diffs}

    # ------------------------------------------------------------ Part 2
    say()
    say("## Part 2 · ② 生效性：直接调用生产函数 `_run_one_arm`（假 Popen）")
    say()
    tmp = tempfile.mkdtemp(prefix="t33-prod-")
    orig_runs = SP.E.RUNS_ROOT
    SP.E.RUNS_ROOT = os.path.join(tmp, "runs")
    os.makedirs(SP.E.RUNS_ROOT, exist_ok=True)
    real_label = "p1-astanin-python-tabulate__5373ebf-C"

    class FakeProc:
        def __init__(self, *, hang=False):
            self.hang = hang
            self.calls = 0
            self.returncode = 1
            self.killed = False

        def communicate(self, timeout=None):
            self.calls += 1
            # 只在第一次抛 TimeoutExpired（真实 Popen 被 kill 后第二次能正常返回）
            if self.hang and self.calls == 1:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return ("=== fake agent stdout ===\n", None)

        def kill(self):
            self.killed = True

    results = []
    for case, hang in (("exit1", False), ("timeout", True)):
        run_id = f"t33-prod-{case}"
        task = {"instance_id": "astanin-python-tabulate__5373ebf", "arm": "C",
                "run_id": run_id, "label": real_label,
                "workspace": os.path.join(SP.E.RUNS_ROOT, run_id, "ws"),
                "problem_file": os.path.join(tmp, "problem.txt"),
                "extra_args": list(SP.A.ARM_EXTRA_ARGS["C"]),
                "arm_report_file": os.path.join(tmp, "arm_report.jsonl"),
                "repo": "astanin/python-tabulate", "base_commit": "0" * 40}
        orig_popen = SP.subprocess.Popen
        SP.subprocess.Popen = lambda *a, **k: FakeProc(hang=hang)
        state_root = os.path.join(tmp, f"state-{case}")
        os.makedirs(state_root, exist_ok=True)
        try:
            row = SP._run_one_arm(task)
        finally:
            SP.subprocess.Popen = orig_popen
        SP.append_triple_state(state_root, row)
        back = SP.read_triple_state(state_root)[("astanin-python-tabulate__5373ebf", "C")]
        idx = f"astanin-python-tabulate__5373ebf/artifacts/policy_evidence_isolated.json"
        landed = idx in back["artifacts"]
        ev = os.path.join(SP.E.art_dir(run_id, task["instance_id"]),
                          "policy_evidence_isolated.json")
        ev_exists = os.path.isfile(ev)
        payload = json.load(io.open(ev, encoding="utf-8")) if ev_exists else {}
        say(f"### 案例 {case}（timed_out={back.get('timed_out')}  verdict={back.get('verdict_class')}）")
        say(f"- 落盘后重读的 state 行含索引 = **{landed}**（索引={idx}）")
        say(f"- worker 生成的证据文件存在 = {ev_exists}；dispatch_rows 条数 = "
            f"{len(payload.get('dispatch_rows') or [])}")
        say(f"- 证据 payload 键 = {sorted(payload.keys())}")
        say(f"- 该行的 artifacts 末尾 = {back['artifacts'][-1]}")
        results.append({"case": case, "timed_out": back.get("timed_out"),
                        "verdict_class": back.get("verdict_class"),
                        "landed_index": landed, "evidence_exists": ev_exists,
                        "dispatch_rows": len(payload.get("dispatch_rows") or []),
                        "payload_keys": sorted(payload.keys())})
    SP.E.RUNS_ROOT = orig_runs
    say()
    say(f"- ⇒ 生产路径（worker 内）**自己生成证据并追加索引**；两个分支（正常退出/超时）都成立 = "
        f"{all(r['landed_index'] for r in results)}")

    # 过滤有效性 + 与归档证据的一致性（用真实 label 与真实 run 窗口起点）
    say()
    state_rows_early = [json.loads(x) for x in io.open(STATE, encoding="utf-8") if x.strip()]
    arch_c = [r for r in state_rows_early if r["arm"] == "C"][0]
    art2 = os.path.join(tmp, "filter-art")
    os.makedirs(art2, exist_ok=True)
    fake_row = {"instance_id": arch_c["instance_id"], "arm": "C", "run_id": "t33-filter",
                "label": arch_c["label"], "started_at_epoch": arch_c["started_at_epoch"]}
    p2 = SP._write_policy_evidence_for_run(fake_row, art2)
    pay2 = json.load(io.open(p2, encoding="utf-8"))
    arch_file = os.path.join(os.path.dirname(arch_c["workspace"]), "artifacts",
                             "policy_evidence_isolated.json")
    arch_pay = json.load(io.open(arch_file, encoding="utf-8"))
    def keyset(rows):
        return sorted((x.get("ts"), x.get("k"), x.get("group"), x.get("event")) for x in rows)
    same = keyset(pay2.get("dispatch_rows") or []) == keyset(arch_pay.get("dispatch_rows") or [])
    say(f"- 过滤有效性（真实 label `{arch_c['label']}` + 真实窗口起点 "
        f"{arch_c['started_at_epoch']}）：worker 版 dispatch_rows="
        f"{len(pay2.get('dispatch_rows') or [])}，归档版="
        f"{len(arch_pay.get('dispatch_rows') or [])}，四元组集合一致 = {same}")
    R["filter_fidelity"] = {"label": arch_c["label"], "worker_rows": len(pay2.get("dispatch_rows") or []),
                            "archived_rows": len(arch_pay.get("dispatch_rows") or []),
                            "same_key_set": same}
    R["production"] = results

    # ------------------------------------------------------------ Part 3
    say()
    say("## Part 3 · 两个生产者的 payload schema 对照")
    say()
    state_rows = [json.loads(x) for x in io.open(STATE, encoding="utf-8") if x.strip()]
    c_row = [r for r in state_rows if r["arm"] == "C"][0]
    archived = os.path.join(os.path.dirname(c_row["workspace"]), "artifacts",
                            "policy_evidence_isolated.json")
    arch = json.load(io.open(archived, encoding="utf-8"))
    worker = results[0]["payload_keys"]
    say(f"- 归档（抽取脚本生成）payload 键 = {sorted(arch.keys())}")
    say(f"- 新（worker 生成）payload 键      = {worker}")
    only_arch = sorted(set(arch.keys()) - set(worker))
    say(f"- 只有归档版才有的键 = {only_arch}")
    say(f"- 注：`arm_report_rows` 是我 t15 复算 `index_chars` / `worktrees_after_dispatch` 的来源；"
        f"worker 版不含它，但同一目录下的 `arm_report.jsonl` 仍在。")
    R["schema"] = {"archived_keys": sorted(arch.keys()), "worker_keys": worker,
                   "only_archived": only_arch}

    # ------------------------------------------------------------ Part 4
    say()
    say("## Part 4 · G2 / G3 文本落实")
    say()
    ev_md = io.open(os.path.join(ROOT, "eval/harness/t29-evidence.md"), encoding="utf-8").read()
    chk = io.open(os.path.join(ROOT, "eval/harness/t29_resume_check.py"), encoding="utf-8").read()
    src = io.open(os.path.join(ROOT, "eval/harness/scheduler_p1.py"), encoding="utf-8").read()
    g2_md = "未在生产 state 行验证落盘" in ev_md
    g2_line = [l.strip() for l in ev_md.splitlines() if "F6" in l]
    g2_assert = ("state_row_index" in chk and "artifacts" in chk)
    g3_doc = ("absolute paths except this one deliberate" in src
              or "除本索引外均为绝对路径" in src) and "run_root" in src
    say(f"- G2 · `t29-evidence.md` 含「函数返回值正确（未在生产 state 行验证落盘）」= {g2_md}")
    say(f"    F6 相关行：{g2_line}")
    say(f"- G2 · `t29_resume_check.py` 有真实 state 行索引断言（state_row_index）= {g2_assert}")
    say(f"- G3 · `policy_isolated_artifact()` docstring 写明「除本索引外均为绝对路径 / 以 run_root 为基准」= {g3_doc}")
    batch_md = io.open(os.path.join(ROOT, "eval/reports/p1-batch.md"), encoding="utf-8").read()
    back_md = io.open(os.path.join(ROOT, "eval/reports/p1-t32-backfill.md"), encoding="utf-8").read()
    say(f"- ③.④ 「回填」二字：`p1-t32-backfill.md` = {'回填' in back_md}；"
        f"`p1-batch.md` = {'回填' in batch_md}")
    say(f"- ③.④ 脚本路径与执行时刻：backfill.md 含脚本名 = "
        f"{'p1_backfill_evidence_index.py' in back_md}；含执行时间 = {'2026-10-13 23:03:49' in back_md}")
    batch_mention = [l.strip()[:110] for l in batch_md.splitlines() if "回填" in l]
    say(f"- `p1-batch.md` 里的「回填」句（共 {len(batch_mention)} 行）：")
    for l in batch_mention[:6]:
        say(f"    · {l}")
    R["g2g3"] = {"g2_evidence_text": g2_md, "g2_f6_lines": g2_line,
                 "g2_state_row_assert": g2_assert, "g3_docstring": g3_doc,
                 "huifan_in_backfill_md": "回填" in back_md,
                 "huifan_in_batch_md": "回填" in batch_md,
                 "script_named": "p1_backfill_evidence_index.py" in back_md,
                 "time_recorded": "2026-10-13 23:03:49" in back_md,
                 "batch_huifan_lines": batch_mention}

    # ------------------------------------------------------------ Part 5
    say()
    say("## Part 5 · 越界与零模型调用")
    say()
    for rev in ("faf7043", "f775742"):
        files = git("show", "--name-only", "--format=", rev).split()
        say(f"- {rev} 改动文件：{files}")
    say(f"- 两份预注册被碰 = "
        f"{any('PREREGISTRATION' in f for f in git('diff', '--name-only', '7d29e6d', 'HEAD').split())}")
    p1batch_log = git("log", "--oneline", "--", "eval/reports/p1-batch.md")
    say(f"- `eval/reports/p1-batch.md` 的提交历史：{p1batch_log.splitlines()}")
    others = {"p1-analysis.md", "t15-review.md", "t30-verification-round-2.md",
              "t26-verification-round-2.md", "t19-verification-round-2.md",
              "t22-verification-round-3.md"}
    touched = [f for f in git("diff", "--name-only", "7d29e6d", "HEAD").split()
               if os.path.basename(f) in others]
    say(f"- 我关心的既有他人报告是否被 t32 改动：{touched or '无'}")
    dirs = subprocess.run([r"E:\Program Files\Git\cmd\git.exe"], capture_output=True)
    newest = sorted((os.path.getmtime(os.path.join(r"E:\aicoding\eval-runs", d)), d)
                    for d in os.listdir(r"E:\aicoding\eval-runs")
                    if d.startswith("p1-"))[-3:]
    import datetime as dt
    say(f"- eval-runs 下最新 3 个 p1-* 目录：")
    for mt, d in newest:
        say(f"    · {d}  {dt.datetime.fromtimestamp(mt).strftime('%Y-%m-%d %H:%M:%S')}")
    commit_time = dt.datetime.fromtimestamp(
        int(subprocess.run([r"E:\Program Files\Git\cmd\git.exe", "show", "-s",
                            "--format=%ct", "f775742"], cwd=ROOT, capture_output=True,
                           text=True).stdout.strip()))
    say(f"- t32 提交时刻 = {commit_time.strftime('%Y-%m-%d %H:%M:%S')}；"
        f"晚于最新 p1-* 目录 = {all(dt.datetime.fromtimestamp(mt) < commit_time for mt, _ in newest)}")
    usage = [json.loads(x) for x in io.open(os.path.join(USAGE_DIR, "usage.jsonl"),
                                            encoding="utf-8", errors="replace") if x.strip()]
    scout = [json.loads(x) for x in io.open(os.path.join(USAGE_DIR, "policy_scout_usage.jsonl"),
                                            encoding="utf-8", errors="replace") if x.strip()]
    p1u = [u for u in usage if str(u.get("label") or "").startswith("p1-")]
    p1s = [s for s in scout if str(s.get("label") or "").startswith("p1-")]
    max_ts = max([str(u.get("ts")) for u in p1u] + [str(s.get("ts")) for s in p1s])
    say(f"- usage 总/p1- = {len(usage)}/{len(p1u)}；scout 总/p1- = {len(scout)}/{len(p1s)}"
        f"（t15/t30 基线 186/56、55/21）")
    say(f"- p1- 用量最大 ts = {max_ts}（t32 提交 23:16:09）⇒ 提交后无新用量 = "
        f"{max_ts < '2026-10-13T23:16:09'}")
    R["scope"] = {"commits": ["faf7043", "f775742"],
                  "p1batch_history": p1batch_log.splitlines(),
                  "others_touched": touched,
                  "newest_run_dirs": [(d, dt.datetime.fromtimestamp(mt).isoformat())
                                      for mt, d in newest],
                  "usage_total": len(usage), "usage_p1": len(p1u),
                  "scout_total": len(scout), "scout_p1": len(p1s),
                  "max_p1_usage_ts": max_ts}

    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(R, ensure_ascii=False, indent=2) + "\n")
    with io.open(OUT_TXT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(LINES) + "\n")
    print("t33 verify: backfill_ok=%s c_indexed=%d/%d ab_identical=%d/%d prod_landed=%s g2=%s g3=%s"
          % (all_wo_same and after_sha == CLAIMED_AFTER_SHA, c_indexed, len(c_rows),
             ab_byte_identical, len(ab_rows), all(r["landed_index"] for r in results),
             g2_md and g2_assert, g3_doc))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


def git(*args):
    return subprocess.run([r"E:\Program Files\Git\cmd\git.exe", *args], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()


if __name__ == "__main__":
    sys.exit(main())
