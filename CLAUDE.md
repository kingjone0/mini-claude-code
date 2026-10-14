# CLAUDE.md — mini-claude-code

## 这是什么

**主项目**：手写一个 mini Coding Agent，实现「读文件 → 定位 → 改代码 → 跑测试 → 失败自省 → 重试」闭环。

**当前状态**：
- 第 0 步已完成——Claude Agent SDK 端到端验证（commit `64beb30`）
- 第 1 步已完成——**独立进程 + stdio 的 MCP server，跨两个客户端验证**（见 `VERIFY-mcp.md`）
- 第 2 步已完成（commit `1efafec`）——server 自己落盘日志（双通道）+ 探针支持三档协议
- 第 3 步已完成（commit `1a0a1fa`）——**API 配置来源收敛**：`.env` 成为唯一来源、
  父进程环境被主动摘除、缺配置明确失败。见**陷阱 14**
- 第 4 步已完成——**修复闭环跑通**：`coding_agent.py`（agent 主循环）+
  `servers/code_tools_server.py`（代码工具，5 个）+ `fixtures/mini_repo/`（靶场）。
  三组验收 + 6 项沙箱对照全通过，见 **`VERIFY-agent.md`**。设计见 **`DESIGN.md`**

**下一步**：五个必须层（`DESIGN.md` 第 10 节）**全部完成** ——
③ 上下文管理（`repo_map` + `run_tests detail=brief`，见 `AB-REPO-MAP.md`）、
④ subagent 分工（`--subagent`，见 `SUBAGENT.md` 通道探针 / `AB-SUBAGENT.md` A/B）、
⑤ 全链路 trace（`agent/trace.py`，见 `TRACE.md`）。
⚠️ **④ 层实现完了，但收益未被证明 —— 两轮都指向同一边**：
「邀请使用」在第一轮靶场 **0/4**、在把"一步命中"捷径堵掉的硬靶场**仍然 0/4**
（`AB-SUBAGENT-HARD.md`）；强制使用时父层 `input_tokens` ×1.68、`cost_usd` ×1.92、
成功率不变（第一轮是 ×1.62 / ×1.68，跨靶场同向）。子代理本身是称职的
（4/4 找对全部根因），所以问题在**采用的动机**与"把总结搬回父层"的代价。
代价的量化：子代理交回的报告有 **6.8–8.1 万字符**，整个进入父层上下文。

## 环境与常用命令

- conda 环境：`aicoding`（Python 3.12.14）
- ⚠️ **先准备 `.env`**（脚本不再回落，缺配置直接退出）：`Copy-Item .env.example .env`，
  然后至少填 `ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL`、`ANTHROPIC_AUTH_TOKEN`（或 `ANTHROPIC_API_KEY`）。见**陷阱 14**。
- 装依赖：`D:\Anaconda3\Scripts\conda.exe run -n aicoding python -m pip install -r requirements.txt`
- 跑正向流程：
  `D:\Anaconda3\Scripts\conda.exe run --no-capture-output -n aicoding python hello_agent.py`
- 跑反向对照（应当失败，退出码 1）：
  `$env:HELLO_AGENT_BASE_URL="https://api.deepseek.com/anthropic-WRONG-PATH"; D:\Anaconda3\Scripts\conda.exe run --no-capture-output -n aicoding python hello_agent.py`

### MCP server（独立进程、stdio）

- 启动（**两个客户端用的都是这条，客户端侧不需要改 server**）：
  `D:\Anaconda3\envs\aicoding\python.exe E:\aicoding\mini-claude-code\servers\local_tools_server.py`
- 不接客户端先自检（探针保持 stdin 打开，能拿到 `tools/call` 的响应）。
  ⚠️ **默认档是 `auto`；要复现历史证据必须显式加 `--protocol legacy`**：
  `D:\Anaconda3\envs\aicoding\python.exe servers/experiments/probe_client.py --protocol legacy out/x -- <上面的启动命令>`
  三档：`auto`（SDK 协商，**不产出** `.stdout.raw`）/ `legacy`（`2025-06-18` 握手）/ `2026-07-28`（现代档，带 `-32022` 反向对照）
- 客户端 A：`claude mcp add local-tools --transport stdio -- <上面的启动命令>`（`--` 必须有）
- 客户端 B：`D:\Anaconda3\Scripts\conda.exe run --no-capture-output -n aicoding python hello_agent_stdio.py`

## 已知陷阱（都实测过，别重踩）

### 1. `setting_sources` 会静默覆盖 `options.env`

实测优先级：**`~/.claude/settings.json` 的 env 块 > `ClaudeAgentOptions.env` > 进程继承环境**

- 写成 `["user"]`：settings.json 会**静默覆盖**你传的 `env`，反向对照将无法失败（第一次就是这么被骗的）
- 省略不写：默认等于 `["user","project","local"]`，同样会覆盖
- **必须显式写 `setting_sources=[]`**（`[]` 同时把 CLAUDE.md 与 user/project/local settings 一并挡在内层 CLI 之外）

**但只写 `[]` 不够** —— 还有第二条静默通道（父进程继承环境里的同名变量），完整做法见**陷阱 14**。

原始输出见 `VERIFY.md` 第三节。

### 2. `cli_path` 要指真身

`E:\node.js\24.21.0\node_modules\@anthropic-ai\claude-code\bin\claude.exe`（约 230 MB）。
**不要**指 `<用户目录>\.nvmd\bin\claude.exe`——那是 nvmd 的启动垫片。

### 3. 报错文案会误导

端点写错时，CLI 报的是「There's an issue with the selected model... It may not exist or you may not have access to it」——**说的是模型问题，实际是 URL 错了**。别顺着报错去查模型。

### 4. 每次运行都有隐藏的额外 API 调用

stderr 会出现 `generate_session_title`，`model_usage` 里因此有两个模型条目。`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` 挡不住。第三阶段批量跑评测时要把它算进成本。

> 选填但**建议填**：`.env` 里的 `ANTHROPIC_DEFAULT_HAIKU_MODEL` 等——那次 `generate_session_title` 走的是 HAIKU 档，不钉住它成本核算对不上账（见 `.env.example`）。

### 5. 结果不可逐次复现

同一个脚本两次运行，input tokens（17013 / 17815）与 `total_cost_usd`（0.09646 / 0.108593）都不一样。做消融对比时**不要拿单次数字当基准**。

### 6. `mcp` 2.x 把 `FastMCP` 改名成了 `MCPServer`

本机装的是 `mcp` **2.2.0**（`claude-agent-sdk` 声明的约束是 `mcp>=1.23.0,<3.0.0`，
所以 2.x 是正常解析结果，**不是装错了版本**）：

```python
❌ from mcp.server.fastmcp import FastMCP   # 报错，有迁移提示
✅ from mcp.server.mcpserver import MCPServer
```

`MCPServer.tool()` **必须带括号**（`@server.tool()`，写成 `@server.tool` 会抛 TypeError）。
`server.run()` 默认就是 `transport="stdio"`。

**别照抄网上的 FastMCP 老教程。** 源码在
`D:\Anaconda3\envs\aicoding\Lib\site-packages\mcp\server\mcpserver\server.py`。

### 7. stdio server 的启动命令：直连 `python.exe`，不要用裸 `conda run`

实测三档（原始输出见 `VERIFY-mcp.md` 第 4.4 节）：

| 启动方式 | server 起来了吗 | stdout 管道 | stdin 转发 |
|---|---|---|---|
| 直连 `D:\Anaconda3\envs\aicoding\python.exe` | 是 | 5 行，全 JSON ✅ | ✅ |
| `conda run --no-capture-output` | 是 | 5 行，全 JSON（与直连**逐字相同**）✅ | ✅ |
| `conda run`（**不加** `--no-capture-output`） | **是**（stderr 有启动横幅） | **0 字节** ❌ | **EOF** ❌ |

`conda run` 默认模式做的是**两件事**，任意一件都足以让 MCP server 跑不起来：

- **(b-1) 输出缓冲到子进程退出**才一次吐出 —— 计时探针在子进程还活着时去看管道是 0 字节，
  退出那一刻两行同时到达。**输出一直都在，不是丢掉。**
- **(b-2) 子进程的 stdin 是 EOF** —— 实测 `readline` 返回 `''`，PING 根本没进去。
  **这条才是 MCP 场景的致命伤**：server 读 stdin 立即 EOF → 正常退出（退出码 0）
  → **从未写过 stdout**（所以是 0 字节，不是"写了被吞"）。

