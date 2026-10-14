# 全链路 trace（第五层）

**这一层解决的不是「留痕」，而是「留了痕但没人看」。**

在它之前，`agent-*.json` 里已经存着完整的事件流了 —— 但「这一轮为什么卡住」
要人工翻几万行 JSON。于是实际上没人翻，「全程可复现」就只停在「留了」这一步。
这一层是那一步到「能说清」之间的桥：

```
agent 运行  →  统一 trace（agent/trace.py）  →  渲染 + 卡点检测（servers/experiments/trace_report.py）
```

---

## 1. 关键发现：不用新采集任何数据

立项时先去读了 SDK 的类型定义
（`D:\Anaconda3\envs\aicoding\Lib\site-packages\claude_agent_sdk\types.py`），
把 `ToolUseBlock` / `ToolResultBlock` / `AssistantMessage` / `UserMessage` /
`ResultMessage` 的字段逐个列出来，跟现有 `events` 里存的东西对了一遍。

结论是：**数据本来就在流过去，只是没被留下。** 以前一个都没存的字段：

| 字段 | 本来在哪 | 少了它就没法回答 |
|---|---|---|
| `ToolResultBlock.tool_use_id` | 每一条工具结果 | 哪条结果对应哪次调用（并行调用时尤其） |
| `ToolResultBlock.is_error` | 同上 | 「工具报错了」与「工具正常返回」分不开 |
| `AssistantMessage.usage` | 每条助手消息 | 逐回合的 token 用量 |
| `AssistantMessage.parent_tool_use_id` | 同上 | 子 agent 归属（未来做 subagent 时要） |
| `ResultMessage.permission_denials` | 运行结束 | **陷阱 15（沙箱洞）当时如果记了它，一眼就能看出来** |
| `ResultMessage.errors` / `api_error_status` / `terminal_reason` | 同上 | 「这一轮为什么停」的结构化答案 |
| `ResultMessage.usage` / `model_usage` | 同上 | 真实输出 token（见 §5 的坑） |
| `ThinkingBlock.thinking` | 思维块 | 「它当时在想什么」 |
| 观察时刻 | 每条记录 | 时间轴（哪一步在等什么） |

所以这一层的成本极低、收益直接：`TraceRecorder` 只是把这些字段**照抄**下来。
它刻意**不存任何派生指标**（计数、均值、总量）—— 那些由读取方现算，
避免同一个量存两份、两份不一致时无从判断谁对（与 `context_report.py` 同一条规矩）。

---

## 2. 数据结构

`agent/trace.py` 里 `TraceRecorder.finish()` 产出的形状：

```json
{
  "trace_version": 1,
  "run":         { "label", "started_at", "fixture", "fixture_root", "fixture_head",
                   "fixture_dirty", "task", "model", "arm", "sandbox",
                   "baseline_exit_code", "max_turns" },
  "steps":       [ { "seq", "t_ms", "type", ... } ],
  "termination": { "outcome", "agent_stop", "baseline_exit_code", "final_exit_code",
                   "changed_files", "test_files_touched", "edits_outside_scope",
                   "agent_error", "result" },
  "caveats":     [ ... ]
}
```

`steps[].type` 有这些：`turn` / `assistant_text` / `thinking` / `tool_call` /
`tool_result` / `user_text` / `unknown_block`，外加 `note(kind, ...)` 写出的任意具名类型
（例如 `agent_exception`）。渲染器对**不认识**的类型有兜底分支照样打印 ——
静默丢正是这一层要消灭的东西。

`termination` 里 **`outcome` 与 `agent_stop` 分两栏，故意不合并**：
前者是独立 pytest 的判定，后者是 CLI 自己的说法。两者不一致本身就是一条重要结论
（见 §4 的 `claimed_but_failed`）。

`run` 里**不记 env**（含凭据）。

---

## 3. 渲染：一份产物 → 一条时间线

```
python servers/experiments/trace_report.py servers/experiments/out/trace-ok-small_pkg.json
python servers/experiments/trace_report.py <产物> --json      # 只要机器可读结论
python servers/experiments/trace_report.py --self-test        # 自检，10 个用例
```

