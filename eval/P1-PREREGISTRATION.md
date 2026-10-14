# 第三阶段 P1 预注册：三臂对比（仓库地图检索 / driver 强制派发子代理）

> **本文件的地位**：这是 P1 全部数字的解释基础。它在**第一条真实臂跑起来之前**提交；
> **§1–§9 一经提交不得事后改写**。跑完之后只允许在文末**追加**「结果节」（§10+），
> 且追加节必须引用它对应的规则节编号。任何对 §1–§9 的修改都意味着**重新做一次预注册**，
> 旧数据全部作废、只能作为「探索性观察」另列一节。
>
> - 仓库：`E:\aicoding\mini-claude-code`（AgentTeams 工作区在仓库之外 ⇒ 本文所有路径写绝对路径或项目根相对 POSIX 路径）
> - 本机解释器（唯一）：`D:\Anaconda3\envs\aicoding\python.exe`，pytest `9.1.1`
>   （裸 `python` 是 WindowsApps 的 0 字节商店占位符，exit 9009，**不作为环境证据**）
> - 控制台代码页 GBK(936) ⇒ 本文与后续所有 UTF-8 产物一律用 Read/Write/Edit 工具读写，不用 shell 重定向
> - 提交方式：`.git/COMMIT_MSG_*.txt` + `git -c i18n.commitEncoding=utf-8 commit -q -F <文件>`；绝不 `git add -A`
> - 任务边界：**本次提交只新增本文档，不改任何代码**（多臂 harness、臂→CLI 映射、跑批调度都是后续任务）。
> - **P0 冻结文本未被改动**：`eval/PREREGISTRATION.md` 自 `27adbdd` 起一字未改，本文只引用它的节号，不复述、不修订它的规则。

---

## §0 P1 要回答的问题、术语表、与 P0 的关系

### 0.1 P1 要回答的问题（一句话 + 三个可判的子问题）

**在 P0 的 12 条冻结实例上，给基线 agent 加上「仓库地图检索」或「driver 强制派发子代理」之后，解出率、成本、耗时各变成什么样，以及失败案例分别死在哪一步。**

| 子问题 | 由哪个指标回答 | 判不了的情形 |
|---|---|---|
| Q1 解出率怎么变 | §2.1 resolution rate（主指标），按臂给出并做实例内配对比较 | 该实例三臂里有任一臂被判 `infra_error` / `timeout` / `harness_bug` ⇒ 该实例**退出配对集**（§4.3），不参与 Q1 的方向判定 |
| Q2 成本怎么变 | §2.3 三条 token 口径 + §2.4 两条成本口径 | 三口径里缺任一 ⇒ 该次运行的 token 数字整体作废（沿用 P0 §10.1）；可以只报 resolved |
| Q3 耗时怎么变 | §2.5 过程指标里的墙钟 | 被 §4.4 的超时上限 kill 的运行**不计入耗时统计**（截断值不是耗时样本），单列 timeout 计数 |
| Q4 失败死在哪一步 | §6 六类归因 | 见 §6.7「不可归因」桶 —— 允许存在，但必须显式列出 |

**本文档不能回答的**（§8 逐条声明）：模型间比较、官方 leaderboard 对齐、跨语言/跨规模外推、任何「改进带来 X 个点提升」的因果结论。

### 0.2 术语表（后文所有判据共用，避免歧义）

| 术语 | 定义 |
|---|---|
| **实例（instance）** | P0 冻结的一条待评测任务 = `eval/instances/pilot-12.jsonl` 里 `set_membership == "pilot-12"` 的那一行 JSON（12 条），字段语义见 P0 §1 |
| **臂（arm）** | 一次运行所施加的**开关组合**。P1 只有三臂 A / B / C，定义写死于 §1.1 |
| **唯一变量** | 某一臂相对臂 A **只多出来的那一个 CLI 开关**；其余参数逐字相同（§1.3 的机器校验） |
| **一次运行（run）** | 「一个实例 × 一个臂」的一次完整端到端尝试：建工作区 → 跑 agent → harness 采集补丁 → 独立 pytest 判定 |
| **三元组（triple）** | 同一实例上的 A/B/C 三次运行构成的配对单元。P1 的比较单位是三元组，不是单次运行 |
| **实例内交错** | 调度顺序为「同一实例连跑 A→B→C，再进下一个实例」（§3.2），目的是消掉时间漂移与端点抖动 |
| **verdict_class** | P0 §5.4 的六分类：`agent_result` / `empty_patch` / `patch_conflict` / `infra_error` / `timeout` / `harness_bug`。P1 **不改它的定义、不加新类** |
| **resolved** | P0 §5.1 的定义（独立 pytest：F2P 全过 ∧ P2P 全过 ∧ `verdict_class == agent_result`）。P1 **不改** |
| **分母** | P0 §5.5 的口径：`agent_result + empty_patch + patch_conflict` 三类之和。`infra_error` / `timeout` / `harness_bug` **不进分母** |
| **父层（parent-side）** | 主 agent 会话自身的 usage 与 `cost_usd`（P0 §10.2） |
| **端到端（end-to-end）** | 父层 + 该次运行派生的所有子进程/scout 会话 usage 之和；**scout 侧必须单列**（§2.4） |
| **scout** | ④ 层 / driver 派发产生的只读侦察子会话（`agent/policy_dispatch.py`）。只有臂 C 会产生 scout |
| **归因类别** | §6 的六类失败标签（定位失败 / 补丁错 / 测试没跑起来 / 超时 / 撞回合上限 / infra）。它是**对 not_resolved 的进一步分层**，不改变 resolved 判定本身 |
| **批次（batch）** | P1 的一整轮：12 实例 × 3 臂 = 36 次运行（§5.6 的上限之下可能跑不满，见 §7） |
| **run_id** | 批次目录标识，形如 `p1-<UTC 时间戳>-<臂字母>`；每臂产物隔离的主键（§5.5） |

### 0.3 与 P0 的关系（**这一节是本文档的地基**）

**P1 全盘沿用 P0 的下列内容，一条都不改、一条都不重新论证：**

| P0 的东西 | 出处 | P1 怎么用 | 是否改动 |
|---|---|---|---|
| 实例 schema 与九个官方字段 + 扩展字段 | P0 §1 | 直接读 `eval/instances/pilot-12.jsonl` 里 `set_membership=="pilot-12"` 的 12 行 | ❌ 不改 |
| 仓库纳入 R1–R8 / 排除标准 / 兜底 | P0 §2 | 已筛完，P1 不再纳入新仓库 | ❌ 不改 |
| 候选提交条件、文件分类、`problem_statement` 来源 | P0 §3 | 题面 = 实例的 `problem_statement` 原文，经 `--problem-file` 传给 agent | ❌ 不改 |
| F2P / P2P 推导与匹配严格度、flaky 规则 | P0 §4 | 判定集合就是实例里冻结的 `FAIL_TO_PASS` / `PASS_TO_PASS` | ❌ 不改 |
| **`resolved` 判据与六分类 verdict** | P0 §5 | **逐字沿用**；P1 不加新分类、不改分母口径 | ❌ 不改 |
| 黄金三向自校验 | P0 §6 | 已在 P0 完成（t9 复核 12/12）；P1 开跑前只做 §5.7 的「接线自检」，**不重跑三向** | ❌ 不改 |
| 规模与抽样规则（12 条 / 4 仓库 / reserve 顶替） | P0 §7 | 实例集就是那 12 条；`excluded_infra` 时按 P0 §7.3 从 reserve **按顺序**顶替 | ❌ 不改 |
| 隔离与并发方案（工作区 / venv / ProcessPool / 超时数值 / 断点续跑） | P0 §8 | 逐字沿用；venv 按 (实例, run) 隔离是 t8 的**加严**，P1 保持 | ❌ 不改 |
| 不变量（清洁检查 / 只认独立 pytest / 仓外三目录 / 可追溯 / 冻结性） | P0 §9 | 逐字沿用，P1 在 §5 补「臂」这一层的增量不变量 | ❌ 不改 |
| 三 token 口径 + 两成本口径 + 非外推声明 | P0 §10 | 逐字沿用，P1 在 §2/§8 补「scout 侧单列」的落地细节 | ❌ 不改 |

**P1 只增加一层变量：臂（arm）。** 除「臂开关」以外，三臂之间的任何东西都必须相同；若发现某处不同，按 §5.2 的机器校验拦下来而不是"记个备注"。

**P0 的两处既有裁定，P1 必须继承（不得在 P1 文档里翻案）：**

1. **§4.2 的按目的细化**（t9 审查 §2 裁定）：F2P 允许「基线上不存在的新增测试」，实现按 P0 §4.2 的目的句而非公式字面读法。⇒ P1 的 12 条实例里 **10 条依赖该细化**，本报告在任何地方提到 F2P 时必须连带这条声明，不得写成「预注册本来就这么说」。
2. **3/12 条 tabulate 实例的第 3 向少一向判别力**（t9 审查 §3 裁定）：`wrong_patch` 净效果 = 回到基线。⇒ harness 层「干净 apply 但改错 ⇒ unresolved」的判别力由其余 9 条证成；P1 引用判别力时沿用此边界。

**P0 的两处措辞更正，P1 文档遵守其正确表述：**

- `usage_log` 在不传 `trace` 时**既有字段的值与写盘路径不变，另新增一个 `usage_source` 键**（不写「逐字一致」）。
- harness 采集到的补丁正文**天然不含 CRLF**（因为 `env.sh()` 以 `text=True` 读子进程输出，universal newlines 把 CRLF 折成 LF）——**不是**「`git diff` 自己会归一化行尾」。真正的行尾防线是 `normalize_worktree_eol` / `normalize_eol_patch`。
  ⇒ 对 P1 的后果：`patch_bytes` 这个诊断量（§6.4）测的是归一化之后的体积，解读时要按这条来。

