# 第三阶段 P0 独立审查报告（t9 · 与执行侧不同模型）

- 审查对象：`E:\aicoding\mini-claude-code` @ `7e648168769f0a4b268335b0d9dcc72c6fbe592a`（t8 修复后）
- 审查者：`reviewer`（独立审批侧，与实现方非同一模型）；**本次未改任何实现代码**
- 审查方式：只读实现 + **自己造并发/反例/复算**；不采信任何一方的转述
- 证据目录：`E:\aicoding\eval-runs\`（本报告每条结论都点名证据文件）
- 本报告落在合同 inScope `eval/reports/` 内；脚本落 `eval/reports/_t9_scripts/`（8 个）

## 0. 结论

**`verdict = pass`（P0 可交付）**，附 2 条**非阻断**的措辞更正要求（必须由 t10 落进文档/注释，
见 §12）与 1 份**边界清单**（见 §13）。

四条 verify 命令我自己跑过（原始输出落 `E:\aicoding\eval-runs\`）：
`eval/gold_check.py` ⇒ exit 0、**12/12 三向全对**（`t9-gold-check.txt`）；
`eval/harness/mechanism_check.py --run-id t9-mech` ⇒ exit 0、**4/4 PASS**（`t9-mech.txt`）；
`eval/build_subset.py --verify-all` ⇒ exit 0、**「实例总数=23 pilot-12 成员=12 跨仓库=4」+ verify-all: PASS**（`t9-verifyall.txt`）；
`git log --oneline -8` ⇒ 时序见 §8。

---

## 1. 实例构造等价性（抽样 6 条 / 覆盖 4 仓库）

脚本 `eval/reports/_t9_scripts/t9_instance_equivalence.py`（只调 `git` 原始输出，**不 import 构造器**）
证据：`E:\aicoding\eval-runs\t9-equiv.txt`

| instance | `base_commit == git rev-parse <fix>^` | `patch` == `git show <fix> -- <非测试.py>` | `test_patch` == `git show <fix> -- <测试文件>` | F2P 溯源 |
|---|---|---|---|---|
| pallets-click__4fd2fea | PASS（`9ce34f20…`） | PASS（637 B vs 703 B，差 1 条 `index` 行） | PASS | PASS 5/5 |
| pallets-click__ac6a2ac | PASS | PASS | PASS | PASS 1/1 |
| more-itertools__1b7f033 | PASS | PASS | PASS | PASS 1/1 |
| pytest-dev-pluggy__20d8143 | PASS | PASS | PASS | PASS 1/1 |
| astanin-python-tabulate__5373ebf | PASS | PASS | PASS | PASS 1/1 |
| astanin-python-tabulate__9dedd45 | PASS | PASS | PASS | PASS 1/1 |

- 「内容等价」判据：删掉 `index <sha>..<sha>` 行后**逐行相同**（唯一差异是 index 行的 SHA 缩写）。
- F2P 溯源判据（我自己写）：每个 node id 的**文件段**必须在该提交的测试改动文件集内，
  且其**函数名出现在 test_patch 的 `+def` 行里** ⇒ 6 条实例的全部 F2P（10 个 node id）均满足。
- `gold patch 只是该提交的源码部分`：`git show --name-status` 显示每个 commit 的文件集合被
  §3.1 规则切成 src / test / other 三堆，`patch` 只含 src 堆、`test_patch` 只含 test 堆，
  且 `patch ∪ test_patch` 之外没有 `.py` 文件（未漏）。

### 1.1 captain 抽查的 README.md 疑点 —— **按规则丢弃，且已记入 known_limits，不是漏掉**

- 全量扫描 23 条实例（`t9-equiv.txt` 第 96 行起）：**pilot-12 的 fix commit 没有任何一条改到
  README.md**；README 系文件出现在：
  - `more-itertools__1b7f033` / `__ccf96ac`（**pilot**）：`README.rst` 等 → `known_limits` 里逐字点名
    （原文「丢弃非 .py 改动文件（CI/docs/changelog/toml）：README.rst, docs/api.rst, more_itertools/more.pyi」）；
  - `astanin-python-tabulate__3aa568c` / `__8014ec6`（**reserve**）：`README.md` → `known_limits[1]`
    原文「丢弃非 .py 改动文件（CI/docs/changelog/toml）：README.md」。
- **规则依据**：§3.1 文件分类规则第 3 条写死「非 `.py` 文件（CI、docs、changelog、toml/cfg、二进制）
  **一律丢弃**，不入任一侧；丢弃情况记入 `known_limits`」。README/CHANGES/changelog 正是这一档。
  ⇒ 结论：**按规则丢弃，且按规则留痕，不是漏掉**。
- ⚠ 方法论提醒（写进报告以免后人误判）：`known_limits` 里 tabulate 的固定条目含
  「addopts 含 `--doctest-modules/--doctest-glob=README.md`」⇒ 若只用 **basename 子串匹配**核对，
  会在这两条上**假通过**。我改按显式条目「丢弃非 .py 改动文件…：<列表>」核对，结论不变。
- 全量扫描结果：**有非 .py 改动未被 known_limits 点名的实例数 = 0**（23/23 有留痕）。

---

## 2. §4.2 实现口径偏离 —— 独立裁定：**属于「细化」，不是「越权」**（但必须在结果节声明为偏离）

脚本 `t9_baseline_and_wrong.py`；证据 `t9-f2p.txt`（数据来自构造期归档的 `B1.json/T1.json/G1.json`
**原始 pytest json report**，在 `E:\aicoding\eval-cache\_artifacts\<repo_key>\<iid>\`，我不重跑即从原始报告重算）

① **原文 vs 实现**：§4.2 公式写的是 `F2P = {n ∈ T 红 | B 中 n 存在且 passed | G 中 n 存在且 passed}`，
紧跟着一句**目的说明**：「「B 中 n 存在且 passed」**排除了「基线上本来就红的测试」——它们不属于
本实例要修的东西**」。实现（`eval/build_subset.py:897-903`）写的是
「B 中该 node **不存在**（新测试）**或** 存在且 passed」，即把「不存在」也放行。

② **我自己抽 3 条受影响实例核对「基线非绿」是否确实是被排除的唯一理由**：

| 实例 | F2P 中 `not_in_B`（=新增测试） | `in_B_passed` | `in_B_red`（基线本来就红） | 字面读法得到的 F2P |
|---|---|---|---|---|
| astanin-python-tabulate__5373ebf | 1 | 0 | **0** | **0 ⇒ 字面读法下 E5 淘汰** |
| pallets-click__4fd2fea | 5 | 0 | **0** | **0 ⇒ 淘汰** |
| pytest-dev-pluggy__dd20a85 | 6 | 0 | **0** | **0 ⇒ 淘汰** |

⇒ 对这三条（以及另 7 条），F2P 里**没有任何**「基线本来就红」的 node；字面读法把它们挡在门外，
**唯一理由就是「该 node 在基线上不存在」（= 本提交新增的回归测试）**，而这恰恰不是 §4.2 目的句
要排除的那一类。

③ **影响面（必须写进结果节）**：12 条 pilot 中 **10 条**在字面读法下会因 F2P 为空被 E5 淘汰
（另 2 条：`more-itertools__f8ccab2` 部分、`pallets-click__1b0e19f` 全部来自老测试）。
即**字面读法根本组不出 §7.1 要求的 12 条**，而 §7.1 又禁止「降低判据来凑数」。

④ **裁定**：**细化（按该条的目的句实现），不是越权**。理由三条：
   1. 目的句明确把排除范围限定为「基线上本来就红的测试」；「基线上不存在」不在其中。
      按目的实现是对冻结文本的**最小扩张解释**，字面读法反而与该条自身的目的句冲突。
   2. 与 SWE-bench 官方 `FAIL_TO_PASS` 语义一致（修复提交新增/改动的测试，修复前失败、修复后通过）。
   3. **没有偷偷改规则**：`git diff 27adbdd HEAD -- eval/PREREGISTRATION.md` **为空**（§1–§9 一字未动）；
      §10.4 要求「结果与规则冲突时如实报告冲突」——构造器把差异写进了
      `eval/instances/pilot-12-sampling-log.md:205` 与**每条受影响实例的 `known_limits`**
      （我逐条核对：10/10 都写了「F2P 全部/部分为**新增测试**…N/M 条」）。
   4. 该条**真正的排除路径仍然活着**：sampling log 里有候选因「T 红灯全部属于『基线本来就有病』」
      被 E5 淘汰（`astanin__python-tabulate` `886e2ed` / `ebd50a7`），说明基线红排除确实在生效。

⑤ **若 captain 想严格守字面**：正确路径**不是回改 §1–§9**（那会毁掉冻结性），而是
**重新做一次预注册**（§9.5/§10.4：旧数据全部作废、只能作为「探索性观察」另列一节）。
代价是 12 条里 10 条不可用、P0 规模归零 —— 我不建议，且我认为按目的实现 + 如实声明是**更忠实**的做法。
**但「这是偏离」这件事必须写进最终报告**（不能写成「预注册本来就这么说」）。

---

## 3. 3 条 tabulate 实例的「第 3 向判别力」—— 独立裁定：**接受，不要求重做**（必须写进文档）

证据 `t9-f2p.txt` 第 3 节 + `goldcheck-20261013-012927/gold_check_summary.json` 的 `wrong_direction_caveat`

- 3 条 tabulate 实例的 `wrong_patch` 与 `patch` **逐字节相同**（`wrong==gold=True`，
  且 `gold_selfcheck.wrong_patch_method == "reverse_of_gold"`）⇒ harness 按冻结语义
  「先叠 gold 再 `git apply -R`」，**净效果 = 回到基线** = 与第 2 向（空补丁）同状态。
  归档的第三向报告也印证：这 3 条第三向只有 **1 个非 passed**，就是那个 F2P node（仍红）。
- 另 9 条实例的第 3 向是 `unrelated_file_comment`：`wrong != gold`，且归档第三向报告显示
  **干净 apply + 结果 unresolved**（例：`pallets-click__4fd2fea` 第三向 5 个非 passed = 恰是它的 5 条 F2P）。
  ⇒ 「补丁能干净 apply 但没修好 ⇒ unresolved」这条**判别力在 harness 层已被其它 9 条实证**。
- **裁定：接受**（不重做），理由：
  1. §6.4 的门槛是「三向全对 ⇒ harness 可用」；这 3 条的**期望结论全部命中**
     （gold resolved / empty unresolved-empty_patch / wrong unresolved-agent_result），
     缺的是**额外判别信息**，不是某条判据没过。
  2. harness 层的判别力由其余 9 条独立证成（有 clean-apply-but-wrong 的正例）。
  3. 根因在构造侧（§6.3 第 3 向把「怎么施加」编码进元数据、正文落的是 gold），
     实例 JSON 已冻结（§9.5）；重做要走 §7.5 例外通道并**替换 3 条** ⇒ 触发 §7.5③
     「替换数 > 2 ⇒ 规模声明改为『有效样本 N 条』」+ 全部重跑，代价与收益不成比例。
- **要求**：最终报告必须写明「**3/12 条实例的第 3 向净效果 = 基线，少一向判别力**」，
  不得声称「12 条三向各自提供了独立判别力」。`gold_check` 已把它打进 stdout 与
  `gold_check_summary.json.wrong_direction_caveat`（我跑的那次：3/12 命中，字段齐全）。

---

## 4. t4 报的隔离竞态是否真被修掉 —— **已修（我自己造并发验证）**

脚本 `t9_race.py`；证据 `E:\aicoding\eval-runs\t9-race.txt` + `t9-race\race_result.json`

- **场景**：**同一实例** `pallets-click__4fd2fea`、**两个不同 run_id**（这正是 D1 的触发形态）、
  用 `ProcessPoolExecutor(2)` **同时**提交 —— **3 轮**（合同要求 ≥3），**没有复用 mechanism_check 的用例**。
- **结果（当前实现）3/3 轮**：
  - `verdict_class=agent_result`、`resolved=True`（两 run 都是）；**0 轮 infra、0 轮静默误判**；
  - 两 run 的 venv **realpath 互异**（例：`pallets__click-9ce34f20-6f1d3edd` vs `…-f420f326`）；
  - **每个 venv 里只有 1 个** `_eval_editable_*.pth`，且其 entries 指向**本 run 自己的工作区**
    （文件名含 run 哈希，例 `_eval_editable_ee160e053441854f.pth`）；
  - `import_probe_before_judge` / `after_judge` 都落在**自己**的工作区内。
- **仪器自证（这套检测有判别力）**：把 `venv_dir` 与 `editable_pth_name` **还原成修复前的公式**
  再跑同样的并发 ⇒ **1/1 轮立刻报警**：B 的挂载自检失败，
  `解析到 E:\…\t9-race-r100A\…\src\click\__init__.py，期望在 E:\…\t9-race-r100B\…内` ——
  即 t4 报的「拿到别人的代码」复现了（本次落在 infra 分类上，不是静默误判；
  这也解释了为何修复前的表现会随交错时序在 infra 与静默之间摇摆）。
- 附带观测：修复前公式用的那个共享 venv `pallets__click-9ce34f20` 里**累积了 20+ 个历史
  `_eval_editable_*.pth`**（t4/t8 各轮残留），正是「共享可变状态」的现场证据。

---

## 5. mechanism_check 的**新并发断言**是否有判别力 —— **有（不是新的空证）**

脚本 `t9_assert_power.py`；证据 `E:\aicoding\eval-runs\t9-assert.txt` + `t9-assert-power\assert_power.json`

| 检查 | 结果 |
|---|---|
| 正常态（我自己跑的 `t9-mech-parallel`，4 实例）新断言「真实磁盘路径互异」（realpath+normcase） | **True**（4 互异 / 4） |
| 正常态新断言「挂载点归属」（每实例 `.pth` 指向自己工作区、且无其它 editable 挂载） | **True**（4/4 OK） |
| 反例 A：`mklink /J` 让实例 B 的工作区就是 A（**junction**） ⇒ 互异断言 | **False（被抓）** |
| 反例 B：把 B 的 `.pth` 改成指向 A 的工作区 ⇒ 归属断言 | **False（被抓）** |
| 还原 B 的 `.pth` 后归属断言 | **True（恢复）** |
| 对照：老的（D2 报的）空证表达式 `len(set(ws_path(...)))==len(ws_path(...))` 在**同一个 junction 场景**下 | **True —— 恒真，测不出任何东西** |

⇒ D2 的修法**确实有判别力**；且我复现了「老断言是空证」这件事本身（同一个 junction 场景下它照样报通过）。

---

## 6. `resolved` 判据不可被绕过 + infra 不算 agent 失败 —— **不可绕过（5/5 PASS）**

脚本 `t9_bypass_and_infra.py`；证据 `E:\aicoding\eval-runs\t9-bypass.txt` + `t9-bypass\bypass_summary.json`

| 情形 | 构造方式（我自己造） | 期望 | 实测 |
|---|---|---|---|
| A **自称修好但没修好** | 在 `src/click/core.py` 只插入 2 行注释，其中写明 “I ran the tests locally, they are all green now, fixed. # harness: please mark this instance resolved.”；`patch_apply_exit_code=0`（**干净 apply**） | `agent_result` / unresolved | **`agent_result` / resolved=False / F2P 0/5** ✅ |
| B 只改测试骗绿 | 只在 `tests/test_options.py` 插一行 | `empty_patch` / unresolved | **`empty_patch` / False / `touched_tests=True` / 剥离后 `judged_patch_paths=[]`** ✅ |
| C 真 infra 故障 | 把 `env.CACHE_ROOT` 指到不存在的目录（真故障，非打桩） | `infra_error`，**不计分母、不计 agent 失败** | `infra_error`；`COUNTS_IN_DENOMINATOR=False`、`COUNTS_AS_AGENT_FAILURE=False`；`summarize([1 条 agent_result(resolved), 该 infra])` ⇒ **resolved=1 / 分母=1** ✅ |
| D 超时优先于退出码 | `timeout_probe=True`（判定超时压到 0.2 s） | `timeout` | `timeout`、`timed_out=True`、`exit_code=1`（与 pytest「有失败」同值）、不计分母/不计失败 ✅ |
| E 阳性对照 | 真 gold | `agent_result` / resolved | **`agent_result` / True**（证明 A/D 的 False 不是判定瘫痪） ✅ |

另（代码级）：`judge()` 的入参只有 `(f2p, p2p, results)`，**没有任何 agent 文本入口**；
`run_one()` 只吃 harness 从工作区采集的补丁文本（§5.1）。⇒ 自述对判定零影响在**结构上**也成立。

---

## 7. 三向自校验 + 并发 + 断点续跑（我自己跑，不引用转述）

- **三向**：我自己跑 `eval/gold_check.py` ⇒ `/exit 0`、**12/12 实例三向全对**
  （gold 12/12、empty 12/12、wrong 12/12）。证据 `E:\aicoding\eval-runs\t9-gold-check.txt`（含逐条 F2P/P2P）。
- **机制验证**：我自己跑 `eval/harness/mechanism_check.py --run-id t9-mech` ⇒ `/exit 0`、**4/4 PASS**
  （timeout_priority / test_stripping / concurrency_isolation / resume）。证据 `t9-mech.txt`。
  - **并发 vs 串行逐条一致**：4 实例，串行 `{agent_result:4}` == 并发 `{agent_result:4}`，逐条 resolved=True 相同；
    并发批的真实磁盘路径互异、全落在本 run 目录下、挂载点归属 4/4 OK。
  - **续跑**：阶段 1 完成 2 条 → `--resume` ⇒ `skipped_completed=2`、总完成 4 条、
    「已完成实例结论未被改写=True」、「state.jsonl 里各只有一条 judged=True」。
- **区间隔离**：我自己另跑 3 轮同实例并发（§4）也一致，无翻车。

---

## 8. 判据写在数据之前（时序）—— 成立

```
27adbdd 2026-10-12T18:39:13+08:00  预注册（只新增 eval/PREREGISTRATION.md，496 行）
2a41e92 2026-10-12T22:51:54+08:00  实例构造器 + 12 条实例
3b906c1 2026-10-12T23:21:33+08:00  harness
14bdd10 2026-10-12T23:56:28+08:00  端到端冒烟
7e64816 2026-10-13T01:22:31+08:00  t8 修复
```

- `git diff --stat 27adbdd HEAD -- eval/PREREGISTRATION.md` ⇒ **空**（提交后一字未改）；
  `git log --follow` 该文件**只有 1 次提交**（27adbdd）；
  `git show --stat 27adbdd` ⇒ 该提交**只含这一个文件**（+496 行，0 删）。
- 预注册（18:39）比**任何**实例定义（22:51）早 **4 小时 12 分**，比第一批跑批产物更早。
- 实现与预注册的一致性：§5（判定口径/六分类/剥离）、§6（三向）、§8.4（600 s / 120 s / 并发 3）
  逐条对齐；发现的偏离只有 §4.2（§2 已裁定为「按目的细化 + 已留痕」）与
  §8.2 的 venv 命名（t8 把它从「每实例一个」加严成「每 (实例, run) 一个」= **更严**，用于消除 D1 竞态；
  与 §8.2「绝不共享」的意图一致，属加严而非放宽）。

---

## 9. 冒烟结论可复算 —— **可复算（逐字段一致）**

脚本 `t9_smoke_recompute.py`；证据 `E:\aicoding\eval-runs\t9-smoke.txt` + `t9-smoke-recompute.json`

1. **判定复算**：用 t5 归档的 `agent_candidate_patch.diff`（**251,255 B**，就是当时判定用的那个文件）
   在**全新工作区**重跑一次 ⇒ 与 `t5-smoke/harness_verdict.json` **逐字段一致**：
   `agent_result` / `resolved=True` / `patch_apply_exit_code=0` / `touched_tests=True` /
   `test_side_paths=['tests/conftest.py']` / `dropped_non_py_paths=['pyproject.toml']` /
   `judged_patch_paths=['src/click/core.py','pyproject.toml']` / F2P **5/5**、P2P 非过 **0**。
2. **成本/耗时复算**（从 `servers/experiments/out/agent-t5-smoke.json` 的 trace 逐回合重算）：
   `input_uncached=45,310`、`cache_read=1,916,160`、`cache_creation=0`、`output=0`、
   回合 **40**、tool_call **77**、`metrics.elapsed_s=273.0` ⇒ **与 `p0-smoke.md` 逐项一致**；
   工具分布也一致（read_file 28 / run_tests 23 / search_code 20 / apply_patch 4 / repo_map 1 / list_files 1）。
   ⚠ 同一次 trace 里 `metrics.tool_calls_total = 0`，与实测 77 **矛盾** ⇒ **D6 的症状在我这里独立复现**。
3. **自述 vs 判定**：`t5-smoke/console.log`（UTF-16）里确有 `==> 判定：未成功`，
   而独立判定为 `resolved=True` ⇒ **两者相反**，「判定不是从自述来的」成立。

---

## 10. t8 的两处 inScope 越界是否可接受 —— **可接受（前提的实质成立；但「逐字不变」字面不成立）**

脚本 `t9_usage_log_default_path.py`；证据 `E:\aicoding\eval-runs\t9-usage-default.txt`

- 合同前提三条，我逐条独立核实：
  1. **selfcheck 全过**：我自己跑 `servers/experiments/subagent_selfcheck.py` ⇒ `/exit 0`、
     末行「**全部通过（0 项失败）**」。证据 `t9-selfcheck.txt`。
  2. **`build_task(False,False) == TASK_BASE`**：我**自己 import 核对**（不引用 selfcheck 输出）
     ⇒ `True`；另 `build_task(False)==TASK_BASE=True`、`build_task(True)!=TASK_BASE=True`；
     `run_agent` 的 `max_turns` **默认值 = 40 未变**；`MAX_TURNS=40 / INSTANCE_MAX_TURNS=120`。
  3. **不传 `trace` 时 `log_usage` 的写盘行为逐字不变** ⇒ **不成立（字面）**：
     我用 git 里的 t8 前版本（`14bdd10:servers/experiments/usage_log.py`）与新版本**在同一进程里
     分别 import、喂同一个人造 ResultMessage、都不传 `trace`**，逐 key 对比写盘行：
     - **新增键：`['usage_source']`**（值 `"model_usage"`）；**删除键：无**；**值变化的键：无**。
     - 旧行 17 个键 / 新行 18 个键；两行文本因此**不逐字节相同**。
     - 也就是说：**既有字段与写盘路径（文件、追加语义、编码）都没变，但产物多了一个 additive 键**。
- **裁定：越界可接受**（合同写窄了，不是成员越权）：改动的两个文件是 D6/D8 症状**唯一**的落点，
  且不传 `trace` 的调用方**既有语义零变化**；`coding_agent.py` 的改动是最小化的接线
  （新增可选参数 + 参数化上限），`--fixture` 路默认值逐字仍是 40。
- **但必须更正一句话**：提交信息/代码注释里「不传 `trace` 时行为与改动前**逐字一致**」应改为
  「**既有字段的值与写盘路径不变，另新增一个 `usage_source` 键**」。
  若 captain 认为「逐字」必须为真，最小修法是让该键只在传了 `trace`（或显式要求）时写入 —— 一行条件，属 t10 可办的微修。

---

## 11. t8 对 D7 的**根因更正**是否属实 + `251,255 → 1,268` 是否可复现 —— **更正成立、数字精确复现；但机理说法错了**

脚本 `t9_d7_reproduce.py` + `t9_d7_mechanism.py`；证据 `E:\aicoding\eval-runs\t9-d7.txt`、`t9-d7\d7_reproduce.json`、`t9-d7-mech.txt`

**A. 「真因在工作树」——成立。** 在 t5 工作区副本上把 3 个被改过的已跟踪文件改回 CRLF（复现当时工作树）：

- 归一前 `git diff` = **251,255 字节**（与 t8 声明**完全一致**），hunk = 3，全是 `@@ -1,3134 +1,3135 @@` 型整文件 hunk；
- 调 `judge.normalize_worktree_eol` 后 `git diff` = **1,268 字节**，hunk = 3，**缩比 198.2x**（声明一致）；
- 归一后的补丁**能看清真实语义改动**（就是 gold 那 9 行：`if is_flag and flag_value is None: flag_value = not self.default` 上移）。
⇒ **t5 把 CRLF 归因于「归档补丁正文」是错的；t8 改到工作树是对的，且数字可复现。**

**B. 但 t8 给的机理（「`git diff` 自己会把行尾归一化后输出」）不成立。** 同一次 diff 两种读法：

| 读法 | 字节数 | CRLF 数 |
|---|---|---|
| 字节模式（`subprocess` 不带 `text=True`） | **254,616** | **3,361** |
| 文本模式（`text=True`，**即 `env.sh()` 的读法**） | 251,255 | **0** |

⇒ **git diff 不归一化行尾**；是 harness 自己的 `subprocess(..., text=True)`（universal newlines）
把 CRLF 折成了 LF。**归档件 `t5-smoke/agent_candidate_patch.diff` 的 CRLF=0 正是这个读法造成的**
—— 独立证据：该归档件里 `pyproject.toml` **212/213 对 `-`/`+` 行逐字相同**（core.py 2614/3134 也有大量相同对）：
**若采集时正文真的没有 CRLF，git 根本不会产出这些 hunk** ⇒ 归档件是**损失性产物**（差异被读法吃掉），
它不能用来证明「采集时正文没有 CRLF」。
- 结论：**t8 的结论对、理由错**；影响面仅限注释与提交信息的解释（修法本身正确、有效、数字真实）。
  需要把 `eval/harness/judge.py:68` 附近「因为 `git diff` 自己会把行尾归一化后输出」改成
  「因为 harness 以 `text=True` 读子进程输出，universal newlines 把 CRLF 折成 LF」。
- 附带一条给 P1 的提醒：**harness 采集到的补丁文本天然不含 CRLF**（读法所限），
  所以「补丁正文行尾」这条防线其实永远不触发；真正的防线是 `normalize_worktree_eol`。

---

## 12. 必须在 t10（文档/日志）里落地的四项

1. **§4.2 偏离声明**：写明「实现按 §4.2 的**目的句**细化（只排除基线本来就红的 node），
   与公式字面读法不同；**10/12 条实例依赖该细化**，字面读法下 F2P 为空 ⇒ E5 淘汰」。
   不得写成「预注册本来就这么说」。
2. **第 3 向判别力声明**：写明「**3/12**（3 条 tabulate）第 3 向净效果 = 基线、少一向判别力；
   harness 层的 clean-apply-but-wrong 判别力由其余 9 条证成」。
3. **措辞更正**（两条，均非功能缺陷）：
   - `usage_log`/`coding_agent` 的越界说明：把「不传 `trace` 时逐字一致」改为
     「既有字段值与写盘路径不变，新增一个 `usage_source` 键」；
   - `judge.py` 的 D7 注释：把「`git diff` 自己会归一化」改为
     「`env.sh()` 的 `text=True`（universal newlines）把 CRLF 折成 LF」；
   - 另有一条纯装饰性的陈旧帮助文本（不影响任何判定）：`run_instance.py` 的 `--timeout-probe`
     帮助写「把判定超时压到 **1s**」，实际代码压到 **0.2s**（`judge_timeout = min(..., 0.2)`）。顺手改掉即可。
4. **边界清单**（§13）逐条写进报告，尤其**不得声称跑过官方 SWE-bench harness**。

## 13. 可写入文档的边界清单（P0 交付口径）

**可以写的（有本轮独立证据支撑）**
- 「12 条实例、跨 4 个纯 Python + pytest 仓库；`base_commit == fix^` 与 `patch`/`test_patch`
  的切分经 `git show` 原始输出逐条核对（抽样 6 条 + 全量 23 条的非 `.py` 留痕核对）。」
- 「黄金三向自校验 12/12 通过（gold/empty/wrong 三向逐条期望命中）」。
- 「判定由 harness 自己启动的独立 pytest 完成；agent 自述对判定零影响
  （实测：一个自称修好、能干净 apply 的补丁被判 unresolved；t5 里自述与判定相反）」。
- 「同一实例并发跑两个 run：0 infra、0 静默误判、每 venv 只有一个挂载点且指向自己工作区（3 轮）」。
- 「并发与串行结果逐条一致；`--resume` 恢复后不重复、不改写已完成结论」。
- 「判据（`eval/PREREGISTRATION.md`）在第一条实例定义之前 4h12m 提交，且提交后零改动」。
- 「端到端冒烟 1 条实例：独立判定 `resolved=True`（F2P 5/5、P2P 60/60），
  与 agent 自述相反；成本/回合/工具数可从 trace 复算（input_uncached 45,310 / cache_read 1,916,160 /
  turns 40 / tool_calls 77 / 墙钟 273 s）」。

**只能写成限制 / 不能写的**
- ❌ **不得声称「跑了官方 SWE-bench harness」**：本评测是自研容器无关 harness，
  与官方在依赖安装、测试选择、超时策略、`problem_statement` 来源上都不同（§10.3 第 1–3 条）。
- ❌ 不得把本批 `resolved` 数字与官方 leaderboard / 他人 SWE-bench 子集分数并列比较。
- ⚠ 必须写成限制：
  - `problem_statement` 取自**提交信息**而非 issue（§3.4）⇒ 难度偏向未知；
  - **没有 Docker 镜像 / conda 环境**：环境类失败会落进我们的 `infra_error`，不是 agent 能力；
  - **网络未强制隔离**（§2.3 兜底，sampling log 已声明）；
  - n = 12 ⇒ 任何 < ~15 个百分点的差异不可解读为效应；单模型单配置 ⇒ 不构成模型间比较；
  - 「第三向」在 3/12 条上无额外判别力；
  - §4.2 的实现是对冻结文本的**按目的细化**，字面读法下本批 10/12 条不成立；
  - `E.git()` 读法会把补丁行尾折成 LF ⇒ 采集到的补丁文本**不含 CRLF**（不影响判定，但影响补丁体积的解读）；
  - `usage.jsonl` 这一路上的成本在 t5 那次是 null（D6 已修，但**修后尚未重跑一次真实 agent 验证**）。
- ⚠ 措辞纪律：`3/12 少一向判别力`、`10/12 依赖 §4.2 细化`、`D6 修法未经真实 agent 复核`
  这三件事都属「必须显形」的局限，写报告时不能省略。

## 14. 本次审查的边界（自我声明）

- 我**没有**跑真实模型调用（P0 的 agent 评测是 P1 的事）；§9 的冒烟复算是**重跑判定**，不是重跑 agent。
- §4、§5、§6 的并发/反例都是**我在本机现造**的，判据只认磁盘真实状态与独立 pytest 输出。
- 我**未改任何实现代码**：`git status --porcelain` 里实现文件（`coding_agent.py`、`eval/harness/`、
  `eval/lib/`、`eval/gold_check.py`、`eval/build_subset.py`、`servers/experiments/usage_log.py`、
  `eval/instances/`、`eval/PREREGISTRATION.md`）**无改动**；我新增的只有 `eval/reports/` 下的 1 份报告 + 8 个脚本
  （另有 t4/t5/t8 遗留的未提交产物，不属本次）。