输出的四段：运行头 / 时间线（含思维原文与 agent 自己说的话）/ 逐回合 token / 卡点检测 + 收尾 + 统计。

时间线走的是 `steps` 而不是「调用列表」—— 因为**思维原文与 agent 自己说的话也在里面**，
而它们恰好是「它为什么这么做」最直接的证据。只渲染工具调用的话，
时间线会退化成一张「它做了什么」的清单，那正是这个脚本要解决的问题。

---

## 4. 反向对照：这一层不是装饰

对照组与实验组用**同一个渲染器**、同一份代码，只有输入不同。

| | `trace-ok-small_pkg.json` | `trace-blocked-small_pkg.json` |
|---|---|---|
| 场景 | 正常（可写） | `--no-write`，agent 只有读工具 |
| 工具调用 | 8 次 | 10 次 |
| 卡点 | **0 条** | **8 条** |
| 时间线 | 全部正常 | `#39 / #46 / #53  apply_patch … ❌` 三次被拒 |
| 结构化原因 | 无 | `permission_denied × 3` + `claimed_but_failed` |
| 独立 pytest | `1 → 0` success | `1 → 1` failed |
| CLI 自述 | `subtype=success` | `subtype=success`（**与判据不一致**） |
| 结果字符合计 | 15,579（read_file 64.8%） | 22,874（read_file 69.0%） |

实验组的思维原文直接给出了「卡在哪」：

> *"Hmm, apply_patch was denied. Let me consider. Maybe I should retry? The denial says don't ask mode.
> But maybe it's a transient. Let me retry once."*
> → *"The apply_patch tool is denied at the tool level. I have no other way to write files.
> The instructions say to STOP and explain."*

**这就是面试里「Agent 为什么会卡住」的可复现答案**：不是「它失败了」，
而是「它在第 16 个回合第一次被拒，判断可能是瞬时的，重试了两次，
第 23 个回合才确认这是工具级封锁，然后按指令停下并说明」—— 每一步都有证据。

---

## 5. 检测项，以及**哪些情况下跑不了**

检测项：`repeat_call` / `tool_error` / `orphan_result` / `stalled_tests` / `slow_step` /
`permission_denied` / `claimed_but_failed` / `max_turns` / `agent_exception` /
`never_attempted_edit` / `unknown_block`。

两条设计规矩写在脚本 docstring 里：

1. **统计一律从 `steps` 现算，不读 `metrics`。** 同一个量存两份，不一致时无从判断谁对。
2. **数据缺失导致跑不了的检测项，必须显式报「跑不了」，不能报「没问题」。**
   用一个**没有能力发现问题**的口径去证明没问题，比不报更糟。

于是旧产物（只有 `metrics` + `events` 的 v0）会走有损适配器，并打印：

```
⚠️ 本次产物缺失数据，以下检测项**跑不了**（不是「没问题」）：
     · slow_step（events 没有时间戳，时间轴不存在）
     · tool_error（events 没存 is_error，工具报错与正常返回分不开）
```

12 份 A/B 产物都是 v0，全部可以这样读（`trace_report.py` 对 v0 的配对是
**按发出顺序 FIFO 猜的**，产物里会自己声明这一点）。

---

## 6. 本阶段**不能**证明的事

- **`t_ms` 是观察时刻，不是执行时长。** 相邻两步的间隔里混着模型生成与网络等待，
  所以「这一步花了 41s」不等于「这个工具跑了 41s」。
- **`slow_step` 的阈值 30s 是拍的**，没有测过分布。它只当线索，不当结论。
- **逐回合 `output_tokens` 恒为 0**（键存在、值是 0，实测 17 个回合全是 0）。
  真实输出 token 只在 `ResultMessage.usage` 里 —— 不能拿逐回合的 0 当成「没有输出」。
- **`turn` 不是 SDK 的 `num_turns`。** 实测同一次运行里 `turn` 数到 17，
  而 `ResultMessage.num_turns=9`：一个 API 回合会发多条助手消息。
