# t32 证据索引回填记录

执行时间：2026-10-13 23:03:49 +08:00
脚本：`eval/reports/p1_backfill_evidence_index.py --apply`
状态文件：`E:\aicoding\eval-runs\p1-20261013T122356-t20-batch\triple_state.jsonl`

这是对已归档 state 的**回填**，不是原始跑批时落盘。脚本拒绝改动除 `artifacts` 外的任何字段，并保存原始字节备份 `triple_state.jsonl.pre-t32`。

- 回填前 SHA-256：`17f3a7119fa01b20b1fa899c1fda2b3ef2af3f737b7444d9a6de334f17da8c78`
- 回填后 SHA-256：`bb89825ada4d1eace311376736ab5a2d5cff01a86389bc58cbc623a01d12bbff`
- 物理行数：27；C 行索引：9/9；A/B 行保持：18/18
- 字段级 diff：第 3、6、9、14、15、21、23、25、27 行各仅 `artifacts` 字段变化，均只追加 `<instance_id>/artifacts/policy_evidence_isolated.json`；`resolved`、`verdict_class`、`stage`、`notes`、`tokens` 等字段零变化。

## 生产路径离线证明

`t29_resume_check.py` 构造临时 C worker run，替换为无模型 FakeProcess，实际调用 `_run_one_arm`，将返回 state 行落盘后重新读取；输出：

```text
F6 state_row_index=offline-instance/artifacts/policy_evidence_isolated.json worker_generated=True
```

这证明未来跑批在 state 行写入前生成证据并附加索引；本节的 9 条归档索引仍必须称为**回填**。
