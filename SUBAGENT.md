# ④ subagent 分工 —— 探针阶段记录

> 本文件记录**在动主循环之前**对 SDK subagent 通道做的通道验证（探针），以及由此得到的
> 设计约束。探针脚本：`servers/experiments/subagent_probe.py`；产物读取器：
> `servers/experiments/subagent_probe_read.py`；原始产物：`servers/experiments/out/subagent-probe-*.json`。

## 0. 为什么先探、不直接实现

`coding_agent.py` 的整个沙箱模型建立在 **`tools=[]`（禁用全部内置工具）** 上。而 subagent
恰恰**是一个内置工具**（`types.py:2212`：`agents` 是「invokable via the Agent tool」）。

也就是说，做 ④ 层 = **在这份代码里第一次主动放开一个内置工具**。在动主循环之前，先把
「这条通道到底通不通、放开之后我的四道防线还成不成立」单独验证一遍。这一步花掉的时间，
比写完实现再回头怀疑「到底是我的实现错了还是通道不通」便宜得多 —— 后者的成本在前几轮
已经付过（`--tools` 的坑、`allowed_tools` 的坑，两次都是复验才发现的）。

## 1. 实测结果

四组配置，同一靶场（`E:\aicoding\targets\more-itertools-multi`）、同一题面
（**禁止父 agent 自己读目标文件，必须派子代理**）：

| 配置 | `--tools` | 父层调用的工具 | spawn 调用 | 带 `parent_tool_use_id` 的消息 | 子代理的 MCP 调用 | 转发的子代理文本 | 总消息数 |
|---|---|---|---|---|---|---|---|
| 1 | `Agent` | **（无）** | 0 | 0 | 0 | 0 | 341 |
| 2 | `Task` | `Agent` | **1** | 9 | **4**（`read_file`×3, `list_files`×1） | 0 | 180 |
| 3 | `Task` + `forward_subagent_text=True` | `Agent` | **1** | 12 | 3（`read_file`×2, `search_code`×1） | **1** | **561** |
| 4（阴性对照） | `NoSuchToolZZZ` | **（无）** | 0 | 0 | 0 | 0 | 232 |

判定：**通道可用**。配置 2/3 里子代理真的读了文件、数对了答案（67 个顶层 `def`，
带 67 项名字清单），父 agent 拿到了回报。

## 2. 六个非显然的发现

### 2.1 spawn 工具在 `--tools` 里叫 `Task`，但模型看到的名字是 `Agent`

`types.py` 里的两种说法（"the Agent tool" L2162/L2213 vs "Task-spawned" L297、
"Task statuses" L1162）**不是笔误，是两个不同的名字空间**：

- **CLI `--tools` 名单**（`claude --help`：*"Specify the list of available tools from the
  built-in set"*）里，这个名字是 **`Task`**；
- **模型看到的工具名**（`tool_use.name`、`allowed_tools` 里的名字）是 **`Agent`**。

配置 2 的实测链条：`--tools Task` → 父 agent 发出 `tool_use.name == "Agent"` →
紧接着出现 `TaskStartedMessage`。两者是同一个东西。

### 2.2 `--tools` 里写一个不认识的名字，**不报错，而是静默变成「零个内置工具」**

这是本次最危险的一个发现。配置 1（`--tools Agent`）和配置 4（`--tools NoSuchToolZZZ`）
**行为完全同形**：agent 只看得见 6 个 MCP 工具，没有任何内置工具，也没有任何错误。

证据是模型自己的工具清单（配置 4 原文）：

> 我可调用的工具只有 code-tools 这一组 MCP 工具：`apply_patch`、`list_files`、`read_file`、
> `repo_map`、`run_tests`、`search_code` … 没有提供 `Agent` / `Task`(或任何等价的
> spawn-subagent)工具

⇒ 一开始把 `--tools` 写成 `"Agent"`，**不会失败，只会得到一个「安静地少了能力」的
agent**。这正是本项目前几轮反复踩的同一类坑（预注册没提交、`Get-FileHash` 比不存在的
路径）：**错误的表现形式是「什么都没发生」，而不是「报错」**。所以探针必须带**阴性对照**
（配置 4），否则「0 次调用」这个读数无法区分「名字写错了」和「通道真的不通」。

