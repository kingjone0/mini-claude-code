# t30 独立复验：resume 终态语义修复 + state 行 artifacts 索引（review round 2）

> **被审任务**：t29（`xi-harness`，非本审查者实现 ⇒ 无自审冲突）
> **被审提交**：`c0262c43f968389127812460328ceed5f936798c`（`P1: make timeout and infra rows terminal on resume`，2026-10-13 22:42:53 +0800）
> **审查时 HEAD**：`c0262c43f968389127812460328ceed5f936798c`
> **本审查未改任何实现代码**：只写 `eval/reports/`；所有实验都在临时目录
> **零模型调用**：复验脚本不加载实例、不建 venv、不 spawn `coding_agent.py`；用量文件在 t29 提交后无新增行
> **verdict = `needs_revision`**（1 blocker + 2 low：F5 复验通过，F6 在生产路径上未生效）

---

## 0 结论

| 验收项 | 结论 |
|---|---|
| ① F5 独立复验（核心，含反向对照） | **PASS** —— 修前逻辑复现出「timeout 槽被重跑 ⇒ 终态被改写」，修后真实 `run_p1_batch(resume=True)` 桩 pending 为空、行数恒 3、sha256 **逐字不变** |
| ② 终态不可改写（不追加第二行 + sha256 不变 + 修前/修后两次输出对照） | **PASS** |
| ③ F6 代码部分生效（state 行 `artifacts` 含 run 相对路径、结论字段不变） | **FAIL** —— `0/9` 现有行含该索引；该代码路径在生产时序上**不可达** |
| ④ 未越界（只改 `eval/harness/`、未碰 reports/预注册/9 槽/实例、零模型调用） | **PASS** |
| ⑤ 回归（gold 12/12、mechanism 全 PASS、selfcheck 0 失败） | **PASS** |

一句话：**F5 修得对、而且我用自己的反例反着验过两次；但 F6 那三行索引代码在真实跑批里永远不会执行** —— `policy_evidence_isolated.json` 是**跑完之后**由 `eval/reports/p1_extract_evidence.py`（事后抽取脚本，不在 t29 的改动范围内）生成的，而 worker 在 `scheduler_p1.py:337` 检查 `os.path.isfile(isolated)` 的时刻该文件还不存在。9/9 现有 C 行的文件 mtime 都晚于各自 run 结束时刻，state 行里仍然 `0/9` 含索引 ⇒ 验收第 ③ 条按字面不成立。

---

## 1 方法与证据

复验脚本（自写，**不复用** t29 的 `t29_resume_check.py`）：`eval/reports/p1_review_t30_resume.py`
→ 输出 `eval/reports/t30-review-verify.txt` / `.json`

```text
Set-Location E:\aicoding\mini-claude-code
& 'D:\Anaconda3\envs\aicoding\python.exe' -X utf8 eval/reports/p1_review_t30_resume.py
```

对照用到的 t29 自有证据：`eval/harness/t29_resume_check.py`（离线桩）、`eval/harness/t29-evidence.md`。

---

## 2 验收逐条

### ① F5 独立复验（核心）—— PASS

**反向对照（修前）**：从 git 取 `4f15fa2:eval/harness/scheduler_p1.py`，逐字复刻修前的跳过条件，并核对同一行在 HEAD 的命中数：

```text
修前（4f15fa2）该行 = if prev and prev.get("stage") == "judged":
HEAD 里同一行命中数 = 0（应为 0）
修前分类：skipped=['A','C']  pending=['B']   ⇒ timeout 槽被当作待跑
修前 resume 的效果（复刻）：行数 3 → 4；读回 B = agent_result/True
                          ⇒ 终态被改写（timeout/False → agent_result/True）
```

**修后**：对**同一份** 3 行状态（A `judged`/`agent_result`、B `failed_infra`/`timeout`、C `judged`/`agent_result`）调用生产函数 `plan_resume()`：

```text
修后分类：skipped=['A','B','C']  pending=[]  skipped_failed=1   ⇒ timeout 槽被当终态
```

**代码与注释一致**（读代码确认）：`scheduler_p1.py:124-139` 的 `plan_resume` 把 `stage ∈ {judged, failed_infra}` 或 `verdict_class ∈ {timeout, infra_error, harness_bug}` 视为终态，并单独计 `skipped_failed`；`run_p1_batch` 的注释写死「补跑必须走新批次（新的预注册与预算）；`--resume` 不得重跑已有终态行」（`:382`）。

