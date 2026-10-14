# P0 端到端冒烟报告（t5）

- **执行者**：harness-alt2（第三阶段 P0 实现者之一）
- **attempt_id**：`f4ff32ea-a6b1-4300-bc50-0bd3be0aec86`
- **被验证版本**：`3b906c1`（harness / coding_agent 实例入口）、`2a41e92`（12 条实例）、`27adbdd`（预注册）
- **纪律**：本任务只跑批与核对，**未改任何实现代码**（见 §5 核对 [5]）；判定只认 harness 的独立 pytest。
- **证据根目录**：`E:\aicoding\eval-runs\t5-smoke\`（agent 产物另在 `servers/experiments/out/`）

---

## 0. 结论先说

| 项 | 结论 |
|---|---|
| 端到端全流程（真实 agent 解题 → harness 判定） | ✅ **跑通**，产物齐全 |
| **harness 独立判定** | ✅ **`resolved=True`**（F2P 5/5、P2P 60/60） |
| **判定独立性（自述 vs 判定）** | ✅ **两者相反**：agent 侧自评「未成功」、harness 判 `resolved`。**判定完全不受自述影响**——这正是 P0 要检验的性质 |
| 单实例成本/耗时 | ⚠ 拿到了 trace 口径的实测值，但**`usage.jsonl` 这条线在错误路径上是 null**（接线问题 D6，见 §4） |
| 不变量（porcelain / 实例集 / 实现代码） | ✅ 全部守住 |

**一句话**：harness 确实能驱动我们的 agent 解题并独立判对；但这次冒烟**同时暴露了 3 个接线问题**（D6 用量线在 error 路径拿不到数、D7 agent 的写文件工具产出 CRLF 把 diff 变成整文件、D8 agent 撞 40 回合上限）——都不是阻断级，但都该在 P1 之前处理。

---

## 1. 选了哪条实例、为什么（纪律要求：不能挑最容易的还不说）

**选中：`pallets-click__4fd2fea`**（`pallets/click`，base `9ce34f20d2370f2e298268227edf0ffce6b9725f`）

选择理由（**先写在跑之前**，不是跑完回填）：

1. **F2P 最多**：5 条参数化 node（`test_flag_value_is_correctly_set[opts1/3/5/7/9-...]`），
   在 12 条里与 `pytest-dev-pluggy__dd20a85`（6 条）同档。判定样本厚 ⇒ 冒烟结论不容易被单点噪声主导。
2. **语义是典型的「定点修复」**：题目 =「Fix condition for setting flag value when type is provided」，
   属于「条件判断位置写错」这一类真实 bug，**不是**加新功能、也不是纯重构 ⇒ 与 SWE-bench 想测的能力对口。
3. **单测快、依赖少**：判定 pytest 墙钟 < 1 s，grid 不靠网络 ⇒ 冒烟的耗时能真实反映 agent 阶段，不被环境噪声淹没。
4. **可比性**：t4 独立复核对这条实例做过最多核对（T/G 观测点、P2P 基线全绿、provenance），
   我这次的结论可以直接与 t4 对表。

**没有挑最容易的**：另有 4 条 F2P=1 的实例（如 `astanin-python-tabulate__9dedd45`）明显更省 token，我**没有**选它们；
也没有回避 tabulate 那 3 条被 t4 标了 D5 的实例——只是从「F2P 厚度 + 语义对口」出发选了 click 这条。

**这条实例的已知局限**（随实例冻结，不是我这次引入的）：
仓库 addopts 为 `-m 'not stress'`；判定用临时 ini 替换仓库配置（预注册 §10.3 第 2 条的口径差异）。

---

## 2. 完整命令与关键原始输出

### 2.1 准备（建工作区 + 写 problem 文件）

```
D:\Anaconda3\envs\aicoding\python.exe eval/reports/_t5_scripts/t5_prepare.py pallets-click__4fd2fea t5-smoke
```

关键输出：

```
工作区        = E:\aicoding\eval-runs\t5-smoke\pallets-click__4fd2fea\repo
工作区 HEAD   = 796d0c2558b89f356a8455592baf462908024e1a
base_commit   = 9ce34f20d2370f2e298268227edf0ffce6b9725f
porcelain(跑前) = ''   （空 = 干净）
problem 原文（112 字符）：
    | Fix condition for setting flag value when type is provided
    |
    | Co-authored-by: Nate Herbert <nateherbert@gmail.com>
