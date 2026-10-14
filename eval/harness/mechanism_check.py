"""机制验证：把四条**写进合同但容易糊过去**的判据各自实测一遍。

这个脚本存在的理由是：`gold_check.py` 只回答「判得对不对」，回答不了下面这些
「判错的时候会怎么错」。四条各自可能在**不报错**的情况下退化：

  ① **超时优先于退出码（§8.4）**：Windows 上被强杀的进程以 exit 1 收尾，与 pytest
     「有测试失败」的 exit 1 不可区分。顺序写反 -> timeout 被记成 not_resolved ->
     分母被污染。这里人为把判定超时压到 1s，验证拿到的是 timeout 而不是 not_resolved。
  ② **测试侧剥离（§5.3）**：agent 改测试骗绿。这里人为构造「只改测试」与
     「改源码 + 顺手改测试」两种候选补丁，验证测试侧被剥离、且被计数。
  ③ **并发隔离（§8.3）**：≥4 并发与串行结果逐条一致，工作区互不干扰。
  ④ **断点续跑（§8.5）**：跑一半杀掉，重跑要跳过已完成、且不改写已完成的结论。

用法：python eval/harness/mechanism_check.py --run-id <id>
退出码：四条全过 = 0。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.dirname(HERE)
PROJECT_ROOT = os.path.dirname(EVAL_DIR)
for p in (PROJECT_ROOT, EVAL_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E                      # noqa: E402
from eval.harness import judge as J                    # noqa: E402
from eval.harness import run_instance as RI            # noqa: E402

# 验证用实例：F2P 数量少、单测快（pallets-click__4fd2fea 的 5 条参数化）
PROBE_INSTANCE = "pallets-click__4fd2fea"
PROBE_INSTANCE_2 = "pytest-dev-pluggy__cbf58d5"
PROBE_INSTANCE_3 = "more-itertools-more-itertools__1b7f033"
PROBE_INSTANCE_4 = "astanin-python-tabulate__9dedd45"


def banner(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------- ① 超时优先

def check_timeout_priority(run_id):
    """① 人为把判定**拖长** + 把超时压低 => 必须是 timeout，不能是 not_resolved。

    ⚠ 两次踩坑记录（都是「看起来验证过了其实没有」）：
      1. 传空补丁 => 在 `empty_patch` 分支提前返回，根本没跑 pytest；
      2. 只把超时压到 1s => 该实例判定只花 0.73s，**跑完了**，压根没超时。
    => 必须**同时**做到：给一个真会被施加的补丁 + 让判定真的跑得比超时久。
    这里用 `--timeout-probe` 之外再给一个「拖长测试」的候选补丁：
    在测试侧注入一个 `time.sleep()`。但测试侧会被 §5.3 剥离 —— 所以拖长必须发生在
    **源码侧**：给 `collection_scope` 之外的一个源码文件加一个模块级 sleep。
    最稳的做法是直接把判定超时压到**极小**（0.05s），任何真实 pytest 启动都跑不完。
    """
    banner("① 超时必须先于退出码判定（预注册 §8.4）")
    rid = f"{run_id}-timeout"
    inst = RI.load_instance(PROBE_INSTANCE)
    # 直接把超时压到 0.05s：pytest 光启动就要几百毫秒，必然被杀。
    # 这样验证的是**分类顺序**（timed_out 标记 vs 退出码），与实例内容无关。
    row = RI.run_one(PROBE_INSTANCE, inst["patch"], run_id=rid, label="mech-timeout",
                     patch_kind="gold", timeout_probe=True, judge_timeout=1)
    art = E.art_dir(rid, PROBE_INSTANCE)
    ev = os.path.join(art, "verdict_run.txt")
    head = E.read_text(ev).splitlines()[:3] if os.path.exists(ev) else []
    print(f"  判定产物：{ev}")
    for line in head:
        print(f"    {line[:200]}")
    ok = (row["verdict_class"] == J.TIMEOUT and row["resolved"] is False
          and row.get("timed_out") is True)
    print(f"  verdict_class = {row['verdict_class']}（期望 timeout）")
    print(f"  timed_out 标记 = {row.get('timed_out')}（期望 True）")
    print(f"  exit_code = {row.get('exit_code')}（被杀 => 1，与 pytest「有失败」同值）")
    print(f"  resolved = {row['resolved']}（期望 False，但**不计入分母**）")
    print(f"  计入 resolved 分母？{row['verdict_class'] in J.COUNTS_IN_DENOMINATOR}（期望 False）")
    print(f"  计作 agent 失败？{row['verdict_class'] in J.COUNTS_AS_AGENT_FAILURE}（期望 False）")
    print("\n  * 关键对照（同一个 exit_code，不同分类）：")
    print("     timeout  : timed_out=True  -> 先查超时 => verdict_class=timeout，不计分母")
    print("     not_res  : timed_out=False -> 再解释退出码 => 按逐 node 结果二分")
    return ok, row


# ---------------------------------------------------------------- ② 测试侧剥离

def check_test_stripping(run_id):
    """② 候选补丁改测试 => 剥离后再判定，并单列统计。"""
    banner("② agent 改测试文件的处置（预注册 §5.3）")
    rid = f"{run_id}-strip"
    inst = RI.load_instance(PROBE_INSTANCE)
    test_paths = J.patch_touched_paths(inst["test_patch"])
    print(f"  该实例的测试侧文件：{test_paths}")

    results = {}

    # (a) 只改测试：把测试文件里断言的目标值改掉，试图「骗绿」
    #     最朴素也最常见的作弊：往测试文件里插一行 skip 或改断言。
    ws_probe = os.path.join(E.run_root(rid), "_probe")
    only_test_patch = (
        "diff --git a/tests/test_options.py b/tests/test_options.py\n"
        "--- a/tests/test_options.py\n"
        "+++ b/tests/test_options.py\n"
        "@@ -1,3 +1,4 @@\n"
        "+# eval 机制验证：这一行在测试侧，必须被剥离\n"
        " import re\n"
        "\n"
        " import pytest\n"
    )
    row_a = RI.run_one(PROBE_INSTANCE, only_test_patch, run_id=rid, label="mech-strip-onlytest",
                       patch_kind="file:only_test")
    results["only_test"] = row_a
    print(f"\n  (a) 只改测试文件的候选补丁：")
    print(f"      touched_tests = {row_a['touched_tests']}（期望 True）")
    print(f"      test_side_paths = {row_a['test_side_paths']}")
    print(f"      剥离后补丁长度 = {len(row_a.get('judged_patch_sha256') or '')} / "
          f"judged_patch_paths = {row_a.get('judged_patch_paths')}")
    print(f"      verdict_class = {row_a['verdict_class']}（期望 empty_patch：剥光后为空）")
    print(f"      resolved = {row_a['resolved']}（期望 False）")
    a_ok = (row_a["touched_tests"] is True and row_a["verdict_class"] == J.EMPTY_PATCH
            and row_a["resolved"] is False
            and row_a.get("judged_patch_paths") == [])

    # (b) 改源码 + 顺手改测试：源码侧应保留、测试侧应剥离
    mixed = inst["patch"] + only_test_patch
    row_b = RI.run_one(PROBE_INSTANCE, mixed, run_id=rid, label="mech-strip-mixed",
                       patch_kind="file:mixed")
    results["mixed"] = row_b
    print(f"\n  (b) gold 源码改动 + 顺手改测试的候选补丁：")
    print(f"      touched_tests = {row_b['touched_tests']}（期望 True）")
    print(f"      judged_patch_paths = {row_b.get('judged_patch_paths')}（期望只剩源码侧）")
    print(f"      剥离掉的测试侧 SHA-256 = {row_b.get('stripped_test_patch_sha256')}")
    print(f"      verdict_class = {row_b['verdict_class']}（期望 agent_result）")
    print(f"      resolved = {row_b['resolved']}（源码侧是真的 gold => 期望 True）")
    b_ok = (row_b["touched_tests"] is True
            and row_b["resolved"] is True
            and row_b["verdict_class"] == J.AGENT_RESULT
            and all(J.is_test_path(p) is False for p in (row_b.get("judged_patch_paths") or []))
            and bool(row_b.get("stripped_test_patch_sha256")))

    # (c) 反向对照：把「改测试」当成正常补丁施加（证明不剥离的话会骗绿）
    #     这里不真的施加，只用 stripped_patch 展示差异，保证上面结论可解释。
    kept, test_side, _dropped, touched, tpaths = J.stripped_patch(mixed)
    print(f"\n  (c) 判据自证（不剥离会怎样）：")
    print(f"      原始补丁涉及 {len(J.patch_touched_paths(mixed))} 个文件，"
          f"其中测试侧 {len(tpaths)} 个：{tpaths}")
    print(f"      剥离后只剩 {len(J.patch_touched_paths(kept))} 个：{J.patch_touched_paths(kept)}")
    c_ok = len(tpaths) >= 1 and len(J.patch_touched_paths(kept)) >= 1

    if os.path.isdir(ws_probe):
        shutil.rmtree(ws_probe, ignore_errors=True)
    return (a_ok and b_ok and c_ok), results


# ---------------------------------------------------------------- ③ 并发隔离

def check_concurrency_isolation(run_id, max_workers=4):
    """③ ≥4 并发 vs 串行：逐条结论一致。"""
    banner(f"③ 并发隔离（预注册 §8.3）：串行 vs {max_workers} 并发")
    from eval.harness import scheduler as SCH

    ids = [PROBE_INSTANCE, PROBE_INSTANCE_2, PROBE_INSTANCE_3, PROBE_INSTANCE_4]
    ser_id, par_id = f"{run_id}-serial", f"{run_id}-parallel"

    print(f"  实例：{ids}")
    print("\n  --- 串行（max_workers=1）---")
    m_ser = SCH.run_batch(ser_id, ids, max_workers=1, label="serial", patch_kind="gold",
                          resume=False, keep_workspace=False)
    print("\n  --- 并发（max_workers=%d）---" % max_workers)
    m_par = SCH.run_batch(par_id, ids, max_workers=max_workers, label="parallel",
                          patch_kind="gold", resume=False, keep_workspace=False)

    ser = {r["instance_id"]: r for r in m_ser["instances"]}
    par = {r["instance_id"]: r for r in m_par["instances"]}
    print("\n  逐条比对：")
    all_ok = True
    for iid in ids:
        s, p = ser.get(iid, {}), par.get(iid, {})
        same = (s.get("verdict_class") == p.get("verdict_class")
                and s.get("resolved") == p.get("resolved"))
        all_ok &= same
        print(f"    {'OK ' if same else 'DIFF'} {iid}")
        print(f"         串行 {s.get('verdict_class')}/{s.get('resolved')}   "
              f"并发 {p.get('verdict_class')}/{p.get('resolved')}")
    # ⚠ D2（t4 报的「空证」，t8 修）：原来的断言是
    #     ws = [p["instance_id"] and E.ws_path(par_id, p["instance_id"]) ...]
    #     all_ok &= (len(set(ws)) == len(ws))
    # `ws_path()` 只是按 `run_id + instance_id` **拼字符串** ⇒ 不同实例天然得到不同字符串，
    # 这条断言**恒为真**，与「工作区在磁盘上是否真的隔离」没有任何逻辑联系。
    # t3 曾把它当「并发隔离」的通过证据 —— 那是空证。
    #
    # 换成**真实磁盘路径比较**，且要能证明「这些路径确实各自独立存在」：
    #   ① 归一化（realpath + normcase）后互异；
    #   ② realpath 必须落在**本批次的 run 目录**下（防止两个不同字符串其实是同一实体）；
    #   ③ .pth 挂载点必须指向各自的工作区（不是只看跑通了 —— 这正是静默误判的藏身处）。
    real_ws = []
    for r in m_par["instances"]:
        iid = r["instance_id"]
        w = E.ws_path(par_id, iid)
        real = os.path.realpath(w)
        real_ws.append((iid, real, os.path.isdir(w)))
    normed = [os.path.normcase(r[1]) for r in real_ws]
    distinct = len(set(normed)) == len(normed)
    all_exist = all(r[2] for r in real_ws)
    under_run = all(os.path.normcase(os.path.realpath(E.run_root(par_id))).rstrip("\\/")
                    in n for n in normed)
    print(f"\n  工作区**真实磁盘路径**互异：{distinct}"
          f"（{len(set(normed))} 个互异 / {len(normed)} 条实例）")
    for iid, real, exists in real_ws:
        print(f"      {iid}: {real}  存在={exists}")
    print(f"  全部真实存在：{all_exist}；全部落在本批次 run 目录下：{under_run}")

    # ③ 挂载点归属：每个实例的 .pth 必须指向**它自己**的工作区
    mount_ok, mount_detail = _verify_mount_ownership(par_id, ids)
    print(f"  挂载点归属正确（每个实例的 .pth 指向自己的工作区）：{mount_ok}")
    for line in mount_detail:
        print(f"      {line}")

    all_ok &= distinct and all_exist and under_run and mount_ok
    print(f"  汇总一致：串行 {m_ser['summary']['counts']} | 并发 {m_par['summary']['counts']}")
    return all_ok, (m_ser, m_par)


def _verify_mount_ownership(run_id: str, ids):
    """核对每条实例的 venv 里，**本 run 的** `.pth` 是否指向**本批次它的工作区**。

    这是 D1 修复后真正的隔离判据：不看「跑通了」，而是看**挂载点指向哪**。
    修复后每个 (实例, run) 有自己的 venv ⇒ 该 venv 里应当**只有本 run 这一个**
    editable 挂载点，且它必须指向本 run 的工作区。多出一个就说明共享没消干净。
    """
    detail = []
    ok = True
    for iid in ids:
        try:
            inst = RI.load_instance(iid)
        except Exception as exc:  # noqa: BLE001
            detail.append(f"{iid}: 装载失败 {exc}")
            ok = False
            continue
        ws = os.path.normcase(os.path.realpath(E.ws_path(run_id, iid)))
        vd = E.venv_dir(inst, run_id)
        sp = os.path.join(vd, "Lib", "site-packages")
        own = E.editable_pth_name(iid, run_id)
        found = []
        others = []
        if os.path.isdir(sp):
            for f in sorted(os.listdir(sp)):
                if not f.endswith(".pth"):
                    continue
                with open(os.path.join(sp, f), encoding="utf-8") as fh:
                    body = fh.read()
                if f == own:
                    for tok in body.split("'")[1::2]:
                        found.append((f, os.path.normcase(os.path.realpath(tok))))
                elif f.startswith("_eval_editable_"):
                    others.append(f)
        if not found:
            detail.append(f"{iid}: venv 里没有本 run 的挂载点 {own}（venv={vd}）")
            ok = False
            continue
        # ① 挂载必须指向本 run 的工作区
        good = any(t == ws or t.startswith(ws + os.sep) for _f, t in found)
        # ② 本 venv 里**不应**再有同 harness 的其它 editable 挂载（共享没消干净）
        clean = not others
        ok &= good and clean
        detail.append(f"{iid}: {'OK' if (good and clean) else 'BAD'} "
                      f"venv={vd} 指向={found[:1]} 其它editable挂载={others or '(无)'}")
    return ok, detail


# ---------------------------------------------------------------- ④ 断点续跑

def check_resume(run_id):
    """④ 跑一半杀掉 => 重跑跳过已完成、不改写已完成结论。"""
    banner("④ 断点续跑（预注册 §8.5）")
    from eval.harness import scheduler as SCH

    rid = f"{run_id}-resume"
    ids = [PROBE_INSTANCE, PROBE_INSTANCE_2, PROBE_INSTANCE_3, PROBE_INSTANCE_4]

    # 分两批：先只跑前 2 条（模拟「跑了一半」）
    first_two = ids[:2]
    m1 = SCH.run_batch(rid, first_two, max_workers=1, label="phase1", patch_kind="gold",
                       resume=False, keep_workspace=False)
    state = SCH.read_state(rid)
    done1 = [i for i in first_two if state.get(i, {}).get("stage") == "judged"]
    print(f"  阶段 1：完成 {len(done1)} 条（{done1}）")
    hash_before = {i: json.dumps(state[i], sort_keys=True, ensure_ascii=False)
                   for i in done1}

    # 再跑全部 4 条，--resume：前 2 条应被跳过
    m2 = SCH.run_batch(rid, ids, max_workers=1, label="phase2", patch_kind="gold",
                       resume=True, keep_workspace=False)
    state2 = SCH.read_state(rid)
    done2 = [i for i in ids if state2.get(i, {}).get("stage") == "judged"]
    hash_after = {i: json.dumps(state2[i], sort_keys=True, ensure_ascii=False)
                  for i in done1}
    print(f"  阶段 2（--resume）：总完成 {len(done2)} 条（{done2}）")
    print(f"  跳过计数 skipped_completed = {m2['skipped_completed']}（期望 {len(done1)}）")

    untouched = all(hash_before.get(i) == hash_after.get(i) for i in done1)
    # state.jsonl 里对已完成实例**只应有一条 judged 记录**（不被重写/追加新结论）
    lines = [json.loads(l) for l in E.read_text(SCH.state_jsonl(rid)).splitlines() if l.strip()]
    per = {}
    for r in lines:
        per.setdefault(r["instance_id"], []).append(r.get("stage"))
    rewrites = {i: v for i, v in per.items() if i in done1 and v.count("judged") != 1}
    print(f"  已完成实例的结论未被改写：{untouched}")
    print(f"  已完成实例在 state.jsonl 里各只有一条 judged：{not rewrites}"
          + (f"  异常={rewrites}" if rewrites else ""))
    print(f"  「中断前 {len(done1)} 条完成 / 恢复后总计 {len(done2)} 条」")

    ok = (len(done1) == 2 and len(done2) == 4 and m2["skipped_completed"] == 2
          and untouched and not rewrites)
    return ok, {"phase1_done": done1, "phase2_total": done2,
                "skipped": m2["skipped_completed"]}


# ---------------------------------------------------------------- 主流程

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="机制验证（超时/剥离/并发/续跑）")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--max-workers", type=int, default=4)
    args = ap.parse_args(argv)
    run_id = args.run_id or ("mech-" + time.strftime("%Y%m%d-%H%M%S"))

    print("=" * 78)
    print(f"机制验证  |  run_id={run_id}")
    print(f"实例：{PROBE_INSTANCE} / {PROBE_INSTANCE_2} / {PROBE_INSTANCE_3} / "
          f"{PROBE_INSTANCE_4}")
    print("=" * 78)

    results = {}
    ok1, r1 = check_timeout_priority(run_id)
    results["timeout_priority"] = ok1
    ok2, r2 = check_test_stripping(run_id)
    results["test_stripping"] = ok2
    ok3, r3 = check_concurrency_isolation(run_id, args.max_workers)
    results["concurrency_isolation"] = ok3
    ok4, r4 = check_resume(run_id)
    results["resume"] = ok4

    banner("机制验证汇总")
    for k, v in results.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print(f"\n证据根目录：{E.run_root(run_id)}")
    summary = {"run_id": run_id, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "prereg": "eval/PREREGISTRATION.md §5.3 / §8.3 / §8.4 / §8.5",
               "results": results}
    E.write_text(os.path.join(E.run_root(run_id), "mechanism_check_summary.json"),
                 json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"结论 JSON：{os.path.join(E.run_root(run_id), 'mechanism_check_summary.json')}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())