### 2.3 `--tools` 只白名单**内置**工具，MCP 工具不受它管

配置 1/4 里 `--tools` 已经把内置工具清零了，但 agent 仍然看得见**全部 6 个**
`mcp__code-tools__*` 工具，**包括我根本没放进 `allowed_tools` 的 `apply_patch` / `run_tests`**。

⇒ 再确认一次前几轮的结论：**`allowed_tools` 是「免询问自动放行」，不是「可用范围」**；
`--tools` 才是可用范围，但它**只作用于内置工具**。两把锁管的是两个不同的集合：

| 锁 | 管什么 | 写错的后果 |
|---|---|---|
| `tools` / `--tools` | **内置**工具的存在与否 | 静默变成零内置工具（2.2） |
| `allowed_tools` | 所有工具的**自动放行** | 少放行 → 调用被权限拦下（会报错，相对安全） |

沙箱仍然成立：`--tools Task` 只放开 spawn 这一个内置工具，`Bash/Read/Write/Edit/Glob/Grep`
全部保持关闭。

### 2.4 子代理拿得到 MCP 工具，靠的是 `AgentDefinition.mcpServers`

```python
AgentDefinition(
    description="统计代码文件里的顶层函数定义个数（只读）",
    prompt="……",
    tools=["mcp__code-tools__list_files", "mcp__code-tools__search_code", "mcp__code-tools__read_file"],
    mcpServers=["code-tools"],   # 字符串 = 引用父层同名 server
    maxTurns=6,
)
```

`mcpServers` 传**字符串**是「引用父层已声明的 server」（`types.py` 注释：*"Each entry is a
server name (str) or an inline {name: config} dict"*）。实测子代理的 `read_file` 参数正确、
返回值正常（`{"result":"# more_itertools/recipes.py（共 1627 行……）"}`）。

### 2.5 父 agent 默认只拿到子代理的**最终总结**，且那条总结是普通的 `tool_result`

`forward_subagent_text=False` 时，子代理的全部中间步骤（它调了什么工具、看到了什么）
**一条都不出现在父流里**。父 agent 收到的只有末尾一条 `parent_tool_use_id` 为**空**的
`tool_result`。

这条设计有一个可测量的行为后果 —— 两次跑的父 agent 自我陈述完全不同：

- **不转发**（配置 2）：*「需要说明的是，这个数字是子代理自报的，**我没有亲自读文件核对**」*
- **转发**（配置 3）：*「**我核对了它给出的名字清单**，正好 67 项，数字与清单一致」*

配置 3 的父 agent 说得出这句话，是因为它**确实看到了**子代理的中间输出。⇒
`forward_subagent_text` 不只是「日志多少」的开关，它**改变父 agent 能核实什么、以及它
对自己核实能力的判断**。

代价：转发让**总消息数从 180 涨到 561（约 3.1×）**，产物从 114,697 B 涨到 285,473 B
（约 2.5×）—— 它把子代理的整个内部推理都灌进父流。

### 2.6 ⚠️ 用 `ResultMessage.usage` 量成本，会**静默低估**带子代理的一轮

| 配置 | `num_turns` | `duration_ms` | `input_tokens` | `cache_read` | `output_tokens` | `total_cost_usd` |
|---|---|---|---|---|---|---|
| 1 `--tools Agent`（无 spawn 工具） | 1 | 3,389 | 1,928 | 0 | 644 | **0.0323** |
| 2 `--tools Task`（通道打通） | 2 | 16,186 | 4,482 | 3,456 | 748 | **0.2516** |
| 3 `--tools Task --forward-text` | 2 | 16,853 | 3,723 | 3,328 | 856 | **0.2056** |
| 4 阴性对照 | 1 | 2,625 | 1,928 | 0 | 503 | **0.0287** |

配置 2 的 `ResultMessage.usage` 只比对照高 132%（1,928 → 4,482），但
**`total_cost_usd` 高了 8.8×**（0.0287 → 0.2516）。差额来自子代理 —— 它的用量**不在**
`ResultMessage.usage` 里，而是单独出现在 `TaskProgressMessage.usage` 中：

```json
{"usage": {"total_tokens": 1898, "tool_uses": 1, "duration_ms": 1475},
 "last_tool_name": "mcp__code-tools__read_file"}
```

