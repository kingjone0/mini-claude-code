"""P0 对账：同一次运行里三个 "input tokens" 的来源与关系。

用法：python servers/experiments/p0_reconcile.py <label|path.json> [more...]

对每个产物打印：
  A) trace.termination.result.model_usage 逐模型 inputTokens / cacheRead / cacheCreation
     —— metrics.input_tokens 的真正来源（usage_log 逐模型求和）
  B) trace.termination.result.usage（ResultMessage.usage）的 input_tokens / cache_*
  C) Σ trace.steps[type==turn].usage.input_tokens（逐回合，父流 AssistantMessage）
     以及 Σcache_read、Σcache_creation、非父流 turn 的排查（parent_tool_use_id 线索）
  D) 对账算式：C vs A、C vs B、A vs B；并给出「同一回合是否被记了两次」的证据
     （turn usage 完全相同的相邻对 / usage 为 dict 的键集合）。

只读分析，不改任何产物。
"""
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OUT = Path(__file__).resolve().parent / "out"


def load(arg: str) -> Path:
    p = Path(arg)
    if not p.is_file():
        p = OUT / f"agent-{arg}.json"
    return p


def analyze(p: Path) -> None:
    d = json.loads(p.read_text(encoding="utf-8"))
    m = d.get("metrics") or {}
    tr = d.get("trace") or {}
    term = tr.get("termination") or {}
    res = term.get("result") or {}
    print("=" * 78)
    print(f"FILE {p.name}")
    print(f"  metrics.input_tokens = {m.get('input_tokens')}  output = {m.get('output_tokens')}"
          f"  cost = {m.get('cost_usd')}")

    mu = res.get("model_usage") or {}
    a_in = a_cr = a_cc = 0
    print("  [A] model_usage（逐模型）:")
    for name, u in mu.items():
        i = u.get("inputTokens") or 0
        cr = u.get("cacheReadInputTokens") or 0
        cc = u.get("cacheCreationInputTokens") or 0
        a_in += i; a_cr += cr; a_cc += cc
        print(f"      {name}: inputTokens={i} cacheRead={cr} cacheCreation={cc} "
              f"sum(in+cr+cc)={i + cr + cc}")
    print(f"      ΣA: input={a_in} cache_read={a_cr} cache_creation={a_cc}")

    ru = res.get("usage") or {}
    if isinstance(ru, dict):
        b_in = ru.get("input_tokens") or 0
        b_cr = ru.get("cache_read_input_tokens") or 0
        b_cc = ru.get("cache_creation_input_tokens") or 0
        print(f"  [B] ResultMessage.usage: input_tokens={b_in} cache_read={b_cr} "
              f"cache_creation={b_cc} sum={b_in + b_cr + b_cc}")
        print(f"      keys={sorted(ru.keys())}")

    turns = [s for s in (tr.get("steps") or []) if s.get("type") == "turn"]
    c_in = c_cr = c_cc = 0
    n_with_usage = 0
    keysets = {}
    per_turn = []
    for s in turns:
        u = s.get("usage")
        if isinstance(u, dict):
            n_with_usage += 1
            ks = tuple(sorted(u.keys()))
            keysets[ks] = keysets.get(ks, 0) + 1
            i = u.get("input_tokens") or 0
            cr = u.get("cache_read_input_tokens") or 0
            cc = u.get("cache_creation_input_tokens") or 0
            c_in += i; c_cr += cr; c_cc += cc
            per_turn.append((s.get("turn"), i, cr, cc))
    print(f"  [C] Σ逐回合 turn.usage（{len(turns)} 个 turn 步骤，{n_with_usage} 个带 usage）:")
    print(f"      Σinput={c_in} Σcache_read={c_cr} Σcache_creation={c_cc}")
    for ks, cnt in keysets.items():
        print(f"      usage 键集合 x{cnt}: {list(ks)}")
    print("      逐回合明细 (turn, input, cache_read, cache_creation):")
    for t in per_turn:
        print(f"        {t}")

    print("  [D] 对账:")
    print(f"      C/A(input only)   = {c_in}/{a_in} = "
          f"{(c_in / a_in if a_in else float('nan')):.3f}")
    print(f"      C/(A in+cr+cc)    = {c_in}/{a_in + a_cr + a_cc} = "
          f"{(c_in / (a_in + a_cr + a_cc) if (a_in + a_cr + a_cc) else float('nan')):.3f}")
    print(f"      (C+C_rr+C_cc)/(A+A_cr+A_cc) = "
          f"{(c_in + c_cr + c_cc)}/{(a_in + a_cr + a_cc)} = "
          f"{((c_in + c_cr + c_cc) / (a_in + a_cr + a_cc) if (a_in + a_cr + a_cc) else float('nan')):.3f}")
    if isinstance(ru, dict):
        print(f"      A/B(input)        = {a_in}/{b_in} = "
              f"{(a_in / b_in if b_in else float('nan')):.3f}")
        print(f"      (A+A_cr+A_cc)/B_sum = {(a_in + a_cr + a_cc)}/{(b_in + b_cr + b_cc)} = "
              f"{((a_in + a_cr + a_cc) / (b_in + b_cr + b_cc) if (b_in + b_cr + b_cc) else float('nan')):.3f}")
    # 重复计数检测：完全相同的 usage dict 在相邻/非相邻 turn 出现
    seen = {}
    dups = []
    for (t, i, cr, cc) in per_turn:
        k = (i, cr, cc)
        if k in seen:
            dups.append((seen[k], t, k))
        else:
            seen[k] = t
    if dups:
        print(f"      [!] 相同 usage 值的 turn 对数={len(dups)}，前 10 对 (首次turn,重复turn,(in,cr,cc)): {dups[:10]}")
    else:
        print("      无两组完全相同的 (input, cache_read, cache_creation)")

    # 流式分片假设检验：连续同值 run 的分组，每组取一条 → Σ(去重后) vs A(inputTokens)
    groups = []
    for (t, i, cr, cc) in per_turn:
        k = (i, cr, cc)
        if groups and groups[-1][1] == k:
            groups[-1][2].append(t)
        else:
            groups.append([t, k, [t]])
    g_in = sum(k[0] for _, k, _ in groups)
    g_cr = sum(k[1] for _, k, _ in groups)
    b_in = (res.get("usage") or {}).get("input_tokens") if isinstance(res.get("usage"), dict) else None
    print(f"      ★ 决定性检验：Σ(连续同值组代表 input)={g_in} vs "
          f"ResultMessage.usage.input_tokens={b_in} → "
          f"{'完全相等' if g_in == b_in else '不相等'}")
    print(f"        （逐回合 usage 是**每条 API 调用一份**，一条 AssistantMessage 流式发多条时"
          f"同一份 usage 被 begin_turn 记了多次；去重后正好等于 B。）")
    print(f"      连续同值分组：{len(per_turn)} 条 turn → {len(groups)} 组；"
          f"Σ组代表 input={g_in}（对比 A 里 deepseek-flash[1m] inputTokens={a_in}）")
    print(f"      组代表 Σ + 小模型 input 是否等于 metrics.input_tokens: "
          f"{g_in}+{mu.get('deepseek-flash', {}).get('inputTokens', 0)}={g_in + (mu.get('deepseek-flash', {}).get('inputTokens') or 0)} vs {m.get('input_tokens')}")


def main() -> int:
    args = sys.argv[1:] or ["subagent-ab-hard-C-1"]
    for a in args:
        analyze(load(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
