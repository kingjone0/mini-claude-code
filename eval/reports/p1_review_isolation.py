"""t15 验收②：同实例并发隔离 —— 自有反例 + 把 `(实例, run)` 隔离公式还原成修复前。

离线、零模型调用。全部在**临时** RUNS_ROOT / VENV_ROOT 里做，不碰任何历史产物。

A. 当前公式（D1 修复后）：同实例两个 run **并发** 3 轮
   判据：0 infra（无异常）、两个 run 的 venv 目录互异、每个 venv 里只有本 run 的
   editable `.pth`、该 `.pth` 指向本 run 的工作区、并发结束后再核一次 import 仍解析到
   本 run 工作区（**0 静默误判**）。

B. 反向复现（把公式还原成修复前）：
   venv_dir 还原成 `<repo>-<base_commit[:8]>`（不含 run_id），
   editable_pth_name 还原成 `_eval_editable_<sha256(instance_id)[:16]>.pth`（不含 run_id）。
   判据：两个 run 落到**同一个 venv / 同一个 .pth**，且先准备的那个 run 的 import 解析被
   后准备的那个 run 抢走（静默误判）或直接被自检拦成 InfraError —— 两者都算缺陷复现。

C. 还原公式后按纯函数复核：两个 run_id 又映射到互异的 venv 与互异的 .pth 名。

输出：eval/reports/p1-review-isolation.txt / .json
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

ROOT = r"E:\aicoding\mini-claude-code"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

from eval.harness import env as E             # noqa: E402
from eval.harness import run_instance as RI   # noqa: E402

IID = "astanin-python-tabulate__5373ebf"
ROUNDS = 3
OUT_TXT = os.path.join(ROOT, "eval", "reports", "p1-review-isolation.txt")
OUT_JSON = os.path.join(ROOT, "eval", "reports", "p1-review-isolation.json")
LINES: list[str] = []
R: dict = {"instance_id": IID, "rounds": [], "reverse": {}, "checks": {}}
TMP = None


def say(s=""):
    LINES.append(s)


def prepare(run_id):
    """复刻 scheduler_p1 的准备链：fresh_workspace -> ensure_venv（含 .pth 挂载与自检）。"""
    inst = RI.load_instance(IID)
    art = E.art_dir(run_id, IID)
    os.makedirs(art, exist_ok=True)
    ws = E.fresh_workspace(inst, run_id, log_path=os.path.join(art, "workspace.log"))
    inst["_ws"] = ws
    deps = RI.deps_from_env_spec(inst)
    pkg = RI.infer_import_name(ws)
    py = E.ensure_venv(inst, os.path.join(art, "venv.log"), deps, pkg, run_id=run_id)
    return {"run_id": run_id, "ws": ws, "py": py, "venv": E.venv_dir(inst, run_id),
            "pth": E.editable_pth_path(inst, run_id), "pkg": pkg, "art": art,
            "inst": inst}


def mount_audit(rec):
    """核对这个 run 的 venv 里挂载点归属：必须只有本 run 的 .pth，且指向本 run 的工作区。"""
    inst = rec["inst"]
    sp = os.path.join(rec["venv"], "Lib", "site-packages")
    own = os.path.basename(rec["pth"])
    same_ws = os.path.normcase(os.path.realpath(rec["ws"]))
    found, others, targets = [], [], []
    if os.path.isdir(sp):
        for f in sorted(os.listdir(sp)):
            if not f.endswith(".pth"):
                continue
            body = io.open(os.path.join(sp, f), encoding="utf-8").read()
            if f == own:
                found.append(f)
                targets = [os.path.normcase(os.path.realpath(tok))
                           for tok in body.split("'")[1::2]]
            elif f.startswith("_eval_editable_"):
                others.append(f)
    ok_own = bool(found) and any(t == same_ws or t.startswith(same_ws + os.sep)
                                 for t in targets)
    return {"own_pth": own, "own_found": bool(found), "other_editable_pth": others,
            "targets": targets, "points_to_own_ws": ok_own,
            "only_own_editable": not others}


def import_owner(rec):
    """当前磁盘状态下的 import 归属（独立于 ensure_venv 当时的自检）。"""
    try:
        resolved = E.assert_pkg_from_workspace(rec["py"], rec["ws"], rec["pkg"])
        return {"ok": True, "resolved": resolved}
    except E.InfraError as exc:
        return {"ok": False, "error": str(exc)}


def round_current(r):
    tag = f"p1-review-t15-iso-r{r}"
    ra, rb = f"{tag}-A", f"{tag}-B"
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa, fb = pool.submit(prepare, ra), pool.submit(prepare, rb)
        out = {"a": None, "b": None, "errors": []}
        for key, fut in (("a", fa), ("b", fb)):
            try:
                out[key] = fut.result()
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"{key}: {type(exc).__name__}: {exc}")
    rec = {"round": r, "errors": out["errors"]}
    if out["errors"]:
        rec["verdict"] = "INFRA"
        R["rounds"].append(rec)
        say(f"### 轮 {r}: INFRA（{out['errors']}）")
        return rec
    a, b = out["a"], out["b"]
    rec["venv_distinct"] = os.path.normcase(a["venv"]) != os.path.normcase(b["venv"])
    rec["pth_distinct"] = os.path.basename(a["pth"]) != os.path.basename(b["pth"])
    rec["mount_a"] = mount_audit(a)
    rec["mount_b"] = mount_audit(b)
    rec["import_after_concurrency"] = {"a": import_owner(a), "b": import_owner(b)}
    rec["verdict"] = "PASS" if (rec["venv_distinct"] and rec["pth_distinct"]
                                and rec["mount_a"]["points_to_own_ws"]
                                and rec["mount_b"]["points_to_own_ws"]
                                and rec["mount_a"]["only_own_editable"]
                                and rec["mount_b"]["only_own_editable"]
                                and rec["import_after_concurrency"]["a"]["ok"]
                                and rec["import_after_concurrency"]["b"]["ok"]) else "FAIL"
    R["rounds"].append(rec)
    say()
    say(f"### 轮 {r}: {rec['verdict']}（并发 2，同实例）")
    say(f"- venv A = {a['venv']}")
    say(f"- venv B = {b['venv']}")
    say(f"- venv 互异 = {rec['venv_distinct']}；.pth 名互异 = {rec['pth_distinct']}")
    say(f"- A 挂载：{json.dumps(rec['mount_a'], ensure_ascii=False)}")
    say(f"- B 挂载：{json.dumps(rec['mount_b'], ensure_ascii=False)}")
    say(f"- 并发结束后再核 import：A={json.dumps(rec['import_after_concurrency']['a'], ensure_ascii=False)[:200]}")
    say(f"                        B={json.dumps(rec['import_after_concurrency']['b'], ensure_ascii=False)[:200]}")
    return rec


def reverse_reproduce():
    """把隔离公式还原成修复前，复现「两个 run 抢同一个挂载位」。"""
    say()
    say("## B 反向复现：把 (实例, run) 隔离公式还原成修复前")
    orig_venv_dir, orig_pth_name = E.venv_dir, E.editable_pth_name

    def prefixed_venv_dir(instance, run_id=""):
        base = f"{E.repo_key_of(instance['repo'])}-{E.venv_tag(instance['base_commit'])}"
        return os.path.join(E.VENV_ROOT, base)          # 修复前：不含 run_id

    def prefixed_pth_name(instance_id, run_id):
        return f"_eval_editable_{E.sha256_text(instance_id)[:16]}.pth"   # 修复前：不含 run_id

    E.venv_dir = prefixed_venv_dir
    E.editable_pth_name = prefixed_pth_name
    try:
        say("- 已还原为修复前公式：venv_dir=<repo>-<base[:8]>；"
            "editable_pth_name=_eval_editable_<sha256(instance_id)[:16]>.pth")
        ra = "p1-review-t15-pre-A"
        rb = "p1-review-t15-pre-B"
        rec = {}
        try:
            a = prepare(ra)
            a_import_before = import_owner(a)
            rec["venv_a"] = a["venv"]
            rec["pth_a"] = a["pth"]
        except Exception as exc:  # noqa: BLE001
            rec["a_prepare_error"] = f"{type(exc).__name__}: {exc}"
            R["reverse"] = rec
            say(f"- A 准备即失败：{rec['a_prepare_error']}")
            say("- ⇒ 缺陷以「响亮的 infra」形式复现")
            return rec
        try:
            b = prepare(rb)
            rec["venv_b"] = b["venv"]
            rec["pth_b"] = b["pth"]
            rec["b_prepare_error"] = None
        except Exception as exc:  # noqa: BLE001
            rec["b_prepare_error"] = f"{type(exc).__name__}: {exc}"
            b = None
        rec["same_venv"] = (rec.get("venv_b") is not None
                            and os.path.normcase(rec["venv_a"]) == os.path.normcase(rec["venv_b"]))
        rec["same_pth"] = (rec.get("pth_b") is not None
                           and os.path.normcase(rec["pth_a"]) == os.path.normcase(rec["pth_b"]))
        rec["a_import_before_b"] = a_import_before
        # 后准备的 B 覆盖了同一个 .pth ⇒ A 的 import 应该被抢走
        a_import_after = import_owner(a)
        rec["a_import_after_b"] = a_import_after
        rec["mount_a_after_b"] = mount_audit(a)
        # 是否真的抢走：解析到的路径是否落在 A 自己的工作区之外
        cross = False
        if a_import_after.get("ok"):
            resolved = os.path.normcase(os.path.realpath(a_import_after["resolved"]))
            ws_a = os.path.normcase(os.path.realpath(a["ws"]))
            cross = not (resolved == ws_a or resolved.startswith(ws_a + os.sep))
            rec["silent_crosstalk"] = cross
            rec["resolved_for_a"] = a_import_after["resolved"]
            rec["ws_a"] = a["ws"]
            rec["ws_b"] = b["ws"] if b else None
        else:
            rec["silent_crosstalk"] = False
            rec["loud_infra"] = a_import_after.get("error")
        rec["defect_reproduced"] = bool(rec["same_venv"] and rec["same_pth"]
                                        and (rec.get("silent_crosstalk") or rec.get("loud_infra")
                                             or rec.get("b_prepare_error")))
        R["reverse"] = rec
        say(f"- venv A/B：{rec['venv_a']} | {rec.get('venv_b')} ⇒ 同一 venv = {rec['same_venv']}")
        say(f"- .pth A/B：{rec['pth_a']} | {rec.get('pth_b')} ⇒ 同一 .pth = {rec['same_pth']}")
        say(f"- A 的 import（B 之前）：{json.dumps(a_import_before, ensure_ascii=False)[:180]}")
        say(f"- A 的 import（B 之后）：{json.dumps(a_import_after, ensure_ascii=False)[:300]}")
        say(f"- B 准备结果：{rec.get('b_prepare_error') or '成功'}")
        say(f"- 静默误判（A 被解析到 B 的工作区）：{rec.get('silent_crosstalk')}")
        say(f"- 缺陷复现 = {rec['defect_reproduced']}")
        if b is not None:
            say(f"- B 自己的 import 归属：{json.dumps(import_owner(b), ensure_ascii=False)[:200]}")
    finally:
        E.venv_dir, E.editable_pth_name = orig_venv_dir, orig_pth_name
        say()
        say("## C 还原后复核（纯函数）")
        try:
            inst = RI.load_instance(IID)
            v1, v2 = E.venv_dir(inst, "run-1"), E.venv_dir(inst, "run-2")
            p1, p2 = (E.editable_pth_name(IID, "run-1"), E.editable_pth_name(IID, "run-2"))
            R["checks"]["restored_distinct_venv"] = os.path.normcase(v1) != os.path.normcase(v2)
            R["checks"]["restored_distinct_pth"] = p1 != p2
            R["checks"]["restored_venv_dir_is_original"] = E.venv_dir is orig_venv_dir
            R["checks"]["restored_pth_name_is_original"] = E.editable_pth_name is orig_pth_name
            say(f"- 还原后 venv_dir 为原函数 = {E.venv_dir is orig_venv_dir}；"
                f"editable_pth_name 为原函数 = {E.editable_pth_name is orig_pth_name}")
            say(f"- 两个 run_id 映射到互异 venv = {R['checks']['restored_distinct_venv']}；"
                f"互异 .pth = {R['checks']['restored_distinct_pth']}")
        except Exception as exc:  # noqa: BLE001
            R["checks"]["restore_error"] = str(exc)
            say(f"- 还原复核异常：{exc}")
    return R["reverse"]


def main():
    global TMP
    TMP = tempfile.mkdtemp(prefix="t15-iso-")
    E.RUNS_ROOT = os.path.join(TMP, "runs")
    E.VENV_ROOT = os.path.join(TMP, "venvs")
    os.makedirs(E.RUNS_ROOT, exist_ok=True)
    os.makedirs(E.VENV_ROOT, exist_ok=True)

    say("# t15 验收②：同实例并发隔离（自有反例 + 反向复现）")
    say()
    say(f"临时 RUNS_ROOT={E.RUNS_ROOT}")
    say(f"临时 VENV_ROOT={E.VENV_ROOT}")
    say(f"实例={IID}；并发 2；轮数={ROUNDS}")
    say()
    say("## A 当前公式：同实例两个 run 并发 3 轮")
    for r in range(1, ROUNDS + 1):
        round_current(r)

    reverse_reproduce()

    ok_rounds = [r for r in R["rounds"] if r.get("verdict") == "PASS"]
    R["checks"]["all_rounds_pass"] = len(ok_rounds) == ROUNDS
    R["checks"]["zero_infra"] = all(not r.get("errors") for r in R["rounds"])
    R["checks"]["reverse_defect_reproduced"] = bool(R["reverse"].get("defect_reproduced"))

    say()
    say("## 小结")
    for k, v in R["checks"].items():
        say(f"- {k}: {v}")

    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(R, ensure_ascii=False, indent=2) + "\n")
    with io.open(OUT_TXT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(LINES) + "\n")
    print("isolation: " + " ".join(f"{k}={'PASS' if v else 'FAIL'}"
                                   for k, v in R["checks"].items()))
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if all(R["checks"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
