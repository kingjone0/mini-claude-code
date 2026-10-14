# ④ 层（subagent 分工）v2 预注册：驱动派发 A/B

> **这份文件在跑第一次实测之前定稿并提交**（`git log --oneline -- AB-SUBAGENT-V2.md` 的时间戳就是证据）。
> §1–§6 一旦提交即不可事后改动；跑完只允许追加 §7「结果」。
> 前序：第一轮 `AB-SUBAGENT.md`、第二轮硬靶场 `AB-SUBAGENT-HARD.md`（本文所有"已实测事实"均引自它们的 §7，**不在本文重新论证**）。
> 本文锚定的代码版本 = commit `d1d87de`。文中引用的行号是**该 commit 的快照**，实现之后行号会漂移 —— 引用时以 commit 为准，不以行号为准。

本文件只回答一个问题：**当"派不派子代理"这个决策从模型搬到 driver（P1），并且把子代理报告的体积压到常数级（P2）、截断变成显式状态（P3）、子代理读到的是不变快照（P4）、每条结论经过独立程序校验（P5）之后 —— 分工的成本和质量是什么样，以及这套量具本身可不可信。**

---

## §0 为什么必须做 v2，以及为什么必须先做 P0（两项前置发现，照抄已实测的事实）

### 0.1 前置发现一：题面自相竞争（这是邀请派发 12 连败的成因）

`coding_agent.py:270-290` 的 `TASK_BASE` 里已经写死了一条 DIY 流程：

> *"建议流程：先跑一次 run_tests 看失败在哪 → 读失败信息与相关代码，定位**根因**（失败点不一定是根因所在）→ apply_patch 修改 → 再跑 run_tests 确认。"*

而 subagent 邀请是 `coding_agent.py:295-304` 的 `TASK_SUBAGENT_NOTE`，**追加在后面**、标着"（可选，不是必须）"、并且自带一句

> *"**不要为了用而用**：如果失败点集中在一处、或者你已经知道根因，直接改更快。"*

也就是说：**协议（写死的流程）和邀请（可选段落）在竞争同一个决策，而邀请方还自己递了台阶。** 两轮共 12 次运行（软靶场 4 次 + 硬靶场 8 次）邀请派发 **0/4 + 0/4**，一次都没被采用。参考实现 `@nanmicoder/dsh-agent-teams` 的审计结论同向：**它也不赌模型自愿** —— 协议钉进常驻 system prompt，外加 `/agent-teams` 的确定性 pre-step 注入。

⇒ 结论（不在本文重证）：**继续加措辞是无效的**。v2 用 P1 把派发决策从模型搬到 driver，按构造消除这个竞争。

### 0.2 前置发现二：同一次运行有三个不同的 "input tokens"，且 cache_read 完全没进指标

以第二轮 C-1 这一次运行为例，三个数同时存在（都是实测）：

| 口径 | 值 | 来源 |
|---|---|---|
| `ResultMessage.usage.input_tokens` | **35,468** | SDK 的结果消息 |
| `model_usage["deepseek-flash[1m]"].inputTokens` | **65,027** | **这才是 `metrics.input_tokens` 的来源** —— `servers/experiments/usage_log.py:47`（`"input": _f(u.get("inputTokens"))`），汇总见同文件 `:74` 的 `total("input")`，改名发生在 `coding_agent.py:1087` 的 `("input", "input_tokens")` |
| trace 逐回合 Σinput | **132,690** | `servers/experiments/trace_report.py:440` 的 `usage_per_turn` 求和 ≈ **2×** modelUsage，疑似重复计数 |

而同一份 `model_usage` 里的 `cacheReadInputTokens = 1,185,664` **完全没有进入任何指标**（`usage_log.py:76` 虽然把它存成 `row["cache_read"]`，但下游 runner/review 只取 `input_tokens` / `output_tokens` / `cost_usd` / `elapsed_s` —— 见 `servers/experiments/subagent_ab_runner.py:45` 与 `subagent_review.py:26` 的字段清单）。

后果不是"结论错了"，而是"**结论没有说自己依赖哪个数**"：A-1→C-1 的三个口径同向（未缓存 ×1.68、cache_read ×1.98、合计 ×1.99），所以方向对口径稳健；但文档只写了一个 `input_tokens`，读者无法判断它是哪一个、也无法复算。第二轮 §7.3 已经因此产生过一次误读（两臂只差 780 tokens 却"区间不重叠"）。

⇒ **这就是必须先做 P0 的理由**：H2 的主指标是父层 `input_uncached` 中位数。在这个数还没被证明唯一、可解释（尤其那 2× 从何而来）之前，任何 D vs A 的比较都可能是在比噪声。P0 排在所有改动之前，且它的判据本身就是 H2 的前置条件（见 §5 C0）。

### 0.3 其余四项改动的问题来源（同样照抄，不重证）

| 问题（来源） | 对应改动 |
|---|---|
| C′ 子代理交回 **67,791–80,663 字符**报告，整份进父层上下文且每轮重发 ⇒ 父层 input ×1.68、账单 ×1.92（`AB-SUBAGENT-HARD.md` §7.4） | **P2** 有界报告 |
| `SCOUT_MAX_TURNS` 8→24 之后仍有 1/4 被截断，交回**半份报告**却被当成完整结论（`AB-SUBAGENT-HARD.md` §7.0）；参考实现 `maxTurns` 截断**完全没处理**（空闲成员带未完成 attempt 被静默 park，只有冷重启才恢复） | **P3** 截断即状态 |
| C-2：父 agent 中途打补丁 ⇒ 子代理去翻**不存在**的备份产物、烧光 24 回合（`AB-SUBAGENT-HARD.md` §7.6 第 1 条）；参考实现 README_ZH.md:51 明确不做共享工作区隔离（"完成时审计，不是 host 写入拦截"） | **P4** 快照隔离 |
| 第二轮只能人工抽查子代理对错（`AB-SUBAGENT-HARD.md` §7.5，4/4 找对 —— 那是人眼看的）；参考实现做了"结论必须有 passing review"，但 `reviewer ≠ implementer` **在代码层不强制**，只有提示词 | **P5** 结论独立校验 |

---

## §1 臂定义

| 臂 | 命令行 | 谁决定派发 | 含义 |
|---|---|---|---|
| **A**（现状对照） | `python coding_agent.py --fixture more-itertools-hard` | 模型（实际上不派） | 与第二轮 A′ 逐字相同：`TASK_BASE`，无 `--subagent`，`Task` 通道关闭 |
| **D**（驱动派发） | `python coding_agent.py --fixture more-itertools-hard --delegate=policy` | **driver** | 新增开关：基线 pytest → 失败测试类聚类成 K 组 → 每组派一个只读 scout（P1）→ findings 落盘、父层只收 ≤2000 字符索引（P2）→ 每次派发记 durable 状态并可自动续派（P3）→ scout 在 detached worktree 快照上工作（P4）→ 每条 finding 由独立进程验证后才进索引（P5） |

