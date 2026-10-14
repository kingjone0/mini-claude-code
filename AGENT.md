# AGENT.md —— mini-claude-code

> 供 AI Agent 接手本项目的速查。**给人看的项目总览在 [`README.md`](README.md)。**
> 动手改代码前，先读 [`CLAUDE.md`](CLAUDE.md) 的 **60 条实测陷阱**。

## 一句话定位

用 **Claude Agent SDK** 从零搭的 mini Coding Agent：给一个真实仓库 + 一句「测试挂了」，
它自己读代码、定位根因、改源码、跑测试。
**成败由独立进程里的 `pytest` 退出码判定，不采信 agent 的自述。**
模型走第三方 Anthropic 兼容端点（DeepSeek）。

## 现在的状态（截至 2026-10-14）

- **五个必须层**：① 自定义 tool + MCP ✅ ② 权限与沙箱 ✅ ③ 上下文管理 ✅ ④ subagent 分工 ✅
  ⑤ 全链路 trace ✅（编号以 `DESIGN.md` §10 为准：④ = subagent、⑤ = trace）。
  ⚠️ **层都做完了，但 ④ 层的收益没被证明 —— 两轮都指向同一边**：
  「邀请使用」在第一轮靶场 **0/4 被采用**、在把"一步命中"捷径堵掉的硬靶场
  **仍然 0/4**；强制使用（阳性对照）时父层 `input_tokens` ×1.68、`cost_usd` ×1.92、
  成功率不变（第一轮 ×1.62 / ×1.68，跨靶场同向）。
  第一轮的靶场**没有造出「定位」这个需求**（pytest 失败摘要自带测试类名）；
  第二轮的靶场把这个漏洞堵上了（缺陷注入在私有辅助函数里），结论没变。
  子代理本身是称职的（4/4 找对全部根因），所以别把它读成「subagent 有用」或「没用」——
  准确的说法是「它干得不错，但没人主动用它，且用了以后父层更贵」。
  详见 `AB-SUBAGENT.md` §7（第一轮）与 `AB-SUBAGENT-HARD.md` §7（第二轮）。
- **阶段二通过标准**：自选仓库跑通闭环 ✅｜README ✅｜架构图 ✅（README 内的 Mermaid）｜3 分钟录屏 ——（用户已明确豁免）。
- **阶段三（量化验证，2026-10 提前跑完）**：P0 已交付并通过独立审查（预注册早于第一条实例 4h12m；23 条实例；容器无关 harness；gold/空/错三向自校验 12/12；t9 `verdict=pass`）。P1 三臂方向性结果已出（t33 复验 `pass`）：**只验证出 1 组改进 B**（B vs A：2 改进 / 7 平 / 0 变差），C 臂治疗未按设计施加、**不可测**。详见 `eval/P1-PREREGISTRATION.md:626-650` 与 `eval/reports/p1-analysis.md`。
- **靶场三档**：
  | 靶场 | 位置 | 规模 | 用途 |
  |---|---|---|---|
  | `mini_repo` | `fixtures/`（**在本仓库内**） | 2 文件 | 最早的最小闭环 |
  | `small_pkg` | `fixtures/`（**在本仓库内**） | 20 文件 / 1233 行，1 个根因引发 13 个失败 | 上下文计量基线 |
  | `more-itertools` | `E:\aicoding\targets\`（**仓库之外**） | 7,277 行源码 / 768 tests | 主靶场 |
  | `more-itertools-multi` | 同上 | 4 处缺陷 / 期望 6 失败 | ④ 层第一轮 A/B（`setup_multi_target.py`） |
  | `more-itertools-hard` | 同上 | 3 处缺陷**在私有辅助函数里** / 期望 32 失败 | ④ 层第二轮 A/B（`setup_hard_target.py`） |
- 三个 more-itertools 靶场都固定在上游 SHA `81c21a8db1c922260cd673841257fb9b112c1318`，
  由 `setup_target.py` / `setup_multi_target.py` / `setup_hard_target.py` 注入缺陷并
  **把缺陷提交进靶场 HEAD**（靶场的"坏"必须是它自己 HEAD 的不变量）。
  单缺陷主靶场当前 HEAD `d21dd31c3f1bde3a9a5de9e044bff7b2045af465`；
  硬靶场 HEAD `d3c6ad0bdcad54c367ccfeec2be7471e263df975`。
  ⚠️ 重跑这些脚本会造出**新的** HEAD 哈希，而旧哈希冻结在预注册与产物里（陷阱 32）。

## 环境

- conda 环境 `aicoding`（Python 3.12.14）；包 `claude-agent-sdk==0.2.152`。
- CLI：`E:\node.js\24.21.0\node_modules\@anthropic-ai\claude-code\bin\claude.exe`（2.1.272），
  路径在代码里**钉死**（陷阱 2），别改成裸 `claude`。
- ⚠️ conda 不在 PATH 上，安装要用 `D:\Anaconda3\Scripts\conda.exe`；
  **但跑本项目脚本要直接调 `D:\Anaconda3\envs\aicoding\python.exe`** ——
  `conda run` 默认模式会让 MCP server 因 stdin EOF 立刻退出（见 [`SDK-HELLO.md`](SDK-HELLO.md)）。
- 凭据：项目根 `.env`（**不进仓库**）。脚本只打印 `key_source` 归因，不打印凭据。

## 怎么跑

```powershell
# 准备大靶场：clone → 固定 SHA → 注入缺陷 → 提交进靶场 HEAD
D:\Anaconda3\envs\aicoding\python.exe setup_target.py