### ② 终态不可改写 —— PASS

用**真实的** `run_p1_batch(resume=True)` 桩（`pending` 为空 ⇒ 不加载实例、不建 venv、不 spawn）：

```text
manifest：planned_slots=3  skipped_completed=2  skipped_failed=1  runs_done=3
manifest budget.started_arms=38（= REV2_INITIAL_STARTED 35 + count_spawned_arms 3）
state 行数：3（未追加第二行）
修前 sha256=4da810797f3338fd3953b87ab8165897d3b55a1e367568e49ca9c98a3d2f47d0
修后 sha256=4da810797f3338fd3953b87ab8165897d3b55a1e367568e49ca9c98a3d2f47d0  ⇒ 逐字不变 = True
临时 run_root 里新增的文件只有 manifest.json：['manifest.json','triple_state.jsonl']
```

**预算口径（`count_spawned_arms` vs 旧公式）**：

```text
同一 (实例, 臂) 两次实际启动 ⇒ state 2 行
read_triple_state 键数（旧公式输入）= 1
count_spawned_arms（新公式输入）  = 2
新 _budget_snapshot().started_arms = 37；旧公式会给出 36（少记 1）
追加 1 行「准备失败」（spawned=False、无 started_at_epoch）后 = 2（准备失败不计）
遗留行（无 spawned 但有 started_at_epoch）= 1（向后兼容）
```

⇒ t15 的 F5 两个后果（重跑改写终态、预算少记）**都已消除**；`read_triple_state` 现在只用于 resume 的 `prior`（全仓只有 `:380` 一处），预算与 manifest 都走 `count_spawned_arms`。

### ③ F6 代码部分生效 —— **FAIL**

代码本身没有错：`policy_isolated_artifact()` 返回的确实是 run 相对路径
`offline-instance/artifacts/policy_evidence_isolated.json`，且 `scheduler_p1.py:336-339` 只在文件存在时才追加、不触碰任何结论字段。**但它不可达**：

1. **全仓没有第二个写入者**：扫 `eval/harness` 与 `eval/reports` 下所有提到该文件名的 `.py`，唯一的写入点是 `eval/reports/p1_extract_evidence.py:17`（`target=art/'policy_evidence_isolated.json'`）；`eval/harness/scheduler_p1.py:121,337` 是引用/存在性检查，`t29_resume_check.py:81` 是字符串断言。`scheduler_p1` 也从不调用该抽取脚本（全仓 `extract_evidence` 只出现在文档与脚本自身）。
2. **生产时序把存在性检查钉死为 False**：

| C run | run 结束时刻 | isolated 文件 mtime | mtime > 结束 | state.artifacts 含索引 |
|---|---|---|---|---|
| tabulate 5373ebf | 20:33:57 | 21:35:38 | True | False |
| tabulate 87a9a4e | 20:36:07 | 21:35:38 | True | False |
| tabulate 9dedd45 | 20:37:42 | 21:35:38 | True | False |
| more-itertools 1b7f033 | 20:52:42 | 21:35:38 | True | False |
| more-itertools ccf96ac | 20:55:41 | 21:35:38 | True | False |
| more-itertools f8ccab2 | 21:09:52 | 21:35:38 | True | False |
| pallets-click 1b0e19f | 21:19:33 | 21:35:38 | True | False |
| pallets-click 4fd2fea | 21:24:52 | 21:35:38 | True | False |
| pallets-click ac6a2ac | 21:35:17 | 21:35:38 | True | False |

   9/9 的文件都是**事后**（21:35:38）由抽取脚本产生的 ⇒ worker 在运行期检查时文件必然不存在 ⇒ 索引不会被追加。现有 state 行含索引比例 **0/9**。
3. 因此验收「确认该 `(实例,臂)` state 行的 `artifacts` 已含 `policy_evidence_isolated.json`（run 相对路径）」**不成立**；t29 的 `t29-evidence.md:10` 只给了 `F6 run_relative_artifact=...` 这一条**字符串函数断言**，它证明的是「函数返回值正确」，不能证明「索引落进了 state 行」。

### ④ 未越界 —— PASS

