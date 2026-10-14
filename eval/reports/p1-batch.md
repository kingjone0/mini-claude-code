# P1 跑批原始记录（verification-round-2）

> t32 证据索引**回填**（2026-10-13 23:03:49 +08:00）：此文件早期关于「未跑批」的历史门槛记录不能替代后续 t20 批次。归档 t20 的 `triple_state.jsonl` 原有 27 行，其中 C 臂 9 行的 `policy_evidence_isolated.json` 索引系**事后回填**，绝非当时落盘；A/B 18 行原字节未改。脚本：`eval/reports/p1_backfill_evidence_index.py --apply`。回填前 SHA-256 `17f3a7119fa01b20b1fa899c1fda2b3ef2af3f737b7444d9a6de334f17da8c78`；回填后 `bb89825ada4d1eace311376736ab5a2d5cff01a86389bc58cbc623a01d12bbff`。逐行字段 diff：第 3、6、9、14、15、21、23、25、27 行只有 `artifacts` 增加该 run 的相对索引，其他字段零变化；C 索引 9/9。原始字节备份位于归档 state 同目录 `.pre-t32`。详见 `eval/reports/p1-t32-backfill.md`。

> 状态：**未跑批**。本文件仅记录阻断式门槛原始证据与证据索引，不包含臂间统计、失败归因或结论。

## 1. 阻断式前置门槛

执行工作目录：`E:\aicoding\mini-claude-code`

### 1.1 HEAD / 提交证据

执行命令：

```text
Set-Location E:\aicoding\mini-claude-code; git log -5 --oneline --decorate
```

原始输出：

```text
220da21 (HEAD -> main, origin/main, origin/HEAD) 第三阶段 P1 ②：harness 多臂支持（臂映射单一真值源 + 实例内三臂交错 + 臂接线自检）
76f34a6 第三阶段 P1 ①：预注册 §3.1/§3.4 措辞收紧（「禁止报 p 值」这条自身不得写成 p 值）
6302dae 第三阶段 P0 ⑨：文档同步（README + eval/README + CLAUDE.md 陷阱 54–60）+ 复核证据入库 + 中文日志
7e64816 第三阶段 P0 ⑧：修隔离竞态（D1）+ 空证断言（D2）+ 用量/行尾/回合上限（D6/D7/D8）
14bdd10 第三阶段 P0 ⑤：端到端冒烟 —— 真实 agent 跑 1 条实例 + 单实例成本耗时
```

门槛结论：**FAIL / 阻断**。当前 HEAD 不包含 t9 的 C 臂修复提交；t7 的修复改动也尚未形成提交（工作树存在修改）。因此不满足“修复已提交并位于 HEAD”的前置条件。

### 1.2 最近一次接线自检三臂终态

证据文件：
`E:\aicoding\eval-runs\p1-20261013T071953-wiring-t7-final\triple_state.jsonl`

原始三行（字段完整内容见该文件；以下保留门槛相关字段）：

```json
{"instance_id":"astanin-python-tabulate__5373ebf","arm":"A","run_id":"p1-20261013T071953-astanin-python-tabulate__5373ebf-A","resolved":false,"verdict_class":"agent_result","stage":"judged"}
{"instance_id":"astanin-python-tabulate__5373ebf","arm":"B","run_id":"p1-20261013T071953-astanin-python-tabulate__5373ebf-B","resolved":false,"verdict_class":"agent_result","stage":"judged"}
{"instance_id":"astanin-python-tabulate__5373ebf","arm":"C","run_id":"p1-20261013T071953-astanin-python-tabulate__5373ebf-C","resolved":false,"verdict_class":"infra_error","stage":"failed_infra"}
```

原始文件实际三行均包含命令行、工作区、venv、console 产物路径；完整原文不在本报告中重写，防止截断，路径即证据索引。

门槛结论：**FAIL / 阻断**。C 臂 `verdict_class=infra_error`，不满足三臂接线自检要求；不得以 arm_check 的 W1–W4/G–K/identity PASS 替代 C 臂真实 agent_result。

## 2. 接线门槛尝试记录

本轮曾启动：

```text
Set-Location E:\aicoding\mini-claude-code; & 'D:\Anaconda3\envs\aicoding\python.exe' eval/harness/wiring_probe.py
```

调度器输出的原始开头：

