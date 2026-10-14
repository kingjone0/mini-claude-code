"""把每次运行的用量追加成一行 JSONL。

为什么要自己记：第三方端点的账单只给总额，给不出「这一次实验花了多少」的粒度。
第三阶段做 SWE-bench 消融时要按 run 对比成本，账单粒度不够 —— 所以脚本自己记。

落盘：`servers/experiments/out/usage.jsonl`（追加，一次运行一行）。

字段（`models` 之外的平铺字段都是**合计值**，便于直接拿去看趋势）：

    ts            本地时区 ISO 时间
    run           RUN_LABEL 环境变量，缺省 "default"
    script        哪个脚本跑的
    key_source    ★ 这次用的是哪条 key —— 见下方说明
    model         主模型（用量最大的那个）
    input/output/cache_read   各模型合计的 token 数
    input_uncached / cache_read / cache_creation
                    ★ P0 起三个 input 口径**分开记**（④ 层 AB 实验的硬要求）：
                      `input` 一直是 **model_usage.inputTokens = 未缓存输入**，
                      以前 cache_read 有合计、cache_creation 只在 models 里、
                      且没人给 `input` 的语义命名 —— 现在三个都有平铺键。
                      ⚠️ `input_tokens`（= 本行的 `input`）**不是**「全部输入」：
                      真实 prompt 规模是 input_uncached + cache_read + cache_creation。
                      C-1 实测：65,027 vs 1,250,691，差 19 倍。
    num_turns / subtype / is_error / total_cost_usd
    result_usage    ★ ResultMessage.usage 原样留档（P0 对账用）。它和 model_usage
                    口径不同：只含主 CLI 会话的回合，不含 subagent/title 等旁路调用。
                    C-1 实测 usage.input_tokens=35,468 < model_usage 的 65,027。
    models        逐模型明细（一次运行可能有多个模型，比如 generate_session_title）

★ P0 结论（「trace 逐回合 Σinput ≈ 2× model_usage」的成因，已修）：
   逐回合 turn.usage 是**每条 API 调用一份**，而一条 AssistantMessage 会流式发出
   多条消息、同一份 usage 被复制在每条上 —— begin_turn 曾按消息计数，把同一份
   usage 记了 2~5 次（C-1 原始 Σinput=132,690 = 2.003× metrics.input_tokens；
   A-1 更甚 108,023 = 3.39×）。去重办法实测有效：**Σ(连续同值组代表) 精确等于
   ResultMessage.usage.input_tokens**（C-1: 35,468=35,468；A-1: 30,913=30,913）。
   coding_agent.py 现在只在 usage 与前一条不同时才开新回合（turn 语义随之变成
   「一次 API 调用」，与 num_turns 依旧不是一回事）。验证脚本：out/p0_reconcile.py。

★ key_source 是这次改造的重点：不做这个标注，将来分不清某次实验用的是哪个 key，
   账单还是归因不了。取值形如 `env:.env` / `fallback:~/.claude/settings.json`。
"""

import json
import os
from datetime import datetime
from pathlib import Path

USAGE_LOG = Path(__file__).resolve().parent / "out" / "usage.jsonl"


