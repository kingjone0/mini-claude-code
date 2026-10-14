"""agent/trace.py —— 全链路 trace 的记录器（第五层）。

**它解决的问题**：`agent-*.json` 里的 `events` 是 SDK 消息的**原始流水**。
它能回答「调了哪些工具」，但回答不了「**为什么卡在这里**」。原因不是缺数据，
而是有几个只在 SDK 消息对象上才有的字段，在写事件时**被丢掉了**：

  | 字段 | 在哪 | 丢掉之后失去了什么 |
  |---|---|---|
  | `ToolResultBlock.tool_use_id` | 结果块 | 调用与结果只能**按顺序**配对；一次「调了但没回来」会把后面全部错位 |
  | `ToolResultBlock.is_error` | 结果块 | 分不清「工具报错」与「工具正常返回了一段文本」 |
  | `ResultMessage.permission_denials` | 结果 | **权限被拦**这一整类卡点完全不可见 |
  | `AssistantMessage.usage` | 消息 | 每一轮花多少 token 无从得知（只有全局总数） |
  | 观察时刻 | —— | 没有时间轴，「慢在哪一步」只能猜 |

所以这一层**不新采集任何数据** —— 数据本来就在流过去，只是没被留下。

━━ 刻意做的三个设计选择 ━━

1. **只存观察到的事实，不存派生指标。** 计数、均值、总量一律由读取方
   （`servers/experiments/trace_report.py`）从 `steps` 现算。理由与
   `context_report.py` 里那条规矩一致：同一件事存两份，两份不一致时无从
   判断谁对，「口径没改」也就无从谈起。

2. **截断字段一律叫 `preview`，真实长度并存 `chars`。**
   名字叫 `text` 会诱导人把它当成全部内容 —— 上下文成本第一次就是这么算错的。

3. **不认识的消息块不丢，记成 `unknown_block`。** 现在只认识
   TextBlock / ToolUseBlock / ToolResultBlock；SDK 还有 ThinkingBlock /
   ServerToolUseBlock 等。静默丢弃的代价是：将来行为变了，而 trace 里什么都没有。
"""

from __future__ import annotations

import time
from typing import Any, Callable

TRACE_VERSION = 1