---

## §1 三臂定义与唯一变量

### 1.1 三臂的逐字定义（写死）

统一入口（三臂同一条命令骨架，仅「臂开关」一项不同）：

```
D:\Anaconda3\envs\aicoding\python.exe -X utf8 coding_agent.py
    --repo-root    E:\aicoding\eval-runs\<run_id>\<instance_id>\repo
    --problem-file E:\aicoding\eval-runs\<run_id>\<instance_id>\problem.txt
    --label        p1-<instance_id>-<arm>
    [臂开关]
```

| 臂 | 名称 | 臂开关（相对臂 A **只多这一个**） | 这一臂在测什么 |
|---|---|---|---|
| **A** | 现状 / baseline | **无**（只传 `--repo-root` / `--problem-file` / `--label`） | 第二阶段之后、不加任何新能力的默认行为：`repo_map` 默认详细度取生效默认 `summary`，无子代理通道，无 driver 派发 |
| **B** | 仓库地图检索 | **只加** `--repo-map-default symbols` | 把 `repo_map` 的默认详细度从 `summary` 换成 `symbols`（符号级全量地图），其它一切不变 |
| **C** | driver 强制派发子代理 | **只加** `--delegate=policy` | 由 driver 在**第一次模型回合之前**按基线失败聚类派 K 个只读 scout，把 ≤2000 字符索引注入题面；父层手里没有 spawn 通道。**K = 该实例 F2P 所在的测试文件数**（本批 12 条：8 条 K=1、3 条 K=2、1 条 K=3，合计 25 组） |

> ⚠ **C 臂的已知接线缺口（必须在跑批前由后续任务补上，本文不假装它不存在）**：
> ① `coding_agent.py` 现在有一条入口守卫 —— `--delegate=policy` **只允许 `--fixture more-itertools-hard`**，实例模式（`--repo-root`）会被直接拦下（exit 2）。这是第二阶段 D 臂钉死 K=3 分组判据时留下的约束，不是 P1 的设计意图。
> ② `policy_dispatch.group_failures()` 的分组键正则只认 `<Name>Tests::` 形状；实测 pilot-12 的 F2P node id **全部不含** `…Tests` 类名 ⇒ 现状会整批回落成单组 `OTHER`（K 恒为 1），C 臂就变成「一个 scout 打全仓」的另一套干预，而不是「按失败组分工」。
> ⇒ 实现多臂支持时必须做两件事：**放开实例模式** + **把分组键改成与 pilot-12 实际 node id 形状对齐的可验证规则**（本文钉死为 §1.2 的 G-K 判据）。
> 这两处改动属于「让 C 臂可跑」的必要接线，**不属于新增第二个变量**；改完必须重跑 §1.3 的 W1–W4 与 §5.7 自检，且 W1 的 argv diff 仍然只允许多出 `--delegate policy` 两个 token。
> 若最终无法在不引入第二个变量的前提下接通 ⇒ **C 臂如实报告为不可测**（§4.4），不允许拿"近似实现"的数字顶替。

### 1.2 C 臂的派发规模判据（G-K，写死）

| 项 | 内容 |
|---|---|
| **是什么** | 每次 C 臂运行的 scout 组数 K 必须等于「该实例 F2P 涉及的测试文件数」，从实例定义里算得，不由 agent 或人临时决定 |
| **怎么判** | `K_expected(i) = \|{ n.split('::')[0] for n in instance(i).FAIL_TO_PASS }\|`；落盘的 `policy_dispatch.jsonl` 里该 label 的不同 `group` 终态记录数必须 == `K_expected`，且 `index_chars ≤ 2000` |
| **证据要求** | `policy_dispatch.jsonl` 路径 + 每条 group 名 + `k` 字段 + `snapshot_head` / `worktrees_after_dispatch == 1`（P4 无残留） |
| **判不了** | 若某实例基线 pytest 输出里取不到任何 F2P 形状的失败行（agent 侧跑的是**未叠 test_patch 的基线**，F2P 缺陷未必可见）⇒ 记 `dispatch_grouping_unobservable`，该次运行标 `harness_bug`（治疗未按设计施加），**不得**改用「随便分 K 组」继续 |

> 这一条是本批最需要盯的实现风险：C 臂的分组依据来自**基线可见的失败**，而 P0 §4.1 写明缺陷是叠加 `test_patch` 之后才暴露的。
> 因此 §1.2 的最后一行不是免责声明而是**门槛**：接通方案必须先证明「在实例模式下，driver 确实能拿到可分组的失败信息」，证明不了就是 C 臂不可测，而不是"C 臂跑了但效果不好"。

**每条臂的身份必须由产物自证**（不靠人回忆）：每次运行的 console 与 trace 运行头都要出现下面这些行，缺一即该次运行标 `harness_bug`（§5.2 I-8）：

| 臂 | console 必须出现的自证 |
|---|---|
| A | `repo_map 默认详细度 = 'summary'  （来源：默认（未设置））` + `④ 子代理 = 关（现状）` + **不得**出现 `P1 驱动派发 = 开` |
| B | `repo_map 默认详细度 = 'symbols'  （来源：--repo-map-default）` + `④ 子代理 = 关（现状）` + **不得**出现 `P1 驱动派发 = 开` |
| C | `repo_map 默认详细度 = 'summary'  （来源：默认（未设置））` + `P1 驱动派发 = 开（--delegate=policy；scout 回合上限 24）` |

> ⚠ 臂 A 的 `repo_map` 来源必须是「默认（未设置）」而**不是**显式传 `--repo-map-default summary`：
> 显式传会让「A 与 B 只差一个开关」这句话变成真的，但也让 A 不再是**现状**（现状是不传）。
> 因此 A 臂**禁止**出现任何 `--repo-map-default` 参数；父进程环境里也不得预留 `AGENT_REPO_MAP_DEFAULT`
> （§5.4 I-6 的跑前检查包含这一条）。

### 1.3 唯一变量的机器校验（跑批前必须过，不过即停）

「每臂与 A 的差异只有一个开关」不能只写在纸上，必须能被程序查出来。以下四条构成**臂接线自检**（后续任务实现，本文钉死判据）：

| # | 检查项 | 判据（可执行） | 不过的后果 |
|---|---|---|---|
| W1 | **argv 差异** | 三臂的实际命令行逐 token diff 相对 A 只多出 `[臂开关]` 那 2 个 token（B 多 `--repo-map-default symbols`；C 多 `--delegate policy`）；不允许少 token、不允许其它 token 变化 | 停批 |
| W2 | **options 装配差异** | 用 `coding_agent.sandbox_spec()` 分别算三臂的工具集：A 与 B **必须完全相等**（`mcp_tools` / `builtin_tools` / `allowed_tools` / `disallowed_tools` / `agents` 逐项比）；C 相对 A **只允许多出** `mcp__code-tools__read_findings` 一项（这是 `--delegate=policy` 的实现自带物，属于该开关的语义，不算第二个变量），且 `builtin_tools` 两边都为 `[]`（父层不放 `Task`） | 停批 |
| W3 | **题面差异** | 三臂喂给 agent 的题面：A 与 B **逐字节相同**（同一个 `problem.txt` 走同一个 `build_instance_task`）；C 相对 A 只多出 `policy_task_suffix(...)` 那段（driver 索引，≤2000 字符 + 固定说明文字）。把三段题面各自的 SHA-256 落盘 | 停批 |
| W4 | **MCP server env 差异** | 传给 stdio server 的 env：A 与 B 只有 `AGENT_REPO_MAP_DEFAULT` 一个键的值不同（`summary` vs `symbols`）；C 相对 A 只多出 `AGENT_FINDINGS_DIR` 一个键（同样属于 `--delegate` 的自带语义）。`AGENT_REPO_ROOT` 三臂各自指向自己的实例工作区，值必须等于该 run 的工作区 realpath | 停批 |

> **为什么允许 C 多出 `read_findings` 和 findings 目录**：这两个东西是「driver 强制派发」这个干预的**组成部分**（没有按需读取通道，索引就只能整份塞进上下文，测的就不是同一个干预）。
> 它们的边界必须写清并在结果节复述：C 臂相对 A 的差别是**「driver 决定派发 + 有界索引 + 按需取回」这一整套机制**，而不是「多了一个工具调用机会」。
> 若有人想把 C 拆成「只派发不给 read_findings」，那是第四臂 ⇒ **必须重新做一次预注册**，不在本文授权范围内。

