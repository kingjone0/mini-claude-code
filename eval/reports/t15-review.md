# P1 独立审查（t15 / review round 1）

> **审查对象**：P1 臂接线（t2）+ C 臂修复与提交（t7/t9/t17/t18/t23/t24）+ 正式跑批（t20-batch）+ 归因统计（t14）
> **审查者**：`reviewer`（`deepseek-official/deepseek-flash`），与执行侧（`xi-api/gpt-6-sol`）**不同 provider + 不同模型**
> **审查开始时的 HEAD**：`5203536f15bf210300e7f1e33210903c006f5e7b`；审查期间有 1 个**纯文档**提交落地（`33d9698`，仅新增 `eval/reports/t25-c-timeout-investigation.md`，无实现改动），本报告已并入其结论（见 F3）。
> **本审查未改任何实现代码**；唯一写入是 `eval/reports/` 下自己的报告与脚本（提交 `0b096d5` + 补充提交）。
> **零模型调用**：本审查全部为产物复算 + 离线机制测试，未启动任何臂进程、未消耗 REV2 预算
> **verdict = `needs_revision`**（6 条 findings，见 §3）

---

## 0 一句话结论

机制层面该修的修上了、且我**独立复现**过：判定链不读 agent 自述（4 个案例实证）、同实例并发隔离成立（3 轮并发 0 infra、0 静默误判，并把公式**还原成修复前**复现出缺陷）、判据确实写在数据之前（提交时序早 4.5–7.4 h）、C 臂分组证据已按 run 抽成独立文件（9/9 存在，逐 run 只读复算 6/9 完全达标）。41 项关键数字我独立重算，**39 项一致、2 项不一致**（§5），且不一致项已追到根因（`p1-analysis.md` 把 `5373ebf-C` 的 scout 值写成了 `f8ccab2-C` 的值）。但仍有必须修的：①那 2 个错的中位数；②预注册 §1.1 声明的 K 分布与冻结实例**不符**（实际 11×K=1 + 1×K=2 = 13 组，文档写 8/3/1 = 25 组，25 其实是 F2P 节点数）；③**3/9 正式 C 臂运行违反 §1.2 分组门槛**（K_observed 2–4 ≠ K_expected 1）——t25 的调查报告（审查期间落地）方向正确并援引 §1.2，但仍未把「等式门槛被违反」写成门槛违反，analysis 的机制句也不符证据；④正式跑批只完成 **27/36**。

---

## 1 审查方法（复跑命令与产物）

| 目的 | 产物脚本 | 输出证据 |
|---|---|---|
| 41 项关键数字独立重算 + C 证据逐 run 审计 + 抽取保真性 | `eval/reports/p1_review_recompute.py` | `p1-review-recompute.txt` / `.json` |
| 判定不可被自述绕过（4 案例）+ 超时/分母口径 | `eval/reports/p1_review_judge_bypass.py` | `p1-review-judge-bypass.txt` / `.json` |
| 同实例并发隔离 3 轮 + 隔离公式**反向复现** | `eval/reports/p1_review_isolation.py` | `p1-review-isolation.txt` / `.json` |
| C 臂 scout 中位数不一致的**追因**（逐 run scout 明细 + parent `policy_scout_*` 双口径） | `eval/reports/p1_review_cscout_trace.py` | `p1-review-cscout-trace.txt` |
| `--resume` 对 timeout/infra 槽的语义（离线实证，F5 的证据） | `eval/reports/p1_review_resume_predicate.py` | `p1-review-resume.txt` / `.json` |

复跑方式（全部只读/临时目录，唯一写目标是 `eval/reports/`）：

```text
Set-Location E:\aicoding\mini-claude-code
& 'D:\Anaconda3\envs\aicoding\python.exe' -X utf8 eval/reports/p1_review_recompute.py
& 'D:\Anaconda3\envs\aicoding\python.exe' -X utf8 eval/reports/p1_review_judge_bypass.py
& 'D:\Anaconda3\envs\aicoding\python.exe' -X utf8 eval/reports/p1_review_isolation.py
& 'D:\Anaconda3\envs\aicoding\python.exe' -X utf8 eval/reports/p1_review_cscout_trace.py
```

### 1.1 合同 verify 命令的实测结果（本审查实跑，exit 0）

```text
Set-Location E:\aicoding\mini-claude-code; & 'D:\Anaconda3\envs\aicoding\python.exe' eval/gold_check.py
```
⇒ `exit=0`，`三向自校验结果：12/12 实例三向全对`（gold 12/12、empty 12/12、wrong 12/12）；证据根 `E:\aicoding\eval-runs\goldcheck-20261013-221145`。同时如实打出构造侧 caveat：3 条 tabulate 实例的 `wrong_patch` 与 gold 逐字节相同 ⇒ 第 3 向退化为基线（与 §2.1 的观察一致）。

```text
Set-Location E:\aicoding\mini-claude-code; & 'D:\Anaconda3\envs\aicoding\python.exe' eval/harness/mechanism_check.py --run-id t15-mech
```
⇒ `exit=0`，四条全 `[PASS]`：`timeout_priority` / `test_stripping` / `concurrency_isolation` / `resume`；resume 项 `skipped_completed = 2`（期望 2）、已完成结论未被改写；证据根 `E:\aicoding\eval-runs\t15-mech`。