```text
git show --name-only c0262c4
  eval/harness/scheduler_p1.py
  eval/harness/t29-evidence.md
  eval/harness/t29_resume_check.py
```

- 只改 `eval/harness/`（`eval/lib/` 未动，符合「只改 …与 eval/lib/」的允许范围）；`git show --name-only | Select-String 'PREREGISTRATION|eval/reports/'` **为空** ⇒ 未碰任何 `eval/reports/` 文档、未碰两份预注册。
- 正式产物未被改：批次 `triple_state.jsonl` `bytes=205543`、`sha256=17f3a7119fa01b20b1fa899c1fda2b3ef2af3f737b7444d9a6de334f17da8c78`，与我 t15 记录的基线**逐字相同**。
- 9 个未启动槽（`20d8143`/`cbf58d5`/`dd20a85`）在 state 里**仍无行**。
- 未重跑实例：`eval-runs` 下最新 `p1-*` 目录创建时间 `2026-10-13 20:27:33`，早于 t29 提交（22:42:53）。
- 零模型调用：`usage.jsonl` 总/p1- = `186/56`、`policy_scout_usage.jsonl` 总/p1- = `55/21`（与 t15 基线相同）；p1- 用量最大 `ts = 2026-10-13T21:32:25+08:00`，**早于提交时刻** ⇒ 提交后无任何新用量。

### ⑤ 回归 —— PASS

| 命令（合同 verify 逐字） | exit | 结果 |
|---|---:|---|
| `python eval/gold_check.py` | 0 | `total=12 passed=12`；逐向 `gold 12/12`、`empty 12/12`、`wrong 12/12`（并对 3 条 tabulate 实例如实打出「wrong_patch 与 gold 逐字节相同 ⇒ 第 3 向净效果=基线」的 caveat）。证据 `E:\aicoding\eval-runs\goldcheck-20261013-224513` |
| `python eval/harness/mechanism_check.py` | 0 | `results = {timeout_priority: true, test_stripping: true, concurrency_isolation: true, resume: true}`。证据 `E:\aicoding\eval-runs\mech-20261013-224734` |
| `python servers/experiments/subagent_selfcheck.py` | 0 | `=== 全部通过（0 项失败）===`（含 A/B/C 臂工具集对照、`read_findings` 注册、policy+no-write 反向对照） |
| `git show --stat --oneline HEAD` | 0 | HEAD = `c0262c4`，3 文件 163 insertions |

> ⚠ 两条命令未带 `-X utf8`（按合同原文执行），控制台是 GBK ⇒ 我把原始 stdout 重定向留档（`E:\aicoding\eval-runs\t30-{goldcheck,mech,selfcheck}.txt`），并从上表引用的 **JSON 汇总**里取数，避免转述。

---

## 3 Findings

### G1 · blocker · F6 的索引代码在生产路径上不可达 ⇒ 验收第 ③ 条不成立

- **问题**：`scheduler_p1.py:336-339` 在 worker 内检查 `os.path.isfile(<art>/policy_evidence_isolated.json)` 后追加索引，但该文件**由跑批之后**的 `eval/reports/p1_extract_evidence.py` 产生（该脚本不改 state、也不是 harness 调用链的一部分）。9/9 现有 C 行的文件 mtime（21:35:38）都晚于各自 run 结束时刻，state 行 `0/9` 含索引。
- **影响**：`state.artifacts` 仍然没有指向 C 臂分组证据的索引；下游（分析/复核/README 证据入库）只能继续靠约定路径猜，F6 想解决的「索引缺口」原样存在。这一条是**结构性证据冒充了生效证据**的同类问题（t2 的教训）：字符串函数正确 ≠ 索引落盘。
- **requiredFix**（三选一，**不得**只改结论字段蒙混）：
  1. 让**抽取脚本**在写 `policy_evidence_isolated.json` 时同步回写「该 run 的 state 行 `artifacts` 加一条 run 相对路径」（抽取脚本在 `eval/reports/`，需 captain 指认 owner，且必须只追加索引、不触碰 `resolved`/`verdict_class` 等结论字段）；
  2. 或把该证据文件的生成搬进 worker（在 C 臂 worker 内收集本 run 的 dispatch 行），使其在索引时刻已存在；
  3. 或对已归档的 27 行做**显式回填**（单独一段脚本 + 只追加索引 + 记录 sha256 前后对照），并在报告里写明「回填」二字。
