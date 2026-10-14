"""④ 层记账的自检 —— **不调模型**，只喂人造消息。

为什么要先有它：④ 层最容易犯的错不是「扣不动」，而是**记错账**。
两种错法都不会报错，只会在两天后写进一个错的结论：

  ① 子代理的 tool_use 混进父流的 `tool_calls_total` —— 父流口径漂移，
     于是「加 ④ 层之前」和「之后」的同一个指标变成两个东西，两臂没法比；
  ② `TaskProgressMessage.usage` 没被记 —— 子代理的用量只在这里，
     不记就等于成本静默偏低（探针实测：父层 input_tokens 只 +132%，
     真实总成本 +780%）。

所以先用构造消息把「分流」与「汇总」钉死，再去花真钱跑 agent。

用法：python servers/experiments/subagent_selfcheck.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from agent.config import force_utf8_output  # noqa: E402
from agent.trace import TraceRecorder  # noqa: E402
import agent.policy_dispatch as pd  # noqa: E402  （P1–P5 driver 流水线）
from claude_agent_sdk import TextBlock, ToolResultBlock, ToolUseBlock  # noqa: E402

import coding_agent as ca  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got: object, want: object) -> None:
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}" + ("" if ok else f"  (期望 {want!r})"))
    if not ok:
        FAILURES.append(label)


@dataclass
class TaskProgressMessage:
    """**类名必须叫这个** —— 分派是靠 `type(message).__name__` 做的。

    故意不 import SDK 里那个同名类：本层的一条实现约束就是「不依赖这几个类
    被 export」，用一个人造的同名类来测，正好把这条约束也测进去。
    """
    task_id: str = "t1"
    usage: dict = field(default_factory=lambda: {"total_tokens": 1898, "tool_uses": 1,
                                                 "duration_ms": 1475})
    last_tool_name: str = "mcp__code-tools__read_file"


@dataclass
class SystemMessage:
    subtype: str = "init"


class NotATaskMessage:
    """类名不在名单里 —— 应该被原样忽略（不能污染 events）。"""


def main() -> int:
    force_utf8_output()
    print("=== ④ 层记账自检（不调模型）===")
    print(f"SUBAGENT_TOOL(CLI 名单) = {ca.SUBAGENT_TOOL!r}  "
          f"SUBAGENT_SPAWN_NAME(模型看到) = {ca.SUBAGENT_SPAWN_NAME!r}")
    check("两个名字确实不同（记错账的根源）", ca.SUBAGENT_TOOL != ca.SUBAGENT_SPAWN_NAME, True)

    recorder = TraceRecorder({"selfcheck": True})
    events: list[dict] = []
    idx = 0

    # ── 1) 父流：一次 spawn 调用 + 它的 tool_result ──────────────────────
    spawn = ToolUseBlock(id="tu-1", name=ca.SUBAGENT_SPAWN_NAME,
                         input={"subagent_type": "scout", "prompt": "找根因"})
    events.append({"i": 1, "type": "tool_use", "tool": spawn.name, "input": spawn.input})
    idx = 1
    events.append({"i": 2, "type": "tool_result", "chars": 1702, "text": "…总结…"})
    idx = 2

    # ── 2) 子代理流（带 parent_tool_use_id）────────────────────────────
    nested = [
        ToolUseBlock(id="sub-1", name="mcp__code-tools__read_file", input={"path": "a.py"}),
        ToolResultBlock(tool_use_id="sub-1", content={"result": "x" * 500}, is_error=False),
        TextBlock(text="根因在 more.py:1234"),
        ToolUseBlock(id="sub-2", name="mcp__code-tools__run_tests", input={}),
    ]
    for block in nested:
        idx = ca.record_subagent_block(block, "tu-1", events, idx, recorder)

    # ── 3) Task* 生命周期消息 ───────────────────────────────────────────
    idx = ca.record_task_message(TaskProgressMessage(), events, idx, recorder)
    idx = ca.record_task_message(SystemMessage(), events, idx, recorder)
    idx = ca.record_task_message(NotATaskMessage(), events, idx, recorder)
    check("非 Task* 消息不产生事件", len([e for e in events if e["type"] == "task_message"]), 1)

    # ── 4) 汇总：父流口径必须一个数都不变 ──────────────────────────────
    parent = ca.summarize(events)
    check("tool_calls_total 只数父流", parent["tool_calls_total"], 1)
    check("tool_calls_per_tool 只有 Agent",
          parent["tool_calls_per_tool"], {ca.SUBAGENT_SPAWN_NAME: 1})
    check("read_count 不含子代理的读", parent["read_count"], 0)
    check("run_tests_count 不含子代理的跑", parent["run_tests_count"], 0)

    sub = ca.summarize_subagent(events)
    check("spawn 次数", sub["subagent_spawn_calls"], 1)
    check("adoption", sub["subagent_adopted"], True)
    check("子代理 tool_use 数", sub["subagent_tool_calls"], 2)
    check("子代理 tool_result 字符", sub["subagent_result_chars"], 500)
    check("子代理文本字符", sub["subagent_text_chars"], len("根因在 more.py:1234"))
    check("Task 消息按类型计数", sub["task_messages_per_type"], {"TaskProgressMessage": 1})
    check("子代理用量估计（取 max）", sub["subagent_tokens_estimate"], 1898)

    # ── 5) trace：note() 能用，且不污染回合号 ─────────────────────────
    t = recorder.finish({"outcome": "selfcheck"})
    kinds = [s["type"] for s in t["steps"]]
    check("trace 里有 subagent_tool_call", "subagent_tool_call" in kinds, True)
    check("trace 里有 subagent_text", "subagent_text" in kinds, True)
    check("trace 里有 task_message", "task_message" in kinds, True)
    check("没有多余的 turn（子代理不占父流回合）", kinds.count("turn"), 0)

    # ── 6) 沙箱规格：关掉时必须与加 ④ 层之前逐字相同 ────────────────────
    off = ca.sandbox_spec(allow_write=True, enable_subagent=False)
    check("关：builtin_tools", off["builtin_tools"], [])
    check("关：allowed_tools = 6 个 MCP", len(off["allowed_tools"]), len(ca.ALL_TOOLS))
    check("关：Task 仍在 disallowed 里", ca.SUBAGENT_TOOL in off["disallowed_tools"], True)
    check("关：名册为空", off["agents"], [])
    on = ca.sandbox_spec(allow_write=True, enable_subagent=True)
    check("开：builtin_tools", on["builtin_tools"], [ca.SUBAGENT_TOOL])
    check("开：Task 已从 disallowed 里减掉", ca.SUBAGENT_TOOL in on["disallowed_tools"], False)
    check("开：disallowed 只少一个", len(off["disallowed_tools"]) - len(on["disallowed_tools"]), 1)
    check("开：allowed 多一个", len(on["allowed_tools"]) - len(off["allowed_tools"]), 1)
    check("开：名册有 scout", on["agents"], ["scout"])
    check("scout 不能写（无 apply_patch）",
          ca.WRITE_TOOL in ca.SCOUT_TOOLS, False)
    check("scout 能跑测试", f"mcp__{ca.SERVER_NAME}__run_tests" in ca.SCOUT_TOOLS, True)

    # ── 7) 题面：只有开了才出现那段邀请 ────────────────────────────────
    t_off, t_on = ca.build_task(False), ca.build_task(True)
    check("关：题面不含子代理", "scout" in t_off, False)
    check("开：题面含子代理", "scout" in t_on, True)
    check("开：题面是在原文后面追加（臂 A 的题面是它的前缀）", t_on.startswith(t_off), True)
    check("关：题面与 TASK_BASE 逐字相同", t_off, ca.TASK_BASE)
    # 通道阳性对照：强制段**只能**在 force 时才出现。它要是漏进了 A/B 的题面，
    # 那两臂就不再只差一个变量了，而且 adoption 会变成我自己刷出来的数。
    check("开但没强制：题面不含强制段", ca.TASK_SUBAGENT_FORCE in t_on, False)
    t_force = ca.build_task(True, force_spawn=True)
    check("强制：题面含强制段", ca.TASK_SUBAGENT_FORCE in t_force, True)
    check("强制：仍以正常题面为前缀", t_force.startswith(t_on), True)
    check("关：force 也不产生子代理段", ca.build_task(False, force_spawn=True), ca.TASK_BASE)

    # ── 8) P1 驱动派发：分组 / 题面 / 索引（**不调模型、不碰靶场**）────────
    print("\n── P1–P5 自检（分组/索引/截断判据/快照守卫，不调模型）──")
    HARD_TAIL = (
        "FAILED tests/test_more.py::IsliceExtendedTests::test_all [(-4, 3, 1)] - assert ...\n"
        "SUBFAILED(slice_args=(-4, 3, 2)) tests/test_more.py::IsliceExtendedTests::test_all\n"
        "FAILED tests/test_more.py::DiscreteFourierTransformTests::test_roundtrip - ...\n"
        "FAILED tests/test_more.py::SlidingWindowTests::test_windowed - ...\n"
        "ERROR tests/test_more.py::SlidingWindowTests::test_other - fixture error\n"
        "32 failed, 766 passed in 12.34s\n"
    )
    groups = pd.group_failures(HARD_TAIL)
    gmap = {g["group"]: g["failed_count"] for g in groups}
    check("按测试类聚组（认得出三类）", sorted(gmap),
          ["DiscreteFourierTransformTests", "IsliceExtendedTests", "SlidingWindowTests"])
    check("组内失败数来自 FAILED/SUBFAILED/ERROR 行", gmap["IsliceExtendedTests"], 2)
    check("排序确定性（失败数降序）", [g["group"] for g in groups][0], "IsliceExtendedTests")
    check("无 Tests 形状时回落单组 OTHER",
          [g["group"] for g in pd.group_failures("FAILED x.py::foo::bar\n1 failed")], ["OTHER"])

    # policy 题面：陈述句、不含邀请、TASK_BASE 仍是逐字前缀。
    idx_txt, idx_n = pd.build_index(
        [{"group": "A", "failed_count": 2}, {"group": "B", "failed_count": 1}],
        [{"status": "completed_verified", "group": "A",
          "findings": [
              {"id": "g1-f1", "file": "more_itertools/more.py", "line": 1234,
               "predictedFailureCount": 2,
               "minimalFix": {"summary": "把 a[i] 改回 a[i-1]"},
               "verification": {"verdict": "verified"}},
              {"id": "g1-f2", "file": "more_itertools/more.py", "line": 99,
               "predictedFailureCount": 1,
               "minimalFix": {"summary": "不该出现的 refuted 条目"},
               "verification": {"verdict": "refuted"}}]},
         {"status": "undiagnosed", "group": "B", "why": "scout 交了空 findings"}])
    check("索引只收 verified（refuted 不出现）", "g1-f2" in idx_txt, False)
    check("verified 条目在索引里", "g1-f1" in idx_txt, True)
    suf = pd.policy_task_suffix(idx_txt, idx_n, 2)
    t_pol = ca.build_task(False, policy_suffix=suf)
    check("policy 题面以 TASK_BASE 为逐字前缀（D 与 A 只差追加段）",
          t_pol.startswith(ca.TASK_BASE), True)
    check("policy 题面不含 B/C 臂的邀请段", ca.TASK_SUBAGENT_NOTE in t_pol, False)
    check("policy 题面是陈述句（已为你派出）", "已为你派出" in t_pol, True)
    check("policy 题面没有选择题口吻", "要不要用 subagent" in t_pol, False)
    check("不带 suffix 时题面逐字不变（A 臂同形）", ca.build_task(False), ca.TASK_BASE)

    # P2 索引有界性：塞爆它。
    big_records = [{"status": "completed_verified", "group": f"G{i}",
                    "findings": [{"id": f"g{i}-f1", "file": "more_itertools/" + "x" * 60,
                                  "line": 1000 + i, "predictedFailureCount": 1,
                                  "minimalFix": {"summary": "y" * 120},
                                  "verification": {"verdict": "verified"}}]}
                   for i in range(1, 61)]
    big_groups = [{"group": f"G{i}", "failed_count": 1} for i in range(1, 61)]
    btxt, bn = pd.build_index(big_groups, big_records)
    check("索引硬上限 ≤2000 字符", bn <= pd.DEPENDENCY_OUTPUT_MAX_CHARS, True)
    check("溢出时尾行点名省略（不许悄悄丢结论）", "因索引上限省略" in btxt, True)
    check("常量口径 = 参考实现的 2000", pd.DEPENDENCY_OUTPUT_MAX_CHARS, 2000)

    # P3 截断判据与 TRUNC_MARK 同源（subagent_review 那份措辞改了这里必须红）。
    import subagent_review as sr
    check("TRUNC_MARK 两处同源", pd.TRUNC_MARK, sr.TRUNC_MARK)
    trunc_summary = ("NOTE: this agent stopped at its 2-turn limit before finishing. "
                     "The text below is PARTIAL")
    check("截断样张能被判据命中", pd.TRUNC_MARK in trunc_summary.lower(), True)

    # P5 快照守卫：越出副本根的 minimalFix 必须被拒（不碰真靶场）。
    import tempfile as _tf
    tmp = _tf.mkdtemp(prefix="selfcheck-snap-")
    try:
        ok, why = pd.apply_minimal_fix(
            __import__("pathlib").Path(tmp),
            {"minimalFix": {"file": "../escape.py", "old": "a", "new": "b"}})
        check("minimalFix 路径越界被拒", ok, False)
        check("越界拒绝有理由", "越出" in why, True)
        ok2, why2 = pd.apply_minimal_fix(
            __import__("pathlib").Path(tmp),
            {"minimalFix": {"file": "nope.py", "old": "a", "new": "b"}})
        check("文件不存在时如实拒绝", ok2, False)
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)

    # P5 校验解释器钉死（任务书要求；mutate.py 同款做法）。
    check("VERIFY_PYTHON 是那个装了 pytest 的环境",
          pd.VERIFY_PYTHON, r"D:\Anaconda3\envs\aicoding\python.exe")

    # normalize_finding：字段不齐宁可丢。
    f_ok = pd.normalize_finding(
        {"id": "junk", "file": "a.py", "line": "12", "symptom": "s", "rootCause": "r",
         "minimalFix": {"file": "a.py", "old": "x", "new": "y"}, "evidence": "e",
         "predictedFailureCount": "3"}, 2, 1)
    check("normalize 强制 id=g<k>-f<n>", f_ok and f_ok["id"], "g2-f1")
    check("normalize 数字字符串归一", f_ok and (f_ok["line"], f_ok["predictedFailureCount"]),
          (12, 3))
    check("缺字段直接丢（不留半条）",
          pd.normalize_finding({"id": "x", "file": "a.py"}, 2, 1), None)

    # ── 9) D 臂沙箱装配：父层无 Task、多 read_findings；A/B/C 逐字不变 ──────
    pol = ca.sandbox_spec(True, False, policy_mode=True)
    base_off = ca.sandbox_spec(True, False)
    check("policy：builtin_tools 仍为空（模型没有 spawn 通道）", pol["builtin_tools"], [])
    check("policy：Task 仍在 disallowed 里", ca.SUBAGENT_TOOL in pol["disallowed_tools"], True)
    check("policy：allowed 比 A 臂恰好多 1（read_findings）",
          len(pol["allowed_tools"]) - len(base_off["allowed_tools"]), 1)
    check("policy：多出来的是 read_findings",
          set(pol["allowed_tools"]) - set(base_off["allowed_tools"]),
          {ca.POLICY_EXTRA_TOOL})
    check("policy：名册仍为空（scout 只在 driver 会话里存在）", pol["agents"], [])
    check("A 臂不受影响：三参调用默认 policy_mode=False 逐字同旧",
          ca.sandbox_spec(True, False), base_off)
    on_c = ca.sandbox_spec(True, True)
    check("B/C 臂不受影响：enable_subagent 时没有 read_findings",
          ca.POLICY_EXTRA_TOOL in on_c["allowed_tools"], False)
    off_nw = ca.sandbox_spec(False, False)
    check("--no-write 反向对照仍拿掉 apply_patch",
          ca.WRITE_TOOL in off_nw["mcp_tools"], False)
    check("policy+no-write：read_findings 在场、apply_patch 不在场",
          (ca.POLICY_EXTRA_TOOL in pol["mcp_tools"],
           ca.WRITE_TOOL in off_nw["mcp_tools"]), (True, False))

    # ── 10) run_tests 新参数缺省同形（server 侧，不起进程只查签名默认值）────
    import inspect as _inspect
    sys.path.insert(0, str(PROJECT_ROOT / "servers"))
    import code_tools_server as cts  # noqa: E402
    sig = _inspect.signature(cts.run_tests)
    defs = {p.name: p.default for p in sig.parameters.values()}
    check("run_tests 新参数缺省为空串（不传=旧行为）",
          (defs.get("python_exe"), defs.get("cwd_rel")), ("", ""))
    check("run_tests 老参数默认值未动",
          (defs.get("test_path"), defs.get("keyword"), defs.get("detail")),
          ("", "", "brief"))
    check("read_findings 已注册为工具（函数存在且带 docstring）",
          callable(getattr(cts, "read_findings", None)) and bool(cts.read_findings.__doc__),
          True)

    print(f"\n=== {'全部通过' if not FAILURES else '有失败'}（{len(FAILURES)} 项失败）===")
    for f in FAILURES:
        print(f"  - {f}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