### 1.3 共享常量清单（三臂一致，逐项给值与来源）
| # | 常量 | 取值 | 来源 / 依据 | 什么情况下判不了 |
|---|---|---|---|---|
| K1 | 模型 | `.env` 里的 `ANTHROPIC_MODEL`（当前实测 `deepseek-flash[1m]`） | 项目根 `.env` 是唯一配置来源；`isolate_from_parent_env()` 摘掉父进程继承来的 `ANTHROPIC_*` | `.env` 缺失 ⇒ `coding_agent.py` 直接报错退出（不回落）。⇒ 该次运行 `infra_error`，**不许手工填值继续** |
| K2 | 端点 | `.env` 里的 `ANTHROPIC_BASE_URL` 等，**不随成员线路变** | 同上 | 同 K1 |
| K3 | CLI 路径 | `coding_agent.CLI_PATH`（Claude Code 真身 `bin\claude.exe`，非任何垫片） | 工作区 `CLAUDE.md`：`nvmd\bin\claude.exe` 是 3.99 MB 垫片，指错就废 | 启动即失败 ⇒ `infra_error` |
| K4 | **`--max-turns`** | **三臂一律不传** ⇒ 走实例模式默认 `INSTANCE_MAX_TURNS = 120` | **理由（写死）**：① D8 已证明 40 会截断真实仓库 —— t5 冒烟在 `pallets-click__4fd2fea` 上撞了 40 回合上限，而那时 agent 已经改对了 `core.py`，只是没机会收尾；② 回合上限是**常量而不是变量**，P1 要测的是「检索粒度」和「谁来决定派发」，把上限做成臂内差异会同时污染两个假设（上限低的臂必然堆满「撞回合上限」归因桶，那个数就没有意义）。⇒ 三臂都不许出现 `--max-turns`；console 必须自证 `回合上限 = 120  （来源：实例模式默认(120)）` | 若某臂出现显式 `--max-turns` ⇒ W1 不过 ⇒ 停批 |
| K5 | 题面 | 实例的 `problem_statement` 原文（P0 §3.4：提交信息，不代笔、不润色、不补 issue 链接） | 每个实例写一份 `problem.txt`，三臂**共用同一份文件**（SHA-256 相同） | 文件缺失/为空 ⇒ 该实例三臂全部作废（不是 agent 失败） |
| K6 | 工具集 | 沙箱 MCP 工具全集 + `apply_patch`（`--no-write` **不启用**），内置工具默认为空 | `sandbox_spec(allow_write=True, enable_subagent=False, policy_mode=…)` | W2 不一致 ⇒ 停批 |
| K7 | 靶场与实例定义 | `eval/instances/pilot-12.jsonl` 的 12 条 pilot 实例 + 各自 `base_commit`；`schema_sha256` 每次读取前重算 | P0 §1 / §9.5 冻结 | 哈希不匹配 ⇒ 拒绝使用 ⇒ `harness_bug`（P0 §1.3） |
| K8 | 判定命令模板 | 实例的 `test_cmd_template`（含 `-p no:cacheprovider -q --timeout=120 -c _eval_pytest.ini --json-report`） | P0 §1.3 / §5 | 模板与实际拼装不一致 ⇒ `harness_bug` |
| K9 | 判定超时 | 单实例判定 pytest 墙钟 **600 s**（内含单测试 `--timeout=120`） | P0 §8.4 | — |
| K10 | agent 阶段墙钟 | 单实例 agent 阶段 **900 s** | P0 §8.4 | 超限 ⇒ `timeout`（先查超时标记再看退出码，P0 §8.4） |
| K11 | 单实例总墙钟 | **2400 s**（含建 venv） | P0 §8.4 | 超限即 kill 并标 `timeout` |
| K12 | 并发上限 | **3**（`max_workers=3`，ProcessPoolExecutor） | P0 §8.4：>3 会把 pytest 计时拖长造成系统性 timeout 偏置 | 实际观测 >3 ⇒ `harness_bug`（§5.4 I-5） |
| K13 | venv | 每 **(实例, run)** 一个，命名含 instance_id + run 哈希；import 行式 `.pth` 挂载工作区 | P0 §8.2 + t8 的 D1 加严 | 挂载自检失败 ⇒ `infra_error`（不自愈、不改判） |
| K14 | 上游缓存 | `E:\aicoding\eval-cache\`（只读） | P0 §9.3 | 缓存被写入 ⇒ `harness_bug` |
| K15 | 运行目录 | `E:\aicoding\eval-runs\` | P0 §9.3 | — |
| K16 | git 调用 | 一律钉 `-c core.autocrlf=false -c core.safecrlf=false` | `eval/lib` 的 `GIT_BASE`（本机全局 `autocrlf=true`） | 绕过它手敲 git ⇒ 该次结果不作数 |
| K17 | 解释器编码 | 子进程 `PYTHONIOENCODING=utf-8` + `-X utf8` | P0 §8.3 + GBK 控制台既有坑 | — |
| K18 | `--no-write` | **不启用**（三臂都给写权限，否则 B/C 的补丁根本产不出来，比的就不是同一件事） | 本文 | 出现即 W1 不过 |
| K19 | `--subagent` / `--subagent-force` / `--fixture` / `--scout-turn-cap` | **三臂一律不传**。`--subagent*` 是「模型自愿派发」的另一套干预（与 `--delegate` 互斥，代码里硬拦）；`--fixture` 与实例入口互斥；`--scout-turn-cap` 只用于机制验证的截断场景，正式跑批用缺省 `SCOUT_MAX_TURNS = 24` | `coding_agent.py` 的入口守卫 | 出现即 W1 不过 |
| K20 | 重复次数 | **每实例每臂 1 次**（不做 k 次重复） | §3.1 的检验力讨论：36 次运行是本批的全部预算，重复会挤掉实例覆盖 | — |

> **常量变更的唯一合法路径**：任何一条 K 需要改动 ⇒ 停止跑批 ⇒ 重新做一次预注册（旧数据全部作废为「探索性观察」）。
> 不允许「这批先这样、下批再统一」。

---

## §2 指标（每条都写口径名，禁止只给一个「input tokens」）

### 2.1 主指标：resolution rate

| 项 | 定义 |
|---|---|
| **是什么** | `resolved_rate(arm) = resolved 数 / 分母`，其中分子 = 该臂 12 条里判定为 `resolved` 的条数；分母按 P0 §5.5 = `agent_result + empty_patch + patch_conflict` 三类之和。**`infra_error` / `timeout` / `harness_bug` 不进分母**（P0 §5.4）。 |
| **怎么判** | 逐字沿用 P0 §5.1：在「`base_commit` + `test_patch`」的工作区上应用 harness 采集的候选补丁后，由 **harness 自己启动的独立 pytest** 报告 `FAIL_TO_PASS` 每条 node id == `passed` **且** `PASS_TO_PASS` 每条 == `passed`，且 `verdict_class == "agent_result"`。四个条件缺一即 not_resolved。`skipped` / `xfail` / `xpass` 一律不算 passed（P0 §4.4）。 |
| **报告形态** | 每臂给 `k / n`（例：`A: 4/12`）+ Wilson 95% 区间（P0 §10.3 第 5 条：n=12 时区间宽度约 ±25–29 个百分点）；同时给分母构成三类的逐条计数与 `infra_error` / `timeout` / `excluded_infra` 计数。 |
| **判不了** | ① 该实例三臂里有任一臂落在分母之外 ⇒ 该实例退出配对集（§4.3），**只进单臂绝对率、不进方向判定**；② `schema_sha256` 重算不匹配 ⇒ 拒绝使用（P0 §1.3）；③ 缺原始 pytest 归档输出 ⇒ 该条数字**不写进报告**（P0 §9.4）。 |

**必须同时报的第二行**：`resolved@pair`（只在三元组完整可用的实例上算的每臂解出数）与 `resolved@all`（该臂全部分母上的解出数）。两个数不一致时以 `resolved@pair` 作比较、以 `resolved@all` 作绝对水平，**不得混用**。

### 2.2 agent 自述的地位（零）

判定链路**不接受**任何 agent 文本作为输入：`judge()` 的入参只有 `(f2p, p2p, results)`；候选补丁正文由 harness 用 `git diff` 从工作区采集。
既有实证（P0 t9 审查 §6）：自称修好、能干净 apply 的补丁被判 unresolved（F2P 0/5）；t5 冒烟里 agent 自评「未成功」而独立判定 `resolved=True`。
⇒ P1 沿用同一条纪律：**归因（§6）也不得采信 agent 自述**，只用磁盘状态 + 独立 pytest + trace 里的工具调用记录。

### 2.3 token 三口径（口径名必须出现在每一个引用处）

沿用 P0 §10.1，字段来源是 `model_usage` 系（**不是** trace 逐回合 Σ —— 后者属另一口径，第二阶段实测出现过 ≈2× 关系，仅作诊断，不进任何对比表格）：

| 口径 | 字段名 | 来源 | 在 P1 里的角色 |
|---|---|---|---|
| 未缓存输入 | **`input_uncached`** | `model_usage[*].inputTokens` 合计 | **token 主指标**（与 P0、第二阶段一致） |
| 缓存读 | **`cache_read`** | `model_usage[*].cacheReadInputTokens` 合计 | 单列。用于解释成本结构；**禁止**把它当「真实 prompt 规模」单独引用 |
| 缓存写 | **`cache_creation`** | `model_usage[*].cacheCreationInputTokens` 合计 | 单列。当前端点实测恒为 0，仍必须逐次落盘（缺字段 ≠ 0） |

- 每次运行三口径必须齐备；缺任一 ⇒ 该次运行的 token 数字整体作废（可以只报 resolved）。
- 允许另报 `output_tokens`，但必须标为**第四口径**，不参与三口径的任何一致性检查。
- ⚠ 本端点的 `output_tokens` 曾整轮报 0（t5 冒烟），所以**不得**用 output 参与任何跨臂比较。

### 2.4 两条成本口径（scout 侧单列，不许只给合并数）

沿用 P0 §10.2，落到 P1 的具体装配：

| 口径 | 定义 | 数据源（P1 钉死） |
|---|---|---|
| **父层（parent-side）** | 主 agent 会话自身的 usage 与 `total_cost_usd` | `servers/experiments/out/usage.jsonl` 里 `script == "coding_agent.py"` 且 `label == p1-<iid>-<arm>` 的那一行（D6 修复后异常路径也能取到数；若仍为 null ⇒ 该次 token 作废并记入 §7 的偏离） |
| **scout / 子进程侧（单列）** | 该次运行派生的所有 scout 会话 usage 与 `total_cost_usd` 之和，**以及逐条明细** | `usage.jsonl` 里 `key_source == "scout-session"` 且 label 前缀匹配的行 + `policy_dispatch.jsonl` 的 `scout_accounting` / `attempts`（含续派产生的额外尝试） |
| **端到端（end-to-end）** | 父层 + scout 侧 | 计算值，必须在同一张表里把上面两列一起给出 |

**硬规则（写死于本文，违反即视为绕开判据）：**

1. **禁止**把父层的下降解读为端到端的节省。C 臂天然会把一部分开销搬到 scout 侧；只有端到端口径才能回答「总共贵了还是便宜了」。
2. C 臂的每一次运行必须报三个数：`parent.input_uncached`、`scout.input_uncached`、`e2e.input_uncached`。**缺 scout 列的 C 臂结果不得发布。**
3. A / B 臂没有 scout ⇒ scout 侧显式记 `0`（不是 null、不是缺字段），并在表里注明「该臂无子代理通道」。
4. 跨批次只比**方向**，不比绝对值（量具/模型/靶场任一变化即不可比，P0 §10.2）。

### 2.5 过程指标

| 指标 | 口径名 | 怎么取 | 用途 / 边界 |
|---|---|---|---|
| 回合数 | `num_turns` | `usage.jsonl` 的 `num_turns`（= `model_usage` 系的 result 层）；trace 逐回合计数作交叉核对 | 与 K4 的 120 上限对照，是 §6.5「撞回合上限」归因的输入之一 |
| 工具调用分布 | `tool_calls_by_name` | **从 trace 的 `tool_use` 事件逐条计数**（`read_file` / `search_code` / `list_files` / `repo_map` / `apply_patch` / `run_tests` / `read_findings` …） | ⚠ 已知缺陷 D6：`metrics.tool_calls_total` 曾报 0 而实测 77（t9 审查 §9 独立复现）。⇒ **P1 一律用 trace 计数**，`metrics.tool_calls_total` 只作诊断，不进任何表格。B 臂的核心过程假设就在 `repo_map` 次数与 `read_file` 次数的此消彼长上 |
| 耗时 | `wall_s.agent_phase` / `wall_s.judge_phase` / `wall_s.total` | 三段分别计时：agent 阶段墙钟、判定 pytest 墙钟、含建 venv 的总墙钟 | 被 kill 的超时运行**不进耗时统计**（截断值不是样本），单列 timeout 计数（Q3 的判不了条款） |
| infra 率 | `infra_rate = (infra_error + timeout) / 计划运行数` | 按臂分别算，也按批次算 | ⚠ 这是**环境/程序质量指标，不是 agent 能力指标**（P0 §10.3 第 2 条：无 Docker ⇒ 环境类失败落在我们头上）。任一批 `infra_rate > 1/3` ⇒ 停批排查，不得继续发布 resolved |
| 试图改测试率 | `touched_tests_rate` | verdict 行的 `touched_tests` 布尔（P0 §5.3 剥离证据） | 单列一列，不是判定输入 |
| 补丁体积 | `patch_bytes_normalized` | 行尾归一化之后、剥离测试侧之前的补丁字节数 | ⚠ 按 §0.3 的措辞更正解读：采集正文天然无 CRLF，所以这个量测的是归一化后的语义体积；不要用它论证「行尾防线有效」 |

---

## §3 判据与检验力（n=12 能说什么、不能说什么）

### 3.1 样本量与设计

- **n = 12 条实例 × 3 臂 = 36 次运行**，每实例每臂 **1 次**（K20）。
- 比较单位是**实例内配对的三元组**，不是「两组独立样本」。配对设计的自由度来自「同一实例、同一 base、同一题面」，因此每个实例最多贡献 1 个方向观测。
- **检验力声明（原文照抄进最终报告）**：
  1. n=12 时单臂 resolution rate 的 Wilson 95% 区间宽度约 **±25–29 个百分点**（p≈0.5）；**任何小于 ~15 个百分点的臂间差异不得被解读为效应**。
  2. 本设计**没有重复**（每格 1 次），因此无法区分「臂的效应」与「该实例在该次运行里的随机波动」——这是 P1 最大的方法学局限，必须在结论前写明。
  3. 二项配对检验（McNemar）在 12 对上的最小可达显著性水平约为 `2 × 0.5^12`（只有全部不一致对才够小），只要有一对以上不一致就远达不到常规阈值。⇒ **算了也没意义，所以不算，本批一律不报任何推断统计量**。
  4. 因此 P1 的定位是**探索性对比 + 失败案例归因**，不是假设检验。它只能产出「值得继续做的方向」，不能产出「改进带来 X 个点提升」。

### 3.2 调度顺序：实例内三臂交错（写死）

```
for instance in <pilot-12 顺序（见 §3.3）:
    for arm in [A, B, C]:          # 同一实例连跑三臂，中间不插入别的实例
        run(instance, arm)
    next instance
