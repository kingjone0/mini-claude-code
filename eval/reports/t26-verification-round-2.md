# t26 C 臂超时归因独立复验

- 复验任务：t26
- 范围：只读既有 t20 产物、源码与 t25 提交；未启动 agent、未重跑实例、未改状态行。
- 结论：`verdict=pass`

## 1. 独立复算时间窗口

从 t20 `triple_state.jsonl` 读取四条 `verdict_class=timeout` 的 C 行：

| instance | started_at | finished_at | wall_s | timed_out |
|---|---|---|---:|---|
| `more-itertools-more-itertools__1b7f033` | 20:37:42 | 20:52:42 | 900.02 | true |
| `pallets-click__1b0e19f` | 21:04:33 | 21:19:33 | 900.03 | true |
| `pallets-click__4fd2fea` | 21:09:52 | 21:24:52 | 900.02 | true |
| `pallets-click__ac6a2ac` | 21:20:17 | 21:35:17 | 900.02 | true |

以上四组与 t25 报告数字一致。独立从共享 `policy_dispatch.jsonl` 按 label 读取 dispatch 事件：

- `more-itertools...__1b7f033-C`：20:38:22，`tests/test_more.py`，`dispatched`；另有同秒 `undiagnosed` 终态。
- `pallets-click...__1b0e19f-C`：21:04:37 `tests/test_options.py`；21:07:45 `tests/test_arguments.py`；21:10:53 `tests/test_info_dict.py`；21:19:30 `tests/test_chain.py`。
- t20 状态行与 dispatch 日志的时间、label 一一对应；后 3 个 click C 的 console 只有 `unrecognized_model` stderr，无 `agent 开始`/`tool_use`；more-itertools C console 有 `agent 开始`及 120 个 `tool_use` 文本匹配。

## 2. 900s 窗口起止与包含范围（源码证据）

- `eval/harness/arms.py:122-124`：`AGENT_PHASE_TIMEOUT_S = 900`。
- `eval/harness/scheduler_p1.py:245-250`：worker 在启动 `coding_agent.py` 前记录 `started`/`started_at`。
- `eval/harness/scheduler_p1.py:268-279`：`subprocess.Popen` 后对整个 coding_agent 子进程调用 `communicate(timeout=A.AGENT_PHASE_TIMEOUT_S)`；超时时 kill，故 K10 窗口从 worker 记时/子进程启动附近开始，到 kill/收尾结束。
- `coding_agent.py:1334-1369`：C 实例模式先施加 `test_patch`，并运行基线 pytest（timeout=600）；这发生在 coding_agent 子进程内，属于900s窗口。
- `coding_agent.py:1398-1463`：失败分组、scout 派发及 worktree 收尾属于同一 coding_agent 子进程，属于900s窗口。
- `coding_agent.py:1581-1596`：父 agent `run_agent` 实际开始；因此父 agent 只获得900s窗口扣除前置阶段后的剩余时间。
- `scheduler_p1.py:290-301`：timeout 优先归类并且不进入独立 pytest 判定；正常退出才调用 `judge_arm_workspace`。独立判定在900s窗口外，不构成超时窗口的一部分。

因此，基线 pytest、C 特有 test_patch/分组 pytest、scout 派发、父 agent 均在900s子进程窗口内；scheduler 准备链和超时后的独立判定在窗口外。该实现事实支持“窗口口径导致 C 治疗未获得可比父 agent 时间”的测量风险，但不单凭它把所有 timeout 判为真实慢。

## 3. 四次 dispatch 性质

四个 click dispatch 的 group 分别为 `tests/test_options.py`、`tests/test_arguments.py`、`tests/test_info_dict.py`、`tests/test_chain.py`，并非同一组循环；因此主序列是按不同失败组的正常派发，而非同组无限重试。可见的一次 retry 属于 `tests/test_info_dict.py` 组内部重试，不改变四组主派发的判断。第四组在窗口末尾约3秒派出，没有结束事件，缺少字段不得推断成功。

more-itertools 仅一组 `tests/test_more.py`，窗口内有 agent 运行证据；它与 click 三条“父 agent 未启动”的情况不能合并作同一种失败。

三条 click 的治疗没有完整施加（时间耗在 C 前置分组/scout 阶段，未见父 agent 开始），按预注册 §4.4 应作为 **C 臂不可测** 交 captain 处理；不改写既有 `timeout`/`resolved` 标签，不把它们当 C agent 能力差异证据。more-itertools 仍是包含前置阶段的真实端到端超时，不能宣称父 agent 获得完整900秒。

## 4. 越界检查

`git show --stat 33d9698f1c87f0be08ad71c6f8219f6c2796298a` 显示 t25 只新增 `eval/reports/t25-c-timeout-investigation.md`（23行）；该提交 diff 不涉及状态产物、实例/题面、臂定义、判定代码或预注册。在 t25 完成回报时其 HEAD/remote 均为 `33d9698f`；本复验执行 git 校验期间，其他成员提交仍在并行发生，现场 HEAD=`ab20a88aa274c2ca453d6141779cfda85f4ee0ed`、远端 main=`0b096d5a127f3a87f31b21d5da828e12fa0a9038`，不可用此时点的 HEAD/remote 差异反证 t25。t25 文档声明“只读既有产物、不重跑实例、不改写行、不消耗模型额度”；提交 diff 与旧 t20 状态文件 mtime（仍为2026-10-13 21:35:17）支持未改产物，但单靠 git 无法证明机器上绝无其他离仓外探针，本复验不作超出证据的绝对断言。本复验调用 gold/mechanism/selfcheck 均为离线回归、零模型调用；它们产生新的离线 goldcheck/mech 目录，不是正式实例模型重跑。

## 5. 证据索引

- `E:\aicoding\eval-runs\p1-20261013T122356-t20-batch\triple_state.jsonl`
- `E:\aicoding\mini-claude-code\servers\experiments\out\policy_dispatch.jsonl`
- 四个 C 臂各自 `artifacts/console_C.log`
- `eval/reports/t25-c-timeout-investigation.md`
- `eval/harness/scheduler_p1.py:245-301`
- `coding_agent.py:1334-1463,1581-1596`
- `eval/harness/arms.py:122-124`
