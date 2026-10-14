"""t4 复核：**独立构造**空补丁与错补丁（不只用 harness 自带样本）。

三条独立构造的向：
  IND-EMPTY : 真的零字节候选补丁
  IND-WRONG1: 自己能干净 apply、但只改无关文件注释（不改缺陷函数）=> 必须 unresolved
  IND-WRONG2: 改到**缺陷所在的函数签名**但方向搞反（把返回值写死错）=> 必须 unresolved
  IND-COMBINE: 空补丁 + 附带只改测试的 diff（剥光后为空）=> 必须 empty_patch

对两个仓库各跑一遍，给原始 verdict。
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

OUT = r"E:\aicoding\eval-runs\t4\independent_directions"

TARGETS = ["pallets-click__1b0e19f", "pytest-dev-pluggy__20d8143",
           "more-itertools-more-itertools__1b7f033"]


def build(iid):
    inst = RI.load_instance(iid)
    src = (J.patch_touched_paths(inst["patch"]) or ["x.py"])[0]
    tests = J.patch_touched_paths(inst["test_patch"]) or []

    # WRONG1：无关注释（干净可 apply）
    w1 = (f"diff --git a/{src} b/{src}\n--- a/{src}\n+++ b/{src}\n"
          f"@@ -1,1 +1,2 @@\n+# t4 独立错补丁1：无关注释\n \n")

    # WRONG2：把 gold 的所有 `+` 行改成「减掉 gold 那行再加一行明显错的」
    #         这不是反向法，而是「方向搞反」——产出仍可 apply 的补丁。
    lines = []
    for line in inst["patch"].splitlines(keepends=True):
        if line.startswith("+") and not line.startswith("+++"):
            lines.append("-" + line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            lines.append("+" + line[1:])
        else:
            lines.append(line)
    w2 = "".join(lines)

    # COMBINE：只改测试（剥光 => empty）
    c = ""
    if tests:
        c = (f"diff --git a/{tests[0]} b/{tests[0]}\n--- a/{tests[0]}\n+++ b/{tests[0]}\n"
             f"@@ -1,2 +1,3 @@\n+# t4: 只动测试\n import pytest\n \n")
    return {"IND-EMPTY": "", "IND-WRONG1_clean_apply_no_fix": w1,
            "IND-WRONG2_inverted": w2, "IND-COMBINE_test_only": c}, {"src": src, "tests": tests}


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for iid in TARGETS:
        cases, meta = build(iid)
        for name, text in cases.items():
            if name == "IND-WRONG2_inverted" and not text.strip():
                continue
            rid = f"t4-ind-{name}"
            row = RI.run_one(iid, text, run_id=rid, label=f"t4-{name}",
                             patch_kind=f"t4:{name}", keep_workspace=False)
            rec = {"instance_id": iid, "case": name, "verdict_class": row["verdict_class"],
                   "resolved": row["resolved"], "notes": row.get("notes", []),
                   "patch_apply_exit_code": row.get("patch_apply_exit_code"),
                   "touched_tests": row.get("touched_tests"),
                   "judged_patch_paths": row.get("judged_patch_paths"),
                   "judged": row.get("judged"), "meta": meta,
                   "counts_in_denominator": row["verdict_class"] in J.COUNTS_IN_DENOMINATOR}
            rows.append(rec)
            print(f"[{iid}] {name:28s} verdict={row['verdict_class']:15s} "
                  f"resolved={row['resolved']!s:5s} apply_rc={row.get('patch_apply_exit_code')}",
                  flush=True)

    bad = [r for r in rows if r["resolved"]]
    expect = {"IND-EMPTY": J.EMPTY_PATCH, "IND-COMBINE_test_only": J.EMPTY_PATCH,
              "IND-WRONG1_clean_apply_no_fix": J.AGENT_RESULT}
    mism = [r for r in rows if r["case"] in expect and r["verdict_class"] != expect[r["case"]]]
    with open(os.path.join(OUT, "independent_directions.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump({"rows": rows, "any_resolved": [(r["instance_id"], r["case"]) for r in bad],
                   "class_mismatch": [(r["instance_id"], r["case"], r["verdict_class"])
                                      for r in mism]}, f, ensure_ascii=False, indent=2)
    print()
    print(f"独立构造 {len(rows)} 条；被判 resolved 的：{len(bad)}（必须 0）")
    for b in bad:
        print(f"   !! {b['instance_id']} {b['case']} => resolved=True")
    print(f"分类与期望不符：{len(mism)}")
    for m in mism:
        print(f"   - {m['instance_id']} {m['case']}: {m['verdict_class']} "
              f"(期望 {expect.get(m['case'])})")
    return 0 if (not bad and not mism) else 1


if __name__ == "__main__":
    sys.exit(main())