**A → D 之间唯一的变量是 `--delegate=policy` 这一个开关**；题面文本、模型、靶场、工具集、复位流程全部一致。D 臂**不再使用** `TASK_SUBAGENT_NOTE` 的邀请段（治疗已经不依赖模型自愿，留着只会重新引入 §0.1 的竞争）。

标签沿用第二轮的命名规则：本轮记录写作 `A-1..A-4` / `D-1..D-4`，与第一轮的 `A-*`/`B-*`/`C-*` 和第二轮的 `A′-*`/`B′-*`/`C′-*` 区分开。

**旧臂的数据不进本轮任何表格。** 第一轮（more-itertools-multi）与第二轮（hard）的靶场、题面、模型、量具都不同；尤其第二轮的 `input_tokens` 是三个口径里未标注的那一个（§0.2），与本轮的 `input_uncached` **不同源、不可比**。跨轮只允许比较**方向**，不允许比较绝对值。

### 1.1 D 臂的六项改动（固定形态，实现不得扩大范围）

- **P0 量具口径**：`metrics` 与 trace 分开记三个数 —— `input_uncached` / `cache_read` / `cache_creation`；并查清 trace 逐回合 Σ ≈ 2× `model_usage` 的成因（是同一 turn 被配对计入两次，还是 SDK 把 `input_tokens` 与 `cache_read_input_tokens` 分别又计了一遍）。**产出物**：一条写明"哪个数是主指标、另外两个数是什么、2× 从哪来"的文字，进 §7 或 `TRACE.md`。P0 不改任何决策逻辑，只改记账。
- **P1 驱动派发**：新增 CLI `--delegate=policy`。driver 先跑基线 pytest（复用现有 `run_tests`/靶场守卫），把失败测试**按测试类聚成 K 组**（分组键 = `pytest -k` 可直接选中的类名），每组派一个只读 scout。父 agent 开工时手里已经有 K 条（经 P5 过滤的）结论，而不是从零开始翻。K 由靶场实际失败类数决定，**本轮预期 K = 3**（`DiscreteFourierTransformTests` / `SlidingWindowTests` / `IsliceExtendedTests`，见 §2）。
- **P2 有界报告**：scout 的完整 findings 写 `servers/experiments/out/scout-<label>-<k>.json`，schema 固定为 `{id, file, line, symptom, rootCause, minimalFix, evidence}`（数组）。**只有 ≤2000 字符的索引进父层上下文**；细节由新增的**只读**工具 `read_findings(id)` 按需取。索引的字符上限是硬编码常量，超限即截断并显式标注，不允许"稍微超一点"。
- **P3 截断即状态**：driver 为**每一次派发**记 durable 状态 `dispatched` / `completed` / `truncated`（落盘，不是内存变量）。回合上限检测**复用** `servers/experiments/subagent_review.py:33` 已有的 `TRUNC_MARK = "turn limit"` 标记（该标记来自 `TaskNotificationMessage.summary` 首句 *"NOTE: this agent stopped at its N-turn limit before finishing. The text below is PARTIAL"*）。检测到 `truncated` ⇒ **自动续派一次**；仍失败 ⇒ 该组标 `undiagnosed`，并在索引里显式写出 `undiagnosed`（父层看得见"这一组没人诊断过"）。
- **P4 快照隔离**：每次运行开跑前 `git worktree add --detach <tmp> HEAD`；scout 侧 MCP server 的环境变量 `AGENT_REPO_ROOT` 指向该快照；**父 agent 留在原工作区**继续读写与打补丁。这根治 C-2 那条"父代理中途打补丁 → 子代理去翻不存在的备份产物、烧光 24 回合"。
- **P5 结论独立校验**：每条 finding 必须带 `predictedFailureCount`。harness 在**独立进程**（不是 scout 自己）里，于**副本**上应用该 `minimalFix`、跑对应的 `pytest -k <类名>`、比对实际失败数与预测 ⇒ `verified` / `refuted` **由程序判定**。**只有 `verified` 的 finding 进父层索引。**

---

## §2 靶场与题面

- 靶场：`more-itertools-hard`，`E:\aicoding\targets\more-itertools-hard`，
  upstream pin `81c21a8db1c922260cd673841257fb9b112c1318`，
  靶场 HEAD = `d3c6ad0bdcad54c367ccfeec2be7471e263df975`（脱离分支），工作区干净。
  **与第二轮同一个靶场，零改动**（这样 P1 的聚类才有已知的 ground truth 可对照）。
- 3 处缺陷全部注入在**私有辅助函数**，名字不出现在任何测试节点 ID 中（第二轮 §2 的原样）：

| 缺陷（私有函数） | 文件 | 失败测试类 | 期望失败数 |
|---|---|---|---|
| `_complex_sumprod`：`r2` 实部/虚部对调 | `more_itertools/more.py` | `DiscreteFourierTransformTests::test_basic` / `::test_roundtrip` | 2 |
| `_sliding_window_islice`：`islice(i, i)` → `islice(i, i + 1)` | `more_itertools/recipes.py` | `SlidingWindowTests::test_islice_version` | 3 |
| `_islice_helper`：负 start `len_iter = next(counter) - 1` → `next(counter)` | `more_itertools/more.py` | `IsliceExtendedTests::test_all`(25) + `::test_elements_lifecycle`(2) | 27 |

  合计 **32 failed / 766 passed**（第二轮实测，落盘 `servers/experiments/out/setup-hard.log`）。修好全部 3 处 ⇒ **0 failed / 768 passed**。
- **P1 的聚类 ground truth**：失败类集合应为上述 3 个类名，`K == 3`；`_islice_helper` 与 `_complex_sumprod` 同处 `more.py` 但分属不同类 ⇒ 本轮**不能**顺带测出"跨文件聚类会不会更好"（见 §6.3）。
- 题面：两臂都用 `coding_agent.py:270-290` 的 `TASK_BASE` **逐字不动**（含那条 DIY 流程 —— 它在 D 臂里不再是竞争者，因为派发已由 driver 决定，不由模型决定）。D 臂**不追加** `TASK_SUBAGENT_NOTE`。靶场 `README.md` 那句"失败信息会指向表象，根因可能在它的上游"保持第二轮原样。
- 模型 / 端点 / `SCOUT_MAX_TURNS`（现值 24，`coding_agent.py:216`）/ 温度等全部冻结；`out/` 目录每臂开始前清空重建。

---

## §3 不变量（违反任一条 ⇒ 该次运行作废并重跑该格，数据不进表）