并发槽位 = 3（K12）：一个槽跑一个「实例-臂」运行，
       但调度器必须保证同一实例的三个运行**在时间上相邻**（连续占用槽位），
       不允许出现「A 在第 1 小时、C 在第 6 小时」这种跨漂移配对。
```

- **为什么交错**：端点延迟、缓存命中率、本机磁盘状态都会随时间漂移（P0 §10.2 已声明缓存命中率随批次剧烈波动）。若按「先跑完 12 条 A，再跑 12 条 B」的顺序，臂效应会与时间效应完全混淆，事后无法分开。交错把漂移变成**三臂共同暴露**的量。
- **机器校验**：每个三元组的三次运行的 `started_at` 极差必须 ≤ **2 小时**；超过 ⇒ 该三元组标注「跨漂移配对」，仍可进单臂绝对率，但**不进方向一致性统计**（§4.3）。
- **不得中途换序**：一旦开始跑批，实例顺序与臂顺序就是冻结的；发现排错 ⇒ 停批、作废已完成部分、重新预注册。

### 3.3 实例顺序（写死，在看到任何结果之前定）

采用 `eval/instances/pilot-12.jsonl` 中 pilot 成员的**文件行序**（即构造器按 P0 §7.2 的 `(fix_commit_date 升序, instance_id 字典序)` 排序后落盘的顺序）。本次核对该行序为：

```
1  astanin-python-tabulate__5373ebf
2  astanin-python-tabulate__87a9a4e
3  astanin-python-tabulate__9dedd45
4  more-itertools-more-itertools__1b7f033
5  more-itertools-more-itertools__ccf96ac
6  more-itertools-more-itertools__f8ccab2
7  pallets-click__1b0e19f
8  pallets-click__4fd2fea
9  pallets-click__ac6a2ac
10 pytest-dev-pluggy__20d8143
11 pytest-dev-pluggy__cbf58d5
12 pytest-dev-pluggy__dd20a85
```

- **机械理由**：直接沿用冻结文件的行序，不做任何基于预期难度、F2P 条数、仓库归属的重排。
- **禁止事项**（继承 P0 §7.4）：禁止在看到任何臂的结果后增删/替换实例或调整顺序；禁止因某条没解出来而回溯改它的 F2P/P2P/题面。

### 3.4 报什么：区间与方向（不报 p 值）

| 报告项 | 具体形式 |
|---|---|
| 每臂绝对水平 | `k/n` + Wilson 95% 区间（区间重叠与否都要如实写出） |
| 臂间差 | 配对差值的**方向一致性**：`#(B>A) / #(B<A) / #(tie)`（按实例逐个比 resolved 的二值结果；成本类比 `input_uncached` 与端到端成本的符号） |
| 效应量 | 只给**百分点差 + 区间**（例：`B − A = +8.3 pp，[−16.7, +33.3]`），并注明「区间宽到足以容纳 0，本样本量不支持任何方向结论时也必须照此写出」 |
| 成本/耗时 | 每臂给中位数与极差（min–max），沿用第二阶段的**极差区间规则**：一方极大值 ≤ 另一方极小值 ⇒ 记「方向信号（区间不重叠）」；区间重叠 ⇒ 记「不确定」，**既不算支持也不算证伪** |
| ❌ **禁止报 p 值** | 全文（含附录与图表标题）不得出现任何推断统计量：p 值、显著性标记、置信度除外的检验结果一律不写。理由见 §3.1 第 3 条 |

### 3.5 治疗施加门槛（先看有没有真的施加，再看有没有效果）

借鉴第二阶段 B′ 臂的教训（**空治疗也能让「区间不重叠」成立**）：宣布任何臂间信号之前，必须先证明该臂的干预确实施加了。以下三条是**前置门槛**，不过则该格比较判为「不可测」，**禁止**用区间不重叠宣布任何信号。

| 门槛 | 判据 | 不过的处理 |
|---|---|---|
| G-B | B 臂每次运行的 trace 里 `repo_map` 调用**至少发生 1 次**，且 MCP server 日志/运行头自证生效值为 `symbols` | 该次运行标 `harness_bug`（干预未施加）；若 ≥1/3 的 B 臂运行不过 ⇒ **B 臂整体判不可测** |
| G-C1 | C 臂每次运行 console 有 `P1 驱动派发 = 开`，且 `policy_dispatch.jsonl` 里该 label 有 ≥1 条终态记录 | 同上：C 臂该次标 `harness_bug` |
| G-C2 | C 臂的题面 SHA-256 ≠ A 臂题面 SHA-256，且差出的部分确为 `policy_task_suffix`（W3 的证据） | 说明索引没进上下文 ⇒ C 臂干预未施加 ⇒ C 臂整体判不可测 |

> ⚠ 这三条门槛**只判断「有没有施加」，不判断「有没有用」**。它们不构成对 §1 唯一变量的修订。

---

## §4 比较规则（配对、缺失、顶替）

### 4.1 逐实例的三臂对照表（必须逐条给，不给汇总糊过去）

最终报告必须包含一张 12 行 × 3 臂的表，每格给：`verdict_class` / `resolved` / `input_uncached` / `e2e_input_uncached` / `wall_s.agent_phase` / `num_turns` / 归因类别（§6）。
**这张表是本批数字的最小可复核单元**：任何汇总数都必须能从它复算出来。

### 4.2 方向一致性的定义

对某个量 `X`（`resolved` 的二值、或 `input_uncached`、或端到端成本、或墙钟）：