这跟「多一层进程有风险」是两回事 —— 加 `--no-capture-output` 之后两条路都是干净的。

本项目统一用**直连 python.exe**（少一层，少一个失败点）。

### 8. Claude Code 会吞掉 MCP server 的 stderr

server 写 stderr 的日志，在客户端侧**看不到**（实测：搜遍客户端 B 的日志没有 server 的 stderr）。
「日志走 stderr」能保证**不砸协议**，但**不保证你能看见**。

**已实施（commit `1efafec`）**：`local_tools_server.py` 现在挂**两条互相独立**的通道——
`FileHandler`（写 `servers/experiments/out/*server.log`）+ `StreamHandler(stderr)`；
并且 **`propagate = False`**，防止日志经 root logger 漏进 stdout（那等于把协议线的处置权交出去）。
日志路径基准取 `__file__` 而非 cwd（cwd 由拉起 server 的客户端决定）；`MCP_SERVER_LOG` 可覆盖。
`out/*server.log` 是运行时状态，被 `.gitignore` 单独排除（规则必须写在 `!…*.log` **之后**——最后匹配者说了算）。

### 9. `conda run` 不支持参数里含换行

`conda run -n <env> python -c "多行代码"` 会直接抛
`AssertionError: Support for scripts where arguments contain newlines not implemented`。
要用多行就写成临时 `.py` 文件，或者直接用环境的 `python.exe`。

### 10. `print()` 到 stdout 害不害事，取决于时机

不是笼统的「禁止 print」。分界线是 **transport 有没有启动**：

- **启动前**（模块顶层、import 期）→ 写在协议线上，**真的会砸**
- **启动后**（tool handler 内）→ mcp 2.x 的 `stdio_server()` 已把 fd 1 指向 stderr，够不到协议线
  （`mcp/server/stdio.py:161-179`，issue #1933 的修复；实测 `print()` 和 `os.write(1,…)` 都被兜住）

本项目**仍然一律走 stderr** —— 因为上面那个窗口期真实存在，且源码写明抢占是 *best-effort*，
失败会回退到 v1 的就地服务方式。规则不变，但理由要精确。

### 11. ⚠️ 不要手写含反斜杠的 shell 命令 —— 我做不到可靠转义

**不是 heredoc 的锅，是我的锅。**
第一版把机制写成「带引号的 heredoc 会吃反斜杠」，**已被字节级实验推翻**
（原始输出：`servers/experiments/out/backslash-escape-channels.log`）：

```
$ cat > quoted.txt <<'XEOF'      # 带引号：完全忠实，不做任何转义处理
A\\B\\C
XEOF

quoted    bytes=6   内容=A\B\C$
```

`<<'XEOF'` 是忠实的 ⇒ 它输出 6 字节只说明**送进去的输入本来就只有一个反斜杠**。
换遍通道结论一致 —— 只要**书写**反斜杠字面量就会丢一层：

| 通道 | bash 实际收到 |
|---|---|
| `echo "A\\B\\C"` / `'A\\B\\C'` / `printf '%s'` / `cat <<'XEOF'` | `A\B\C` —— 都丢一层 |
| `python -c "print('A'+chr(92)*2+'B')"` | **`A\\B\\C`** ✅ 唯一成功 |

**规则**：写 JSON 一律用 `python -c "json.dump(…)"` 或 Write 工具，
**不要手写含反斜杠的 shell 命令**。需要反斜杠时用 `chr(92)` 构造，别写字面量。

**代价很大，已犯过一次**：我据此得出「相对路径不可用」「`${CLAUDE_PROJECT_DIR:-.}` 不可用」
两个**完全相反**的结论，还编了一个「Claude Code 有发现缓存」的假说去解释纯属自己造成的现象。
用正确 JSON 重跑，三条路径写法全部 `FOUND`。

#### 顺带一条独立的真规则

**无引号 heredoc (`<<EOF`) 会塌掉一层**（复核方实测，输入相同时：quoted=8 / unquoted=6 字节）。
它**不是**我这次的原因（我的输入本来就只有一层），但将来真要写 heredoc 时得知道。

配套的纪律：**失败时要能区分「配置没加载」和「路径没解析对」**。
判别档：同 cwd 下换一个**真实存在的环境变量**（`${MCP_TEST_PYTHON}`）→ 若 `FOUND`，
说明配置能加载，那 `NOT_LOADED` 就只能是路径问题。

### 12. `.mcp.json` 是项目作用域 —— cwd 是实验条件的一部分

- cwd = 项目根 → `.mcp.json` 生效；cwd = 子目录 → **`claude mcp list` 仍看得到**（会向上找），
  **配置也确实被加载**（绝对路径那一档实测 `FOUND`），
  但 **`args` 里的相对路径按 cwd 解析**，子目录下会解析成 `servers/servers/…` 而失效。
- **相对路径可用，但只在「cwd = 项目根」时**。`${CLAUDE_PROJECT_DIR:-.}` 与它**完全等价**
  （该变量本机没有被设置，`:-` 回退到 `.`）—— 别以为写成变量就更稳。
- `claude mcp add` 的 local 作用域落在 `~/.claude.json` 的 **`projects[<当前 cwd>]`** 键下，
  换个目录执行就落到别的键上。

**所以：凡与 `.mcp.json` 有关的实验，必须固定 cwd 并把 cwd 连同结果一起记录。**
没有 cwd 的结果不算证据 —— 这是我被复核打回一次的直接原因。

### 13. 实验 key 与用量归因：`.env` 唯一来源 + `usage.jsonl` 留痕

**两个 key 分开**：交互式 Claude Code 用 `~/.claude/settings.json` 的，
实验脚本用项目根 `.env` 的（`cp .env.example .env` 后填一行）。
目的：第三方端点账单只有总额，不分 key 就回答不了「这次实验花了多少」——
第三阶段跑 SWE-bench 消融正是要这个粒度。

- ⚠️ **必须显式把 token 放进 `ClaudeAgentOptions.env`，不能靠进程环境**（陷阱 1 + 陷阱 14）。
- **`key_source` 现在只有两种取值**：`env:<文件名>`（正常）/ `config-error`（缺配置那一行）。
  ~~`fallback:~/.claude/settings.json`~~ —— **这个分支已在 09-18 删掉**，脚本不再读 settings.json。
- 做对照实验可用 `DOTENV_PATH=<文件>` 换 `.env` 的**位置**，不必动真正的 `.env`。
  它只换文件位置，不是 key 的第二个来源。

**每次运行追加一行**到 `servers/experiments/out/usage.jsonl`（`RUN_LABEL` 环境变量当标签）。

⚠️ **失败的运行也必须记一行。** 异常会直接冒泡打断 `async for`，
不显式 `try/except` 的话那一行永远记不上 —— 已经踩过一次，而且失败运行恰恰是要留证的对照。
token 字段写 `null` 而不是 `0`：**「没拿到数据」和「花了 0 元」必须能区分**。
错误信息落盘前会过一遍 `scrub()` 抹掉可能的凭据形态。

### 14. 配置来源收敛：`.env` 是唯一来源，缺配置**明确失败**

**问题**：一个配置值最终生效，可能来自三个地方 —— 其中两个是**静默**的：

| # | 来源 | 故障模式 | 症状 |
|---|---|---|---|
| ① | 项目根 `.env` | —— | 唯一合法来源 |
| ② | 父进程继承的 `os.environ` | **静默继承** | `key_source` 说是 A、实际生效的是 B，**完全不报错** |
| ③ | CLI 自己读的 settings 文件 | **静默覆盖**（陷阱 1） | 反向对照**应该失败却成功** |

② 比 ③ 更阴：**换一套 `.env` 也挡不住它** —— 因为它的来源根本不是 `.env`。

**做法：不是排序，是消源。**

| 要消除的来源 | 机制 | 为什么只能用这个机制 |
|---|---|---|
| ③ settings 文件 | `setting_sources=[]` | CLI 支持「一个都不读」这个显式开关 |
| ② 继承环境 | `isolate_from_parent_env()`：`del os.environ[k]` | SDK 的环境合并是 `{**os.environ, **options.env}`，**只能覆盖同名键、删不掉键**，所以只能在交给 SDK 之前先删 |