- **v0 的每次调用字符数只能当参考**（顺序配对会错配，见 §7）。
- **这一层不判断「修改正确不正确」**，那仍然是独立 pytest 的事。

---

## 7. 实现时踩到的坑（都已固化成自检用例）

| 坑 | 现象 | 修法 |
|---|---|---|
| **PowerShell 按 ANSI 读写 UTF-8** | `(Get-Content -Raw) -replace … \| Set-Content` 在 PS 5.1 上有损：GBK 解码器遇到「合法前导字节 + 非法后继」会吃掉两个字节并吐 `?`，**连带吞掉行尾的 `\n`**，多行合并。676 行里 143 行受损、292 个 U+FFFD、`ERROR_KINDS = {` 被吞进上一行注释 → `SyntaxError` | 整份重写。**改代码不要用 PowerShell 的字符串管线** |
| **配不上就 `continue`** | v0 并行调用时结果配不上，被静默丢掉 → 渲染成「无结果」，看上去像工具没返回 | 一律记成**孤儿结果**并在时间线里显式打出 |
| **「连续两次相同」缺少方向** | 真实 A/B 产物上误报「run_tests 连续 2 次都是 0」—— 那是改完**复验通过**，正常行为 | 必须**非 0**，且 detail 说清中间**改没改过代码**（「改了还失败」≠「原地重跑」） |
| **孤儿检测只在 v1 模式跑** | 最需要它的那侧（v0）被守卫挡掉了 | 两种模式都跑，detail 里分清含义 |
| **v0 字段位置** | `changed_files` / `cost_usd` 在 `metrics` **顶层**、不在 `termination` 下 → 报告打出「改动文件：(无)」，而那一轮判的是 success（自相矛盾） | 全部映射 |
| **值内部硬截断** | `apply_patch(... new='        if)` 看上去像 agent 生成了语法错误的补丁 | 先 `repr` 再截断，且**在键与键之间断开** —— 半个值会被当成真的 |

---

## 8. 附带收获：第一次真实运行就抓出一个存量 bug

第一次真实运行（`trace-smoke1`）的输出是：

```
改动文件：['fixtures/small_pkg/inventory/pricing.py']
越界改动：['fixtures/small_pkg/inventory/pricing.py']   ← 就是那个正确的修复
==> 判定：未成功
```

agent 干得完全正确，判定却说失败。根因：`git status --porcelain` 输出的是
**相对仓库根**的路径，而 `--porcelain` 会强制忽略 `status.relativePaths` 配置
（`git status` 也不接受 `--relative`）。`fixtures/small_pkg` 在本仓库之内，
于是路径多出一层前缀，被 scope 判成越界。

`e5fceef` 最初**是**剥前缀的（用 `FIXTURE_REL`）；`e23ff35`「换大靶场」时把
`run_git` 的 cwd 改成 `FIXTURE`，顺手删掉了那次剥离 —— **只在外层靶场验证过，
于是静默坏在仓库内靶场上**，存在了 3 个提交。修法：用
`git rev-parse --show-prefix` 问 git 自己（仓库内靶场得到 `fixtures/small_pkg/`，
仓库外得到空串，两种靶场于是输出同一种形状）。

**教训：改公共基础设施时要两种靶场都跑一遍。** 单侧验证过的改动，
在另一侧可以静默坏掉而不触发任何报错。

---

## 9. 怎么复现

```bash
cd E:/aicoding/mini-claude-code

# 自检（构造输入 + 断言，10 个用例）
python servers/experiments/trace_report.py --self-test

# 正常一轮
python coding_agent.py --fixture small_pkg --label trace-smoke
python servers/experiments/trace_report.py servers/experiments/out/agent-trace-smoke.json

# 反向对照：同样的靶场，只把写工具关掉
python coding_agent.py --fixture small_pkg --label trace-revA --no-write
python servers/experiments/trace_report.py servers/experiments/out/agent-trace-revA.json
```

两份冻结的证据：`servers/experiments/out/trace-ok-small_pkg.json`（0 条卡点）、
`servers/experiments/out/trace-blocked-small_pkg.json`（8 条卡点）。