1. **靶场的"坏"是它自己 HEAD 的不变量**：3 处缺陷已提交进靶场 HEAD；`reset_fixture()` 用 `git checkout` 从 HEAD 还原。每轮**开跑前复位、跑完立刻复位**（第一轮踩过"agent 的修复漏进仓库"这个坑）。
2. **基线必须是红的、而且红得符合预期**：开跑前跑一次基线 pytest，`baseline_failures == 32` 且 `baseline_exit_code == 1`，否则中止（退出码 3）。终局判定用**独立 pytest 的退出码与失败数**（`final_failures == 0` / `final_passed == 768`），**不采信 agent 自述**。
3. **测试文件是硬约束**：两臂每次运行的 `test_files_touched` 必须为空；`edits_outside_scope` 必须为空。
4. **P4 快照只读性**：scout 一律通过 `AGENT_REPO_ROOT` 指向的 detached worktree 读代码；快照目录在整个运行期间**不被任何进程写入**（开跑前后各校验一次快照的 `git status --porcelain` 为空）。若快照被写过 ⇒ 该次运行作废（这条不变量就是 C-2 的直接防线）。
5. **P5 校验独立性**：验证 `predictedFailureCount` 的进程**不是** scout 本身，且在**副本**上应用补丁；scout 的 `minimalFix` 字符串与实际应用的补丁逐字一致（不一致 ⇒ 记 `refuted`，不记 `verified`）。
6. **D 臂的派发总数守恒**：每次运行记录的 `dispatched + retried` 之和 ≥ K（每组至少派过一次），且每个 `scout-<label>-<k>.json` 都有对应的状态记录。缺记录 ⇒ 该次运行作废。
7. **量具自洽**：每次运行满足 `input_uncached + cache_read + cache_creation == 总输入口径`（三者由 P0 分别记账，和应等于对应口径的合计）；且 trace 逐回合求和与 `model_usage` 的差异已被 P0 解释（见 §5 C0）。对不上 ⇒ 先修量具，不许读数。
8. **交错执行**：8 次运行按 `A,D,A,D,A,D,A,D` 顺序执行，中间不插入任何代码改动。若期间必须改 harness ⇒ **已跑的 4 格全部作废重跑**（第二轮 §7.0 那次中途改 `SCOUT_MAX_TURNS` 的教训：干预要写在数据前面，不能写在后面）。

---

## §4 假设（每条：主指标 + 判据 + 证伪条件；保留"不可测"出口）

### H1 采用率 —— **降级为前置检查，不再是主指标**

- **主指标**：D 臂每次运行的 `subagent_spawn_calls`，与 driver 记录的 K 对比。
- **判据**：4 次运行全部 `subagent_spawn_calls == K`（预期 K=3；含 P3 自动续派时按"每组至少有一次 `completed`"计）。
- **证伪条件**：任一次运行出现 `subagent_spawn_calls < K` ⇒ 驱动派发没按构造生效（通道坏了，不是模型不选）。此时**本轮全部停止**，回到实现排查，不产出任何 A vs D 的结论。
- **为何不再是主指标**：P1 把决策搬到 driver，采用率按构造应为 100%。它剩下的唯一作用是**证明治疗确已施加**（§5 的前置门槛）。第二软的教训就在这一点上：B′ 臂 0/4 从未施加治疗，主指标那一格却照样"区间不重叠"。

### H2 成本 —— 有界报告能否把派发的额外开销收回来

- **主指标**：父层 `input_uncached` 的**中位数**（口径由 P0 固定，见 §0.2 与 §5 C0）。
- **判据**：`median(input_uncached | D) <= median(input_uncached | A)`，且按 §5 的极差区间规则读出"不高于"（D 的极大值 ≤ A 的极小值 ⇒ 支持；区间重叠 ⇒ 不确定）。
- **机制预期**：A 臂父层要为"翻 5,644 行文件找 3 个根因"付未缓存输入；D 臂把这部分搬进 scout，父层只多收一份 ≤2000 字符索引 ×K。若 P2 真的有效，D 的父层应当**不高于** A（注意：D 的父层还额外承担"读索引 + 可能调用 `read_findings`"的成本，所以这里不是显然的）。
- **证伪条件**：D 的中位数高于 A 且区间不重叠 ⇒ P2 的有界报告**不足以**收回 P1 的开销。这是一次有价值的否证：说明瓶颈不在报告体积，而在别处（例如 K 份索引本身、或父层拿到结论后反而多跑了回合）。
- **"不可测"出口**：若 H1 前置检查未通过（有任何一组未被真正派出去），H2 **判"不可测"，不判"支持"也不判"证伪"**。若 C0 未通过（口径未澄清），同样判"不可测"。

### H3 质量 —— 分工不能牺牲正确性

- **主指标**：成功率 = `final_failures == 0` 的运行次数 / 4（独立 pytest 判定，不变量 2）。
- **判据**：`success_rate(D) == success_rate(A)`（预期两臂都 4/4），**且**两臂每次运行都修好了全部 3 处缺陷 —— 逐个核对最终 diff 是否命中 §2 表中三处（`apply_patch_count == 3`、`changed_files` 恰为 `more_itertools/more.py` + `more_itertools/recipes.py`）。
- **证伪条件**：任一臂出现失败（<4/4），或某次运行只修了 2 处却靠别的改动让测试变绿 ⇒ D 不比 A 好、甚至更差；即使成本降了也不能算成功。
- **"不可测"出口**：若靶场基线不是恰好 32 failed（不变量 2 破）⇒ 该次运行的 H3 格子不计入。若 H1 未通过，H3 仍可测（成功率不依赖治疗施加与否的解释权），但**必须注明"这是在未施加治疗的条件下测的"**。

### H4 校验有效性 —— P5 是不是真过滤器

- **主指标**：`verified_ratio = verified / (verified + refuted)`，按全部 D 臂 finding 汇总；以及 refuted finding 的**原始数据抽查**。
- **判据**：(a) `verified_ratio > 0`（否则 P5 一条都没放进来，父层拿到的索引是空的或全是噪声）；(b) 对**每一条** `refuted` 的 finding 人工核查其 `rootCause` / `minimalFix` / `predictedFailureCount`，确认"它确实不该进父层索引"（要么根因错、要么最小改法错、要么预测失败数与实际不符）。
- **证伪条件**：出现任一 `refuted` 其实是对的（被程序误杀）⇒ P5 的判据本身有 bug，此时"H2/H3 的收益"要打折看待，因为它建立在"父层只看到 verified"这个前提上。反之若 `verified` 里有事后看是错的结论 ⇒ P5 给的信心是假的，这是更坏的失效，必须单独写明。
- **不可测出口**：若 D 臂共产生的 finding 数为 0 ⇒ H4 判"不可测"（不是"通过"）。

### H5 截断 —— 不允许"半份报告被当成完整结论"

- **主指标**：D 臂被截断的派发次数，以及这些截断是否全部被记成 `truncated` 并触发自动续派。
- **判据**：满足以下之一即通过 —— (a) 被截断次数 = 0；(b) 每一次截断都有 durable 状态 `truncated`、都发生了自动续派、最终该组状态 ∈ {`completed`, `undiagnosed`}，且**没有任何一次把 `PARTIAL` 内容写进父层索引**。
- **证伪条件**：任一 `truncated` 未被记录（静默发生）、或任一 `PARTIAL` 报告的内容进了父层索引、或续派未发生 ⇒ P3 没起作用，本轮关于"截断已被治理"的说法全部作废。
- **不可测出口**：若 D 臂 4 次运行一次都没派 ⇒ 同 H1，判"不可测"。