F2P (5 条)：tests/test_options.py::test_flag_value_is_correctly_set[opts1-True-True] 等 5 条
P2P：60 条；collection_scope=['tests']
venv          = E:\aicoding\eval-venvs\pallets__click-9ce34f20
venv 里的 .pth：['_eval_editable_076de2fe10731e0c.pth', '_t4_mount.pth', '_t4b_mount.pth']
```

> `problem_statement` 是**提交信息原文**（预注册 §3.4），我没有代笔、没有补 issue 链接。

### 2.2 跑 agent（**baseline 臂**）

```
D:\Anaconda3\envs\aicoding\python.exe -X utf8 coding_agent.py ^
  --repo-root    E:\aicoding\eval-runs\t5-smoke\pallets-click__4fd2fea\repo ^
  --problem-file E:\aicoding\eval-runs\t5-smoke\_problem.txt ^
  --label t5-smoke
```

**臂身份**：除 `--repo-root/--problem-file/--label` 外**没加任何开关** —— 没有 `--subagent`、
没有 `--delegate`、没有 `--repo-map-default`（走默认 `summary`）。
console 自证：`④ 子代理 = 关（现状）`、`repo_map 默认详细度 = 'summary'  （来源：默认（未设置））`。

关键输出（节选）：

```
实例模式：repo_root=E:\aicoding\eval-runs\t5-smoke\pallets-click__4fd2fea\repo
实例题面：problem_file=E:\aicoding\eval-runs\t5-smoke\_problem.txt  （570 字符）
mini Coding Agent  |  label=t5-smoke  repo_root=...  allow_write=True
=== 基线 pytest：退出码 2　失败 0　通过 0 ===
  ERROR tests/test_basic.py - pytest.PytestRemovedIn10Warning: Passing a non-Co...
  （实例模式：不校验基线失败数 —— 缺陷由 harness 的 test_patch 暴露，见 §4.1）
=== agent 开始 ===
...
[assistant] I now understand the setup: the repo's tests are the pre-fix versions (the grader restores
           the commit's test changes), so no in-repo test currently exercises the bug. Let me apply
           the fix to the root cause.
[tool_use] mcp__code-tools__apply_patch  {"path": "src/click/core.py", ...}  -> 已修改 src/click/core.py
[agent 异常] ResultError: Claude Code returned an error result: Reached maximum number of turns (40) (exit code: 1)
=== 独立验证（不经过 agent）===
  pytest 退出码：2 -> 1
  pytest 失败数：0 -> 1（通过 0 -> 667）
  改动文件：['pyproject.toml', 'src/click/core.py', 'tests/conftest.py']
  测试文件被改：['tests/conftest.py']
  ==> 判定：未成功
```

完整 console：`E:\aicoding\eval-runs\t5-smoke\console.log`（也留在 `servers/experiments/out/agent-t5-smoke.log`）

### 2.3 harness 独立判定

```
D:\Anaconda3\envs\aicoding\python.exe -X utf8 eval/reports/_t5_scripts/t5_collect_and_judge.py ^
  pallets-click__4fd2fea t5-smoke E:\aicoding\eval-runs\t5-smoke\pallets-click__4fd2fea\repo
```

关键输出：

```
porcelain(跑后)   = 'M pyproject.toml\n M src/click/core.py\n M tests/conftest.py'
采集到的 diff     = 251255 字节
diff 涉及文件     = ['pyproject.toml', 'src/click/core.py', 'tests/conftest.py']
测试侧文件        = ['tests/conftest.py']   touched_tests=True
剥离测试侧后剩     = ['src/click/core.py', 'pyproject.toml']

--- harness 独立判定 ---
  verdict_class = agent_result
  resolved      = True
  F2P 5/5
  P2P 60/60
  notes: ['候选补丁改动了 1 个测试侧文件，已按 §5.3 剥离后再判定']
