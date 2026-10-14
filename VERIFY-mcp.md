# MCP 验证记录：独立进程 + stdio，同一个 server 服务两个客户端

**日期**：2026-09-16
**目的**：不是背「MCP 解决了 Function Calling 的什么问题」，而是**用实验证明那一句话**——
同一个 server 进程、同一份代码，被两个毫不相干的客户端使用，**server 一行都不用改**。

---

## 结论摘要

| # | 命题 | 判据 | 结果 |
|---|---|---|---|
| 1 | 客户端 A（Claude Code）能连上并调用 | `claude mcp list` + 真实调用返回 | ✅ 「hello world foo」→ 字符数 15，词数 3 |
| 2 | 客户端 B（Agent SDK）能连**同一个** server | 3 次真实 tool_use | ✅ 同上字符串 |
| 3 | 两个客户端用的是**同一份代码** | 文件 SHA256 全程未变 | ✅ `fc0cdbc8…5261` |
| 4 | 启动路径写错就**连不上** | `claude mcp list` | ✅ `✘ Failed to connect` |
| 5 | 工具**真的从可用列表消失** | 可判定问法 | ✅ `NOT_LOADED`（好配置同问法 = FOUND） |
| 6 | 越界读文件被拒 | 探针 + 两个客户端 | ✅ 返回「拒绝」，未泄露内容 |

**一句话结论**：把工具从「进程内的一个 Python 对象」换成「一个用 stdio 说话的独立进程」之后，
**客户端换了、语言可以换、进程可以换**，而 server 侧零改动 —— 这就是 MCP 相对裸 Function Calling 多出来的那层。

---

## 〇、先钉死「同一份代码」

这一条是整个验证的地基。用**哈希**，不用体积/时间戳这类旁证：

```
$ sha256sum servers/local_tools_server.py
fc0cdbc8c50ac7581e82f6c69da530b8ab182b6a0eda68fcc821ba55f0d95261 *servers/local_tools_server.py
```

这个哈希在**两个客户端全部跑完之后**复算，与跑之前一致 ——
即：客户端 A 跑完 → 客户端 B 跑 → 反向对照 → 哈希不变。**中途没有任何一行改动。**

### 环境

| 项 | 值 |
|---|---|
| conda 环境 | `aicoding`（Python 3.12.14） |
| `mcp` | 2.2.0（**注意 API 已改名**，见「附带发现 1」） |
| Claude Code CLI | 2.1.272 |
| 模型/端点 | DeepSeek（`api.deepseek.com/anthropic`，`deepseek-flash[1m]`） |

### server 是什么

`servers/local_tools_server.py` —— 独立进程，stdio 传输，两个工具：

| 工具 | 参数 | 说明 |
|---|---|---|
| `word_count` | `text: str` | 算法与昨天的进程内版本**逐字一致**，便于直接对比输出 |
| `read_study_file` | `relpath: str` | 读 `STUDY_ROOT` 下的文件；越界（`..` 逃逸、绝对路径）一律拒绝 |

启动命令（客户端 A 和 B 用的是**同一条**）：

```
D:\Anaconda3\envs\aicoding\python.exe E:\aicoding\mini-claude-code\servers\local_tools_server.py
```

---

## 一、客户端 A：Claude Code

注册（`local` 作用域，先跑通这个）：

```bash
$ claude mcp add local-tools --transport stdio -- "D:\Anaconda3\envs\aicoding\python.exe" "E:\aicoding\mini-claude-code\servers\local_tools_server.py"
Added stdio MCP server local-tools with command: D:\Anaconda3\envs\aicoding\python.exe E:\aicoding\mini-claude-code\servers\local_tools_server.py to local config
File modified: C:\Users\<用户名>\.claude.json [project: E:\<私有目录>]
```

> `--` 分隔符必须加。不加的话，Claude Code 会把 server 自己的参数当成它自己的选项解析。

健康检查：

```bash
$ claude mcp list
Checking MCP server health…

local-tools: D:\Anaconda3\envs\aicoding\python.exe E:\aicoding\mini-claude-code\servers\local_tools_server.py - ✔ Connected

$ claude mcp get local-tools
local-tools:
  Scope: Local config (private to you in this project)
  Status: ✔ Connected
  Type: stdio
  Command: D:\Anaconda3\envs\aicoding\python.exe
  Args: E:\aicoding\mini-claude-code\servers\local_tools_server.py
```

> ⚠️ **与预期不符的一处**：本版本的 `claude mcp list` **不显示工具数**，只有 `✔ Connected`。
> 所以「连上了」这句话在 list 里**是拿不到工具层面证据的**，必须真的调一次。

真实调用（`claude -p`，非交互）：

```bash
$ claude -p "请调用 word_count 工具，参数 text 传 'hello world foo'，然后把工具返回的结果原样告诉我。" \
    --allowedTools "mcp__local-tools__word_count" --output-format json
```

原始输出（完整 JSON 已落盘：`servers/experiments/out/client-a-claude-code.json`）：

```
subtype            = success
is_error           = False
num_turns          = 2
total_cost_usd     = 0.13553700000000002
permission_denials = []
result             = 「hello world foo」→ 字符数 15，词数 3
```

`num_turns = 2`（一次工具调用 + 一次作答）说明工具调用真的发生了，不是模型凭空复述。

---

## 二、客户端 B：Claude Agent SDK（stdio 配置）

**它和客户端 A 没有任何共享代码** —— 一个是 CLI 应用，一个是 Python 脚本。

与昨天的 `hello_agent.py` 唯一的差别，就是 `mcp_servers` 这一行：

```diff
- server = create_sdk_mcp_server(name="localtools", version="1.0.0", tools=[word_count])
- mcp_servers={"localtools": server}                 # ← 传一个 Python 对象
+ mcp_servers={"local-tools": {                      # ← 传一段「怎么把进程拉起来」的描述
+     "type": "stdio",
+     "command": PYTHON,
+     "args": [SERVER_SCRIPT],
+ }}
```