def plain(obj: Any) -> Any:
    """把 SDK 给的对象变成能 `json.dumps` 的东西。

    **不做类型推断**：认得出的（标量 / dict / list）照原样递归，
    认不出的 `str()` 之后再截断 —— 并且调用方必须知道它可能被截过。
    先前的教训是「以为 content 是 str」，所以这里宁可多一层转换，
    也不假设任何对象的形状。
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    return str(obj)[:MAX_PREVIEW]

# 存进 trace 的文本一律截断到这个长度。它是**预览**，不是全文 ——
# 全文长度在同一条记录的 `chars` 里。工具返回动辄几万字符，
# 全存进 trace 会让产物大到没人看，反而失去「能读懂」这个唯一目的。
MAX_PREVIEW = 1200

# `caveats` 会随产物一起落盘。写在产物里而不是只写在注释里，是因为
# 读产物的人（包括几周后的我）不会去翻这个模块的源码。
CAVEATS = [
    "t_ms 是**观察时刻**（收到该 block 的时刻，相对本次运行开始），"
    "不是工具在 server 端的真实执行时长；相邻两条之间的间隔还包含模型生成与网络等待。",
    "所有文本字段都是**截断预览**（preview），真实长度看同一条记录的 chars。",
    "turn 以 AssistantMessage 为单位递增，**与 SDK 的 num_turns 不是一回事**："
    "实测同一次运行里 turn 数到 17，而 ResultMessage.num_turns=9"
    "（一个 API 回合会发多条助手消息，文本与工具调用各算一条）。",
    "P0 起 turn 的去重口径：连续两条消息带**完全相同的 usage** 视为同一次 API 调用的"
    "流式分片，只记第一份（实测旧版 Σ逐回合input = 2.003×~3.39× model_usage 合计，"
    "去重后 Σ 精确等于 ResultMessage.usage.input_tokens）。usage 为 None 的消息"
    "没有可比内容，仍每条开一回合 —— 宁多勿漏。",
    "逐回合 usage 里的 `output_tokens` **键存在、值恒为 0**（实测 17 个回合全为 0）；"
    "唯一真实的输出 token 在 termination.result.usage 里 ——"
    " 不能拿逐回合的 0 当成「没有输出」。",
    "查不到 tool_use_id 的结果标 orphan=true；这里只存观察到的事实，"
    "不存派生指标（计数/均值/总量由读取方现算）—— 避免出现两份口径。",
]


class TraceRecorder:
    """把 SDK 消息流记成一份可读、可复现的 trace。

    `now` 可注入是为了可测：自检里用它喂出一个确定的时间轴，
    否则「间隔检测」这类逻辑只能靠真实等待来触发。
    """

    def __init__(self, run: dict[str, Any], *, now: Callable[[], float] = time.monotonic):
        self._now = now
        self._t0 = now()
        self._run = dict(run)
        self._steps: list[dict[str, Any]] = []
        self._turn = 0
        self._last_usage_key: Any = object()   # sentinel：保证首条一定开回合
        self._turn_by_call: dict[str, int] = {}
        self._finished = False

    # ── 内部 ──────────────────────────────────────────────────────────────
    def _t_ms(self) -> int:
        return int(round((self._now() - self._t0) * 1000))

    def _step(self, **fields: Any) -> dict[str, Any]:
        if self._finished:
            # 真会发生的 bug：先把 termination 写进去、返回的循环又追加了一条。
            # 那一条会静默消失（读取方只看到 finished 的快照）——
            # 与其让它静默消失，不如当场炸。
            raise RuntimeError("trace 已 finish，不能再追加步骤")
        step: dict[str, Any] = {"seq": len(self._steps) + 1, "t_ms": self._t_ms()}
        step.update(fields)
        self._steps.append(step)
        return step

    # ── 记录 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _usage_key(usage: Any) -> Any:
        """usage 的可比较指纹。认不得的形状整体 str() —— 与 plain() 的兜底一致，
        不猜内容；str 截断后仍相同只会多合并一次，不会漏开回合的风险可忽略。"""
        if usage is None or isinstance(usage, (bool, int, float, str)):
            return usage
        if isinstance(usage, dict):
            return tuple(sorted((str(k), TraceRecorder._usage_key(v)) for k, v in usage.items()))
        return str(usage)[:MAX_PREVIEW]

    def begin_turn(self, usage: Any = None) -> int | None:
        """一条**新的 API 调用** = 一回合。返回新的回合号（1-based），
        若是流式分片（同一份 usage 的复制）则不开新回合、返回 None。

        逐回合的 `usage` 记成一条独立的 `turn` 步骤 —— 旧产物里只有
        `usage.jsonl` 的**全局总数**，「第几轮开始变贵/变慢」这个问题无处可查。

        ⚠️ P0 修正（实测证据见 servers/experiments/out/p0_reconcile.py）：
        SDK 在**每条流式消息**上复制同一份 usage，一条 AssistantMessage 可能发
        多条消息。旧版按消息计数，同一份 usage 被记 2~5 次 —— C-1 原始
        Σinput=132,690 恰为 model_usage 合计的 2.003×，A-1 达 3.39×。
        现在连续同值的 usage 视为同一次 API 调用的分片，只记第一份。
        去重后的 Σ 精确等于 ResultMessage.usage.input_tokens（两个样本都验证过）。

        ⚠️ 就算 `usage` 是 None 也**照样开**回合。时间线要靠它分回合：
        缺了就整条不发，会让「这是第几回合」在 trace 里断档，而回合边界
        正是「哪一步属于哪一轮」的坐标 —— 为一个可选字段牺牲坐标不划算。
        （None 没有内容可比，无法识别分片；连续多条无 usage 的消息各开一回合，
        宁多记不漏记 —— 去重只对**带 usage** 的消息生效。）
        """
        key = self._usage_key(usage)
        if usage is not None and key == self._last_usage_key:
            # 流式分片：同一份 usage 的复制，不开新回合，也不重复记账。
            return None
        self._last_usage_key = key
        self._turn += 1
        self._step(type="turn", turn=self._turn,
                   usage=plain(usage) if usage is not None else None)
        return self._turn

    def text(self, text: str) -> None:
        self._step(type="assistant_text", turn=self._turn,
                   chars=len(text), preview=text[:MAX_PREVIEW])

    def thinking(self, text: str) -> None:
        self._step(type="thinking", turn=self._turn,
                   chars=len(text), preview=text[:MAX_PREVIEW])

    def tool_use(self, block: Any) -> None:
        # input 完整存下来：它是「这一步打算干什么」的唯一记录，
        # 也是判断「是不是在重复同一个动作」的全部依据。
        self._turn_by_call[block.id] = self._turn
        self._step(type="tool_call", turn=self._turn, tool=block.name,
                   tool_use_id=block.id, input=block.input)

    def tool_result(self, block: Any, *, text: str) -> None:
        """`text` 由调用方传入归一化后的结果文本。

        归一化（`ToolResultBlock.content` 是 dict 而不是 str，见项目 CLAUDE.md
        陷阱 17）留在 `coding_agent.py` 里，不挪到这里 —— 挪过来会变成两份实现，
        而那一份是踩过坑才写对的。
        """
        turn = self._turn_by_call.get(block.tool_use_id)
        fields: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "turn": turn,
            # ⚠️ 这个字段以前根本没记。「工具报错」与「工具正常返回一段文本」
            #    在旧产物里长得一模一样，只能靠人去读预览猜。
            "is_error": bool(block.is_error),
            "chars": len(text),
            "preview": text[:MAX_PREVIEW],
            # content 的实际形状（dict / list / str）值得留着：陷阱 17 就是
            # 「以为它是 str」造成的，形状一旦变化，这里第一个看得出来。
            "content_type": type(block.content).__name__,
        }
        if turn is None:
            fields["orphan"] = True   # 没找到对应的 tool_call，不猜一个回合号给它
        self._step(**fields)

    def user_text(self, text: str) -> None:
        self._step(type="user_text", turn=self._turn,
                   chars=len(text), preview=text[:MAX_PREVIEW])

    def unknown_block(self, where: str, block: Any) -> None:
        """不认识的块记下来，不丢。

        `where` 取 "assistant" / "user"。只记类型名与它带的字段名 ——
        不认识的东西不猜它的内容。
        """
        names = sorted(vars(block)) if hasattr(block, "__dict__") else []
        self._step(type="unknown_block", turn=self._turn,
                   where=where, block_type=type(block).__name__, fields=names)

    def note(self, kind: str, **fields: Any) -> None:
        """通用兜底：记录一次与具体块无关的事件（例如 agent 抛异常）。"""
        self._step(type=kind, turn=self._turn, **fields)

    # ── 收尾 ──────────────────────────────────────────────────────────────
    def finish(self, termination: dict[str, Any]) -> dict[str, Any]:
        self._finished = True
        return {
            "trace_version": TRACE_VERSION,
            "run": self._run,
            "steps": self._steps,
            # termination 里会有 SDK 对象（`permission_denials` 等），在这里统一转干净，
            # 免得落盘时才在 json.dumps 上炸 —— 那时候整轮证据已经跑完了。
            "termination": plain(termination),
            "caveats": CAVEATS,
        }
