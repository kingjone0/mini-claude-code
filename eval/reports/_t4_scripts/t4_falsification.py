"""t4 复核：三类「不该被判 resolved」的情形（补丁缺失 / 应用失败 / 故意写错）。

**这个脚本自己造候选补丁**（不只用 harness 自带的 empty/wrong 样本），
直接调 `run_instance.run_one`（被复核对象），看它给出什么 verdict_class：

  case A「缺失」      : patch_text=None（候选补丁根本不存在）
  case B「应用失败」  : 语法上合法但上下文对不上的 diff（必然 conflict）
  case C「故意写错」  : 能干净应用、但改的是无关文件/无关函数（修不好缺陷）
  case D「只改测试」  : 试图改测试骗绿
  case E「真 gold」    : 阳性对照（必须 resolved，否则说明前面几条的 resolve=False 没有意义）

判定标准：A/B/C/D 一律 resolved=False，且 verdict_class ∈
{empty_patch, patch_conflict, agent_result}；**绝不允许**出现 resolved=True。
case E 必须 resolved=True。

用法：python t4_falsification.py <run_id>
"""

from __future__ import annotations

import json
import os
import sys

PROJ = r"E:\aicoding\mini-claude-code"
for p in (PROJ, os.path.join(PROJ, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import judge as J          # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402

OUT = r"E:\aicoding\eval-runs\t4\falsification"

# 两个仓库各来一组，覆盖「源码布局不同」的情况
CASES = [
    ("pallets-click__1b0e19f", "tests/test_options.py"),
    ("more-itertools-more-itertools__1b7f033", "tests/test_more.py"),
]


def _first_line_of(iid, rel_path):
    """从实例的 base_commit 取 `rel_path` 的第一行（git show <base>:<path>）。"""
    import subprocess
    inst = RI.load_instance(iid)
    rd = os.path.join(r"E:\aicoding\eval-cache", inst["repo"].replace("/", "__"))
    p = subprocess.run([r"E:\Program Files\Git\cmd\git.exe", "-C", rd, "show",
                        f"{inst['base_commit']}:{rel_path}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 or not p.stdout:
        return None
    first = p.stdout.splitlines(keepends=True)[0]
    return first if first.strip() else None


def make_cases(iid, test_path):
    """用**实例自己的 test_patch** 反推测试文件路径，构造候选补丁。"""
    inst = RI.load_instance(iid)
    tp_paths = J.patch_touched_paths(inst["test_patch"])
    real_test = tp_paths[0] if tp_paths else test_path
    src_paths = [p for p in J.patch_touched_paths(inst["patch"])]
    src = src_paths[0] if src_paths else "NONEXISTENT.py"

    # B) 应用失败：拿 gold 正文，但把被修改的上下文行换成不存在的内容
    gold = inst["patch"]
    broken = []
    for line in gold.splitlines(keepends=True):
        if line.startswith(" ") and len(line.strip()) > 3:
            broken.append(" " + line[1:].lstrip()[:0] + "XXX_T4_NONEXISTENT_CONTEXT_LINE\n")
        else:
            broken.append(line)
    broken_text = "".join(broken)

    # C) 故意写错：能**干净应用**，但只是往文件顶部插一行注释（不改缺陷函数）
    #    ⚠ 必须拿文件真实的第 1 行当上下文，否则会变成 patch_conflict（本脚本第一版
    #    就是这么错的：用了不存在的上下文行 => 被判 patch_conflict 而不是 agent_result）。
    first_line = _first_line_of(iid, src)
    if first_line is None:
        raise RuntimeError(f"取不到 {iid} 的 {src} 首行，无法构造「可干净应用」的错补丁")
    ctx_line = first_line
    wrong_ok = (
        f"diff --git a/{src} b/{src}\n"
        f"--- a/{src}\n"
        f"+++ b/{src}\n"
        f"@@ -1,1 +1,2 @@\n"
        f"+# t4 故意写错的候选补丁：只加一行注释，不修任何缺陷\n"
        f" {ctx_line}"
    )

    # D) 只改测试：往测试文件顶部插一行注释（试图「动测试」）
    only_test = (
        f"diff --git a/{real_test} b/{real_test}\n"
        f"--- a/{real_test}\n"
        f"+++ b/{real_test}\n"
        f"@@ -1,2 +1,3 @@\n"
        f"+# t4: 只动测试文件，应被剥离\n"
        f" import pytest\n"
        f" \n"
    )
    return {
        "A_missing": (None, "候选补丁缺失（None）"),
        "B_apply_fail": (broken_text, "上下文被破坏的补丁 => 应 patch_conflict"),
        "C_wrong_deliberate": (wrong_ok, "能干净应用但没修缺陷 => 应 agent_result/unresolved"),
        "D_test_only": (only_test, "只改测试 => 应 empty_patch（剥光）"),
        "E_gold_control": (gold, "阳性对照：真 gold => 应 resolved"),
    }, {"test_path": real_test, "src_path": src, "test_patch_paths": tp_paths}


def main():
    run_id = sys.argv[1] if len(sys.argv) > 1 else "t4-falsify"
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for iid, _tp in CASES:
        cases, meta = make_cases(iid, _tp)
        for name, (text, desc) in cases.items():
            rid = f"{run_id}-{name}"
            row = RI.run_one(iid, text, run_id=rid, label=f"t4-{name}",
                             patch_kind=f"t4:{name}", keep_workspace=False)
            rec = {
                "instance_id": iid, "case": name, "desc": desc,
                "verdict_class": row["verdict_class"], "resolved": row["resolved"],
                "notes": row.get("notes", []),
                "touched_tests": row.get("touched_tests"),
                "judged_patch_paths": row.get("judged_patch_paths"),
                "patch_apply_exit_code": row.get("patch_apply_exit_code"),
                "judged": row.get("judged"),
                "counts_in_denominator": row["verdict_class"] in J.COUNTS_IN_DENOMINATOR,
                "counts_as_agent_failure": row["verdict_class"] in J.COUNTS_AS_AGENT_FAILURE,
                "meta": meta,
                "evidence_dir": os.path.join(r"E:\aicoding\eval-runs", rid, iid, "artifacts"),
            }
            rows.append(rec)
            print(f"[{iid}] {name:20s} verdict={row['verdict_class']:15s} "
                  f"resolved={row['resolved']!s:5s} denom={rec['counts_in_denominator']!s:5s} "
                  f"agent_fail={rec['counts_as_agent_failure']}", flush=True)
            for n in row.get("notes", []):
                print(f"      note: {n}", flush=True)

    # 断言
    negatives = [r for r in rows if r["case"] != "E_gold_control"]
    positives = [r for r in rows if r["case"] == "E_gold_control"]
    bad = [r for r in negatives if r["resolved"]]
    ctrl_bad = [r for r in positives if not r["resolved"]]
    allowed = {J.EMPTY_PATCH, J.PATCH_CONFLICT, J.AGENT_RESULT, J.INFRA_ERROR, J.TIMEOUT}
    out_of_class = [r for r in negatives if r["verdict_class"] not in allowed]

    # case A 应是 empty_patch；B 应是 patch_conflict；C 应是 agent_result
    expect = {"A_missing": J.EMPTY_PATCH, "B_apply_fail": J.PATCH_CONFLICT,
              "C_wrong_deliberate": J.AGENT_RESULT, "D_test_only": J.EMPTY_PATCH}
    mismatched = [r for r in negatives
                  if r["case"] in expect and r["verdict_class"] != expect[r["case"]]]

    summary = {
        "run_id": run_id,
        "negatives_total": len(negatives),
        "negatives_resolved_bad": [(r["instance_id"], r["case"]) for r in bad],
        "positive_control_failed": [(r["instance_id"], r["case"]) for r in ctrl_bad],
        "out_of_class": [(r["instance_id"], r["case"], r["verdict_class"]) for r in out_of_class],
        "class_mismatch": [(r["instance_id"], r["case"], r["verdict_class"],
                            expect.get(r["case"])) for r in mismatched],
        "rows": rows,
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "falsification.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print()
    print(f"负向样本 {len(negatives)} 条，被判 resolved 的：{len(bad)}（必须为 0）")
    print(f"阳性对照 {len(positives)} 条，未 resolved 的：{len(ctrl_bad)}（必须为 0）")
    print(f"分类与期望不符：{len(mismatched)}")
    for m in mismatched:
        print(f"   - {m['instance_id']} {m['case']}: 实得 {m['verdict_class']}，"
              f"期望 {expect.get(m['case'])}")
    print(f"JSON -> {os.path.join(OUT, 'falsification.json')}")
    return 0 if (not bad and not ctrl_bad and not mismatched) else 1


if __name__ == "__main__":
    sys.exit(main())