# P0 独立复核报告（t4）

- **复核者**：runner2（独立复核者，只跑批与核对，**未改任何实现代码**）
- **attempt_id**：`df95572b-fa7d-40d2-8d5d-b56934f739c1`
- **复核对象**：t2 实例构造器 + 12 条 pilot-12 实例；t3 容器无关 harness（`eval/harness/*`、`eval/gold_check.py`）
- **被复核版本**：`3b906c1`（harness）/ `2a41e92`（实例）/ `27adbdd`（预注册）；本次复核期间工作树未改动这两部分
- **复核纪律**：**不采信 t2/t3 自述**。每一项都自己跑、自己读代码、自己看原始输出；判定只认**独立 pytest 的退出码与 node id**。
- **所有原始证据落** `E:\aicoding\eval-runs\`（各脚本的 JSON 与 console 输出留档）；复核脚本落 `eval/reports/_t4_scripts/`。

## 0. 结论先说

| 项 | 结论 |
|---|---|
| 实例集（provenance、红/绿） | ✅ **通过**。12/12 条三路一致，`base_commit == fix_commit^` 全对 |
| 黄金三向自校验 | ✅ **通过**。独立重跑 12/12 三向全对，与 t3 自述一致 |
| 「判定不采信自述」 | ✅ **通过**。8 条负向样本 **0 条被判 resolved** |
| 断点续跑 | ✅ **通过**。真强杀后恢复，`skipped_completed=1`，结论未被改写 |
| 基础设施错误分类 / 超时优先 | ✅ **通过**。timeout/infra 均正确归类且不进分母 |
| **并发隔离** | ❌ **发现阻断级缺陷**：共享 venv 内 `.pth` 竞态（**可复现，4/6 轮触发**），其中含**静默误判** |
| 交叉核对方法学 | ⚠ 另发现 t3 `mechanism_check.py` 的并发断言**恒真（空证）** |

**P0 可交付性判定：当前 pilot-12 的评测数字可用；但「并发隔离」这条不变量不成立，必须先修 `.pth` 竞态再宣称 harness 完全达标。** 详见 §4 与 §7。

---

## 1. 抽样独立重跑：红/绿与 node id 逐条核对

**验收要求**：自己重跑 ≥4 条实例的「红/绿」判定（覆盖 ≥2 个仓库），逐条核对 `FAIL_TO_PASS` 的 node id 与退出码；核对 `PASS_TO_PASS` 在基线与 gold 下都通过。

### 1.1 独立 runner（完全不复用 harness 代码）

`eval/reports/_t4_scripts/t4_independent_redgreen.py` 自己实现了一整条链路：`git archive` 建工作区、自己写 import 行式 `.pth`、自己跑 pytest、自己按 §5.1 判 F2P/P2P。**与 harness 的共同点只有外部契约**（目录约定、node id 形式、判据），代码路径独立 ⇒ 可作交叉核对。

```
python eval/reports/_t4_scripts/t4_independent_redgreen.py E:\aicoding\eval-runs\t4\pilot_ids.json E:\aicoding\eval-runs\t4\independent -
```

**结果：12/12 条全 OK**，覆盖全部 **4 个仓库**（超出「≥4 条、≥2 仓库」的要求）：

| 实例 | T 观测点 F2P 全红 | G 观测点 resolved | T.exit | G.exit | T.P2P 未过 | G.P2P 未过 |
|---|---|---|---|---|---|---|
| astanin-python-tabulate__5373ebf | True | True | 1 | 0 | 0 | 0 |
| astanin-python-tabulate__87a9a4e | True | True | 1 | 0 | 0 | 0 |
| astanin-python-tabulate__9dedd45 | True | True | 1 | 0 | 0 | 0 |
| more-itertools-more-itertools__1b7f033 | True | True | 1 | 0 | 0 | 0 |
| more-itertools-more-itertools__ccf96ac | True | True | 1 | 0 | 0 | 0 |
| more-itertools-more-itertools__f8ccab2 | True | True | 1 | 0 | 0 | 0 |
| pallets-click__1b0e19f | True | True | 1 | 0 | 0 | 0 |
| pallets-click__4fd2fea | True | True | 1 | 0 | 0 | 0 |
| pallets-click__ac6a2ac | True | True | 1 | 0 | 0 | 0 |
| pytest-dev-pluggy__20d8143 | True | True | 1 | 0 | 0 | 0 |
| pytest-dev-pluggy__cbf58d5 | True | True | 1 | 0 | 0 | 0 |
| pytest-dev-pluggy__dd20a85 | True | True | 1 | 0 | 0 | 0 |

判定口径（与预注册 §5.1 一致）：T = `base + test_patch`（期望 F2P 红），G = `base + test_patch + gold`（期望 F2P 全绿 ∧ P2P 全绿）。**T 的退出码全部为 1、G 全部为 0**，与「test_patch 让 F2P 变红、gold 让它变绿」完全吻合。

### 1.2 三路交叉：与 t2 自述、与 t2 归档原始日志比对

为避免「只看自己一条链路」，另外从 **t2 归档的原始 pytest JSON report**（`eval-cache/_artifacts/<repo>/<iid>/{T1,G1,T2,G2}.json`）**独立重算**一遍（`t4_archived_recompute.py`，不读任何自述文本）：

```
重算一致：12/12；不一致 0
```

逐条核对 F2P 的 **node id 与 outcome**，例如：

- `pallets-click__4fd2fea`：T1 中 5 条 F2P 全为 `failed`（`test_flag_value_is_correctly_set[opts1-True-True]` … `[opts9-True-True]`），G1 中 5 条全为 `passed`，P2P 未过 0/60。
- `pytest-dev-pluggy__dd20a85`：T1 中 6 条 F2P（`testing/test_helpers.py::test_varnames_*` 与 `testing/test_warnings.py::test_hookspec_missing_self_warns`）全 `failed`，G1 全 `passed`。
- `more-itertools-more-itertools__1b7f033`：`tests/test_more.py::TestConcurrentTee::test_concurrent_consumers` 在 T1 `failed`、G1 `passed`。

三路（gold_check / 我的独立 runner / t2 归档重算）**逐条一致，无一处不符**。**与构造器/t2 的结论不一致之处：无。**

### 1.3 P2P 在**基线**（B 观测点）也全绿

验收要求「核对 `PASS_TO_PASS` 在基线与 gold 下都通过」。前面 T/G 是含 test_patch 的观测点；这里补第三个点：**纯 `base_commit`、不施加任何补丁**，只请求 P2P 的 node（`t4_baseline_observation.py`）。

```
[pallets-click__4fd2fea] P2P_green_at_baseline=True (P2P未过=0/60)  exit=0
[pytest-dev-pluggy__cbf58d5] P2P_green_at_baseline=True (P2P未过=0/60)  exit=0
[more-itertools-more-itertools__1b7f033] P2P_green_at_baseline=True (P2P未过=0/60)  exit=0
[astanin-python-tabulate__9dedd45] P2P_green_at_baseline=True (P2P未过=0/60)  exit=0
B 观测点（纯基线，只请求 P2P）：4/4 条跑通
  P2P 在基线全绿：4/4