> 形状不是我编的，是从 `claude_agent_sdk/types.py:606` 的 `McpStdioServerConfig` 读出来的：
> `type: NotRequired[Literal["stdio"]] / command: str / args: NotRequired[list[str]] / env: NotRequired[dict[str,str]]`

原始输出（完整日志：`servers/experiments/out/stray-handler-clientB.log` 为实验版，本节为生产版运行）：

```
=== 本客户端使用的 MCP 配置 ===
  mcp_servers = {"local-tools": {"type": "stdio", "command": "D:\\Anaconda3\\envs\\aicoding\\python.exe", "args": ["E:\\aicoding\\mini-claude-code\\servers\\local_tools_server.py"]}}

[tool_use]  mcp__local-tools__word_count({"text": "hello world foo"})
[tool_result] {"result":"「hello world foo」→ 字符数 15，词数 3"}

[tool_use]  mcp__local-tools__read_study_file({"relpath": "大纲.md"})
[tool_result] {"result":"<私有文件内容，公开版已省略>\n\n…（全文略，工具完整返回）"}

[tool_use]  mcp__local-tools__read_study_file({"relpath": "../../Windows/win.ini"})
[tool_result] {"result":"拒绝：../../Windows/win.ini 越出 E:\\<私有目录>（本工具只允许读该目录之内）"}

=== 结果 ===
  subtype        = success
  is_error       = False
  num_turns      = 4
  total_cost_usd = 0.176567

=== 工具调用清点 ===
  调用次数 = 3
    - mcp__local-tools__word_count
    - mcp__local-tools__read_study_file
    - mcp__local-tools__read_study_file

=== 判定：两个工具都被真实调用，且运行无错 ===
=== 越界拦截：命中（返回中含「拒绝」） ===
```

**两个客户端返回的是同一个字符串** `「hello world foo」→ 字符数 15，词数 3`
—— 一个由 Claude Code 的进程树产生，一个由 Python 脚本的进程树产生，来自**同一个 server 文件**。

---

## 三、反向对照（不失败就不算数）

### 3.1 启动路径写错 → 连不上

单变量：**只**把 server 脚本路径改成不存在的文件，可执行文件不变。

```bash
$ claude mcp add local-tools-BAD --transport stdio -- \
    "D:\Anaconda3\envs\aicoding\python.exe" "E:\aicoding\mini-claude-code\servers\__does_not_exist__.py"

$ claude mcp list
Checking MCP server health…

local-tools: D:\Anaconda3\envs\aicoding\python.exe E:\aicoding\mini-claude-code\servers\local_tools_server.py - ✔ Connected
local-tools-BAD: D:\Anaconda3\envs\aicoding\python.exe E:\aicoding\mini-claude-code\servers\__does_not_exist__.py - ✘ Failed to connect — -32000: MCP error -32000: Connection closed
```

同一个解释器，一条 ✔ 一条 ✘ —— 证明 `✔ Connected` 是**真的握手**，不是配置写对了就给个绿灯。

（验证完已 `claude mcp remove local-tools-BAD -s local` 清理。）

### 3.2 工具是否**真的从可用列表消失**

3.1 只证明了「连接状态」是真的。工具列表还得单独证。

用 `--mcp-config` 注入临时配置 + `--strict-mcp-config` 隔离其它配置（**不动你的现有配置**），
再用**可判定的问法**问模型 —— 不问「你有什么工具」（会得到自信但可能编造的答案），
而是问「上下文里是否出现字符串 X」：

```bash
# 坏配置
$ claude -p '不要使用任何工具。只依据启动时已加载的上下文回答：其中是否出现字符串 "mcp__bad__word_count"？出现就逐字引用那一行，否则只输出 NOT_LOADED。' \
    --mcp-config "...\mcp-bad.json" --strict-mcp-config
NOT_LOADED

# 好配置（同问法，只换配置）
$ claude -p '…是否出现字符串 "mcp__good__word_count"？…' \
    --mcp-config "...\mcp-good.json" --strict-mcp-config
出现了。它出现在启动时加载的**工具定义**里 —— 其名称字段逐字为：
"name": "mcp__good__word_count", "description": "统计一段文本的字符数和词数。…"
（同一组定义中另一个工具是 mcp__good__read_study_file；入参 schema 为 {"text": string}）
```

一个 **FOUND / 一个 NOT_LOADED**，唯一变量是配置指向的 server —— 工具可用性确实随连接状态变化。

> 顺带证实：工具的 `description` 和 `inputSchema` 是**从 Python 函数的 docstring 与类型注解自动生成**的
> （`{"text": string}` 正对应 `def word_count(text: str)`），不需要手写 schema。

> ⚠️ **诚实记录一处偏差**：我要求「逐字引用」，模型复述时把原文的中文逗号 `，` 打成了半角 `,`。
> 结论不受影响（工具名在不在是决定性的），但这说明 **「逐字引用」这个保证本身有偏差**，
> 不能拿它当字节级证据用。

### 3.3 越界读文件必须被拒

上面两个客户端都跑了这条，返回：

```
拒绝：../../Windows/win.ini 越出 STUDY_ROOT（本工具只允许读该目录之内）
```

注意它返回的是**业务结果**而不是异常 —— 边界由工具自己守，调用方无法靠构造路径绕过。

---

## 四、stdout 实验：那条「绝不能用 print」的规则，到底该怎么说

### 4.1 起因：任务书里的硬规则和源码对不上

任务书写「绝不能用 print() 往 stdout 写东西」。但读 `mcp/server/stdio.py:161-179` 时发现：

> While serving, fd 0 points at the null device and **fd 1 at stderr**, so handlers and children read EOF
> and their **stray output misses the wire**; both descriptors are restored on exit.