# 让 agent 自己修（判定由服务端独立跑的 pytest 完成）
D:\Anaconda3\envs\aicoding\python.exe coding_agent.py --fixture more-itertools --label demo

# 反向对照：拿掉写工具，它应当如实报告失败
D:\Anaconda3\envs\aicoding\python.exe coding_agent.py --fixture small_pkg --label demo-nowrite --no-write

# 看时间线与卡点 / 自检检测逻辑
D:\Anaconda3\envs\aicoding\python.exe servers\experiments\trace_report.py servers\experiments\out\agent-demo.json
D:\Anaconda3\envs\aicoding\python.exe servers\experiments\trace_report.py --self-test
```

## 改这个仓库前必须知道的

- **`CLAUDE.md` 的 60 条陷阱是先读项。** 最容易撞的几条：
  - 陷阱 2 —— `cli_path` 钉死；陷阱 13/14 —— `.env` 是唯一凭据来源，且要主动摘除父进程环境。
  - 陷阱 18 —— **靶场的「坏」必须是 HEAD 的不变量**，否则判定会静默假成功。
  - 陷阱 25 —— `git status --porcelain` 的路径基准是**仓库根**，两种靶场输出形状不同。
  - 陷阱 30 —— **不要用 PowerShell 的字符串管线改代码文件**：本机 PS 5.1 会按 ANSI 处理 UTF-8
    并毁掉文件（实测 676 行里 143 行受损）。改代码用编辑工具。
- **改公共基础设施时要三种靶场都跑一遍**：`agent/config.py`、`run_git`、工具 server。
  历史上就是因为「只在外层靶场验证过」，一个路径剥前缀的 bug 静默活了 3 个提交。
- **判定不采信 agent 自述**；写任何结论都要附**产物路径 + 位置**（陷阱 23：复核者的数字也会错）。
- **`agent-*.json` / `agent-*.log` 是运行时状态，已 gitignore。** 要长期保留的证据必须另起名字
  （如 `trace-ok-small_pkg.json`）。

## 文件速览

| 路径 | 说明 |
|---|---|
| `coding_agent.py` | 主程序：跑循环、独立判定、记用量与 trace |
| `agent/config.py` | 公共配置（端点加载、父进程环境摘除、凭据脱敏、UTF-8 输出） |
| `agent/trace.py` | `TraceRecorder` —— 结构化 trace |
| `servers/code_tools_server.py` | 独立进程 MCP server，5 个面向代码的工具 |
| `servers/experiments/` | 实验脚本；`out/` 是落盘证据（见该目录的 `README.md`） |
| `fixtures/` | 仓库内靶场（`mini_repo` / `small_pkg`） |
| `setup_target.py` | 准备仓库外大靶场（clone + 固定 SHA + 注入缺陷 + 提交进 HEAD） |
| `servers/experiments/mutate.py` | 变异测试：量化每个候选改动挂掉多少测试 |

## 该读的文件

| 文件 | 内容 |
|---|---|
| [`README.md`](README.md) | 项目总览、结果、架构图、设计规矩 |
| [`DESIGN.md`](DESIGN.md) | 项目立项：目标、架构、工具清单与权限边界、验收标准 |
| [`VERIFY-agent.md`](VERIFY-agent.md) | 四组验收原始输出 + 沙箱对照 |
| [`TRACE.md`](TRACE.md) | 全链路 trace 的设计、检测项、反向对照 |
| [`AB-REPO-MAP.md`](AB-REPO-MAP.md) | A/B 实验报告（含独立复核与勘误） |
| [`SDK-HELLO.md`](SDK-HELLO.md) | SDK 最小验证（端点优先级 / 密钥归因 / MCP 两形态） |
| [`VERIFY.md`](VERIFY.md) / [`VERIFY-mcp.md`](VERIFY-mcp.md) | 更早的原始输出（四格隔离、跨客户端） |