> ⚠ 口径说明：`mechanism_check` 的 `resume` 项走的是 **P0** 的 `eval/harness/scheduler.py`（`SCH.run_batch`），**不是** P1 的 `scheduler_p1.py`；因此这条 PASS **不覆盖** F5 指出的 `scheduler_p1.py:333` 语义，两者不矛盾。

```text
Set-Location E:\aicoding\mini-claude-code; git log --oneline -8
```
⇒ 见 §7 的提交时序（审查起点 `5203536`，审查期间他人纯文档提交 `33d9698`，本审查 `0b096d5` / `ab20a88`）。

被审对象与输入（sha256）：

| 输入 | bytes | sha256 |
|---|---:|---|
| 批次状态 `eval-runs/p1-20261013T122356-t20-batch/triple_state.jsonl` | 205543 | `17f3a7119fa01b20b1fa899c1fda2b3ef2af3f737b7444d9a6de334f17da8c78` |
| 冻结实例 `eval/instances/pilot-12.jsonl`（**未改动**） | 325882 | `7b6764014a5d938c98ac395c6ff49dc48e1ade5686547d2d5ab54aca2105cbc7` |
| `servers/experiments/out/usage.jsonl`（parent 侧，p1- 56 行） | — | — |
| `servers/experiments/out/policy_scout_usage.jsonl`（scout 侧，p1- 21 行） | — | — |
| `servers/experiments/out/policy_dispatch.jsonl`（共享 dispatch，149 行） | — | — |

---

## 2 六项验收逐条

### 2.1 判定不可被自述绕过 —— **PASS**

在**临时** `RUNS_ROOT`/`VENV_ROOT` 里建真工作区与真 venv，走生产判定链 `scheduler_p1.judge_arm_workspace()`（采集候选补丁 → 剥测试侧 → 重建 test_patch 起点 → 独立 pytest → 逐 node judge），每个案例都先往**产物目录**写两份「agent 自述成功」诱饵（`console_A.log` 里 `status: SUCCESS / resolved: true / tests: ALL PASSED`、`agent_selfreport.json` 里 `{"resolved": true, "f2p_passed": 1, "p2p_passed": 60}`）：

| 案例 | 实例 | 诱饵 | 实得 verdict_class / resolved | pytest exit | 判定 |
|---|---|---|---|---:|---|
| 什么都没改 + 自述成功 | tabulate 5373ebf | 有 | `agent_result` / **False** | 1 | 自述被忽略 |
| 往源码追加一行无用注释 + 自述成功 | tabulate 5373ebf | 有 | `agent_result` / **False** | 1 | 自述被忽略 |
| 冻结的**真错补丁**（1b7f033 的 `unrelated_file_comment`，正着施加）+ 自述成功 | more-itertools 1b7f033 | 有 | `agent_result` / **False** | 1 | 自述被忽略 |
| gold 补丁（对照） | tabulate 5373ebf | 有 | `agent_result` / **True** | 0 | 判定不是恒假 |

逐 node 证据齐全且与期望一致，例如第一个案例：`judged={"resolved": false, "f2p_total": 1, "f2p_not_passed": ["test/test_output.py::test_floatfmt_decimal"], "p2p_total": 60, "p2p_not_passed": []}`；对照案例 `f2p_not_passed=[]` 且 exit 0。**没有任何一条结论的依据是 agent 自述。**

口径断言（同脚本）：

- **超时先于退出码**：`classify_run` 对 `timed_out=True & returncode=1` 返回 `timeout`（不是 `agent_result`/`infra_error`），四例输出 `['timeout','agent_result','infra_error','agent_result']` 与期望逐字相同。批次产物侧亦一致：4 条超时行 `timed_out=True, exit_code=1, verdict_class=timeout, stage=failed_infra`（若先看退出码会记成「改错了」）。
- **infra/timeout 不计入分母**：`judge.summarize(3×agent_result 其中 1 resolved + 1 timeout + 1 infra)` ⇒ `denominator=3, resolved=1, infra_retryable=2`。

> 附带发现（**已归档的构造侧遗留，不是判定缺陷**）：`astanin-python-tabulate__*` 的冻结 `wrong_patch` 与 `patch` **逐字节相同**，语义是 `wrong_patch_method=reverse_of_gold`。我第一次把它**正着**施加，判定给出 `resolved=True`（= 施加了 gold）。唯一正确施加方式是 `run_instance.wrong_patch_plan()` 的「先叠 gold 再 `git apply -R`」。`eval/harness/run_instance.py:204-225` 与 `gold_check.py` 的 caveat 已如实写明；**结论：`wrong_patch` 正文不能当「错补丁」直接用**。

### 2.2 并发隔离仍成立（自有反例 + 反向复现）—— **PASS**

**A 当前公式**：同实例（tabulate 5373ebf）两个 run **并发**（`ThreadPoolExecutor(2)`）做 3 轮 `fresh_workspace → ensure_venv`（含 `.pth` 挂载与 import 自检），全部在临时目录：

| 轮 | 异常 | venv 互异 | `.pth` 名互异 | 各自 venv 内只有自己的 editable 挂载 | 挂载指向自己的工作区 | 并发结束后再核 import |
|---:|---|---|---|---|---|---|
| 1 | 无 | True | True | True | True | 均解析到自己工作区 |
| 2 | 无 | True | True | True | True | 均解析到自己工作区 |
| 3 | 无 | True | True | True | True | 均解析到自己工作区 |