---

## §5 判据与样本量（跑之前写死，跑完不许回头改）

- **n = 4 / 臂**（沿用两轮），8 次运行按 `A,D,A,D,A,D,A,D` 交错（不变量 8）。
- **主指标族**：H2 用父层 `input_uncached` 中位数；H3 用成功率计数；H4/H5 用比率/计数。
- **"信号"的定义**：两臂的**极差区间不重叠**（min/max 区间）。区间重叠 ⇒ 一律记"不确定"，**不算支持也不算证伪**。
- **不报 p 值**：n = 4 时 Wilcoxon 符号秩双侧最小 p = 2 / C(8,4) = 2/70 ≈ **0.029** —— 只有 4 对 4 全胜才够 0.05。两轮都只用区间，本轮沿用，避免制造一个 n=4 撑不住的统计量。

### 前置门槛（必须先过，才能读任何"信号"）

- **C0（量具门槛，P0 的验收）**：`input_uncached` / `cache_read` / `cache_creation` 三个字段在 metrics 与 trace 里都存在、口径唯一，且逐回合 Σ 与 `model_usage` 的 2× 差异有书面解释。**C0 不过 ⇒ H2 全部格子判"不可测"。**
- **C1（治疗门槛，H1 的前置检查）**：D 臂 4 次运行**全部** `subagent_spawn_calls == K`。**C1 不过 ⇒ H2/H4/H5 一律判"不可测"，禁止用"区间不重叠"宣布任何信号。**

### 这条门槛的来历（原文写进 §5，因为它就是本轮方法学的核心修正）

> 第二轮 `AB-SUBAGENT-HARD.md` §7.3 原文：*"B′ 一次都没派子代理。这意味着 B′ 与 A′ 的差别只有题面里多了一段'你可以派子代理'的邀请文字，治疗从未被施加 —— 所以这一格比较的是**噪声**，不是分工。而它恰好'区间不重叠'（B′ 最大 26537 < A′ 最小 27317，只差 **780 tokens = 2.5%**）。一个**空治疗**都能让预注册的'区间不重叠 ⇒ 有信号'规则成立，说明这条规则在 n=4 时太松。"*

⇒ **本轮规则：判"信号"之前必须先通过"治疗确已施加"的前置检查（C1 ⇐ H1）。未通过则主指标格子记"不可测"，不记"支持"、不记"证伪"。** 这条写在数据之前的规则，是 v2 相对 Round 2 最主要的方法学改动。

### 其他固定判据

- **C2**：两臂每次运行的 `test_files_touched` 都为空、`edits_outside_scope` 都为空（不变量 3）。
- **C3**：两臂每次运行的基线都是 32 failed / 766 passed（不变量 2 的运行时校验）。
- **C4**：D 臂每次运行的父层索引实际字符数 ≤ 2000（P2 的硬上限；超限即视为 P2 未实现，H2 失去解释基础）。
- **C5**：D 臂每个 `scout-<label>-<k>.json` 的 schema 逐字符合 §1.1 P2/P5 的字段定义，且 `read_findings(id)` 能按 id 取回全文。
- **C6**：P4 快照在每次运行结束后 `git status --porcelain` 仍为空（不变量 4），并且父 agent 的工作区**不是**那个快照。
- **记录但不进假设检验的定性证据**：每条 finding 的人工抽查（至少覆盖全部 `refuted` 与随机 3 条 `verified`）；K 份索引的实际字符数；`read_findings` 被父层调用的次数（若为 0，说明父层根本没看细节 —— 这对解读 H2 很关键）。

---

## §6 本阶段**不能**证明的事

1. **单模型、单靶场的结论不能外推**。本轮一切数字只在 `deepseek-flash[1m]` × `more-itertools-hard` × `TASK_BASE` 这个三元组下成立。换仓库、换语言、换说法、换模型都可能完全不同 —— 两轮之间已经演示过一次"同一个结论换个靶场仍然成立/换个口径就不明"的脆弱性。
2. **n = 4 检验力不足**。只能看极差区间重不重叠，不能算显著性；第二轮已经证明这条规则连"空治疗"都能放行（§5）。即便本轮加了 C1 门槛，n=4 仍然分辨不了 5%~10% 量级的差异。
3. **K 的聚类方式对结果的影响未测**。本轮钉死"按失败测试类聚类"，预期 K=3。按文件聚类、按失败数均衡切分、合并同类、给一个 scout 派两组 —— 这些都会改变 K 与每组负载，也几乎肯定会改变成本和结论，但**本轮一概不做**，因此不能说"驱动派发省 X"，只能说"在这种聚类下、这个靶场上是这样"。
4. **"驱动派发"是对 ④ 层命题的重新定义，必须明说**。④ 层原本问的是"**agent 会不会自主分工**"（一个关于模型行为/意愿的问题）。P1 把派发决策从模型搬到 driver 之后，本轮回答的是"**如果由 harness 强制并行侦察、并把报告体积和状态管起来，端到端成本与质量如何**"。这是另一个问题。**本轮的正向结果不能被引用成"模型学会了用子代理"或"subagent 分工这一层做成了"** —— 模型在本轮根本没有做过这个决策。原来的"意愿"问题在 v2 之后仍然是开放的（0/12 的历史不会因为本轮而翻案）。
5. **P5 只验证"预测失败数对不对"，不验证"根因讲得对不对"**。一条 finding 可以 `verified`（改下去确实让那 32 个失败归零）却在解释上是错的或含糊的。本轮不测"结论的可解释性"，也不测"父层是否理解并正确使用了指令"。
6. **不测并发/调度收益**。本轮 K 个 scout 的派发时序（串行还是并行）不作为变量；耗时（`elapsed_s`）只作描述性记录，不进假设检验。
7. **不测多模型/多靶台的泛化**，也不测"去掉其中某一项 P 会怎样"（消融）。本轮 D 臂是 P1+P2+P3+P4+P5 的**打包治疗**，任何单项的贡献都无法从本轮数据里分离出来。
8. **成本口径仍是"父层视角为主"**。H2 只看父层 `input_uncached`；scout 侧的用量与账单本轮只做记录、不做主指标，所以"D 臂不高于 A 臂"这句话**不等于**"整个系统更便宜"。

---

## 附：本文档的硬约束（写给实现者，也写给未来的我）

- 本文件是**唯一**允许在本次任务里创建/修改的文件；不改 `coding_agent.py`、不改 `servers/**`、不改 `README.md` / `CLAUDE.md`。
- 提交时 `git add` **只加 `AB-SUBAGENT-V2.md`** 一个文件（工作区当前干净：`git status --porcelain` 为空，HEAD = `d1d87de`）。
- 本文档不跑实测、不预先填写 §7。§7 只在跑完之后追加，且不得改写 §0–§6 的任何字。

## ⚠️ 两条「必须守」（文档里也要显眼写出）