- ⚠️ **必须在 `query()` 之前摘**：SDK 是在 **spawn 的那一刻**读 `os.environ`；之后删的是副本，对子进程没有影响。
- ⚠️ **不按 `CLAUDE_CODE_` 前缀清**：同前缀下还有 `ENTRYPOINT` / `MESSAGING_SOCKET` /
  `MESSAGING_TOKEN` / `SESSION_ID` / `EXECPATH` 这些**运行必需**的控制面变量（CLI 内部 `XS` 排除集）。
  **前缀是命名约定，不是语义边界** —— 名单逐个有理由，扩大范围要补证据。
  现在只摘四个会改变模型行为的：`CLAUDE_CODE_EFFORT_LEVEL` / `CLAUDE_CODE_SUBAGENT_MODEL` /
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW` / `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`。
- **缺配置 → `ConfigError`，直接退出，不回落。** `ANTHROPIC_BASE_URL` 列为必填的理由：
  不填会打到 `api.anthropic.com`，而**报错文案会说成「模型不存在」**（陷阱 3）——
  现象会把人引到错误的层，所以改成**前置显式检查**。
  `ANTHROPIC_MODEL` 必填是因为缺省值会随 CLI 版本变，钉不住就谈不上消融对比。
- **`.env` 必填项**：`ANTHROPIC_BASE_URL` + `ANTHROPIC_MODEL` + 凭据
  （`ANTHROPIC_AUTH_TOKEN` 或 `ANTHROPIC_API_KEY`，二选一）。
- **留痕**：`usage.jsonl` 多一个 `parent_env_scrubbed` 字段，记**被摘掉的键名**（不含值）。
- `DOTENV_PATH` / `HELLO_AGENT_BASE_URL` 是**脚本开关**，不是 `.env` 的键。

**完整机制与讲解**：见本地私有学习笔记（不进仓库）。

### 15. ⚠️ `allowed_tools` 不是白名单 —— 要禁内置工具必须用 `tools=[]`

**这是第二阶段的沙箱设计被实测推翻的一条，最值钱。**

想「让 agent 只能用我们自己的 MCP 工具」时，第一版只写了
`allowed_tools=[5 个 MCP 工具]`。结果反向对照显示，agent 仍然调用了：

```
Bash 19 次 / Read 9 次 / Edit 2 次 / Write 2 次 / Grep 1 次
```

而且其中一次 `Read` 读到了**沙箱之外**的文件。换句话说：
**我们的路径守卫只保护了 MCP 工具，内置工具完全绕过了它。**

SDK 源码里的真实语义（`claude_agent_sdk/types.py`）：

| 选项 | 语义 | 行号 |
|---|---|---|
| `allowed_tools` | 「**免询问自动放行**」。不在其中只是"需要权限"，**仍在模型上下文里** | 1955 |
| **`tools`** | 「**可用内置工具的基础集合**」—— **`[]` = 禁用全部内置工具** | 1944 |
| `disallowed_tools` | 从模型上下文移除，即使本来被允许 | 2028 |
| `permission_mode` | `"dontAsk"` = 不提示；未预先批准就拒绝 | 1991 |

文档原话：**"To restrict which tools are available at all, use `tools`."**

**正确写法**（`coding_agent.py`）：

```python
tools=[],                      # 硬开关：禁用全部内置工具
allowed_tools=<5 个 MCP 工具>,  # 只自动放行这些
disallowed_tools=[...],        # 纵深防御
permission_mode="dontAsk",
strict_mcp_config=True,        # 不加载 .mcp.json / 用户级注册
```

**为什么容易踩**：正向跑通**完全不会暴露这个洞** —— agent 用 MCP 工具就把活干完了，
`allowed_tools` 的误导性只在"故意让它失败"的对照里才现形。

原始证据：`VERIFY-agent.md` 第 4 节。

### 16. 控制台是 GBK：打印工具返回值会崩掉整轮

`coding_agent.py` 第一次运行崩在：

```
UnicodeEncodeError: 'gbk' codec can't encode character '\ufffd'
```

当时 agent **正在正常工作**（已调了 `list_files` 和 `run_tests`），却被一个 `print` 打断，
而且堆栈把真实进展盖住了 —— **看起来像 agent 坏了，其实是输出层坏了**。

**规则：任何新程序，在第一次 `print` 之前先调 `force_utf8_output()`。**
现在它在公共模块 `agent/config.py` 里（`probe_client.py` 早就有同一个函数，
那次是忘了在新程序里加）。

### 17. `ToolResultBlock.content` 是 dict，不是字符串

```python
❌ str(block.content)              # 得到 "{'result': '退出码 = 1\\n────…'}"
✅ tool_result_text(block)         # 归一化后再解析
```

直接 `str()` 的后果是：指标 `run_tests_exit_codes` 算出来是 `'1\n────────…'` 这种垃圾。
**这类 bug 特别危险 —— 它"有值"，看起来正常，但数字是错的**，
拿着它去写验证记录就是把错误固化下来。

修法：`tool_result_text()` 归一化 + 正则 `退出码\s*=\s*(\d+)` 提取。

### 18. ⚠️ agent 会改靶场源码 —— 而靶场是被 git 跟踪的

**本仓库特有的坑，而且踩过一次。**

靶场（`fixtures/`）既是**测试夹具**，又是**被版本控制的文件**。agent 跑一轮就会改它。
`reset_fixture()` 因此在**跑之前和跑之后都要调**：

```
跑前调 → 保证基线是"坏的"（否则整轮验证无意义）
跑后调 → 否则 agent 的改动留在工作区，被下一次 `git add -A` 当成你的改动提交
```

**踩的那次**：跑后没复位 → `git add -A` 把 agent 对 `pricing.py` 的修复提交进了 HEAD →
而 `reset_fixture()` 用的是 `git checkout`（**从 HEAD 还原**）→
之后每轮都从"已修复"开始：**基线 pytest = 0，判定"成功"，但什么也没验证**。

**由此得出一条不变量**：

> **靶场的"坏"必须是 HEAD 的不变量。** 任何把修复提交进 HEAD 的提交，
> 都会让整套验证**静默失效** —— 症状是"看起来成功"，不是报错。

所以 `coding_agent.py` 里加了一道守卫：基线 pytest 退出码为 0 时**直接中止（返回 3）**，
不调模型、不产出结论。守卫本身已实测过（临时分支造条件 → 确认退出码 3 且无 `[tool_use]`）。

`reset_fixture()` 两步都要做：`git checkout`（还原被改的）+ `git clean -fd`（删新建的）。

### 19. PowerShell 传提交信息：两个坑，都踩了

**① `@"..."@` 双引号 here-string 会吃掉反引号。**

反引号是 PowerShell 的转义符。用 `@"..."@` 传含 markdown 代码标记的提交信息，
反引号会被静默吞掉。实测本仓库 `39b44a0` / `d46c379` / `1aa8557` / `e5fceef`
的提交信息里**反引号数都是 0**，而原始输入里写了。

**② 改用 `@'...'@` 之后仍会失败 —— 消息里含引号时参数会被拆开。**

现象：git 报 `error: pathspec '...'`，把消息的一部分当成了文件名，**提交根本没发生**。
这是 PowerShell → 原生程序的参数边界问题，不是 git 的问题。

**结论（硬规则）**：

```powershell
# ❌ 不要这样传提交信息
git commit -m "含 `反引号` 或 "引号" 的长消息"
git commit -m $msg          # 即使 $msg 来自 here-string 也不安全