```

---

## 3. 判定独立性：agent 自述 vs harness 判定（**本条的核心**）

### 3.1 对照

| | 来源 | 结论 |
|---|---|---|
| **agent 侧自评**（`coding_agent.py` 自己的 fixture 口径检查） | `==> 判定：未成功` | **未成功** |
| **harness 独立判定** | `verdict_class=agent_result`, `resolved=True`，F2P 5/5、P2P 60/60 | **已解决** |
| 两者是否一致 | | ❌ **相反** |

**这个「相反」是本次冒烟最有价值的一条结果**：
`coding_agent.py` 的自评是**它自己的**口径（「全仓 pytest 退出码 == 0 且没改测试」），
在这个实例上必然判「未成功」——因为 agent 撞了 40 回合上限，且工作区里还留着两处临时调试改动
（`pyproject.toml` 放开了 filterwarnings、`tests/conftest.py` 插了 sys.path），
加上 `tests/test_imports.py::test_light_imports` 本来就红。
而 **harness 只按预注册 §5.1 判**：在「基线 + test_patch」上应用候选补丁后，
`FAIL_TO_PASS` 逐条 `passed` ∧ `PASS_TO_PASS` 逐条 `passed`。

⇒ **两者相反，恰好证明「判定不是从 agent 自述来的」**。如果 harness 是采信自述的，它就会跟着判「未成功」。
P0 要检验的正是这个。

> 反过来也要说清：**agent 自评「未成功」并不等于 agent 没解出来**。
> 它那个口径与 §5.1 不是同一个东西（它判的是整个仓库的 pytest，而 §5.1 判的是 F2P/P2P 这 65 条 node）。
> 这正说明「自评」不能当判据 —— 两个口径都会给出「看起来合理」的答案。

### 3.2 harness 的判定依据（逐条）

判定命令（原始 pytest 命令行，来自 `harness_verdict.json` 的 `pytest.cmd`）：

```
<venv>\Scripts\python.exe -m pytest -p no:cacheprovider -q --timeout=120 ^
  -c <artifacts>\_eval_pytest.ini -o cache_dir=E:\aicoding\eval-runs\_pcache ^
  --rootdir=E:\aicoding\eval-runs\t5-smoke-judge\pallets-click__4fd2fea\repo ^
  --json-report --json-report-file=<artifacts>\verdict.json ^
  tests/test_options.py::test_flag_value_is_correctly_set[opts1-True-True] ... （共 65 个 node）
```

- `exit_code = 0`、`timed_out = false`、`wall_seconds = 0.673`
- **F2P 未过 = `[]`**（5/5 全过）
- **P2P 未过 = `[]`**（60/60 全过）

证据文件：

| 文件 | 路径 |
|---|---|
| harness 判定 JSON | `eval-runs\t5-smoke\harness_verdict.json` |
| 逐条 F2P/P2P 结果 | `eval-runs\t5-smoke-judge\pallets-click__4fd2fea\artifacts\judged.json` |
| 原始 pytest 输出 | `...\artifacts\verdict_run.txt` |
| 逐 node json report | `...\artifacts\verdict.json` |
| §5.3 剥离证据 | `...\artifacts\candidate_test_side_stripped.diff` |

### 3.3 agent 到底改对了吗（人工核对，不入判定）

把 agent 的改动**忽略行尾**后与 HEAD 比对，语义差异**恰好就是 gold 修复**：

```diff
@@ -2611,8 +2611,9 @@
                 self.default = False

+        if is_flag and flag_value is None:
+            flag_value = not self.default
+
         self.type: types.ParamType
         if is_flag and type is None:
-            if flag_value is None:
-                flag_value = not self.default
             # Re-guess the type from the flag value instead of the default.
```

即把 `flag_value` 的赋值**移到 `self.type` 推断之前** —— 与题目「Fix condition for setting flag value
when type is provided」完全对应。**agent 真的解出来了**，只是被回合上限打断、没做收尾清理。

---

## 4. 单实例成本与耗时

### 4.1 ⚠ 先说口径问题（这是本次暴露的接线问题 D6）

`usage.jsonl` 里我这一行**全是 null**：

```json
{"ts":"2026-10-12T23:50:29+08:00","script":"coding_agent.py","key_source":"env:.env",
 "model":null,"input":null,"input_uncached":null,"output":null,"cache_read":null,
 "cache_creation":null,"num_turns":null,"subtype":null,"is_error":null,
 "total_cost_usd":null,"result_usage":null,"models":{},
 "label":"t5-smoke", ...,"final_exit_code":1}
