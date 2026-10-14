"""Offline regression for P1 terminal resume rows and spawn accounting.

No instances, agents, or model calls are launched. Uses temporary JSONL only.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from eval.harness import scheduler_p1 as SP

IID = "offline-instance"


def _line(arm, verdict, resolved, stage):
    return {"instance_id": IID, "arm": arm, "run_id": "offline-" + arm,
            "verdict_class": verdict, "resolved": resolved, "stage": stage,
            "cmd": ["fake-coding-agent"], "started_at_epoch": 1, "exit_code": 1}


def main():
    with tempfile.TemporaryDirectory(prefix="t29-resume-") as root:
        initial = [_line("A", "agent_result", True, "judged"),
                   _line("B", "timeout", False, "failed_infra"),
                   _line("C", "agent_result", True, "judged")]
        for row in initial:
            SP.append_triple_state(root, row)
        path = SP.triple_state_jsonl(root)
        original = open(path, "rb").read()
        digest = hashlib.sha256(original).hexdigest()
        old_prior = SP.read_triple_state(root)
        old_skipped = [arm for arm in "ABC" if old_prior[(IID, arm)]["stage"] == "judged"]
        old_pending = [arm for arm in "ABC" if arm not in old_skipped]
        assert old_skipped == ["A", "C"] and old_pending == ["B"]
        print(f"BEFORE skipped={old_skipped} pending={old_pending} B=timeout/False")
        # Simulate what the old pending=B path permits, in a second temporary
        # state file, never touching an archived run or the original state.
        with tempfile.TemporaryDirectory(prefix="t29-old-") as old_root:
            with open(SP.triple_state_jsonl(old_root), "wb") as output:
                output.write(original)
            SP.append_triple_state(old_root, _line("B", "agent_result", True, "judged"))
            old_rows = open(SP.triple_state_jsonl(old_root), "rb").read().splitlines()
            assert len(old_rows) == 4
            assert SP.read_triple_state(old_root)[(IID, "B")]["resolved"] is True
            print("BEFORE simulated B=agent_result/True rows=4 (old terminal overwritten)")
        # The fixed planner must reject every terminal row, and must never
        # append or rewrite the initial bytes even in --resume mode.
        skipped, pending, skipped_failed = SP.plan_resume(
            [{"instance_id": IID, "arm": arm} for arm in "ABC"],
            SP.read_triple_state(root))
        assert [s["arm"] for s in skipped] == ["A", "B", "C"]
        assert not pending and skipped_failed == 1
        original_run_root = SP.E.run_root
        SP.E.run_root = lambda _run_id: root
        try:
            manifest = SP.run_p1_batch(run_tag="offline", stamp="test", instance_ids=[IID],
                                       arms_list=["A", "B", "C"], max_workers=1, resume=True)
        finally:
            SP.E.run_root = original_run_root
        assert manifest["skipped_failed"] == 1 and manifest["skipped_completed"] == 2
        after = open(path, "rb").read()
        assert after == original and hashlib.sha256(after).hexdigest() == digest
        print(f"AFTER skipped={[s['arm'] for s in skipped]} pending={pending} "
              f"manifest.skipped_failed={manifest['skipped_failed']} rows=3 "
              f"sha256={digest} unchanged=True")
        # Two actual spawned rows for the same key must count twice, unlike
        # read_triple_state()'s last-row mapping.
        with tempfile.TemporaryDirectory(prefix="t29-count-") as count_root:
            first = _line("B", "timeout", False, "failed_infra")
            first["started_at_epoch"] = 1
            SP.append_triple_state(count_root, first)
            SP.append_triple_state(count_root, _line("B", "agent_result", True, "judged"))
            assert len(SP.read_triple_state(count_root)) == 1
            assert SP.count_spawned_arms(count_root) == 2
            print("BUDGET unique_keys=1 actual_spawn_rows=2 counted=2")
        # Exercise the actual C worker and serialize its returned row. Fake
        # subprocess exits nonzero so no instance/agent/judge is ever run.
        class FakeProcess:
            returncode = 1
            def communicate(self, timeout=None):
                return ("offline child", None)
        with tempfile.TemporaryDirectory(prefix="t29-art-") as run_root:
            old_popen, old_art_dir = SP.subprocess.Popen, SP.E.art_dir
            SP.subprocess.Popen = lambda *a, **kw: FakeProcess()
            SP.E.art_dir = lambda run_id, iid: os.path.join(run_root, iid, "artifacts")
            try:
                task = {"instance_id": IID, "arm": "C", "run_id": "offline-C",
                        "label": "offline-label-C", "workspace": os.path.join(run_root, IID, "repo"),
                        "problem_file": "unused", "extra_args": [], "arm_report_file": "unused",
                        "repo": "offline/repo", "base_commit": "deadbeef"}
                c_row = SP._run_one_arm(task)
                SP.append_triple_state(run_root, c_row)
            finally:
                SP.subprocess.Popen, SP.E.art_dir = old_popen, old_art_dir
            recorded = json.loads(open(SP.triple_state_jsonl(run_root), encoding="utf-8").readline())
            artifact = SP.policy_isolated_artifact(recorded)
            assert artifact in recorded["artifacts"]
            assert os.path.isfile(os.path.join(run_root, artifact))
            assert recorded["verdict_class"] == "infra_error"
            print(f"F6 state_row_index={artifact} worker_generated=True")


if __name__ == "__main__":
    main()
