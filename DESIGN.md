# DESIGN.md —— mini Coding Agent 最小闭环设计

> **目的**：把现有的「验证脚手架」变成**真的 agent**。
> **状态**：已实施完毕（五个必须层全部落地并通过验收）。
> 本文件保留立项设计原文；**当前状态以 [`README.md`](README.md) 为准。**
> **本文件同时充当「项目立项」的设计文档**：架构图 / 工具清单 / 验收标准。
>
> ⚠️ **本文件是立项时写下的设计，绝大部分已被实施并验证。当前状态请看 [`README.md`](README.md)。**
> 已知偏离原设计的地方至少四处：§4 的 `tools=[]` 是实施中被反向对照推翻原设计后补的；
> §10 的覆盖表里「全链路 trace」已完成（见 [`TRACE.md`](TRACE.md)）、
> 「subagent 分工」**已完成**（见 [`SUBAGENT.md`](SUBAGENT.md)、[`AB-SUBAGENT.md`](AB-SUBAGENT.md)
> 与 [`AB-SUBAGENT-HARD.md`](AB-SUBAGENT-HARD.md) —— 实现做了，两轮 A/B 都表明
> **这一层的收益未被证明**：「邀请使用」在两轮靶场上都是 **0/4 被采用**）；
> 层的编号以 §10 为准（**④ = subagent、⑤ = trace**，README 早先写反过）；
> §3 的架构图未画出 trace 落盘这条线。

---

## 1. 目标

让 agent 自主完成一次真实的代码修复闭环：

```
读文件 → 定位 → 改代码 → 跑测试 → 失败自省 → 重试
```

**本阶段明确不做**：多仓库、真实 GitHub issue、subagent 分工、上下文压缩、全链路 trace、
Docker 化与录屏 —— 那些是后续第 5–9 周的内容。

> **后续实际（2026-10-14 回填）**：subagent 分工、全链路 trace **已在后续完成**（见 §10 覆盖表）；
> 上下文压缩经评估**有意不做**（现状：上限 + 便宜默认值 + 按需取一条，无 LLM 摘要/压缩）；
> 多仓库 / 真实 GitHub issue / Docker 化仍未做；3 分钟录屏经用户明确豁免。

---

## 2. 已有基础（不用重做）

| 层 | 现状 | 证据 |
|---|---|---|
| 配置隔离 | `.env` 唯一来源 + 主动摘除父进程环境 | 陷阱 13/14，commit `1a0a1fa` |
| MCP 独立进程 | stdio server，跨两个客户端验证过 | `VERIFY-mcp.md` |
| 验证方法论 | 四格隔离实验 + 「反向对照必须能失败」 | `VERIFY.md` |
| 工具 | 只有 `word_count` / `read_study_file` —— **都不是面向代码的** | `servers/local_tools_server.py` |

**缺的**：面向代码的工具 + 靶场 + agent 主循环 + 能失败的验收。

---

## 3. 架构

```
coding_agent.py                      ← 主程序：给任务、跑循环、判定结果、记用量
   │  Claude Agent SDK (query)
   ↓
claude.exe（真身 230MB，cli_path 钉死，见陷阱 2）
   │  stdio JSON-RPC
   ↓
code_tools_server.py                 ← 独立进程 MCP server（本阶段新增）
   ├─ list_files(dir, pattern)
   ├─ search_code(regex, glob)
   ├─ read_file(path, start, end)
   ├─ apply_patch(path, old, new)
   └─ run_tests()
```

**为什么工具放独立进程**（而不是像 `hello_agent.py` 那样进程内）：
① 跨客户端能力已经验证过；② 天然进程边界 —— 沙箱可以真的靠进程隔离，
而不是只靠 `if` 判断；③ 这让「权限与沙箱」这一层有真实载体，而不是纸面设计。

---

## 4. 工具清单与权限边界

| 工具 | 权限 | 输入 | 边界与拒绝条件 |
|---|---|---|---|
| `list_files` | 只读 | `dir="."`, `pattern="*"` | root 内；结果条数上限 |
| `search_code` | 只读 | `regex`, `glob="*.py"` | 只搜 root 内；输出上限；**正则编译失败要报错不崩** |
| `read_file` | 只读 | `path`, `start?`, `end?` | root 内；超长截断 |
| `apply_patch` | **写** | `path`, `old`, `new` | root 内；**`old` 必须在文件中唯一出现**，否则拒绝；拒绝 `..` / 绝对路径 / 符号链接逃逸 |
| `run_tests` | **执行** | 无 | **固定命令白名单**，`cwd=root`，硬超时，输出截断 |

### 三条设计原则

**① 拒绝必须说明理由。** 返回拒绝原因，而不是静默失败 —— agent 需要知道「为什么被拒」
才能换路，这本身就是「失败自省」的一部分。

**② 路径守卫只有一份实现，所有工具共用：**

```python
def resolve_in_root(relpath: str) -> Path:
    root = REPO_ROOT.resolve()
    target = (root / relpath).resolve()   # 必须在拼接 + 规范化之后比较真实落点
    if not target.is_relative_to(root):
        raise SandboxError(f"{relpath} 越出 {root}")
    return target
```

> 不能只看 `relpath` 长什么样 —— `Path("C:/x")` 会让 `/` 直接丢弃 root。
> 这条教训来自 `read_study_file` 的实现（见 `local_tools_server.py:122-129`）。