```

⇒ **P2P 在 B、T、G 三个观测点均 0 失败**。

> **⚠ 方法学留痕（我自己踩的坑，如实记录）**：本脚本第一版把 F2P **和** P2P 一起塞进基线请求，结果 4/4 条 `exit=4`、`nodes_collected=0`，看上去像「P2P 在基线全不绿」。查原始 stderr 才看清是 `ERROR: not found: ...test_flag_value_is_correctly_set` —— F2P 的参数化是 `test_patch` **新增**的，纯基线上根本不存在，pytest 因此以 **exit 4（usage error）**退出。**这是请求了不存在的 node，不是测试失败**；改成只请求 P2P 后 exit 0、60/60 全绿。留此记录以说明「exit 4」与「P2P 红」不可混淆。

---

## 2. 实例 provenance：`base_commit` 与 gold/test_patch 是否真是该提交的部分

### 2.1 `base_commit == fix_commit^`：12/12 全对

用缓存仓库独立跑 `git rev-parse <fix_commit>^` 并与实例声明的 `base_commit` 比对：

```
more-itertools-more-itertools__1b7f033  base==fix^ : True (declared=4cbe6f9d parent=4cbe6f9d)
pallets-click__4fd2fea                  base==fix^ : True (declared=9ce34f20 parent=9ce34f20)
pytest-dev-pluggy__dd20a85              base==fix^ : True (declared=1990b19c parent=1990b19c)
astanin-python-tabulate__5373ebf        base==fix^ : True (declared=8f320866 parent=8f320866)
pallets-click__1b0e19f                  base==fix^ : True (declared=499bbeea parent=499bbeea)
more-itertools-more-itertools__f8ccab2  base==fix^ : True (declared=5c8336c5 parent=5c8336c5)
```

### 2.2 `patch` / `test_patch` 与 `git show <fix_commit>` 的逐字节比对

初次比对显示「不相等」，**必须查清是缺陷还是我的比对方式问题**。逐行 diff 后定位：

```
--- declared
+++ real
@@ -2 +2 @@
-index 6986b6008176ca706ceb5811c96b0f1244db2da2..ad093b722585902b14faffeaa391c9a93fbcbe70 100755
+index 6986b60..ad093b7 100755
```

**唯一差异是 `index` 行的 SHA 缩写（40 位 vs 7 位）**，其余正文逐字节相同。对全部 12 条 × 2 个字段做了**差异分类**（`t4_patch_vs_gitshow.py`）：

```
每条 patch / test_patch：other_diffs=0   （index 行差异除外，无任何内容差异）
index 归一后仍不等（真实分歧）的条目数：0
```

为确认分类器本身没坏（避免「分类器坏了 ⇒ 全 0 差异 ⇒ 假通过」），另写了**负对照自检**（`t4_classifier_selftest.py`）：

```
normalize_index_lines -> 'index 6986b60..ad093b7 100755'
classify(only-index): {'index_abbrev': 2, 'other': 0}
classify(content-diff): {'index_abbrev': 0, 'other': 2}
SELFTEST PASS
```

⇒ **结论：gold `patch` 确为修复提交的源码部分、`test_patch` 确为其测试部分，逐字节忠实**；`index` 缩写差异是构造器落盘形态（完整 SHA）与 `git show` 默认缩写不同，**不影响补丁语义**（`git apply` 只按上下文行匹配）。**不作为缺陷计。**

### 2.3 F2P 的 node id 落在 test_patch 覆盖的测试文件内

`f2p_files_covered_by_test_patch`：**12/12 全为 True**。抽样：

| 实例 | F2P 所在文件 | 该文件在 test_patch 中 |
|---|---|---|
| more-itertools-more-itertools__1b7f033 | `tests/test_more.py` | ✅ |
| pallets-click__4fd2fea | `tests/test_options.py` | ✅ |
| pytest-dev-pluggy__dd20a85 | `testing/test_helpers.py`, `testing/test_warnings.py` | ✅ |
| astanin-python-tabulate__5373ebf | `test/test_output.py` | ✅ |

另核对这些测试文件在 `fix_commit` 的树里**确实存在**（`git show <fix>:<path>` 全部 rc=0）。

---

## 3. 黄金三向自校验：独立跑 + 独立构造空/错补丁

### 3.1 自己跑 `eval/gold_check.py`（三向全对）

```
Set-Location E:\aicoding\mini-claude-code
& 'D:\Anaconda3\envs\aicoding\python.exe' eval/gold_check.py --run-id t4-goldcheck-final
```

**exit 0**，关键原始输出：

```
三向自校验结果：12/12 实例三向全对
逐向统计：gold 通过 12/12，empty 通过 12/12，wrong 通过 12/12
三向全对 => harness 对本批实例可用（§6.4）。
```

逐条（gold 向的 F2P/P2P 计数均满分）：

| 实例 | gold | empty | wrong |
|---|---|---|---|
| astanin-python-tabulate__5373ebf | agent_result/True (F2P 1/1, P2P 60/60) | empty_patch/False | agent_result/False (F2P 0/1) |
| more-itertools-more-itertools__1b7f033 | agent_result/True (1/1, 60/60) | empty_patch/False | agent_result/False (0/1) |
| pallets-click__4fd2fea | agent_result/True (5/5, 60/60) | empty_patch/False | agent_result/False (0/5) |
| pytest-dev-pluggy__dd20a85 | agent_result/True (6/6, 60/60) | empty_patch/False | agent_result/False (0/6) |
| …（其余 8 条同型） | agent_result/True | empty_patch/False | agent_result/False |

**与 t3 自述的一致性：一致。** 与 §1 我的独立 runner、与 t2 归档重算也一致。

### 3.2 **独立构造**空补丁与错补丁（不只用它自带样本）

`t4_independent_directions.py` 自己造了 3 类候选补丁 × 3 个仓库（12 条）：

```
[pallets-click__1b0e19f]   IND-EMPTY            verdict=empty_patch     resolved=False
[pallets-click__1b0e19f]   IND-WRONG1_...       verdict=patch_conflict  resolved=False
[pallets-click__1b0e19f]   IND-WRONG2_inverted  verdict=patch_conflict  resolved=False
[pallets-click__1b0e19f]   IND-COMBINE_test_only verdict=empty_patch    resolved=False
（pytest-dev-pluggy__20d8143 / more-itertools-more-itertools__1b7f033 同型）
独立构造 12 条；被判 resolved 的：0（必须 0）
```

- **IND-EMPTY**（真零字节）⇒ `empty_patch`，`resolved=False` ✅
- **IND-COMBINE**（只改测试，剥光后为空）⇒ `empty_patch` ✅
- **IND-WRONG1/WRONG2** ⇒ 未 resolved ✅（这两条我的构造没能干净 apply，落到 `patch_conflict`；**这不影响「未 resolved」的结论**，且它们在 §4.2 的 falsification 里已被「能干净应用」的版本覆盖）

### 3.3 第 3 向的判别力限制（t3 已如实标注，我独立确认）

`gold_check` 显式打出：

```
⚠ 第 3 向判别力说明（3/12 条命中）：
  - astanin-python-tabulate__5373ebf / __87a9a4e / __9dedd45：
    wrong_patch 与 gold 逐字节相同 => 第 3 向净效果 = 基线（与第 2 向同状态），该向不提供额外判别力