实现是 `_claim_fd(1, sys.stdout, "wb", _open_stdout_diversion)`，而 `_open_stdout_diversion()` 就是 `os.dup(2)`。
注释点名这是 issue #1933 的修复。**也就是说 transport 启动之后，fd 1 被换成了 stderr。**

### 4.2 实验 (a)：handler 里故意往 stdout 写

`servers/experiments/_stray_in_handler_server.py` 在 `word_count` 的 handler 里写两行：

```python
print("!!! STRAY-VIA-PRINT !!!", flush=True)      # 走 sys.stdout 对象
os.write(1, b"!!! STRAY-VIA-OSWRITE-FD1 !!!\n")   # 绕过 Python 对象，直接写 fd 1
```

用探针跑（**保持 stdin 打开**，否则 stdin 提前 EOF，`tools/call` 的响应还没读就收摊了）：

```
$ python servers/experiments/probe_client.py --protocol legacy out/stray-handler -- <直连 python.exe> _stray_in_handler_server.py
  initialize OK: server={'name': 'stray-in-handler', 'version': '1.0.0'} protocol=2025-06-18
  tools/list -> ['word_count', 'read_study_file']
  tools/call word_count -> 「hello world foo」→ 字符数 15，词数 3
  server 退出码: 0

=== 管道体检 ===
  stdout 原始行数: 3
  其中不是 JSON 的行: 0
    （干净：每一行都是 JSON）

=== 判定：协议正常 ===
```

**stdout 管道里 3 行全是合法 JSON，杂输出一行都没进去。** 那两行去哪了 —— `out/stray-handler.stderr.log`：

```
[stray-server] 启动：transport=stdio（handler 内含 stdout 杂输出）
!!! STRAY-VIA-PRINT !!!
!!! STRAY-VIA-OSWRITE-FD1 !!!
[stray-server] fstat(1) = os.stat_result(st_mode=4096, st_ino=0, st_dev=0, …)
[stray-server] fstat(2) = os.stat_result(st_mode=4096, st_ino=0, st_dev=0, …)
[stray-server] fd1 与 fd2 同源？ True
```

两种写法**都被兜住**（`print` 走 Python 对象、`os.write(1)` 直接写 fd）→
被换掉的是 **fd 本身**，不是 Python 层的包装。

### 4.3 实验 (a) 的真实客户端复验

上面的探针是手工客户端。再用**真正的客户端 B**连这个「脏」server，客户端一行不改：

```
  mcp_servers = {"local-tools": {"type": "stdio", "command": "D:\\Anaconda3\\envs\\aicoding\\python.exe",
                 "args": ["E:\\aicoding\\mini-claude-code\\servers\\experiments\\_stray_in_handler_server.py"]}}

[tool_use]  mcp__local-tools__word_count({"text": "hello world foo"})
[tool_result] {"result":"「hello world foo」→ 字符数 15，词数 3"}
[tool_use]  mcp__local-tools__read_study_file({"relpath": "../../Windows/win.ini"})
[tool_result] {"result":"拒绝：../../Windows/win.ini 越出 E:\\<私有目录>（本工具只允许读该目录之内）"}

  subtype = success   is_error = False   num_turns = 4   调用次数 = 3
=== 判定：两个工具都被真实调用，且运行无错 ===
```

**handler 往 stdout 写了两行东西，真实客户端完全无感。**

### 4.4 实验 (b)：`conda run` 该不该用

我原来的推理是「stdio 下中间多一层进程会增加协议被干扰的面」。**实测把这个推理证伪了**：

| 启动方式 | server 启动了吗 | stdout 管道内容 | 握手 |
|---|---|---|---|
| 直连 `python.exe` | 是 | 5 行，全 JSON | ✅ |
| `conda run --no-capture-output` | 是 | **5 行，全 JSON（与直连逐字相同）** | ✅ |
| `conda run`（**不加** `--no-capture-output`） | 是（stderr 有启动横幅） | **0 字节** | ❌ EOF |

```
$ python probe_client.py --protocol legacy out/conda-nocap -- conda.exe run -n aicoding python server.py
  [EOF] initialize: server 的 stdout 提前关闭
  initialize 未成功，跳过后续请求
  server 退出码: 0
  stdout 原始行数: 0
    （管道里一个字节都没有 —— 这本身就是异常信号）
=== 判定：协议异常 ===
```

三档 `diff` 下来，直连与 `conda run --no-capture-output` 的 JSON **逐字相同** ——
问题不在「多一层进程」。

那 `conda run` 默认模式到底做了什么？**实测下来是两件事，不是一件。**

#### (b-1) 输出被缓冲到子进程退出，才一次性吐出

计时探针 `_stream_timing_probe.py`：子进程 t=0 打一行、睡 6 秒、t=6 再打一行，
**两个 `print` 都带 `flush=True`**（刻意的单变量设计 —— 不 flush 的话，
「管道 0 字节」就分不清是 conda 缓冲还是 Python 自己缓冲）。

它在**子进程还活着**的 t=3s 去看管道：

```
[direct] 运行中 t=3s：stdout 已收到 16 字节
[direct] 进程退出后：stdout 共收到 32 字节
[direct]   OUT @ t= 0.03s  b'LINE-ONE-AT-T0\r\n'

[conda-nocapflag] 运行中 t=3s：stdout 已收到 16 字节
[conda-nocapflag] 进程退出后：stdout 共收到 32 字节
[conda-nocapflag]   OUT @ t= 1.88s  b'LINE-ONE-AT-T0\r\n'

[conda-default] 运行中 t=3s：stdout 已收到 0 字节
[conda-default] 进程退出后：stdout 共收到 34 字节
[conda-default]   OUT @ t= 7.84s  b'LINE-ONE-AT-T0\r\n'
[conda-default]   OUT @ t= 7.84s  b'LINE-TWO-AT-T6\r\n'
[conda-default] 判定：输出被『缓冲到退出』—— 运行中拿不到，退出时一次性到达
```

