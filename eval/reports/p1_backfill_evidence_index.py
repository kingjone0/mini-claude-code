"""One-shot, auditable artifacts-only backfill for archived t20 P1 state.

No model calls, no reruns. Refuses mismatched row counts, missing evidence,
pre-existing indexes, or any non-artifacts field change. Saves original bytes
as a .pre-t32 backup next to state before replacing it atomically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime

DEFAULT_STATE = Path(r"E:\aicoding\eval-runs\p1-20261013T122356-t20-batch\triple_state.jsonl")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def backfill(path: Path, *, apply: bool = False) -> dict:
    before = path.read_bytes()
    lines = before.splitlines(keepends=True)
    assert len(lines) == 27, f"expected 27 physical lines, got {len(lines)}"
    changed, output, c_count = [], [], 0
    for number, line in enumerate(lines, 1):
        row = json.loads(line)
        if row["arm"] != "C":
            output.append(line)
            continue
        c_count += 1
        iid = row["instance_id"]
        index = f"{iid}/artifacts/policy_evidence_isolated.json"
        evidence = Path(row["workspace"]).parent / "artifacts" / "policy_evidence_isolated.json"
        assert evidence.is_file(), f"missing per-run evidence: {evidence}"
        assert index not in row["artifacts"], f"already indexed line {number}"
        new = dict(row)
        new["artifacts"] = list(row["artifacts"]) + [index]
        assert {k: v for k, v in new.items() if k != "artifacts"} == {
            k: v for k, v in row.items() if k != "artifacts"}, number
        assert new["artifacts"][:-1] == row["artifacts"]
        changed.append({"line": number, "instance_id": iid,
                        "changed_fields": ["artifacts"], "appended": index})
        output.append((json.dumps(new, ensure_ascii=False) + "\n").encode("utf-8"))
    assert c_count == len(changed) == 9
    after = b"".join(output)
    report = {"timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
              "state": str(path), "before_sha256": sha(before), "after_sha256": sha(after),
              "rows": len(lines), "C_indexed": len(changed), "AB_unchanged": len(lines)-len(changed),
              "field_diffs": changed}
    report["operation"] = "回填（仅追加 artifacts 索引；不是原始跑批时落盘）"
    if apply:
        backup = path.with_name(path.name + ".pre-t32")
        assert not backup.exists(), f"backup already exists: {backup}"
        with open(backup, "xb") as stream:
            stream.write(before)
            stream.flush()
            os.fsync(stream.fileno())
        temp = path.with_name(path.name + ".t32-tmp")
        assert not temp.exists(), f"temp already exists: {temp}"
        with open(temp, "xb") as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        assert sha(path.read_bytes()) == report["after_sha256"]
        assert sha(backup.read_bytes()) == report["before_sha256"]
        report["backup"] = str(backup)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--apply", action="store_true", help="explicit one-time archive update")
    opts = parser.parse_args()
    print(json.dumps(backfill(opts.state, apply=opts.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
