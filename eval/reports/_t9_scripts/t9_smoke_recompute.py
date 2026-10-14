"""t9 独立审查 第 9 条：t5 冒烟结论能否从产物**复算**出来。

三件事：
  1. **判定复算**：用 t5 归档的 `agent_candidate_patch.diff`（251,255 B，就是当时判定用的那个文件）
     在一个**全新工作区**上重跑一次判定，与 t5 的 `harness_verdict.json` 逐字段对表。
  2. **成本/耗时复算**：从 `servers/experiments/out/agent-t5-smoke.json` 的 trace 逐回合记录
     重算三口径 input / output / 回合数 / 工具调用数 / 墙钟，与 `eval/reports/p0-smoke.md`
     里写的数字对表（含「去重」与「不去重」两种口径，看它写的是哪一种）。
  3. **自述 vs 判定**：t5 自述「判定：未成功」，harness 判 resolved=True —— 复算这个反差。

用法：python eval/reports/_t9_scripts/t9_smoke_recompute.py
"""
from __future__ import annotations

import json
import os
import re
import sys

PROJ = r"E:\aicoding\mini-claude-code"
for p in (PROJ, os.path.join(PROJ, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

IID = "pallets-click__4fd2fea"
RUN = "t9-smoke-recompute"
T5 = r"E:\aicoding\eval-runs\t5-smoke"
TRACE = os.path.join(PROJ, "servers", "experiments", "out", "agent-t5-smoke.json")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    from eval.harness import run_instance as RI

    print("=" * 96)
    print("1) 判定复算：用 t5 归档的候选补丁在**全新工作区**重跑")
    print("=" * 96)
    t5v = json.load(open(os.path.join(T5, "harness_verdict.json"), encoding="utf-8"))
    patch = open(os.path.join(T5, "agent_candidate_patch.diff"), encoding="utf-8").read()
    print(f"  t5 归档补丁 = {len(patch.encode('utf-8'))} 字节（sha256 运行时算）")
    mine = RI.run_one(IID, patch, run_id=RUN, label="t9-recompute",
                      patch_kind="file:agent_candidate_patch.diff")
    keys = ["verdict_class", "resolved", "patch_apply_exit_code", "touched_tests",
            "test_side_paths", "dropped_non_py_paths", "judged_patch_paths"]
    same = True
    for k in keys:
        a, b = t5v.get(k), mine.get(k)
        flag = "OK " if a == b else "DIFF"
        same &= (a == b)
        print(f"    [{flag}] {k:<24} t5={a!r}  复算={b!r}")
    jm, jt = mine.get("judged") or {}, None
    print(f"    复算 F2P = {jm.get('f2p_total', 0) - len(jm.get('f2p_not_passed', []))}/{jm.get('f2p_total')}"
          f"   复算 P2P 非过 = {jm.get('p2p_not_passed')}")
    # 读 t5 归档的 judged.json
    jp = os.path.join(T5, "t5-smoke-judge", IID, "artifacts", "judged.json")
    if os.path.exists(jp):
        jt = json.load(open(jp, encoding="utf-8"))
        print(f"    t5 归档 judged.json：resolved={jt['resolved']} f2p={jt['f2p_total']} p2p={jt['p2p_total']}"
              f" not_passed={jt['f2p_not_passed']}")
        same &= (jt["resolved"] == jm.get("resolved"))
    print(f"  判定结论逐项一致：{same}")

    print("\n" + "=" * 96)
    print("2) 成本/耗时复算（从 trace 逐回合重算）")
    print("=" * 96)
    doc = json.load(open(TRACE, encoding="utf-8"))
    steps = (doc.get("trace") or {}).get("steps") or []
    turns = [s for s in steps if s.get("type") == "turn"]
    tools = [s for s in steps if s.get("type") == "tool_call"]
    deduped, prev = [], None
    for s in turns:
        u = s.get("usage") or {}
        key = (u.get("input_tokens"), u.get("cache_read_input_tokens"),
               u.get("cache_creation_input_tokens"))
        if prev is not None and key == prev:
            continue
        deduped.append(s)
        prev = key

    def total(seq, field):
        return sum(int((s.get("usage") or {}).get(field) or 0) for s in seq)

    raw = {f: total(turns, f) for f in ("input_tokens", "cache_read_input_tokens",
                                        "cache_creation_input_tokens", "output_tokens")}
    ded = {f: total(deduped, f) for f in raw}
    print(f"  trace steps = {len(steps)}  turn 记录 = {len(turns)}（去重后 {len(deduped)}）"
          f"  tool_call = {len(tools)}")
    print(f"  不去重：input_uncached={raw['input_tokens']} cache_read={raw['cache_read_input_tokens']}"
          f" cache_creation={raw['cache_creation_input_tokens']} output={raw['output_tokens']}")
    print(f"  去重后：input_uncached={ded['input_tokens']} cache_read={ded['cache_read_input_tokens']}"
          f" cache_creation={ded['cache_creation_input_tokens']} output={ded['output_tokens']}")
    m = doc.get("metrics") or {}
    print(f"  metrics.elapsed_s = {m.get('elapsed_s')}  （p0-smoke 写 273 s）")
    print(f"  metrics.tool_calls_total = {m.get('tool_calls_total')}"
          f"  （p0-smoke 说这个字段与实测 77 矛盾）")
    print(f"  agent_error = {str(m.get('agent_error'))[:110]}")
    tool_mix = {}
    for t in tools:
        nm = t.get("tool") or t.get("name") or "?"
        tool_mix[nm] = tool_mix.get(nm, 0) + 1
    print(f"  工具分布（复算）= {tool_mix}")
    print(f"  p0-smoke §成本耗时写的是：input_uncached=45,310 / cache_read=1,916,160 / output=0 /"
          f" 回合 40 / 工具 77 / 墙钟 273 s")
    claimed_ok = (ded["input_tokens"] == 45310 and ded["cache_read_input_tokens"] == 1916160
                  and len(deduped) == 40 and len(tools) == 77 and m.get("elapsed_s") == 273.0)
    print(f"  ⇒ 与「去重口径」逐项一致：{claimed_ok}")

    print("\n" + "=" * 96)
    print("3) 自述 vs 独立判定")
    print("=" * 96)
    sr = json.load(open(os.path.join(T5, "agent_self_report.json"), encoding="utf-8"))
    print(f"  agent 自述（最后一段）：{str(sr.get('assistant_texts', [])[-1:])[:200]}")
    print(f"  agent side agent_error = {str(sr.get('agent_error'))[:140]}")
    txt = ""
    for t in sr.get("assistant_texts", []):
        txt += t + "\n"
    claim = "未成功" in txt or "没有成功" in txt or "not successful" in txt.lower()
    print(f"  自述里出现「未成功」= {claim}")
    print(f"  harness 判定 = {mine['verdict_class']} / resolved={mine['resolved']}")
    print(f"  ⇒ 两者相反：{claim and mine['resolved'] is True}（说明判定不是从自述来的）")

    out = os.path.join(r"E:\aicoding\eval-runs", "t9-smoke-recompute.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rejudge_same_as_t5": same, "verdict_class": mine["verdict_class"],
                   "resolved": mine["resolved"], "judged": mine.get("judged"),
                   "trace_raw": raw, "trace_dedup": ded, "turns": len(deduped),
                   "tools": len(tools), "tool_mix": tool_mix,
                   "elapsed_s": m.get("elapsed_s"), "claimed_ok": claimed_ok}, f,
                  ensure_ascii=False, indent=2)
    print(f"\n  证据：{out}")
    return 0 if (same and claimed_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