默认模式下两行**都在 t=7.84s（子进程退出那一刻）同时到达**。
**输出一直都在，不是被丢掉，只是运行期间拿不到。**

#### (b-2) 子进程的 stdin 是 EOF —— 这条才是 MCP 场景的致命伤

同一套三档对照，换成子进程读一行 stdin：

```
### 档 1：直连 python.exe（对照基线）###
child: readline 返回 'PING\n'
GOT-LINE: PING

### 档 2：conda run --no-capture-output ###
child: readline 返回 'PING\n'
GOT-LINE: PING

### 档 3：conda run（默认）###
child: readline 返回 ''
GOT-LINE:
```

默认模式下 `readline` 返回 `''` —— **子进程根本没收到 stdin**。

这一条把 MCP 场景里那三个看起来互相打架的观察，一次解释清楚：

| 观察 | 解释 |
|---|---|
| stdout 是 **0 字节** | stdin 立即 EOF → server 立刻退出 → **从未写过 stdout**。不是「写了但被吞掉」 |
| 退出码是 **0** | 是正常退出。stdin EOF 对命令行程序本来就是「收工」信号，不是崩溃 |
| stderr 却**有**启动横幅 | 那是进程退出后，conda 把缓冲吐出来的时候一起给的（即 (b-1)） |

> 前两行是根据 (b-2) 的直接观测做的**推断**，我没有逐帧记录 server 的退出时刻；
> (b-2) 本身是直接实测的。

**MCP server 是长驻 + 双向通信的进程，(b-1) 和 (b-2) 任意一条都足以让它跑不起来。**
所以结论没变（**stdio server 不要用裸 `conda run`**），但理由要写全两条，
而不是笼统地说「conda 把输出收走了」—— 那是把 (b-1) 的现象错当成了 (b-2) 的原因。

### 4.5 那规则到底该怎么写

| 时机 | print() 的去向 | 会不会砸协议 |
|---|---|---|
| transport 启动**前**（模块顶层、import 期） | 真 stdout（协议线） | **会** ← 这才是真正该守的线 |
| transport 启动**后**（handler 内） | fd 1 已被指向 stderr | 不会（实测见 4.2 / 4.3） |
| 抢占失败回退（`dup2` 抛 OSError 时走 `stream.buffer` 就地服务） | 真 stdout | 会 |

**本项目仍然所有日志走 stderr** —— 因为第一行那个窗口期真实存在，且源码里明确写了抢占是
*Best-effort*（失败会回退到 v1 的就地服务方式）。规则不变，但**理由是精确的**，
不是「网上说不能 print」。

---

## 五、附带发现

### 1. `FastMCP` 已改名 —— 别照抄老教程

本机 `mcp` 2.2.0：

```
❌ from mcp.server.fastmcp import FastMCP      → 报错，有迁移提示
✅ from mcp.server.mcpserver import MCPServer
```

实测签名（`mcp/server/mcpserver/server.py:158`）：

```python
MCPServer(name=..., version="", *, tools=None, debug=False, log_level="INFO", ...)
MCPServer.tool(name=None, title=None, description=None, ...)   # 必须带括号：@server.tool()
MCPServer.run(transport: Literal["stdio","sse","streamable-http"] = "stdio")  # → anyio.run(run_stdio_async)
```

### 2. 库自身的日志走 stderr，不污染协议

`mcpserver/utilities/logging.py` 里 `configure_logging()` 用的是
`RichHandler(console=Console(stderr=True))` —— 安全。

### 3. ⚠️ **但 server 的 stderr 默认看不到**

用客户端 B 连「脏」server 时，日志里**搜不到** `STRAY` 字样，`[cli stderr]` 只有 CLI 自己的一行。
**Claude Code 把 MCP server 的 stderr 吞掉了。**

含义：「日志写 stderr」能保证**不砸协议**，但**不保证你能看见**。
第二阶段调 Agent 时，server 侧要自己落盘日志文件，别指望 stderr 能帮你 debug。

### 4. `.mcp.json` 里的路径能怎么写：三种写法实测

全部在 **cwd = `E:\aicoding\mini-claude-code`**（`.mcp.json` 也在该目录）下测得：

| 写法 | 结果 | 备注 |
|---|---|---|
| 绝对路径 `["E:\\…\\local_tools_server.py"]` | ✅ `FOUND` | 无歧义，但机器相关 |
| 裸相对路径 `["servers/local_tools_server.py"]` | ✅ `FOUND` | **基准是 cwd** |
| `["${CLAUDE_PROJECT_DIR:-.}/servers/local_tools_server.py"]` | ✅ `FOUND` | 与裸相对路径**完全等价** |

**（a）`${VAR}` 在 `command` 和 `args` 里都会展开。** 网上有一份第三方指南说
「`${VAR}` 只在 `env` 块有效、`args` 里不行」——**实测推翻了它**（Claude Code 2.1.272）：

```bash
# 配置：{"command": "${MCP_TEST_PYTHON}", "args": ["${MCP_TEST_SERVER}"]}
# 设了环境变量 → FOUND    不设 → NOT_LOADED      ← 一对能失败的对照
```

**（b）相对路径也可用，但只在「cwd = 项目根」时成立。** cwd 换成子目录会 `NOT_LOADED` ——
**不是配置没加载**（绝对路径那一档在同一 cwd 下是 `FOUND`），而是相对路径的解析基准就是 cwd。
判别过程见第六节第 0 条。

**（c）`${CLAUDE_PROJECT_DIR}` 这个变量在本机没有被设置**，所以第三行里
`:-` 回退到了 `.`（cwd），与第二行**等价**——两个写法一起在子目录失效，也一起在项目根生效。
**别以为写成变量就比相对路径更稳。**

