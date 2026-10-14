# P1 失败归因与统计（t20-batch，独立重算）

> 数据批次：`E:\aicoding\eval-runs\p1-20261013T122356-t20-batch`。本报告只读 `triple_state.jsonl`、各 run 的 `judged.json` / `policy_evidence_isolated.json` 与 `usage.jsonl` 重算；不把 `p1-batch.md` 的汇总当输入。批次实际 27 行（9 个实例×3 臂），因 C 连续超时止血，另外 9 槽未启动。

## 1. 可复现重算与主结果

重算脚本：[`p1_recompute.py`](p1_recompute.py)。它读取 27 行状态、按 label 匹配 parent usage，并从 C 臂隔离证据逐 attempt 加总 scout usage；输出逐行 `recompute_rows.json` 到批次目录。

### 1.1 主口径（全部 27 行）

分母严格取 `agent_result + empty_patch + patch_conflict`，排除 `timeout`/`infra_error`/`harness_bug`。本批无 empty_patch/patch_conflict：

| 臂 | resolved / 有效分母 | resolution rate | 被排除 |
|---|---:|---:|---|
| A | 6/9 | 66.7% | 0 |
| B | 8/9 | 88.9% | 0 |
| C | 3/5 | 60.0% | 4 timeout（均约900.02–900.03s）|

这不是 12 实例完整 P1：有效实例仅 9；不报 p 值，只报方向与区间，样本量远小于预注册 n=12，检验力不足。按 n=12 预注册的区间规则，任何小差异都不能称为效应；本批更应视作探索性。

逐实例配对（resolved 二值，`B vs A` / `C vs A`）：

| 实例 | A/B/C | B→A | C→A |
|---|---|---:|---:|
| astanin-tabulate 5373ebf | 1/1/1 | 0 | 0 |
| astanin-tabulate 87a9a4e | 1/1/0 | 0 | − |
| astanin-tabulate 9dedd45 | 1/1/1 | 0 | 0 |
| more-itertools 1b7f033 | 0/1/timeout | + | 不可比（C timeout）|
| more-itertools ccf96ac | 0/0/0 | 0 | 0 |
| more-itertools f8ccab2 | 0/1/1 | + | + |
| pallets-click 1b0e19f | 1/1/timeout | 0 | 不可比|
| pallets-click 4fd2fea | 1/1/timeout | 0 | 不可比|
| pallets-click ac6a2ac | 1/1/timeout | 0 | 不可比|

按逐实例表复算（9 个已启动实例；C 有 4 条 timeout，不可比）：B>A 2、B<A 0、平 7、不可比 0；C>A 1、C<A 1、平 3、不可比 4。这里的“+”仅是方向记录，不是因果效应。

> **2026-10-14 勘误**：原汇总行写「B>A 2、B<A 0、平 4、不可比 3；C>A 1、C<A 3、平 2、不可比 3」，把「完整三元组」口径混入全量口径，与 :25-33 逐实例表不符（按表面标记，C 可变差的实例只有 `87a9a4e` 一条）。已按逐实例表复算修正。

### 1.2 门禁误报的作废情景

旧 `verdict_gate` 曾把 pytest-json-report 的 `outcome='subtests passed'` 误报为不一致；t24 已修为等价 `passed`，并对原 27 行只读复测 PASS，行内 `judged.json` 与 `resolved` 未变。因此主口径必须使用全部 27 行。

仅为审计透明度，若错误地剔除门禁标记的 8 行（10/11/12/13/15/16/17/21），重算为 A=6/6、B=6/6、C=2/3；这是**已作废的情景区间**，不是第二个有效结果。它说明一个检查器口径错误足以把 B 相对 A 的 +2 翻成 6:6 平手，判据自身必须独立验证。

## 2. token、成本与过程

三 token 口径始终分开：`input_uncached`、`cache_read`、`cache_creation`；不使用 output 参与比较。下表为有效运行的中位数（极差）；C 的 token/成本仅对 5 条非-timeout 有父层，scout 另列。