```text
P1 §5.7 接线自检  |  instance=astanin-python-tabulate__5373ebf  tag=wiring-wiring
[p1-sched] P1 批次 p1-20261013T073546-wiring-wiring  | 实例 1 臂 ['A','B','C'] 并发 1 续跑=False
[p1-sched] 计划 3 槽
[p1-sched] ▶ A astanin-python-tabulate__5373ebf run_id=p1-20261013T073546-astanin-python-tabulate__5373ebf-A
```

该尝试在收到阻断指令后立即取消，未作为放行证据；不启动正式 36 次跑批。其产物（若存在）仅作取消尝试的运行痕迹，不纳入正式数据。

## 3. 正式跑批状态

计划：12 条冻结实例 × A→B→C = 36 次。

实际正式跑批：**0/36**。

停止原因：阻断式前置门槛失败（HEAD 未含 t9 修复提交；最近一次 probe 的 C 臂为 `infra_error`）。按预注册 §4.4/§5.7/§7.3，不放宽判据、不替换近似实现、不继续消耗模型预算。

因此本报告没有逐实例 `resolved` 表、pytest 判定、成本汇总或 resume 演练；这些字段在未通过门槛时不应伪造。

## 4. 冻结实例与范围证据

命令：

```text
git status --porcelain -- eval/instances/pilot-12.jsonl
```

原始输出：空（未报告该冻结文件改动）。

跑批前冻结文件 SHA256（记录于门槛检查）：

```text
7B6764014A5D938C98AC395C6FF49DC48E1ADE5686547D2D5AB54ACA2105CBC7
```

本轮未修改实现代码；停止前仅新增本报告（以及调度器取消尝试可能产生的仓外运行痕迹）。

## 5. 预算 / 接线自检消耗索引

预注册 §7.3 总运行上限为 ≤39（含接线自检）。逐轮目录盘点（每个 triple_state.jsonl 行数即已落盘槽位；未完成目录另行标注）：

| 批次 | 已落盘臂槽数 | 终态摘要 |
|---|---:|---|
| `p1-20261013T052518-wiring-t3-precheck` | 3 | infra_error=3 |
| `p1-20261013T052711-wiring-t3-precheck` | 3 | infra_error=3 |
| `p1-20261013T052800-wiring-t7-fix1` | 3 | agent_result=3 |
| `p1-20261013T054508-wiring-t7-fix2` | 3 | agent_result=3 |
| `p1-20261013T055618-wiring-t7-fix3` | 3 | agent_result=2, infra_error=1 |
| `p1-20261013T060947-wiring-t7-fix4` | 3 | agent_result=2, infra_error=1 |
| `p1-20261013T062026-wiring-t7-fix5` | 3 | infra_error=3 |
| `p1-20261013T063428-wiring-t7-fix6` | 2 | agent_result=1, infra_error=1 |
| `p1-20261013T071953-wiring-t7-final` | 3 | agent_result=2, infra_error=1 |
| `p1-20261013T073546-wiring-wiring` | 0 | `triple_state.jsonl` 不存在；A/B/C 三个臂目录存在；本轮由本执行者启动后按阻断指令取消 |
| `p1-20261013T073719-wiring-t9-cfix` | 1 | 当前可读文件仅 A=agent_result；B/C 尚未在该文件落盘（该轮由 t9 侧产生，非本执行者启动） |

已落盘接线臂槽合计：**27**（不含取消轮的 3 个空目录，不含 t9-cfix 尚未落盘的 B/C）。其中前置证据要求的 `fix1`–`fix6`、`t3-precheck`、`t7-final` 已逐轮列出；`t9-cfix` 仍需在后续预算审计中补齐最终三臂落盘/用量。上述盘点说明原预注册“≤39（含自检）”已无法同时容纳这些自检与 36 次正式运行；应在正式跑批前由预注册修订任务重算上限。

完整逐轮原始证据索引：`E:\aicoding\eval-runs\p1-*wiring*\triple_state.jsonl`、各批次 `manifest.json`、各臂 `artifacts` 与 usage 产物。本轮正式 36 次为 0 次。

## 6. t13 暂停尝试与 REV2 记账（历史续页，待用户启动）