> ⚠️ 这三行最初全部测成了 `NOT_LOADED`，是**我手写 shell 命令时转义不可靠**导致的
> ——写 JSON 时反斜杠少了一层，`command` 字段损坏。**不是 heredoc 的锅。**
> 详见第六节第 3 条 —— 那条值得单独看，因为它同时解释了一个我编错的「缓存假说」。

### 5. 自动生成的 outputSchema

从原始 stdout 里能看到，返回类型注解是 `str` 时 `MCPServer` 会自动补 `outputSchema`，
并把返回值包成 `structuredContent: {"result": ...}`：

```json
{"jsonrpc":"2.0","id":3,"result":{"content":[{"text":"「hello world foo」→ 字符数 15，词数 3","type":"text"}],
 "isError":false,"structuredContent":{"result":"「hello world foo」→ 字符数 15，词数 3"}}}
```

对比一下进程内版本（`create_sdk_mcp_server`）返回的是手写 dict `{"content":[{"type":"text","text":...}]}` ——
**独立进程这条路把 schema 生成、参数校验都自动化了**。

### 6. 模型自己指出的一个真实缺口

客户端 B 跑完时，模型主动指出：`read_study_file` **没有分页/截断参数**，
读大文件会一次性塞进上下文，与「只在必要时注入文件内容」的上下文管理目标冲突。

—— 它没看到的是，工具内部有 20000 字符的硬上限（对该文件没触发）。
但这个观察本身成立：**截断写死在实现里，调用方看不见**，应该做成参数。
已记入「下一步」。

---

## 六、本记录里那些测试的有效性前提

这一节列的是**会让测试静默给出假结果**的前提 —— 不报错，只是答案不对。
任何人拿本记录的方法去复现或做别的验证，都先看这四条。**第 0 条和第 3 条是我自己踩过的。**

### 0. cwd 决定 `.mcp.json` 在不在作用域 —— 没有 cwd 的结果不算证据

`.mcp.json` 是**项目作用域**配置，所以 cwd 是**实验条件的一部分**，必须和被测量一起记录。

**本记录的第一版就栽在这里**：两种 cwd 的结果被并列在同一张表里
（私有工作区目录与项目目录各一种），读者无法分辨哪一条来自 `.mcp.json`、
哪一条来自 local 作用域注册（早先 `claude mcp add` 留下的）。
**结论因此全部挂起重跑。**

重跑前先清场：`claude mcp remove local-tools --scope local`，
确认 `~/.claude.json` 的 `projects` 下该私有目录的 `mcpServers` 为空。
之后所有 MCP 实验**一律在项目目录下跑**。

#### 受控重跑结果（cwd 已固定并记录）

| cwd | `.mcp.json` 在哪 | `claude mcp list` | headless 能用吗 |
|---|---|---|---|
| 仓库根 | 仓库根 | 看得到（⏸ Pending approval） | **能**（`FOUND`） |
| 仓库根 | 不存在 | `No MCP servers configured` | 不能（`NOT_LOADED`） |
| 子目录 `servers/` | 仓库根 | **看得到**（会向上找） | 能加载，但**相对路径会失效**（见下） |

一个反直觉点：cwd 在子目录时 `claude mcp list` **仍然显示仓库根那份 `.mcp.json`**（它向上查找），
但 **`args` 里的相对路径是按 cwd 解析的**，不是按 `.mcp.json` 所在目录。同一 cwd 下的判别档：

| cwd = `servers/`，args 写成 | 结果 |
|---|---|
| `["servers/local_tools_server.py"]` | `NOT_LOADED`（解析成 `servers/servers/…`） |
| `["E:\…\servers\local_tools_server.py"]`（绝对路径） | **`FOUND`** ← 证明配置**确实被加载了**，失败的是路径解析 |

**所以：相对路径可用，但只在「cwd = 项目根」时成立。**

#### 本记录里每次运行用的 cwd

| 实验 | cwd | 配置来源 |
|---|---|---|
| 客户端 A 首次注册与调用（**已作废**） | `E:\<私有目录>` | local 作用域注册 |
| 客户端 B（`hello_agent_stdio.py`） | 任意 | 代码里写死的显式 stdio 配置，**不经 cwd** |
| 3.2 节 `FOUND`/`NOT_LOADED` 对照 | `E:\<私有目录>` | `--mcp-config` + `--strict-mcp-config`：**显式文件路径，不受 cwd 影响** |
| Q1 / Q2 / Q3 / Q4 重跑、子目录判别档 | `E:\aicoding\mini-claude-code`（子目录档为 `…\servers`） | `.mcp.json` |
| 4.2 / 4.3 探针与「脏」server | 仓库根 | 命令行显式路径 |
| 4.4 (b) 三档计时与 stdin | 仓库根 | 命令行显式路径 |

### 1. 自定义 `ANTHROPIC_BASE_URL` 关掉了 tool search —— 这是「问工具名在不在」能成立的前提

Claude Code 默认对 MCP 工具做**延迟加载**（tool search）：会话启动时只加载工具名与 server 指令，
完整 schema 按需取。但当 `ANTHROPIC_BASE_URL` 指向**非一方主机**（本机是 DeepSeek）时，
Claude Code **默认关掉 tool search**，回退到把每个 MCP 工具的 schema **完整材料化进上下文**。

第 3.2 节那组 `FOUND` / `NOT_LOADED` 对照之所以有效，**正是因为处在「全部材料化」这个状态**。

> ⚠️ **换认证方式后这个测试会失效。** 改回官方端点（或显式设 `ENABLE_TOOL_SEARCH=true`）之后，
> 工具名不再出现在初始上下文里，**好配置也会答 `NOT_LOADED`** —— 两侧同答案，实验就失去了区分力。

