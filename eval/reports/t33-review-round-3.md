# t33 独立复验：t32 的 ② 生产路径索引 + ③ 归档回填（review round 3）

> **被审任务**：t32（`xi-harness`，非本审查者实现 ⇒ 无自审冲突）
> **被审提交**：`faf7043473c2c594ded1e9ae951f146a13be0cbe`（② 生产路径生成证据，22:55:20）+ `f775742f111d357c74b4f50bb8fd36d709257852`（③ 归档回填，23:16:09）
> **审查时 HEAD**：`f775742f111d357c74b4f50bb8fd36d709257852`
> **本审查未改任何实现代码**：只写 `eval/reports/`；实验全在临时目录
> **零模型调用**：② 的生效性用**假 Popen 直接调用生产函数** `_run_one_arm`，不 spawn 任何 agent
> **verdict = `pass`**（6 项验收全过；另有 3 条**不阻断**的补充观察，见 §4）

---

## 0 结论

| # | 验收项 | 结论 |
|---|---|---|
| ① | ② 的生效性（落盘 state 行含索引，不看自述） | **PASS** —— 直接调用生产 `_run_one_arm`，落盘重读的行含索引；`exit1` 与 `timeout` 两个分支都成立；且过滤逻辑与归档证据四元组集合一致 |
| ② | ③ 的回填完整性（sha256 / 字段级 diff / 9-9 / A-B 未误改 / 「回填」字样） | **PASS** —— 回填前 sha256 与我 t15/t30 的**独立基线逐字相同**；27 行非 `artifacts` 字段零变化；9/9 C 行含索引；A/B 18 行整行逐字节不变 |
| ③ | G2 与 G3 落实 | **PASS** |
| ④ | 未越界（不碰他人报告/预注册、不重跑、不改结论字段、零模型调用） | **PASS**（`p1-batch.md` 那一处是 ③.④ **点名要求**的追加，见 §2.4 的边界张力说明） |
| ⑤ | 回归（gold 12/12、mechanism 全 PASS、selfcheck 0 失败） | **PASS** |
| ⑥ | 结论落盘 + verdict | **PASS**（本文件） |

一句话：**② 这次是真生效的**——我把生产的 `_run_one_arm` 用假 Popen 实跑，证据文件由 worker 自己生成、**落盘后的 state 行确实带上索引**，而且它筛出的 dispatch 行与归档抽取版**逐条一致**（同一 label + 同一窗口起点 ⇒ 2 行、四元组集合相同）；**③ 的回填是干净的**——「回填前」的 sha256 正好等于我 t15/t30 独立记录的基线，27 行里除 `artifacts` 外**一个字节都没动**，A/B 18 行整行逐字节不变。没有发现需要返工的缺陷；§4 三条补充观察建议在 t31 顺手处理。

---

## 1 方法与证据

自写复验脚本：`eval/reports/p1_review_t33_verify.py` → `eval/reports/t33-review-verify.txt` / `.json`

```text
Set-Location E:\aicoding\mini-claude-code
& 'D:\Anaconda3\envs\aicoding\python.exe' -X utf8 eval/reports/p1_review_t33_verify.py
```

---

## 2 验收逐条

### 2.1 ① ② 的生效性 —— PASS（不采信自述）

不看 t32 的报告结论，直接**调用生产函数** `scheduler_p1._run_one_arm(task)`（把 `E.RUNS_ROOT` 指到临时目录、`subprocess.Popen` 换成假进程，**不 spawn 任何 agent**），再把返回行经 `append_triple_state` 落盘、用 `read_triple_state` 重读：

| 案例 | 假进程行为 | 落盘行的终态 | 落盘行含索引 | worker 生成证据文件 | dispatch_rows |
|---|---|---|---|---|---|
| `exit1` | 正常返回、returncode=1 | `timed_out=False` / `infra_error` | **True** | True | 0（见下注） |
| `timeout` | 第一次 `communicate` 抛 `TimeoutExpired`、kill 后返回 | `timed_out=True` / `timeout` | **True** | True | 0（见下注） |

