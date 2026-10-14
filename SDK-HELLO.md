# sdk-hello — Claude Agent SDK 最小验证

用 **Claude Agent SDK** 跑通一次最小闭环：**一次提问 + 一个自定义 tool**，模型走第三方端点（DeepSeek）。

这是在动手做 mini Coding Agent 之前的第一个实验，目的是先确认 SDK 在本机、
在本套第三方端点配置下**到底能不能跑、按什么规则跑**。

## 跑出来的结论

1. ✅ 闭环成立：提问 → 模型决定调用 tool → 本地 tool 执行 → 模型拿到结果并回答
2. ✅ 自定义 tool 确实被执行（走进程内 MCP server，不是服务端 connector）
3. ⚠️ **实测出一条文档没写清的优先级规则**，见下方「最重要的一条」

## 最重要的一条：端点配置的优先级

实测优先级（`VERIFY.md` 有完整的四格隔离实验）：

```
~/.claude/settings.json 的 env 块   ＞   ClaudeAgentOptions.env   ＞   进程继承环境
```

含义：**只要 `setting_sources` 含 `"user"`，`settings.json` 就会静默覆盖你在 Python 里传的 `env`，且不报错。**

两个反直觉点：

- `setting_sources=["user"]` → 覆盖 `options.env`
- **省略 `setting_sources` 同样会覆盖** —— 按文档默认等于 `["user","project","local"]`

必须**显式写 `setting_sources=[]`** 才能让 `options.env` 真正生效。本仓库的 `hello_agent.py` 就是这么写的，
并在代码注释里说明了原因——**不要随手改回 `["user"]`**，那会让端点来源变得无法验证。

## 环境

| 项 | 值 |
|---|---|
| conda 环境 | `aicoding`（Python 3.12.14） |
| 包 | `claude-agent-sdk==0.2.152` |
| CLI | `E:\node.js\24.21.0\node_modules\@anthropic-ai\claude-code\bin\claude.exe`（2.1.272） |

创建环境与安装：

```bash
D:\Anaconda3\Scripts\conda.exe create -n aicoding python=3.12 -y
D:\Anaconda3\Scripts\conda.exe run -n aicoding python -m pip install -r requirements.txt
```

注意：`conda` 不在 PATH 上（非交互式 shell 里看不到是正常的），必须用上面的完整路径。

## 运行

```bash
# 正向（应成功，退出码 0）
D:\Anaconda3\Scripts\conda.exe run --no-capture-output -n aicoding python hello_agent.py

# 反向对照（应失败，退出码 1）—— 用来证明端点配置真的在起作用
#   注意：Linux/macOS 的 bash 下把 set 语法换成 HELLO_AGENT_BASE_URL=... 前缀即可
set HELLO_AGENT_BASE_URL=https://api.deepseek.com/anthropic-WRONG-PATH
D:\Anaconda3\Scripts\conda.exe run --no-capture-output -n aicoding python hello_agent.py
```

`--no-capture-output` 是必需的：SDK 通过子进程 + stdio 与 CLI 通信，`conda run` 默认捕获输出会干扰。

## 密钥处理与用量归因

### 密钥不进代码、不进仓库

打印时对 `ANTHROPIC_AUTH_TOKEN` 只显示字符数：

```
ANTHROPIC_AUTH_TOKEN = <35 字符，已隐藏>
```

### 两个 key：交互式会话 vs 实验脚本

**问题**：Claude Code 交互会话和实验脚本共用一个 key，而第三方端点的账单**只有总额**，
给不出「这次实验花了多少」的粒度 —— 第三阶段做 SWE-bench 消融时要按 run 对比成本，这个粒度为 0。

**做法**：实验脚本优先读项目根 `.env` 里的**实验专用 key**：

| 情况 | token 用哪个 | 输出里的 `key_source` |
|---|---|---|
| `.env` 存在且含凭据 | `.env` 的（`BASE_URL` / `MODEL` 仍从 `settings.json` 取） | `env:.env` |
| 否则 | `~/.claude/settings.json` | `fallback:~/.claude/settings.json` |

配置只要两步：

```bash
cp .env.example .env
# 编辑 .env，填一行 ANTHROPIC_AUTH_TOKEN=<实验专用 key>
```

