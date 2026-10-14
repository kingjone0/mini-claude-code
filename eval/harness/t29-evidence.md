# t29 F5/F6 离线证据

零模型调用、零实例重跑、未修改正式批次产物。

```text
BEFORE skipped=['A', 'C'] pending=['B'] B=timeout/False
BEFORE simulated B=agent_result/True rows=4 (old terminal overwritten)
AFTER skipped=['A', 'B', 'C'] pending=[] manifest.skipped_failed=1 rows=3 sha256=901dda18a8b399bf86edc4a2368bc0f2351fef8b33439074fac8442a5c434abb unchanged=True
BUDGET unique_keys=1 actual_spawn_rows=2 counted=2
F6 run_relative_artifact=offline-instance/artifacts/policy_evidence_isolated.json
```

旧逻辑只跳过 `stage=judged`，因此 timeout/infra 会 pending；新 `plan_resume` 将 judged、failed_infra、timeout、infra、harness_bug 都视为终态，manifest 的 `skipped_failed` 记录失败终态数。补跑必须走新批次（新的预注册与预算），resume 不重跑已有终态。

`count_spawned_arms` 按 state 行的 `started_at_epoch` 计实际 spawn，不按 `read_triple_state` 的 `(instance,arm)` 去重；同一键两次实际启动计数为 2。t29 的 F6 断言仅证明**函数返回值正确（未在生产 state 行验证落盘）**。t32 选择方案②：C worker 在返回 state 行前生成 `policy_evidence_isolated.json`，再追加 run-relative 索引；离线桩真实调用 `_run_one_arm` 并把返回 state 行落盘重读，输出 `F6 state_row_index=offline-instance/artifacts/policy_evidence_isolated.json worker_generated=True`。已归档 C 行的索引另于 2026-10-13 23:03:49 +08:00 **回填** 9/9（详见 `eval/reports/p1-t32-backfill.md`），不能声称当时落盘。