⇒ **要得到一轮的真实成本，必须把两处相加**。本项目的核心指标之一就是 `input_tokens`，
如果不改量具就加 ④ 层，**成本指标会静默变成错的**（而且错的方向是「看起来更便宜」，
正好是最不容易被怀疑的方向）。

## 3. Task 生命周期消息（SDK 类名与载荷）

这四个类在 `types.py` 里没有文档化的字段，实测载荷如下：

| 类 | 关键字段 | 何时出现 |
|---|---|---|
| `TaskStartedMessage` | `task_id`、`tool_use_id`、`description`、`subagent_type`、`is_backgrounded: false`、`spawn_depth: 1`、`task_type: "local_agent"`、`prompt`、`session_id` | spawn 之后立刻 |
| `TaskProgressMessage` | `usage: {total_tokens, tool_uses, duration_ms}`、`last_tool_name` | **子代理每次工具调用后一条** |
| `TaskUpdatedMessage` | `{"patch": {"status": "completed", "end_time": …}}` | 子代理结束时 |
| `TaskNotificationMessage` | `status`、`output_file`、`summary` | 紧接着 `TaskUpdated` |

消息顺序（配置 2 实测）：

```
父层 AssistantMessage  tool_use name="Agent"      [159]
TaskStartedMessage                                [160]
子代理 AssistantMessage tool_use (parent=call_00_…) [164]
子代理 UserMessage      tool_result (parent=…)      [165]
… 重复 …
TaskUpdatedMessage                                [175]
TaskNotificationMessage                           [176]
父层 UserMessage        tool_result (parent=-)      [177]
父层 AssistantMessage   text（总结给用户）           [179]
```

两个有用的细节：

- `session_id` 是**子代理自己的**（`ba718238-…`），与父会话不同 ⇒ 可以用来把日志切成
  「父流 / 子流」两段。
- `TaskNotificationMessage.output_file` 指向子代理完整输出的落盘路径
  （`%LOCALAPPDATA%\Temp\claude\<slug>\<session>\tasks\<task_id>.output`），
  而 `summary` 比父层收到的 `tool_result` **更完整**（配置 2：4,000+ 字符 vs 1,702）。
  ⇒ 想看子代理的原话，读 `output_file`；只看父层 `tool_result` 会看到被截断的版本。

## 4. 由此得到的实现约束

1. 放开 spawn 工具用 **`tools=["Task"]`**（不是 `"Agent"`），并且**必须带阴性对照**
   （写成不存在的名字 → 读数退化为「零内置工具」）。
2. `DISALLOWED_TOOLS` 里原本有 `"Task"`（见 `coding_agent.py:155-158`），启用 ④ 层时
   要把它**从禁用名单里减掉**，否则自己把自己否掉 —— 这个减法要用代码做，不能靠人记住。
3. 权限模型保持四道防线（`tools` / `allowed_tools` / `disallowed_tools` /
   `permission_mode="dontAsk"` + `strict_mcp_config=True`），新增的只是 `tools` 里的一个名字。
4. 子代理的 `AgentDefinition.tools` 要**显式列举**只读工具，不给 `apply_patch`。
5. **量具必须先改**：事件流要记 `parent_tool_use_id`（区分父流/子流）和
   `TaskProgressMessage.usage`（子代理用量），否则成本指标静默失真（2.6）。
6. `forward_subagent_text` 默认 **False**（3.1× 消息量）；只在需要「父 agent 能核实子代理
   的工作」时打开 —— 而这件事本身就是一个值得测量的变量，不是纯粹的日志开关。

## 5. 本阶段**不能**证明的事

- **没测**：子代理在 `AgentDefinition.tools` 里没被列出的 MCP 工具（如 `apply_patch`）
  到底能不能调用。探针里子代理只用了只读工具，没有做「故意越界」的阴性对照。
  ⇒ 在补上之前，不能声称 `AgentDefinition.tools` 是一道**有效**的边界。
- **没测**：多子代理并行（`run_in_background: true`）、`spawn_depth > 1`（子代理再派子代理）、
  `maxTurns` 触顶时的行为。探针只跑了单层、前台、远未触顶的情况。