> ⚠️ **必须显式注入，不能靠进程环境。** 实测 `settings.json` 的 env 块会被注入进程环境并被继承
> （见 [`VERIFY.md`](VERIFY.md) 第四节）—— 脚本若不显式把 key 放进 `ClaudeAgentOptions.env`，
> 就没法确定某次实验到底用了哪个 key。所以两条路都在脚本里**显式**设置，并把来源打印出来。
>
> 做对照实验时可以用 `DOTENV_PATH=<别的文件>` 换掉 `.env` 的位置，不必动真正的 `.env`。
> 注意它只换**文件位置**，不是 key 的第二个来源。

### 用量日志：`servers/experiments/out/usage.jsonl`

脚本自己记用量，**不依赖提供商**。每次运行追加一行：

```jsonc
{"ts": "2026-09-16T15:27:33+08:00", "run": "keyroute-fallback", "script": "hello_agent.py",
 "key_source": "fallback:~/.claude/settings.json（.env 存在但没有凭据）",
 "model": "deepseek-flash[1m]", "input": 16970, "output": 74, "cache_read": 16640,
 "num_turns": 2, "is_error": false, "total_cost_usd": 0.095,
 "models": { "deepseek-flash[1m]": { "input": 16970, "output": 74, ... } }}
```

`run` 来自环境变量 `RUN_LABEL`（如 `RUN_LABEL=q1-A`），不传写 `default`。
平铺的 `input`/`output`/`cache_read` 是各模型**合计**，`models` 里是逐模型明细
（一次运行可能有多个模型，比如 `generate_session_title`）。

**失败的运行也会记一行**，token 字段为 `null` —— 「能失败」的对照正是靠它留证：

```jsonc
{"run": "keyroute-badkey", "key_source": "env:_bad_key_test.env",
 "model": null, "input": null, ..., "aborted": true, "error": "ResultError: … 401 …"}
```

注意是 `null` 而**不是** `0`：**「没拿到数据」和「花了 0 元」必须能区分。**

## 为什么要有反向对照

正向跑通**证明不了**任何事——它可能碰巧命中了别的配置路径（这个实验里就真的发生了）。
只有把端点故意改错、确认它**确实失败**，才能证明这个变量是真正生效的。

反向对照的报错文案有误导性，别被带偏：

```
There's an issue with the selected model (deepseek-flash[1m]).
It may not exist or you may not have access to it.
```

**它说的是模型问题，实际原因是 URL 错了。**

## 文件

| 文件 | 说明 |
|---|---|
| `hello_agent.py` | 主脚本：一次提问 + 一个自定义 tool（`word_count`），走**进程内** MCP server |
| `hello_agent_stdio.py` | 同上，但连的是**独立进程 + stdio** 的 MCP server |
| `servers/local_tools_server.py` | 独立进程 MCP server（stdio），暴露 `word_count` + `read_study_file` |
| `servers/experiments/` | 实验脚本与落盘证据（探针、stdout 实验、反向对照配置） |
| `requirements.txt` | 依赖，固定版本 |
| `VERIFY.md` | 昨天的验证：**原始输出与诊断过程**，含四格隔离实验 |
| `VERIFY-mcp.md` | 今天的验证：**独立进程 stdio + 跨两个客户端**的完整原始输出 |
| `.mcp.json.example` | MCP 配置**模板** —— 复制成 `.mcp.json` 并改一处路径（见下）。**本体不进仓库** |
| `.env.example` | 实验专用 key 模板 —— 复制成 `.env` 并填一行。**本体不进仓库** |
| `servers/experiments/out/usage.jsonl` | **每次运行的用量**（一行一次），含 `key_source` 归因 |
| `.gitignore` | 排除缓存、`.env`、`.mcp.json` |

---

## MCP：进程内 vs 独立进程

同一个工具，两条路。**左边是昨天，右边是今天，两边跑的是同一个 `word_count`。**

| 维度 | 进程内 `create_sdk_mcp_server()` | 独立进程 + stdio |
|---|---|---|
| server 长什么样 | 一个 **Python 对象**，活在客户端进程里 | 一个**进程**，在 stdin/stdout 上说 JSON-RPC |
| 传给客户端的东西 | 对象引用 `{"localtools": server}` | 一段**启动描述** `{"command": …, "args": […]}` |
| 谁能用它 | 只有这个 Python 进程 | **任何会说 MCP 的客户端** —— 今天是 Claude Code + Agent SDK |
| 语言 | 必须 Python | 任意（协议不关心实现语言） |
| 工具的 schema | **手写** `{"text": str}` + 手拼返回 dict | 从**函数签名与 docstring 自动生成**（连 `outputSchema` 都有） |
| 生命周期 | 跟着客户端进程生死 | 独立，可单独起停、单独调试 |
| 调试 | 打断点就在眼前 | 进程边界，得靠落盘日志（⚠️ 见下） |
| 崩溃影响 | 直接拖垮客户端 | 客户端能感知失败并重连 |
| 隔离 | 无 —— 同进程、同权限 | 天然进程边界，可配 `env`、单独限权 |