```

**独立确认**：这 3 条 `wrong` 向 `F2P=0/N`、`resolved=False`，与 `empty` 向同状态。它们**判对了**（都是 unresolved），但**与第 2 向重复**。根因在构造侧（t2 落盘的是 gold 正文 + `wrong_patch_method=reverse_of_gold` 标记，而非「更深一层的缺陷」），harness 按冻结语义施加是正确处置。**我认同 t3 的如实标注，并建议 t6 作为独立结论记录。**

---

## 4. 并发隔离：❌ 发现阻断级缺陷（可复现）

### 4.1 ≥4 并发 vs 串行：逐条一致 ✅（这部分通过）

`t4_concurrency.py` 同批 4 条（4 个仓库各一条）分别串行与 4 并发：

```
OK   pallets-click__4fd2fea              串行 agent_result/True  F2P未过=[] P2P未过=0 exit=0
                                         并发 agent_result/True  F2P未过=[] P2P未过=0 exit=0
OK   pytest-dev-pluggy__cbf58d5          串行/并发 均 agent_result/True, P2P未过=0
OK   more-itertools-more-itertools__1b7f033  串行/并发 均 agent_result/True, P2P未过=0
OK   astanin-python-tabulate__9dedd45    串行/并发 均 agent_result/True, P2P未过=0
串行 vs 并发 差异：[]     工作区唯一 4/4     venv 唯一 4/4
```

⇒ **不同实例并发的场景是干净的**（下次确认：23 条实例 → 23 个互异 venv，见 §4.3）。

### 4.2 「同一实例被并发触发两次」：🔴 写坏了，且含静默误判

**根因（读代码定位 + 实测确认）**：

1. venv 路径 = `eval-venvs/<repo_key>-<base_commit[:8]>`（`eval/harness/env.py:venv_dir`）——**同一 base 的多条实例共享一个 venv**；
2. editable 挂载文件名 = `_eval_editable_<sha256(instance_id)[:16]>.pth`（`eval/harness/run_instance.py:301`）——**只由 instance_id 决定，不含 run_id**；

⇒ 两个 run 跑**同一实例**时并发写**同一个 .pth 文件**，后写者覆盖先写者。

**实测（`t4_pth_race.py`：6 轮，每轮 2 个不同 run_id 同时跑同一实例）**：

```
round 0: A=agent_result/True  B=agent_result/True   infra=[]
round 1: A=infra_error/False  B=agent_result/True   infra=['t4-race-1A']
    note: editable 自检失败：click 未从本实例工作区导入（解析到 ...\t4-race-1B\...）
