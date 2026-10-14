"""t15 验收①：判定不可被 agent 自述绕过（独立复现，离线，零模型调用）。

在**临时** RUNS_ROOT/VENV_ROOT 里建真工作区与真 venv，走生产判定路径
（scheduler_p1.judge_arm_workspace：采集候选补丁 -> 剥测试侧 -> 重建 test_patch 起点
 -> 独立 pytest -> 逐 node judge），四种情形：

  1 case-empty-selfreport        : agent 什么都没改，但在产物目录写了「已修复/测试全过」的自述
  2 case-bogus-edit-selfreport   : agent 往源码里加了一行无用注释（能 apply、不修缺陷）+ 自述
  3 case-wrong-frozen-selfreport : 用「冻结的」真错补丁（more-itertools__1b7f033 的
                                   unrelated_file_comment 正文，正着施加）+ 自述
  4 case-gold-control            : gold 补丁（对照，证明判定不是恒假）

另加两条口径断言：
  · 超时先于退出码：timed_out=True & returncode=1 => timeout（不是 agent 失败）
  · 分母：infra/timeout 不计入（judge.summarize）

⚠ 实例 `astanin-python-tabulate__*` 的冻结 `wrong_patch` 与 gold **逐字节相同**，
   语义是 `wrong_patch_method=reverse_of_gold`（先叠 gold 再 `git apply -R`）。本脚本
   实测并记录：把它**正着施加**会得到 resolved=True；正确的链式施加（见 run_instance.
   wrong_patch_plan）才回到基线。这是构造侧已归档的遗留，不是判定缺陷。

输出：eval/reports/p1-review-judge-bypass.txt / .json
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile

ROOT = r"E:\aicoding\mini-claude-code"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

from eval.harness import arms as A            # noqa: E402
from eval.harness import env as E             # noqa: E402
from eval.harness import judge as J           # noqa: E402
from eval.harness import run_instance as RI   # noqa: E402
from eval.harness import scheduler_p1 as SP   # noqa: E402

TAB = "astanin-python-tabulate__5373ebf"
MI = "more-itertools-more-itertools__1b7f033"
OUT_TXT = os.path.join(ROOT, "eval", "reports", "p1-review-judge-bypass.txt")
OUT_JSON = os.path.join(ROOT, "eval", "reports", "p1-review-judge-bypass.json")
LINES: list[str] = []
RESULT: dict = {"cases": [], "extra": {}}


def say(s=""):
    LINES.append(s)


SELFREPORT = (
    "════════ AGENT SELF-REPORT ════════\n"
    "status: SUCCESS\n"
    "resolved: true\n"
    "tests: ALL PASSED (1/1 F2P, 60/60 P2P)\n"
    "已完整修复该缺陷，无需进一步检查。\n"
)


def prepare(iid, run_id):
    inst = RI.load_instance(iid)
    art = E.art_dir(run_id, iid)
    os.makedirs(art, exist_ok=True)
    ws = E.fresh_workspace(inst, run_id, log_path=os.path.join(art, "workspace.log"))
    inst["_ws"] = ws
    deps = RI.deps_from_env_spec(inst)
    pkg = RI.infer_import_name(ws)
    py = E.ensure_venv(inst, os.path.join(art, "venv.log"), deps, pkg, run_id=run_id)
    applied = RI.apply(ws, inst.get("test_patch") or "", art)
    if applied.rc != 0:
        raise E.InfraError("test_patch 施加失败")
    return inst, ws, py, art, pkg


def write_selfreport(art):
    """在产物目录写「agent 自述成功」的诱饵（判定链不该读）。"""
    p1 = os.path.join(art, "console_A.log")
    p2 = os.path.join(art, "agent_selfreport.json")
    E.write_text(p1, SELFREPORT)
    E.write_text(p2, json.dumps(
        {"resolved": True, "status": "SUCCESS", "f2p_passed": 1, "p2p_passed": 60,
         "claim": "agent 自述：全部通过"}, ensure_ascii=False, indent=2) + "\n")
    return [p1, p2]


def run_case(name, iid, mode):
    run_id = f"p1-review-t15-{name}"
    inst, ws, py, art, pkg = prepare(iid, run_id)
    decoys = write_selfreport(art)
    note = ""
    if mode == "empty_override":
        row = A.empty_row(instance_id=iid, arm="A", run_id=run_id, label=A.make_label(iid, "A"))
        row["workspace"] = ws
        row = SP.judge_arm_workspace(row, inst, patch_override="")
    elif mode == "bogus_edit":
        # 往源码里加一行无用注释：能 clean apply、不修缺陷。
        # 目标文件从 gold 补丁的 `+++ b/<path>` 里取（不硬编码仓库布局）。
        import re as _re
        m = _re.search(r"^\+\+\+ b/(\S+)", inst.get("patch") or "", _re.M)
        rel = m.group(1) if m else None
        if not rel:
            raise E.InfraError("gold 补丁里找不到 +++ b/ 路径")
        target = os.path.join(ws, *rel.split("/"))
        with io.open(target, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n# agent 的「修复」：一行无用注释（不改变行为）\n")
        note = f"往 {rel} 追加一行注释（改动文件={target}）"
        row = A.empty_row(instance_id=iid, arm="A", run_id=run_id, label=A.make_label(iid, "A"))
        row["workspace"] = ws
        row = SP.judge_arm_workspace(row, inst)   # 走生产采集路径
    elif mode == "wrong_forward":
        text, rev, chain, plan_note = RI.wrong_patch_plan(inst)
        applied = RI.apply(ws, text, art)
        note = f"wrong_patch_plan => {plan_note}; apply exit={applied.rc}"
        row = A.empty_row(instance_id=iid, arm="A", run_id=run_id, label=A.make_label(iid, "A"))
        row["workspace"] = ws
        row = SP.judge_arm_workspace(row, inst)
    elif mode == "gold":
        row = A.empty_row(instance_id=iid, arm="A", run_id=run_id, label=A.make_label(iid, "A"))
        row["workspace"] = ws
        row = SP.judge_arm_workspace(row, inst, patch_override=inst["patch"])
    else:
        raise ValueError(mode)
    rec = {"case": name, "instance_id": iid, "run_id": run_id, "workspace": ws,
           "selfreport_decoys": decoys, "note": note,
           "verdict_class": row.get("verdict_class"), "resolved": row.get("resolved"),
           "judged": row.get("judged"),
           "pytest_cmd": (row.get("pytest") or {}).get("cmd"),
           "pytest_exit": (row.get("pytest") or {}).get("exit_code"),
           "candidate_patch_sha256": row.get("candidate_patch_sha256"),
           "judged_patch_sha256": row.get("judged_patch_sha256"),
           "notes": row.get("notes")}
    say()
    say(f"## {name}  (instance={iid})")
    if note:
        say(f"- {note}")
    say(f"- 自述诱饵（判定链不该读）：{decoys}")
    say(f"- verdict_class = {row.get('verdict_class')}   resolved = {row.get('resolved')}")
    say(f"- judged = {json.dumps(row.get('judged'), ensure_ascii=False)}")
    say(f"- 候选补丁 sha256 = {row.get('candidate_patch_sha256')}；"
        f"判决用（剥测试侧后）sha256 = {row.get('judged_patch_sha256')}")
    say(f"- pytest exit = {(row.get('pytest') or {}).get('exit_code')} "
        f"timed_out = {(row.get('pytest') or {}).get('timed_out')}")
    say(f"- pytest cmd 前 6 段 = {((row.get('pytest') or {}).get('cmd') or [])[:6]}")
    say(f"- notes = {row.get('notes')}")
    RESULT["cases"].append(rec)
    return rec


def main():
    tmp = tempfile.mkdtemp(prefix="t15-judge-")
    E.RUNS_ROOT = os.path.join(tmp, "runs")
    E.VENV_ROOT = os.path.join(tmp, "venvs")
    os.makedirs(E.RUNS_ROOT, exist_ok=True)
    os.makedirs(E.VENV_ROOT, exist_ok=True)

    say("# t15 验收①：判定不可被自述绕过（临时目录 + 真判定链）")
    say()
    say(f"临时 RUNS_ROOT={E.RUNS_ROOT}")
    say(f"临时 VENV_ROOT={E.VENV_ROOT}")
    i0 = RI.load_instance(TAB)
    say(f"实例 {TAB}: F2P={i0['FAIL_TO_PASS']}  P2P={len(i0['PASS_TO_PASS'])} 条")
    i1 = RI.load_instance(MI)
    say(f"实例 {MI}: F2P={i1['FAIL_TO_PASS']}  P2P={len(i1['PASS_TO_PASS'])} 条")

    empt = run_case("case-empty-selfreport", TAB, "empty_override")
    bogus = run_case("case-bogus-edit-selfreport", TAB, "bogus_edit")
    wrong = run_case("case-wrong-frozen-selfreport", MI, "wrong_forward")
    gold = run_case("case-gold-control", TAB, "gold")

    say()
    say("## 口径断言：超时先于退出码、infra/timeout 不计入分母")
    synth = [
        {"timed_out": True, "returncode": 1, "results": {}, "meta": {}},
        {"timed_out": False, "returncode": 1, "results": {"a": "failed"}, "meta": {}},
        {"timed_out": False, "returncode": 2, "results": {}, "meta": {}},
        {"timed_out": False, "returncode": 0, "results": {"a": "passed"}, "meta": {}},
    ]
    cls = [J.classify_run(r) for r in synth]
    say(f"- classify_run 四例（timed_out=True&rc=1 / rc=1&有失败 / rc=2 / 正常）=> {cls}")
    expect = ["timeout", "agent_result", "infra_error", "agent_result"]
    say(f"  期望 {expect} => {cls == expect}")
    RESULT["extra"]["classify_run"] = {"output": cls, "expected": expect,
                                       "timeout_first": cls[0] == J.TIMEOUT}
    mix = [
        {"verdict_class": "agent_result", "resolved": True},
        {"verdict_class": "agent_result", "resolved": False},
        {"verdict_class": "agent_result", "resolved": False},
        {"verdict_class": "timeout", "resolved": False},
        {"verdict_class": "infra_error", "resolved": False},
    ]
    summ = J.summarize(mix)
    say(f"- summarize(3 agent_result 其中 1 resolved + 1 timeout + 1 infra) = "
        f"{json.dumps(summ, ensure_ascii=False)}")
    say(f"  期望 denominator=3, resolved=1, infra_retryable=2 => "
        f"{summ['denominator'] == 3 and summ['resolved'] == 1 and summ['infra_retryable'] == 2}")
    RESULT["extra"]["summarize"] = summ

    # 额外记录：tabulate 的冻结 wrong_patch 正着施加 = gold = resolved
    say()
    say("## 额外观察：冻结 wrong_patch 的两种施加语义（tabulate 系）")
    it = RI.load_instance(TAB)
    same = (it.get("wrong_patch") or "").strip() == (it.get("patch") or "").strip()
    say(f"- {TAB}: wrong_patch_method={(it.get('gold_selfcheck') or {}).get('wrong_patch_method')}"
        f"；wrong_patch 与 gold 逐字节相同 = {same}")
    say("- 正着施加该正文 = 施加 gold => resolved=True（本脚本第一次跑就实测到了）；"
        "唯一正确施加方式是 `wrong_patch_plan()` 给出的「先叠 gold 再 git apply -R」。")
    say("- 这一条是**构造侧**已归档的遗留（见 run_instance.py:204-225 与 gold_check 的 caveat），"
        "不是判定侧缺陷；但它说明「wrong_patch 正文」本身不能当「错补丁」用。")
    RESULT["extra"]["frozen_wrong_patch"] = {
        "instance_id": TAB, "same_as_gold": same,
        "method": (it.get("gold_selfcheck") or {}).get("wrong_patch_method")}

    verdicts = {
        "selfreport_ignored_empty": (empt["verdict_class"] == "agent_result"
                                     and empt["resolved"] is False),
        "selfreport_ignored_bogus_edit": (bogus["verdict_class"] == "agent_result"
                                          and bogus["resolved"] is False
                                          and bogus["candidate_patch_sha256"] is not None),
        "selfreport_ignored_wrong_frozen": (wrong["verdict_class"] == "agent_result"
                                            and wrong["resolved"] is False),
        "gold_control_resolves": (gold["resolved"] is True),
        "timeout_before_exit_code": RESULT["extra"]["classify_run"]["timeout_first"],
        "infra_timeout_not_in_denominator": (summ["denominator"] == 3),
    }
    RESULT["verdicts"] = verdicts
    say()
    say("## 小结")
    for k, v in verdicts.items():
        say(f"- {k}: {'PASS' if v else 'FAIL'}")

    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(RESULT, ensure_ascii=False, indent=2) + "\n")
    with io.open(OUT_TXT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(LINES) + "\n")
    print("judge-bypass: " + " ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in verdicts.items()))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if all(verdicts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
