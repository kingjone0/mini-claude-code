# t25 C 臂 900s 超时机制调查

本调查只读既有产物，不重跑实例、不改写任何已产出行，也不消耗模型额度。

## 计时窗口

`eval/harness/scheduler_p1.py:245` 在 worker 入口记时，`:268-275` 启动 `coding_agent.py` 并对整个子进程 `communicate(timeout=A.AGENT_PHASE_TIMEOUT_S)`；`:276-293` 到时 kill，归类 timeout。`eval/harness/arms.py:123` 定义该值 900s。这个窗口**包括** coding_agent 的初始基线 pytest、C 独有的 test_patch 分组 pytest、scout 派发和父 agent；**不包括** scheduler 在提交 worker 前的 fresh_workspace / venv 准备以及 timeout 后才执行的独立 pytest 判定。

`coding_agent.py:1334-1396` 施加 test_patch、运行分组 pytest 并恢复工作区；`:1398-1463` 分组和派发 scout；`:1476-1499` 定稿题面及 arm_report。父 agent 直到 `:1581-1596` 才开始。故 900s 不是父 agent 独占的工作时间，而是包括 C 特有前置阶段的端到端子进程墙钟。正式批次四条 C 行的 wall 为 900.02/900.03/900.02/900.02 秒，与 kill 窗口吻合。

## dispatch 是否循环

`servers/experiments/out/policy_dispatch.jsonl:131,133,136,142` 记录 `p1-pallets-click__1b0e19f-C` 的四个 `dispatched`：21:04:37 的 k1=`tests/test_options.py`；21:07:45 的 k2=`tests/test_arguments.py`；21:10:53 的 k3=`tests/test_info_dict.py`；21:19:30 的 k4=`tests/test_chain.py`。四个组号/文件各异，`snapshot_head` 均为 `c7c19810b95851acb757672194fe411540751e94`；不是同一组无限循环。k3 在 `:138` 有 `retry_dispatched`，`:141` 的组终态 `retried=true`、`attempt_status=truncated`；这是分组内部一次重试，已记录而非隐匿循环。k4 于整个窗口截止前约 3 秒才派出，没有结束事件，故此次 C 无完整派发终态，也没有 `worktrees_after_dispatch` 成功落盘：不能从缺字段推断工作树为 1。

## agent 是否得到时间、产物是否存在

四条 timeout（`more-itertools__1b7f033`、`pallets-click__1b0e19f`、`pallets-click__4fd2fea`、`pallets-click__ac6a2ac`）的 `artifacts/` 均**没有** `judged.json`、`verdict.json`、`result.json`，因为 `scheduler_p1.py:290-301` 只在子进程正常退出时进入独立判定。三个 click 的 `console_C.log` 均无 `=== agent 开始 ===` 且 `tool_use` 匹配数为 0，父 agent 未获得工作时间。more-itertools 的 console 已出现 `=== agent 开始 ===` 与约 120 次 `tool_use` 文本匹配，至少这一条真实进入了父 agent 阶段；这些文本计数不是 API 精确回合计数。

## 结论与影响

三选一主结论：**③治疗未按设计施加**，具体是三条 click C 在 900s 窗口内耗于 C 特有的分组/scout 前置阶段、父 agent 从未启动；不能把这三条作为「C agent 更差」证据。按照预注册 §1.2/§4.4，C 应报**不可测**，不能事后改写既有 timeout/resolved 标签；需要由 captain 裁定后续统计处理及是否预注册新的计时口径。第四条 more-itertools C 已进入父 agent，属于真实端到端慢（但该 900s 仍包含前置开销），不能据此推断其父 agent 获得满额 900s。具体缺陷位置是 `scheduler_p1.py:275` 对整个 coding_agent 进程使用 K10 的“agent 阶段”预算、与 `coding_agent.py:1581` 的实际父 agent 起点不一致；本轮不改超时判据或既有标签。

`unrecognized_model` 并非 C 独有：`pallets-click__1b0e19f` 的 A/B/C console 命中数分别为 1/1/5，A/B 均正常完成，因此不是导致 C 独有超时的充分解释。只读复核命令：PowerShell `Select-String -Path '<该实例各臂 artifacts/console_*.log>' -Pattern 'unrecognized_model'`。