# ✅ 一律走文件
#    用 write 工具把消息写成 UTF-8 文件（顺便绕开 GBK 代码页，见陷阱 16）
git commit -F <消息文件>
```

### 20. `Select-Object -First N` 会杀掉上游的原生进程

在 PowerShell 里对**原生程序**（python.exe / git.exe）的输出接
`Select-Object -First N`，取够 N 条后 PowerShell 会**停止上游管道**，
也就是**把那个进程直接杀掉**。

实测：一次本该 `return 3` 的运行，被 `-First 20` 截断后
`$LASTEXITCODE` 变成了 **-1**，而且守卫打印的中止原因**根本没进输出**。

**为什么危险**：它同时掩蔽**退出码**和**证据**，而输出看起来"有内容"，不报错。
这会把一次失败误读成一次普通的截断显示。

**规则**：要看退出码或完整证据时，**不要用 `Select-Object -First` 截原生程序的输出**。
先整个接进变量（`$out = & cmd ...; $code = $LASTEXITCODE`），再在变量上做 `Select-Object -Last N`。

### 21. `changedPaths` 填不进：会话工作区与本仓库不相交

AgentTeams 的会话工作区与项目目录**不相交**（工作区在仓库之外）。
`changedPaths` / `inScope` 要求**相对工作区**的路径，于是：

| 写法 | 结果 |
|---|---|
| 绝对路径 `E:/aicoding/...` | 报 `illegal` |
| `../aicoding/...` | **也报 `illegal`**（校验器不允许逃逸工作区）|
| 去盘符 `aicoding/mini-claude-code/...` | 报 `undeclared`（对不上 inScope 里声明的形式）|

**四次都撞在同一个坎上**（t1 / t3 / t5 / t7）。处置：**直接置空该字段**，在 `output` 或
`evidence_note` 里写明实际改动的文件，**不要在这上面耗时间**。

根本解法只有两个：**让团队从代码仓库启动**，或者接受「代码在工作区之外时，scope 字段只是注释」。

### 22. 核对脚本的输入，决定它是「断言」还是「快照」

**本仓库踩过一次，值得单列。**

`out/t6_index_check.py` 被当作「可复现的逐条核对脚本」交付，并被写进报告的复现指引。
实测：**修前修后都报 6/6 WRONG**。查源码发现它的断言**硬编码在 `claims` 列表（L26–31），不读报告正文** ——
它是**冻结快照**，不是回归测试。它**区分不了**「报告没改」和「报告改了但脚本没跟上」；
照它验收会误判「修复没生效」。

后来重写的 `out/t8_index_regression.py` **从报告正文正则解析**断言再对照产物，才是可回归的。

> **判据：一个核对脚本的输入若是常量表，它就是快照，验证不了任何东西。**
> 写或收下一个「核对脚本」时，第一问是**它的输入是什么**。

### 23. 复核者的数字类 finding 也会错 —— 要求附「产物 + 定位」

同一次实验里，第 1 轮复核给出的一条数字类 finding **是错的**（说某计数是 15，实测 16）。
被复核方核对后**拒绝照改**，并保留了原数 —— 那是对的；**若照改，就会把一个正确数字改成错误的**，
而且会带着「已按复核意见修复」的正当性，以后更难发现。

**规则**：
- 复核者提数字 / 引用类修正，**必须附「具体产物 + 事件位置」**，让被复核方能逐条复算；
- 被复核方**不得仅因"复核者说了"就改** —— 数据不支持就如实写，并在报告里留下核对说明；
- **队长可以裁定可数的事实争议**：直接读原始产物，不采信任何一方的转述。

### 24. `agent-*.json` 事件里有两套坐标：`i` 字段 vs 数组下标

事件序列同时存在：

- **`i` 字段** —— 1-based，**从 2 起跳，且中间会跳号**（实测出现过 `i=6` 后直接 `i=8`、`i=11` 后直接 `i=13`）
- **`events[]` 数组下标** —— 0-based，连续

**两者不是恒定偏移，不能靠平移换算**（曾误以为「恒差 +3」，只是因为取样点恰好都落在差 3 的区间）。
引用事件位置时**必须写明是哪一套**：`events[3]`（即 `i=5`）。

### 25. `git status --porcelain` 的路径基准是**仓库根**，换 cwd 不会改变它

`fixtures/small_pkg` 在本仓库之内，它的仓库根是 `mini-claude-code`；而
`../targets/more-itertools` 有独立 git 历史，仓库根就是它自己。于是同一条
`git status --porcelain -- .` 在两种靶场下输出**不同形状**的路径：

```
cwd = fixtures/small_pkg      →   M fixtures/small_pkg/inventory/pricing.py   （多一层前缀）
cwd = targets/more-itertools  →   M more_itertools/more.py                    （就是相对路径）
```

`--porcelain` **强制忽略 `status.relativePaths` 配置**，`git status` 也**不接受** `--relative`
（那是 `git diff` 的选项）。所以「换了 cwd 输出就会跟着换」这个直觉是错的。

- 修法：用 `git rev-parse --show-prefix` 问 git 自己，再剥掉那一层。
- **教训（比这个 bug 本身更重要）**：`e23ff35` 把 `run_git` 的 cwd 改成 `FIXTURE` 时
  顺手删掉了原来的剥前缀逻辑，**只在外层靶场验证过** → 静默坏在仓库内靶场上，存在了 3 个提交，
  期间那侧每一次「干得完全正确」的运行都被判成「越界改动 → 未成功」。
  **改公共基础设施时要两种靶场都跑一遍。**

### 26. 「配不上就 `continue`」= 静默丢数据，而它会伪装成「工具没返回」

给工具结果配对时，早先的写法是「找不到对应的调用就跳过」。在真实旧产物上，
这表现为 `list_files() → 无结果` —— 看上去像工具什么都没返回，
实际是**配对把它丢掉了**。

> **判据：任何「找不到就跳过」的分支，都要问一句「跳过之后，读的人会看到什么」。**
> 如果看到的是「没有」，那它就不是跳过，是伪装。

### 27. 「连续两次相同」类检测必须带方向，也要说清中间发生了什么

`stalled_tests` 初版规则是「连续两次 `run_tests` 退出码相同」。在真实 A/B 产物上
它误报了一条「连续 2 次都是 **0**」—— 那其实是改完之后**复验通过**，是正常行为。

- 必须加方向：**非 0 才算卡住**；
- 必须说清中间**改没改过代码**：「改了还是失败」与「没改就原地重跑」是两种毛病，
  混成一句就等于没说。

### 28. 逐回合 `output_tokens` 恒为 0（键存在）；`turn` 不是 `num_turns`

- `AssistantMessage.usage` 里 `output_tokens` **键存在、值恒为 0**（实测 17 个回合全为 0）。
  真实的输出 token 只在 `ResultMessage.usage` 里。**别拿逐回合的 0 当成「没有输出」**，
  更不要据此写「哪一回合最贵」的结论。
- `turn` 是按 `AssistantMessage` 计的数，**与 SDK 的 `num_turns` 不是一回事**：
  实测同一次运行 `turn` 数到 17，而 `num_turns=9`（一个 API 回合会发多条助手消息，
  文本与工具调用各算一条）。

### 29. 旧产物里 `changed_files` 在 `metrics` **顶层**，不在 `termination` 下

读 `agent-*.json` 做兼容适配时，字段位置错了**不会报错**：报告会打印
「改动文件：(无)」，而那一轮判的却是 `success` —— 自相矛盾，但不翻旧代码发现不了。
**映射旧产物前先把 `metrics` 的键全列一遍，别按新格式的位置猜。**

### 30. 不要用 PowerShell 的字符串管线改代码文件

```powershell
# ❌ 会毁文件
(Get-Content -Raw $f) -replace 'a','b' | Set-Content -NoNewline $f
```

本机 `pwsh` 是 **PSVersion 5.1**：`Get-Content -Raw` 按 **ANSI/GBK** 解码 UTF-8 文件，
`Set-Content` 又按 ANSI 写回。机制是 GBK 解码器遇到「合法前导字节 + 非法后继字节」时
**吃掉两个字节并吐 `?`**，于是**行尾的 `\n` 被连带吞掉、多行合并**。
实测后果：676 行里 **143 行受损**、292 个 U+FFFD、`ERROR_KINDS = {` 被吞进上一行注释
→ `SyntaxError: unexpected indent`，**结构性损坏，只能整份重写**。

**改代码用编辑工具，不要走 shell 的字符串管线。** 同理 `Add-Content` 追加也会按 ANSI 写。

### 31. `.pyc` 里烘焙了**编译时**的绝对路径，仓库搬家后 traceback 会指向旧位置

靶场最初 clone 到 `%TEMP%\clonetest`，后来移到 `E:\aicoding\targets\more-itertools`。
在**没被改过的**测试文件上跑 pytest，报错路径仍然显示旧位置：

```
C:\Users\<用户名>\AppData\Local\Temp\clonetest\tests\test_more.py:911: AssertionError
```

**不要据此认为「跑的是另一份代码」** —— Python 会校验 `.pyc` 的 mtime + size，源码没动就复用缓存，
而 `co_filename` 是编译时写死的。判断实际跑的是哪一份，看 `git rev-parse HEAD` 与 `git status`，
**不要看 traceback 里的路径**。

### 32. 「准备脚本」重跑会重造靶场 HEAD 的哈希 —— 而哈希是冻结的

`setup_target.py` 的 `pin()` 是**无条件** `git checkout <固定 SHA>`。`inject()` 本身幂等
（内容已在文件里就跳过），但 **`pin()` 不幂等**：重跑会先把 HEAD 从缺陷提交 `d21dd31c…`
挪回上游 `81c21a8…`（缺陷随之消失），再重新注入 + 提交 —— `git commit` 带上新的时间戳，
于是**产生一个新的提交哈希**。

而 `d21dd31c3f1bde3a9a5de9e044bff7b2045af465` 冻结在 `README.md`、`AB-REPO-MAP.md`、
`VERIFY-agent.md` 与 12 份运行产物里。哈希一挪，所有「开跑前后 HEAD 一致」的断言就全部对不上，
**而且不报任何错**。

修法：`main()` 先判 `already_ready(dest)`（缺陷已在文件里 **且** 工作区干净）→ 跳过
clone/checkout/注入/提交，只做校验。只想校验现状、不碰任何东西，用 `setup_target.py --check`。

教训同陷阱 25：**一个「准备/校验」脚本只要会改动被别人引用的状态，就必须先判「是不是已经就绪」。**

### 33. 「预注册」不提交，就等于没有预注册

`AB-TOOL-DESC.md` 写好了 §1–6 的判定规则，我以为是"跑之前写好"就够了，转头去写探针和编排器。
24 次跑完才发现 `git log -- AB-TOOL-DESC.md` **是空的** —— 文件**从未入过 git**，
`git status` 里一直是 `??`。于是"判定规则早于数据"这句话**只剩下我自己的陈述**，仓库证不了它。

判定"这个文件有没有历史"只有一个办法：**`git log -- <path>`**。
`git status` 里的 `??` 意思是**从没提交过**（不是"改过"）。未跟踪文件**没有历史**，
mtime 可以随便改，什么都证明不了。

修法（下一轮起）：**先提交预注册，再开跑**；并在编排器里加一道闸门 —— 预注册文档有未提交
改动就**拒绝运行**。**约定没被检查拦住，它就只是一句话**（同陷阱 18–20 那次"跑完不复位"）。

### 34. 在 PowerShell 里把程序输出落成 UTF-8 文件，两头都要管

想把一个 Python 脚本的 stdout 存成 UTF-8 证据文件，链路有**两个**编码要管：

| 环节 | 错了会怎样 | 正确做法 |
|---|---|---|
| Python 的 stdout（输出到管道时用系统 ANSI = GBK） | 中文按 GBK 出、PS 按 UTF-8 解 → **U+FFFD**：标签全毁、**数字还在** | 脚本里在**第一个 print 之前**调 `force_utf8_output()`（陷阱 16） |
| PS 解码原生程序输出（`[Console]::OutputEncoding`） | 同上，方向反过来 | `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8` |

**另一条独立的坑**：PS 的 `>` 重定向写的是 **UTF-16LE（`0xFF 0xFE` BOM）**，不是 UTF-8 ——
下游按 UTF-8 读会直接 `UnicodeDecodeError`。要 UTF-8 就用 `Out-File -Encoding utf8`
（PS 5.1 会带 BOM，本仓库现有证据文件就是这么来的）。

写出来的文件别用眼睛验收：`read_text(encoding='utf-8')` 之后数 `"\ufffd"` 的个数，**0 才算过**。
本轮 `desc_stats.py` 第一版正是这样把一份证据文件的中文全变成 U+FFFD 的
（**数字全对**，所以肉眼看一眼很容易放过去）。

**同一件事在 `git commit -F` 上也成立**：`Out-File -Encoding utf8`（PS 5.1）会带 BOM，
而 BOM 会被原样写进 commit subject —— 实测 `9e6334a` 的 subject 开头就多了一个 `\ufeff`
（`git log --oneline` 里那一格显示为空白）。要干净就用
`[System.IO.File]::WriteAllText($p, $text, (New-Object System.Text.UTF8Encoding($false)))`
（`$false` = 不写 BOM）。BOM 不会让命令失败，只会在**你事后读自己的日志时**多一个隐形字符。

### 35. `--tools` 写一个**不认识的名字不报错**，而是静默退化成「零个内置工具」

SDK 的 `--tools`（`ClaudeAgentOptions.tools`）是**内置**工具的可用范围白名单。往里写一个
拼错/不存在的名字，CLI **不抛错、不警告**，直接当成"一个都没放行"。实测同形：

| `--tools` | 父层实际能用的内置工具 | spawn 调用 | 总消息数 | 异常 |
|---|---|---|---|---|
| `Agent`（模型的叫法，**不是 CLI 的名字**） | 无 | 0 | 341 | 无 |
| `NoSuchToolZZZ`（故意写错，阴性对照） | 无 | 0 | 232 | 无 |
| `Task`（**CLI 里真正的名字**） | `Agent` | **1** | 180 | 无 |

⇒ 同一个东西有**三个名字**：CLI / `--tools` 名单里叫 `Task`、模型看到的 `tool_use.name`
是 `Agent`、消息类型是 `Task*Message`；而 `allowed_tools` 里写 `"Task"` 实测能放行。
**推论（比这条坑本身重要）**：任何「放开了 X，看它会不会被用」的探针**必须带一个阴性对照**
（故意写错的名字 / 故意不给的能力），否则"0 次调用"无法区分**名字写错**与**通道不通**。

### 36. 只要工作被搬进子代理，旧量具就会**静默偏低**（而且偏低的方向讨好结论）

`ResultMessage.usage` 只统计**父** agent。子代理的用量单独在 `TaskProgressMessage.usage`
（`{total_tokens, tool_uses, duration_ms}`）里。实测同一个探针的两次配置：

| 量具 | 配置 `--tools Agent`（无子代理） | 配置 `--tools Task`（有子代理） | 变化 |
|---|---|---|---|
| `ResultMessage.usage.input_tokens` | 1,928 | 4,482 | **+132%** |
| `total_cost_usd` | 0.0287 | 0.2516 | **+780%（8.8×）** |

父层用量只涨了一倍多，真实成本涨了近八倍 —— 差额全在被漏掉的那一处。
⇒ 加任何"把活外包出去"的能力之前，先问一句：**这个能力花掉的资源，我现在的量具量得到吗？**
量不到就是"看起来更便宜"，而这正是最容易被写进结论的错误方向。

### 37. 幂等写操作要按**前置状态**判断，不能按"目标状态"判断

`setup_hard_target.py` 的注入函数原本这么判"是不是已经注入过了"：

```python
if d["new"] in text:      # ✗ 错
    continue
```

第 3 处缺陷的 `new` 是两行，**第二行 `len_iter = next(counter)` 正好是原文
`len_iter = next(counter) - 1` 的前缀** ⇒ 子串判定认为"已经在里面了"，
于是**静默跳过注入**：靶场只坏了 5 个测试（= 前两处），而期望是 32 个。
日志、退出码、`✓/✗` 全都没有异常 —— 唯一的表现是**失败数不对**，而那一刻我
还不知道它应该不对。

正确判据是**旧文本还在不在**，并且锚点必须恰好出现一次：

```python
if d["old"] not in text and d["new"] in text:   # 已是注入后的样子
    continue
if text.count(d["old"]) != 1:                   # 锚点不唯一 = 会改错地方
    raise SystemExit(...)
```

**推论**：目标是"让世界变成 S"，判据必须是"世界现在是不是 S 的前置状态 P"；
直接判 S 会在 **S 被部分满足**时误判为"已完成"。锚点数 != 1 也要当场拒绝 ——
`more.py` 里 `len_iter = next(counter)` 那类短行天然会撞车（同一文件另一处也有）。

### 38. 脏工作区会让基线"超时"，而最便宜的检查最容易被排到最后

`mutate.py` 报「基线 退出码=-1 失败=0 通过=0 用时 900s / ⚠️ 基线不是全绿」。
我先怀疑：①7 个遗留 python 进程抢 CPU ②解释器不对 ③这套测试本来就慢。
**三个全错。** 真因是靶场留着上一次被强杀的变异（`git status` = `M more_itertools/more.py`），
那处变异让测试**挂住**：`-q` 进度停在 73% 不动，几分钟都不过 82%。

决定性对照：`git checkout -- . && git clean -fd` 之后**同一条命令 29.9s / 768 passed**。

两条修法：

- 测量脚本开跑前先判工作区是否干净，脏就**拒绝运行**（`return 4`）并提示先还原 ——
  否则你测的是"别人改过的代码"，而且没有任何迹象。
- **「超时」要单独记一类状态**，不能和"没抓到"混进同一格：变异让测试挂住
  （可能死循环）与变异悄悄存活过全部测试，是两类完全不同的事实（同陷阱 4 的分寸）。

`git status` 这个最便宜的检查，我当时排在了最后。**排查顺序应该按"代价从低到高"排，
不是按"猜测的可能性"排。**

### 39. pytest-subtests 的失败行不是 `FAILED ...`，是 `SUBFAILED(...) ...::Class::test`

more-itertools 大量用 `pytest-subtests`。真实失败行长这样：

```
SUBFAILED(expected=[(0, 1, 2)]) tests/test_recipes.py::SlidingWindowTests::test_islice_version
```

只匹配 `^(FAILED|ERROR) (\S+)` 会把「3 个子测试失败」解析成**一个名字都没有** ——
看起来像"根本没失败"，而下一步就会据此把一条有效的变异判成"没抓到"并扔掉。

```python
FAILNAME_PAT = re.compile(r"^(?:FAILED|ERROR|SUBFAILED\S*)\b.*?(\S+::\S+)", re.M)
```

**普适教训**：正则要拿**真实输出的原始字节**对一遍，不能拿想象中的行格式
（同"自检正则读不懂 `%r` 打出来的带引号日志"）。而且要把原始输出**落盘**——
失败测试叫什么名字，往往正是判断这件事够不够格的关键证据。

### 40. PowerShell 里 `*>` 重定向之后，退出码检查落在**另一条流**上

起跑批处理时写的是：

```powershell
& $PY runner.py ... *> 'out\run.log'      # 日志里只有 runner 的输出
"COMPILE=$LASTEXITCODE"                   # 这行只会打到作业的 stdout
```

`py_compile` 报了 `C=1`（真的是语法错误），但**我读的是日志文件**，日志里只有
runner 自己的 traceback，于是差点以为"编译通过、是运行期问题"。

- 重定向到文件之后，**退出码必须打到一个你也真的会读的地方**；
- 打印一个字符串不会让整条命令失败（字符串的 `$LASTEXITCODE` 永远是 0）——
  要 `if ($LASTEXITCODE -ne 0) { exit 1 }`，让作业本身红掉；
- 读日志 ≠ 读结论。**作业的 stdout 和重定向文件是两个地方，都要看。**

顺带：`py_compile` **确实**能抓到 `global X` 写在"使用 X 之后"的
`SyntaxError: name 'X' is used prior to global declaration` —— 这次就是它抓到的。

### 41. 子代理的回合上限会把报告**截断在半路**，而且只在通知里留痕

`AgentDefinition(maxTurns=8)`：第一轮 4 次强制运行里**有 2 次**被截断
（子代理工具调用 26 / 20 次），`TaskNotificationMessage.summary` 头一句写着
*"NOTE: this agent stopped at its 8-turn limit before finishing. The text below is PARTIAL"*。
跑完的那几次只用了 12–17 次工具调用。

被截断的子代理交回**半份报告**。那不是"分工有没有用"，那是"分工被我自己的预算掐死了"，
而且偏差方向很坏：**同时低估子代理的成本和父层的收益**，两边都朝"看起来更便宜"偏
（同陷阱 36 的方向）。

⇒ 用子代理做实验之前先立一条检查：**每份通知里有没有 turn limit**。
上限按"跑完那几次实际用了多少"定，别拍一个整数。

### 42. 探查产物结构时，字段名要**现查**，不要猜

第一版 `subagent_review.py` 猜了顶层 `run` 和事件里的 `text` —— 全错：
运行头在 **`trace["run"]`**，事件里的文本字段叫 **`preview`**，真实长度在同一条记录的
**`chars`** 里。猜错的后果不是报错，而是一行**空值**：脚本"跑通了"、输出"有字段"，
但那个字段从来没有被填过（同 `ToolResultBlock.content` 其实是 dict 那次）。

⇒ 探查脚本的**第一步永远是列 `list(d.keys())` 和 `Counter(type)`**，把结构打出来再写逻辑。

### 43. 对照臂**没被施加治疗**时，两臂比较测的是噪声 —— 而且噪声能让"区间不重叠"成立

④ 层硬靶场的 A/B：臂 B′ 的任务是"邀请它分工"，结果 `subagent_spawn_calls = 0`（4/4）。
也就是说 **臂 B′ 与臂 A′ 的唯一差别是题面里多了一段邀请文字**，治疗从未发生。
而按预注册的判据（"两臂极差区间不重叠 = 有信号"），这一格**成立了**：
B′ 最大 26537 < A′ 最小 27317，只差 **780 tokens（2.5%）**。

一个**空治疗**就能让判据通过 ⇒ 判据太松，而且松的方向正好是"看起来有发现"。

复核之后真正的差别是：A′ 有两次跑了 33–37 次工具调用，B′ 四次都在 22–28 次。
**是轮数的落差，不是分工的落差。**

⇒ 结论性实验的预注册必须写两条而不是一条：

1. 主指标的判据（区间/显著性）；
2. **治疗真的被施加了的证据**（这里就是采用率 ≥ 1）—— 没有这条，主指标判**"不可测"**，
   不判"支持"。把"没采用"和"没效果"分开记录，是两件完全不同的事。

同类的坑还有一层：**"没被用到"本身要能在产物里被看见**。所以 `coding_agent.py` 在
**不放开** ④ 层时也会写 `subagent_enabled: False`，
而不是让这个字段缺席 —— 缺席和"有这一层但一次没派"在下游会长得一模一样。

### 44. `findings` id 的唯一性作用域必须 ⊇ 检索作用域 —— 否则第二轮起必然撞名

`read_findings(id)` 的三件东西作用域不一致：id 只用 `g<k>-f<n>`（**单份产物内**唯一）、
`AGENT_FINDINGS_DIR` 指向**共享**的 `out/`、而检索是对 `out/scout-*.json` **全目录 glob 且要求唯一命中**。
于是跑批场景（多轮产物同目录）下：

- D 臂 4 轮共 12 次 `read_findings`，**12 次全失败**：`out/code-tools-server.log` 里只有「handler 调用」行、
  没有一次成功；同一个 `g1-f1` 出现在 5 份产物里。
- **工具本身是好的**：隔离正对照（只有一份产物）3/3 成功，取回 2620 / 1601 / 2088 字符。
  ⇒ **「工具不可用」与「在跑批上下文里不可用」是两件事，必须分开写**。
- 修法（`53428d8`）：id 带运行 label 前缀 `<label>-g<k>-f<n>`，`read_findings` 对带 label 的 id 直接锁定
  唯一产物；旧形状 id 仍可读，重名时明确报错并**列出可用的带 label id**。修后注册批 **11 调用 / 11 成功 / 0 失败**。

**通用判据**：标识符的**生成作用域**必须 ⊇ **检索作用域**。id 在一份产物内唯一、检索却是全目录时，
撞名不是概率问题，是必然。

### 45. 汇总入口的默认值会静默毁数据（`--report`）

`subagent_ab_runner.py` 的 `ARMS` 默认 `('A','B','C')`、`--fixture` 缺省回落到 `more-itertools-multi`，
而 `--report` 会**以 `'w'` 覆盖** `runs.jsonl` / manifest。实测：合同字面命令 `--report --tag subagent-ab-v2`
把 8 行汇总**覆盖成 4 行**、manifest 写「完成 4/12 次」，**不报任何错**（D 臂整臂消失）。

已修（`53428d8`）：从已有产物**推导**臂集、写入前校验，产物不齐即 `exit=3` 拒写并点名会丢的 label
（复核：产物齐全 → exit 0 且 8 行文件**字节未变**；只留 A 产物 → exit 3 拒写）。

⇒ **教训**：任何「重读 + 覆盖」的汇总入口，「读哪些」这类参数**不许有默认值** —— 只能从产物推导或强制显式给。

### 46. 多消费一段会话，可能把「谁的结论」换掉（作者归因）

`_scout_session` 原本收到 `TaskNotification` 就 `return`（提前关掉 CLI 会话，`ResultMessage` 永远到不了）。
R5 改成继续消费到尾段之后：**driver 包装会话自己的后续文本变成了「scout 的结论」**。

产物级证据（不是推测，是 SDK session transcript）：`~/.claude/projects/E--aicoding-mini-claude-code/205cb8f2-….jsonl`
第 69 行、`isSidechain=false` —— **主会话自己的 assistant 文本**（2984 字符）里含
`{"findings":[{"id":"g3-f1",…}]}`，与 scout 产物记录的 finding 逐字一致。时间线：第 1 个通知 →
包装模型说「scout 截断、没交报告」→ **自己又派了一次 Task** → 自己调 11 次代码工具 → 自己交出 findings。

⇒ 修法（`6583afd`）：`select_findings_payload()` 只认 scout 的**三个来源**（通知 summary / scout `output_file` /
带该次 Task `parent_tool_use_id` 的流式文本），**包装会话文本一字不收**；装配层用 `PreToolUse` 钩子按 `agent_id`
判来源（主线程调 `mcp__code-tools__*` 一律 deny、scout 放行），并限制「这一轮只许派一次 Task」。

**通用判据**：只要数据有"作者"，作者归因就必须由**结构**（来源白名单 + 权限层）保证，
不能靠"它应该是谁的"来推断；改记账代码时先问一句 —— **它会不会顺手改掉数据的作者**。

### 47. 钩子挡得住「效果」，挡不住「花费」

给包装会话加 `PreToolUse` 钩子后，被拦下的尝试 `effective=0`（结论不再来自包装会话）—— 但账单**涨了**：

| 同一形态（cap=2 负对照） | scout 侧账单 | 会话墙钟 Σ |
|---|---|---|
| 修复前 `v2-fix-trunc` | **1.404199 USD** | **160.9s** |
| 修复后 `v2-b1c-trunc` | **5.77702 USD** | **765.7s** |

模型被拒后**反复重试**（每次被拒仍然要付费）⇒ 账单约 **4.1×**（逐行求和证据：`policy_scout_usage.jsonl`）。

两条口径纪律：① `policy_scout_cost_usd` = 该次运行所有 driver 会话的 `ResultMessage` 成本合计，
**含包装模型自己的回合与被拒后的重试**，不等于"scout 的成本"；② `TAIL_AFTER_NOTIFY_S=180` 是**每条消息**的超时，
不是尾段总预算（实测一次 attempt 187.4s 仍拿到 `ResultMessage`），尾段只受外层 900s 约束。

⇒ 加任何"授权 / 拒绝"机制时，**效果指标与成本指标要分开量**；权限拒绝通常**不省钱**。

### 48. findings 的「没定位」和「报告解析失败」必须分开记

某一组被记成「没有拿到 scout 自己的 findings JSON」（字面对），但查 transcript：那个 scout **已经定位了**
（`more_itertools/more.py` line 2732-2763、`len_iter = next(counter)`、`predictedFailureCount=27`），
只是报告不是合法 JSON（字符串里用了 Python 风格的 `\'`，`json.loads` 报 `Invalid \escape`），
于是按规则记 `undiagnosed`、父层自己补做。

⇒ **判据**：抽取失败的原因要写进 `why` / 诊断字段 —— 「没定位」「报告非 JSON」「无报告」是三种不同事实，
合并成一句会把「结论率 x/K」读成能力问题。同陷阱 26：**看起来一样的"空"，来源可以完全不同。**

### 49. 质量门槛只验「申报项在不在 inScope 内」，不核对申报是否属实

`quality-gates.js`：完成门槛 `evaluateQualityCompletion`(:347) 的 implementation/repair 分支(:392-403)
只对每个申报路径调 `classifyChangedPath`(:130-140) 做 inScope 分类；`collectChangedPaths`(:141-161)
**从不被调用** ⇒ **不对照 `git status`**，也不检查申报完不完整。现场证据：一次提交实际改了 **9 个文件**，
只申报 **1 个**，门槛照样放行。

同一条门槛也**没有**「只准申报一个元素」的限制（`nonemptyStringList`(:179-181) 允许任意长度 ≥1；
:397-402 是逐元素循环；:400 的报错按 `<path>` 参数化）。

⇒ **「门槛通过」≠「申报属实」**，两者要分开写；改动的完整性只能自己核（`git show --stat`）。

### 50. AgentTeams：成员被移除后 `edit_plan` 因整图校验永久不可用

本队实测：一旦有任务引用了**被移除成员**的旧 assignee，整图校验就永久卡住 ⇒ **`edit_plan` 改不了依赖**
（报错与具体那个任务无关，看起来像别的问题）。三次撞上（t6 / t8 / t12），处置一律是
**取消旧任务 + `create` 等价任务、把依赖改到新的上游**（t13 / t14 / t15）。

⇒ 建任务时把依赖建在对的节点上；**要改图就重建节点**，别在 `edit_plan` 上耗时间。

### 51. AgentTeams：自动闭环 3 轮 review 触顶后，依赖失败任务的整图会停摆

插件在 review 判 `needs_revision` 时会自动生成下一轮「修复 + 复审」，但**上限 3 轮**。本队到第 3 轮复审
（t18）后触顶：不再生成第 4 轮修复，而后代任务全部依赖那个被判失败的任务 ⇒ **整图停摆**
（t12–t16 只能取消 + 重建）。最后靠队长把**判据定义**写进重跑合同、并经用户批准跳过一轮修复才继续。

⇒ 两条纪律：① 依赖链上要留一个"可由队长改写合同"的出口；
② **review 的判据定义必须写在数据之前** —— 否则会出现「数字都跑完了、门槛定义还在改」的局面
（本队两次踩到：t18 的 F-gate、t19 的合同改写）。

### 52. 长交付物落文件、消息只传路径；工作区之外的项目必须给绝对路径

本队实测的**假阴性**：审查员把 §7 草稿写成文件（19112 字节 / 198 行，sha256 可核）并在通知里给了**绝对路径**；
接收方用**工作区相对**的 glob（`**/*section7*`）去找 ⇒ 报「文件不存在」。根因不是文件不在，而是
**团队工作区在项目目录之外**（工作区之外），相对 glob **扫不到工作区外**。

⇒ 两条纪律：① 长交付物**落文件、消息只传路径**（邮件容易被截断）；② 对工作区之外的项目，
**收发双方都用绝对路径**（`read` 工具 + 绝对路径），别用工作区相对的 `glob`。
报告「文件不存在」之前，先确认**自己用的是哪个根**。

### 53. 两条被实测否决的「会话级权限」路线（附证据限制）

要给 driver 包装会话加上「不许用代码工具、只许派一次 Task」，两条路试过都不行：

- **会话级 `allowed_tools` 只留 `["Task"]`**：白名单是**整会话共享**的，scout 也被一起掐死 ——
  3 组全 `undiagnosed`、0 份 scout 结论，scout 侧账单 **2.175183 USD**（≈2.18）白花。
  ⚠️ **证据限制**：这条目前只有「3 组 undiagnosed + 账单 + SDK 警告」级别的证据，
  **缺 scout 侧可复核的被拒计数 / 文案**（`scout_tool_results` / `scout_tool_errors` 当时没落进 durable 行）。
  要把它当硬结论引用，先按那次复审的建议把 scout 侧计数补进状态记录。
- **`can_use_tool` 回调按 `agent_id` 判来源**：SDK 直接警告
  `CanUseToolShadowedWarning: can_use_tool will not be invoked for: Task` ——
  `allowed_tools` 里放行了整个工具时，回调**根本不会被调用**；实测那次探针 4.1s 就 `failed`、
  `scout_tool_results=0`、**连 TaskNotification 都没等到**。要它生效得把权限模式从 `dontAsk` 改成 `default`。

最终采用 **`PreToolUse` 钩子按 `agent_id` 判来源**，权限模式保持 `dontAsk`（与之前所有实测批次同形）。
证据：`out/probe_b1_permission.txt`、`out/probe_b1_hook_deny.txt` vs `out/probe_b1_stream.txt`。

⇒ 教训：SDK 的「回调式权限」与「名单式权限」作用层级不同，**先看 SDK 的 warning 再动手**；
探针要留 `scout_tool_results` / `scout_tool_errors` 这类**双向证据**，否则"被掐死"只能靠账单反推。

### 54. ⚠️ 共享 venv 的 `.pth` 竞态会造成**静默误判**（最贵的一种错）

评测 harness 早期把 venv 命名成 `<repo>-<base_commit[:8]>`，**同一 base 的所有 run 共用一个 venv**；
而 editable 挂载点是写在 venv 里的 `.pth`，文件名**只由 instance_id 决定**、不含 run_id。

`.pth` 在解释器启动时**按文件名排序逐个执行**，每个都 `sys.path.insert(0, ...)`
⇒ **最终谁排最前由文件名排序决定，与「哪个 run 正在跑」完全无关**。
两个 run 跑同一实例 ⇒ 必然有一个拿到**别人的工作区**。

实测（t4 独立复核 + t8 复现，两次都 6 轮 6 次触发）：4 轮被导入自检拦成 `infra_error`，
**2 轮是静默误判** —— 判定用的是别人的代码，却给出正常的 `agent_result`、`notes` 为空、无任何告警。

⇒ 教训：
- **别去调 `.pth` 的命名顺序**（试过：只把 run_id 加进文件名 ⇒ 两个挂载仍同时存在，胜负照样由排序决定）。
- **也别指望 `PYTHONPATH`**（实测无效：`.pth` 的 `insert(0,...)` 发生在 site 阶段，会把 PYTHONPATH 挤到后面）。
- **正确修法是消掉共享可变状态本身**：venv 按「实例 + run」隔离（t8，提交 `7e64816`，代价实测仅 4.8 s）。
- 「每人写一个 claim 文件再互相看」的闸门会**对称死锁**（双方都以为要等对方，6 轮全卡死、零结果）——
  要互斥就用单一原子锁（`os.open(..., O_CREAT|O_EXCL)`）。
- **判定前后各核一次「导入路径必须落在本实例工作区」**，不符即 `infra_error` —— 这是拦静默误判的最后一道闸。

### 55. ⚠️ 用 `instance_id` 拼出来的路径做「唯一性」断言 = **空证**

并发隔离的断言原写成：

```python
ws = [E.ws_path(run_id, p["instance_id"]) for p in instances]
all_ok &= (len(set(ws)) == len(ws))     # 恒真
```

`ws_path()` 只是把 `run_id` 和 `instance_id` **拼字符串** ⇒ 不同实例天然得到不同字符串，
这条断言**永远为真**，和「工作区在磁盘上是否真的隔离」**没有任何逻辑联系**。
它被当成过「并发隔离」的通过证据 —— 那是**无效证据**。

⇒ 教训：**断言必须比磁盘上的真实状态，不能比你自己拼出来的字符串。**
`realpath + normcase` 之后互异、且必须真实存在、且落在本批次目录下 —— 三条都要有。
**并且要反向自证判别力**：人为让两条指向同一真实路径（Windows 上 `mklink /J` 建 junction），
断言**必须失败**；不失败就说明它还是空证。

### 56. ⚠️ 被测包恰是 pytest 的依赖时，`pip uninstall` 会让 pytest 崩

`pluggy` 既是待测包、又是 pytest 自身的依赖。site-packages 里的 **PyPI 副本会压过工作区**
⇒ agent 改了工作区代码，pytest 却加载 PyPI 那份 ⇒ **判定变成在测一个根本没被修改的包**。

- ❌ `pip uninstall pluggy` ⇒ pytest 直接报 `PackageNotFoundError: No package metadata was found for pluggy`
- ✅ 用 **import 行式 `.pth`**（`sys.path.insert(0, <包父目录>)`）把工作区插到最前

### 57. `install_cmd` 是**一个字符串**，不是 token 列表

按列表用 ⇒ 解析出**空依赖** ⇒ venv 里没有 pytest ⇒ 所有实例都判成 `infra_error`，
而症状看起来像「agent 什么都没干」。现在 `shlex.split` 并**两种形状都吃**。
⇒ 教训：外部声明的字段形状要**现查**（同陷阱 42），别按直觉的类型假设。

### 58. agent 的自评可能与独立判定**相反** —— 所以判定绝不能采信自述

t5 端到端冒烟实测：agent 自评 `==> 判定：未成功`（它自己的整仓 pytest 退出码口径），
而 harness 独立判定是 `agent_result` / **`resolved=True`** / F2P 5/5、P2P 60/60。
人工核对（忽略行尾）后确认：**它的语义改动恰好就是真实修复**，只是被回合上限打断了。

⇒ 判决**只认 harness 自己启动的 pytest**。若采信自述，这一条会被判错。

### 59. agent 写文件产 **CRLF**，会让 9 行改动膨胀成 251 KB 补丁

agent 的 `apply_patch` 把文件写成 CRLF，而基线 commit 是 LF ⇒ `git diff` 认为**每一行都变了**
⇒ 输出 `@@ -1,3134 +1,3135 @@` **整文件单 hunk**。实测同一处 9 行语义改动：
**251,255 字节**（相对真实 diff 放大 198.2×）。

⇒ 修法：**在采集 diff 之前**把工作树的行尾归一（只动被修改的已跟踪文本文件，
不碰未跟踪文件、跳过二进制）；补丁正文的归一只是第二道防线。

⚠ 机理别搞错（我第一版就写错了）：
- **`git diff` 并不归一化行尾** —— 字节模式读出 **254,616 B / CRLF 3,361 处**，CRLF 还在。
- 是 harness 自己的 `subprocess(..., text=True)`（**universal newlines**）把 CRLF 折成了 LF
  ⇒ 文本模式 251,255 B / CRLF 0。
- ⇒ **归档的补丁文本天然不含 CRLF**，它是**损失性产物**，不能用来证明「采集时正文没有 CRLF」。
  判别法：归档件里若有**逐字相同的 `-`/`+` 行对**（如 `pyproject.toml` 212/213 对），
  说明正文原本就有 CRLF —— 否则 git 不会产出这些 hunk。

### 60. 复核者自己的脚本也会有 bug —— 必须**自证清白**

t4/t8/t9 三轮都出现过「复核脚本自己坏了，却报出看起来很有力的结论」：
- t9 抓出 t8 的 D7 注释**结论对、理由错**（见陷阱 59）；
- t8 的第一版竞态验证脚本归属匹配写错（用子串匹配路径），**归属全是 `[]`**，
  差点把「修好了」误报成「没修好」；同一脚本第一版还用**串行结果**当并发基线；
- t8 的第一版 D7 取证量错了对象（去量**归档的补丁正文**而非**工作树**），得到无意义的 1.0×。

⇒ 教训：
- **反向对照优先**：正向跑通证明不了任何事。断言要能**失败**（真去构造它该失败的情形）。
- 数字类结论要**附产物 + 位置**（同陷阱 23）。
- 自己写的复核脚本，**先让它在一个已知坏掉的输入上红一次**，再信它的绿。

## 写代码时的约定

- **密钥不进代码、不进仓库**：实验脚本从**项目根 `.env`** 读（只有交互式 Claude Code 才用 `~/.claude/settings.json`）。
  打印走 `redact()` 的**双判据** —— 键名像密钥（`TOKEN|KEY|SECRET|PASSWORD|AUTH`）**或**值长得像凭据
  （`sk-…` / `Bearer …`），任一命中就打码。理由：白名单挡不住将来加进来的
  `ANTHROPIC_CUSTOM_HEADERS: "… Bearer <key>"` 这类键，**漏掉一个的代价远大于多打码一个**。
  错误信息落盘前过 `scrub()`。
- 提交前做一次密钥泄露检查（查完整 key、前缀、`sk-` 形态三种特征）
- 验证类实验要**设计能失败的对照**，不要把"跑通了"当结论

## 怎么确认某个文件有没有被加载

CLAUDE.md 只从**当前工作目录及向上每一级**加载——**只向上，不横向**。要确认，别问"你加载了什么"，用可判定的问题：

```powershell
claude -p '不要使用任何工具。只依据启动时已加载的上下文回答：其中是否出现字符串 "X"？出现就逐字引用那一行，否则只输出 NOT_LOADED。'
```

引错一个字就露馅。会话内也可以直接跑 `/context`，看 **Memory files** 一栏。

## 交付与记录

- 每日日志与证据在本机私有工作区（不进本仓库）
- 每天的进展写进私有工作区的 `daily/YYYY-MM-DD.md`，**当天写完不隔夜**
- 「AI 帮倒忙 / 踩坑」一栏**必须写实**，不要美化
- 遇到真实卡点，提醒用户归档进私有工作区的「坑与经验库」