⇒ **0 infra、0 静默误判**；`.pth` 指向各自工作区（`only_own_editable=True`，无第二个 `_eval_editable_*`）。这不是跑它自带用例：`mechanism_check.py` 的并发项用的是**四条不同实例**并发，我只做它没覆盖的「**同一实例**两个 run 并发」。

**B 反向复现（把 `(实例, run)` 隔离公式还原成修复前）**：

```text
venv_dir            : <VENV_ROOT>/<repo_key>-<base_commit[:8]>              （不含 run_id）
editable_pth_name   : _eval_editable_<sha256(instance_id)[:16]>.pth          （不含 run_id）
```

实测结果（原文摘录）：

- 两个 run 落到**同一个 venv** `...\venvs\astanin__python-tabulate-8f320866`（`same_venv=True`）、**同一个 `.pth`**（`same_pth=True`）；
- run A 的 import 在 B 准备**之前**解析到 A 自己的工作区（`ok=true`）；
- B 准备**之后**再核 A：`ok=false`，`assert_pkg_from_workspace` 抛 `隔离失败：tabulate 的导入路径不在本实例工作区内（解析到 ...\p1-review-t15-pre-B\...\tabulate\__init__.py，期望在 ...\p1-review-t15-pre-A\...` ⇒ **缺陷复现（`defect_reproduced=True`）**。

本次复现到的是**响亮形态**（被判定前后自检拦成 InfraError），我没有观测到静默形态；D1 历史上的静默形态（6/6 轮含 2 轮静默）是 P0 t4/t8 的证据，不在本审查的实证范围内 —— 这一点按「只能写成限制」处理。

**C 还原后复核**：`E.venv_dir` / `E.editable_pth_name` 恢复为原函数、两个 run_id 映射到互异 venv 与互异 `.pth` 名（脚本 `finally` 里恢复，无残留改动）。

### 2.3 复算关键数字 —— **39/41 一致，2 项不一致（已追因）**

41 项逐项对照见 §5。不一致的正好 2 项，且都落在 `p1-analysis.md` 的 C 臂 scout 行：

| 数字 | `p1-analysis.md` | 本次重算 | 结论 |
|---|---|---|---|
| C scout `cache_read` 中位 | 149,632 | **81,024** | 报告错 |
| C scout `cost` 中位 | 0.415 | **0.2505** | 报告错 |

**根因（追因见 `p1-review-cscout-trace.txt`）**：C 非 timeout 行的 5 个 scout 值是
`5373ebf=21,362 / 87a9a4e=29,324 / 9dedd45=18,550 / ccf96ac=23,826 / f8ccab2=23,003`（input_uncached），
`cache_read = 81,024 / 302,464 / 44,544 / 72,576 / 149,632`，`cost = 0.250472 / 0.743152 / 0.208872 / 0.240093 / 0.414556`。
按 5 值取中位：`input_uncached=23,003`（**恰好**与报告一致）、`cache_read=81,024`、`cost=0.2505`。
报告的 149,632 / 0.415 恰为 **`f8ccab2-C` 那一行的值** ⇒ 5 值集合里 `5373ebf-C` 被写成了 `f8ccab2-C` 的值（重复行）。**极差与 e2e 中位因此不受影响而仍然正确**（`cache_read` 极差 44,544–302,464 一致；`scout cost` 极差 0.209–0.743 一致；C e2e 中位 48,831 / 1.584 与极差 34,635–136,080 / 0.532–9.902 全部一致）。两种可能的 analyst 口径（逐 run 窗口内加总 / parent 行的 `policy_scout_*` 字段）**互相一致**，都给出 81,024 与 0.2505，所以这不是口径分歧而是取值错配。

**结论方向未被翻转**，但 `p1-analysis.md` 是 P1 的正式交付件，这两处必须改（改后仍是「parent 中位下降 ≠ e2e 下降」，因为 e2e 中位 1.584 > A 的 1.984? —— **注意方向**：C e2e cost 中位 1.584 **低于** A 的 1.984，而 C e2e `input_uncached` 中位 48,831 **高于** A 的 36,681。原文「加入 scout 后 C 的 e2e input/cost 仍增加」的 **input 部分成立、cost 部分在 n=5 中位数上不成立**，必须按此改写，不能笼统写成「e2e 仍增加」）。

> 另记：`p1-analysis.md` §2 的 C scout 中位与极差来自与我不同的选择口径（我同时验证了「label 全量」与「run 窗口」两种；label 全量会把探针行串进来，例如 `p1-astanin-python-tabulate__5373ebf-C` 在 scout 文件里有 7 条）。**报告没有写明它用哪种口径**，这是可复算性的一个弱点（我在 `p1-review-recompute.txt` §2 已给出 label 复用的完整清单：usage 3 个 label 分别有 13/12/11 条）。

### 2.4 判据写在数据之前 —— **PASS**