- 索引值为 `astanin-python-tabulate__5373ebf/artifacts/policy_evidence_isolated.json`（run 相对），落在 `row["artifacts"]` 末尾。
- **过滤逻辑的保真性**（补做，因为上面两例的窗口起点是「现在」⇒ 真实 dispatch 行都在窗口之前，故 0 行）：用**真实 label** `p1-astanin-python-tabulate__5373ebf-C` + **真实窗口起点** `1791894460` 调用同一个生产函数 ⇒ worker 版 `dispatch_rows=2`，归档抽取版也是 `2`，`(ts,k,group,event)` 四元组集合**一致 = True**。⇒ 生产版证据不是空壳，且与归档版同源同口径。
- ⇒ 判定：不是「函数返回值正确」，而是**落盘的 state 行确实含索引**。

### 2.2 ② ③ 的回填完整性 —— PASS（全部自算）

| 检查 | 结果 |
|---|---|
| 备份存在 | `triple_state.jsonl.pre-t32` 存在 |
| 回填**前** sha256 | `17f3a7119fa01b20b1fa899c1fda2b3ef2af3f737b7444d9a6de334f17da8c78` —— 与我 t15/t30 **独立记录的基线逐字相同**（不是我采信 t32 的数字，是它与我早先的记录吻合） |
| 回填**后** sha256 | `bb89825ada4d1eace311376736ab5a2d5cff01a86389bc58cbc623a01d12bbff`，与 t32 报告声称一致 |
| 物理行数 | 前 27 / 后 27 |
| 非 `artifacts` 字段 | **27/27 行逐字相同**（`resolved`/`verdict_class`/`stage`/`notes`/`judged`/`pytest`/`timed_out`/`tokens_*`/`budget_after`… 零变化） |
| C 行含索引 | **9/9**，每行 `artifacts[:-1] == 原 artifacts`（只追加、未重排、未删改） |
| A/B 行 | **18/18 整行逐字节不变** |
| 变化行号 | 第 3、6、9、14、15、21、23、25、27 行 —— 与 `argparse` 脚本的 `field_diffs` 一致 |
| 「回填」字样 | `p1-t32-backfill.md` 有；`p1-batch.md` 第 3 行有（含执行时刻 `2026-10-13 23:03:49 +08:00`、脚本 `eval/reports/p1_backfill_evidence_index.py --apply`、前后 sha256、逐行 diff 摘要、`.pre-t32` 备份位置，并明确写「**事后回填，绝非当时落盘**」） |

脚本本身的防护也复核了：断言 27 行、断言 9 条 C、断言证据文件存在、断言索引不在原行、断言非 `artifacts` 字段全等、断言 `artifacts[:-1]` 不变、备份用 `xb` 独占创建、`os.replace` 原子替换、替换后重算两侧 sha256。

### 2.3 ③ G2 与 G3 —— PASS

- **G2**：`eval/harness/t29-evidence.md` 现在写明「t29 的 F6 断言仅证明**函数返回值正确（未在生产 state 行验证落盘）**。t32 选择方案②：C worker 在返回 state 行前生成 `policy_evidence_isolated.json`……已归档 C 行的索引另于 2026-10-13 23:03:49 +08:00 **回填** 9/9……不能声称当时落盘」；`t29_resume_check.py` 增加了真实 state 行的索引断言（输出 `F6 state_row_index=… worker_generated=True`）✓
- **G3**：`policy_isolated_artifact()` 的 docstring 已写明「State `artifacts` entries are absolute paths except this one deliberate index. Resolve it against the batch `run_root` (not the worktree).」✓

### 2.4 ④ 未越界 —— PASS（含一处边界张力，如实写出）

| 提交 | 改动文件 |
|---|---|
| `faf7043` | `eval/harness/scheduler_p1.py`、`eval/harness/t29-evidence.md`、`eval/harness/t29_resume_check.py` |
| `f775742` | `eval/harness/t29-evidence.md`、`eval/harness/t29_resume_check.py`、`eval/reports/p1-batch.md`、`eval/reports/p1-t32-backfill.md`（新）、`eval/reports/p1_backfill_evidence_index.py`（新） |

- 两份预注册**未被碰**（`git diff --name-only 7d29e6d HEAD` 不含 `PREREGISTRATION`）✓
- 我 t15/t30 的审查报告、`p1-analysis.md`、`t26-…md` 等既有他人报告**未被碰** ✓
- 未重跑实例：`eval-runs` 下最新 `p1-*` 目录仍是 t20 批次（其 mtime 变为 23:03:49 只因回填替换了 state 文件）+ pluggy 工作区（20:27），无新批次目录 ✓
- 零模型调用：`usage.jsonl` 总/p1- = `186/56`、`policy_scout_usage.jsonl` 总/p1- = `55/21`（与我 t15/t30 基线相同）；p1- 用量最大 `ts=2026-10-13T21:32:25+08:00`，早于两次提交（22:55、23:16）✓
- 未改结论字段：由 §2.2 的字段级 diff 直接证明 ✓

