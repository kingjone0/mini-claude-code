# t22 独立复验：作用域扫描与判定门禁

**verdict: pass**。独立读取代码、检查提交差异并重新运行离线门禁；未调用模型，未启动 wiring/正式跑批，新增运行槽位 **0**。验证基线 `22b950aee60da91fefcc4e8e0f2e982c689d4c8a`。

## 1. 作用域扫描与逐项人工分类

独立执行：

```text
D:\Anaconda3\envs\aicoding\python.exe eval/harness/placeholder_scan.py --scope implementation
scope=implementation matches=0
implementation_hits=0 document_quotes=0
IMPLEMENTATION_EXIT=0

D:\Anaconda3\envs\aicoding\python.exe eval/harness/placeholder_scan.py --scope all
scope=all matches=2
eval/reports/p1-batch.md:129 [报告引文] 历史 t13-start 产物的占位 notes
 eval/reports/t19-verification-round-2.md:34 [报告引文] t19 反向样本的 notes
implementation_hits=0 document_quotes=2
ALL_EXIT=0
```

逐行用 UTF-8 Read 工具复核，不仅依赖扫描器的自动标签：`eval/reports/p1-batch.md:129` 叙述历史 t13-start 调度器只写待判 notes、缺独立 pytest 的事实，是**历史引文**；`eval/reports/t19-verification-round-2.md:34` 引用构造的占位行 notes，是**反向测试证据**。两处都是 Markdown 报告，非运行态实现。`eval/harness/placeholder_scan.py:11-25` 固定扫描实施路径 `eval/harness`、`eval/lib`、`coding_agent.py`、`agent`，全域附加 `eval/reports/**/*.md`；它把精确旧句拆段构造，不会自命中；`verdict_gate.py:26` 的宽泛运行态正则仍存在。实现路径精确旧句为零。扫描器所谓 all 是其显式定义的全域（实现+报告），而非整个仓库任意文件；判据以此定义为准。

## 2. 正反门禁（重做而非转述）

本轮自行写入 `E:\aicoding\eval-runs\_t22-placeholder.jsonl`，一行 `instance_id=astanin-python-tabulate__5373ebf, arm=A, run_id=p1-t22-synthetic-A, verdict_class=agent_result, resolved=false, stage=judged`，notes 使用历史待判句。直接运行门禁，终端在 GBK 下中文乱码，路径/退出码原样摘录：

```text
[FAIL] 判定产物门禁  rows=1
  第1行 astanin-python-tabulate__5373ebf/A: notes 含待判定/占位措辞
  第1行 astanin-python-tabulate__5373ebf/A: 缺判定产物 judged.json: E:\aicoding\eval-runs\p1-t22-synthetic-A\astanin-python-tabulate__5373ebf\artifacts\judged.json
  第1行 astanin-python-tabulate__5373ebf/A: 缺判定产物 verdict.json: E:\aicoding\eval-runs\p1-t22-synthetic-A\astanin-python-tabulate__5373ebf\artifacts\verdict.json
  第1行 astanin-python-tabulate__5373ebf/A: 缺判定产物 result.json: E:\aicoding\eval-runs\p1-t22-synthetic-A\astanin-python-tabulate__5373ebf\artifacts\result.json
  第1行 astanin-python-tabulate__5373ebf/A: 缺判定产物 verdict_run.txt: E:\aicoding\eval-runs\p1-t22-synthetic-A\astanin-python-tabulate__5373ebf\artifacts\verdict_run.txt
NEGATIVE_EXIT=2
[PASS] 判定产物门禁  rows=3
POSITIVE_EXIT=0
```

正向输入是 t18 归档 `E:\aicoding\eval-runs\p1-20261013T110701-wiring-t9-cfix\triple_state.jsonl`。另读同目录 `arm_check_result.json`：`pass=true`；三臂均 `agent_result / resolved=true / stage=judged`，A/B/C 各自 venv 路径哈希不同。没有重复运行模型探针。

## 3. t21 改动及判定链前后对照

```text
git show --stat --oneline 22b950ae
22b950a P1: scope placeholder scan and correct t19 verdict
 eval/harness/placeholder_scan.py         | 52 ++++++++++++++++++++++++++++++++
 eval/reports/t19-verification-round-2.md | 11 ++++---
 2 files changed, 59 insertions(+), 4 deletions(-)

git show --name-status --format='' 22b950ae
A eval/harness/placeholder_scan.py
M eval/reports/t19-verification-round-2.md

git diff 2303d30 22b950ae -- eval/harness/scheduler_p1.py eval/harness/verdict_gate.py eval/harness/judge.py eval/harness/arms.py coding_agent.py
[空输出，exit 0]
```

因此 t21 没有修改 `judge_arm_workspace`、判定函数或门禁判据。当前调用链仍是 `scheduler_p1.py:241` → `judge_arm_workspace:98` → `J.stripped_patch:113` → `RI.pytest_run:145` → `J.verdict_from:153`；`verdict_gate.py:26,35-93,96-124` 仍验证占位 notes、判定产物、逐 node 对照和退出码。git 的前后差异为空直接证明 t21 未更改语义，不靠作者自述。

## 4. 不变量核对

| 不变量 | 代码或产物证据 |
|---|---|
| 超时先于退出码 | `eval/harness/judge.py:261-275` 的 `if timed_out` 先于 rc；`scheduler_p1.py:230-239` 同样先判 timed_out 后判非零退出 |
| infra/timeout 不进分母 | `judge.py:297-306` 返回独立类别与 false，`309-322` 仅 `COUNTS_IN_DENOMINATOR` 汇总；`mechanism_check.py` timeout_priority 通过 |
| 每 `(实例, run)` venv 隔离 | `scheduler_p1.py:191-192,299,319-320` 将 run_id 送 `venv_dir`/`ensure_venv`；归档 A/B/C venv 后缀为 `32535824`/`76ce1d77`/`d7810f09`，互不相同 |
| 测试侧剥离后判定 | `scheduler_p1.py:113` 调 `J.stripped_patch`，`125-145` 重置工作区、先应用冻结 test_patch、再只应用 kept 源码补丁、最后运行独立 pytest |
| 臂定义不变 | `arms.py` 的 A 空参数、B 仅 `--repo-map-default symbols`、C 仅 `--delegate policy`；t21 对 `arms.py` diff 为空，真实状态行的 `arm_extra_args` 与此一致 |

## 5. 复验边界与结论

队长已将旧 `wiring_probe.py --run-tag t9-cfix` 验证命令替换为七条全离线命令。本轮**未执行**任何真实模型探针，而复用 t18 完整的 T110701 集成工件；独立运行作用域扫描、正反门禁及静态差异核验。`gold_check.py` 退出 0、12/12 三向通过；`mechanism_check.py` 退出 0、四项 PASS；`subagent_selfcheck.py` 退出 0、零失败。没有运行正式批次，也没有改动任何实现代码。`verdict=pass` 指有作用域扫描与门禁语义复验均通过；不把文档里的历史引用伪报为实现占位。
