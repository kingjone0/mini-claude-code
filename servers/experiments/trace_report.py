"""trace_report.py —— 把一次运行的 trace 渲染成**可读时间线**，并指出「卡在哪一步」。

**为什么需要它**：`agent-*.json` 已经是完整的原始记录，但回答
「这一轮为什么卡住」要人工翻几万行 JSON —— 于是实际上没人翻，
「全程留痕」就只停在「留了」这一步。这个脚本是那一步到「能说清」之间的桥。

━━ 两条设计规矩 ━━

1. **统计一律从 `steps` 现算，不读 `metrics`。**
   同一个量存两份，两份不一致时无从判断谁对。判据只留一条通道，
   才谈得上「口径没改过」—— 与 `context_report.py` 同一条规矩。

2. **数据缺失导致跑不了的检测项，必须显式报「跑不了」，不能报「没问题」。**
   旧产物（只有 `metrics` + `events`）没有时间戳、没有 `tool_use_id`、
   没有 `is_error`。如果这时照样输出「未发现卡点」，那是在用一个
   **没有能力发现问题**的口径去证明没问题 —— 比不报更糟。

用法：
    python trace_report.py agent-big-v1.json            # 渲染时间线
    python trace_report.py agent-big-v1.json --json     # 只要机器可读的结论
    python trace_report.py agent-big-v1.json --max-text 60
    python trace_report.py --self-test                  # 自检（构造输入 + 断言）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ⚠️ 必须在任何 print 之前（项目 CLAUDE.md 陷阱 16）：本机控制台是 GBK，
# 打印 ⚠️ / ✅ 这类字符会 UnicodeEncodeError 把报告打断在半途。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agent.config import force_utf8_output  # noqa: E402

SHORT_PREFIX = "mcp__code-tools__"
EXIT_CODE_PAT = re.compile(r"退出码\s*=\s*(\d+)")
RT = f"{SHORT_PREFIX}run_tests"
RF = f"{SHORT_PREFIX}read_file"

# 单步间隔超过它就记一条「在等什么」。30s 是**拍的**，不是测出来的 ——
# 所以它只当线索用，不当结论：报告里写的是「这一步花了 41.2s」，
# 而不是「这里卡住了」。要断言卡住，需要的是同一步在多次运行里都慢。
SLOW_STEP_MS = 30_000

# 结果里出现这些才算「工具真的报错了」。is_error 是 SDK 给的原始字段。
ERROR_KINDS = {"tool_error", "permission_denied", "max_turns", "claimed_but_failed",
               "agent_exception"}

# v1 trace 里有、v0 events 里没有的东西 → 对应的检测项**跑不了**。
# 这里只列真正跑不了的。像 `orphan_result` 那种「能跑、但结论被污染」的，
# 不算跑不了（把它列进来是另一种谎报），单独放进 V0_CAVEATS 说清楚。
V0_UNAVAILABLE = [
    "slow_step（events 没有时间戳，时间轴不存在）",
    "tool_error（events 没存 is_error，工具报错与正常返回分不开）",
]
V0_PAIRED_BY = "order（v0 无 tool_use_id，只能按出现顺序配对 —— 陷阱 24 的坑）"
V0_CAVEATS = [
    "**按发出顺序（FIFO）配对是猜的**：v0 没有 tool_use_id，而并行调用时结果按"
    "**完成**顺序回来，与发出顺序不一定一致。错配不会报错，它只会让某个结果的"
    "字符数挂到另一个调用头上 —— 所以旧产物的「每次调用多少字符」只能当参考。",
]

# 时间线里带特殊标记的步骤类型。`note()` 写出来的步骤，type 就是调用方
# 给的 kind（例如 "agent_exception"），所以这里只列「已知的具名类型」，
# 落不到任何一个的类型会走通用分支 —— 不静默丢。
TEXT_STEPS = {"assistant_text": "💬", "thinking": "🧠", "user_text": "👤"}


def short_name(tool: str) -> str:
    return tool.replace(SHORT_PREFIX, "")


def brief(value: object, limit: int = 90) -> str:
    """把工具入参渲染成一行短摘要。

    刻意**只显示前几个键、并截断** —— 时间线的价值在于一眼扫完，
    入参全文在产物 JSON 里，随时可以去看。

    ⚠️ 两条实测教训，都别顺手删：

    · **先 `repr` 再截断**。按原字符串截完再 `repr`，截断点会落在转义序列
      `\\n` 的开头，读起来像是 agent 生成了一段语法错误的补丁，而它其实是对的。
    · **在「键与键之间」断开，不要在值内部硬切**。早先按总长度 `text[:limit]`
      硬切，切出来是 `new='        if` —— 看上去就像补丁的新值只有这两个字符。
      宁可少显示一个键，也不要显示半个值：**半个值会被当成真的**。
    """
    if not isinstance(value, dict):
        text = str(value)
        return text if len(text) <= limit else text[:limit - 1] + "…"

    parts: list[str] = []
    used = 0
    for k, v in value.items():
        shown = repr(v)
        if len(shown) > 42:
            # repr 出来的字符串两端带引号，截断后要把它收尾的引号补回去
            shown = shown[:41] + ("…'" if isinstance(v, str) else "…")
        piece = f"{k}={shown}"
        # 第一个键无论多长都要显示，否则这个调用会渲染成空白
        if parts and used + 2 + len(piece) > limit:
            parts.append("…")
            break
        used += (2 if parts else 0) + len(piece)
        parts.append(piece)
    return ", ".join(parts)


# ── 载入：把两种产物统一成一种形状 ────────────────────────────────────────
@dataclass
class Loaded:
    path: Path
    source: str
    run: dict
    steps: list[dict]
    termination: dict
    caveats: list[str] = field(default_factory=list)
    # 数据缺失导致**跑不了**的检测项。空列表 = 全部可跑。
    unavailable: list[str] = field(default_factory=list)
    paired_by: str = "tool_use_id"


def load(path: Path) -> Loaded:
    data = json.loads(path.read_text(encoding="utf-8"))

    trace = data.get("trace")
    if isinstance(trace, dict) and trace.get("steps"):
        return Loaded(
            path=path,
            source=f"trace v{trace.get('trace_version', '?')}（本次运行自带 trace）",
            run=trace.get("run") or {},
            steps=trace["steps"],
            termination=trace.get("termination") or {},
            caveats=list(trace.get("caveats") or []),
            paired_by="tool_use_id",
        )

    # ── 旧产物：只有 metrics + events ──────────────────────────────────────
    # 转换是**有损的**，而且损失的三样恰好正是回答「为什么卡住」要用的东西。
    # 所以这里不仅转换，还把损失**登记到 unavailable**，让报告说出来。
    events = data.get("events")
    if not events:
        raise SystemExit(
            f"{path.name} 里既没有 trace，也没有 events —— 它可能是一行中止记录"
            "（配置错误 / 靶场基线为绿），那一轮 agent 根本没跑。"
        )

    metrics = data.get("metrics") or {}
    steps: list[dict] = []
    for e in events:
        kind = e.get("type")
        if kind == "tool_use":
            steps.append({"type": "tool_call", "tool": e.get("tool"),
                          "input": e.get("input") or {}, "tool_use_id": None})
        elif kind == "tool_result":
            steps.append({"type": "tool_result", "tool_use_id": None,
                          "chars": e.get("chars"), "preview": e.get("text") or "",
                          "is_error": None})
        elif kind == "assistant_text":
            steps.append({"type": "assistant_text", "preview": e.get("text") or "",
                          "chars": len(e.get("text") or "")})
    return Loaded(
        path=path,
        source="events-v0（旧产物，**有损**转换）",
        run={"label": metrics.get("label"), "fixture": metrics.get("fixture"),
             "arm": {"repo_map_default": metrics.get("repo_map_default"),
                     "source": metrics.get("repo_map_default_source")}},
        steps=steps,
        termination={"outcome": "success" if metrics.get("success") else "failed",
                     "baseline_exit_code": metrics.get("baseline_exit_code"),
                     "final_exit_code": metrics.get("final_exit_code"),
                     "agent_error": metrics.get("agent_error"),
                     # ⚠️ 这几个在 v0 里躺在 **metrics 顶层**，不在 termination 下面。
                     #    不映射的话报告会打印「改动文件：(无)」—— 而那一轮明明
                     #    改对了文件、并且判成了 success（自相矛盾，且不看旧代码发现不了）。
                     "changed_files": metrics.get("changed_files") or [],
                     "test_files_touched": metrics.get("test_files_touched") or [],
                     "edits_outside_scope": metrics.get("edits_outside_scope") or [],
                     "cost_usd": metrics.get("cost_usd"),
                     "elapsed_s": metrics.get("elapsed_s"),
                     "usage": {"input_tokens": metrics.get("input_tokens"),
                               "output_tokens": metrics.get("output_tokens")}},
        caveats=["旧产物没有 t_ms / tool_use_id / is_error，"
                 "下面的时间线与检测能力都是打折的。", *V0_CAVEATS],
        unavailable=list(V0_UNAVAILABLE),
        paired_by=V0_PAIRED_BY,
    )


# ── 配对：把 tool_call 与它的结果配起来 ────────────────────────────────────
def pair_calls(steps: list[dict], *, by_id: bool) -> list[dict]:
    """返回按时间顺序的调用列表（外加配不上任何调用的「孤儿结果」）。

    `by_id=True` 走 `tool_use_id`（v1）；否则**退化成按发出顺序配对**（v0）。
    退化那条路径是有代价的：并行调用时结果按**完成**顺序回来，而调用按
    **发出**顺序排队，两者不一致时就会错配。所以：
      · 用 FIFO（先发出的先配）—— 这是**猜的**，不是事实。
        但它比 LIFO 更接近真实：同一批并行调用里，先发出的通常先回来。
      · 返回值里带 `paired_by`，报告必须把「这里是猜的」说出来。

    ⚠️ 「配不上就悄悄丢」是这里真踩过的坑：早先 `pending_order` 为 None 时
    直接 `continue`，于是真实的旧产物里 `list_files` 被渲染成「无结果」——
    看上去像工具没返回，实际是配对被丢掉了。现在一律记成孤儿结果。
    """
    calls: list[dict] = []
    pending_calls: dict[str, dict] = {}
    pending_order: list[dict] = []

    for i, s in enumerate(steps):
        kind = s.get("type")
        if kind == "tool_call":
            call = {"index": i, "seq": s.get("seq"), "turn": s.get("turn"),
                    "t_ms": s.get("t_ms"), "tool": s.get("tool"),
                    "input": s.get("input") or {}, "tool_use_id": s.get("tool_use_id"),
                    "chars": None, "is_error": None, "preview": "", "result_seq": None}
            calls.append(call)
            if by_id and call["tool_use_id"]:
                pending_calls[call["tool_use_id"]] = call
            else:
                pending_order.append(call)
        elif kind == "tool_result":
            target = None
            if by_id and s.get("tool_use_id"):
                target = pending_calls.pop(s.get("tool_use_id"), None)
            elif not by_id and pending_order:
                target = pending_order.pop(0)
            if target is None:
                # 配不上就**留下痕迹**：v0 的并行调用会走到这里。
                calls.append({"index": i, "seq": s.get("seq"), "turn": s.get("turn"),
                              "t_ms": s.get("t_ms"), "tool": None, "input": {},
                              "tool_use_id": s.get("tool_use_id"),
                              "chars": s.get("chars"), "is_error": s.get("is_error"),
                              "preview": s.get("preview") or "",
                              "result_seq": s.get("seq"), "orphan": True})
                continue
            target["chars"] = s.get("chars")
            target["is_error"] = s.get("is_error")
            target["preview"] = s.get("preview") or ""
            target["result_seq"] = s.get("seq")
            target["result_t_ms"] = s.get("t_ms")
    return calls


def call_signature(call: dict) -> str:
    """「同一个动作」的判据：工具名 + 规范化后的入参。"""
    return (f"{short_name(call['tool'] or '?')}"
            f"({json.dumps(call['input'], sort_keys=True, ensure_ascii=False)})")


def exit_code_of(call: dict) -> int | None:
    """从 run_tests 的结果预览里取退出码。

    安全的前提是**它永远在第一行**（`code_tools_server.py:566`：
    `f"退出码 = {proc.returncode}\\n{'─' * 40}\\n{output}"`），所以 1200 字符的
    预览一定含有它。这条前提写在这里而不是默默依赖 —— 服务端把退出码挪到
    输出末尾的那天，这个检测会**静默失效**。
    """
    if short_name(call.get("tool") or "") != "run_tests":
        return None
    m = EXIT_CODE_PAT.search(call.get("preview") or "")
    return int(m.group(1)) if m else None


# ── 分析 ──────────────────────────────────────────────────────────────────
def analyze(loaded: Loaded) -> dict:
    calls = pair_calls(loaded.steps, by_id=(loaded.paired_by == "tool_use_id"))
    by_id_mode = loaded.paired_by == "tool_use_id"
    findings: list[dict] = []

    # ① 重复动作：同一个 (工具, 入参) 再来一次，**而且中间什么都没改**。
    #
    # 两个限定都是自检逼出来的，别顺手删掉：
    #  · 只判**带参**调用。`run_tests()` / `list_files()` 这类零参调用，签名
    #    相同是必然的 —— 重复与否不携带信息。自检用例 1 最初就是被两次
    #    `run_tests()` 多报了一条（期望 1 条、实得 2 条），是自检把它逮住的。
    #  · 只在「中间没有成功的写操作」时才报。改完代码再读一遍同一个文件、
    #    再跑一次测试，都是**正常的下一步**，不是打转。
    seen: dict[str, dict] = {}
    writes = 0
    for c in calls:
        if c.get("tool") is None:
            continue
        if short_name(c["tool"]) == "apply_patch" and not c.get("is_error"):
            writes += 1
        if not c.get("input"):
            continue
        sig = call_signature(c)
        prev = seen.get(sig)
        if prev is None:
            seen[sig] = {"seq": c["seq"], "n": 1, "writes": writes}
        elif prev["writes"] == writes:
            prev["n"] += 1
            findings.append({"kind": "repeat_call", "seq": c["seq"], "turn": c.get("turn"),
                             "detail": f"{sig} 第 {prev['n']} 次（首次 #{prev['seq']}）"
                                       f"—— 中间没有任何成功的写操作"})
        else:
            # 中间改过 → 文件已经不是原来那份了，重新起算
            seen[sig] = {"seq": c["seq"], "n": 1, "writes": writes}

    # ② 工具报错（v0 无此字段 → 跑不了，不假装查过）
    if by_id_mode:
        for c in calls:
            if c.get("is_error"):
                findings.append({"kind": "tool_error", "seq": c["seq"], "turn": c.get("turn"),
                                 "detail": f"{short_name(c['tool'] or '?')} 返回 is_error"
                                           f"｜{(c.get('preview') or '')[:120]}"})

    # ③ 孤儿结果：没配上任何一次调用。
    #    两种模式都要报 —— v0 恰恰是**更容易**产生孤儿的那一侧（并行调用 + 顺序配对），
    #    早先只在 v1 模式下检查，等于把最需要这条检测的产物排除在外。
    #    但两种模式下含义不同，detail 必须分清，否则「配对错位」会被读成
    #    「工具多返回了一个结果」。
    for c in calls:
        if c.get("orphan"):
            why = ("按 id 找不到对应的调用" if by_id_mode
                   else "按发出顺序找不到待配的调用（v0 无 id，并行调用就会错位）")
            findings.append({"kind": "orphan_result", "seq": c.get("seq"),
                             "turn": c.get("turn"),
                             "detail": f"{why}；tool_use_id={c.get('tool_use_id')!r}，"
                                       f"该结果 {c.get('chars')} 字符"})

    # ④ 「改了但没变好」：连续两次 run_tests 退出码相同。
    #
    # ⚠️ 两个条件都是被**真实旧产物**打回来才补上的（自检的合成数据盖不住）：
    #   · **必须非 0**。连续两次都 0 是「改完复验了两遍」，是正常行为 ——
    #     拿真实的 A/B 产物试渲染时，它被误报过一条「连续 2 次都是 0」。
    #   · detail 必须说清中间**到底改没改代码**：「改了还是失败」与
    #     「没改就原地重跑」是两种不同的毛病，混成一句话就等于没说。
    writes_done = 0
    prev_rt: dict | None = None
    for c in calls:
        if short_name(c.get("tool") or "") == "apply_patch" and not c.get("is_error"):
            writes_done += 1
        code = exit_code_of(c)
        if code is None:
            continue
        if prev_rt is not None and prev_rt["code"] == code and code != 0:
            why = ("中间改过代码却没让它变好" if writes_done > prev_rt["writes"]
                   else "中间没改代码，是原地重跑")
            findings.append({"kind": "stalled_tests", "seq": c["seq"], "turn": c.get("turn"),
                             "detail": f"run_tests 连续 2 次都是退出码 {code}"
                                       f"（#{prev_rt['seq']}、#{c['seq']}）—— {why}"})
        prev_rt = {"code": code, "seq": c["seq"], "writes": writes_done}

    # ⑤ 慢步：相邻两条记录的观察间隔
    if by_id_mode:
        for prev_s, s in zip(loaded.steps, loaded.steps[1:]):
            a, b = prev_s.get("t_ms"), s.get("t_ms")
            if isinstance(a, int) and isinstance(b, int) and b - a > SLOW_STEP_MS:
                findings.append({"kind": "slow_step", "seq": s.get("seq"), "turn": s.get("turn"),
                                 "detail": f"距上一条记录 {(b - a) / 1000:.1f}s"
                                           f"（阈值 {SLOW_STEP_MS / 1000:.0f}s）"
                                           f"—— 这一步在等什么"})

    term = loaded.termination
    result = term.get("result") or {}

    # ⑥ 权限被拦 —— 陷阱 15 那类卡点，旧产物里完全不可见
    for d in (result.get("permission_denials") or []):
        findings.append({"kind": "permission_denied", "seq": None, "turn": None,
                         "detail": f"被拒：{json.dumps(d, ensure_ascii=False)[:200]}"})

    # ⑦ agent 说「完成」而独立 pytest 说没完成 —— 这一条最要紧
    if term.get("agent_stop") == "success" and term.get("outcome") == "failed":
        findings.append({"kind": "claimed_but_failed", "seq": None, "turn": None,
                         "detail": "ResultMessage.subtype=success，但独立 pytest 判定 failed"
                                   " —— 自述与判据不一致"})

    # ⑧ 撞上限
    if term.get("agent_stop") == "error_max_turns":
        findings.append({"kind": "max_turns", "seq": None, "turn": None,
                         "detail": f"撞到 max_turns={loaded.run.get('max_turns')}"})

    if term.get("agent_error"):
        findings.append({"kind": "agent_exception", "seq": None, "turn": None,
                         "detail": str(term["agent_error"])[:300]})

    # ⑨ 该改而一次都没改：判定未成功，且全程没调过写工具
    patched = any(short_name(c.get("tool") or "") == "apply_patch" for c in calls)
    if term.get("outcome") == "failed" and not patched and calls:
        findings.append({"kind": "never_attempted_edit", "seq": None, "turn": None,
                         "detail": "判定未成功，而全程一次 apply_patch 都没调 ——"
                                   " 要分清「没诊断出来」与「诊断出来了但动不了手」"})

    # ⑩ 不认识的块（有就说明 SDK 的行为与记录器假设不一致了）
    for s in loaded.steps:
        if s.get("type") == "unknown_block":
            findings.append({"kind": "unknown_block", "seq": s.get("seq"), "turn": s.get("turn"),
                             "detail": f"{s.get('where')} 消息里有不认识的块："
                                       f"{s.get('block_type')}（字段 {s.get('fields')}）"})

    # ── 统计（全部现算）──────────────────────────────────────────────────
    per_tool: dict[str, dict] = {}
    total_chars = 0
    for c in calls:
        if c.get("tool") is None:
            continue
        name = short_name(c["tool"])
        slot = per_tool.setdefault(name, {"calls": 0, "chars": 0})
        slot["calls"] += 1
        if isinstance(c.get("chars"), int):
            slot["chars"] += c["chars"]
            total_chars += c["chars"]

    turns = [s for s in loaded.steps if s.get("type") == "turn"]
    texts = [s for s in loaded.steps if s.get("type") == "assistant_text"]
    thinking = [s for s in loaded.steps if s.get("type") == "thinking"]

    return {
        "file": loaded.path.name,
        "source": loaded.source,
        "paired_by": loaded.paired_by,
        "unavailable": loaded.unavailable,
        "caveats": loaded.caveats,
        "run": loaded.run,
        "termination": term,
        "steps": loaded.steps,
        "calls": calls,
        "findings": findings,
        "stats": {
            "tool_calls": sum(v["calls"] for v in per_tool.values()),
            "total_result_chars": total_chars,
            "per_tool": per_tool,
            "turns": len(turns),
            "assistant_texts": len(texts),
            # ⚠️ 实测过两个方向，所以这个 0 不能一概而论：
            #    真实运行里 SDK **确实发出过** thinking 块（这次 5 条）；
            #    但也有过一条都没发的情况。为 0 时的含义是
            #    「这一轮没有思维内容可看」，不是「记录器漏了」——
            #    这两件事要分开，否则「看不到」会被误读成「没记」。
            "thinking_blocks": len(thinking),
            "run_tests_exit_codes": [c for c in (exit_code_of(x) for x in calls)
                                     if c is not None],
            "apply_patch_count": per_tool.get("apply_patch", {}).get("calls", 0),
            "repo_map_count": per_tool.get("repo_map", {}).get("calls", 0),
        },
        "usage_per_turn": [s.get("usage") for s in turns],
    }


# ── 渲染 ──────────────────────────────────────────────────────────────────
def _hr(ch: str = "─", n: int = 72) -> str:
    return ch * n


def usage_line(u: object) -> str:
    if not isinstance(u, dict):
        return str(u)
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens")
    got = [f"{k.replace('_tokens', '')}={u[k]}" for k in keys if isinstance(u.get(k), int)]
    return "  ".join(got) if got else json.dumps(u, ensure_ascii=False)


def render(analysis: dict, *, max_text: int = 160) -> None:
    run = analysis["run"]
    term = analysis["termination"]
    stats = analysis["stats"]

    print(_hr("═"))
    print(f"全链路 trace｜{analysis['file']}")
    print(f"  来源      ：{analysis['source']}")
    print(f"  配对依据  ：{analysis['paired_by']}")
    if run.get("label"):
        print(f"  label     ：{run.get('label')}   fixture={run.get('fixture')}")
    if run.get("fixture_head"):
        print(f"  靶场 HEAD ：{str(run['fixture_head'])[:12]}  "
              f"dirty={run.get('fixture_dirty')}  root={run.get('fixture_root')}")
    arm = run.get("arm") or {}
    if arm.get("repo_map_default") is not None:
        print(f"  实验条件  ：repo_map_default={arm.get('repo_map_default')!r}"
              f"（来源 {arm.get('source')}）")
    sandbox = run.get("sandbox") or {}
    if sandbox:
        print(f"  沙箱      ：tools={sandbox.get('tools')}  "
              f"permission_mode={sandbox.get('permission_mode')}  "
              f"builtin 禁用 {len(sandbox.get('disallowed_tools') or [])} 个")
    print(f"  基线      ：pytest 退出码 {term.get('baseline_exit_code')}")
    print(_hr("═"))

    if analysis["unavailable"]:
        print("\n⚠️ 本次产物缺失数据，以下检测项**跑不了**（不是「没问题」）：")
        for item in analysis["unavailable"]:
            print(f"     · {item}")

    # ── 时间线 ──
    # 走 `steps` 而不是 `calls`：思维原文与 agent 自己说的话也在 steps 里，
    # 而它们恰好是「它为什么这么做」最直接的证据。只渲染工具调用的话，
    # 时间线就退化成一张「它做了什么」的清单 —— 那正是这个脚本要解决的问题。
    print(f"\n时间线（{stats['tool_calls']} 次工具调用 / {stats['turns']} 回合）")
    by_index = {c["index"]: c for c in analysis["calls"]}
    for i, s in enumerate(analysis["steps"]):
        kind = s.get("type")
        if kind == "turn":
            print(f"  {_hr('─', 3)} 回合 {s.get('turn')} {_hr('─', 3)}")
        elif kind in TEXT_STEPS:
            text = " ".join((s.get("preview") or "").split())
            if text:
                ell = "…" if len(text) > max_text else ""
                print(f"     {TEXT_STEPS[kind]} {text[:max_text]}{ell}")
        elif kind == "unknown_block":
            print(f"     ⚠ 不认识的块：{s.get('where')} / {s.get('block_type')}"
                  f"（字段 {s.get('fields')}）")
        elif kind == "tool_result":
            c = by_index.get(i)
            if c is not None and c.get("orphan"):
                print(f"  #{c.get('seq')}  ⚠ 孤儿结果 tool_use_id={c.get('tool_use_id')!r}"
                      f"  {c.get('chars')} 字符")
        elif kind == "tool_call":
            c = by_index.get(i)
            if c is None or c.get("tool") is None:
                continue
            gap = f" · +{c['t_ms'] / 1000:.1f}s" if isinstance(c.get("t_ms"), int) else ""
            size = f"{c['chars']:,} 字符" if isinstance(c.get("chars"), int) else "无结果"
            flag = " ❌" if c.get("is_error") else ""
            code = exit_code_of(c)
            code_s = f" · 退出码={code}" if code is not None else ""
            print(f"  #{c.get('seq')}  {short_name(c['tool'])}({brief(c['input'])})"
                  f"  → {size}{code_s}{gap}{flag}")
        else:
            # `note()` 写出来的步骤：type 就是调用方给的 kind。
            # 不认识的也照样打出来 —— 静默丢正是这个脚本要消灭的东西。
            extra = {k: v for k, v in s.items()
                     if k not in ("type", "seq", "t_ms", "turn", "preview")}
            print(f"     ⚠ [{kind}] {json.dumps(extra, ensure_ascii=False)[:200]}")

    # 逐回合 token（只有 v1 有）
    usage = [(i + 1, u) for i, u in enumerate(analysis["usage_per_turn"]) if u]
    if usage:
        print("\n逐回合 token 用量：")
        for turn_no, u in usage:
            print(f"  回合 {turn_no:<3} {usage_line(u)}")

    # ── 卡点 ──
    print(f"\n{_hr('═')}")
    if analysis["findings"]:
        print(f"卡点检测：{len(analysis['findings'])} 条")
        for f in analysis["findings"]:
            mark = "❌" if f["kind"] in ERROR_KINDS else "⚠"
            where = f"#{f['seq']}" if f.get("seq") is not None else "  "
            print(f"  {mark} {where:<6} [{f['kind']}] {f['detail']}")
    else:
        print("卡点检测：0 条")
        if analysis["unavailable"]:
            print("  ⚠️ 但上面的检测项没跑全 —— 这个 0 不能当成「干净」。")
    print(_hr("═"))

    # ── 收尾 ──
    print("收尾：")
    print(f"  独立 pytest 判定 ：{term.get('outcome')}"
          f"（{term.get('baseline_exit_code')} → {term.get('final_exit_code')}）")
    print(f"  CLI 自己的说法    ：subtype={term.get('agent_stop')}")
    result = term.get("result") or {}
    if result:
        print(f"  ResultMessage    ：num_turns={result.get('num_turns')}  "
              f"duration={result.get('duration_ms')}ms  "
              f"is_error={result.get('is_error')}  "
              f"terminal_reason={result.get('terminal_reason')}")
        print(f"  权限被拒         ：{len(result.get('permission_denials') or [])} 次  "
              f"errors={result.get('errors')}  "
              f"api_error_status={result.get('api_error_status')}")
    print(f"  改动文件         ：{term.get('changed_files') or '(无)'}")
    print(f"  测试文件被改     ：{term.get('test_files_touched') or '否'}")
    print(f"  越界改动         ：{term.get('edits_outside_scope') or '无'}")
    if term.get("cost_usd") is not None or term.get("elapsed_s") is not None:
        print(f"  成本 / 耗时      ：${term.get('cost_usd')} · {term.get('elapsed_s')}s"
              "（v0 自带；v1 的时长看上面的 duration_ms）")

    # ── 统计 ──
    print("\n统计（从 steps 现算，不读 metrics）：")
    print(f"  结果字符合计 {stats['total_result_chars']:,}")
    for name, v in sorted(stats["per_tool"].items(), key=lambda kv: -kv[1]["chars"]):
        pct = (v["chars"] / stats["total_result_chars"] * 100
               if stats["total_result_chars"] else 0)
        print(f"    {name:<20} {v['calls']:>2} 次  {v['chars']:>8,} 字符  {pct:>5.1f}%")
    print(f"  run_tests 退出码序列：{stats['run_tests_exit_codes']}")
    print(f"  apply_patch={stats['apply_patch_count']}  repo_map={stats['repo_map_count']}  "
          f"thinking 块={stats['thinking_blocks']}")

    if analysis["caveats"]:
        print("\n该产物的自述限制：")
        for c in analysis["caveats"]:
            print(f"  · {c}")


# ── 自检 ──────────────────────────────────────────────────────────────────
def _v1_steps(*, with_permission_denial: bool = True) -> tuple[list[dict], dict, dict]:
    """构造一份**已知有问题**的 trace。预期结果是算出来的，不是抄的。"""
    def call(seq, t, tool, inp, **kw):
        return {"seq": seq, "t_ms": t, "type": "tool_call", "turn": kw.pop("turn", 1),
                "tool": tool, "tool_use_id": kw.pop("tid", f"toolu_{seq}"), "input": inp}

    def res(seq, t, tid, chars, preview, is_error=False):
        return {"seq": seq, "t_ms": t, "type": "tool_result", "tool_use_id": tid,
                "chars": chars, "preview": preview, "is_error": is_error}

    steps = [
        {"seq": 1, "t_ms": 0, "type": "turn", "turn": 1,
         "usage": {"input_tokens": 100, "output_tokens": 20}},
        {"seq": 2, "t_ms": 100, "type": "assistant_text", "turn": 1, "chars": 5,
         "preview": "先看看"},
        call(3, 200, RF, {"path": "a.py"}, tid="t1"),
        res(4, 300, "t1", 100, "code…"),
        call(5, 400, RT, {}, tid="t2"),                    # 零参：不参与重复判定
        res(6, 500, "t2", 200, "退出码 = 1\n────\n1 failed"),
        call(7, 600, RF, {"path": "a.py"}, tid="t3"),      # 与 #3 同签名 → repeat
        res(8, 700, "t3", 100, "code…"),
        call(9, 800, RT, {}, tid="t4"),
        res(10, 900, "t4", 200, "退出码 = 1\n────\n1 failed"),   # → stalled
        call(11, 31000, RF, {"path": "b.py"}, tid="t5"),   # 间隔 30.1s → slow（>30s）
        res(12, 31100, "t5", 100, "code…", is_error=True),  # → tool_error
    ]
    term = {
        "outcome": "failed", "agent_stop": "success",     # → claimed_but_failed
        "baseline_exit_code": 1, "final_exit_code": 1,
        "result": {"permission_denials": [{"tool_name": "mcp__code-tools__apply_patch"}]
                   if with_permission_denial else []},
    }
    run = {"max_turns": 40, "label": "self-test"}
    return steps, term, run


def self_test() -> int:
    """自检：**在代码里构造**输入，再断言分析结果。

    ⚠️ 为什么必须构造输入：项目 CLAUDE.md 陷阱 22 ——
    如果脚本读的是一张写死的期望值表，它只是一份快照，修前修后都「通过」。
    这里的期望值是「输入满足什么条件」，所以输入一变，断言就会跟着变。
    """
    failures: list[str] = []

    def check(cond: bool, what: str) -> None:
        print(f"  {'✅' if cond else '❌'} {what}")
        if not cond:
            failures.append(what)

    def analyze_steps(steps, term=None):
        return analyze(Loaded(path=Path("<self-test>"), source="trace v1", run={},
                              steps=steps, termination=term or {"outcome": "success"}))

    # ── 用例 1：构造一份已知有 7 类问题的 trace ──
    print("用例 1：一份「已知有卡点」的 trace —— 检测器必须全部指出")
    steps, term, run = _v1_steps()
    a = analyze(Loaded(path=Path("<self-test>"), source="trace v1", run=run,
                       steps=steps, termination=term))
    kinds = [f["kind"] for f in a["findings"]]
    for k in ("repeat_call", "stalled_tests", "slow_step", "tool_error",
              "permission_denied", "claimed_but_failed", "never_attempted_edit"):
        check(k in kinds, f"检出 {k}")
    check(kinds.count("repeat_call") == 1,
          f"repeat_call 恰好 1 条（实得 {kinds.count('repeat_call')}）")
    check(kinds.count("stalled_tests") == 1,
          f"stalled_tests 恰好 1 条（实得 {kinds.count('stalled_tests')}）")
    check(a["stats"]["run_tests_exit_codes"] == [1, 1],
          f"退出码序列 = [1, 1]（实得 {a['stats']['run_tests_exit_codes']}）")
    check(a["stats"]["apply_patch_count"] == 0, "apply_patch 计数 = 0")
    check(a["stats"]["turns"] == 1, f"回合数 = 1（实得 {a['stats']['turns']}）")

    # ── 用例 2：同一份 trace 去掉那个权限拒绝 —— 对应检测必须消失 ──
    #    这一条是在验「检测器不是因为别的原因碰巧报出来的」。
    print("\n用例 2：去掉权限拒绝 —— permission_denied 必须随之消失")
    steps2, term2, run2 = _v1_steps(with_permission_denial=False)
    a2 = analyze(Loaded(path=Path("<self-test>"), source="trace v1", run=run2,
                        steps=steps2, termination=term2))
    kinds2 = [f["kind"] for f in a2["findings"]]
    check("permission_denied" not in kinds2, "permission_denied 消失（单变量对照）")
    check(sorted(kinds2) == sorted(k for k in kinds if k != "permission_denied"),
          "其余检测项不受影响")

    # ── 用例 3：干净的一轮 —— 必须是 0 条 ──
    print("\n用例 3：一份「干净」的 trace —— 不能报出任何卡点")
    clean_steps = [
        {"seq": 1, "t_ms": 0, "type": "assistant_text", "turn": 1, "chars": 2, "preview": "好"},
        {"seq": 2, "t_ms": 10, "type": "tool_call", "turn": 1, "tool": RT,
         "tool_use_id": "c1", "input": {}},
        {"seq": 3, "t_ms": 20, "type": "tool_result", "tool_use_id": "c1", "chars": 10,
         "preview": "退出码 = 1\n────\nx", "is_error": False},
        {"seq": 4, "t_ms": 30, "type": "tool_call", "turn": 2,
         "tool": f"{SHORT_PREFIX}apply_patch", "tool_use_id": "c2",
         "input": {"path": "a.py"}},
        {"seq": 5, "t_ms": 40, "type": "tool_result", "tool_use_id": "c2", "chars": 10,
         "preview": "已应用", "is_error": False},
        {"seq": 6, "t_ms": 50, "type": "tool_call", "turn": 3, "tool": RT,
         "tool_use_id": "c3", "input": {}},
        {"seq": 7, "t_ms": 60, "type": "tool_result", "tool_use_id": "c3", "chars": 10,
         "preview": "退出码 = 0\n────\nok", "is_error": False},
    ]
    a3 = analyze_steps(clean_steps, {"outcome": "success", "agent_stop": "success",
                                     "result": {"permission_denials": []}})
    check(a3["findings"] == [], f"0 条卡点（实得 {[f['kind'] for f in a3['findings']]}）")
    check(a3["stats"]["run_tests_exit_codes"] == [1, 0], "退出码序列 = [1, 0]（改完复验过）")

    # ── 用例 4：v0 适配器 —— 跑不了的检测项必须被登记 ──
    print("\n用例 4：v0 旧产物 —— 缺数据的检测项必须报「跑不了」，不能报「没问题」")
    v0 = {"metrics": {"success": False, "label": "old"},
          "events": [{"i": 2, "type": "tool_use", "tool": RT, "input": {}},
                     {"i": 3, "type": "tool_result", "chars": 20, "text": "退出码 = 1\n─\nx"}]}
    tmp = Path("_selftest_v0.json")
    tmp.write_text(json.dumps(v0, ensure_ascii=False), encoding="utf-8")
    try:
        lv0 = load(tmp)
        check(bool(lv0.unavailable), "unavailable 非空（登记了跑不了的项）")
        check(len(lv0.unavailable) == 2, f"登记 2 项（实得 {len(lv0.unavailable)}）")
        a4 = analyze(lv0)
        k4 = [f["kind"] for f in a4["findings"]]
        check("slow_step" not in k4, "slow_step 未被谎报为「没问题」（它压根跑不了）")
        check("tool_error" not in k4, "tool_error 同上")
        check(a4["stats"]["run_tests_exit_codes"] == [1], "退出码仍可从预览解析出来")
    finally:
        tmp.unlink(missing_ok=True)

    # ── 用例 5：配对必须靠 tool_use_id，而不是顺序 ──
    print("\n用例 5：结果乱序返回 —— 按 id 配对必须仍然正确")
    shuffled = [
        {"seq": 1, "t_ms": 0, "type": "tool_call", "turn": 1, "tool": RF,
         "tool_use_id": "x1", "input": {"path": "a.py"}},
        {"seq": 2, "t_ms": 0, "type": "tool_call", "turn": 1, "tool": RF,
         "tool_use_id": "x2", "input": {"path": "b.py"}},
        # 后发的 x2 先回来
        {"seq": 3, "t_ms": 10, "type": "tool_result", "tool_use_id": "x2", "chars": 222,
         "preview": "B", "is_error": True},
        {"seq": 4, "t_ms": 20, "type": "tool_result", "tool_use_id": "x1", "chars": 111,
         "preview": "A", "is_error": False},
    ]
    a5 = analyze_steps(shuffled, {"outcome": "failed"})
    by_id = {c["tool_use_id"]: c for c in a5["calls"] if c.get("tool")}
    check(by_id["x1"]["chars"] == 111, f"x1 配到 111（实得 {by_id['x1']['chars']}）")
    check(by_id["x2"]["chars"] == 222, f"x2 配到 222（实得 {by_id['x2']['chars']}）")
    check(by_id["x2"]["is_error"] is True, "x2 的 is_error 正确落到 x2 上")
    check(not any(c.get("orphan") for c in a5["calls"]), "没有孤儿结果")

    # ── 用例 6 + 对照 7：一条规则的两个方向 ──
    # 「改过之后再读同一处算不算打转」是 repeat_call 里唯一带判断的地方，
    # 所以两个方向都要钉住 —— 只测一边的话，把整条规则删掉也照样通过。
    print("\n用例 6：读 → 改 → 再读同一个文件 —— 不能报成重复（那是正常的下一步）")
    edit_steps = [
        {"seq": 1, "t_ms": 0, "type": "tool_call", "turn": 1, "tool": RF,
         "tool_use_id": "e1", "input": {"path": "a.py"}},
        {"seq": 2, "t_ms": 10, "type": "tool_result", "tool_use_id": "e1", "chars": 50,
         "preview": "old", "is_error": False},
        {"seq": 3, "t_ms": 20, "type": "tool_call", "turn": 2,
         "tool": f"{SHORT_PREFIX}apply_patch", "tool_use_id": "e2",
         "input": {"path": "a.py", "old": "x", "new": "y"}},
        {"seq": 4, "t_ms": 30, "type": "tool_result", "tool_use_id": "e2", "chars": 20,
         "preview": "已应用", "is_error": False},
        {"seq": 5, "t_ms": 40, "type": "tool_call", "turn": 3, "tool": RF,
         "tool_use_id": "e3", "input": {"path": "a.py"}},
        {"seq": 6, "t_ms": 50, "type": "tool_result", "tool_use_id": "e3", "chars": 50,
         "preview": "new", "is_error": False},
    ]
    a6 = analyze_steps(edit_steps, {"outcome": "success"})
    k6 = [f["kind"] for f in a6["findings"]]
    check("repeat_call" not in k6, f"不报 repeat_call（实得 {k6}）")

    print("\n用例 7（用例 6 的对照）：把中间那次 apply_patch 拿掉 —— 必须报回来")
    no_edit = [s for s in edit_steps if s.get("seq") not in (3, 4)]
    a7 = analyze_steps(no_edit, {"outcome": "failed"})
    k7 = [f["kind"] for f in a7["findings"]]
    check(k7.count("repeat_call") == 1,
          f"报且只报 1 条 repeat_call（实得 {k7.count('repeat_call')}）")

    # ── 用例 8：写失败不算「改过」 ──
    print("\n用例 8：apply_patch 失败（is_error）—— 不能算成「中间改过」")
    failed_edit = [dict(s) for s in edit_steps]
    failed_edit[3]["is_error"] = True          # 那次 apply_patch 报了错
    a8 = analyze_steps(failed_edit, {"outcome": "failed"})
    k8 = [f["kind"] for f in a8["findings"]]
    check(k8.count("repeat_call") == 1,
          f"仍报 repeat_call（实得 {k8.count('repeat_call')}）—— 失败的写没改变文件")

    # ── 用例 9：改完复验两遍都通过 —— 不能报成「卡住」 ──
    #    真实 A/B 旧产物上误报过一条「连续 2 次都是退出码 0」，所以钉住这条：
    #    **非 0 才是卡住**，两次通过只是复验。
    print("\n用例 9：退出码 0 → 0（改完复验了两遍）—— 不能报 stalled_tests")
    two_passes = [dict(s) for s in clean_steps]
    two_passes.append({"seq": 8, "t_ms": 70, "type": "tool_call", "turn": 4, "tool": RT,
                       "tool_use_id": "c4", "input": {}})
    two_passes.append({"seq": 9, "t_ms": 80, "type": "tool_result", "tool_use_id": "c4",
                       "chars": 10, "preview": "退出码 = 0\n────\nok", "is_error": False})
    a9 = analyze_steps(two_passes, {"outcome": "success", "agent_stop": "success",
                                    "result": {"permission_denials": []}})
    k9 = [f["kind"] for f in a9["findings"]]
    check("stalled_tests" not in k9, f"不报 stalled_tests（实得 {k9}）")
    check(a9["stats"]["run_tests_exit_codes"] == [1, 0, 0], "退出码序列 = [1, 0, 0]")

    # ── 用例 10：v0 并行调用 —— 配不上的结果必须留痕，不能消失 ──
    print("\n用例 10：v0 并行调用（use, use, result, result）—— 漏配的结果要留痕")
    v0_parallel = {"metrics": {"success": True, "label": "v0-parallel"},
                   "events": [
                       {"i": 1, "type": "tool_use", "tool": RT, "input": {}},
                       {"i": 2, "type": "tool_use", "tool": RF, "input": {"path": "a.py"}},
                       {"i": 3, "type": "tool_result", "chars": 111, "text": "退出码 = 1\n─\nx"},
                       {"i": 4, "type": "tool_result", "chars": 222, "text": "code…"},
                       {"i": 5, "type": "tool_result", "chars": 333, "text": "多出来的"},
                   ]}
    tmp2 = Path("_selftest_v0_parallel.json")
    tmp2.write_text(json.dumps(v0_parallel, ensure_ascii=False), encoding="utf-8")
    try:
        a10 = analyze(load(tmp2))
        paired = [c for c in a10["calls"] if c.get("tool")]
        orphans = [c for c in a10["calls"] if c.get("orphan")]
        check(len(paired) == 2, f"两次调用都留在列表里（实得 {len(paired)}）")
        check(len(orphans) == 1, f"多出来的那个结果记为孤儿（实得 {len(orphans)}）")
        check(any(f["kind"] == "orphan_result" for f in a10["findings"]),
              "orphan_result 在 v0 模式下也会报（不再被 v1 守卫挡掉）")
        check(paired[0]["chars"] == 111,
              f"FIFO：先发出的调用配到第一个结果（实得 {paired[0]['chars']}）")
    finally:
        tmp2.unlink(missing_ok=True)

    print("\n" + _hr("═"))
    if failures:
        print(f"自检失败：{len(failures)} 条")
        for f in failures:
            print(f"  ❌ {f}")
        return 1
    print("自检通过：全部断言成立")
    return 0


def main() -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="agent-*.json（v1 trace 或 v0 旧产物）")
    ap.add_argument("--json", action="store_true", help="只输出机器可读的结论")
    ap.add_argument("--max-text", type=int, default=160, help="时间线里文本预览的截断长度")
    ap.add_argument("--self-test", action="store_true", help="构造输入并断言检测逻辑")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.path:
        ap.error("需要一个产物路径，或用 --self-test")

    analysis = analyze(load(Path(args.path)))
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        render(analysis, max_text=args.max_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