前置清单核对：`git log` 同时含 t9 `9390f4f`、t10 `242e007`；本轮观察到本地 HEAD 和 `origin/main` 都为 `242e0070acd3a820802c52f710d06368a22669f8`。最近完整的 `p1-20261013T073719-wiring-t9-cfix/triple_state.jsonl` 共 A/B/C 三行且均为 `agent_result`；此项仅用于启动前前置判据，不作为新的 t13 接线探针 PASS。

在暂停指令到达之前，执行了 `eval/harness/wiring_probe.py --run-tag t13-gate`；输出至计划 3 槽、批次 `p1-20261013T080248-wiring-t13-gate`，未出现任一 `▶` 臂进程启动行。收到暂停后立即取消，后台 job 回报 `killed before exit`。随后的独立盘点：批次目录 0 文件（无 `triple_state.jsonl` / manifest），A 工作区准备已完成、B 工作区准备半途终止、C 无目录；三臂均无 console 日志、无本轮 agent/pytest 残留或 P4 快照残留。**REV2 已启动臂进程口径计 0，模型调用 0；累计仍为 29/75 个已启动臂进程**。残留为 1 完整工作区、1 半成品工作区、1 个 venv，均原样保留，不手动删除。

此轮没有 `arm_check_result.json`，故 t13 接线门槛**尚未执行完成**；正式批次仍是 **0/36**，等待用户启动提示后才能重新执行门槛及跑批。t8 历史记录保留在上文，未被覆盖。

## 7. t13-start 接线门槛（接线 PASS，独立判定产物 FAIL）

命令：`eval/harness/wiring_probe.py --run-tag t13-start`；批次：`E:\aicoding\eval-runs\p1-20261013T103107-wiring-t13-start`。探针 exit 0，manifest 记录 runs=3/3、A→B→C。`triple_state.jsonl` 三臂终态均为 `agent_result`，wall_s：A 256.05、B 202.03、C 284.64；总 wall=761.4s。

`E:\aicoding\eval-runs\p1-20261013T103107-wiring-t13-start\arm_check_result.json` 的 `pass=true`，W1 argv、W2 options、W3 task sha、W4 mcp env、G-K dispatch、identity 均 PASS。G-K：expected_k=1，groups=`test/test_output.py`，k_observed=1，index_chars=141≤2000，worktrees_after_dispatch=1；原始共享 dispatch 日志路径在该 JSON 的 `dispatch_log` 字段。

**阻断事实**：调度器产物仍写 `resolved=false` 占位，notes 为“coding_agent 正常退出；待 harness 独立 pytest 判定”；`scheduler_p1.py` 没有调用独立 `judge(...)` 或 `run_instance.run_one(...)`，无 `judged.json` / `verdict.json` / `result.json` / 独立 pytest 逐 node 明细。新增“至少一条真实独立判定产物”门槛为 FAIL。正式 36 次跑批**未启动（0/36）**；本轮 3 个已启动臂进程使 REV2 累计从 29 变为 **32/75**。

## 8. t20 前置门槛未过（最新探针只有 A 行）

`git log --oneline` 包含 t9 `9390f4f`、t10 `242e007`、t17 `42bc236`、t18 `2303d30`、t19 `166441c`，以及 t21 `22b950a`、t22 `9ee5f5e`；`HEAD == origin/main == 9ee5f5e00d2493ad87436f5318602603d30dfade`。冻结文件 `eval/instances/pilot-12.jsonl` 的 SHA256 为 `7B6764014A5D938C98AC395C6FF49DC48E1ADE5686547D2D5AB54ACA2105CBC7`，`git status --porcelain -- eval/instances` 空。

在执行 t20-gate 之前逐轮按时间检索，**最近一次**接线探针状态是 `E:\aicoding\eval-runs\p1-20261013T112414-wiring-t9-cfix\triple_state.jsonl`，仅有 A 臂 1 行 `agent_result` / `resolved=true`，无 B/C 行；该文件最后修改于 2026-10-13 19:27:41，检查时无运行中的 wiring_probe 进程。故 t20 前置门槛“最近一次探针三行 A/B/C 全 `agent_result`”为 FAIL。**本轮未启动 t20-gate，未启动正式跑批（0/36），没有消耗新臂槽**；这次阻断的预算基线按现有完整历史探针为 35/75，最新单行 A 的预算归属待 owner 核实，不能擅自改写为三行。

## 9. t20-batch 正式跑批（止血停止，未完成36槽）