| 臂 | parent input_uncached | parent cache_read | parent cache_creation | parent cost USD | scout input_uncached | scout cache_read | scout cost USD | e2e input_uncached | e2e cost USD |
|---|---|---|---|---|---|---|---|---|---|
| A (n=9) | 36,681 (13,877–50,097) | 1,848,704 (110,464–2,915,200) | 0 (0–0) | 1.984 (0.269–3.853) | 0 (0–0) | 0 (0–0) | 0 | 36,681 (13,877–50,097) | 1.984 (0.269–3.853) |
| B (n=9) | 38,283 (23,515–113,906) | 2,138,752 (218,368–9,045,504) | 0 (0–0) | 2.254 (0.487–7.323) | 0 | 0 | 0 | 38,283 (23,515–113,906) | 2.254 (0.487–7.323) |
| C (n=5) | 25,005 (8,581–113,077) | 899,072 (34,944–12,958,848) | 0 (0–0) | 1.344 (0.126–9.487) | 23,003 (18,550–29,324) | 81,024 (44,544–302,464) | 0.2505 (0.209–0.743) | 48,831 (34,635–136,080) | 1.584 (0.532–9.902) |

C 超时条目只有 scout 侧已落盘的 usage，父层未完成，故不将其伪装为完整 e2e 成本样本。A/B 无 scout，明确记 0。**父层成本下降 ≠ 端到端更便宜**：C 的 parent 中位数低于 A/B；加入 scout 后，e2e `input_uncached` 中位数仍增加（36,681 → 48,831），但 e2e cost 中位反而更低（1.984 → 1.584）。C 仅 5 条有效分母，e2e cost 极差 0.532–9.902，**不得据此说 C 更便宜**。

过程指标（从 `usage.jsonl` 的 `num_turns` 和状态 `wall_s` 重算）：A 回合中位数 68（17–107），wall 中位数 247.81s（42.49–622.20）；B 70（26–136），304.64s（67.14–641.52）；C 有效 49（10–155），357.33s（93.43–849.83）。C 另有 4×900s timeout。B 有 136 回合、C 有 155 回合，均超过预注册默认 120；这是实际产物中的过程偏离，不能称“撞 120 上限”而没有对应的 turn-cap 证据。批次归档没有每个 run 的 trace 文件，故工具调用按名称分布无法可靠重算；不以 `metrics.tool_calls_total` 或 console 猜补。

manifest 的 `$142.860337` 与 `input_uncached=2,941,549/cache_read=115,952,384` 是**累计含历次探针**，不是本批成本，不能用于逐臂或本批 e2e 比较；逐臂数字来自 label 匹配的 parent usage 和隔离 scout accounting。

## 3. 失败归因（§6 优先级）

所有非 resolved 行：

| instance/arm | primary | 证据 |
|---|---|---|
| tabulate 87a9a4e/C | mislocalization/wrong_fix（证据不足以二选一，保守记 mislocalization） | `...87a9a4e-C.../artifacts/judged.json`：F2P `test/test_regression.py::test_empty_table_with_maxheadercolwidths` 未通过，P2P 无回归；candidate patch sha=`1d324...` |
| more-itertools 1b7f033/A | mislocalization | `...1b7f033-A.../judged.json`：F2P `TestConcurrentTee::test_concurrent_consumers` 未通过，P2P 空；patch sha=`98ad...` |
| more-itertools ccf96ac/A、B、C | mislocalization | 各 `judged.json` 均 F2P `TestRandomDerangement::test_basics` 未通过、P2P 空；A/B/C patch sha 分别 `f5e3...`/`6554...`/`8b02...` |
| more-itertools f8ccab2/A | mislocalization | `judged.json` 两个 RunningMedian F2P 未通过、P2P 空；patch sha=`ac9b...` |
| more-itertools 1b7f033/C | timeout_killed | 行14 `timed_out=true`, `wall_s=900.02`；只有一个 dispatch 时间点，console 无可用模型时间戳，不能推断 scout 是否占满窗口 |
| pallets-click 1b0e19f/C、4fd2fea/C、ac6a2ac/C | **harness_bug / treatment not as designed，C 不可测** | 各 run 的 `policy_evidence_isolated.json` 在单个 900s 窗口内出现多个不同时间的 `dispatched`（分别 4、3、3 个事件，且含 retry/后续 dispatch），而非一次按 K 组完成后交给父 agent；父层无 usage，console 只有 `unrecognized_model` stderr。按 §1.2/§4.4 不把它们写成 C “更差”，而标不可测。 |