**(a) 不得把代码里的 `policy_c1_gate` / runner manifest 标签当治疗门槛引用。**
它仍是**旧的结论率定义**（`policy_gate_criterion` = 「每组至少有一次 scout 结论」），本批 D-4 显示 `failed`
（该组派发已发生、attempt=completed，但 scout 报告非法 JSON ⇒ 无结论）。本批治疗门槛按**合同写定的口径**
（逐组 `dispatched_once=true` 且至少一次 attempt `completed`）判定 = **12/12 组、4/4 轮全过**。
引用 4/4 之外的说法（例如「C1 失败 ⇒ H2/H4/H5 不可测」）都是错的。

**(b) 不得把本轮结果写成「模型学会了自主分工」。**
预注册 §6.4：派发决策已搬到 driver（P1），模型本轮**根本没有做过这个决策**；两轮共 12 次「邀请派发 0/4」
的历史不因本轮翻案。也**不得**把 D-4 第 1 组写成「scout 没定位」——它**定位了**（报告里给出 lines 2732-2763、
`len_iter = next(counter)`、`predictedFailureCount=27`），但报告是**非法 JSON**（字符串里出现 Python 风格 `\'`，
`json.loads` 报 `Invalid \escape`）⇒ 按规则记 `undiagnosed`、父层自己补做；结论率 11/12 的成因是**报告格式失败**，
不是能力失败。

---

# §7 结果（注册批 subagent-ab-v2b，2026-10-11）

## 7.0 流程与作废说明（如实）
本批是「**跳过一次形式修复轮**」后的注册批：t18 复审 t17（`6583afd`）为 10 条里 9 条通过，唯一未通过项是
成本口径（F-cost），另有 high 级门槛定义问题（F-gate）；t18 判定这些 finding 全属**口径/记录级**、
「代码层无需再改」。插件的自动修复闭环在 t18 后触顶，captain 据 t18 建议把 **F-gate 的治疗门槛定义**与
**F-cost 的措辞**写进 t19 的合同（**判据写在数据之前**），并经用户批准跳过第 4 轮修复。
⇒ 本批**代码树与 t17 完全相同**（HEAD = `6583afd`）：F-gate 在代码里仍是旧定义（`policy_c1_gate`、
`policy_gate_criterion`、runner manifest 行仍写「每组至少一次 scout 结论」），**本批只是按合同定义重算门槛**
—— 这不是「修复已完成因此合规」，而是一次被显式声明的流程选择。

作废与保留：旧批 `subagent-ab-v2`（D-v2）跑在 `read_findings` 全程失效（0/12）的代码上 ⇒ C5 不成立；
`53428d8` 版（D-v2b）被 t11 查出 blocker B1（包装会话模型自己的输出变成「scout 的结论」）；
两批**原始产物一律保留**作对照，不入任何主结论表。

## 7.1 C0–C6 判定（注册批）
- **C0 过**：`input_uncached` / `cache_read` / `cache_creation` 三口径在 metrics 与 `usage.jsonl` 齐备；
  trace 逐回合 Σ == `ResultMessage.usage.input_tokens` **8/8 精确相等**（例 D-1 12785 == 12785）；
  P0 的「逐回合 Σinput ≈ 2×」已查明为真重复计数并在 `agent/trace.py` 修掉。
- **C1 过（按合同写定的治疗门槛口径）**：**12/12 组 `dispatched_once=true` 且至少一次 attempt `completed`**；
  每轮 K=3；**结论率 11/12（91.7%）单列**。
  ⚠️ 代码产出的 `policy_c1_gate` 是旧定义（结论导向），本批 D-4 显示 `failed`，**不得**拿它当治疗门槛。
- **C2 过**：8/8 轮 `test_files_touched=[]`、`edits_outside_scope=[]`、`changed_files` 恰为
  `more_itertools/more.py` + `more_itertools/recipes.py`。
- **C3 过**：8/8 轮基线 32 failed / 766 passed，终局 0 failed / 768 passed（独立 pytest 退出码判定）。
- **C4 过**：D 臂父层索引实际字符数 733 / 698 / 706 / 663，全部 ≤ 2000（硬上限常量）。
- **C5 过**：见 7.3。
- **C6 过**：快照 head `d3c6ad0…`、`policy_snapshot_removed=(exit=0)`、派发后 worktree 数 = 1；
  靶场 `git worktree list` 仅主工作区、`git status --porcelain` 为空；父层工作区 ≠ 快照（父层 env 指向靶场根）。

## 7.2 H1–H5 判定（注册批）
- **H1（采用率，前置检查）过（治疗口径）**：见 C1。预注册字面写的 `subagent_spawn_calls == K`
  **已不适用**（D 臂该键按设计恒 0，见 7.7 R1）；实际主口径 = `policy_scout_sessions`（= Σ每组 attempts，
  含 driver 续派）+ `policy_k_groups`，并以逐组 `dispatched_once` / attempt 状态佐证。
- **H2（父层 `input_uncached`）数据支持**：A 中位 **29200.5**，极差 [23451, 32913]；D 中位 **14993.0**，
  极差 [14234, 21125]；D 的极大值 ≤ A 的极小值 ⇒ 按 §5 的极差区间规则，信号方向成立（比值 **0.5135**）。
  ⚠️ 幅度与解释见 7.6/R6：本批父层**真的取回了细节**（11 次 `read_findings` 成功，每次 1.6–2.5k 字符），
  所以本批比值比旧批（0.4117，当时取回通道全程失效）更干净；但两批都只说明**父层**更省。
- **H3（质量）数据支持**：两臂各 4/4 成功（独立 pytest）；24 处 `apply_patch` 全部落在 §2 的 3 个
  ground-truth 站点（23 处与预注册最小改法逐字一致，D-1 的 DFT 一处为同站点语义等价改法）；测试文件零改动。
- **H4（P5 是否真过滤器）**：(a) 过 —— verified 11 / refuted 0 ⇒ `verified_ratio = 11/11 = 1.0 > 0`；
  (b) **不可测** —— 本批 0 条 refuted，**无核查对象**（不是「通过」）。
- **H5（截断）**：(a) 成立 —— 本批截断 0 次（12/12 attempt `completed`）；
  (b) **不可测** —— 「截断 → 自动续派」路径本批**未触发**（该路径只在上一批 `--scout-turn-cap 2`
  的负对照里验过：truncated×3 → 各续派一次 → 2 组 undiagnosed / 1 组 verified）。
- **泛化**：n=4、单靶场（more-itertools-hard）、单模型（deepseek-flash[1m]）、单题面，一律**不外推**
  （§6.1/6.2/6.7）。

## 7.3 C5 结论（`read_findings`）
- **父层真的用了它**：D 臂 4 轮共调用 `read_findings` **11 次**（逐轮 3/3/3/2），**成功 11 次、失败 0 次**
  （失败原文：无；重名 / 未找到 / 拒绝类日志 0 条）；取回长度 1609–2476 字符
  （例 `subagent-ab-v2b-D-1-g1-f1` → 2476 字符，来源 `scout-subagent-ab-v2b-D-1-1.json`）。