启动命令（环境变量已按 REV2 实际累计36设置）：
`$env:P1_REV2_INITIAL_STARTED='36'; python eval/harness/scheduler_p1.py --run-tag t20-batch --set pilot --arms A,B,C --max-workers 3`

批次根目录：`E:\aicoding\eval-runs\p1-20261013T122356-t20-batch`。计划36，完成27；调度器 exit 0，wall=4281.4s。执行状态顺序以 `triple_state.jsonl` 为准，实例内调度标签依次出现 A/B/C，随后下一实例；触发止血原文：`C consecutive infra/timeout >= 2`，停止提交新臂，`next_index=0`；未启动的9槽无状态行。

终态汇总：27行，其中 `agent_result=23`、`timeout=4`；`resolved=true=17`、`resolved=false=10`。超时行（先按 timeout 归类）：`more-itertools-more-itertools__1b7f033/C` wall=900.02s、`pallets-click__1b0e19f/C` wall=900.03s、`pallets-click__4fd2fea/C` wall=900.02s、`pallets-click__ac6a2ac/C` wall=900.02s。C臂连续超时止血触发，正式跑批按合同停止，未强行补跑。

状态证据：`triple_state.jsonl` 27行，205543 bytes，SHA256=`17F3A7119FA01B20B1FA899C1FDA2B3EF2AF3F737B7444D9A6DE334F17DA8C78`。预算已启动臂进程累计=`36+27=63/75`；历史部分探针 T112414/A 已计在初始36内。正式批次未重试，未续跑。manifest：`E:\aicoding\eval-runs\p1-20261013T122356-t20-batch\manifest.json`。`verdict_gate.py --state ...\triple_state.jsonl` exit 2，报告逐 node 不一致项（more-itertools 行10–13、15–17、21），未将其改标或隐去。

C臂分组证据抽取：脚本 `E:\aicoding\mini-claude-code\eval\reports\p1_extract_evidence.py` 已运行，生成 `E:\aicoding\eval-runs\p1-20261013T122356-t20-batch\policy_extraction_index.json`，当前抽取9个已落盘C臂行；每次独立文件位于对应臂 `artifacts\policy_evidence_isolated.json`，含共享日志匹配行及 arm_report 行，未对缺失字段推断。

### t20-batch 逐行原始索引

完整逐行状态、pytest 字段、实际 CLI、判定 JSON/console/arm-report 文件 bytes+SHA256 索引：`E:\aicoding\eval-runs\p1-20261013T122356-t20-batch\t20_audit_index.json`（27 rows，217794 bytes，SHA256=`f7222c371de8c10a0f9517e9da7cf59835649a19816807ec34e58ad4673b1899`）。状态文件：205543 bytes，SHA256=`17F3A7119FA01B20B1FA899C1FDA2B3EF2AF3F737B7444D9A6DE334F17DA8C78`。manifest budget：initial_started_arms=36，started_arms=63，max_started_arms=75，wall_seconds_limit=43200，infra_stop_consecutive=2；runs_done=27，skipped_completed=0；usage=`input_uncached=2,941,549`、`cache_read=115,952,384`、`cost_usd=142.860337`。其中状态文件 SHA 为回填前值；文件头回填注已说明现值为 `bb89825a…`，变化仅 9 条 C 行的 `artifacts`（A/B 18 行整行不变）。