**边界张力（必须写明，我不替谁抹平）**：验收④的字面要求是「未碰 `eval/reports/` 下既有的他人报告（只允许新增自己的脚本/证据）」，而 `p1-batch.md` 是 xi-runner 的报告、t32 确实动了它（在第 3 行插入一条回填注，并首次把该文件纳入版本库 —— 它此前一直是 **untracked**）。但验收③.④又**点名要求**「报告与 `p1-batch.md` 写明了『回填』二字与脚本路径/执行时刻」——`p1-batch.md` 不碰就做不到。两条判据在这一点上互相冲突，我按**更具体的 ③.④** 判定，并把事实与冲突一并记录：t32 对 `p1-batch.md` 的改动是**纯追加**（t15 时期我读到的正文锚点 `### t20-batch 逐行原始索引`、冻结实例 SHA256、state SHA256 均原样在位；第 3 行的注是新增），没有重写历史结论。建议 captain 在后续合同里明确：**共享批次记录文件的追加权归谁**（或规定「只允许在文件头部/尾部追加一节」）。

### 2.5 ⑤ 回归 —— PASS

| 命令（合同 verify 逐字） | exit | 结果 |
|---|---:|---|
| `python eval/gold_check.py` | 0 | `total=12 passed=12`；逐向 `gold 12/12`、`empty 12/12`、`wrong 12/12`。证据 `E:\aicoding\eval-runs\goldcheck-20261013-231814`（summary sha256 `c7b4bdfc07f1aa1d23db62fd604d3f375c0b72fc595985af2bf5872e2a57625a`） |
| `python eval/harness/mechanism_check.py` | 0 | `{timeout_priority: True, test_stripping: True, concurrency_isolation: True, resume: True}`。证据 `E:\aicoding\eval-runs\mech-20261013-232033`（summary sha256 `b81f93af3406b4450aac0e65a8e49a5ca240893ad694f81f089685ee86f47333`） |
| `python servers/experiments/subagent_selfcheck.py` | 0 | 末行 `=== 全部通过（0 项失败）===`（stdout 里唯一含 "FAIL" 的行是 `[ok ] 组内失败数来自 FAILED/SUBFAILED/ERROR 行: 2`，是关键字解析的说明，不是失败） |

---

## 3 反向复核：我 t30 的原始判据是否仍成立

t30 我判「worker 的 `isfile` 检查在生产路径上必为 False」。t32 之后该判据的**前提已被推翻**：现在 worker 不再「等文件出现」，而是**自己写文件再索引**（`_write_policy_evidence_for_run`），因此 t30 的 blocker 不再适用；`f775742` 的 worker 生成路径由我在 §2.1 用生产函数实跑验证。为避免「自证正确」，我同时保留 t30 的结论作为**历史事实**（当时 9/9 文件 mtime 晚于 run 结束、0/9 索引），并明确：**归档 9 行的索引是回填**，不是当初落盘 —— 这一点 t32 在 `p1-batch.md:3` 与 `p1-t32-backfill.md` 都写明了。

---

## 4 补充观察（**不阻断**，建议 t31 顺手处理）

### O1 · medium · 同一路径现在有两个 schema 的生产者

- **事实**：归档版（抽取脚本生成）payload 键 = `['arm_report_rows','dispatch_rows','label','note','run_id','started_at_epoch','verdict_class']`；worker 版 = `['dispatch_rows','label','note','run_id','started_at_epoch']`（缺 `arm_report_rows`、`verdict_class`）。
- **影响**：`arm_report_rows` 正是我 t15 复算 `index_chars` / `worktrees_after_dispatch` 的来源；未来批次若只看 worker 版文件，这两个字段取不到（**但**同一 `artifacts/` 目录下 `arm_report.jsonl` 仍在，信息没有丢，只是位置变了）。另外 `verdict_class` 在 worker 时刻尚未定稿，属**固有**差异，不能怪实现。
- **建议**：worker 版顺带把同目录 `arm_report.jsonl` 的行读进 `arm_report_rows`，并加一个 `producer` 字段（`worker` / `extractor`）；或在 `eval/README` 写明两种 schema 与各自的字段含义。