- **工具可用（独立于本次跑批）**：当前代码下实时装载 MCP server 实测 —— 旧形状 `g1-f1` 明确报
  「在多份产物里重名」并列出可用的带 label id；带 label 的**历史产物** id（`subagent-ab-v2-D-1-g1-f1`）
  与新产物 id（`subagent-ab-v2b-D-1-g1-f1`）都能唯一取回；不存在的 id 与穿越型 id 行为正确。
  离线回归 `servers/experiments/v2_readfindings_scope_check.py` 全部通过。
- **`summary_source` 分布**：11 条 finding 全部来源 `summary`（`evidence_author=scout`），
  **来源为包装模型文本的 = 0**；1 组无结论（原因见 7.8）。
- 结论：**C5 成立**（工具可用 + 父层确实使用了它 + 来源单一）。旧批的 0/12 作为「修复前」对照保留。

## 7.4 两批对照（`subagent-ab-v2` = D-v2 vs `subagent-ab-v2b` = D-v3）
| 指标 | 旧批 subagent-ab-v2（D-v2） | 注册批 subagent-ab-v2b（D-v3） | 解释 |
|---|---|---|---|
| A 父层 `input_uncached` 中位 | 33133.5 [24897, 35100] | 29200.5 [23451, 32913] | A 是「不派发」对照臂，绝对水平逐批波动；跨批只读方向 |
| D 父层 `input_uncached` 中位 | 13642.5 [13385, 14607] | 14993.0 [14234, 21125] | D 略升由 D-4 轮抬高（21125：该轮有 1 组无结论，父层自己补做） |
| D/A 比值（父层口径） | 0.4117（区间不重叠） | **0.5135（区间不重叠）** | 方向两批一致；本批父层真的取回了细节，比值更干净 |
| 成功率 A / D | 4/4 / 4/4 | 4/4 / 4/4 | 无差异 |
| D 索引字符数 | 575 / 565 / 645 / 599（中位 587） | 733 / 698 / 706 / 663（中位 702） | +115 ≈ label id（+20×3）+ 来源标注（+19×3） |
| `read_findings` | 12 调用 / **0 成功** | 11 调用 / **11 成功** / 0 失败 | R2 修复前向生效 |
| 父层 `cost_usd` 中位 A / D | 1.045659 / 0.176076 | 0.681227 / 0.159758 | A 绝对值波动大；D 稳定 |
| scout 侧成本 | **未记录**（端到端不可算） | 0.773373 / 0.752672 / 1.042999 / 1.023188（Σ 3.592232） | D-v2 无此口径 |
| D 端到端（父+scout） | 不可测 | 中位 **1.065311**，scout 占比中位 **82.7%** | 与 A 父层中位 0.681227 比 = **1.5638×** |

**跨形态不可比声明（三项，必须写进任何引用本表的结论）**
1. D-v2（t4 批次）与 D-v3（本批）内部形态不同（见 7.5），只能比较「治疗是否施加、父层 `input_uncached`」
   这类不依赖结论产量的指标；**不得**跨形态比较结论产量、`verified_ratio`、scout 侧成本与端到端成本。
2. 三批（`subagent-ab-v2` / `53428d8` 版 / 本批）中**只有本批是注册批**，前两批只作「修复前会怎样」的对照。
3. D-v2 的端到端成本**不可测**（当批 scout 用量未记账），不得用任何估算替代。

## 7.5 D-v2 与 D-v3 的六处内部差异
1. finding id 带运行 label 前缀（`<label>-g<k>-f<n>`；旧形状 `g<k>-f<n>` 仍可读）；
2. 结论来源锁定为**只认 scout** 的三个来源（通知 summary / scout output_file / 带该次 Task
   `parent_tool_use_id` 的流式文本）；**包装会话文本一律不作数**；
3. driver 会话的 `PreToolUse` 钩子按调用来源判：主线程调 `mcp__code-tools__*` 一律 deny、scout 放行；
4. driver 会话**每轮只许派一次 Task**（续派只由 harness 做）；
5. driver 内部派发提示词要求 scout **前台运行**（`run_in_background=false`）；
6. `forward_subagent_text=True`，scout 的 `permissionMode` 改为 `bypassPermissions`
   （工具清单仍是 5 个只读 MCP 工具，只读不变量由工具清单保证）。

以上**不改变 A vs D 的臂定义与父层题面**（已用 AST 逐函数核对 `build_task` / `sandbox_spec` / `run_agent`
与本轮之前逐字相同；预注册 §1.1 的冻结项 —— 落点 `out/scout-<label>-<k>.json`、id 可读、schema 8 字段、
索引 ≤2000、`TRUNC_MARK`、P5 判据 —— 均未改动）。

## 7.6 成本更正（F-cost）
- 上一轮报告把 scout 侧 **5.77702 USD** 说成「包装模型自发多派 12 个 Task 的失控态、已被钩子挡住」；
  **更正**：5.77702 USD 是**修复后**那次 cap=2 负对照（label `v2-b1c-trunc`）的账单，该轮 durable 行
  同时带钩子的拒绝计数（`wrapper_mcp_calls=14`、全部 denied、`effective=0`；额外 Task 12 次被拒）。
  修复前同 cap=2 负对照（`v2-fix-trunc`）只有 **1.404199 USD**、scout 会话墙钟 **Σ160.9s**；
  修复后为 **5.77702 USD**、**Σ765.7s**（单次最长 attempt 187.4s）。⇒ **钩子挡住的是效果
  （effective=0），不是花费**：模型被拒后反复重试，每次拒绝仍要付费，负对照账单反而涨到 **4.1×**。
- 本批（cap=24）没有出现这种失控：`wrapper_mcp` 合计 1 次（被拦）、`extra_task=0`、`background_spawns=0`
  ⇒ 正常路径下包装模型不重试。
- 另：`TAIL_AFTER_NOTIFY_S=180` 是**每条消息**的超时，**不是**尾段总预算（t18 实测一次 attempt 187.4s
  仍拿到 ResultMessage）；尾段只受外层 `SCOUT_RESULT_TIMEOUT_S=900` 约束。

## 7.7 勘误三节（预注册 §1–§6 不改写，只在此声明）
- **R1（H1/C1 的预注册指标不适用）**：§4 H1/§5 C1 写的 `subagent_spawn_calls == K` 在 D 臂**按设计恒 0**
  （父层没有 Task 通道）。实际使用的主口径 = `policy_scout_sessions`（= Σ每组 attempts，含 driver 续派）
  + `policy_k_groups`；治疗门槛 = 逐组 `dispatched_once=true` 且至少一次 attempt `completed`；
  **结论率**（拿到 scout 结论的组数）单列。这是一次**事后口径替换**，在此明写。