round 2: A=agent_result/True  B=infra_error/False   infra=['t4-race-2B']
round 3: A=infra_error/False  B=agent_result/True   infra=['t4-race-3A']
round 4: A=agent_result/True  B=agent_result/False  infra=[]      <<< 静默误判
round 5: A=infra_error/False  B=agent_result/True   infra=['t4-race-5A']

共 6 轮，出现 infra_error 的轮：[1, 2, 3, 5]  合计 4 次（≈67%）
.pth 文件个数（各轮）：[1]  => 恒为 1 个同名文件
.pth 各轮指向：round0:B / round1:B / round2:A / round3:B / round4:A / round5:B
```

**两档后果，第二档更危险**：

| 档 | 现象 | 危害 |
|---|---|---|
| 显性 | `infra_error`（自检抓到导入路径 ≠ 自己工作区） | 计为 infra、不进分母 ⇒ **数字尚可救**，但白跑一轮 |
| **隐性** | **round 4**：B 的 `.pth` 指向 **A 的工作区**，B 却正常跑完给出 `agent_result/resolved=False`，**notes 为空、无任何告警** | **静默误判**：B 判定的是别人的工作区，且**不会被任何自检拦住** ⇒ 会污染 resolved 分母 |

round 4 的原始 `.pth` 内容（从 `pth_race.json` 取，非转述）：

```
name: _eval_editable_076de2fe10731e0c.pth
body: import sys; sys.path.insert(0, 'E:\\aicoding\\eval-runs\\t4-race-4A\\pallets-click__4fd2fea\\repo')
      import sys; sys.path.insert(0, 'E:\\aicoding\\eval-runs\\t4-race-4A\\pallets-click__4fd2fea\\repo\\src')