| 时点 | 证据 |
|---|---|
| 预注册 `76f34a6808d4cfc18fa3eb819d24abb918445363` | author date `2026-10-13 12:57:26 +0800`，committer `12:58:22 +0800`；`git merge-base --is-ancestor 76f34a6 HEAD` exit 0 |
| REV2 `242e0070acd3a820802c52f710d06368a22669f8` | author/committer `2026-10-13 15:58:13 +0800`；`merge-base --is-ancestor 242e007 HEAD` exit 0 |
| **正式跑批** `p1-20261013T122356-t20-batch` | 目录创建时间 `2026-10-13 20:23:56`（本地） |

⇒ 预注册早于正式批次 **7 h 26 min**，REV2 早于正式批次 **4 h 26 min**。两者都只改文档（`eval/P1-PREREGISTRATION*.md`）。更早的 `p1-*` 目录（13:25 起）全部是接线探针/离线判定演练，不是正式批次数据；`p1-judge-gold`/`p1-judge-empty`（18:40）是离线 gold/empty 演练，也无模型调用。**没有发现任何「先有数据后写判据」的痕迹。**

**实现与预注册的一致性**：逐项核对 §1.1 三臂开关、§1.3 W1–W4、§5.5 每 (实例,臂) 独立 run_id、§5.7 接线自检、§7 止损，均与实现一致（`arms.py` 单一真值源、`scheduler_p1.py` 按 `ARM_EXTRA_ARGS` 拼 argv、`--max-turns` 三臂都不传）。**但有两处偏离/不一致**：

1. **§1.2 分组门槛 3/9 不达标**（见 2.5 与 F3）——这是**门槛未满足**，不是实现违反预注册（实现按 `k_expected_from_instance` 从实例文件算，是对的）；
2. **§1.1 声明的 K 分布与冻结实例不符**（见 F2）——预注册文档自身的数字错，实现不受影响（运行时 K 取自实例文件）。

### 2.5 C 臂证据的独立可复算性（审计发现）—— **6/9 完全达标，3/9 不达标**

**抽取确实发生了**：9 条 C 臂行**每一条**在自己 run 的 `artifacts/policy_evidence_isolated.json` 里都有独立副本（`iso_exists=True`，14.8–37.8 KB），内容含该 run 的 `dispatch_rows` + `arm_report_rows`；`p1_extract_evidence.py` 按 `label` + `ts >= started_at_epoch` 过滤；**保真性**：9/9 的 isolated 行都是共享 `policy_dispatch.jsonl` 同 label 行的**子集**（`iso ⊆ shared = True`，逐条 `(ts,k,group,event)` 四元组比对）。

**只读该 run 目录复算**（组名 / k / `index_chars` / `worktrees_after_dispatch` / `K_expected` 来自 `eval/instances/pilot-12.jsonl`）：

| C run | verdict | `K_expected` | terminal 组数 | 组名 | k | `index_chars` | `worktrees` | 门槛 |
|---|---|---:|---:|---|---:|---:|---:|---|
| tabulate 5373ebf | agent_result | 1 | 1 | `test/test_output.py` | [1] | 141 | 1 | PASS |
| tabulate 87a9a4e | agent_result | 1 | 1 | `test/test_regression.py` | [1] | 407 | 1 | PASS |
| tabulate 9dedd45 | agent_result | 1 | 1 | `test/test_output.py` | [1] | 141 | 1 | PASS |
| more-itertools 1b7f033 | timeout | 1 | 1 | `tests/test_more.py` | [1] | 140 | 1 | PASS |
| more-itertools ccf96ac | agent_result | 1 | 1 | `tests/test_recipes.py` | [1] | 143 | 1 | PASS |
| more-itertools f8ccab2 | agent_result | 1 | 1 | `tests/test_recipes.py` | [1] | 143 | 1 | PASS |
| pallets-click 1b0e19f | timeout | 1 | **3** | test_arguments / test_info_dict / test_options | [1,2,3] | **缺失** | **缺失** | **FAIL** |
| pallets-click 4fd2fea | timeout | 1 | **2** | test_arguments / test_options | [1,2] | **缺失** | **缺失** | **FAIL** |
| pallets-click ac6a2ac | timeout | 1 | **2** | test_arguments / test_options | [1,2] | **缺失** | **缺失** | **FAIL** |

- 组名**确实是测试文件路径**（与 §1.2 的 `test_file` 口径一致）；6/9 的 `index_chars ≤ 2000`（140–407）、`worktrees_after_dispatch == 1`；
- **3/9 违反 §1.2**（`K_observed` 2–3 ≠ `K_expected` 1）**且这 3 条恰好都是 900 s timeout**，它们在自己的 run 目录里**取不到 `index_chars` / `worktrees_after_dispatch`**（父层被 900 s 窗口杀死，arm_report 的 policy 行未落盘）⇒ 「在只读该 run 目录的前提下复算全部四项」对这 3 条**不成立**；
- 3 条违反门槛的运行**都是同一仓库（pallets-click）**：基线失败落在 3 个测试文件里，而该实例的 F2P 只在 1 个文件 ⇒ 按 §1.2 的字面判据，C 臂这一次的**治疗未按设计施加**；
- **索引缺口（F6）**：`policy_evidence_isolated.json` 不在 state 行的 `artifacts` 字段里（9/9 都是 `listed_in_state=False`），报告里也没给该路径 ⇒ 证据在，但索引不完整。
- **与 t25 的关系**：`t25-c-timeout-investigation.md`（审查期间落地的纯文档提交 `33d9698`）已独立复现同一组 dispatch 行并指出「父 agent 从未启动 / 治疗未按设计施加」，但**没有**把 `K_observed ≠ K_expected` 写成 §1.2 门槛违反（详见 F3）。

