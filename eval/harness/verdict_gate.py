"""Validate P1 independent verdict evidence before a batch can be accepted.

The arm wiring report alone proves only that CLI/options were assembled; it does
not prove that the independent pytest judge ran. This gate inspects every row in
triple_state.jsonl and the corresponding on-disk verdict artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.dirname(HERE)
PROJECT_ROOT = os.path.dirname(EVAL_DIR)
for path in (PROJECT_ROOT, EVAL_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from eval.harness import env as E  # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402

PLACEHOLDER = re.compile(r"待.{0,40}判定|占位")
ARTIFACT_NAMES = ("judged.json", "verdict.json", "result.json", "verdict_run.txt")


def _load_json(path: str):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def check_row(row: dict, line_no: int) -> list[str]:
    iid, arm, run_id = row.get("instance_id"), row.get("arm"), row.get("run_id")
    prefix = f"第{line_no}行 {iid}/{arm}"
    errors = []
    if not iid or arm not in ("A", "B", "C") or not run_id:
        return [f"{prefix}: 缺 instance_id/arm/run_id"]
    if PLACEHOLDER.search(" ".join(str(x) for x in row.get("notes") or [])):
        errors.append(f"{prefix}: notes 含待判定/占位措辞")
    if row.get("verdict_class") != "agent_result":
        return errors  # infra/timeout/empty_patch/patch_conflict have no pytest obligation
    if row.get("stage") != "judged":
        errors.append(f"{prefix}: agent_result 未标记 stage=judged")

    art = E.art_dir(run_id, iid)
    paths = {name: os.path.join(art, name) for name in ARTIFACT_NAMES}
    for name, path in paths.items():
        if not os.path.isfile(path):
            errors.append(f"{prefix}: 缺判定产物 {name}: {path}")
    if any(not os.path.isfile(path) for path in paths.values()):
        return errors
    try:
        result, judged, report = (_load_json(paths[name]) for name in
                                  ("result.json", "judged.json", "verdict.json"))
    except (OSError, ValueError) as exc:
        return errors + [f"{prefix}: 判定 JSON 无法读取: {exc}"]

    if result.get("instance_id") != iid or result.get("arm") != arm or result.get("run_id") != run_id:
        errors.append(f"{prefix}: result.json 身份与状态行不匹配")
    for key in ("verdict_class", "resolved", "judged", "pytest"):
        if result.get(key) != row.get(key):
            errors.append(f"{prefix}: result.json.{key} 与状态行不一致")
    pytest = row.get("pytest") or {}
    if not isinstance(pytest.get("exit_code"), int) or isinstance(pytest.get("exit_code"), bool):
        errors.append(f"{prefix}: pytest.exit_code 非整数/缺失")
    if pytest.get("exit_code") != report.get("exitcode"):
        errors.append(f"{prefix}: pytest.exit_code 与 verdict.json.exitcode 不一致")
    if not isinstance(report.get("tests"), list) or not report["tests"]:
        errors.append(f"{prefix}: verdict.json 缺逐 node pytest tests 明细")
    try:
        inst = RI.load_instance(iid)
    except (OSError, ValueError, SystemExit) as exc:
        return errors + [f"{prefix}: 实例定义无法核对: {exc}"]
    expected = (len(inst.get("FAIL_TO_PASS") or []), len(inst.get("PASS_TO_PASS") or []))
    if judged.get("f2p_total") != expected[0] or judged.get("p2p_total") != expected[1]:
        errors.append(f"{prefix}: judged.json F2P/P2P 总数与实例定义不符 {expected}")
    if row.get("judged") != judged:
        errors.append(f"{prefix}: 状态行 judged 与 judged.json 不一致")
    # pytest-json-report uses ``subtests passed`` for a successful unittest
    # method containing subtests; judge.py normalizes that outcome to passed.
    # Keep the gate on the same semantic outcome vocabulary as the judge.
    tests = {}
    for test in report.get("tests") or []:
        if not isinstance(test, dict):
            continue
        outcome = test.get("outcome")
        if outcome == "subtests passed":
            outcome = "passed"
        tests[test.get("nodeid")] = outcome
    for field, nodes in (("f2p_not_passed", inst.get("FAIL_TO_PASS") or []),
                         ("p2p_not_passed", inst.get("PASS_TO_PASS") or [])):
        wrong = sorted(n for n in nodes if tests.get(n) != "passed")
        if sorted(judged.get(field) or []) != wrong:
            errors.append(f"{prefix}: {field} 与 pytest 逐 node 结果不一致")
    expected_resolved = not judged.get("f2p_not_passed") and not judged.get("p2p_not_passed")
    expected_resolved = expected_resolved and not judged.get("f2p_missing") and not judged.get("p2p_missing")
    if row.get("resolved") is not expected_resolved:
        errors.append(f"{prefix}: resolved 无法由逐 node F2P/P2P 重算")
    return errors


def check_file(path: str) -> dict:
    errors, checked = [], 0
    try:
        with open(path, encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                checked += 1
                try:
                    row = json.loads(line)
                    errors.extend(check_row(row, line_no))
                except (ValueError, TypeError) as exc:
                    errors.append(f"第{line_no}行: 无法校验: {exc}")
    except OSError as exc:
        errors.append(f"状态文件无法读取: {exc}")
    if not checked:
        errors.append("状态文件没有运行行")
    return {"pass": not errors, "checked": checked, "reasons": errors, "state_file": path}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="P1 独立 pytest 判定产物门禁")
    parser.add_argument("--state", required=True, help="triple_state.jsonl 路径")
    args = parser.parse_args(argv)
    result = check_file(args.state)
    print(f"[{'PASS' if result['pass'] else 'FAIL'}] 判定产物门禁  rows={result['checked']}")
    for reason in result["reasons"]:
        print(f"  {reason}")
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