def _line(path, obj) -> None:
    """往 path 追加一行 JSON（UTF-8，与 USAGE_LOG 同纪律）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _f(v):
    """数值字段统一处理：None 保留为 None，别写成 0 —— 0 和『没数据』是两回事。"""
    return v


def plain_result_usage(result) -> dict | None:
    """ResultMessage.usage 原样转成可 JSON 序列化的 dict（P0 对账留档用）。

    为什么记它：`model_usage` 与 `usage` 口径不同 —— C-1 实测
    usage.input_tokens=35,468，而 model_usage 里 deepseek-flash[1m] 的
    inputTokens=65,027（后者含 generate_session_title 等旁路调用）。
    两个都留在同一行，跨运行汇总才能分开报，不用跨文件拼。
    对象形状不做假设：认得 dict/标量就递归，认不得的整体 str() 截断。
    """
    u = getattr(result, "usage", None) if result is not None else None
    if u is None:
        return None
    if isinstance(u, (bool, int, float, str)):
        return {"_raw": u}
    if isinstance(u, dict):
        return {str(k): (v if isinstance(v, (bool, int, float, str, type(None)))
                         else str(v)[:400]) for k, v in u.items()}
    items = getattr(u, "items", None)
    if callable(items):
        try:
            return {str(k): (v if isinstance(v, (bool, int, float, str, type(None)))
                             else str(v)[:400]) for k, v in items()}
        except Exception:
            pass
    return {"_raw": str(u)[:400]}


def usage_from_trace(trace: dict | None) -> dict:
    """从 trace 的逐回合记录里**回填**三口径与 output（t8 / D6 修）。

    为什么必须回填：agent 异常路径（撞回合上限、端点报错等）下 `result is None`
    ⇒ `model_usage` 为空 ⇒ `log_usage` 把 input/output/cache 全写 null，而
    `summarize(events)` 也因 `events` 为空而全 0（实测 `tool_calls_total=0`，
    但 trace 里明明有 **77 次**工具调用）。后果是 **P1 的成本外推会系统性漏掉
    所有失败轮 —— 而失败轮往往是最贵的那批**（跑满回合上限）。

    trace 里逐回合的 usage 是齐的，所以从那里取。口径与预注册 §10.1 对齐：
      · input_uncached  = Σ 逐回合 input_tokens（**去重后**）
      · cache_read      = Σ cache_read_input_tokens
      · cache_creation  = Σ cache_creation_input_tokens
      · output          = Σ output_tokens
    去重规则（与 trace 自带的 caveat 一致）：连续两条带**完全相同 usage** 的助手消息
    属同一次 API 调用的流式分片，只记第一份。

    返回 dict；trace 不可用时返回全 None（**不伪造 0**：没数据与花 0 元必须能区分）。
    """
    empty = {"input_uncached": None, "cache_read": None, "cache_creation": None,
             "output": None, "num_turns": None, "tool_calls_total": None,
             "turn_records_raw": None, "turn_records_deduped": None}
    if not isinstance(trace, dict):
        return empty
    steps = trace.get("steps") or []
    turns = [s for s in steps if s.get("type") == "turn"]
    if not turns:
        return empty

    def _i(v):
        return int(v) if isinstance(v, (int, float)) else 0

    deduped, prev = [], None
    for s in turns:
        u = s.get("usage") or {}
        key = (u.get("input_tokens"), u.get("cache_read_input_tokens"),
               u.get("cache_creation_input_tokens"))
        if prev is not None and key == prev:
            continue
        deduped.append(s)
        prev = key

    def total(field):
        return sum(_i((s.get("usage") or {}).get(field)) for s in deduped)

    # ⚠ trace 里工具调用的 type 是 `tool_call`（不是 tool_use/tool）—— 用错名字会得到
    # 「0 次调用」而看起来像 agent 没干活（t5 实测踩过）。
    tools = [s for s in steps if s.get("type") == "tool_call"]
    return {
        "input_uncached": total("input_tokens"),
        "cache_read": total("cache_read_input_tokens"),
        "cache_creation": total("cache_creation_input_tokens"),
        "output": total("output_tokens"),
        "num_turns": len(deduped),
        "tool_calls_total": len(tools),
        "turn_records_raw": len(turns),
        "turn_records_deduped": len(deduped),
    }


def log_usage(result, *, script: str, key_source: str, extra: dict | None = None,
              log_path: Path | None = None, trace: dict | None = None) -> dict:
    """把一次运行的用量追加到 JSONL，并把写进去的那一行返回（便于顺手打印）。

    `result` 是 ResultMessage；传 None 也表示一次真实运行（比如运行没正常结束），
    这时 token 字段留 None 而不是 0 —— 「花了 0 元」和「没拿到数据」必须能区分。

    `trace`（t8 / D6 新增，可选）：trace 的 `steps` 结构。给了它且 `result is None` 时，
    从逐回合 usage **回填**三口径/output/回合数/工具调用数，并标 `usage_source="trace"`。

    ⚠ **措辞更正（t9 独立审查抓出，t10 落地）**：t8 在提交信息里说「不传 `trace` 时
    行为与改动前**逐字一致**」—— **字面不成立**。同进程对比 `14bdd10` 与当前版本的写盘行：
    **新增 1 个键 `usage_source`**（键数 +1），删除键 0、既有键的值变化 0。
    ⇒ 准确说法是「**既有字段值与写盘路径不变，但新增一个 `usage_source` 键**」。
    这里**不**为了迁就那句话改成「只在传 trace 时才写 `usage_source`」—— 让产物自描述
    （每行都能看出用量数字来自 `model_usage` 还是 `trace` 回填）是对的，
    反向迁就一句不严谨的表述会把设计改坏。

    `log_path` 仅供自检/测试注入落盘位置，缺省仍是 usage.jsonl —— 默认行为不变。
    """
    models = (getattr(result, "model_usage", None) or {}) if result is not None else {}

    detail: dict[str, dict] = {}
    for name, u in models.items():
        detail[name] = {
            "input": _f(u.get("inputTokens")),
            "output": _f(u.get("outputTokens")),
            "cache_read": _f(u.get("cacheReadInputTokens")),
            "cache_creation": _f(u.get("cacheCreationInputTokens")),
            "cost_usd": _f(u.get("costUSD")),
            "provider": u.get("provider"),
            "cost_basis": u.get("costBasis"),
        }

    def total(key: str) -> int | None:
        vals = [d[key] for d in detail.values() if d.get(key) is not None]
        return sum(vals) if vals else None

    # 主模型：按 input+output 最大者。没有 usage 时留 None。
    primary = None
    if detail:
        primary = max(
            detail.items(),
            key=lambda kv: (kv[1].get("input") or 0) + (kv[1].get("output") or 0),
        )[0]

    row = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run": os.environ.get("RUN_LABEL", "default"),
        "script": script,
        "key_source": key_source,
        "model": primary,
        # P0：三个 input 口径分开记。`input` 不动（历史被解释变量的语义），
        # `input_uncached` 是它的**显式命名** —— 同一个值，两个键，读的人不用再猜。
        "input": total("input"),
        "input_uncached": total("input"),
        "output": total("output"),
        "cache_read": total("cache_read"),
        "cache_creation": total("cache_creation"),
        "num_turns": getattr(result, "num_turns", None) if result is not None else None,
        "subtype": getattr(result, "subtype", None) if result is not None else None,
        "is_error": getattr(result, "is_error", None) if result is not None else None,
        "total_cost_usd": getattr(result, "total_cost_usd", None) if result is not None else None,
        # ResultMessage.usage 与 model_usage 是两个口径（前者不含旁路调用），
        # P0 对账要求两者都留在同一行里，免得跨文件拼。
        "result_usage": plain_result_usage(result),
        "models": detail,
        "usage_source": "model_usage" if detail else None,
    }

    # ⚠ D6：result 缺失（异常路径）但 trace 可用 ⇒ 从 trace 回填。
    # 只填**原本是 None** 的字段，绝不覆盖 model_usage 已经给出的值（两者口径不同，
    # 混用会让同一列在不同轮次里含义不一致 —— 那比缺失更坏）。
    if result is None and trace is not None:
        t = usage_from_trace(trace)
        filled = []
        for k in ("input_uncached", "cache_read", "cache_creation", "output"):
            if row.get(k) is None and t.get(k) is not None:
                row[k] = t[k]
                filled.append(k)
        # `input` 与 `input_uncached` 是同一个量的两个名字 ⇒ 一起填，保持同值
        if row.get("input") is None and t.get("input_uncached") is not None:
            row["input"] = t["input_uncached"]
        if row.get("num_turns") is None and t.get("num_turns") is not None:
            row["num_turns"] = t["num_turns"]
        row["usage_source"] = "trace" if filled else "trace(空)"
        row["trace_usage"] = t
        row["trace_backfilled_fields"] = filled
        # 工具调用数也从 trace 取真实值（events 为空时 summarize 会给 0，与事实矛盾）
        if t.get("tool_calls_total") is not None:
            row["tool_calls_total"] = t["tool_calls_total"]
    if extra:
        row.update(extra)

    _line(log_path or USAGE_LOG, row)
    return row