- **R3（AgentTeams 质量门的两条量具发现不成立）**：读 `lib/quality-gates.js` 源码 —— 完成门槛
  `evaluateQualityCompletion`（:347）的 implementation/repair 分支（:392-403）只用 `classifyChangedPath`
  （:130-140）做 inScope 分类，**从不对比 `git status`**（`collectChangedPaths` :141-161 未被调用）；
  该文件也**不存在**「只接受单元素 changedPaths」的限制（`nonemptyStringList` :179-181 允许任意长度，
  :397-402 逐元素循环，:400 的信息按 `<path>` 参数化）。t3 当时的 `changedPaths` 申报（1 项）与提交实际
  改动的 9 个文件不符；终端任务不可改，故只在此记载。
- **R6（H2 幅度）**：旧批的 0.4117 是「有界报告 + `read_findings` 通道失效」的合成值（父层一条细节都没取到），
  属**上界**；本批取回通道正常（11/11 成功），0.5135 是更干净的估计。两批都只能说「**父层**未缓存输入
  不高于 A」，不能推出「整个系统更便宜」（7.4 的端到端数字是反方向的）。
- **R8（P0 口径）**：三个 input 口径的书面说明此前只在代码注释与 `caveats` 里；trace 侧仍用 SDK 原名
  （逐回合 `input_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens`），且**逐回合 Σ 属第三个
  口径**（`ResultMessage.usage`，8/8 精确相等），与 `metrics.input_uncached`（model_usage 合计）不同源。
  本节即为其书面出处之一。

## 7.8 不得主张的事（预注册 §6.4 及其它）
- **不得**把本批正向结果写成「**模型学会了自主分工**」「agent 自主采用了 subagent」「④ 层做成了」：
  本轮派发决策由 driver 做出（P1），模型根本没做过这个决策；「邀请派发 0/4」的历史**不因本轮翻案**（§6.4）。
- **不得**写成「D 臂更便宜」：父层不高于 A ≠ 系统更便宜（本批端到端 D 中位 1.065311 vs A 父层中位 0.681227
  = **1.5638×**；scout 占比中位 82.7%）（§6.8）。
- **不得**写成「C1 失败」：那是代码里的旧定义（结论导向）；按合同写定的治疗门槛口径是 12/12 组通过。
- **不得**写成「D-4 那组 scout 没定位」：它定位了（报告里给出 lines 2732-2763 与 `predictedFailureCount=27`），
  但报告是**非法 JSON**（字符串里出现 Python 风格 `\'`，`json.loads` 报 `Invalid \escape`）⇒ 按规则记
  `undiagnosed`、父层自己补做；结论率 11/12 的成因是**报告格式失败**，不是能力失败。
- **不得**写成「H4 是真过滤器」「截断已被治理」：本批 0 refuted、0 截断 ⇒ 两者**无对象**，只能写「不可测」。
- **不得**跨形态比较结论产量 / `verified_ratio` / scout 侧成本（见 7.4 三项声明）。
- **不得**外推：n=4、单靶场、单模型、单题面（§6.1/6.2/6.7）。

## 7.9 §7 可写 / 不可写清单
**可写**：7.0 流程与作废说明；7.1 C0–C6；7.2 H1–H5（含 H2 的父层口径与幅度说明）；7.3 C5 结论
（11/11/0、工具可用 vs 父层真的用了、`summary_source` 分布）；7.4 两批对照表 + 三项不可比声明；
7.5 D-v2/D-v3 六处差异；7.6 F-cost 更正（1.404199 → 5.77702、Σ160.9 → 765.7、钩子挡效果不挡花费）；
7.7 R1/R3/R6/R8 勘误；7.8 不得主张的事；以及「本批跳过一次形式修复轮、代码树 = t17、门槛定义写在数据之前」
这一流程事实。

**不可写**：任何「模型学会了自主分工 / ④ 层做成了」式结论；任何「D 臂整体更便宜」；「C1 失败」（旧定义）；
「D-4 scout 没定位」；「H4 已验证为真过滤器」「截断已被治理」；把 `policy_c1_gate` / manifest 行直接当作
本批治疗门槛；跨形态比较结论产量与 scout 成本；把 n=4 / 单靶场的数字写成通用结论；把本批与旧批混成一张
没有形态标签的主结论表。

## 7.10 粘贴保真、与产物的分歧与补全（integrator2，t21 追加；**非草稿正文**）

**§7.10-0 本节性质与保真声明**

- 上面「两条必须守」与 7.0–7.9 **逐字**取自审查员草稿文件
  `servers/experiments/out/v2b-section7-draft.md`（t20 交付；sha256
  `BA7AF02F80B7050E4F891B706841BCEB59D3A397169EDF8AA2A3EEE3F2D82D44`、19112 字节 / 198 行 / U+FFFD = 0；
  该文件是**草稿，不是证据**）。粘贴取的是草稿第 13–177 行，**标题层级原样保留**（未改一个字符）。
- **本节（7.10）是粘贴时新增的说明，不在草稿原文里。** §0–§6 一字未改，删除 0 行、修改 0 行（对照证据见提交信息里的
  `git show <提交> -- AB-SUBAGENT-V2.md`：改动全部是新增行）。
- 凡本节与草稿不一致处，**以原始产物 / 审查原文为准**，并在下面逐条写明分歧与我的核对方式。

**§7.10-1 我（t21）的独立复算（只读 `servers/experiments/out/`，不采信任何转述）**

- 父层 `input_uncached`：注册批 A = `[23451, 25987, 32414, 32913]`，中位 **29200.5**；D = `[14234, 14864, 15122, 21125]`，
  中位 **14993.0** ⇒ 比值 **0.5135**，D max ≤ A min（区间不重叠）。旧批 A = `[24897, 31765, 34502, 35100]`
  中位 **33133.5**；D = `[13385, 13484, 13801, 14607]` 中位 **13642.5** ⇒ 比值 **0.4117**。
- 父层 `cost_usd` 中位：注册批 A **0.6812275**、D **0.159758**；旧批 A 1.045659、D 0.176076。
- 端到端（父层 + `policy_scout_cost_usd`）：0.942066 / 0.903495 / 1.188557 / 1.428528，
  中位 **1.0653115**、Σ **4.462646**（父 0.870414 + scout Σ **3.592232**）；
  scout 合计占比 **80.5%**、逐轮占比中位 **82.7%**；与 A 父层中位比 **1.5638×**。
- F-cost 两组负对照（`policy_scout_usage.jsonl` 逐行求和）：`v2-fix-trunc` Σ **1.404199 USD** / 墙钟 Σ **160.9s**；
  `v2-b1c-trunc` Σ **5.77702 USD** / 墙钟 Σ **765.7s**（单次最长 187.4s）。
- D 臂父层索引字符：**733 / 698 / 706 / 663**；`policy_worktrees_after_dispatch=1`、`policy_snapshot_removed=(exit=0)`、
  快照 head `d3c6ad0…`（4/4 轮一致）。
