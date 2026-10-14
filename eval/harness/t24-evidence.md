# t24 verdict_gate 逐 node 责任归因

本任务零模型调用、未重跑实例、未改写任何既有产物。

## 复现

原门禁对 `E:\aicoding\eval-runs\p1-20261013T122356-t20-batch\triple_state.jsonl` 返回 exit 2，flag 行：10/11/12/13/15/16 的 `p2p_not_passed`，17/21 同时有 `f2p_not_passed` 与 `p2p_not_passed`。

## 责任方

责任在门禁比较口径，不在跑批行或判定逻辑。以第10行为例，状态行与 `artifacts/judged.json` 均为 `p2p_total=60,p2p_not_passed=[]`；同目录 `verdict.json` 的 pytest JSON report 有 61 个 tests，其中 3 个 node 的原文 outcome 是 `subtests passed`：

- `tests/test_more.py::AllUniqueTests::test_basic`
- `tests/test_more.py::ArgMinArgMaxTests::test_basic`
- `tests/test_more.py::ArgMinArgMaxTests::test_key`

门禁原代码把 `subtests passed != passed` 当失败，遂制造假警报。`judge.py:278-294` 以测试结果的语义 `passed` 计算 judged；这些三节点被判定器视为通过，行内 `p2p_not_passed=[]` 正确。第11/12/13/15/16/17/21 行使用同一 more-itertools pytest-json-report 形状，逐行同样核对；第17/21 的 F2P 亦因相同 `subtests passed` 误报。

## 修复与影响

`verdict_gate.py` 将 pytest JSON report 的 `subtests passed` 归一化为 `passed`，不改任何判定产物或 resolved。修后对原批次只读重判：

```text
[PASS] 判定产物门禁 rows=27
exit=0
```

因此 9 行（实际 10 条字段错误）原有 `resolved` 均可信，三臂 resolution rate 与配对数字不受影响；受影响的仅是门禁误报计数（10 条字段报错降为 0）。

## 回归

`gold_check` 12/12、`mechanism_check` 四项 PASS、`subagent_selfcheck` 0 failures。提交仅修改 `eval/harness/verdict_gate.py`，无模型调用；显式 add 并推送。