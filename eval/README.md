# 第三阶段 P0：SWE-bench **语义等价**评测 harness

**一句话**：从真实开源仓库的修复提交 **revert** 出「有缺陷的起点」，让 agent 去修，
再用**独立启动的 pytest** 判定它对没对 —— 全程不依赖 Docker / VM。

判据先于数据写死在 [`PREREGISTRATION.md`](PREREGISTRATION.md)（预注册）。
**本 README 只讲怎么跑；判据一律以预注册为准。**

---

## 0. ⚠ 先读这一节：这不是官方 SWE-bench harness

| | 官方 SWE-bench | 本仓库 |
|---|---|---|
| 运行环境 | Docker 镜像（每实例一套依赖） | **不用 Docker/VM**：每实例一个目录副本 + 一个独立 venv |
| 题目来源 | 官方 issue 原文 | **提交信息（commit message）**，见 §3.4 |
| 依赖安装 | 镜像内固化 | 运行时 `pip install`（缓存仓库声明的依赖） |
| 测试选择 / 超时 | 官方策略 | 本仓库 §5、§8.4 的策略 |

- **未运行官方 harness、未使用官方镜像。** 两边只是**语义等价**（都是「revert 真实修复 →
  用 F2P/P2P 判定」），不是同一套程序。
- ⇒ **本仓库产出的 `resolved` 数字不得与官方 leaderboard 或他人 SWE-bench 子集分数并列比较。**
- **P0 只交付基础设施，尚未产出评测数字。** 交付的是预注册 + 12 条实例 + harness
  本身可信的证据（三向自校验 / 并发隔离 / 不采信自述 / 断点续跑）。

**P0 的已知限制**（完整清单见主 `README.md` 同名节与 `reports/`）：网络未强制隔离；
实例只选自「同一提交同时改源码与测试」的修复 ⇒ 缺陷分布受该筛选条件支配；
**n = 12**、跨 4 个小型纯 Python 库 ⇒ **不外推**；无 Docker ⇒ 环境类失败落进 `infra_error`。

---

## 1. 依赖与解释器

**唯一解释器**（conda 不在 PATH 上，必须用完整路径）：

```
D:\Anaconda3\envs\aicoding\python.exe
```

| 依赖 | 用途 |
|---|---|
| `pytest`（本机 9.1.1） | 判定。**必须能 import `pytest-json-report`**，否则结果解析为空 |
| `pytest-timeout` | 判定超时（§8.4） |
| `pytest-subtests` | 它的 `subtests passed` 会被归一成 `passed` |
| `git` | `E:\Program Files\Git\cmd\git.exe` |

harness 自己按实例的 `install_cmd` 给每个实例建 venv 装依赖，**不用你手动装**。
但**跑 harness 的那个解释器**要有 `pytest-json-report` + `pytest-subtests`（解析要用）。

⚠ 本机 git 全局 `core.autocrlf=true`；harness 的**所有** git 调用都显式钉了
`-c core.autocrlf=false -c core.safecrlf=false`，不要绕过它去手敲 git。

---

## 2. 复现步骤（三条命令，可照抄）

以下命令一律在项目根 `E:\aicoding\mini-claude-code` 下执行。把 `$PY` 设成上面那个完整路径。

### 2.1 三向自校验（先跑这个 —— 它证明 harness 本身没坏）

```powershell
Set-Location E:\aicoding\mini-claude-code
$PY = 'D:\Anaconda3\envs\aicoding\python.exe'
& $PY eval/gold_check.py --run-id goldcheck-run1
```

对 12 条实例各跑**三个方向**，逐条期望命中才算通过：

| 方向 | 补丁 | 期望 |
|---|---|---|
| gold | 真实修复 | `agent_result` / **resolved = True** |
| empty | 空补丁 | `empty_patch` / resolved = False |
| wrong | 错补丁 | 能干净 apply 但结果 **unresolved** |

**通过 = exit 0**，并输出「12/12 实例三向全对」。结论 JSON 落在
`E:\aicoding\eval-runs\<run-id>\gold_check_summary.json`。

> ⚠ 已知限制：其中 **3 条 tabulate 实例**的第 3 向与第 2 向同状态、**少一向判别力**
> （原因见 `reports/p0-verification.md`）。harness 层「干净 apply 但改错 ⇒ unresolved」
> 的判别力由**其余 9 条**证成。`gold_check` 会显式打出该说明并写入 `wrong_direction_caveat`。

### 2.2 单实例

```powershell
& $PY eval/harness/run_instance.py --instance pallets-click__4fd2fea --patch gold --run-id single1
```

`--patch` 取 `gold` / `empty` / `wrong` / `none`；`--timeout-probe` 用于实证「超时先于退出码」
（把判定超时压到 **0.2s**，必须低于 pytest 启动时间）；`--keep-workspace` 判定后不删工作区。

### 2.3 并发批次（+ 断点续跑）