```

原因：这轮撞了 40 回合上限 ⇒ `run_agent` 抛 `ResultError` ⇒ `result` 为 `None` ⇒
`log_usage(None, ...)` 所有用量字段写 null。**也就是说 `usage.jsonl` 这条线在「agent 异常」路径上拿不到成本**
（`agent-t5-smoke.json` 里 `events: 0` 也是同一个根因 —— `summarize(events)` 因而全为 0）。

**好消息**：trace（`agent-t5-smoke.json` 的 `.trace.steps`）**逐回合 usage 是齐的**，
所以我从 trace 取数。**判定链路完全不依赖这条线**（判定只认独立 pytest），
所以 this 不影响 §3 的结论，但**会影响 P1 的预算外推**——必须先修 D6，否则「agent 失败的那部分运行」会系统性缺成本。

### 4.2 实测值（来源：trace 逐回合 usage）

| 口径 | 值 | 说明 |
|---|---|---|
| `input_uncached`（**主指标**，§10.1） | **45,310** | Σ 逐回合 `usage.input_tokens`（去重后 40 条 = 40 条，无分片） |
| `cache_read` | **1,916,160** | Σ `cache_read_input_tokens` |
| `cache_creation` | **0** | 该端点不报缓存写 |
| `output_tokens` | **0** ⚠ | **每个回合都是 0** —— 端点未回填该字段（见下） |
| Σ三口径（≈真实 prompt 规模） | 1,961,470 | `input_uncached + cache_read + cache_creation` |
| 回合数（trace turn 记录） | **40** | 撞上 `--max-turns 40` 上限 |
| 工具调用总数 | **77** | 见下表 |
| 墙钟 `last_step_t_ms` | **273,000 ms ≈ 273 s** | trace 最后一步的观察时刻（含模型生成+网络等待） |
| console 的 `elapsed_s` | **273.0** | 与 trace 一致 |
| `cost_usd` | **拿不到** | `usage.jsonl` 为 null（D6）；trace 不含 cost |

工具调用分布（77 次）：

| 工具 | 次数 |
|---|---|
| `read_file` | 28 |
| `run_tests` | 23 |
| `search_code` | 20 |
| `apply_patch` | 4 |
| `repo_map` | 1 |
| `list_files` | 1 |

### 4.3 端到端口径

**baseline 臂没有子代理/scout**（console 自证 `④ 子代理 = 关`），
`--delegate` 未传 ⇒ 无 driver 派发。因此：

- **父层** = 上表
- **子进程 / scout 侧** = **0**（无此类调用）
- **端到端合计** = 父层 = 上表

`servers/experiments/out/policy_scout_usage.jsonl` 是**其它批次**的产物，**不是本次**的，不计入。

### 4.4 用于 P1 预算外推的单价区间（**n=1 观测，不是分布**）

**必须先声明的三条限制**：

1. **n=1**：这是**一次观测**，不是分布。预注册 §10.3 第 5 条已说 n=12 时 Wilson 区间就有 ±25–29 个百分点，
   n=1 连区间都谈不上。
2. **这轮是「失败轮」**：撞 40 回合上限被强停 ⇒ **回合数与 token 可能偏向乐观**（它没走完收尾）。
   拿它当「一条实例要花多少」的上界比当期望值更靠谱。
3. **`output_tokens` 与 `cost_usd` 缺失**（D6），所以下面**只能给 token 区间、给不了美元**。

**单价（单实例、baseline 臂、deepseek-flash[1m]）**：

| 量 | 观测值 | 备注 |
|---|---|---|
| 未缓存输入 | 45,310 tok | 主指标 |
| 缓存读 | 1,916,160 tok | 是本轮成本结构的主导项（≈42× 未缓存输入） |
| 输出 | **缺**（字段为 0/未回填） | P1 前需修 D6 才能测准 |
| 回合 | 40（**被上限截断**） | 真实需要可能 > 40 |
| 工具调用 | 77 | |
| 墙钟 | 273 s | |

**外推用法（写清假设）**：若 P1 按 **12 条 × 1 配置** 跑，且假设单条约 45k 未缓存输入 /
1.9M 缓存读 / ≤300 s，则整批**上界估算**约 54 万未缓存输入、2300 万缓存读、≤1 小时墙钟（串行）。
**但**：① 这是 1 条实例的观测；② 该轮被回合上限截断；③ 不同仓库的上下文规模差异可能达数倍
（click 的 `core.py` 就有 3134 行）。⇒ **P1 实际排期前应先跑 3–5 条再定预算**，不要直接拿这个数字乘。

---

## 5. 不变量核对

由 `t5_invariants.py` 逐条量出（证据 `eval-runs\t5-smoke\invariants.json`）：

| # | 项 | 实测 | 结论 |
|---|---|---|---|
| 1 | 实例工作区 `git status --porcelain` 跑前 | `''`（空） | ✅ 干净检出 |
| 1 | 同上，跑后 | `M pyproject.toml` / `M src/click/core.py` / `M tests/conftest.py` | ✅ **可解释**：正是 agent 的候选补丁，§5.1 要采集的就是它 |
| 2 | 上游缓存仓库 `eval-cache\pallets__click` | porcelain `''`、HEAD `2247b35e...` | ✅ 我们只读它，未改动 |
| 3 | `eval/instances/` | porcelain `''`、未跟踪 `''` | ✅ **未被污染** |
| 4 | 仓库自身 `git status --porcelain` | 102 行；本次相关只有 `?? eval/reports/` 与 `servers/experiments/out/` 的 agent 产物 | ✅ 除预期产物外为空 |
| 5 | **实现代码是否被 t5 改动** | `git status` 对 `coding_agent.py`/`eval/harness`/`eval/gold_check.py`/`eval/build_subset.py`/`eval/lib`/`eval/PREREGISTRATION.md` = `''` | ✅ **未改实现代码** |

---

## 6. 本次暴露的接线问题（如实列出，不修，交 captain）

### D6（medium）`usage.jsonl` 在 agent 异常路径上拿不到用量

- **现象**：撞 40 回合上限 ⇒ `result=None` ⇒ `usage.jsonl` 全 null；`agent-t5-smoke.json` 的 `events: 0`
  ⇒ `summarize(events)` 全 0（`tool_calls_total=0`，与实测 77 次调用矛盾）。
- **影响**：**判定不受影响**（判定只认独立 pytest）；但 **P1 的成本外推会系统性缺掉所有失败轮** ——
  而「失败轮」恰恰是最贵的那批（它们往往跑满回合上限）。
- **建议**：`log_usage` 在 `result is None` 时改为从 trace 回填（trace 的逐回合 usage 是齐的，本报告 §4.2 证明了可行）。

### D7（medium）agent 的写文件工具产出 **CRLF**，把 diff 撑成整文件

- **现象**：agent 改完 `src/click/core.py` 后工作区是 **CRLF（3135 行）**，而基线 commit 是 **LF（3134 行）**。
  ⇒ `git diff` 变成 `@@ -1,3134 +1,3135 @@` 的**整文件单 hunk**，采集到的候选补丁 **251,255 字节**
  （真实语义改动只有 9 行）。
- **影响**：本次**判定仍正确**（harness 干净 apply 了整个文件，F2P/P2P 逐条结果无误）。
  但它带来三类风险：①补丁体积放大约 2.7 万倍；②「补丁能否干净 apply」的判据被行尾噪声放大；
  ③人工复核时看不出真实改动（我必须额外做一次「忽略行尾再 diff」才找到那 9 行）。
- **根因候选**：`fetch_workspace`/`fresh_workspace` 已经钉了 `core.autocrlf=false`，
  但**agent 侧写文件的 MCP 工具**（`apply_patch`）自己按 CRLF 写盘，不经过 git。
- **建议**：在 harness 采集补丁时把 CRLF 归一化（或在工作区 `.gitattributes` 钉 `* text=auto eol=lf`），
  并在判定前对候选补丁做一次行尾归一。

### D8（low）agent 撞 40 回合上限 —— 但这条实例其实是解出来的

- **现象**：`Reached maximum number of turns (40)`。agent 在第 40 回合已经改对了 `core.py`，
  但没机会清理它自己留下的两处临时调试改动（`pyproject.toml`、`tests/conftest.py`），也没写收尾说明。
- **影响**：不影响本次 `resolved` 结论（§5.3 剥离了测试侧，`pyproject.toml` 改动不干扰 F2P/P2P）。
  但它说明**在真实仓库上 40 回合可能不够** —— 这条实例光探索（28 次 read + 23 次 run_tests）就用掉大半预算。
- **建议**：P1 前把实例模式的回合上限抬高（或做成参数），并在题面里减少无效探索
  （agent 花了大量回合去确认「仓库自带测试是修复前的版本」——这是实例构造方式的固有性质，
  它自己最后也想明白了）。

### 附：一个非问题的观察（为了不让读者误解 D7）

agent 在探索期做的 `apply_patch conftest.py` 第一次失败（`不存在或不是文件：conftest.py`），
第二次才改到 `tests/conftest.py` —— 这是 agent 自己的试错，**不是 harness 接线问题**，
记在这里只为解释 console 里的那条错误行。

---

## 7. 与 t4 复核的关系（对表）

t4 报了 D1（`.pth` 竞态，blocker）与 D3（静默误判无检测）。本次冒烟顺带现场核对了两者的**当前形态**：

- 该实例的 venv（`pallets__click-9ce34f20`）里确实残留 t4 的 `_t4_mount.pth` / `_t4b_mount.pth`，
  且 `_eval_editable_*.pth` 当时指向 `t4-verify-final2` 的工作区（**跨 run 共享 venv，D1 的成因**）。
- **但本次 single-run 串行执行下，判定结果正确**：`ensure_venv` 会重写自己的 `.pth`
  并**强制自检**「导入路径必须落在本实例工作区」（不满足即 `InfraError`）——
  这条自检正是 D3 建议的缓解手段，**已经在实现里**（`eval/harness/env.py` 的 `_assert_pkg_from_workspace`
  等价逻辑 + `run_instance.py` 的 `import 自检`）。
- 我另外实测：`import click` 在这个 venv 里**确实**会因残留 `.pth` 指向旧路径而先受干扰，
  但新建工作区后正确定位到本次工作区。**D1/D3 的修法仍应由 captain 决定**（我按纪律没改实现代码）。

---

## 8. 复算入口（第三方想重跑）

```powershell
# 1) 前置：确认实例与 venv
D:\Anaconda3\envs\aicoding\python.exe eval/reports/_t5_scripts/t5_precheck.py