要复现这组对照，先确认 tool search 处于关闭状态：空会话里跑 `/context all`
（deferred 模式下会显示 `(loaded on-demand)`，且 MCP 工具占 0 token）。

### 2. `claude mcp list` 的 "Pending approval" 不代表 headless 不加载

⚠️ 这一条**与最初的预期相反**，实测如此（cwd = `E:\aicoding\mini-claude-code`，见上方 cwd 表）：

| `.mcp.json` | `claude mcp list` | headless 问 `mcp__local-tools__word_count` | 实际调用 |
|---|---|---|---|
| 在场 | **⏸ Pending approval** | **FOUND** | **成功**（`num_turns=2`，返回真实结果） |
| 移走 | `No MCP servers configured` | `NOT_LOADED` | — |

单变量：只把 `.mcp.json` 移走，其余不动、cwd 不动。此时 `~/.claude.json` 里该项目**零条目**、
顶层无 `mcpServers` 键（无 user 作用域）、`enableAllProjectMcpServers` 未设置 ——
也就是说它确实没被批准过，headless 照样加载。

```bash
# 侧 A：.mcp.json 在场（cwd = E:\aicoding\mini-claude-code）
$ claude mcp list
local-tools: … - ⏸ Pending approval (run `claude` to approve)
$ claude -p '…是否出现字符串 "mcp__local-tools__word_count"？…'
FOUND

# 侧 B：把 .mcp.json 移走（同一个 cwd）
$ claude mcp list
No MCP servers configured. Use `claude mcp add` to add a server.
$ claude -p '…同一个问法…'
NOT_LOADED
```

**含义**：`claude -p` 会加载未批准的项目级 `.mcp.json`。
所以**不能**用 `mcp list` 的 Pending 状态去推断 headless 实验里工具在不在。
交互式会话里的批准流程是另一回事，本记录**没有**覆盖。

原始输出：`servers/experiments/out/q1-mcpjson-loading.log`（两次运行都带 cwd）。

### 3. ⚠️ 手写含反斜杠的 shell 命令时，我的转义不可靠 —— 因此污染过两组实验

这条不是 Claude Code 的性质，**也不是 heredoc 的性质**，是**我自己的操作坑**。
后果严重到必须写进来。

#### 现象

写 `.mcp.json` 需要 `\\`。我用 `cat > .mcp.json <<'XEOF'` 写，文件里却变成单个 `\`：

```
想写进去： "command": "D:\\Anaconda3\\envs\\aicoding\\python.exe"
实际写入： "command": "D:\Anaconda3\envs\aicoding\python.exe"   ← JSON 非法
```

`\A` 不是合法 JSON 转义 → **`command` 字段本身损坏** → 进程 spawn 失败 → `NOT_LOADED`。
我把这个 `NOT_LOADED` 归因给了 `args`，得出两个**完全相反**的结论：

| 结论（污染版） | 受控重跑（JSON 正确） |
|---|---|
| `.mcp.json` 里 `${CLAUDE_PROJECT_DIR:-.}` 不可用 | **可用**（`FOUND`） |
| `.mcp.json` 里裸相对路径不可用 | **可用**（`FOUND`，基准是 cwd） |

**更糟的是**：我据此编了一个「Claude Code 对 `.mcp.json` 有发现缓存」的假说去解释 `dumper` 那次失败
（改名后连续两次 `NOT_LOADED`、进程从未被 spawn）。重跑 Q4 第 2 步——
**名字一字不动、只把路径改成有效** → 立刻变成 `FOUND`。**没有缓存。**

#### 机制：不是 heredoc，是我

第一版记录把机制写成「带引号的 heredoc 会吃掉反斜杠」。**这是错的**，
已由复核方提出、字节级实验推翻（原始输出：`servers/experiments/out/backslash-escape-channels.log`）：

```
$ cat > quoted.txt <<'XEOF'
A\\B\\C
XEOF
$ cat > unquoted.txt <<XEOF
A\\B\\C
XEOF

quoted    bytes=6   内容=A\B\C$
unquoted  bytes=6   内容=A\B\C$
```

关键在于：**`<<'XEOF'`（带引号）是完全忠实的，不做任何转义处理。**
它输出 6 字节 ⇒ **送进去的输入本身就只有一个反斜杠** ⇒ 问题出在输入侧，不在 heredoc。

换遍所有通道，结论一致 —— 只要**书写**反斜杠字面量，就会丢一层：

| 通道 | bash 实际收到 |
|---|---|
| `echo "A\\B\\C"`（双引号） | `A\B\C` —— 丢一层 |
| `echo 'A\\B\\C'`（单引号） | `A\B\C` —— 丢一层 |
| `printf '%s\n' 'A\\B\\C'` | `A\B\C` —— 丢一层 |
| `cat <<'XEOF'`（引号 heredoc） | `A\B\C` —— 丢一层 |
| **`python -c "print('A'+chr(92)*2+'B')"`** | **`A\\B\\C`** ✅ 唯一成功 |

**所以规则不是「别用 heredoc」，而是：根本不要书写反斜杠字面量。**
用 `chr(92)` 构造出来的反斜杠完整到达 —— 因为它压根没经过"我手写转义"这一步。

> 现场证据：本文件这段 log 的标题行里，我写"两个反斜杠"的地方实际显示成了一个
> —— 同一个坑在写这条记录时又触发了一次。

#### 一条独立的真规则：无引号 heredoc 会塌一层

这跟上面是**两回事**，但确实存在，单独记下（复核方实测，输入相同）：

```
quoted   (<<'XEOF')  bytes=8   内容=A\\B\\C$
unquoted (<<XEOF)    bytes=6   内容=A\B\C$
```

无引号 heredoc 会塌掉一层。**它不是我这次问题的原因**（我的输入本来就只有一层），
但它是 heredoc 自身的真规则，将来真要写 heredoc 时得知道。

#### 两条规则

- **写 JSON 一律用 `python -c "json.dump(...)"` 或 Write 工具**，
  不要手写含反斜杠的 shell 命令。
  其余实验配置（`mcp-good.json` 等）当初是用 Write 工具写的，反斜杠正确，那些结论是干净的。
- **失败归因要能区分「配置没加载」和「路径没解析对」。** 这次的判别档：
  同 cwd 下换一个**真实存在的环境变量**（`${MCP_TEST_PYTHON}`）→ `FOUND`，
  说明配置能被加载，那 `NOT_LOADED` 就只能是路径问题。

> **这是本次验证里第三次「把现象归因给了错误的机制」**：
> 前两次是 **缓存假说**（真因是 command 损坏）和 **args**（真因是 command 损坏），
> 这次是 **heredoc**（真因是我手写转义不可靠）。
> 共同点：都在**没做区分性实验**的情况下，给现象配了一个听起来合理的解释，然后拿它当结论往下走。
> 元规则已写进私有工作区的「坑与经验库」。

原始输出：`servers/experiments/out/backslash-escape-channels.log`（本次转义实验）、
`out/q2-q3-mcpjson-path-forms.log` 与 `out/q4-cache-vs-path.log`
（后两个保留了被污染的那一段，并标注了作废原因）。

---

## 七、复现方式

> ⚠️ probe 的默认模式现为 `--protocol auto`（走 SDK 自动协商，不产出 `.stdout.raw`）。
> 下面凡是要复现历史 raw / `2025-06-18` 实验的命令，都**显式**写了 `--protocol legacy`。

```bash
# ── server 自检（不接客户端，先排除自身故障）────────────────
D:/Anaconda3/envs/aicoding/python.exe servers/experiments/probe_client.py \
  --protocol legacy \
  "servers/experiments/out/direct" -- \
  "D:/Anaconda3/envs/aicoding/python.exe" "E:/aicoding/mini-claude-code/servers/local_tools_server.py"