```

B（`t4-race-4B`）在这份指向 `4A` 的 .pth 下跑完，`verdict_class=agent_result`、`resolved=False`、`notes=[]`。**A 与 B 的工作区、证据目录确实互异**（`distinct_workspaces=True`、`distinct_artifacts=True`），但它们**共享的 venv 里的挂载点不互异**——这就是隔离被穿透的地方。

### 4.3 影响面（如实界定，不夸大）

`t4_venv_sharing.py` 按**实际 venv 路径**普查全部 23 条实例：

```
(1) venv 共享普查：23 条实例 -> 23 个 venv
    共享 venv 的分组：（无：每条实例各自一个 venv）
(2) .pth 路径命名规则验证
      实例 astanin-python-tabulate__3aa568c
        run_id=runA -> ...\_eval_editable_f55f380b6420f097.pth
        run_id=runB -> ...\_eval_editable_f55f380b6420f097.pth
        路径相同（=> 并发会互相覆盖）：True
  两条实例共享 venv：False
  .pth 只依赖 instance_id（不含 run_id）：True
```

- **当前 pilot-12 的常规批跑不会踩到**：同一 run_id 内并发跑**不同**实例时，`.pth` 文件名互异、每个实例各自一个 venv ⇒ 安全（§4.1 已实测）。
- **但这是潜在雷**，触发条件 = 「**同一实例被并发跑两次**」（不同 run_id、或同一 run_id 重复、或断点续跑期间另起一轮、或人工复跑/A-B 对比）。一旦触发，**最坏情况是静默误判并污染分母**。

### 4.4 🔴 另发现：t3 的并发断言恒真（空证）

`eval/harness/mechanism_check.py:193-197`：

```python
# 工作区互不干扰：不同实例的 workspace 路径必须不同
ws = [p["instance_id"] and E.ws_path(par_id, p["instance_id"]) for p in m_par["instances"]]
print(f"\n  工作区互不干扰：{len(set(ws))} 个互异路径 / {len(ws)} 条实例（期望相等）")
all_ok &= (len(set(ws)) == len(ws))
```

`E.ws_path(run_id, iid)` 是按 `run_id + instance_id` **拼出来的字符串**，`ws` 列表里每项都带不同的 `instance_id` ⇒ `len(set(ws)) == len(ws)` **恒为真**。这条断言**证明不了任何隔离性质**，t3 把它列为「并发隔离」通过证据属于**空证**。

（另外 `p["instance_id"] and ...` 这个写法本身也没有过滤作用——非空字符串恒为真值。）

**我另写的 `t4_concurrency.py` 用真实路径比较 + 「同实例并发两次」+ `judged.json` 一致性三重检查，可直接复用。**

---

## 5. 断点续跑：✅ 通过（真中断，非「模拟跑一半」）

`mechanism_check` 的版本用「先只传前 2 条 id」模拟中断，**并没有产生被强杀的半成品状态**。我改用真杀（`t4_resume.py`：等 `state.jsonl` 出现第 1 条 `judged` 后 `taskkill /F /T`，连 ProcessPool 子进程一起杀）：

```
阶段 1：观察到 1 条 judged：['astanin-python-tabulate__9dedd45'] => 立刻强杀
  杀后 state.jsonl 行数：1
  半行/损坏的 JSON 行：0（read_state 应能容错跳过）