- `read_findings`：逐轮 `tool_calls_per_tool` = **3 / 3 / 3 / 2**；MCP server 日志窗口
  2026-10-11 01:49:50–02:08:51 共 **11 次** `handler 调用`，id 全是带 label 的唯一 id
  （`subagent-ab-v2b-D-1-g1-f1` … `subagent-ab-v2b-D-4-g3-f1`）；trace 里 11 次 `tool_use → tool_result` 配对
  **0 次报错**。⇒ 「11 调用 / 11 成功 / 0 失败」两处独立成立。

**§7.10-2 分歧与补全（逐条）**

1. **分歧一（字段基准，不是数字冲突）——取回长度。** 7.3 写「取回长度 1609–2476 字符（`…-D-1-g1-f1` → 2476 字符）」。
   我按 `agent-subagent-ab-v2b-D-*.json` 里 `tool_result` 事件的 `chars` 字段（与 `len(text)` 逐字相等）复算为
   **1727–2596 字符**，其中 `subagent-ab-v2b-D-1-g1-f1` → **2596**，其余 10 次落在 1727–2314。
   两者相差约 118–120 字符，方向与量级一致（都是「约 1.7–2.6k 字符的细节按需取回」）。
   **按「以产物为准」，以本节复算值为准；7.3 的 1609–2476 保留为草稿值备查。**
2. **补全 R1 的理由句（7.7 R1 只给了定义，没给「为什么必须这么定义」）。** 引 t18 的 F-gate 原文：
   > 两者在正常情况下重合，但在「scout 诚实交空 findings（未定位）」时**分叉**：那是一次正常的、题面明确允许并鼓励
   > 的产出（`_scout_prompt`：「没能定位根因就交 `{"findings": []}`」），dispatch 已发生、索引也已把「未定位」交给父层，
   > 但 `scout_conclusion_obtained=false` ⇒ `policy_c1_gate=failed`。按预注册 §5 C1 的写法，C1 不过就要把
   > H2/H4/H5 一律判「不可测」——只要有一组 scout 交空或两次都被截断，就会把一整批**有效施加了治疗**的运行判成
   > 「治疗未施加 / 不可测」。

   本批 D-4 第 1 组正是这个分叉的实例（派发已发生、attempt `completed`，但报告非法 JSON ⇒ 无结论）——
   若按旧字段判，本批会被误判成「治疗未施加」。
3. **补全 R3 的文件清单（7.7 只写了「1 项 vs 9 个文件」）。** 引 t5/t11 的 finding 原文：t3 实际入库的 9 个文件 =
   **6 个 inScope 内**（`coding_agent.py`、`agent/trace.py`、`agent/policy_dispatch.py`、`servers/code_tools_server.py`、
   `servers/experiments/subagent_ab_runner.py`、`servers/experiments/subagent_selfcheck.py`）
   + **3 个合同外**（`servers/experiments/usage_log.py`、`servers/experiments/subagent_review.py`、
   `servers/experiments/p0_reconcile.py`）；而 t3 当时只申报了 `coding_agent.py` **1 项**，完成门槛照样放行 ——
   这正是「门槛只做 inScope 分类、不核对申报完整性」的现场证据（第二条「单元素限制」论断同样不成立）。
4. **补全 D 臂边界裁定（7.5 只说了「不改变臂定义与父层题面」）。** 引 t18 §3 原文：
   > a（「必须前台运行」）与 b（PreToolUse 钩子）**都算 D 臂内部实现细节，不是新的实验变量**：臂定义
   > （A vs D = `--delegate=policy`）与父层装配完全未变，钩子只挂在 `_scout_options`（driver 会话）上，
   > A/B/C 臂与 D 臂父 agent 都不受影响；它把 P1 的命题「派发由 driver 决定、模型手里没有 spawn 通道」
   > 从提示词约定升级为权限层强制。

   AST 逐函数对比：`build_task` / `sandbox_spec` / `run_agent` / `summarize_subagent` 与批次之前**逐字相同**
   （`coding_agent.py` 的非注释差异行 = 0）。但它们是 **D-v2 → D-v3 的形态差异**，故 7.4 的三项跨形态不可比声明照旧适用。
5. **补全 `policy_scout_cost_usd` 的口径措辞（7.4/7.6 用了这个数，没写它含什么）。** 引 t18 §4 原文：
   > **父层口径（跨批可比，H2 主指标）**：`input_uncached` / `cache_read` / `cache_creation`（中位数 + 极差）。
   > 措辞：「父层会话未缓存输入（口径 = `model_usage` 各模型 `inputTokens` 合计）」。
   > **端到端口径（仅 D-v3 有）**：`端到端 = 父层 cost_usd + policy_scout_cost_usd`。措辞必须写：
   > 「`policy_scout_cost_usd` = 该次运行所有 driver 会话的 `ResultMessage` 成本合计，**含包装模型自己的回合
   > 与被拒后的重试**，不等于严格意义上的 scout 成本；scout 占比 = `policy_scout_cost_usd` / 端到端」。

   并逐轮附 wrapper 计数，`effective>0` / `background_spawns>0` / `extra_task_calls>0` 必须标数据质量 flag。
6. **补全流程背景的两个细节（7.0 已写偏离，这里补齐口径）。** ① 插件自动质量闭环触顶的具体上限是
   **3 轮 review**（t18 之后不再生成第 4 轮修复）。② 7.6 写「本批 `wrapper_mcp` 合计 1 次（被拦）」，逐轮复算为
   D-1 `0/0/0`、D-2 `1/1/0`、D-3 `0/0/0`、D-4 `0/0/0`（= 尝试 / 被拒 / **生效**）⇒ 那 1 次尝试发生在 D-2 且被拒，
   **本批 effective 全 0**、`extra_task=0`、`background_spawns=0`（7.6 与 7.4 的结论不变，只是补齐逐轮出处）。
7. **数据质量与已知限制（原样保留，不因为它们不好看就略去）**：
   - H4(b)、H5(b) 两条**不可测**（0 refuted / 0 截断 ⇒ **无对象**，不是「通过」）；
   - D-v2 的端到端成本**不可测**（当批未记 scout 用量），不得估算替代；
   - 本批 scout 侧用量来自 driver 会话的 `ResultMessage`，其「scout 独立 cache 拆分 / 独立费用」SDK 不给
     （`TaskUsage` 只有 3 个整数）——这一限制同时写在 `policy_scout_usage.jsonl` 的 `scope` / `limits` 字段里；
   - 泛化不成立：n=4、单靶场（more-itertools-hard）、单模型（`deepseek-flash[1m]`）、单题面（§6.1/6.2/6.7）。
8. **本批的原始证据**（均留盘、**不入 git**，按 `.gitignore` 纪律）：`subagent-ab-v2b-*`（8 份 agent JSON + 8 份 console.log
   + runs.jsonl + manifest）、12 份 `scout-subagent-ab-v2b-D-*-*.json`、`policy_dispatch.jsonl`、`policy_scout_usage.jsonl`、
   `usage.jsonl`、`code-tools-server.log`；旧批 `subagent-ab-v2-*` 与 `53428d8` 版产物一并保留作对照（未覆盖、未编辑）。