# 预期：initialize OK / tools/list -> ['word_count','read_study_file'] / 协议正常 / 退出码 0

# ── 客户端 A（★ 必须在 E:\aicoding\mini-claude-code 下执行）────
#    cwd 决定 local 作用域落在哪个 project 键下，也决定 .mcp.json 在不在作用域。
#    先清场，避免私有工作区下那条旧注册把结果搞混：
#      cd "<私有目录>" && claude mcp remove local-tools --scope local
cd "E:/aicoding/mini-claude-code"
claude mcp add local-tools --transport stdio -- \
  "D:\Anaconda3\envs\aicoding\python.exe" "E:\aicoding\mini-claude-code\servers\local_tools_server.py"
#    注册会落在 ~/.claude.json 的 projects["E:/aicoding/mini-claude-code"] 下
claude mcp list                      # 预期：✔ Connected
claude -p "请调用 word_count 工具，参数 text 传 'hello world foo'，然后把工具返回的结果原样告诉我。" \
  --allowedTools "mcp__local-tools__word_count"

# ── 客户端 B ────────────────────────────────────────────
D:/Anaconda3/Scripts/conda.exe run --no-capture-output -n aicoding python hello_agent_stdio.py
# 预期：3 次 tool_use、退出码 0、「越界拦截：命中」

# ── 反向对照（三组，都必须"能失败"）──────────────────────
#   1) 坏启动路径
claude mcp add local-tools-BAD --transport stdio -- \
  "D:\Anaconda3\envs\aicoding\python.exe" "E:\aicoding\mini-claude-code\servers\__does_not_exist__.py"
claude mcp list                      # 预期：✘ Failed to connect
claude mcp remove local-tools-BAD -s local

#   2) 工具是否真的从可用列表消失（用可判定问法）
claude -p '不要使用任何工具。只依据启动时已加载的上下文回答：其中是否出现字符串 "mcp__bad__word_count"？出现就逐字引用那一行，否则只输出 NOT_LOADED。' \
  --mcp-config "E:\aicoding\mini-claude-code\servers\experiments\out\mcp-bad.json" --strict-mcp-config
#   预期：NOT_LOADED   （把 bad 换成 good 再问 mcp__good__word_count，预期：FOUND）

#   3) conda run 的对照（必须能失败）
D:/Anaconda3/envs/aicoding/python.exe servers/experiments/probe_client.py --protocol legacy "servers/experiments/out/conda" -- \
  "D:/Anaconda3/Scripts/conda.exe" run --no-capture-output -n aicoding python "E:/aicoding/mini-claude-code/servers/local_tools_server.py"
#   预期：协议正常（5 行 JSON）
D:/Anaconda3/envs/aicoding/python.exe servers/experiments/probe_client.py --protocol legacy "servers/experiments/out/conda-nocap" -- \
  "D:/Anaconda3/Scripts/conda.exe" run -n aicoding python "E:/aicoding/mini-claude-code/servers/local_tools_server.py"
#   预期：协议异常（0 字节）

# ── 实验 (a)：handler 内 print ───────────────────────────
D:/Anaconda3/envs/aicoding/python.exe servers/experiments/probe_client.py --protocol legacy "servers/experiments/out/stray-handler" -- \
  "D:/Anaconda3/envs/aicoding/python.exe" "E:/aicoding/mini-claude-code/servers/experiments/_stray_in_handler_server.py"
#   预期：stdout 3 行全 JSON、STRAY 出现在 .stderr.log
MCP_SERVER_SCRIPT="E:\aicoding\mini-claude-code\servers\experiments\_stray_in_handler_server.py" \
  D:/Anaconda3/Scripts/conda.exe run --no-capture-output -n aicoding hello_agent_stdio.py
#   预期：真实客户端调用全部正常