- **file/line**：`eval/harness/scheduler_p1.py:336-339`、`eval/reports/p1_extract_evidence.py:17`

### G2 · low · `t29-evidence.md` 的 F6 条款把函数断言写成了生效证据

- **问题**：`eval/harness/t29-evidence.md:10` 的 `F6 run_relative_artifact=offline-instance/artifacts/policy_evidence_isolated.json` 来自 `t29_resume_check.py:80-82` 的字符串相等断言，未在任何 state 行上验证索引落盘，措辞容易被读成「F6 已生效」。
- **requiredFix**：把该行改成「函数返回值正确（未在生产 state 行验证落盘）」，并在 G1 修好后补一条真实 state 行的索引断言。

### G3 · low · `artifacts` 里混入一种 run 相对路径，语义与其余条目不一致

- **问题**：`row["artifacts"]` 其余条目都是绝对路径（`console_*.log`、`judged.json`、`verdict.json`…），新增的这一条是 `"<iid>/artifacts/policy_evidence_isolated.json"`。验收要求正是「run 相对路径」，所以**不是判据违反**；但下游读者需要知道两种形态并存。
- **requiredFix**：在 `policy_isolated_artifact()` 的 docstring 或一份「artifacts 字段规范」里写明「除本索引外均为绝对路径；本索引以 run_root 为基准」，或提供一个反向解析函数（`eval/reports/p1_audit_batch.py:12` 现在按 basename 匹配，能兼容，但那是耦合而非规范）。

---

## 4 本报告不能证明什么

1. 我**没有**在真实跑批上验 F6（那需要新的模型预算）；我的结论是「按生产时序与唯一写入者推断该分支不可达」，并由 9/9 的文件 mtime 与 `0/9` 的索引状态佐证。
2. 我**没有**重跑任何实例、没有消费任何臂槽；回归用的是离线 gold/empty 与机制桩。
3. F5 的复验是**离线桩**（`pending` 为空的分支）；我没有在「部分槽完成 + 部分槽未启动」的真实批次上跑 `--resume`（那样会启动模型调用）。
4. 我**没有**核对 t29 改动是否会改变 manifest 消费方的兼容性（`skipped_completed` 语义从「全部跳过」变成「已完成跳过」，并新增 `skipped_failed`）；`eval/reports/p1-batch.md` 与 README 若引用了旧语义需在 t31 一并订正 —— 这一点我只指出风险，未做全仓引文扫描。

---

## 5 证据索引

| 文件 | bytes | sha256 |
|---|---:|---|
| `eval/reports/p1_review_t30_resume.py`（本次自写复验脚本） | 16853 | `252168c81ca11e53454913bb2352be5d27d331a2b29db348a4c6f6e35b310656` |
| `eval/reports/t30-review-verify.txt` | 6251 | `70d76030f4c769195dc5054c862f8ccce201a18cb6894196469df12ec36c212b` |
| `eval/reports/t30-review-verify.json` | 5478 | `01442b1429b36d3e9c285a39fada3295deb1ca734a52f2e730c4e9fe6f031704` |
| `eval-runs/goldcheck-20261013-224513/gold_check_summary.json` | 17449 | `973859f5b787b4cbbad49df2e1b0de6e81841f0eb15059d7f2afd044e1d6738f` |
| `eval-runs/mech-20261013-224734/mechanism_check_summary.json` | 286 | `224f97d2806be37f03164ce17874e96c6baa58bb244d47a3d206b35d565e56df` |
| 批次 `eval-runs/p1-20261013T122356-t20-batch/triple_state.jsonl` | 205543 | `17f3a7119fa01b20b1fa899c1fda2b3ef2af3f737b7444d9a6de334f17da8c78`（与 t15 基线一致） |

代码引用（只读）：`eval/harness/scheduler_p1.py:93-115`（`count_spawned_arms`）、`:118-121`（`policy_isolated_artifact`）、`:124-139`（`plan_resume`）、`:178-183`（`_budget_snapshot`）、`:295-322`（`spawned` 标记）、`:333-341`（C 证据索引）、`:380-383`（resume 接缝）、`:516-527`（manifest）；`eval/reports/p1_extract_evidence.py:17`（唯一写入者）；`eval/harness/run_instance.py:199-225`（`wrong_patch_plan`）。
