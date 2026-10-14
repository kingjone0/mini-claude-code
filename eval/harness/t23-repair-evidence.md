# t23 止损与 REV2 预算修复证据

本任务零模型调用、零运行槽位。

## 缺陷复现

旧版 `scheduler_p1.py` 使用 `MAX_RUNS_TOTAL = 39`、`BATCH_BUDGET_SECONDS = 8 * 3600`，并一次性 `pool.submit` 全部任务；限额检查在 `as_completed` 之后。`break` 不能取消已排队 future，进程池退出还会等待已提交任务，因此连续 infra 止损和 token/美元动态止损均不成立。REV2 `242e007` 改为已启动臂 ≤75、12h、input_uncached 6,300,000、cache_read 126,000,000、$225。

## 修复与离线桩测试

现在 scheduler 使用最多 `max_workers` 个 future 的滑动窗口；每次提交前检查已启动臂、墙钟、token、cache、美元和连续 infra/timeout。止损后不再提交新臂，进程池中没有未启动排队任务。

```text
python eval/harness/t23_stub_test.py
submitted=2 after_stop=2 reason=A consecutive infra/timeout >= 2
```

该桩序列构造四个连续 infra 结果，第 3 个提交前已因前两次失败止损，提交计数停止增长。

REV2 起点可通过 `P1_REV2_INITIAL_STARTED` 配置，默认 35；usage 从 `servers/experiments/out/usage.jsonl` 与 `policy_scout_usage.jsonl` 汇总并写入 manifest。`--resume` 读取已有 `triple_state.jsonl`，不会将累计启动数清零。