# 2) 建工作区 + 写 problem 文件
D:\Anaconda3\envs\aicoding\python.exe eval/reports/_t5_scripts/t5_prepare.py pallets-click__4fd2fea t5-smoke

# 3) 跑 agent（baseline 臂）
D:\Anaconda3\envs\aicoding\python.exe -X utf8 coding_agent.py --repo-root E:\aicoding\eval-runs\t5-smoke\pallets-click__4fd2fea\repo --problem-file E:\aicoding\eval-runs\t5-smoke\_problem.txt --label t5-smoke

# 4) 采集 + 独立判定
D:\Anaconda3\envs\aicoding\python.exe -X utf8 eval/reports/_t5_scripts/t5_collect_and_judge.py pallets-click__4fd2fea t5-smoke E:\aicoding\eval-runs\t5-smoke\pallets-click__4fd2fea\repo

# 5) 用量（trace 口径）
D:\Anaconda3\envs\aicoding\python.exe -X utf8 eval/reports/_t5_scripts/t5_usage_from_trace.py servers/experiments/out/agent-t5-smoke.json

# 6) 不变量
D:\Anaconda3\envs\aicoding\python.exe -X utf8 eval/reports/_t5_scripts/t5_invariants.py t5-smoke pallets-click__4fd2fea
```

> ⚠ 第 3 步会**重新调用真实模型并产生费用**。第 4 步之后的所有步骤都是只读/幂等的。

---

## 附录 A：产物清单

| 产物 | 路径 | 大小 |
|---|---|---|
| agent 事件 JSON（含 trace） | `servers/experiments/out/agent-t5-smoke.json` | 172,602 B |
| agent console | `E:\aicoding\eval-runs\t5-smoke\console.log` | — |
| agent 自述摘录 | `E:\aicoding\eval-runs\t5-smoke\agent_self_report.json` | — |
| **harness 判定 JSON** | `E:\aicoding\eval-runs\t5-smoke\harness_verdict.json` | — |
| **实例工作区最终 diff** | `E:\aicoding\eval-runs\t5-smoke\agent_candidate_patch.diff` | 251,255 B |
| 跑后 porcelain | `E:\aicoding\eval-runs\t5-smoke\porcelain_after.txt` | — |
| 判定证据目录 | `E:\aicoding\eval-runs\t5-smoke-judge\pallets-click__4fd2fea\artifacts\` | — |
| 用量（trace 口径） | `servers/experiments/out/t5_usage_from_trace.json` | — |
| 不变量核对 | `E:\aicoding\eval-runs\t5-smoke\invariants.json` | — |
| 准备记录 | `E:\aicoding\eval-runs\t5-smoke\prepare.json` | — |

## 附录 B：本次用到的脚本（都在 `eval/reports/_t5_scripts/`）

| 脚本 | 作用 |
|---|---|
| `t5_precheck.py` | 候选清单、venv/.pth 现状、仓库状态（只读） |
| `t5_venv_shadow_probe.py` | 实测残留 `.pth` 是否遮蔽工作区（只读） |
| `t5_prepare.py` | 建工作区 + 写 problem 文件 + 记录跑前状态 |
| `t5_collect_and_judge.py` | 采集 diff + 摘自述 + 调 harness 判定 |
| `t5_usage_from_trace.py` | 从 trace 提 token/回合/工具分布 |
| `t5_invariants.py` | 不变量逐条核对（只读） |

**全部脚本均不修改实现代码**，只读实例定义、调用 harness 的公开入口、把产物写进 `eval-runs/`。