```
sign(X, arm1 vs arm2 | instance i) =
    +1  若 X_i(arm1) > X_i(arm2)
     0  若两者相等，或两者里任一不可比（缺失/作废）
    -1  若 X_i(arm1) < X_i(arm2)
```

报告格式：`B vs A：+a / 0 b / −c`（a+b+c=12，不可比的格子计入 0 并单列原因）。
**不得**只报 a 和 c 而隐藏 b；**不得**把「不可比」计成平局而不说明。

### 4.3 缺失与退出配对集

| 情形 | 处理 |
|---|---|
| 三元组里某一臂 `infra_error` / `timeout` | 该臂不进该实例的分母（P0 §5.4）；该**三元组退出方向一致性统计**（§4.2 计 0 并列原因），但仍进各臂的绝对率 |
| 某一臂 `harness_bug` | 按 P0 §5.4：该批次整体作废重跑。P1 的例外授权：**仅允许**在 §5.7 接线自检通过后、正式跑批开始前的一次「首实例三臂演练」中出现 harness_bug 时修复重跑；跑批开始后再出现 ⇒ 停批，不自愈 |
| 三臂齐全但某臂 token 三口径不齐 | 该臂 token 作废，resolved 仍可比（口径分离） |
| reserve 顶替 | 仅在正式实例被判 `excluded_infra`（P0 §5.4：同实例最多 2 次 infra 重试仍失败）时按 P0 §7.3 **顺序**顶替，不许挑；顶替后该实例的三臂必须**全部重跑**（不允许只补跑缺失的臂） |

### 4.4 判不了的诚实出口

若某臂的有效样本 < 12，报告必须写成「**有效样本 N 条**」并明示偏离（对齐 P0 §7.5③），
**禁止**为了凑满 36 次而放宽任何判据（不加开关、不改超时、不换实例、不手工补 token）。

---
## §5 不变量（P0 §9 全部继续生效，本节只加「臂」这一层）

### 5.1 P0 九条不变量的继承清单

| P0 节 | 内容 | P1 状态 |
|---|---|---|
| §9.1 | 每实例工作区跑前/跑后 `git status --porcelain` 检查（跑前必空；跑后按「未跟踪且未被忽略」判垃圾；agent 造成的已跟踪修改是候选补丁本身，不算垃圾） | ✅ 逐字生效 |
| §9.2 | 判定唯一来源是 harness 启动的独立 pytest；无 agent 参与的 gold 判定重复 3 次结论稳定 | ✅ 逐字生效（已在 P0 完成，P1 不重跑） |
| §9.3 | 仓外三目录 `eval-cache` / `eval-runs` / `eval-venvs`；`E:\aicoding\mini-claude-code` 内绝不出现外部克隆、venv、pytest 缓存、JUnit 报告、原始日志 | ✅ 逐字生效 + I-9 增量 |
| §9.4 | 每个数字可回溯到归档的原始 pytest 输出 + `schema_sha256` 匹配的实例 JSON；缺证据的数字不写进报告 | ✅ 逐字生效 + I-10 增量 |
| §9.5 | 冻结性 | ✅ 逐字生效（本文 §1–§9 同样适用） |

### 5.2 臂层增量不变量（I-1 … I-12，每条给可执行检查）

| # | 不变量 | 可执行检查 | 违反后果 |
|---|---|---|---|
| **I-1** | **实例定义冻结**：`eval/instances/` 下任何文件不得被 P1 改动 | `git -C E:\aicoding\mini-claude-code status --porcelain -- eval/instances` 必须为空；每次读取前重算 `schema_sha256` | `harness_bug`，停批 |
| **I-2** | **同实例三臂同一份实例定义与同一 base** | 三次运行各自记录 `instance_id` / `base_commit` / `schema_sha256` / `problem.txt` 的 SHA-256，三者必须逐字相同 | `harness_bug`，停批 |
| **I-3** | **判定只认独立 pytest**；agent 自述、console 里的 `==> 判定：…` 行、人眼判断都不是判定输入 | verdict JSON 里必须存在 `pytest.cmd` + `exit_code` + 逐 node 结果；`judge()` 入参不含任何 agent 文本（结构性检查） | 该条结果不作数 |
| **I-4** | **infra / timeout 不计入分母，且超时判定先于退出码** | 判定行必须有 `timed_out` 标记；代码路径检查：`if timed_out → TIMEOUT` 必须在读 `exit_code` **之前**。⚠ Windows 强杀进程以 exit 1 收尾，与 pytest「有失败」的 exit 1 不可区分 ⇒ 顺序颠倒会把 timeout 误记成 not_resolved | `harness_bug`，停批 |
| **I-5** | **并发 ≤ 3** | 调度器落盘 `max_workers` 与实际并发峰值（同时在跑的子进程数采样）；峰值 > 3 ⇒ 违规 | `harness_bug`，该批次作废重跑 |
| **I-6** | **跑前环境洁净**：不得有残留的臂变量泄漏 | 每次运行前检查父进程 `AGENT_REPO_MAP_DEFAULT` / `AGENT_FINDINGS_DIR` / `AGENT_REPO_ROOT` / `ANTHROPIC_*` 是否被意外设置；A 臂尤其要求 `AGENT_REPO_MAP_DEFAULT` **未设置**（否则 §1.1 的来源自证会变成假的） | 该次运行作废重跑一次；连续两次 ⇒ `harness_bug` |
| **I-7** | **每臂产物隔离**（§5.5） | 每个 run 的目录互不相同且 realpath 互异；venv 按 (实例, run) 隔离且其中只有一个 `_eval_editable_*.pth` 指向本 run 工作区 | `harness_bug`，停批（这是 t4/D1 的静默误判成因） |
| **I-8** | **臂身份自证**：console 与 trace 运行头必须打出 §1.1 那张表的对应行 | 逐次运行 grep 自证行；`arm.repo_map_default` 与其 `source` 字段都要落盘 | 该次运行标 `harness_bug` |
| **I-9** | **跑批前后仓库状态可解释** | 跑前与跑后各存一份 `git status --porcelain` 全文；两份的差异必须能被逐项点名解释（只允许新增 `eval/reports/_p*_scripts/`、文档、以及明确豁免的运行产物）。**上游缓存 `eval-cache` 必须保持 porcelain 为空** | `harness_bug` |
| **I-10** | **每个数字带证据路径** | 每条运行落 `state.jsonl` 行 + `result.json` + `artifacts/`（`judged.json` / `verdict_run.txt` / `verdict.json` / `candidate_patch_raw.diff` / `candidate_patch_judged.diff` / `venv.log` / `workspace.log`）；C 臂另加 `policy_dispatch.jsonl` | 缺证据的数字不写进报告 |
| **I-11** | **断点续跑不改写已完成结论** | `--resume` 语义沿用 P0 §8.5：`stage == judged` 且工件 hash 齐备 ⇒ 跳过；同一实例同一臂在同一 `run_id` 下只允许一条 `judged` 行 | `harness_bug` |
| **I-12** | **P0 冻结文本未被改动** | `git diff --stat <P1 提交> HEAD -- eval/PREREGISTRATION.md` 为空；`git log --follow --oneline eval/PREREGISTRATION.md` 仍只有 1 次提交 | 立即回滚，视为破坏冻结 |

### 5.3 唯一变量的持续校验（不是只跑前查一次）

每次运行落盘的 `arm` 字段（trace 运行头）都要与 §1.1 的定义比对；**任何一次不匹配都当场停批**，而不是"记进结果节的注意事项"。
这条来自 P0 的一贯纪律：变量设错最坏的表现不是报错，而是**伪装成「这一臂没效果」**。

### 5.4 工作区与仓库状态可解释（跑前 / 跑后）

| 时点 | 检查 | 期望 |
|---|---|---|
| 跑前（每次运行） | 实例工作区 `git status --porcelain` | **空**（刚从 base 检出） |
| 跑前（批次级） | `E:\aicoding\eval-runs\p1*` 不存在或为空；`eval-cache` porcelain 空；`eval/instances/` porcelain 空 | 满足才允许起批 |
| agent 结束后 | 实例工作区 porcelain | **非空是可解释的**：内容必须等于该次运行采集到的候选补丁涉及的文件集（逐条点名） |
| 判定结束后 | `artifacts/` 在 `repo/` **之外**（P0 §9.1：判定临时文件不落 repo/） | 满足 |
| 批次结束后 | 项目根 porcelain 差异逐项解释（I-9） | 满足 |

### 5.5 每臂产物隔离（run_id 命名规则写死）

```
run_id  =  p1-<UTCyyyymmddTHHMMSS>-<instance_id>-<A|B|C>
目录    =  E:\aicoding\eval-runs\<run_id>\<instance_id>\{repo, artifacts}
venv    =  E:\aicoding\eval-venvs\<instance_id>-<pytag>-<run 哈希>   （沿用 t8 的 (实例, run) 隔离）
label   =  p1-<instance_id>-<arm>                                    （决定 usage.jsonl 行的归属）
```