- **没测**：`--tools Task` 对 `SESSION`/权限提示等其它内置机制的副作用。
- **样本量**：每个配置**只跑了一次**。本文件里的「父 agent 自我陈述不同」（2.5）是一个
  **观察**，不是结论 —— 该按前几轮的做法，用多次重复去看它是否稳定。
- 探针题面是人为构造的（「必须派子代理」）。它证明的是**通道可用**，**不能**证明
  子代理分工在真实修 bug 任务上有收益 —— 那需要另外的 A/B（demand-first）。

## 6. 后续：A/B 已跑完（结果与一处需求论证被证伪）

实现落在 `coding_agent.py`（`--subagent` / `--subagent-force`），预注册与结果是
[`AB-SUBAGENT.md`](AB-SUBAGENT.md)（§7）。三句话摘要：

- 臂 B（邀请）**0/4 采用**；臂 C（强制）4/4 采用 ⇒ 通道没问题，是**模型不选它**。
- 臂 C 里父层 `input_tokens` **×1.62**、`cost_usd` **×1.68**，成功率不变 ——
  机制是父 agent 按题面要求把子代理报的 4 个区间**全部自己复核了一遍**。
- ⚠️ **本文件第 1 节那句「靶场把定位压力造出来了」被证伪了一半**：文件大到读不完是真的，
  但 pytest 的失败摘要**自带失败测试类名**，函数名基本免费送出 ⇒ 这个靶场
  **没有造出「定位」这个需求**。详见 `AB-SUBAGENT.md` §7.2。

## 7. 第二轮（硬靶场）：两条关于**子代理本身**的新事实

`AB-SUBAGENT-HARD.md` 把"失败信息点名缺陷函数"这条捷径堵掉之后重跑了一遍。
采用率**依然 0/4**（需求论证补上了，采用率没动）。但那一轮在**子代理机制**上
暴露出两条与题目无关的、可复用的东西：

### 7.1 `AgentDefinition.maxTurns` 会把子代理**截断在半路**，而且只在通知里留痕

`SCOUT_MAX_TURNS = 8` 时，第一轮 4 次强制运行里**有 2 次**被截断
（子代理工具调用 26 / 20 次），硬靶场第一次 C-1 也被截断（16 次）。
截断的证据是 `TaskNotificationMessage` 的 `summary` 第一句：

> *"NOTE: this agent stopped at its 8-turn limit before finishing.
> The text below is PARTIAL output; treat it as incomplete."*

**这条不会让任何东西报错。** 父 agent 拿到一份半截报告照样往下干，
指标一切正常 —— 而结论会同时**低估子代理的成本**与**父层的收益**
（两边都朝"看起来更便宜"偏，同 §2.6 的方向）。

⇒ 实现约束：**任何一个"照着通知里的 `summary` 当子代理结论用"的下游，
都必须先检查这句话在不在。** 本仓库的检查落在
`servers/experiments/subagent_review.py`（`--brief` 的"状态"列）。
本轮把上限提到 24，4 次里仍有 1 次被截断 —— 而那次的原因是下面这条。

### 7.2 ⚠️ 子代理和父 agent 共用工作区 ⇒ "只读侦察"的前提**不成立**

子代理读的是**父 agent 正在改的同一棵工作树**。C-2 的部分报告原文：

> *"The code changed mid-session (lines 2737 and 5204 were defective in my first read,
> now corrected). Let me look for any backup/patch artifacts that reveal the original
> injected forms, and check for other copies."*

父 agent 在子代理侦察期间打补丁 → 子代理再读同一处发现"缺陷不见了" →
它合理地推断"有人替换了文件，应该去找备份"，于是去翻**根本不存在**的东西，
把回合预算烧光（45 次子调用，交回来的报告只剩 414 字符）。

**只读侦察兵的全部价值来自"它读到的东西在这一轮里不变"。**
共享工作区 + 并发父 agent 直接破坏这个前提 —— 而且破坏方式不是报错，
是让子代理**去追一个由我造成的幻影**。

⇒ 实现约束：要让"只读侦察"成立，必须给它一个**快照**
（`git worktree add` / `git archive` 到临时目录），不能让它看活动工作区。
本轮没有做，所以本轮的 C 臂数据是在这条缺陷存在的情况下取得的。

