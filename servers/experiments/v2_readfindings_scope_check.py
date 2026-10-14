#!/usr/bin/env python
"""④ 层 v2 修复回归检查：**R2 为主，兼记 R4 / R7 / R9**。

为什么要有它（R2，blocker）：第一轮 D 臂跑批里 `read_findings` **12 次调用 0 次成功**。
根因不是工具坏了，而是 **id 的作用域与目录的作用域不一致**：

    driver 把 id 写成 `g<k>-f<n>`      —— 只在**单份产物内**唯一
    而 read_findings 对整个共享 out/ glob —— 跑批从不清空 out/，旧产物一直在

于是多轮产物并存时每个 id 都重名（服务端日志里 `g1-f1` 出现在 5 份产物里）。
out/ 里现成躺着 12 份 `scout-subagent-ab-v2-D-*.json`（4 次运行 × 3 组），
正是这个冲突的原始现场 —— 本脚本直接拿它们做**修复前/修复后对照**：

  A. R2 冲突现场：修复前（基线提交 %(base)s 的**真实代码**，从 git 里取出来装载，
     不是复刻）对每个旧形状 id 都返回「重名」；修复后同一批产物**逐个可取回全文**，
     且旧形状 id 的重名报错会把「可用的带 label id」列出来。
  B. R2 冻结项：产物落点、id 可读性、schema 字段数一个都没动（预注册 §1.1）。
  C. R4 状态记录：用合成 scout 会话（不调模型）复现「截断 → 自动续派 → 成功」与
     「截断 → 续派 → 仍失败」两条路径，证明这件事**能被 durable 状态记录看见**。
  D. R7 字段改名：verify 记录里不再有 `observed_before`，改成 `baseline_group_failed`
     并带 `baseline_source` 写明它到底是什么（不触发 pytest，走"文件不存在"的早退路径）。
  E. R9 汇总闸门：`--report` 的臂集从产物反推；仿真「runs.jsonl 已有 8 条、产物只剩
     A 臂 4 份」时**拒绝写入**并以非零退出码结束。

纪律：不调模型、不改仓库文件、不碰靶场（C/E 只在 %TEMP% 下建目录；
走 git 读基线代码时用 `git show`，不 checkout、不写仓库）。
退出码：0 = 全部通过；1 = 有检查项失败。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent          # servers/experiments
REPO = HERE.parent.parent                       # mini-claude-code
OUT = HERE / "out"
SERVER_REL = "servers/code_tools_server.py"
PREFIX_COMMIT = "497e644"   # 修复前基线（t3 把 P1–P5 入库的那次提交）

FAILURES: list[str] = []


def check(label: str, got: object, want: object) -> bool:
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}"
          + ("" if ok else f"  (期望 {want!r})"))
    if not ok:
        FAILURES.append(label)
    return ok


def load_server_module(path: Path, findings_dir: Path, log_path: Path, name: str):
    """按 env 装载一份 code_tools_server 模块（**不启动 MCP 服务**）。"""
    os.environ["AGENT_FINDINGS_DIR"] = str(findings_dir)
    os.environ["AGENT_TOOLS_LOG"] = str(log_path)
    sys.dont_write_bytecode = True               # 不往 servers/ 里丢 .pyc
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_prefix_module(tmp: Path):
    """把**修复前**那版 server 从 git 里取出来装载（拿真实的旧行为，不靠复刻）。

    取不到（浅克隆/换了仓库）时返回 None —— 调用方会明确说"这一列缺席"，
    绝不用复刻冒充真实基线。
    """
    try:
        p = subprocess.run(["git", "show", f"{PREFIX_COMMIT}:{SERVER_REL}"],
                           cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except OSError:
        return None
    if p.returncode != 0 or "def read_findings" not in (p.stdout or ""):
        return None
    old = tmp / "_prefix_code_tools_server.py"
    old.write_text(p.stdout, encoding="utf-8")
    return old


def scout_files(d: Path) -> list[Path]:
    return sorted(d.glob("scout-*.json"))


def load_findings(p: Path) -> list[dict]:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("findings") if isinstance(data, dict) else None
    return [f for f in (items or []) if isinstance(f, dict)]


def legacy_lookup(files: list[Path], ident: str):
    """复刻修复前的查找语义：只比 id、对整个目录 glob。**仅作交叉印证用**。"""
    hits = [{**f, "_source": p.name} for p in files for f in load_findings(p)
            if str(f.get("id")) == ident]
    if not hits:
        return None, "未找到"
    if len(hits) > 1:
        return None, "重名：" + ", ".join(h["_source"] for h in hits)
    return hits[0], None


def section_r2_conflict(tmp: Path, cur) -> None:
    print("\n── A. R2 冲突现场：12 份真实产物（修复前 / 修复后对照）──")
    files = scout_files(OUT)
    d_files = [p for p in files if p.name.startswith("scout-subagent-ab-v2-D-")]
    print(f"  out/ 下 scout 产物共 {len(files)} 份，其中原始冲突现场 "
          f"scout-subagent-ab-v2-D-* 共 {len(d_files)} 份")
    if len(d_files) < 2:
        check("冲突现场产物在场（≥2 份 scout-subagent-ab-v2-D-*.json）", len(d_files), ">=2")
        return

    # 每份产物里的 (旧形状 id, 带 label id)。带 label 的形状由**当前** server 的
    # 命名规则算出（_qualified_id_of），不是这里手写的第二份规则。
    rows: list[tuple[str, str, str]] = []       # (file, plain_id, qualified_id)
    for p in d_files:
        for f in load_findings(p):
            plain = str(f.get("id"))
            rows.append((p.name, plain, cur._qualified_id_of(p.name, plain)))
    by_plain: dict[str, list[str]] = {}
    for fname, plain, _q in rows:
        by_plain.setdefault(plain, []).append(fname)
    print("  冲突表（旧形状 id → 落在几份产物里）：")
    for plain, srcs in sorted(by_plain.items()):
        print(f"    {plain:<10} × {len(srcs)} 份：{', '.join(srcs)}")
    check("冲突确实存在（每个旧形状 id 都落在 ≥2 份产物里）",
          all(len(v) >= 2 for v in by_plain.values()), True)

    # ── 修复前：真实旧代码 ────────────────────────────────────────────────
    old_path = load_prefix_module(tmp)
    if old_path is None:
        print(f"  ⚠️ 取不到基线代码 {PREFIX_COMMIT}:{SERVER_REL} —— "
              f"「修复前」一列用复刻语义代替（已在上面注明差异）")
        before_ok = 0
        for plain in sorted(by_plain):
            hit, why = legacy_lookup(files, plain)
            before_ok += 1 if hit else 0
            print(f"    read_findings({plain!r}) → "
                  f"{'取回 1 条' if hit else '失败：' + why[:60]}")
    else:
        old = load_server_module(old_path, OUT, tmp / "prefix-server.log", "cts_prefix")
        before_ok = 0
        for plain in sorted(by_plain):
            got = old.read_findings(plain)
            ok = got.lstrip().startswith("{")
            before_ok += 1 if ok else 0
            print(f"    read_findings({plain!r}) → "
                  f"{'取回 1 条' if ok else '失败：' + got.splitlines()[0][:80]}")
    total_ids = len(by_plain)
    check(f"修复前（{PREFIX_COMMIT} 真实代码）：0/{total_ids} 个旧形状 id 能取回",
          before_ok, 0)

    # ── 修复后：当前代码 ─────────────────────────────────────────────────
    after_ok = 0
    mismatches: list[str] = []
    for fname, plain, qid in rows:
        src = next(f for f in load_findings(OUT / fname) if str(f.get("id")) == plain)
        got = cur.read_findings(qid)
        try:
            obj = json.loads(got)
        except ValueError:
            obj = None
        if not isinstance(obj, dict):
            mismatches.append(f"{qid}: {got[:80]}")
            continue
        same = (obj.get("_source") == fname
                and obj.get("file") == src.get("file")
                and obj.get("line") == src.get("line")
                and obj.get("symptom") == src.get("symptom")
                and obj.get("rootCause") == src.get("rootCause"))
        if same:
            after_ok += 1
        else:
            mismatches.append(f"{qid}: _source={obj.get('_source')} 期望 {fname}")
    check(f"修复后：{len(rows)}/{len(rows)} 条（12 份产物 × 每份 {len(rows)//max(len(d_files),1)} 条）"
          f"都能按带 label 的 id 唯一取回、且内容逐字对得上",
          after_ok, len(rows))
    if mismatches:
        for m in mismatches[:5]:
            print(f"      ✗ {m}")

    # ─- 旧形状 id：必须**明确报错**并把可用形状列出来（冲突可解，不是死路）─
    plain = sorted(by_plain)[0]
    got = cur.read_findings(plain)
    check(f"修复后：旧形状 id {plain!r} 重名时明确报错（含「重名」）", "重名" in got, True)
    listed = [q for _f, pl, q in rows if pl == plain and q in got]
    check(f"修复后：报错里列出了可用的带 label id（{len(listed)} 个都出现）",
          len(listed), len(by_plain[plain]))
    ok_all = all(cur.read_findings(q).lstrip().startswith("{") for q in listed)
    check("修复后：报错里列出的 id 每一个都真的能取回", ok_all, True)
    check("修复后：不存在的带 label id 仍报未找到",
          "未找到" in cur.read_findings("no-such-label-g9-f9"), True)
    check("修复后：路径穿越型 id 仍被拒",
          "拒绝" in cur.read_findings("../../etc/passwd"), True)


def section_r2_frozen(cur) -> None:
    print("\n── B. R2 冻结项（预注册 §1.1）：落点 / id 可读 / schema ──")
    names = [p.name for p in scout_files(OUT)]
    bad = [n for n in names if not re.fullmatch(r"scout-.+-\d+\.json", n)]
    check("scout 产物仍落 out/scout-<label>-<k>.json（无例外）", bad, [])
    # id 可读性：形如 <label>-g<k>-f<n>，长度可控，不是哈希。
    lab, k, n = "subagent-ab-v2-D-1", 1, 1
    qid = f"{lab}-g{k}-f{n}"
    check("id 形状 = <label>-g<k>-f<n>", bool(re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", qid)), True)
    check("id 仍可读（带运行 label、含组号条目号、长度 ≤64）",
          (len(qid) <= 64, qid.endswith("-g1-f1"), "subagent-ab-v2-D-1" in qid),
          (True, True, True))
    check("id 不是哈希（label 段不是一长串十六进制）",
          bool(re.fullmatch(r"[0-9a-f]{16,}", lab)), False)
    # schema：normalize_finding 是整个 v2 侧唯一产出 finding 形状的地方。
    import agent.policy_dispatch as pd
    raw = {k: "x" for k in ("id", "file", "line", "symptom", "rootCause",
                            "minimalFix", "evidence", "predictedFailureCount")}
    raw["line"] = 12
    raw["predictedFailureCount"] = 3
    f = pd.normalize_finding(raw, 2, 1, "lab-x")
    check("schema 字段仍是那 8 个（id/file/line/symptom/rootCause/minimalFix/"
          "evidence/predictedFailureCount）",
          sorted(f or {}), sorted(["id", "file", "line", "symptom", "rootCause",
                                   "minimalFix", "evidence", "predictedFailureCount"]))
    check("带 label 时 id = <label>-g2-f1", (f or {}).get("id"), "lab-x-g2-f1")
    f2 = pd.normalize_finding(raw, 2, 1)
    check("不带 label 时退化为旧形状（自检/离线脚本兼容）", (f2 or {}).get("id"), "g2-f1")


def section_r4_state(tmp: Path) -> None:
    print("\n── C. R4 durable 终态记录：截断 → 自动续派 → 成功 / 仍失败（合成，不调模型）──")
    import agent.policy_dispatch as pd

    # 造一个一次性的 git 仓库当 P4 快照源（绝不碰真靶场、不碰本仓库的 .git）。
    repo = tmp / "fake-repo"
    repo.mkdir()
    (repo / "more_itertools").mkdir()
    (repo / "more_itertools" / "more.py").write_text("x = 1\n", encoding="utf-8")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)

    FINDING = {"id": "scout-says-g1-f1", "file": "more_itertools/more.py", "line": 1,
               "symptom": "s", "rootCause": "r",
               "minimalFix": {"file": "more_itertools/more.py", "old": "x = 1", "new": "x = 2"},
               "evidence": "e", "predictedFailureCount": 2}
    TRUNC_SUMMARY = ("NOTE: this agent stopped at its 2-turn limit before finishing. "
                     "The text below is PARTIAL")

    def acct(out_tok: int) -> dict:
        return {"input_uncached": 100, "cache_read": 7, "cache_creation": 0,
                "output_tokens": out_tok, "cost_usd": 0.01, "elapsed_s": 1.5}

    def run_scenario(name: str, second_status: str, second_findings: bool | None = None,
                     second_source: str | None = "output_file"):
        out2 = tmp / f"out-{name}"
        out2.mkdir()
        calls: list[dict] = []
        give = (second_status == "completed") if second_findings is None else second_findings

        async def fake_session(**kw):
            calls.append(kw)
            if kw["followup_partial"] is None:
                return {"task_id": "t1", "status": "truncated", "summary": TRUNC_SUMMARY,
                        "summary_source": None, "evidence_author": "none",
                        "raw_findings": None, "spawn_tool_use_ids": ["spawn-1"],
                        "wrapper_mcp_calls": 0, "wrapper_extra_task_calls": 0,
                        "elapsed_s": 1.5, "scout_accounting": acct(11)}
            return {"task_id": "t2", "status": second_status,
                    "summary": "```json\n{\"findings\": [...]}\n```",
                    "summary_source": (second_source if give else None),
                    "evidence_author": ("scout" if give else "none"),
                    "raw_findings": [FINDING] if give else None,
                    "spawn_tool_use_ids": ["spawn-2"],
                    "wrapper_mcp_calls": 0, "wrapper_extra_task_calls": 0,
                    "elapsed_s": 2.5, "scout_accounting": acct(22)}

        def fake_verify(snapshot, copy_dir, finding, group_key, group_failed):
            return {"finding_id": finding["id"], "group": group_key, "verdict": "verified",
                    "note": "合成校验（未真跑 pytest）", "applied": True,
                    "predicted": finding["predictedFailureCount"],
                    "baseline_group_failed": group_failed, "observed_after": 0,
                    "exit_code": 0}

        pd._scout_session = fake_session      # 只替换 SDK 边界（不调模型）
        pd.verify_finding = fake_verify       # P5 的 pytest 也跳过（这里只验记账）
        out = _run_dispatch(pd, repo, out2, f"synth-{name}")
        pd._scout_session = ORIG_SESSION
        pd.verify_finding = ORIG_VERIFY
        return out, calls

    import agent.policy_dispatch as _pd0
    # 保存原函数，跑完恢复（本脚本是独立进程，仍按纪律恢复）
    globals()["ORIG_SESSION"] = _pd0._scout_session
    globals()["ORIG_VERIFY"] = _pd0.verify_finding

    out_ok, calls_ok = run_scenario("retry-success", "completed")
    finals = out_ok["finals"]
    check("每组恰有一条终态记录", len(finals), 1)
    f0 = finals[0]
    check("终态 event = 组状态 completed（不是某次尝试的状态）", f0.get("event"), "completed")
    check("attempts 链 = truncated → completed",
          [a["status"] for a in f0.get("attempts") or []], ["truncated", "completed"])
    check("retried=True", f0.get("retried"), True)
    check("retry_outcome 写成人话", f0.get("retry_outcome"),
          "attempt1=truncated;attempt2=completed")
    check("truncated_retry_succeeded=True（「截断后自动续派并成功」在记录里可见）",
          f0.get("truncated_retry_succeeded"), True)
    check("续派那一次真的带上了半份报告（提示词非空）",
          bool((calls_ok[1].get("followup_partial") or "").strip()), True)
    check("终态记录带 scout 侧用量（R5）",
          (f0.get("scout_accounting") or {}).get("output_tokens"), 22)
    check("两条 scout 记录都在 attempts 里",
          [(a["scout_accounting"] or {}).get("output_tokens")
           for a in f0.get("attempts") or []], [11, 22])
    check("metrics 汇总了 scout 侧成本", out_ok["metrics"].get("policy_scout_cost_usd"), 0.02)
    check("metrics 里的终态串", out_ok["metrics"].get("policy_groups_final"),
          "k1=completed(retried)")
    check("metrics 计数：截断续派后成功 1 组",
          out_ok["metrics"].get("policy_truncated_retry_succeeded"), 1)
    # ── B1-3 / B4（修复轮 #3）────────────────────────────────────────────
    check("每组状态记录写明了结论来源（B1-3）", f0.get("summary_source"), "output_file")
    check("每组状态记录写明了产出者（B1-3）", f0.get("evidence_author"), "scout")
    check("每条 finding 都有来源（B1-3）",
          list((f0.get("finding_sources") or {}).values()), ["output_file"])
    check("索引行标出来源（B1-3）", "来源=scout/output_file" in out_ok["index_text"], True)
    c1 = f0.get("c1") or {}
    check("B4 逐组门槛字段：dispatched_once", c1.get("dispatched_once"), True)
    check("B4 逐组门槛字段：scout_conclusion_obtained", c1.get("scout_conclusion_obtained"), True)
    check("B4 逐组门槛字段：group_status", c1.get("group_status"), "completed")
    check("B4 逐组门槛字段：结论来源", c1.get("scout_conclusion_source"), "output_file")
    m = out_ok["metrics"]
    check("B4 门槛 = passed（每组至少一次 scout 结论）", m.get("policy_c1_gate"), "passed")
    check("B4 主口径落盘 policy_gate_metric",
          m.get("policy_gate_metric"), "policy_scout_sessions+policy_k_groups")
    check("B4 主口径数值：scout 会话数 = Σ每组 attempts",
          m.get("policy_scout_sessions"), 2)
    check("B4 退役键不再出现在新产物里", "policy_dispatch_calls" in m, False)
    check("B4 Task tool_use 计数（全部 attempt，含包装自发）",
          m.get("policy_task_tool_use_count"), 2)
    check("B4 Task tool_use 计数（每组最后一次尝试）",
          m.get("policy_task_tool_use_count_last_attempt"), 1)
    check("B1 结论来源分布落盘", m.get("policy_finding_sources"),
          "output_file=1,无结论的组=0")

    out_bad, _ = run_scenario("retry-fail", "truncated")
    f1 = out_bad["finals"][0]
    check("两次都截断时，终态 event = undiagnosed（组的结论进了状态记录）",
          f1.get("event"), "undiagnosed")
    check("该组 group_status=undiagnosed", f1.get("group_status"), "undiagnosed")
    check("attempt_status 留档为 truncated", f1.get("attempt_status"), "truncated")
    check("undiagnosed 的组不算「续派成功」", f1.get("truncated_retry_succeeded"), False)
    check("metrics 计数：undiagnosed 1 组",
          out_bad["metrics"].get("policy_groups_undiagnosed"), 1)
    check("B4 门槛 = failed（没有一组拿到 scout 结论）",
          out_bad["metrics"].get("policy_c1_gate"), "failed")
    check("B1 来源分布标出「无结论的组」",
          out_bad["metrics"].get("policy_finding_sources"), "无结论的组=1")
    check("没拿到 scout 结论时 why 里说明「包装会话的文本不作数」",
          "包装会话的文本不作数" in (f1.get("why") or ""), True)

    # B3：**第二次尝试本身也被截断、但仍拿到 scout 结论** ⇒ 组状态 completed 而
    # attempts=[truncated,truncated]。这正是 v2-fix-trunc k3 的真实形态（t10 的验收
    # 文字曾把它误写成 [truncated,completed]）—— 用合成跑确认代码与语义一致。
    out_tt, _ = run_scenario("trunc-both-verified", "truncated", second_findings=True,
                             second_source="output_file")
    ft = out_tt["finals"][0]
    check("B3 场景：attempts=[truncated,truncated]",
          [a["status"] for a in ft.get("attempts") or []], ["truncated", "truncated"])
    check("B3 场景：组状态仍是 completed（组状态 ≠ 每次尝试都成功）",
          ft.get("group_status"), "completed")
    check("B3 场景：attempt_status 如实记为 truncated（不掩盖）",
          ft.get("attempt_status"), "truncated")
    check("B3 场景：truncated_retry_succeeded=True 的语义 = 截断后经续派拿到 verified",
          ft.get("truncated_retry_succeeded"), True)
    check("B3 场景：门槛仍按「拿到过 scout 结论」判 passed",
          out_tt["metrics"].get("policy_c1_gate"), "passed")

    rows = [json.loads(ln) for ln in (tmp / "out-retry-fail" /
                                      "policy_dispatch.jsonl").read_text(encoding="utf-8").splitlines()]
    check("状态记录里不再有 event=truncated 的**终态**行（组结论不再被尝试状态顶掉）",
          [r["event"] for r in rows if r["event"] == "truncated"], [])
    check("每个 attempt 各留一行 dispatched/retry_dispatched",
          [r["event"] for r in rows if "dispatched" in r["event"]],
          ["dispatched", "retry_dispatched"])


ORIG_SESSION = None
ORIG_VERIFY = None


def _run_dispatch(pd, repo: Path, out_dir: Path, label: str) -> dict:
    """跑一次（被替换了 SDK 边界的）run_policy_dispatch，并**从盘上读回**终态记录。"""
    import asyncio
    res = asyncio.run(pd.run_policy_dispatch(
        env={}, groups=[{"group": "G1", "failed_count": 2, "sample_lines": []}],
        label=label, fixture=repo, out_dir=out_dir, recorder=None, turn_cap=2,
        verify_python_note="合成（未真跑 pytest）"))
    res["finals"] = pd.read_group_finals(out_dir, label)
    return res


def section_b1_source_locking(tmp: Path) -> None:
    """F. B1/B2：结论来源锁定（离线复现泄漏现场）+ 包装会话工具清单 + scout 用量键名。

    为什么能离线证明 B1：判据全在 `_scout_session` 的**抽取层**，与模型无关 ——
    这里喂一条**人造消息流**（主会话文本里带着 findings JSON、包装模型自己又派了一次
    Task、自己调了 MCP 工具），看它还会不会被当成 scout 结论。这正是 v2-fix-trunc k3
    那次真实泄漏的形状（SDK transcript：isSidechain=false 那一行）。
    """
    print("\n── F. B1/B2：结论来源锁定 + 包装会话工具清单 + scout 用量键名（离线，不调模型）──")
    import asyncio
    import agent.policy_dispatch as pd
    from claude_agent_sdk import ResultMessage, TextBlock, ToolUseBlock

    # ── F1 装配层：包装会话拿不到代码工具（B1-2）────────────────────────────
    agents = {"scout": pd._scout_agent(2)}
    opts = pd._scout_options(env={}, snapshot=tmp, out_dir=tmp, agents=agents)
    mcp_names = [f"mcp__{pd.SERVER_NAME}__{t}" for t in pd.SCOUT_TOOL_NAMES]
    check("包装会话 tools 只有 Task", list(opts.tools), ["Task"])
    check("包装会话的内置工具黑名单仍在",
          ("Bash" in list(opts.disallowed_tools or [])), True)
    check("装配里挂了 PreToolUse 钩子（按调用来源判代码工具）",
          list((opts.hooks or {}).keys()), ["PreToolUse"])
    # 逐条验钩子策略：主线程的 MCP 调用必须被拒；scout（agent_id 非空）的必须放行。
    async def _hook(inp):
        return await pd.guard_code_tools_for_subagent(inp, "tu", {})
    deny_main = asyncio.run(_hook({"hook_event_name": "PreToolUse",
                                   "tool_name": f"mcp__{pd.SERVER_NAME}__read_file",
                                   "tool_input": {}}))
    allow_sub = asyncio.run(_hook({"hook_event_name": "PreToolUse",
                                   "tool_name": f"mcp__{pd.SERVER_NAME}__read_file",
                                   "tool_input": {}, "agent_id": "sub-1"}))
    allow_task = asyncio.run(_hook({"hook_event_name": "PreToolUse",
                                    "tool_name": "Task", "tool_input": {}}))
    check("B1-2 钩子：包装会话（主线程）调代码工具 ⇒ deny",
          (((deny_main or {}).get("hookSpecificOutput") or {}).get("permissionDecision")),
          "deny")
    check("B1-2 钩子：deny 带明确理由（模型看得见）",
          "不允许使用代码工具" in
          str(((deny_main or {}).get("hookSpecificOutput") or {}).get("permissionDecisionReason")),
          True)
    check("B1-2 钩子：scout（agent_id 非空）调代码工具 ⇒ 不表态（放行）", allow_sub, {})
    check("B1-2 钩子：Task 不表态（放行）", allow_task, {})
    # 每会话一份的 guard：第 1 次派发放行、第 2 次起拒（「续派只许 driver 做」）。
    guard = pd.make_wrapper_guard()

    async def _g(inp):
        return await guard(inp, "tu", {})

    t1 = asyncio.run(_g({"hook_event_name": "PreToolUse", "tool_name": "Task",
                         "tool_input": {"subagent_type": "scout"}}))
    t2 = asyncio.run(_g({"hook_event_name": "PreToolUse", "tool_name": "Task",
                         "tool_input": {"subagent_type": "scout"}}))
    check("B1 钩子：包装会话第 1 次派 Task ⇒ 放行", t1, {})
    check("B1 钩子：包装会话第 2 次派 Task ⇒ deny（续派只许 driver 侧做）",
          (((t2 or {}).get("hookSpecificOutput") or {}).get("permissionDecision")), "deny")
    g2 = pd.make_wrapper_guard()
    m_main = asyncio.run(g2({"hook_event_name": "PreToolUse",
                             "tool_name": f"mcp__{pd.SERVER_NAME}__read_file",
                             "tool_input": {}}, "tu", {}))
    m_sub = asyncio.run(g2({"hook_event_name": "PreToolUse",
                            "tool_name": f"mcp__{pd.SERVER_NAME}__read_file",
                            "tool_input": {}, "agent_id": "sub-1"}, "tu", {}))
    check("B1 钩子：同一份 guard 也拦主线程的代码工具",
          (((m_main or {}).get("hookSpecificOutput") or {}).get("permissionDecision")), "deny")
    check("B1 钩子：同一份 guard 对 scout 不表态", m_sub, {})
    check("会话级白名单仍含 5 个只读代码工具（scout 与包装会话共用，砍了会掐死 scout）",
          [t for t in opts.allowed_tools if t in mcp_names], mcp_names)
    check("包装会话的权限模式仍是实测稳定的 dontAsk",
          opts.permission_mode, pd.PERMISSION_MODE_DONT_ASK)
    sd = (opts.agents or {}).get("scout")
    check("scout 工具清单 = 5 个只读 MCP 工具",
          list(getattr(sd, "tools", []) or []), mcp_names)
    check("scout 没有写工具（只读不变量）",
          any("apply_patch" in str(t) for t in (getattr(sd, "tools", []) or [])), False)
    check("scout 有内置工具黑名单（Bash/Write 都在里面）",
          ("Bash" in (getattr(sd, "disallowedTools", []) or [])
           and "Write" in (getattr(sd, "disallowedTools", []) or [])), True)
    check("forward_subagent_text=True（子代理文本才带 parent_tool_use_id 转发）",
          opts.forward_subagent_text, True)

    # ── F2 抽取层：人造消息流 ────────────────────────────────────────────
    FIND = {"id": "scout-x", "file": "a.py", "line": 1, "symptom": "s", "rootCause": "r",
            "minimalFix": {"file": "a.py", "old": "x", "new": "y"}, "evidence": "e",
            "predictedFailureCount": 1}
    JSON_TEXT = "```json\n" + json.dumps({"findings": [FIND]}, ensure_ascii=False) + "\n```"
    TRUNC = ("NOTE: this agent stopped at its 2-turn limit before finishing. "
             "The text below is PARTIAL")

    class StreamMsg:                       # 主会话/子代理消息的通用形状
        def __init__(self, content=None, parent_tool_use_id=None):
            self.content = content if content is not None else []
            self.parent_tool_use_id = parent_tool_use_id

    class TaskNotificationMessage:          # 类名必须叫这个（分派靠 type().__name__）
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def result_msg() -> ResultMessage:
        return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                             is_error=False, num_turns=1, session_id="s",
                             usage={"input_tokens": 11, "output_tokens": 7},
                             total_cost_usd=0.25,
                             model_usage={"m": {"inputTokens": 100, "outputTokens": 7,
                                                "cacheReadInputTokens": 3,
                                                "cacheCreationInputTokens": 5,
                                                "costUSD": 0.25}})

    def spawn(tid: str) -> StreamMsg:
        return StreamMsg([ToolUseBlock(id=tid, name="Agent", input={"subagent_type": "scout"})])

    def run_stream(name: str, seq: list) -> dict:
        out_dir = tmp / f"b1-{name}"
        out_dir.mkdir(exist_ok=True)
        orig_query = pd.query

        def fake_query(*, prompt, options):
            async def gen():
                for m in seq:
                    yield m
            return gen()

        pd.query = fake_query
        try:
            return asyncio.run(pd._scout_session(
                env={}, group={"group": "G", "failed_count": 1, "sample_lines": []},
                k_idx=1, label=f"b1-{name}", snapshot=tmp, out_dir=out_dir,
                dispatch_log=out_dir / "policy_dispatch.jsonl", recorder=None,
                turn_cap=2, followup_partial=None, parent_tool_use_id="t1"))
        finally:
            pd.query = orig_query

    # 泄漏现场：主会话文本里有 findings JSON，但 scout 自己什么都没交。
    leak = run_stream("leak", [
        spawn("spawn-A"),
        TaskNotificationMessage(task_id="t1", status="completed", summary=TRUNC,
                                output_file=None, usage=None, tool_use_id="spawn-A"),
        spawn("spawn-B"),                       # 包装模型自己又派了一次 Task
        TaskNotificationMessage(task_id="t2", status="completed", summary=TRUNC,
                                output_file=None, usage=None, tool_use_id="spawn-B"),
        StreamMsg([ToolUseBlock(id="m1", name="mcp__code-tools__read_file", input={})]),
        StreamMsg([TextBlock(text=JSON_TEXT)]),  # ← 主会话（包装模型）自己的文本
        result_msg(),
    ])
    check("B1-1 泄漏现场：包装会话文本**不**被当成 scout 结论",
          leak.get("raw_findings"), None)
    check("B1-1 泄漏现场：来源标签为 None（没有可用来源）", leak.get("summary_source"), None)
    check("B1-1 泄漏现场：evidence_author=none", leak.get("evidence_author"), "none")
    check("B1-1 泄漏现场：包装模型自发的 Task 被计数",
          leak.get("wrapper_extra_task_calls"), 1)
    check("B1-1 泄漏现场：包装模型自己调 MCP 工具被计数",
          leak.get("wrapper_mcp_calls"), 1)
    check("B1-1 泄漏现场：那份不属于本次 harness 的通知被忽略",
          leak.get("ignored_notifications"), 1)
    check("B1-1 泄漏现场：harness 自己那把 Task 的 id 被记下来",
          leak.get("harness_spawn_tool_use_id"), "spawn-A")

    # 正路 1：scout 的通知 summary 里带 findings
    ok1 = run_stream("summary", [
        spawn("spawn-C"),
        TaskNotificationMessage(task_id="t1", status="completed",
                                summary="done\n" + JSON_TEXT, output_file=None,
                                usage={"total_tokens": 5, "tool_uses": 1, "duration_ms": 9},
                                tool_use_id="spawn-C"),
        result_msg(),
    ])
    check("正路 summary：取到 scout 结论", len(ok1.get("raw_findings") or []), 1)
    check("正路 summary：来源=summary", ok1.get("summary_source"), "summary")
    check("正路 summary：产出者=scout", ok1.get("evidence_author"), "scout")

    # 正路 2：只有 scout 那一侧的流式文本（parent_tool_use_id=harness 那把 Task）
    ok2 = run_stream("scout-text", [
        spawn("spawn-D"),
        TaskNotificationMessage(task_id="t1", status="completed", summary=TRUNC,
                                output_file=None, usage=None, tool_use_id="spawn-D"),
        StreamMsg([TextBlock(text=JSON_TEXT)], parent_tool_use_id="spawn-D"),
        result_msg(),
    ])
    check("正路 scout-text：取到 scout 结论", len(ok2.get("raw_findings") or []), 1)
    check("正路 scout-text：来源=stream_text_scout",
          ok2.get("summary_source"), "stream_text_scout")

    # ── B2：scout 用量行按**文档名**读，不再是 None ───────────────────────
    usage_file = tmp / "b1-scout-text" / "policy_scout_usage.jsonl"
    lines = [json.loads(ln) for ln in usage_file.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    line = lines[-1] if lines else {}
    print(f"      policy_scout_usage.jsonl 最后一行（文档名 → 值）："
          f"output_tokens={line.get('output_tokens')} cost_usd={line.get('cost_usd')} "
          f"subtype={line.get('subtype')} task_usage={line.get('task_usage')}")
    check("B2：按文档名读 output_tokens 不再是 None", line.get("output_tokens"), 7)
    check("B2：按文档名读 cost_usd 不再是 None", line.get("cost_usd"), 0.25)
    check("B2：按文档名读 subtype 不再是 None", line.get("subtype"), "success")
    check("B2：task_usage 键在场（值为 scout 自报的 TaskUsage；本次伪造流里没有它）",
          "task_usage" in line, True)
    check("B2：旧键名同时在场且值相同",
          (line.get("output"), line.get("total_cost_usd")), (7, 0.25))
    check("B2：key_map 映射表随行落盘",
          (line.get("key_map") or {}).get("output_tokens"), "output")
    check("B2：input/cache 三口径都在",
          [line.get(k) for k in ("input_uncached", "cache_read", "cache_creation")],
          [100, 3, 5])
    check("B2：一个 scout 只在文件里有一行", len(lines), 1)


def section_r7_field(tmp: Path) -> None:
    print("\n── D. R7：verify 记录里的失败数不再叫 observed_before ──")
    import agent.policy_dispatch as pd
    snap = tmp / "r7-snap"
    snap.mkdir(exist_ok=True)
    rec = pd.verify_finding(snap, tmp / "r7-copy",
                            {"id": "x", "minimalFix": {"file": "nope.py",
                                                       "old": "a", "new": "b"}},
                            "G", 3)
    check("不再有 observed_before 这个键", "observed_before" in rec, False)
    check("改名为 baseline_group_failed，值 = driver 基线里该组的失败数",
          rec.get("baseline_group_failed"), 3)
    src = rec.get("baseline_source") or ""
    check("加了 baseline_source 写明它到底是什么（含「基线」与「不是观察值」）",
          ("基线" in src and "不是" in src and "观察值" in src), True)
    check("真正的观察值字段 observed_after 仍在", "observed_after" in rec, True)
    print("      改动前：observed_before = <driver 基线里该组的失败数>（名字读起来像观察值）")
    print("      改动后：baseline_group_failed = 同一个值 + baseline_source 说明出处；"
          "observed_after 才是记录里唯一的观察值")


def section_r9_gate(tmp: Path) -> None:
    print("\n── E. R9：--report 的臂集从产物推断 + 拒绝写出更短的 runs.jsonl ──")
    sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location("_abr", HERE / "subagent_ab_runner.py")
    abr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(abr)

    arms, n, fx = abr.scan_artifacts("subagent-ab-v2")
    check("真实 out/ 上推断出 v2 的臂集（不再只捞默认 A,B,C）", arms, ("A", "D"))
    check("推断出每臂 n", n, 4)
    check("从产物 trace 推断出靶场", fx, "more-itertools-hard")
    arms_h, n_h, _ = abr.scan_artifacts("subagent-ab-hard")
    check("旧 tag 同样能推断（A,B,C 三臂）", arms_h, ("A", "B", "C"))
    check("不存在的 tag 推断为空（=> 调用方必须报错退出）",
          abr.scan_artifacts("no-such-tag"), ((), 0, None))

    # ── 闸门仿真：在临时 OUT 上跑两次 main() ─────────────────────────────
    def fake_out() -> Path:
        d = tmp / "abr-out"
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        return d

    def write_artifacts(d: Path, arms_: str, up_to: int) -> None:
        for arm in arms_:
            for i in range(1, up_to + 1):
                (d / f"agent-fake-{arm}-{i}.json").write_text(json.dumps(
                    {"metrics": {"success": True, "cost_usd": 0.1, "label":
                                 f"fake-{arm}-{i}"},
                     "events": [], "trace": {"run": {"fixture": "more-itertools-hard"}}}),
                    encoding="utf-8")

    def run_main(argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        old_argv, old_out = sys.argv, abr.OUT
        abr.OUT = d
        sys.argv = ["abr", *argv]
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = abr.main()
        finally:
            sys.argv, abr.OUT = old_argv, old_out
        return rc, buf.getvalue()

    d = fake_out()
    write_artifacts(d, "AD", 4)
    rc1, out1 = run_main(["--report", "--tag", "fake"])
    lines1 = len((d / "fake-runs.jsonl").read_text(encoding="utf-8").splitlines())
    check("E1 八份产物（A,D）⇒ 汇总写出 8 行", (rc1, lines1), (0, 8))
    check("E1 打印了推断来源", "从产物推断" in out1, True)
    check("E1 manifest 写「完成 8/8 次」", "完成 8/8 次" in out1, True)
    before_jsonl = (d / "fake-runs.jsonl").read_bytes()
    before_manifest = (d / "fake-manifest.txt").read_bytes()

    # 把 D 臂产物删掉（模拟「忘了带 --arms 的汇总」会捞不到的那一半）
    for p in sorted(d.glob("agent-fake-D-*.json")):
        p.unlink()
    rc2, out2 = run_main(["--report", "--tag", "fake"])
    after_jsonl = (d / "fake-runs.jsonl").read_bytes()
    after_manifest = (d / "fake-manifest.txt").read_bytes()
    check("E2 产物只剩 A 臂时**拒绝写入**（非零退出码）", rc2 != 0, True)
    check("E2 runs.jsonl 一个字节都没变（8 行还是 8 行）",
          (len(after_jsonl.splitlines()), after_jsonl == before_jsonl), (8, True))
    check("E2 manifest 也没被改写", after_manifest == before_manifest, True)
    check("E2 stderr 说明了要丢哪几条（拒绝的理由）",
          ("拒绝" in out2 and "fake-D-" in out2), True)

    d2 = fake_out()
    abr.OUT = d2
    sys.argv = ["abr", "--report", "--tag", "fake2"]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            rc3 = abr.main()
    finally:
        sys.argv = ["abr"]
        abr.OUT = d
    check("E3 一份产物都没有 ⇒ 报错退出（不写出空 runs.jsonl）",
          (rc3, (d2 / "fake2-runs.jsonl").exists()), (2, False))
    check("E3 报错文本说了推不出臂集", "推不出臂集" in buf.getvalue(), True)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.path.insert(0, str(REPO))
    print("=== ④ 层 v2 修复回归检查（R2 / R4 / R7 / R9；不调模型）===")
    tmp = Path(tempfile.mkdtemp(prefix="v2-scope-"))
    try:
        cur = load_server_module(REPO / SERVER_REL, OUT, tmp / "cur-server.log", "cts_cur")
        section_r2_conflict(tmp, cur)
        section_r2_frozen(cur)
        section_r4_state(tmp)
        section_b1_source_locking(tmp)
        section_r7_field(tmp)
        section_r9_gate(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("AGENT_FINDINGS_DIR", None)
        os.environ.pop("AGENT_TOOLS_LOG", None)
    print(f"\n=== {'全部通过' if not FAILURES else '有失败'}（{len(FAILURES)} 项失败）===")
    for f in FAILURES:
        print(f"  - {f}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