**③ ⚠️ 「agent 只有一条通路」必须靠 `tools=[]`，不是 `allowed_tools`。**

**这是实施中实测推翻了原设计的一条，必须记下来：**

```python
tools=[],                     # ← 唯一的硬开关：禁用全部内置工具
allowed_tools=[5 个 MCP 工具], # ← 只是"免询问放行"，不是可用范围白名单
disallowed_tools=[...],        # ← 纵深防御
permission_mode="dontAsk",
strict_mcp_config=True,
```

只设 `allowed_tools` 时，实测 agent 仍然调用了 `Bash` 19 次、`Read` 9 次、
`Edit`/`Write` 各 2 次、`Grep` 1 次，**还读到了沙箱之外的文件**。

SDK 文档原话：**"To restrict which tools are available at all, use `tools`."**
原始证据见 `VERIFY-agent.md` 第 4 节。

---

## 5. 靶场（fixture）

```
fixtures/mini_repo/
├─ calc.py          ← 故意留 2 个已知缺陷
├─ test_calc.py     ← 2 个失败测试
└─ README.md        ← 靶场说明（agent 的任务边界）
```

**为什么自建靶场，而不是直接用真实开源仓库**：

| 理由 | 说明 |
|---|---|
| 成功标准客观且二值 | 测试 pass/fail，**不依赖人工判断** |
| 可重复 | 每次跑前从 git 检出干净靶场 |
| 唯一变量可控 | 第三阶段换成 SWE-bench 时，**机制完全一样**，只是靶场变大 |

---

## 6. 验收标准（必须能失败）

> **状态：已验证。** 原始输出见 `VERIFY-agent.md`。下表是设计时写的预期，
> 实测结果列在最右。

| # | 类型 | 期望 | 实测 |
|---|---|---|---|
| 1 | **正向** | agent 自主修好靶场，测试由 fail 变 pass，退出码 0 | ✅ pytest `1 → 0`，改动仅限 `calc.py`/`stats.py`，`run_tests` 退出码序列 `[1, 0]` |
| 2 | **反向 A**（拿掉写工具） | 应**如实报告失败**，而不是宣称修好 | ✅ pytest `1 → 1`、改动为无；agent 自述"hit a hard environment blocker" |
| 3 | **反向 B**（端点写错） | 整体失败，且不崩成堆栈 | ✅ 判定未成功；`agent_error` 记录了 CLI 的误导性报错 |
| 4 | **反向 C**（越界/注入） | 被路径守卫与白名单拒绝 | ✅ 6 项全拒（见 `VERIFY-agent.md` 3.4） |

> ⚠️ **对照 1 单独不能证明任何事** —— 它可能只是「碰巧改对了」。
> 而且**正向跑通不会暴露沙箱的洞**：第 4 节那条 `tools=[]` 的问题，
> 就是在反向 A 里才照出来的。这就是为什么 2/3/4 不能省。

---

## 7. 要记录的指标（面试可直接引用）

| 指标 | 用途 |
|---|---|
| 是否修好（测试真实退出码） | 核心结果 |
| 用了几个 turn | 效率 |
| **工具调用完整序列（含失败的调用）** | **「失败自省」的直接证据**：失败后是否换了策略 |
| 首次失败 → 最终成功的轮数 | 闭环能力的度量 |
| token / cost（写进 `usage.jsonl`） | 成本归因，第三阶段消融要用 |

---

## 8. 实施分解

| 阶段 | 内容 | 产出 |
|---|---|---|
| **P1** | 公共配置模块（从 `hello_agent*.py` 抽出，避免第三次复制） | `agent/config.py` |
| **P2** | 代码工具 MCP server + 统一路径守卫 | `servers/code_tools_server.py` |
| **P3** | 靶场 fixture | `fixtures/mini_repo/` |
| **P4** | agent 主循环 | `coding_agent.py` |
| **P5** | 四组验收 + 原始输出落盘 | `VERIFY-agent.md` |
| **P6** | README / CLAUDE.md / AGENT.md 更新 | — |

---

## 9. 风险与已知约束

| 风险 | 应对 |
|---|---|
| **`pytest` 未安装**（设计时未知项） | **已解决**：`requirements.txt` 钉住 `pytest==9.1.1`，靶场与 `run_tests` 均依赖它 |
| agent 可能「假装修好」 | 验收**以测试真实退出码为准**，不采信 agent 自述 |
| `apply_patch` 歧义匹配 | `old` 必须唯一出现，否则拒绝并要求更具体的片段 |
| 靶场被改坏后不可重复 | 靶场纳入版本控制，每次跑前 `git checkout` 还原 |
| **单次结果不可复现**（陷阱 5） | 指标记多次运行的**分布**，不拿单次当基准 |

---

## 10. 与「五个必须层」的对应

计划要求项目含五层，本阶段的覆盖情况：

| 层 | 本阶段 | 后续 |
|---|---|---|
| 自定义 tool + MCP server | 本次实现 | — |
| **权限与沙箱** | 本次实现（路径守卫 + 命令白名单） | — |
| 上下文管理 | **已完成**（`max_turns` + 输出截断 + 按需取一条；**有意不做** LLM 摘要/压缩） | — |
| subagent 分工 | **已完成**（`--subagent`；两轮 A/B 都表明**收益未被证明**：0/4 被采用） | — |
| 全链路 trace | **已完成**（`agent/trace.py` + `servers/experiments/trace_report.py`，见 [`TRACE.md`](TRACE.md)） | — |