阶段 2：同 run_id + --resume 重跑全部 4 条
  [skip] astanin-python-tabulate__9dedd45  已判：agent_result resolved=True
  [agent_result] more-itertools-more-itertools__1b7f033  resolved=True
  [agent_result] pallets-click__4fd2fea                  resolved=True
  [agent_result] pytest-dev-pluggy__cbf58d5              resolved=True
```

机器可读证据（从 `manifest.json` 与 `state.jsonl` 直读，**不解析控制台文本**，`t4_resume_evidence.py`）：

```
skipped_completed = 1   （期望 = 1，即中断前已完成的那条）
state.jsonl 行数 = 4  实例数 = 4
只出现一次的 judged 实例：[全部 4 条]
出现多行/重复追加的实例：{}
summary = {"total": 4, "counts": {"agent_result": 4, ...}, "resolved": 4, "denominator": 4}
断点续跑（manifest 口径）判定：PASS
```

⇒ (a) 已完成的被跳过 ✅ (b) 结论未被改写 ✅ (c) `state.jsonl` 里无重复追加 ✅ (d) 剩余 3 条被跑完、最终 4 条齐全 ✅；中断未留下半行损坏记录。

> **留痕**：控制台里 `已完成跳过 N` 那行被 GBK 打成了乱码、我的正则没解析出来（首次判定显示 `None` 而报 FAIL）。改用 `manifest.json` 的 `skipped_completed` 字段后为 1。**这是控制台编码问题，不是 harness 缺陷**；但说明**依赖控制台文本做判定不可靠**，已改为读 manifest。

---

## 6. 「判定不采信自述」+ 基础设施错误分类

### 6.1 三类「不该 resolved」的情形（`t4_falsification.py`，自己造补丁）

```
[pallets-click__1b0e19f] A_missing          verdict=empty_patch     resolved=False denom=True agent_fail=True
[pallets-click__1b0e19f] B_apply_fail       verdict=patch_conflict  resolved=False denom=True agent_fail=True
[pallets-click__1b0e19f] C_wrong_deliberate verdict=agent_result    resolved=False denom=True agent_fail=False
[pallets-click__1b0e19f] D_test_only        verdict=empty_patch     resolved=False denom=True agent_fail=True
[pallets-click__1b0e19f] E_gold_control     verdict=agent_result    resolved=True  denom=True agent_fail=False
（more-itertools-more-itertools__1b7f033 同型）