### O2 · low · `p1-batch.md` 结尾仍写回填前的 sha256

- 第 190 行「最终批次产物索引：`triple_state.jsonl` 205543 bytes SHA256 `17F3A711…`」是回填**前**的值；第 3 行的回填注已给出前后两值，但正文末尾没有指针，读者可能对不上。
- **建议**：在第 190 行句末加「（该值于 2026-10-13 23:03:49 被回填改写为 `bb89825a…`，见文件头注）」。

### O3 · low · 我在造桩时撞到的一个边角（**不是当前缺陷**）

- 我第一版假 Popen 在 kill 之后**第二次** `communicate()` 也抛异常，结果 worker 在写 console 与证据**之前**就跳进外层 `except Exception` ⇒ 得到 `harness_bug` 行且 `artifacts` 为空（落盘行自然没有索引）。生产上 `Popen.kill()` 之后 `communicate()` 正常返回，所以这是**我桩子的错**，不是 t32 的缺陷。
- **建议**（可选）：把「console 写入 + C 证据生成 + 索引」放进 `finally` 或提前到 `communicate` 之外，使「子进程无法收割」这一极端情况下仍留下取证线索；若不做，至少在文档里承认该分支无 artifacts。

---

## 5 本报告不能证明什么

1. 我**没有**跑真实批次（零模型调用）；② 的验证是用假 Popen 调用**生产函数**，因此它证明的是「生产代码路径会写文件并索引」，**不是**「下一次真实跑批一定成功」。
2. 我**没有**验证 9 条未启动 pluggy 槽的任何行为（仍未启动）。
3. 回填的**字段级 diff** 是我在备份与现文件之间自算的；我**没有**也无法验证 t32 执行回填当刻的中间态（只在事后比对两端）。
4. `p1-batch.md` 的「历史正文未被改写」我是用**锚点字符串仍在**（`### t20-batch 逐行原始索引`、冻结实例 SHA256、state SHA256）与行数推证，**不是**与 t15 时期的字节级比对（该文件在我 t15 审查时是 untracked，且 t26 之后已增补过）。
5. 我**没有**全仓扫描所有消费 `policy_evidence_isolated.json` 的代码（O1 只指出我确知的三个消费点：我的 t15 复算脚本、`p1_recompute.py`、`p1_audit_batch.py`）。

---

## 6 证据索引

| 文件 | bytes | sha256 |
|---|---:|---|
| `eval/reports/p1_review_t33_verify.py`（本审查自写脚本） | 19015 | `45e520debb6d8fbc95c88f405517d93777299b4efa6c788964de0b21a494a7f8` |
| `eval/reports/t33-review-verify.txt` | 6756 | `4a4e2000cefe7d3cde324553a52701b4ac36f16124e8bba4fd3d835e561e44bf` |
| `eval/reports/t33-review-verify.json`（含逐行 field_diffs / 生产桩两案例 / schema 对照） | 10864 | `64a0ca1b3859d4e0edcc41aa7d1989c0c981aa1f710146ae5fe5b27a2d76fcdf` |
| 归档 state（回填后） | — | `bb89825ada4d1eace311376736ab5a2d5cff01a86389bc58cbc623a01d12bbff` |
| 归档 state 备份（回填前） | — | `17f3a7119fa01b20b1fa899c1fda2b3ef2af3f737b7444d9a6de334f17da8c78` |
| `eval-runs/goldcheck-20261013-231814/gold_check_summary.json` | — | `c7b4bdfc07f1aa1d23db62fd604d3f375c0b72fc595985af2bf5872e2a57625a` |
| `eval-runs/mech-20261013-232033/mechanism_check_summary.json` | — | `b81f93af3406b4450aac0e65a8e49a5ca240893ad694f81f089685ee86f47333` |

代码引用（只读）：`eval/harness/scheduler_p1.py`（`_write_policy_evidence_for_run`、`policy_isolated_artifact`、`count_spawned_arms`、`plan_resume`、`_run_one_arm`）、`eval/harness/t29-evidence.md`、`eval/harness/t29_resume_check.py`、`eval/reports/p1_backfill_evidence_index.py`、`eval/reports/p1-t32-backfill.md`、`eval/reports/p1-batch.md:3,190`。