```powershell
& $PY eval/harness/scheduler.py --run-id batch1 --set pilot --patch-kind gold --max-workers 4
# 中断后原样再跑一次，加 --resume：跳过 state.jsonl 里已判完的
& $PY eval/harness/scheduler.py --run-id batch1 --set pilot --patch-kind gold --max-workers 4 --resume
```

`--set pilot` = 正式集 12 条；`--set reserve` = 连 reserve 一起。
`--instance <id>` 可重复传，用于只跑某几条。

### 2.4 机制验证（可选，四组不变量）

```powershell
& $PY eval/harness/mechanism_check.py --run-id mech1
```

四组：超时优先级 / 测试侧剥离 / 并发隔离 / 断点续跑。全 PASS 才算通过。

---

## 3. 目录约定（**全都在仓库之外**，不进版本历史）

| 路径 | 内容 | 磁盘现状（2026-10-13 实测） |
|---|---|---|
| `E:\aicoding\eval-cache\` | 上游仓库克隆（harness **只读**，绝不往里写） | **0.23 GB**（5 个仓库 + `_artifacts`） |
| `E:\aicoding\eval-runs\` | 运行工作区 + 证据（harness 唯一的写目标） | **0.15 GB** |
| `E:\aicoding\eval-venvs\` | 每实例独立 venv | **4.95 GB**（209 个 venv，单实例约 24 MB） |

- **仓库里只有代码与实例定义**（`eval/instances/*.json`）；缓存/工作区/venv 一律落上面三处。
- ⚠ **venv 会累积**：t8 起 venv 按「实例 + run」隔离（修竞态，见 §5），
  同一个实例每换一个 `--run-id` 就多一个 venv。**跑大批次前先看 `eval-venvs` 还剩多少盘**，
  不需要的旧目录可以直接删（删了会自动重建，代价约 5 s）。

---

## 4. 判定口径（摘要，完整以预注册为准）

- **`resolved` ⟺** 在「`base_commit` + `test_patch`」的工作区上，由 harness 独立启动的 pytest
  跑出：每条 `FAIL_TO_PASS` 都 `passed` **且** 每条 `PASS_TO_PASS` 都 `passed`，
  且 `verdict_class == "agent_result"`。
- **六分类**：`agent_result` / `empty_patch` / `patch_conflict` / `infra_error` / `timeout` / `harness_bug`。
  分母只算前三类；**`infra_error` 与 `timeout` 不算 agent 失败**（它们是环境/程序的问题）。
- **候选补丁由 harness 自己从工作区采集**（`git diff`），**不接受 agent 自贴的 diff**；
  补丁里若动了测试侧文件，按 §5.3 **剥离后再判定**（「试图改测试」单列统计，不是判定输入）。
- **agent 的自述对判定零影响。** 实测反例：t5 冒烟里 agent 自评「未成功」，
  而独立判定是 `resolved=True`（F2P 5/5、P2P 60/60）。

---

## 5. 常见故障处置

### 5.1 依赖装不上 / venv 里没有 pytest
- 实例的依赖来自它的 `install_cmd`。**注意它是「一个字符串」不是 token 列表** ——
  早期实现直接当列表用 ⇒ 解析出空依赖 ⇒ venv 里没 pytest ⇒ 全部判成 `infra_error`。
  现在会 `shlex.split` 并**两种形状都吃**。
- 处置：看该实例的 `artifacts/venv.log`（里面有完整 `pip install` 输出）。
  确认建 venv 用的解释器是上面那个 `D:\Anaconda3\envs\aicoding\python.exe`。

### 5.2 editable 挂载 / `.pth` 隔离（**坑最深的一条**）
被测包常常**本身就是 pytest 的依赖**（例如 `pluggy`）。此时 site-packages 里的
**PyPI 副本会压过工作区** ⇒ agent 改工作区代码，pytest 却加载 PyPI 的那份，
**判定就变成在测一个根本没被修改的包**。

处置（已实现，别绕开）：
- 用**import 行式 `.pth`**（`sys.path.insert(0, <包父目录>)`）把工作区插到最前。
- **不能 `pip uninstall` 那个包** —— 会让 pytest 直接报
  `PackageNotFoundError: No package metadata was found for pluggy` 而崩。
- ⚠ **`.pth` 在解释器启动时按文件名排序逐个执行**，每个都 `insert(0, ...)`
  ⇒ **最终谁排最前由文件名排序决定，与「哪个 run 正在跑」无关**。
  所以「一个 venv 里挂两个工作区」必然有一个 run 拿到别人的代码。
  **t8 的修法是让 venv 按「实例 + run」隔离**（消掉共享可变状态本身），
  而不是去调文件名顺序。若你看到 `隔离失败：... 的导入路径不在本实例工作区内`，
  说明有东西绕过了这条隔离，按 `reports/p0-verification.md` §4 的办法复现。
- venv 里**只应有一个** `_eval_editable_*.pth` 指向本 run 的工作区。

### 5.3 判定超时
- 默认单实例判定墙钟 600 s（`--judge-timeout`）。
- **超时标记先于退出码判定**（§8.4）：Windows 上被 kill 的进程以 exit 1 结束，
  与「pytest 跑了但测试失败」**无法靠退出码区分**。顺序反了会把 `timeout` 记成 `not_resolved`。
- 想实证这条机制：`--timeout-probe`（压到 0.2 s）。**必须压得比 pytest 启动还短**，
  否则判定会在超时前跑完 —— 那就「什么都没验证到」却显示通过（实现里踩过两次）。

### 5.4 测试 flaky
- 判定结果里 `passed` 是唯一「绿」。`skipped` / `xfail` 都不算 passed。
- 若某条 P2P 偶发失败，先看 `artifacts/verdict_run.txt` 的 per-node 列表；
  harness **不重试判定**（重试会让「跑了几次才过」变成不可解释的口径）。
- 实例层面的 `infra_error` 会自动重试（`MAX_INFRA_RETRIES=2`），
  但**判定结果本身不重试**。

### 5.5 续跑（`--resume`）
- 状态在 `eval-runs/<run_id>/state.jsonl`（逐行 fsync）。
- **跳过已完成的判据是该行的 `stage` 字段** —— 早期实现只写 `state.json` 不写这一列，
  导致续跑永远跳过不了、全部重跑（已修）。
- 续跑**不改写**已完成结论；同一实例在 `state.jsonl` 里只会有一条 `judged` 行。

---

## 6. 文件结构

```
eval/
  PREREGISTRATION.md      预注册（判据，先于数据写死，冻结）
  README.md               你在这里
  build_subset.py         实例构造器（幂等；--verify-all 校验全部实例定义）
  gold_check.py           黄金三向自校验
  instances/              23 条实例 JSON（12 条 pilot-12 + 11 条 reserve）+ 抽样日志 + 排除记录
  harness/
    env.py                工作区与 venv 生命周期（§8.1/§8.2）
    judge.py              补丁拆分、六分类、resolved 判据（§5）
    run_instance.py       单实例全链路（工作区 → venv → 施加补丁 → 独立判定）
    scheduler.py          进程池并发 + 断点续跑（§8.3/§8.5）
    mechanism_check.py    四组机制验证
  reports/                复核与审查报告 + 各自脚本（t4/t5/t8/t9）
```

构造实例（若 `eval/instances/` 为空时需要先做）：

```powershell
& $PY eval/build_subset.py --scan          # 只看候选池统计
& $PY eval/build_subset.py --build         # 构造（幂等，不覆盖已存在的 JSON）
& $PY eval/build_subset.py --verify-all    # 校验全部实例定义
```

---

## 7. 这份 harness 自己可信吗

P0 的重点不是「跑出好成绩」，而是**证明判定本身可信**。已做的四类证据：

1. **三向自校验** 12/12（`gold_check.py`）—— harness 修坏时这个会红。
2. **并发隔离**：同一实例并发两个 run，修复后 **0 infra、0 静默误判**，
   每个 venv 只有一个挂载点且指向自己的工作区；不同实例并发与串行**逐条一致**。
3. **不采信自述**：自造负向样本（补丁缺失 / 应用失败 / 故意改错但能干净 apply / 只改测试）
   **0 条被判 resolved**；阳性对照（真 gold）**全部 resolved** —— 证明上面的 False
   不是判定瘫痪导致的。
4. **断点续跑**：真 `taskkill` 强杀后 `--resume`，不重复、不改写已完成结论、无半行损坏。

三条独立报告（各自作者、与执行侧不同）：
`reports/p0-verification.md`（t4 独立复核）、`reports/p0-smoke.md`（t5 端到端冒烟）、
`reports/p0-review-t9.md`（t9 独立审查，**verdict = pass**）。

## 8. P1 产物 schema 与批次记录约定

P1 的 C 臂证据存在两种明确 schema，按生产者读，不得混用：**worker 版**由生产路径 `_run_one_arm` 生成，`policy_evidence_isolated.json` 可能不含 `arm_report_rows` / `verdict_class`；读取这两个字段时，必须从**同一 run 目录**的 `arm_report.jsonl` 取值。**归档 27 行版**是抽取/回填版，字段更全；其 `artifacts` 索引可能由 `eval/reports/p1_backfill_evidence_index.py --apply` 事后追加，不能误读为当时落盘。另有已知非生产分支：若 kill 后第二次 `communicate()` 抛异常，worker 会在写 console/证据前进入外层 `except`，该 `harness_bug` 行的 `artifacts` 为空；生产不会发生，属于已承认的边界而非当前缺陷。

共享批次记录 `reports/p1-batch.md` 只允许在头部或尾部追加一节；不得改写既有结论行与表格数值。历史记录与后续回填必须通过新增说明保持可追溯。