负向样本 8 条，被判 resolved 的：0（必须为 0）
阳性对照 2 条，未 resolved 的：0（必须为 0）
分类与期望不符：0
```

| 情形 | 构造方式 | 实得 verdict | resolved | 是否进分母 |
|---|---|---|---|---|
| **补丁缺失** | `patch_text=None` | `empty_patch` | False | 是（实质失败） |
| **应用失败** | 上下文被破坏的 diff | `patch_conflict` | False | 是 |
| **故意写错** | **能干净 apply**、只加一行注释、不修缺陷 | `agent_result` | **False** | 是 |
| 只改测试（附加） | 只动 `tests/*.py` | `empty_patch`（剥光） | False | 是 |
| **阳性对照** | 真 gold | `agent_result` | **True** | 是 |

⇒ **没有任何一条负向样本被判 resolved**；阳性对照正常 resolved，证明「False」不是判定瘫痪造成的。

**验收条款要求的「unresolved 或 infra」**：A/D 为 `empty_patch`、B 为 `patch_conflict`、C 为 `agent_result/False` —— 全部是「判不了的判不了、没修好的不算过」，**无一 resolved**，符合条款。

> **留痕**：C 案第一版因我给的 diff 上下文行不存在，落成 `patch_conflict` 而非 `agent_result`；我在脚本里加了「从 `base_commit` 取真实首行当上下文」的修正并重跑（`t4-falsify2`），C 才落到 `agent_result/False`。**这是我测试构造的 bug，不是 harness 缺陷**；留此说明以免被误读为 harness 分类错误。

### 6.2 基础设施错误分类 + 超时优先（`t4_infra_timeout.py`：exit 0 全 PASS）

```
(A) ALL_CLASSES            = ('agent_result','empty_patch','patch_conflict','infra_error','timeout','harness_bug')
    COUNTS_IN_DENOMINATOR  = ('agent_result','empty_patch','patch_conflict')
    COUNTS_AS_AGENT_FAILURE= ('empty_patch','patch_conflict')
    => infra/timeout 不进分母、不算 agent 失败：True

(B) classify_run 顺序：
    timed_out=True, rc=1  => timeout        （期望 timeout）
    timed_out=False,rc=1  => agent_result   （期望 agent_result）
    rc=3 + errors         => infra_error
    rc=0 但无逐 node 结果 => infra_error

(C) 实操（超时压到 0.05s 跑真实实例）：
    verdict_class = timeout   resolved = False   timed_out = True
    exit_code = 1             （被强杀也是 1，与「有失败」同值）
    进分母？False  算 agent 失败？False

(F) summarize()：total=6  denominator=4  infra_retryable=2
```

⇒ **Windows 特有陷阱被正确处理**：同一个 `exit_code=1`，`timed_out=True` 时判 `timeout`（不进分母）、`timed_out=False` 时才按逐 node 结果二分。**顺序正确，未见「timeout 被误记成 not_resolved」。** infra/timeout 被单独归类且不计入 agent 失败 ✅

---

## 7. 发现的缺陷汇总（全部如实列出，不替对方圆场）

| # | 严重度 | 缺陷 | 证据 | 位置 |
|---|---|---|---|---|
| **D1** | **blocker** | **共享 venv 内 `.pth` 竞态**：文件名只依赖 `instance_id`、不含 `run_id`，同实例并发跑会互相覆盖；**可复现 4/6 轮**，且含**静默误判**（判定别人的工作区、无告警） | `t4_pth_race.json`（round 4）、`concurrency.json`、`venv_sharing.json` | `run_instance.py:301` + `env.py:venv_dir` |
| **D2** | **high** | **`mechanism_check` 的并发断言恒真（空证）**：`E.ws_path()` 按 instance_id 拼路径，`len(set(ws))==len(ws)` 永真 ⇒ 该条「并发隔离」证据无效 | `mechanism_check.py:193-197` | 同上 |
| **D3** | medium | **静默误判无检测**：导入路径 ≠ 自己工作区、但 pytest 仍能跑完时，harness **不作任何标记**（只在导入失败时才报 infra） | `t4_pth_race.json` round 4（`notes=[]`，`agent_result/False`） | `run_instance.py` / `env.py:ensure_venv` |
| D4 | low（非缺陷，已核实） | `patch`/`test_patch` 与 `git show` 的 `index` 行 SHA 缩写不同（40 vs 7 位） | §2.2 | 构造器落盘形态，**不影响语义** |
| D5 | low（构造侧遗留） | 3 条 tabulate 实例第 3 向与第 2 向同状态、无额外判别力 | §3.3 | t2 冻结语义，t3 已如实标注，**建议 t6 记录** |

**另有两处是我自己复核脚本的 bug**（已在文中留痕，非 harness 问题）：基线请求误含 F2P 致 exit 4（§1.3）、falsification C 案上下文行错致 patch_conflict（§6.1）。

---

## 8. P0 是否可交付：结论与依据

**结论：pilot-12 的评测数字可用且已通过独立复核；但「并发隔离」这条不变量当前不成立，D1 应先修，修完再宣称 harness 完全达标。**

依据（逐条对应）：

1. **数字可用**：12/12 条实例的红/绿判定经**三路独立核对一致**（我的独立 runner、gold_check、t2 归档原始日志重算）；`base_commit == fix_commit^` 全对；gold/test_patch 与 `git show` 逐字节忠实（仅 index 缩写差异）；P2P 在 B/T/G 三观测点全绿。
2. **判据可信**：黄金三向 12/12 全对，且我**自造**空补丁/错补丁/缺失/应用失败/只改测试共 8 条负向样本，**0 条被判 resolved**，阳性对照正常 resolved ⇒ 「判定不采信自述」成立。
3. **断点续跑可信**：真强杀进程后恢复，`skipped_completed=1`、无重复追加、结论未改写。
4. **infra/timeout 口径可信**：超时先于退出码的顺序正确，实测拿到 `timeout` 且不进分母。
5. **但隔离不成立**：同实例并发跑两次会在共享 venv 里抢同一个 `.pth`，**67% 触发**，最坏造成**静默误判并污染分母**。当前 pilot-12 常规批跑（不同实例并发）**侥幸不踩**，但这是设计与不变量层面的真实缺口，不能算通过。

### 建议修复（交 captain 定夺，我不改代码）

1. `.pth` 文件名**加入 run_id**（或工作区路径的哈希），消除同名覆盖 —— 最小改动；
2. 或在 `ensure_venv` 里对 `.pth` 的写入/自检**加锁**，并把「导入路径 ≠ 本实例工作区」从「只在失败时报」升级为**始终校验、不符即判 `infra_error`**（补 D3）；
3. 若选「每实例独立 venv」，可彻底消除共享，代价是建 venv 时间上升；
4. `mechanism_check.py:193-197` 的并发断言须换成**真实磁盘路径比较**（可直接复用 `eval/reports/_t4_scripts/t4_concurrency.py` 的三重检查）；
5. 建议在预注册 §8.3 里补一条**不变量**：「同一实例任一时刻只能有一个活跃 run（或 `.pth` 挂载必须与工作区一一对应）」，让该约束有出处。

---

## 附录 A：本次复核跑过的命令（可复现）

```
# 验收 verify 三条（全部 exit 0）
Set-Location E:\aicoding\mini-claude-code
& 'D:\Anaconda3\envs\aicoding\python.exe' eval/gold_check.py --run-id t4-goldcheck-final
& 'D:\Anaconda3\envs\aicoding\python.exe' eval/build_subset.py --verify-all
git status --porcelain

# 复核脚本（均落 eval/reports/_t4_scripts/）
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_provenance.py            '<ids>'
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_patch_vs_gitshow.py       E:\aicoding\eval-runs\t4\pilot_ids.json
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_classifier_selftest.py
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_archived_recompute.py
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_independent_redgreen.py   E:\aicoding\eval-runs\t4\pilot_ids.json E:\aicoding\eval-runs\t4\independent -
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_baseline_observation.py
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_compare.py
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_falsification.py          t4-falsify2
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_independent_directions.py
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_concurrency.py            t4-conc
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_isolation_defect.py
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_pth_race.py
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_venv_sharing.py
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_resume.py                 t4-resume
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_resume_evidence.py
& '...python.exe' -X utf8 eval/reports/_t4_scripts/t4_infra_timeout.py
```

## 附录 B：证据文件索引

| 文件 | 内容 |
|---|---|
| `eval-runs/t4-goldcheck-final/gold_check_summary.json` | 验收「自己跑三向」的结论 JSON |
| `eval-runs/t4/three_way_comparison.txt` | gold_check / 独立 runner / 归档重算 三路对照表 |
| `eval-runs/t4/independent/redgreen.json` | 独立 runner 的 12 条红/绿逐条结果 |
| `eval-runs/t4/archived_recompute/archived_recompute.{json,txt}` | 从 t2 归档原始日志重算的 12 条 |
| `eval-runs/t4/provenance/provenance.json` | `base==fix^` 等 provenance 检查 |
| `eval-runs/t4/provenance/patch_vs_gitshow.{json,txt}` | patch/test_patch 与 git show 的差异分类 |
| `eval-runs/t4/baseline_observation/baseline.json` | B 观测点 P2P 全绿 |
| `eval-runs/t4/falsification/falsification.json` | 8 条负向 + 2 条阳性对照 |
| `eval-runs/t4/independent_directions/independent_directions.json` | 独立构造的空/错补丁 |
| `eval-runs/t4/concurrency/concurrency.json` | 串行 vs 并发 + 同实例并发两次 |
| `eval-runs/t4/pth_race/pth_race.json` | **D1/D3 的 6 轮复现证据（含 round 4 静默误判）** |
| `eval-runs/t4/venv_sharing/venv_sharing.json` | venv 共享普查 + .pth 命名规则 |
| `eval-runs/t4/resume/resume_manifest_evidence.json` | 断点续跑的 manifest 口径证据 |
| `eval-runs/t4/infra_timeout/infra_timeout.json` | 分类表 + 超时优先 + 实操超时 |