|行|instance|arm|resolved|verdict_class|wall_s|run_id|
|---:|---|:---:|:---:|---|---:|---|
|1|astanin-python-tabulate__5373ebf|A|true|agent_result|247.81|...-5373ebf-A|
|2|astanin-python-tabulate__5373ebf|B|true|agent_result|304.64|...-5373ebf-B|
|3|astanin-python-tabulate__5373ebf|C|true|agent_result|375.73|...-5373ebf-C|
|4|astanin-python-tabulate__87a9a4e|A|true|agent_result|151.80|...-87a9a4e-A|
|5|astanin-python-tabulate__9dedd45|A|true|agent_result|42.49|...-9dedd45-A|
|6|astanin-python-tabulate__87a9a4e|C|false|agent_result|127.95|...-87a9a4e-C|
|7|astanin-python-tabulate__9dedd45|B|true|agent_result|90.76|...-9dedd45-B|
|8|astanin-python-tabulate__87a9a4e|B|true|agent_result|254.11|...-87a9a4e-B|
|9|astanin-python-tabulate__9dedd45|C|true|agent_result|93.43|...-9dedd45-C|
|10|more-itertools-more-itertools__1b7f033|A|false|agent_result|473.87|...-1b7f033-A|
|11|more-itertools-more-itertools__1b7f033|B|true|agent_result|521.11|...-1b7f033-B|
|12|more-itertools-more-itertools__ccf96ac|A|false|agent_result|306.36|...-ccf96ac-A|
|13|more-itertools-more-itertools__ccf96ac|B|false|agent_result|372.27|...-ccf96ac-B|
|14|more-itertools-more-itertools__1b7f033|C|false|timeout|900.02|...-1b7f033-C|
|15|more-itertools-more-itertools__ccf96ac|C|false|agent_result|357.33|...-ccf96ac-C|
|16|more-itertools-more-itertools__f8ccab2|A|false|agent_result|622.20|...-f8ccab2-A|
|17|more-itertools-more-itertools__f8ccab2|B|true|agent_result|641.52|...-f8ccab2-B|
|18|pallets-click__1b0e19f|B|true|agent_result|67.14|...-1b0e19f-B|
|19|pallets-click__1b0e19f|A|true|agent_result|221.40|...-1b0e19f-A|
|20|pallets-click__4fd2fea|A|true|agent_result|207.71|...-4fd2fea-A|
|21|more-itertools-more-itertools__f8ccab2|C|true|agent_result|849.83|...-f8ccab2-C|
|22|pallets-click__4fd2fea|B|true|agent_result|246.18|...-4fd2fea-B|
|23|pallets-click__1b0e19f|C|false|timeout|900.03|...-1b0e19f-C|
|24|pallets-click__ac6a2ac|A|true|agent_result|393.92|...-ac6a2ac-A|
|25|pallets-click__4fd2fea|C|false|timeout|900.02|...-4fd2fea-C|
|26|pallets-click__ac6a2ac|B|true|agent_result|559.60|...-ac6a2ac-B|
|27|pallets-click__ac6a2ac|C|false|timeout|900.02|...-ac6a2ac-C|

超时证据：状态行 `started_at`/`finished_at` 均为 900s agent 阶段窗口；C dispatch 共享日志仅提供 dispatch 时间点（more-itertools 1b7f033：20:38:22；pallets-click 1b0e19f：21:04:37、21:09:56、21:20:21 及其后续组事件），console_C.log 对 pallets-click__1b0e19f 仅有 5 条 `scout cli stderr unrecognized_model`，无可用模型时间戳；故本报告只列时间戳，不推断 scout 是否计入900s窗口。

### 未启动槽与最终账目

未启动的9个正式槽为：`pytest-dev-pluggy__20d8143` 的 A/B/C、`pytest-dev-pluggy__cbf58d5` 的 A/B/C、`pytest-dev-pluggy__dd20a85` 的 A/B/C。三实例均没有各臂 run 目录/state 行；止血触发后未执行 `--resume`，不补跑、不改写已完成行。REV2最终账目：已启动臂 `63/75`；累计成本 `$142.86/$225`（manifest精确为 `$142.860337`，累计口径，不可当本批成本）；本批 `recompute_rows.json` parent `$59.53` 加 scout 后合计 `$61.38`。最终批次产物索引：`triple_state.jsonl` 205543 bytes SHA256 `17F3A7119FA01B20B1FA899C1FDA2B3EF2AF3F737B7444D9A6DE334F17DA8C78`，`manifest.json` 1276 bytes SHA256 `3AC79A16A62EB7D728242D944D1F6857E3303D420929AAC9D60F70F491FBADA8`，`t20_audit_index.json` 217794 bytes SHA256 `F7222C371DE8C10A0F9517E9DA7CF59835649A19816807EC34E58AD4673B1899`，`recompute_rows.json` 305540 bytes SHA256 `D633EE1F0512D4D650B693619A402BFE839A49C6FF966C47BF29A9C86F430E2A`。前两份状态/manifest mtime均为 `2026-10-13 21:35:17`；后两份为审计索引生成物，未修改状态数据。最终核查现场 HEAD=`ab20a88aa274c2ca453d6141779cfda85f4ee0ed`、origin/main=`0b096d5a127f3a87f31b21d5da828e12fa0a9038`，两者差异来自并行后续提交，批次代码/状态产物未回写。