### 2.6 写入边界清单 —— 见 §4（本节即该验收项的交付）

---

## 3 Findings（6 条，逐条给证据与修法）

### F1 · high · `p1-analysis.md` 的 C 臂 scout 两个中位数算错

- **证据**：`eval/reports/p1-analysis.md:51`（表 `C (n=5)` 行）写 `scout cache_read 149,632 / scout cost 0.415`；我按 5 值重算为 `81,024 / 0.2505`（`p1-review-recompute.txt` §4、§8 的 31/33 项）。149,632 与 0.415 恰是 `f8ccab2-C` 行的值 ⇒ `5373ebf-C` 在计算里被替换成了 `f8ccab2-C`。
- **影响**：结论方向未翻转（极差、e2e 中位均仍一致），但 §2 表格与 §4 摘要里的 C scout 数列是错的；且「加入 scout 后 C 的 e2e input/**cost** 仍增加」按重算在 cost 上**不成立**（C e2e cost 中位 1.584 < A 1.984）。
- **requiredFix**：把 C scout `cache_read` 中位改为 81,024、`cost` 中位改为 0.2505；并把 §2/§4 的「e2e 仍增加」改成「e2e `input_uncached` 仍增加（36,681 → 48,831），而 e2e cost 中位反而更低（1.984 → 1.584），但 C 只有 5 条有效分母、极差极宽（0.532–9.902），不得据此说 C 更便宜」。

### F2 · high · 预注册 §1.1 的 K 分布/总数与冻结实例不符

- **证据**：`eval/P1-PREREGISTRATION.md:103` 写「本批 12 条：8 条 K=1、3 条 K=2、1 条 K=3，合计 25 组」。按 §1.2 自己的公式（`K_expected = |{n.split('::')[0] for n in FAIL_TO_PASS}|`）对 `eval/instances/pilot-12.jsonl` 实测：**11 条 K=1、1 条 K=2（pluggy dd20a85）、合计 13 组**；而 **25 = F2P 节点总数**（1+1+1+1+1+2+1+5+1+1+4+6）。
- **影响**：C 臂的预期派发规模被高估一倍（25 vs 13）；且 11/12 实例 K=1 ⇒ 「按失败组分工」在本批大半退化为**单 scout**。交付件里若引用「25 组」即错。
- **requiredFix**：在预注册 §1.1 追加一处**订正说明**（不改历史行文的原意，只追加）：`K_expected` 实测分布为 11×1 + 1×2 = 13 组，原文「合计 25 组」等于 F2P 节点数、属口径混用；并把该订正同步进 P1 结果节（§10+）。

### F3 · high · 3/9 正式 C 运行违反 §1.2 分组门槛（K_observed ≠ K_expected），尚未有报告把它写成「门槛违反」

- **证据**：`pallets-click__1b0e19f-C` 终态组 3（`test_arguments`/`test_info_dict`/`test_options`，k=[1,2,3]；另有 k=4 `test_chain` 未及终态）、`4fd2fea-C` 终态组 2、`ac6a2ac-C` 终态组 2，而三者 `K_expected=1`（F2P 全在同一文件）⇒ `eval/P1-PREREGISTRATION.md:117` 的门槛「不同 group 终态记录数必须 == K_expected」**不满足**。
- **已有的记录与缺口**：
  - `t25-c-timeout-investigation.md`（审查期间落地，`33d9698`）**已**逐条列出 1b0e19f 的四个 `dispatched`（k1…k4 组名互异、`snapshot_head` 相同、k3 有一次 `retry_dispatched`），结论「③治疗未按设计施加 / C 应报不可测」并援引 §1.2/§4.4 —— 方向正确；
  - 但它把这四个组描述为「不是同一组无限循环」的正常分组循环，**没有指出这本身就是 §1.2 等式门槛的违反**（`K_observed=4 ≠ K_expected=1`），也没有把「组数 2–4 倍」与「900 s 窗口被吃满」连成因果（单组 scout 实测 186–275 s，2–4 组 + 1 次 retry ≈ 600–1100 s）；
  - `eval/reports/p1-analysis.md:70` 的原因写成「在单个 900s 窗口内出现多个不同时间的 `dispatched`，而非一次按 K 组完成后交给父 agent」——而顺序派发 k=1,2,3,4 正是「按 K 组循环」的设计行为，**机制描述与证据不符**；`p1-batch.md` 也未按门槛违反记录（只列了超时时间戳）。
- **影响**：结论方向（这三条不可测、不得写成「C 更差」）**是对的**，analysis 也已把 timeout 排除出分母；但「预注册门槛被违反」这一事实没有显性落到交付件里，下游容易把 F2 的「预期 25 组」与这里的「实测 2–4 组」串成错误叙事。也解释了 4 条 C timeout 里 3 条同属 pallets-click。
- **requiredFix**：在 `p1-analysis.md` 与 P1 结果节显式写「**§1.2 门槛未满足：K_observed 3/2/2（另有 1 组未及终态）≠ K_expected 1 ⇒ 治疗未按设计施加 ⇒ C 不可测**」，删掉/改写「而非一次按 K 组完成后交给父 agent」这句；补上事实性原因「该仓库基线失败分布在 3 个测试文件，而实例 F2P 只涉及 1 个」并说明 §1.2 的字面等式判据对「基线另有失败」的仓库会系统性不成立（供 REV3 裁定）。

### F4 · blocker · 正式跑批只有 27/36，P1 目标未达成

- **证据**：`triple_state.jsonl` 27 行（9 实例 × 3 臂），`agent_result=23、timeout=4`；9 条 pluggy 槽（`20d8143`/`cbf58d5`/`dd20a85`）工作区与 venv 已备好但**无状态行**（`eval-runs/p1-20261013T122356-pytest-dev-pluggy__*` 目录存在、无 triple_state 条目）；止损原文 `C consecutive infra/timeout >= 2`。
- **影响**：按 §7 的止损规则**停止本身合规**（不许为凑数放宽判据），analysis 也如实写了「有效实例仅 9、不是 12 条完整 P1」；但交付目标是 12×3=36，因此**任何**对外表述都不得写成「12 实例三臂对比已完成」。
- **requiredFix**：要么在 t25/t26 里按预注册允许的方式补完剩余 9 槽（须先修 F5），要么在结果节与 README 里把口径写死为「**9 实例 27 行 + 9 槽因止损未启动**」；两种都需在报告里显式给出未启动 9 槽的清单。

### F5 · medium · `--resume` 会重跑 timeout/infra 槽、改写终态且预算少记（已离线实证）

- **证据（离线实证，零模型调用）**：`eval/reports/p1_review_resume_predicate.py` 直接 import `scheduler_p1`，用真实的 `read_triple_state` / `append_triple_state` 在临时目录造出「A 判完、B 超时（`stage=failed_infra`）、C 判完」的 3 行状态，再复刻 `scheduler_p1.py:333` 的表达式 `if prev and prev.get("stage") == "judged"`：
  - `skipped=['A','C']`、`pending=['B']` ⇒ **timeout 槽被当作待跑槽重新提交**；
  - 对同一 `(实例, 臂)` 再追加一行后，该槽读回的终态从 `timeout/resolved=False` 变成 `agent_result/resolved=True`（`rewritten=True`，文件里同键两行、读回取后写者）⇒ **改写已完成结论**；
  - `len(read_triple_state())` 按 `(instance, arm)` 去重，重跑不增加计数 ⇒ 按 `scheduler_p1.py:131-136` 的公式 `started_arms = REV2_INITIAL_STARTED + len(prior) + in_flight`，**实际 spawn 4 次只记 3 次**，REV2 的 75 槽上限会被少记。
- **为什么不能拿 `mechanism_check` 的 resume PASS 顶掉本条**：那条走 P0 的 `scheduler.py`（`run_batch`），跳过条件不同；P1 的 `scheduler_p1.py` 的跳过条件是 `stage == "judged"`，本条的离线实证直接调用 `scheduler_p1` 的 `read_triple_state` / `append_triple_state`。
- **影响**：直接决定「剩余 9 槽怎么补」。按现状 resume，本批 **4 条 timeout 槽**会被重跑（其中 3 条已确认违反 §1.2），既改写终态又不受预算约束。
- **requiredFix**：明确 resume 语义后二选一：①把 timeout/infra 视为**终态**（跳过，并在 manifest 记 `skipped_failed`），补跑靠新批次；②允许重跑，但必须把重跑写进状态（新增 `attempt` 字段、保留旧行）并让 `started_arms` 计入实际 spawn 次数。二者都要先写进合同/预注册，然后再动 9 个未启动槽。

### F6 · low · C 臂 per-run 证据未进 state 索引，3/9 缺 `index_chars`/`worktrees`

- **证据**：9/9 的 `artifacts/policy_evidence_isolated.json` 存在但 `policy_evidence_isolated.json in row["artifacts"] == False`；3 条 timeout（1b0e19f/4fd2fea/ac6a2ac）的 isolated 文件里搜不到 `index_chars`/`worktrees_after_dispatch`（`p1-review-recompute.txt` §5）。
- **影响**：可复算本身成立（文件在 run 目录里，我确实只读 run 目录复算成功 6/9），但「索引不全 + 3 条缺项」会让下游以为 9/9 都齐。
- **requiredFix**：在 `p1-batch.md` 里改为「9/9 有 isolated 证据文件；其中 6/9 可复算全部四项，3 条 timeout 缺 `index_chars`/`worktrees`」，并把 `policy_evidence_isolated.json` 的路径写进逐行索引；后续（t16）把该文件名补进 state 行的 `artifacts` 字段。

---

## 4 写入边界清单（可写 / 只能写成限制）

**可写（本审查有独立证据支持的表述）**

1. 在 **9 实例 / 27 行**范围内：A 6/9、B 8/9、C 3/5（分母 = `agent_result`+`empty_patch`+`patch_conflict`，本批无 empty/patch_conflict）；`timeout` 4 条、`infra_error` 0 条。
2. parent 层 token/成本/回合/墙钟的中位与极差（41 项里 39 项与 `p1-analysis.md` 一致，见 §5）。
3. 机制结论：**判定只认 harness 自己跑的独立 pytest**（4 个自述诱饵案例 + 逐 node 证据）；**超时先于退出码**；**infra/timeout 不计入分母**。
4. 机制结论：**同实例并发隔离成立**（3 轮并发 0 infra、0 静默误判、`.pth` 归属正确），且**把公式还原成修复前能复现缺陷**。
5. 机制结论：**判据先于数据**（预注册早 7 h 26 min、REV2 早 4 h 26 min 于正式批次目录）。
6. C 臂分组证据**已按 run 抽取**（9/9 文件存在，isolated ⊆ 共享），其中 6/9 逐 run 复算达标（组名=测试文件路径、`k==K_expected`、`index_chars ≤ 2000`、`worktrees==1`）。

**只能写成限制（不得写成结论）**

1. **不得**声称跑了官方 SWE-bench harness；**不得**与官方 leaderboard 或任何外部基线并列。本批是自建 harness + 12 条自选实例（实际 9 条跑完）。
2. **不得**把「父层成本下降」写成「系统更便宜」：C parent 中位 1.344 < A 1.984，但 e2e（含 scout）中位 1.584、极差 0.532–9.902；`input_uncached` 侧 e2e 中位 48,831 > A 36,681。父层与端到端**必须分列**。
3. **不得**把 C 臂说成「并行分组侦察」：11/12 实例 `K_expected=1`（单个 scout 组），且 3/9 正式运行的组数与 `K_expected` 不符、`K=1` 的运行只是「一个 scout 打该文件」。
4. **不得**报 p 值，**不得**宣称 B 相对 A 的 +2 是稳定效应：n=9（非 12）、每格无重复、按 §3 的检验力规则只能报方向与区间。
5. **不得**把 C 的 3/5（60%）当能力排名：3 条 timeout 违反 §1.2 门槛（治疗未按设计施加）、另 1 条 timeout 缺模型时间戳，均不可归因能力。
6. **不得**把 27/36 写成 36/36，也**不得**把 9 条未启动槽计入分母或缺测补零。
7. **不得**用 manifest 的累计 `$142.86` / `input_uncached=2,941,549` 当作本批成本：那是含历次接线探针的累计值。
8. **不得**把「`wrong_patch` 正文」当错补丁（tabulate 系与 gold 逐字节相同，须走 `wrong_patch_plan`）。

---

## 5 41 项复算对照（摘要）

完整表在 `eval/reports/p1-review-recompute.txt` §8。39 项一致，2 项不一致（F1）。

- 一致（举例）：A/B/C `resolved/分母` = 6/9、8/9、3/5；parent `input_uncached` 中位 36,681 / 38,283 / 25,005 且极差逐字相同；parent `cache_read` 中位 1,848,704 / 2,138,752 / 899,072 且极差逐字相同；parent cost 中位 1.984 / 2.254 / 1.344（差 ≤0.001，四舍五入）；turns 中位 68 / 70 / 49；wall 中位（仅计入分母的行）247.81 / 304.64 / 357.33、极差逐项相同；C scout `input_uncached` 中位 23,003、极差 18,550–29,324；C e2e `input_uncached` 中位 48,831、极差 34,635–136,080；C e2e cost 中位 1.584、极差 0.532–9.902；timeout 行数 4。
- 不一致：C scout `cache_read` 中位（149,632 → 81,024）、C scout `cost` 中位（0.415 → 0.2505）。根因见 F1。

**口径提醒**：parent 侧的「label → usage 行」匹配在 `5373ebf` 上有歧义（usage.jsonl 里 `p1-…5373ebf-A/B/C` 各有 13/12/11 条，来自历次接线探针）。我必须加 `ts >= started_at_epoch` 才能取到正式批次那一行；`p1-analysis.md` 未写它用哪种口径 —— 若当初用的是「取最后一条」，本批恰好正确，但**同一 label 复用时该方法会串行**，建议在报告里写明。

---

## 6 本审查不能证明什么

1. 我**没有重跑任何正式臂**（零模型调用）；所有关于臂表现的数字都来自产物复算，不是新的实验。
2. 我**没有验证**那 9 条未启动槽（pluggy ×3）会成功；F4 只说明它们未启动。
3. 反向复现只拿到**响亮形态**（自检抛 `InfraError`）；D1 历史上的**静默形态**（6/6 轮含 2 轮静默误判）不在本次实证内，我只引用了 P0 的旧证据。
4. F3 的因果链（基线失败集 → 组数 2–4 → scout 串行吃满 900 s）来自产物 + 预注册文本的推断，**没有**做端到端复跑验证。
5. 我没有验证额度/网络侧的可用性，也没有验证 t25/t26 的补跑方案。
6. `eval/reports/p1-analysis.md`、`p1-batch.md` 在我审查时仍是 **untracked**（不在 HEAD 里），因此我的复算对象是工作区文件（sha256 见 §7）；若后续被改写，本文的数字需重跑脚本复核。
7. 审查期间仓库前进了 1 个**纯文档**提交（`33d9698`，只新增 t25 调查报告）与 t25/t26 的在途工作；我的全部结论针对 `5203536` 的实现与 `p1-20261013T122356-t20-batch` 的 27 行产物，**不覆盖**审查时点之后可能新增的跑批行或实现改动。

---

## 7 证据索引（本文引用的全部文件 + sha256）

审查者产出（本任务新增，全部在 `eval/reports/`）：

| 文件 | bytes | sha256 |
|---|---:|---|
| `p1_review_recompute.py` | 24619 | `b384e31880836dd6c302c6e8a711f3fb2f9391686dee45cdeae91b48e2d93289` |
| `p1_review_judge_bypass.py` | 12004 | `bd1103cec17eed312a37cecd1dc50e20b4613f50adbef70e09bc85df3597e6aa` |
| `p1_review_isolation.py` | 13262 | `71ed197f2e2a2fac399a6b7e87917da886e58f169967a81c639327db82a1fff8` |
| `p1_review_cscout_trace.py` | 5709 | `e536a3ad3f1e6482f22b89468ce47e173bd0f955f5d43ff6417ac15e309efe5a` |
| `p1-review-recompute.txt` | 15783 | `77398b9be61437a64b22c71a0f7ee86a2a9ed5433bfb05470774ba1d2ceb6bd7` |
| `p1-review-recompute.json` | 29144 | `90333bde4c43f34b1142b5fa0bed938bf3777753b42d0262c2846ca0cb2fe8bc` |
| `p1-review-judge-bypass.txt` | 7184 | `5260288c658c38fe81d305e746b42312607fef09ac86ffaf9e978d101f64883d` |
| `p1-review-judge-bypass.json` | 26861 | `e611737818e2b0f4b9ffb3f6e35e50bcfc44b9788c4614fc866a6d306118f71c` |
| `p1-review-isolation.txt` | 7182 | `ea4bd93a0cee170b8891a32cf8b4bef177dcc9aabd074ad151c167b8114b792c` |
| `p1-review-isolation.json` | 7662 | `4559156500001f887b3c5816e8e34f849897fe290865e335ebf09afca645a89f` |
| `p1-review-cscout-trace.txt` | 11433 | `70ce4ab8e529b4f7d1810c8f9c75dd03263ec953ea82d9748debcdc67bc2c86b` |
| `p1_review_resume_predicate.py` | 5468 | `7cbea627d2436d81dbfba122ae27b4b8ef9d9fde8206c897305f1532561ebdc5` |
| `p1-review-resume.txt` | 1512 | `2c92cfa38a1ac4cf3986ea2ac173d61ad9ee45ab9482f66c7eb7833e2bb3794f` |
| `p1-review-resume.json` | 792 | `99da49929b157fac811d7fcfa0ea2e7e9222420e2b4f6b71224b7b7a2aad4ba7` |

被审对象（只读）：

| 文件 | 说明 | sha256 |
|---|---|---|
| `eval-runs/p1-20261013T122356-t20-batch/triple_state.jsonl` | 27 行状态 | `17f3a7119fa01b20b1fa899c1fda2b3ef2af3f737b7444d9a6de334f17da8c78` |
| `eval-runs/p1-20261013T122356-t20-batch/t20_audit_index.json` | 逐行索引 | `f7222c371de8c10a0f9517e9da7cf59835649a19816807ec34e58ad4673b1899` |
| `eval/instances/pilot-12.jsonl` | 冻结实例（未改） | `7b6764014a5d938c98ac395c6ff49dc48e1ade5686547d2d5ab54aca2105cbc7` |
| `eval/reports/p1-analysis.md` | t14 归因统计（被审，untracked） | `4a70fc1ae6bd58aa82f2a1161780939df6c9af267781a985a58f52865ac5b520` |
| `eval/reports/p1-batch.md` | t20 跑批原始记录（被审，untracked） | `1b734b288cbe7c68aa00c456c98dccfc39599ccef561aa250b19913aff959aad` |
| `eval/reports/p1_recompute.py` | t14 的重算脚本（被审，untracked） | `a8f1fc301cddc06f12eb4e95324cf55f26eb4abd1d9255ab05e3456be0caa7f0` |
| `eval/reports/t25-c-timeout-investigation.md` | 审查期间落地的 C 超时调查（提交 `33d9698`，纯文档 23 行） | — |

提交时序（本审查相关）：`5203536`（审查起点）→ `33d9698`（他人纯文档提交，审查期间落地）→ `0b096d5`（本审查报告 + 脚本，已推送，`HEAD == origin/main == 0b096d5`）。

代码引用（只读，未改动）：`eval/harness/scheduler_p1.py:294-299,312,333,449-462`、`eval/harness/judge.py:263-275,297-323`、`eval/harness/env.py:135-160,185-204,236-261,293-323,360-423`、`eval/harness/run_instance.py:199-225`、`eval/P1-PREREGISTRATION.md:103,112-122`、`eval/reports/p1-analysis.md:51,55,70`。

---

## 8 交给 captain 的处置建议（按依赖顺序）

1. **F1** → 修 `p1-analysis.md` 两个中位数与 e2e 表述（**零模型调用**，可由 analyst 或 captain 直接改）。
2. **F2** → 预注册追加 K 分布订正（**零模型调用**，architect）。
3. **F3** → 结果节与 analysis 改写这三条的归因文字（**零模型调用**）；t25 已提供机制证据，只需补「§1.2 等式门槛被违反」这句与因果链。
4. **F5** → 先定 resume 语义（**零模型调用**），它决定 F4 怎么补。
5. **F4** → 在 F5 之后补剩余 9 槽，或在结果节写死「9 实例 27 行 + 9 槽未启动」。
6. **F6** → t16 收口时补索引与路径。