### 为什么这条路值得走

**裸 Function Calling 的绑定是「函数 ↔ 调用方」。MCP 把绑定改成了「进程 ↔ 协议」。**
所以今天这件事才成立：

> 同一份 `servers/local_tools_server.py`（SHA256 `fc0cdbc8…5261`，全程未改），
> 被 **Claude Code**（一个 CLI 应用）和 **`hello_agent_stdio.py`**（一个 Python 脚本）分别使用，
> 两者没有共享任何代码，返回的是同一个字符串。

完整原始输出（含三组反向对照）见 **[`VERIFY-mcp.md`](VERIFY-mcp.md)**。

### 两条实测得来的注意事项

1. **`conda run` 不能直接用。** 实测下来它默认模式做了两件事，**任意一件都足以让 MCP server 跑不起来**：

   | | 现象 | 后果 |
   |---|---|---|
   | (b-1) | 子进程输出**缓冲到子进程退出**才一次性吐出 | 跑着的进程看不到任何输出 |
   | (b-2) | **子进程的 stdin 是 EOF**（根本没转发进去） | 长驻进程立刻「收工」退出 |

   (b-2) 才是 MCP 场景的致命伤：server 读 stdin 立即 EOF → 正常退出（退出码 0）→
   **从未写过 stdout**（所以管道是 0 字节，不是"写了被吞"）。

   必须加 `--no-capture-output`，或者**直接调环境里的 `python.exe`**。
   注意这跟「多一层进程有风险」是两回事 —— 加上 `--no-capture-output` 后，两条路的输出**逐字相同**。
   原始输出见 [`VERIFY-mcp.md`](VERIFY-mcp.md) 第 4.4 节。

2. **server 的 stderr，Claude Code 默认看不到。** 日志写 stderr 能保证不砸协议，
   但不保证你能看见。Agent 调不动的时候，别指望 stderr，要自己落盘日志文件。

### 怎么把它接到 Claude Code

`.mcp.json` 是**项目作用域**配置（Claude Code 在项目根启动时读取）。
**本仓库不提交它** —— 它的 `command` 必然是某台机器上的绝对路径，
而机器相关的路径不该固化进 git 历史。提交的是模板：

```bash
cp .mcp.json.example .mcp.json
# 然后把 command 改成你机器上的 Python 解释器绝对路径
```

```jsonc
{
  "mcpServers": {
    "local-tools": {
      "type": "stdio",
      "command": "D:\\Anaconda3\\envs\\aicoding\\python.exe",  // ← 只改这一处
      "args": ["servers/local_tools_server.py"]
    }
  }
}
```

**两条实测约束**（原始输出见 [`VERIFY-mcp.md`](VERIFY-mcp.md) 第五节第 4 条、第六节第 0 条）：

1. `args` 里相对路径的**基准是 cwd**，所以**必须在项目根目录启动 Claude Code**。
   在子目录启动会解析成 `servers/servers/…` 而连不上 —— 注意此时 `claude mcp list` **仍会显示**这份配置
   （它向上查找），所以"看得到"不等于"能用"。
   实测 `${CLAUDE_PROJECT_DIR:-.}` 与裸相对路径**完全等价**（该变量没被设置，`:-` 回退到 cwd），
   写它并不会更稳。
2. 首次使用时 `claude mcp list` 会显示 `⏸ Pending approval`。
   **但 headless（`claude -p`）照样会加载它** —— 别用这个状态判断"到底连上没有"。

> 将来若要脱离 conda（让陌生人不必装 conda 环境），把 `command` 指向项目 venv
> 的 `python.exe` 即可，配置结构不用变。

### 关于「不能用 print」

`README` 这里只留结论，推导过程与原始输出在 `VERIFY-mcp.md` 第四节：

- **transport 启动之前**（模块顶层、import 期）往 stdout 写 → **真的会砸协议**，这条线要守
- **transport 启动之后**（tool handler 内）→ fd 1 已被 mcp 2.x 抢占并指向 stderr，杂输出够不到协议线
- 本项目仍然**所有日志走 stderr** —— 因为上面那个窗口期真实存在，且源码写明抢占是 *best-effort*