# ── Q1~Q4：.mcp.json 的受控实验（★ 全部在仓库根，每次先打 cwd）────
#   通用要点：
#     · 写 .mcp.json **一律用 python 的 json.dump**，不要用 heredoc（见第六节第 3 条）
#     · 每次改完先 `cat .mcp.json` 确认反斜杠是 `\\` 而不是 `\`
#     · 测完把 .mcp.json 还原

cd "E:/aicoding/mini-claude-code" && pwd

# Q1：未批准的 .mcp.json，headless 会不会加载
claude mcp list                                    # 预期：⏸ Pending approval
claude -p '只依据启动时已加载的上下文回答，只输出一行：上下文里是否出现字符串 "mcp__local-tools__word_count"？出现输出 FOUND，否则输出 NOT_LOADED。'
#   预期：FOUND
mv .mcp.json .mcp.json.bak
claude mcp list                                    # 预期：No MCP servers configured
claude -p '……同一个问法……'                          # 预期：NOT_LOADED
mv .mcp.json.bak .mcp.json

# Q2/Q3：三种路径写法（同一 cwd，逐个换 .mcp.json 再还原）
D:/Anaconda3/envs/aicoding/python.exe -c "import json,pathlib; pathlib.Path('.mcp.json').write_text(json.dumps({'mcpServers':{'projdir':{'type':'stdio','command':r'D:\Anaconda3\envs\aicoding\python.exe','args':['\${CLAUDE_PROJECT_DIR:-.}/servers/local_tools_server.py']}}},indent=2),encoding='utf-8')"
claude -p '……"mcp__projdir__word_count"……'          # 预期：FOUND
#   裸相对路径同理（"args": ["servers/local_tools_server.py"]），预期：FOUND
#   绝对路径同理，预期：FOUND

# Q3 判别档：同 cwd 换成「真实存在的环境变量」——用来区分「配置没加载」和「路径没解析对」
export MCP_TEST_PYTHON="D:\Anaconda3\envs\aicoding\python.exe"
export MCP_TEST_SERVER="E:\aicoding\mini-claude-code\servers\local_tools_server.py"
#   配置写成 {"command": "${MCP_TEST_PYTHON}", "args": ["${MCP_TEST_SERVER}"]}
#   预期：FOUND  → 说明配置能被加载；那 Q2/Q3 若 NOT_LOADED 就只能是路径问题

# Q4：区分「缓存」和「路径错误」
#   第 1 步：同一个 .mcp.json 放 good(有效) + bad(坏路径)，一次提问同时问两个名字
#            预期：good FOUND / bad NOT_LOADED
#   第 2 步：**保持 bad 这个名字不变**，只把它的 args 改成有效路径，再问
#            预期：FOUND  → 之前是路径问题，不是缓存
#    （实测结果就是 FOUND。这一步同时推翻了我先前编的「发现缓存」假说。）

# 子目录作用域：cwd = servers/，.mcp.json 仍在仓库根
cd "E:/aicoding/mini-claude-code/servers"
claude mcp list                                    # 预期：仍看得到（会向上找）
#   相对路径 args → NOT_LOADED（解析成 servers/servers/…）
#   绝对路径 args → FOUND（证明配置确实被加载，失败的是路径解析）
```

### 关于「可复现」：锚定版本，不靠冻结文件

本文件描述的是**某一次运行**。要复现，用 commit 锚点取回那一版代码：

```bash
git log --oneline -- servers/local_tools_server.py    # 找到对应提交
git checkout <hash> -- servers/local_tools_server.py  # 取回那一版
git checkout HEAD -- servers/local_tools_server.py    # 还原
```

**不要**用「约定这个文件不许改」来保证可复现 —— 那样每验证一次就多一个不能动的文件。
本文件已把 `local_tools_server.py` 的 SHA256 写在开头，可用来确认取回的版本是否正确。

### 落盘证据清单

| 文件 | 内容 |
|---|---|
| `servers/experiments/out/client-a-claude-code.json` | 客户端 A 的完整 JSON 输出 |
| `servers/experiments/out/client-a-claude-code.stderr.log` | 客户端 A 的 stderr |
| `servers/experiments/out/direct.{stdout.raw,stderr.log,result.json}` | 探针 / 直连 python.exe |
| `servers/experiments/out/conda.{stdout.raw,stderr.log,result.json}` | 探针 / conda run --no-capture-output |
| `servers/experiments/out/conda-nocap.{stdout.raw,stderr.log,result.json}` | 探针 / conda run（**无** --no-capture-output） |
| `servers/experiments/out/stray-handler.{stdout.raw,stderr.log,result.json}` | 实验 (a) 探针 |
| `servers/experiments/out/stray-handler-clientB.log` | 实验 (a) 真实客户端完整日志 |
| `servers/experiments/out/mcp-{good,bad,expand}.json` | 反向对照用的临时配置 |
| `servers/experiments/out/timing-three-ways.log` | 4.4 (b-1)：三档启动方式下输出**何时**到达管道 |
| `servers/experiments/out/stdin-forwarding.log` | 4.4 (b-2)：三档启动方式下子进程的 `readline` 收到了什么 |
| `servers/experiments/out/q1-mcpjson-loading.log` | Q1：`.mcp.json` 在场 / 移走 两侧（**每次运行都带 cwd**） |
| `servers/experiments/out/q2-q3-mcpjson-path-forms.log` | Q2/Q3：三种路径写法 + 判别档 + 子目录档（含被污染段，已标注作废原因） |
| `servers/experiments/out/q4-cache-vs-path.log` | Q4：缓存 vs 路径（含被污染段，已标注） |
| `servers/experiments/out/backslash-escape-channels.log` | 第六节第 3 条：反斜杠在五种通道下的实际去向（H3 的判别实验） |
| `servers/experiments/out/mcp-projdir.json`、`mcp-novar.json` | `--mcp-config` 场景下的路径写法对照配置 |

逐步说明见 [`servers/experiments/README.md`](servers/experiments/README.md)。
