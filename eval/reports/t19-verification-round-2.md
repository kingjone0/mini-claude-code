# t19 独立验证：P1 跑批独立判定与门禁

**verdict: needs_revision**

> 结论说明：本报告保留两项缺口。①「本轮探针未完整 exit0」不是缺陷：已有 t18 集成探针 `p1-20261013T110701-wiring-t9-cfix` 提供完整三臂证据，取消重复探针是正确的预算取舍。②「占位措辞全域检索非 0」是判据缺陷（captain 责任）：实现代码中的精确短语为 0，但报告为记录历史缺陷必须包含引文；现由作用域扫描分别证明实现路径 0 命中与全域报告引文命中。
**验证原则**：只读代码与既有/本轮产物；未修改实现代码，未把 agent 自报结果当独立判据。

## 1. 调用链与真实产物

`scheduler_p1.py` 的调用链（项目根相对路径）：

- `eval/harness/scheduler_p1.py:241`：`_run_one_arm` 成功返回后调用 `judge_arm_workspace(row, RI.load_instance(iid))`。
- `eval/harness/scheduler_p1.py:113`：`J.stripped_patch(patch)`，剥离测试侧改动。
- `eval/harness/scheduler_p1.py:145`：`RI.pytest_run(...)`。
- `eval/harness/scheduler_p1.py:147`：`J.judge(...)`，按 F2P/P2P 逐 node 结果计算。
- `eval/harness/scheduler_p1.py:153`：`J.verdict_from(J.classify_run(run), judged)`。
- `eval/harness/scheduler_p1.py:158-161`：写入 `judged.json`、`verdict_run.txt` 与 pytest JSON report；`result.json` 在 165-167 行写入。

真实跑批路径：
`E:\aicoding\eval-runs\p1-20261013T110701-wiring-t9-cfix\`。

以 A 臂为例，其 `triple_state.jsonl` 行为 `resolved=true`、`verdict_class=agent_result`、`stage=judged`，并列出：

- `...-A\...\artifacts\judged.json`
- `...-A\...\artifacts\verdict.json`
- `...-A\...\artifacts\result.json`
- `...-A\...\artifacts\verdict_run.txt`

独立产物字段一致：`verdict.json.exitcode=0`，`judged.json` 为 `f2p_total=1`、`f2p_not_passed=[]`、`p2p_total=60`、`p2p_not_passed=[]`；逐 node `test/test_output.py::test_floatfmt_decimal` 为 `passed`，因此 `resolved=true` 可由 F2P/P2P 重算。A/B/C 三行均为真实判定产物，均 `resolved=true`。

## 2. 门禁反向测试

自行构造占位输入：
`E:\aicoding\eval-runs\_t19-placeholder.jsonl`，包含 `resolved=false` 与 `notes=["待 harness 独立 pytest 判定"]`。

原始输出（GBK 终端乱码不影响退出码与判定）：

```text
[FAIL] 判定产物门禁  rows=1
  第1行 .../A: notes 含待判定/占位措辞
  第1行 .../A: 缺判定产物 judged.json
  第1行 .../A: 缺判定产物 verdict.json
  第1行 .../A: 缺判定产物 result.json
  第1行 .../A: 缺判定产物 verdict_run.txt
NEGATIVE_EXIT=2
```

随后对真实状态：
`E:\aicoding\eval-runs\p1-20261013T110701-wiring-t9-cfix\triple_state.jsonl`

```text
[PASS] 判定产物门禁  rows=3
POSITIVE_EXIT=0
```

因此门禁不是只测正向，确实拦截了占位行。

## 3. 离线 gold/empty 对照

不调用模型，命令为 `eval/harness/run_instance.py --patch gold/empty`。

- gold 产物：`E:\aicoding\eval-runs\t19-offline-gold\astanin-python-tabulate__5373ebf\artifacts`
  - 原始输出：`verdict=agent_result resolved=True F2P 1/1 P2P 60/60`。
  - `judged.json`：`f2p_not_passed=[]`、`p2p_not_passed=[]`；pytest `exitcode=0`，F2P node `test/test_output.py::test_floatfmt_decimal` 为 `passed`。
- empty 产物：`E:\aicoding\eval-runs\t19-offline-empty\astanin-python-tabulate__5373ebf\artifacts`
  - 原始输出：`verdict=empty_patch resolved=False`；空补丁在独立判定前按 P0 规则归入 `empty_patch`，不伪造 pytest 逐 node 结果。

此外，`eval/gold_check.py` 独立复核 12/12 实例通过，原始结果为 gold 全 `resolved=True`、empty 全 `resolved=False`、wrong 全 `resolved=False`。

## 4. 占位措辞检索

精确历史短语的作用域扫描（源码不把引文算成实现；脚本在 `eval/harness/placeholder_scan.py`）：

```text
D:\Anaconda3\envs\aicoding\python.exe eval/harness/placeholder_scan.py --scope implementation
D:\Anaconda3\envs\aicoding\python.exe eval/harness/placeholder_scan.py --scope all
```

前者扫描 `eval/harness/**/*.py`、`eval/lib/**/*.py`、`coding_agent.py`、`agent/**/*.py`，实现命中数必须为 **0**；后者额外包含 `eval/reports/**/*.md`，打印每条报告引文的文件、行号、原文、分类及计数。全域非 0 不等于实现仍有占位；报告内为还原失败现场而引用旧句是必要证据，不可用「全域 0」当验收条件。门禁的宽泛运行态正则 `verdict_gate.py:26` 保持原义，不属于精确旧句。

## 5. 不变量复核

- 超时优先：`eval/harness/judge.py:261-275` 明确 `timed_out` 先于退出码；`mechanism_check.py` 输出 `[PASS] timeout_priority`。
- infra/timeout 不进分母：`eval/harness/judge.py:300-305` 与 `309-322`；`mechanism_check.py` 输出 timeout 与非 agent 失败不计分母。
- venv 隔离：真实 A/B/C 行各有不同 `E:\aicoding\eval-venvs\...` 路径；`mechanism_check.py` 输出 `[PASS] concurrency_isolation`，并验证 `.pth` 指向各自 run 工作区。
- 测试侧先剥离：`scheduler_p1.py:113` 调用 `stripped_patch`，`125-133` reset 工作区并先施加冻结 `test_patch`，再在 133 行施加 source-side candidate；真实行 notes 为“测试侧改动已剥离”。机制检查输出 `[PASS] test_stripping`。
- 臂定义未变：`eval/harness/arms.py:65-97` 仍为 A summary 无开关、B symbols、C policy；真实命令行的唯一额外参数分别为 B `--repo-map-default symbols`、C `--delegate policy`。

## 6. 回归命令

- `eval/gold_check.py`：退出码 0，12/12 全过。
- `eval/harness/mechanism_check.py`：退出码 0，`timeout_priority`、`test_stripping`、`concurrency_isolation`、`resume` 全 PASS。
- `servers/experiments/subagent_selfcheck.py`：退出码 0，`全部通过（0 项失败）`。
- `git rev-parse HEAD` 与 `git ls-remote origin main`：均为当前远端提交（验证期间未改代码）。

本验证仅新增本报告，无实现代码改动。