- **一次运行一个 run_id**（不是「一个批次一个 run_id」）：因为 venv 与 `.pth` 挂载点是唯一的共享可变状态来源（D1 竞态），按运行粒度隔离最省事，也让「哪个 run 拿到哪份工作区」变成路径上写得出来的事。
- 代价是 venv 会累积（README §3 已警告）⇒ 起批前检查 `E:\aicoding\` 剩余空间；预计新增 ≤ 36 × 24 MB ≈ **0.9 GB**（不足 ⇒ 停批，不清别人的目录）。
- 三臂的产物**不得互相引用或复用**：B 臂不复用 A 臂的工作区，C 臂不复用 A 臂的 venv。

### 5.6 停止规则与成本上限的位置

成本上限、单次预估量级、触顶时的报告义务全部写在 **§7**（不在本节重复，避免两处数字漂移）。
失败归因见 **§6**；非外推声明见 **§8**；结果节规矩见 **§9**。

### 5.7 跑批前的臂接线自检（本文档授权的唯一「真实模型调用」例外）

**目的**：证明 §1.3 的四条 W 检查在真机上成立，然后才允许发布任何臂间数字。

| 步骤 | 内容 | 约束 |
|---|---|---|
| S1 | 选 §3.3 的第 1 条实例（`astanin-python-tabulate__5373ebf`），按 A→B→C 各跑一次 | 消耗预算，计入 §7 的 36 次之内（**不是额外的**） |
| S2 | 跑 W1–W4 四条机器校验 | 任一不过 ⇒ 停批修接线，**这三次运行作废**（作废的三次仍计入成本，如实报告） |
| S3 | 过 G-B / G-C1 / G-C2 三条治疗门槛的检查逻辑 | 门槛本身在正式结果上判定，S3 只验证「能不能测出门槛」 |
| S4 | 人工核对三臂的 console 自证行（§1.1 表）与 §5.4 的状态可解释性 | 全过才允许进第 2 条实例 |

> ⚠ S1 用的是**真实模型调用**，会产生费用，且它的 resolved 结果**不得进入任何臂间比较的叙述**（它是接线自检，不是实验数据）。
> 处理方式：S1 的三次运行在报告里单列一节，标 `wiring_probe`；若后续跑批从第 2 条实例开始，则 A/B/C 三臂的第 1 条实例要么按同规则重跑一次（计入 39 次实际上限，见 §7.3），要么如实报告为「第 1 条实例只有自检数据」。
> **禁止**把自检那三次直接当正式数据用 —— 那会让「判据写在数据之前」变成一句空话。

---

## §6 失败归因分类（六类，预定义；每条失败必须落到一类 + 证据）

### 6.0 归因的地位与总纪律

- 归因是**对 not_resolved / 非正常结束的进一步分层解释**，**不改变** resolved 判定本身，也不新增 verdict_class。
- **归因的判据不依赖 agent 自述**（I-3 同样管住这里）：只用四类客观证据 ——
  ① harness 独立 pytest 的逐 node 结果；② 采集到的候选补丁正文与其触及文件集；③ trace 里的工具调用序列与逐回合 usage；④ 磁盘状态（porcelain、工作区 HEAD、`policy_dispatch.jsonl`）。
  agent 说「我定位到了 X」只能作为 trace 存档，**不参与归因判定**。
- 每条运行给**恰好一个主归因**（primary）+ 可选若干次要标签（secondary）。互斥规则见 §6.6。
- 报告必须给三臂 × 六类的**计数矩阵**，并给每条落在 ①/② 类的实例的逐条证据指针。

### 6.1 ①定位失败（mislocalization）

| 项 | 内容 |
|---|---|
| **是什么** | agent 改的内容与根因无关：改了无关文件，或改动根本没触及引发 F2P 的代码区域 |
| **怎么判（可执行）** | 同时满足：<br>(a) `verdict_class == agent_result` 且 `resolved == False`；<br>(b) 存在至少一条 F2P node 结果 ≠ `passed`；<br>(c) 采集补丁（剥离测试侧后）触及的文件集合 ∩ **gold patch 触及的文件集合** == ∅，**或**交集非空但补丁的所有 hunk 都不在 gold 任一 hunk 的 ±30 行窗口内；<br>(d) P2P 未过数 == 0（即它没把别处弄坏——弄坏了走 ②）。 |
| **证据要求** | `candidate_patch_judged.diff` 路径 + `patch_touched_paths` 列表 + gold `patch` 的文件集（来自实例 JSON 的 `data_paths.patch_file`，**仅用于归因，永不进 agent 可见输入**）+ 逐 hunk 行区间对照表 |
| **判不了** | gold patch 无法解析出 hunk 行号（畸形 diff）⇒ 标 `unattributable`；补丁为空 ⇒ 走 `empty_patch` 桶（§6.6），不算定位失败 |

### 6.2 ②补丁错（wrong_fix）

| 项 | 内容 |
|---|---|
| **是什么** | 改了正确区域，但没修对；或顺带把别处弄坏了 |
| **怎么判** | 同时满足：<br>(a) `agent_result` ∧ `not_resolved`；<br>(b) **不满足** §6.1 的 (c)（即补丁确实落在 gold 的文件 ∩ ±30 行窗口内）；<br>**或** P2P 未过数 > 0（引入了回归，无论它有没有修好 F2P）。<br>⚠ 若 F2P 全过但 P2P 有未过 ⇒ 也归本类（`resolved=False` 的原因就是回归）。 |
| **证据要求** | 逐 node 的 `judged.json`（点名哪些 F2P 没过 / 哪些 P2P 回归）+ 对应 hunk 片段 + 该测试的 `verdict_run.txt` 失败摘要行 |
| **判不了** | 补丁能 apply 但 pytest 崩（exit ≥2 / collection error）⇒ 走 ⑥ infra（P0 §5.4），不在本类里猜 |

### 6.3 ③测试没跑起来（tests_not_run）

这一类测的是**过程**，不是结局（一次运行可以同时 resolved=True 又落进本类的观察，所以它是**单列一列**而非互斥桶）。

| 项 | 内容 |
|---|---|
| **是什么** | agent 全程没有成功执行过一次有意义的测试验证（没调 `run_tests`，或每次调用都失败/被拒） |
| **怎么判** | trace 的 `tool_use` 计数中 `run_tests` 次数 == 0，**或** 所有 `run_tests` 调用返回的都是拒绝/错误文本（判据：返回串以 `拒绝：` 开头，或含 `不存在或不是文件`，或 MCP 层 is_error）。<br>补充口径：`run_tests` ≥1 次但全部只跑了**与 F2P 文件无关**的路径 ⇒ 记 secondary 标签 `ran_wrong_tests`。 |
| **证据要求** | trace 文件路径 + `run_tests` 调用逐条入参与返回首行摘录 |
| **判不了** | trace 缺失或损坏 ⇒ 该条运行的过程指标整体作废（不得由 console 回忆补齐） |

### 6.4 ④超时（timeout_killed）

| 项 | 内容 |
|---|---|
| **是什么** | 撞到 K10/K11 的墙钟上限被 kill |
| **怎么判** | verdict 行的 `timed_out == True`（**先查此标记，再看退出码**，I-4）。区分两个子口径并都要落盘：`timeout_agent_phase`（agent 阶段 >900 s）/ `timeout_judge_phase`（判定 pytest >600 s）/ `timeout_total`（>2400 s）。 |
| **计入分母？** | ❌ **不计入分母、不计作 agent 失败**（P0 §5.4）。本类只是把它从「能力表现」里摘出来单独报率。 |
| **证据要求** | 计时字段三个都要（`wall_s.agent_phase` / `wall_s.judge_phase` / `wall_s.total`）+ kill 时刻 + 被 kill 的 pid/命令行 |
| **判不了** | 无计时记录 ⇒ 不得事后补判超时；按实际退出码归类，并在结果节声明这条数据的缺陷 |

### 6.5 ⑤撞回合上限（turn_cap_hit）

| 项 | 内容 |
|---|---|
| **是什么** | agent 被 `max_turns = 120`（K4）截断，而不是自己收的尾 |
| **怎么判** | 三条件取**或**：<br>(a) trace/console 出现 `Reached maximum number of turns` 字样；<br>(b) `num_turns >= 120`；<br>(c) `usage.jsonl` 该行 `subtype` 为 error 形态且 `is_error == true`。<br>⚠ D8 教训：t5 那次 agent 在第 40 回合**已经改对了代码**，只是没机会收尾 ⇒ 撞上限 ≠ 没解出来。因此本类必须与 resolved 结果**交叉呈现**（四格表：撞上限×resolved / 撞上限×not_resolved / 未撞×resolved / 未撞×not_resolved）。 |
| **计入分母？** | 撞上限的运行若仍拿到可判定的补丁 ⇒ `verdict_class` 照常是 `agent_result` / `empty_patch` / `patch_conflict` ⇒ **计分母**（这是 agent 的真实表现，不是环境问题）。只有被墙钟 kill 才走 ④。 |
| **证据要求** | `num_turns` + 上限值来源自证行（`回合上限 = 120 （来源：实例模式默认(120)）`）+ 最后一次工具调用与最后一段 assistant 文本的时间戳 |
| **判不了** | D6 残留风险：若某条异常路径上 `num_turns` 仍为 null ⇒ 只能用 trace 逐回合计数替代，并显式标注口径来源；两者都缺 ⇒ `unattributable` |

### 6.6 ⑥infra（基础设施故障）

| 项 | 内容 |
|---|---|
| **是什么** | 与 agent 能力无关的运行故障。沿用 P0 §5.4 的枚举：网络不可达 / 依赖装不上 / venv 损坏 / `.pth` 挂载自检失败 / pytest 退出码 ≥2 或收集错误 / 磁盘或权限失败 / `test_patch` 无法应用 |
| **怎么判** | `verdict_class == infra_error`（由程序按 P0 §5.4 判据决定，**人工不得改标**）。重试规则沿用 P0：同实例最多 2 次 infra 重试，仍失败 ⇒ `excluded_infra`，批次规模减 1 并在结果节写明 |
| **证据要求** | `artifacts/infra_error.txt`（含 traceback）+ `venv.log` + 失败的命令行与退出码 |
| **P1 特有的增量** | C 臂的 driver 派发内部异常（`policy_error` 非空、scout 会话崩、worktree 创建/移除失败）⇒ **也算 infra**，但**不整轮作废**：该次运行仍会带着「索引为空」继续跑父 agent。此时必须在归因里同时打 `secondary = dispatch_failed_empty_index`，并在 §3.5 G-C2 门槛下判该次「干预未施加」⇒ 标 `harness_bug`（不是 infra，因为比较对象消失了）。这条区分写死：**派发炸了但索引进去了 = infra 重试；派发炸了导致索引为空 = harness_bug**。 |

### 6.7 归因互斥与优先级顺序（避免同一条被两类抢走）

判定按下列顺序**首个命中即定 primary**：

```
1. harness_bug            （§5.2 任一不变量被破坏 / 治疗未施加）→ 批次级问题，不进归因矩阵的分母统计
2. infra_error            （⑥）
3. timeout                （④）
4. empty_patch            （agent 什么都没改，或只改测试被剥光 → P0 §5.4 的正常实质失败，单列一格，不塞进 ①）
5. patch_conflict         （补丁无法 apply → 单列一格，不塞进 ②；归因时另附「冲突位置」证据）
6. turn_cap_hit           （⑤）
7. tests_not_run          （③）
8. mislocalization        （①）
9. wrong_fix              （②）
10. unattributable         （以上都无法用现有证据判定 ⇒ 如实列出，不许硬塞）
```

- ①–⑥ 是合同要求的六类；`empty_patch` / `patch_conflict` / `unattributable` 是**继承 P0 §5.4 与诚实出口**的附加格，不得用来掩盖归因失败。
- `resolved == True` 的运行**不打** primary 归因（它没失败），但仍要打 ③/⑤ 的**过程观察列**（「解出来了但同时撞了上限」是本批最有信息量的组合之一）。
- `unattributable` 的数量必须原文出现在最终报告里。若非 `agent_result` 之外的 not_resolved 条目里 `unattributable` > 2 条 ⇒ 说明 §6 的判据不够用，处理方式是**如实报告 + 在下一次预注册里补规则**，不是回头改本文。

### 6.8 归因独立性（谁做归因）

归因由**不执行跑批的人/成员**完成（第三阶段流程的第④步「独立于跑批者」）：
归因者只读归档产物（`eval-runs/<run_id>/...`、trace JSON、`usage.jsonl`），**不得**为了归因而重跑 agent 或修改任何工件。
每条归因结论落一份结构化 JSON（`instance_id, arm, primary, secondary[], evidence_paths[], rule_hit`），其中 `rule_hit` 必须是 §6.1–§6.6 里那条可执行判据的编号 —— 便于审查者抽查「判据是否被绕开」。

---

## §7 成本上限与停止规则

### 7.1 单次运行的预估量级（引用已实测数字，标明 n 与来源）

| 观测 | 值 | 来源 | 性质 |
|---|---|---|---|
| A 臂单条（真实仓库实例） | `input_uncached` **45,310** / `cache_read` **1,916,160** / 墙钟 **273 s** / 回合 40（**被旧上限 40 截断**）/ 工具调用 77 | P0 t5 冒烟（`pallets-click__4fd2fea`），`reports/p0-smoke.md` §4.2，t9 审查 §9 已逐字段复算 | **n=1**，且是被截断的失败轮 |
| A 臂单条（D6 修复后，实例默认上限） | `input_uncached` **41,416** / `output` 30,004 / `cache_read` 1,483,392 / `total_cost_usd` **≈ 1.70** / 回合 73 | `usage.jsonl` label `t8-d8-instance-default`（同一条 click 实例，`--max-turns` 不传 = 120） | **n=1**，success=false 但用量完整 |
| scout 单组 | `input_uncached` ≈ 9.4k–19.0k / `total_cost_usd` ≈ 0.11–0.33 / 墙钟 11.8–39.9 s | `servers/experiments/out/policy_scout_usage.jsonl`（第二阶段 D 臂批次） | **不是本靶场**，仅作 C 臂 scout 侧的量级参考 |

**据此给出的单次运行预算区间（写死为规划值，不是判据）**：

| 臂 | 预计单次 `input_uncached` | 预计单次端到端成本 | 预计单次墙钟 |
|---|---|---|---|
| A | ≤ 60k | ≤ **2.0 USD** | ≤ 900 s（K10 上限） |
| B | ≤ 90k（`symbols` 地图更大，探索可能更省也可能更费 —— **方向未知，这正是 P1 要测的**） | ≤ 3.0 USD | ≤ 900 s |
| C | 父层 ≤ 60k **+ scout 侧 K × ≤ 20k**（K = §1.2 定义的派发组数，本批 1–3） | ≤ **2.0 + 0.35×K USD**，即 ≤ 3.1 USD | ≤ 900 s（scout 在父 agent 开工前跑完，占用同一段墙钟） |

> ⚠ §7.1 的 scout 单价来自**第二阶段 D 臂批次**（`more-itertools-hard` 靶场），不是本批 12 条实例；它只是量级参考。
> 若 C 臂实测 scout 侧显著超出该量级 ⇒ 触发 §7.3 的成本上限停止规则，**不得**回头改本节的上限数字。

### 7.2 整批上限（触顶即停，不放宽判据）

| 维度 | 上限 | 依据 |
|---|---|---|
| 运行次数 | **36 次**（12 实例 × 3 臂）；含接线自检在内总计 **≤ 39 次**（§5.7 S1 的三次若不复用则额外占额） | 设计即 36 |
| 端到端 token 总量 | `Σ input_uncached` ≤ **3,000,000**；`Σ cache_read` ≤ **60,000,000** | 按 §7.1 的单次上界 × 36 再留约 25% 余量后的取整 |
| 美元成本 | Σ 端到端 `total_cost_usd` ≤ **100 USD** | 单次 ≤3 USD × 36 ≈ 108，取整钉 100 作为硬顶 |
| 批次墙钟 | **8 小时**（沿用 P0 §8.4 的全批次预算） | 并发 3 ⇒ 36 次 × ~600 s ÷ 3 ≈ 2 小时为乐观值，8 小时是含 infra 重试的硬顶 |
| 磁盘 | `eval-venvs` 新增 ≤ **1.0 GB**；起批前须实测剩余空间充足 | §5.5 |

### 7.3 停止规则（三条，逐条给触发条件与动作）

1. **成本触顶**：任一项（token / 美元 / 墙钟 / 磁盘）达到 §7.2 的上限 ⇒ **立即停止起新的运行**（已在跑的先收尾）。
   报告义务：**如实报告已完成的条数**，格式为「计划 36 次，实际完成 N 次（A: a 次 / B: b 次 / C: c 次），停在第 … 条实例的第 … 臂，触发的是 <哪一项> 上限（实测值 vs 上限值）」。
   **不许为了凑满 36 次而放宽任何判据**（不加开关、不改超时、不换实例、不手工补 token、不把 infra 改标成 not_resolved）。
2. **infra 率触顶**：某臂 `infra_rate > 1/3` ⇒ 停批排查（§2.5）。这类停批**优先于**成本继续消耗，因为 infra 高的批次产不出可解释的数字。
3. **不变量违反**：§5.2 任一 I 检查不过 ⇒ 当场停批（不重试、不自愈），把违反映射成 `harness_bug` 并如实报告。

### 7.4 预算外推的前置声明（防止读者拿 §7.1 去乘）

§7.1 的两个 A 臂观测都是 **n=1**、来自**同一个仓库的同一条实例**（click），且分别处于「被旧上限截断」与「success=false」状态。
不同仓库上下文规模差异可达数倍（click 的 `core.py` 3134 行 vs pluggy 全仓 124 个 node）。
⇒ 本批**允许**在跑完前 3–5 条之后**更新预估中位数用于排期**，但**禁止**用更新后的预估去修改 §7.2 的任何上限（上限是承诺，不是估计）。

---

## §8 非外推声明（八条，必须原文出现在最终报告里）

1. **单模型、单端点**：只跑项目 `.env` 里那一个模型（当前 `deepseek-flash[1m]`）的一个端点配置。⇒ 不构成任何「模型间比较」，也不支持「改进带来 X 个点提升」的说法。换模型/换端点，本批数字全部失效为历史观察。
2. **n = 12 条实例**：Wilson 95% 区间宽度约 ±25–29 个百分点（p≈0.5）；**任何小于 ~15 个百分点的臂间差异不得解读为效应**；每格 1 次运行、无重复 ⇒ 无法分离臂效应与实例内的随机波动。
3. **4 个小型纯 Python 库**：`astanin/python-tabulate`、`more-itertools/more-itertools`、`pallets/click`、`pytest-dev/pluggy`（各 3 条）。这些仓库是被 P0 §2 的纳入规则（纯 Python + pytest + 免编译 + 免网络 + 基线测试 ≤300 s）筛出来的 ⇒ 天然偏向小型纯 Python 库。**不外推**到 numpy/pandas 类重编译仓库、JS/Go 生态、企业单体仓库、需要服务/端口/GPU 的系统。
4. **实例由真实修复提交 revert 构造**（`construction_method == "revert_of_real_fix_commit"`）：缺陷分布受「哪些提交同时改了源码和测试」这一筛选条件支配，不代表线上真实 bug 分布。
5. **未运行官方 SWE-bench harness、未使用官方 Docker 镜像/conda 环境**：用的是自研容器无关 harness（目录副本 + 每 run 独立 venv + 运行时 pip install），在依赖安装、测试选择、超时策略上都与官方不同 ⇒ 环境相关失败会体现在我们的 `infra_error` 率上，而不是 agent 能力上。
6. **`problem_statement` 来自提交信息而非 GitHub issue 正文**（P0 §3.4）：任务文本分布与官方不同，**难度偏向未知**（提交信息可能更贴近实现、更易定位，也可能信息更少、更难定位）。⇒ **本报告分数不得与官方 leaderboard 或他人 SWE-bench 子集分数并列比较**，任何场合都不行。
7. **两处 P0 既有裁定继续生效**（§0.3）：① 12 条里 **10 条**的 F2P 依赖 P0 §4.2 的「按目的细化」（字面读法下 F2P 为空 ⇒ E5 淘汰，本批根本组不出 12 条）；② **3/12** 条 tabulate 实例的第 3 向净效果 = 基线、少一向判别力，harness 层 clean-apply-but-wrong 的判别力由其余 9 条证成。提到判别力时必须带上这两句。
8. **本报告不能证明的事**：不能证明 agent 具备通用软件工程能力；不能证明其在官方基准上的表现；不能证明成本的长期趋势（缓存命中率随批次剧烈波动，P0 §10.3）；不能证明「仓库地图检索」或「driver 强制派发」在任何其它模型/仓库/题面来源下有效；不能给出因果结论 —— 本文档授权的是**方向性观察 + 失败案例归因**。

---

## §9 结果节规矩（追加节的唯一合法形态）

1. **追加节编号从 §10 起**。本文占用 §0–§9；§0–§9 一经提交**不得改写**（含错别字修订之外的一切改动）。
   发现规则本身写错了 ⇒ 重新做一次预注册（旧数据作废为探索性观察），**不允许**在本文件里就地更正。
2. **每个追加节首行必须写「对应规则节：§X.Y」**，并给出证据路径（`eval-runs/<run_id>/…` 下的具体文件）。
3. **追加节只引用、不改写 §1–§9**：可以写「按 §2.1 的口径，A 臂为 4/12」，不可以写「考虑到 infra 偏多，我们把分母改为 agent_result 一类」——后者是对 §2.1/§5.2 I-4 的改写。
4. **追加节允许做什么**：报数字；报已完成条数与偏离（§4.4、§7.3）；报 `unattributable` 清单；报 §5.7 自检的处理方式；报 reserve 顶替记录（对齐 P0 §7.5①②③）；报与某条预注册规则的**冲突**（如实陈述，不回头改规则）。
5. **追加节禁止做什么**：新增或修改臂定义；调整任何 K 常量；变更归因判据；引入推断统计；把 `infra_error` 改标成 `not_resolved` 或反向；事后挑选实例组成"配对集"来制造方向一致性。
6. **数字的可追溯性**：结果节里每一个汇总数都必须能从 §4.1 那张 12×3 明细表复算出来；缺原始 pytest 归档输出的数字不得写入（P0 §9.4 / I-10）。
7. **发布前独立审查**：结果节草稿必须经一次**不同模型的独立审查**（复算关键数字、抽查归因是否真按 §6 的可执行判据、核 §1.3/§3.5 的门槛未被绕开），审查报告落 `eval/reports/`。审查未过不得提交结果节。

---

## 附 A：本文档的九项自检清单（提交前逐项核）

- [x] ① 三臂定义与唯一变量：§1.1（A 无开关 / B 只加 `--repo-map-default symbols` / C 只加 `--delegate=policy`）+ §1.2 C 臂派发规模判据 + §1.3 四条机器校验 W1–W4
- [x] ② 共享常量全部列全：§1.3 K1–K20，含 `--max-turns` **不传 = 实例默认 120** 及其理由（D8 截断实证 + 上限是常量不是变量）、模型/端点来自 `.env`、题面、工具集、靶场与实例定义、超时数值（600/900/2400）、并发 ≤3、`--no-write` 不启用
- [x] ③ 指标：§2.1 resolution rate（主指标，独立 pytest：F2P 全过 ∧ P2P 全过）+ §2.3 三 token 口径（`input_uncached` 主 / `cache_read` / `cache_creation`）+ §2.4 两条成本口径（父层 / 端到端，**scout 侧单列**）+ §2.5 过程指标（回合数、工具调用分布、耗时、infra 率）
- [x] ④ 判据与检验力：§3（n=12、**实例内三臂交错** §3.2、只报区间与方向 §3.4、**禁止 p 值**、§3.1 检验力不足的四条声明、小的百分点差异不得解读为效应）
- [x] ⑤ 不变量：§5（P0 §9 继承清单 + I-1…I-12：实例冻结 / 只认独立 pytest / infra+timeout 不计分母且**超时先于退出码** / 同实例三臂同定义同 base / 并发 ≤3 / 每臂产物隔离 §5.5 / 跑前跑后状态可解释 §5.4）
- [x] ⑥ 失败归因六类：§6（①定位失败 ②补丁错 ③测试没跑起来 ④超时 ⑤撞回合上限 ⑥infra，每类「是什么/怎么判/证据要求/判不了」齐备 + §6.7 互斥优先级 + §6.8 归因者独立于跑批者 + 「判定不依赖 agent 自述」）
- [x] ⑦ 成本上限与停止规则：§7（单次量级引用 P0 冒烟实测 273 s / `input_uncached` 45,310 及 D6 修复后的 1.70 USD；整批上限 36 次 / 3M token / 100 USD / 8 h / 1.0 GB；触顶时如实报告已完成条数；不许为凑数放宽判据）
- [x] ⑧ 非外推声明：§8 八条（单模型单端点、n=12、4 个小库、revert 构造、**未运行官方 harness、未用官方镜像、题面来自提交信息**、不得与官方 leaderboard 并列、不能证明之事）
- [x] ⑨ 结果节规矩：§9（追加节从 §10 起、只引用不改写 §1–§9、允许/禁止清单、发布前独立审查）
- [x] 与 P0 的关系：§0.3（P0 判据/实例/harness 全盘沿用且**不改**，P1 只加「臂」这一层变量；继承 t9 的三处裁定与两处措辞更正）

## 附 B：本文档提交时的状态自证（「判据写在数据之前」的证据）

- 本文档提交时：`E:\aicoding\eval-runs\p1*` **不存在**（尚无任何 P1 跑批产物）。
- 提交时 `HEAD` 的父提交为 P0 收尾提交（`6302dae`），其后没有任何 P1 运行记录。
- P0 的 `eval/PREREGISTRATION.md` 在本次提交中**未被改动**（`git show --stat` 只列 `eval/P1-PREREGISTRATION.md` 一个文件）。
- 本次提交**不含任何代码改动**（`coding_agent.py` / `eval/harness/*` / `agent/*` / `eval/instances/*` 均未出现在 diff 里）；多臂支持、臂→CLI 映射、交错调度是后续任务。
- 提交哈希与时间由 git 记录，结果节与最终报告必须原样引用它们。


## §10 结果节（t20 批次；对应规则节：§2、§3、§4、§6、§7、§8、§9）

> 本节为 §1–§9 的追加结果，不改写前文规则。独立审查报告：`eval/reports/t33-review-round-3.md`（verdict=pass）。本批使用语义等价自建 harness，未运行官方 SWE-bench harness。

### 10.1 主结果与偏离

按 §2.1 分母，A=6/9，B=8/9；完整配对的 B vs A 为 **2 改进 / 4 平手 / 0 变差**。不报 p 值；n=9，未达预注册 n=12，检验力不足，本结果仅属探索性方向性观察。逐实例配对差值与三 token、两成本口径见 `eval/reports/p1-analysis.md` §1–§2。

本批实际为 **9 实例 / 27 行（agent_result 23 + timeout 4）+ 9 槽因止损未启动**，不是 12 实例三臂对比已完成；只验证出 1 组改进 B，第二组 C 未获可比测量。若要使 C 可比，需预注册新的计时口径并重跑。C 的前置开销证据位于 `servers/experiments/out/policy_dispatch.jsonl` 的 k1–k4、各 run 的 `console_C.log`（无 agent 开始）及 900.02–900.03 s wall 记录。

按 §1.1 实测 C 派发为 **11×K=1 + 1×K=2 = 13 组**；原文「合计 25 组」是 F2P 节点总数，属口径混用。11/12 实例 K=1，故不得称「并行分组侦察」。三条 pallets-click C 的 §1.2 等式门槛未满足：`K_observed 3/2/2`（另有 1 组未及终态）≠ `K_expected 1`，治疗未按设计施加，C 不可测；该仓库基线失败分布在 3 个测试文件，而实例 F2P 只涉及 1 个，组数 2–4 倍吃满 900s。

### 10.2 成本与臂数

臂数为 **63/75**，累计 usage 成本 **$142.86/225**，本批成本 **$61.38**；累计值不得当作本批值。C scout 中位 `cache_read`=81,024、cost=0.2505；e2e `input_uncached` 仍增加（36,681→48,831），e2e cost 中位反而更低（1.984→1.584），但 C 仅 5 条有效分母、极差 0.532–9.902，不得据此说 C 更便宜；父层成本下降不等于端到端更便宜。

### 10.3 状态字段、回填与作废情景

本批**未使用 resume**。t29 后 manifest 的 `skipped_completed` 语义是「已完成跳过」，并新增 `skipped_failed`；timeout/infra/harness_bug 视为终态而被跳过。归档 27 行的 `artifacts` 索引由 `eval/reports/p1_backfill_evidence_index.py --apply` 于 2026-10-13 23:03:49 +08:00 **事后回填**，不是当时落盘：SHA 从 `17f3a711…` 变为 `bb89825a…`，仅 9 条 C 行的 `artifacts` 变化，A/B 18 行逐字节不变，详见 `eval/reports/p1-t32-backfill.md` 与 `eval/reports/p1-batch.md` 文件头。旧 `subtests passed` 门禁误报曾足以把 B 的 +2 翻成 6:6 平手；t24 已证门禁错、数据对。

### 10.4 失败归因与限制

F6：9/9 有 isolated 证据文件，其中 6/9 可复算全部四项；3 条 timeout 缺 `index_chars`/`worktrees_after_dispatch`。限制：`K_expected=1` 不得称并行分组侦察；未跑官方 harness，数字不得与 leaderboard 并列；不把 C 写成「更差」或「能力 3/5」。详尽归因见 `eval/reports/p1-analysis.md` §3–§4。