C 的必要观察：例如 pallets-click 1b0e19f 有 21:04:37、21:07:45、21:10:53、21:19:30 多次 dispatch；这不是同一派发的重复终态行，而是 `policy_dispatch.py` 对组/重试逐次追加的 durable 事件。代码证据：初始每组写 `event=dispatched`，截断才写 `retry_dispatched`，最终再写终态；同 ts 的 `dispatched` + `completed/undiagnosed` 是一次派发的开始与终态，不是两次模型调用。不同 ts 的多次 dispatched 才是多组/重试事件。该窗口内反复派发且未完成父层，治疗没有按预注册“先完成 K 组再运行父 agent”稳定施加，故 C 不可测。

C 分组规模：按 §1.1 实测为 **11×K=1 + 1×K=2 = 13 组**；预注册原文「合计 25 组」实际是 F2P 节点数，属口径混用，C 预期派发规模被高估约一倍。且本批 11/12 实例 K=1；此时 C 等价于**一个 scout 打全仓/该文件**，不能称“并行分组侦察”。三条 pallets-click C 的 §1.2 等式门槛未满足：`K_observed 3/2/2`（另有 1 组未及终态）≠ `K_expected 1` ⇒ 治疗未按设计施加 ⇒ C 不可测。该仓库基线失败分布在 3 个测试文件，而实例 F2P 只涉及 1 个；组数约为预期的 2–4 倍并吃满 900s 窗口。

## 4. 反例边界与可直接引用摘要

- 三臂都未解出的实例：`more-itertools-more-itertools__ccf96ac`（A/B/C=0/0/0）。
- B 相对 A 变差：无；B 改进的正向差异只发生在 `1b7f033` 与 `f8ccab2`，其余已完成配对为平。
- C 相对 A 变差的可比实例：`tabulate__87a9a4e`；C 失败而 A 成功。pallets-click 三个 C timeout 不应称变差，已按治疗未按设计施加/不可测处理。
- B 未帮上的原因：多数实例 A 已成功（天花板效应）；ccf96ac A/B 均同一 F2P 未过，证据指向定位/修复未覆盖根因。B 的高 token/wall 极端（f8ccab2 B=136 turns、113,906 uncached）说明 symbols 地图并不保证更省。
- C 未帮上的原因：87a9a4e 的单 scout 结论未带来通过；ccf96ac 同一 F2P 仍失败；click C 是多次 dispatch/timeout 的接线问题，不可归因能力。

**可直接放 README 的摘要（限于这批 9 实例/27 行）**：在已完成且可判定的 t20 子批中，A 为 6/9、B 为 8/9；B 相对 A 的实例配对方向为 2 个改善、7 个平手、无变差，但样本量小且未完成预定 12 实例，不能宣称稳定提升。C 仅 5 条有效分母、3/5，另 4 条 900s 超时；其中 3 条出现窗口内多次派发，按预注册应视为治疗未按设计施加、C 不可测，不能写成 C 更差。C 的有效样本 e2e 成本包含 scout，父层成本下降不代表端到端更便宜。

## 5. 本报告不能证明什么

1. 不能证明 B 的 +2 是稳定因果效应：样本不是完整 n=12、每格无重复、检验力不足，不报 p 值。
2. 不能把 C 的 3/5 或 60% 当成能力排名：至少 3 条 timeout 的多次 dispatch 使治疗不可测，另 1 条缺少模型时间戳，无法判断是窗口伪影还是真实慢。
3. 不能把旧门禁标出的 8 行剔除；t24 已证明是 `subtests passed` 比较口径误报。作废情景 A6/6、B6/6、C2/3 仅用于展示判据错误如何翻转结论。
4. 不能从本批累计 manifest `$142.86` 推导本批成本；它含历次探针。也不能用 parent cost 代替 e2e cost。
5. 不能声称“并行分组侦察”：K=1 的实例只有一个 scout 组。
6. 不能补写缺失的工具调用分布、timeout 父层 token 或 scout 是否占用全部 900s；归档证据不足时保持不可归因。
