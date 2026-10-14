# 验证记录

Claude Agent SDK 最小验证的原始输出与诊断过程。

- **日期**：2026-09-15
- **环境**：conda 环境 `aicoding`，Python 3.12.14，`claude-agent-sdk` 0.2.152
- **CLI**：`E:\node.js\24.21.0\node_modules\@anthropic-ai\claude-code\bin\claude.exe`（2.1.272）
- **端点**：`https://api.deepseek.com/anthropic`，模型 `deepseek-flash[1m]`

---

## 结论摘要

1. **SDK 跑通第三方端点**：一次提问 + 一个自定义 MCP tool，闭环成立。
2. **自定义 tool 确实被调用**：`mcp__localtools__word_count` 实际执行并返回结果。
3. **端点配置生效已验证**：正向成功、反向失败，两次只差一个环境变量。
4. **实测出三档优先级**（本次最重要的发现，见下文「隔离诊断」）：
   `settings.json` 的 env 块 **>** `ClaudeAgentOptions.env` **>** 进程继承环境
5. **继承链已用环境变量 diff 证实**：`conda run` 只新增 13 个 `CONDA_*` 变量、删除 0 个、原样透传 64 个
   ——它是「叠加层」不是「替换层」，**不能隔离环境变量**（见第四节）。

---

## 一、正向对照（应成功）

```bash
cd E:/aicoding/labs/sdk-hello
D:/Anaconda3/Scripts/conda.exe run --no-capture-output -n aicoding python hello_agent.py
```

```
[cli stderr] [claude-code:unrecognized_model] {"model":"deepseek-flash[1m]","query_source":"sdk"}
=== 生效的端点配置（密钥已打码）===
  ANTHROPIC_BASE_URL = https://api.deepseek.com/anthropic
  ANTHROPIC_AUTH_TOKEN = <35 字符，已隐藏>
  ANTHROPIC_MODEL = deepseek-flash[1m]
  ANTHROPIC_DEFAULT_OPUS_MODEL = deepseek-flash[1m]
  ANTHROPIC_DEFAULT_SONNET_MODEL = deepseek-flash[1m]
  ANTHROPIC_DEFAULT_HAIKU_MODEL = deepseek-flash
  API_TIMEOUT_MS = 3000000
  cli_path = E:\node.js\24.21.0\node_modules\@anthropic-ai\claude-code\bin\claude.exe

=== 提问 ===
请调用 word_count 工具，参数 text 传 'hello world foo'，然后把工具返回的结果原样告诉我。

[tool_use]  mcp__localtools__word_count({'text': 'hello world foo'})
[tool_result] [{'type': 'text', 'text': '「hello world foo」→ 字符数 15，词数 3'}]
[assistant] 工具返回结果原样如下：

```
「hello world foo」→ 字符数 15，词数 3
```

即：字符数 15，词数 3。

=== 结果 ===
  subtype        = success
  is_error       = False
  num_turns      = 2
  duration_ms    = 1876
  session_id     = c74310d0-629d-4178-9f85-6b209fa0bc43
  total_cost_usd = 0.09646
  result         = 工具返回结果原样如下：

```
「hello world foo」→ 字符数 15，词数 3
```

即：字符数 15，词数 3。
  model_usage    = {'deepseek-flash[1m]': {'inputTokens': 17013, 'outputTokens': 123, 'cacheReadInputTokens': 16640, 'cacheCreationInputTokens': 0, 'webSearchRequests': 0, 'costUSD': 0.09646, 'contextWindow': 1000000, 'maxOutputTokens': 32000, 'thinkingTokens': 0, 'canonicalModel': 'deepseek-flash[1m]', 'provider': 'firstParty', 'costBasis': 'unknown'}}
  AssistantMessage.model 出现过的值 = ['deepseek-flash']
退出码: 0
```

---

## 二、反向对照（应失败）—— 这是唯一能证明端点生效的实验

只把 `ANTHROPIC_BASE_URL` 换成同一主机下的错误路径，其余完全不变：

```bash
HELLO_AGENT_BASE_URL="https://api.deepseek.com/anthropic-WRONG-PATH" \
  D:/Anaconda3/Scripts/conda.exe run --no-capture-output -n aicoding python hello_agent.py
```

```
[cli stderr] [claude-code:unrecognized_model] {"model":"deepseek-flash[1m]","query_source":"sdk"}
=== 生效的端点配置（密钥已打码）===
  ANTHROPIC_BASE_URL = https://api.deepseek.com/anthropic-WRONG-PATH
  ANTHROPIC_AUTH_TOKEN = <35 字符，已隐藏>
  ANTHROPIC_MODEL = deepseek-flash[1m]
  ANTHROPIC_DEFAULT_OPUS_MODEL = deepseek-flash[1m]
  ANTHROPIC_DEFAULT_SONNET_MODEL = deepseek-flash[1m]
  ANTHROPIC_DEFAULT_HAIKU_MODEL = deepseek-flash
  API_TIMEOUT_MS = 3000000
  cli_path = E:\node.js\24.21.0\node_modules\@anthropic-ai\claude-code\bin\claude.exe

=== 提问 ===
请调用 word_count 工具，参数 text 传 'hello world foo'，然后把工具返回的结果原样告诉我。

[assistant] There's an issue with the selected model (deepseek-flash[1m]). It may not exist or you may not have access to it.
Traceback (most recent call last):
  File "D:\Anaconda3\envs\aicoding\Lib\site-packages\claude_agent_sdk\_internal\query.py", line 311, in _read_messages
    async for message in self.transport.read_messages():
  File "D:\Anaconda3\envs\aicoding\Lib\site-packages\claude_agent_sdk\_internal\transport\subprocess_cli.py", line 1142, in _read_messages_impl
    raise self._exit_error
claude_agent_sdk._errors.ProcessError: Command failed with exit code 1 (exit code: 1)
Error output: Check stderr output for details

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "E:\aicoding\labs\sdk-hello\hello_agent.py", line 156, in <module>
    raise SystemExit(asyncio.run(main()))
  File "D:\Anaconda3\envs\aicoding\Lib\asyncio\runners.py", line 195, in run
    return runner.run(main)
  File "D:\Anaconda3\envs\aicoding\Lib\asyncio\runners.py", line 118, in run
    return self._loop.run_until_complete(task)
  File "D:\Anaconda3\envs\aicoding\Lib\asyncio\base_events.py", line 691, in run_until_complete
    return future.result()
  File "E:\aicoding\labs\sdk-hello\hello_agent.py", line 121, in main
    async for message in query(prompt=prompt, options=options):
  File "D:\Anaconda3\envs\aicoding\Lib\site-packages\claude_agent_sdk\query.py", line 123, in query
    async for message in client.process_query(
  File "D:\Anaconda3\envs\aicoding\Lib\site-packages\claude_agent_sdk\_internal\client.py", line 56, in process_query
    async for msg in inner:
  File "D:\Anaconda3\envs\aicoding\Lib\site-packages\claude_agent_sdk\_internal\client.py", line 189, in _process_query_inner
    async for data in query.receive_messages():
  File "D:\Anaconda3\envs\aicoding\Lib\site-packages\claude_agent_sdk\_internal\query.py", line 903, in receive_messages
    raise exc
claude_agent_sdk._errors.ResultError: Claude Code returned an error result: There's an issue with the selected model (deepseek-flash[1m]). It may not exist or you may not have access to it. (exit code: 1)
ERROR conda.cli.main_run:execute(125): `conda run python hello_agent.py` failed. (See above for error)
退出码: 1
```

**读法**：端点错 → 请求根本没到达 DeepSeek → CLI 把失败归因成「模型不存在 / 无权限」。
注意报错文案是**误导性的**——它说的是模型问题，实际原因是 URL 错了。

---

## 三、隔离诊断：三档优先级是怎么测出来的

第一次做反向对照时**它没有失败**，说明我当时传的 `env` 没生效。于是做了四格隔离实验
（脚本：临时目录 `diag_endpoint.py`，非本仓库文件），逐格只改一个变量：

| 用例 | `setting_sources` | `options.env` | 进程继承环境 | 结果 |
|---|---|---|---|---|
| A | `["user"]` | 错误 URL | 正确 URL | ✅ **成功**（意外） |
| B | `[]` | 错误 URL | 正确 URL | ❌ 失败 |
| C | `[]` | 正确 URL | 正确 URL | ✅ 成功 |
| D | `[]` | 无 | 正确 URL | ✅ **成功**（意外） |

**用例 A** —— `options.env` 明明传了错误 URL，仍然成功：

```
--- A: setting_sources=[user] + env=错误URL ---
    setting_sources=['user']
    env.ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic-WRONG-PATH
    结果: subtype=success is_error=False
```

**用例 B** —— 只把 `setting_sources` 改成 `[]`，同样的错误 URL，立刻失败：

```
--- B: setting_sources=[] + env=错误URL ---
    setting_sources=[]
    env.ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic-WRONG-PATH
    结果: 异常 ResultError: Claude Code returned an error result: There's an issue with
    the selected model (deepseek-flash[1m]). It may not exist or you may not have access
    to it. (exit code: 1)
```

**用例 C / D** —— 确认正确路径可通，并暴露凭据的真实来源：

```
--- C: setting_sources=[] + env=正确URL ---
    结果: subtype=success is_error=False

--- D: setting_sources=[] + 无 env（应失败：无凭据） ---
    env.ANTHROPIC_BASE_URL=None
    结果: subtype=success is_error=False     ← 没传凭据也成功了
```

**用例 D 是关键**：没传任何凭据也成功，说明凭据来自**进程继承的环境变量**，不是 `options.env`。

### 溯源：继承的环境变量从哪来

```bash
env | grep -iE "^(ANTHROPIC|API_TIMEOUT)"
# → ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL / ... 共 7 个都在

reg query "HKCU\Environment" | grep -i anthropic
# → (无输出)  注册表用户环境变量里没有
```

变量不在 Windows 用户环境里，所以是 **Claude Code 主进程注入的**：

> 这条推断后来用环境变量 diff 实验**完整证实**了，包括「`conda run` 只增不改」这个关键细节——见第四节。

```
~/.claude/settings.json 的 env 块
  → Claude Code 启动时注入自身进程环境
    → 子 shell 继承 → conda run 继承 → python 继承 → SDK 拉起的 claude CLI 继承
```

### 得出的优先级

**`settings.json` 的 env 块 ＞ `ClaudeAgentOptions.env` ＞ 进程继承环境**

即：只要 `setting_sources` 含 `"user"`，`settings.json` 就会**静默覆盖**你在 Python 里传的 `env`，
而且不报任何错。省略 `setting_sources` 同样危险——按文档默认等于 `["user","project","local"]`，
效果一样，必须**显式写成 `[]`** 才能真正屏蔽。

`hello_agent.py` 因此写死 `setting_sources=[]`，并在代码里注明原因。

---

## 四、继承是怎么传进来的（环境变量 diff 实验）

第三节的结论「凭据来自进程继承环境」当时只是一个推断。本节用可复现的实验把它坐实，
并回答一个容易想错的问题：**脚本是 `conda run` 起来的，继承的环境变量为什么没被 conda 清掉？**

### 实验 1：一个 conda 不认识的变量能否穿过 `conda run`

在 shell 里设 `MY_CANARY=canary-12345`（conda 完全不认识这个名字），三种方式各跑一次：

| | 未经过 conda（`py` 启动器） | 经过 `conda run -n aicoding` |
|---|---|---|
| `MY_CANARY` | `canary-12345` | **`canary-12345`**（原样保留） |
| `python` 解释器 | `C:\Users\<用户名>\AppData\Local\Programs\Python\Python312\python.exe` | `D:\Anaconda3\envs\aicoding\python.exe` |
| `CONDA_PREFIX` | 空 | `D:\Anaconda3\envs\aicoding` |

**conda 换了 python 解释器，但没有碰 `MY_CANARY`。**

### 实验 2：把两种方式的完整环境变量 dump 出来做 diff

```powershell
# dump_env.py 内容：
#   import json, os, sys
#   json.dump(dict(os.environ), open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)

$env:MY_CANARY = 'canary-12345'
py dump_env.py env_a.json
D:\Anaconda3\Scripts\conda.exe run --no-capture-output -n aicoding python dump_env.py env_b.json
# 再比对两个 json 的键值差异
```

结果：

```
不走 conda = 66 个变量
 走 conda = 79 个变量

conda 新增 13 个：
  CONDA_PREFIX / CONDA_DEFAULT_ENV / CONDA_SHLVL / CONDA_EXE / CONDA_ROOT /
  CONDA_PROMPT_MODIFIER / CONDA_PYTHON_EXE / CONDA_ALLOW_SOFTLINKS /
  _CONDA_OLD_CHCP / __CONDA_OPENSSL_CERT_FILE_SET /
  PYTHONUTF8 / PYTHONIOENCODING / SSL_CERT_FILE

conda 删除  0 个          ← 一个都没删
conda 改动  2 个
  ~ PATH   : 前面插入 D:\Anaconda3\envs\aicoding;...
  ~ PROMPT : 加了 "(aicoding) " 前缀

原样透传 64 个（含 MY_CANARY）
```

### 结论：conda 是「叠加层」，不是「替换层」

它只做三件事：加自己的变量、把环境目录插到 `PATH` 最前、改提示符。**从不清空环境。**

因此 `conda run` **不能隔离环境变量**——它管的是「用哪个 python、哪个 site-packages、PATH 谁优先」
（即**包**），不管**进程环境**。「conda 环境」≠「干净沙箱」。

### 那 `ANTHROPIC_*` 是谁放的：三处排查

| 查哪里 | 结果 |
|---|---|
| `HKCU\Environment`（Windows 用户级） | 无 |
| `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`（系统级） | 无 |
| 一个不是 Claude Code 子进程的普通 shell | **一个都没有** |
| Claude Code 会话里的 shell | **7 个全在**（`ANTHROPIC_*` 6 个 + `API_TIMEOUT_MS`） |

注册表没有、普通 shell 没有、只有 Claude Code 进程树里有 ⇒ **只能是 Claude Code 启动时读
`~/.claude/settings.json` 的 `env` 块，注入自己的进程环境。**

### 完整传递链

```
Claude Code 会话进程 (claude.exe)
│  ① 启动时读 ~/.claude/settings.json 的 env 块 → 注入【自己的】进程环境（源头）
│
└─ ② 执行 Bash/PowerShell 工具 → 起一个 shell 子进程
   │     OS 复制父进程的环境块 → 变量跟着走
   │
   └─ ③ conda.exe run -n aicoding python hello_agent.py
      │     conda 复制父环境 + 13 个自己的 + 改 PATH/PROMPT
      │     ANTHROPIC_* 原样保留（实验 2：64 个变量零改动）
      │
      └─ ④ D:\Anaconda3\envs\aicoding\python.exe
         │     同样复制 → 变量还在
         │
         └─ ⑤ SDK 启动 claude.exe 子进程
                复制 python 的环境 → 这就是「继承环境」（第 3 优先级）
```

**机制一句话：环境变量是「复制」而不是「重新计算」；只要没人显式删掉，它就会沿进程树一路传下去。**

### 这条理解带来两个实际结论

1. **解释了用例 D 为什么成功**：在 Claude Code 会话里跑脚本，即使 `options.env` 什么都不传，
   第 ③ 层继承来的变量里已有完整配置——**脚本其实从没离开过配置**。这也是第一次反向对照难以失败的原因。
2. **这正是 `setting_sources=[]` + 显式 `env=` 的必要性所在。** 同一个脚本换地方跑，行为不同：

   | 跑在哪 | 继承环境里有配置吗 | 结果 |
   |---|---|---|
   | Claude Code 会话里 | 有 | 代码写错也可能「跑通」（假象） |
   | 自己的普通终端 | 没有 | 只能靠 `load_endpoint_env()` 从 settings.json 读 |
   | CI / 别人 clone 后跑 | 没有 | 同上 |

   前两行结果一样、原因不同——**这正是最危险的地方**：在会话里「验证通过」了，换台机器就可能挂。
   `setting_sources=[]` + 显式 `env` 做的就是把「碰巧能跑」变成「确实能跑」：端点来源唯一、明确、不靠运气。

---

## 五、附带发现

### 1. `unrecognized_model` 警告（无害）

每次运行都出现，成功和失败时都有：

```
[cli stderr] [claude-code:unrecognized_model] {"model":"deepseek-flash[1m]","query_source":"sdk"}
```

CLI 的内置模型表里没有 `deepseek-flash[1m]`，但 DeepSeek 服务端认识它，实际调用正常。
**作用**：这是个可以及早发现「端点没配对」的信号，但它本身**不代表出错**。

### 2. `total_cost_usd` 用的是 Anthropic 定价表，不是 DeepSeek 账单

`model_usage` 里的两个字段：

```
'provider': 'firstParty',    ← 声称是 Anthropic 自家
'costBasis': 'unknown'       ← 且明确表示计费依据未知
```

但这次请求实际发往 DeepSeek。`total_cost_usd = 0.09646` 是按 **Anthropic 的价目表**估算出来的，
跟 DeepSeek 的实际计费是两个口径。**这解释了「`/cost` 对不上账单」的成因**——
不是配置没生效，是计价口径不同。

> 注意：本条只证明了「成本字段的口径是 firstParty 且 basis 未知」，
> 没有核对过 DeepSeek 的真实账单。要下定论仍需拿账单页比对。

---

## 六、复现方式

```bash
# 正向
D:/Anaconda3/Scripts/conda.exe run --no-capture-output -n aicoding python hello_agent.py

# 反向（应失败，退出码 1）
HELLO_AGENT_BASE_URL="https://api.deepseek.com/anthropic-WRONG-PATH" \
  D:/Anaconda3/Scripts/conda.exe run --no-capture-output -n aicoding python hello_agent.py
```

第四节的继承实验（PowerShell）：

```powershell
# 实验 1：自定义变量能否穿过 conda
$env:MY_CANARY = 'canary-12345'
py        -c "import os;print('MY_CANARY=', os.environ.get('MY_CANARY'))"
D:\Anaconda3\Scripts\conda.exe run --no-capture-output -n aicoding `
  python -c "import os;print('MY_CANARY=', os.environ.get('MY_CANARY'))"

# 实验 2：完整环境变量 diff
py dump_env.py env_a.json
D:\Anaconda3\Scripts\conda.exe run --no-capture-output -n aicoding python dump_env.py env_b.json
# 比对 env_a.json 与 env_b.json 的键值差异

# 三处排查：ANTHROPIC_* 到底是不是系统级设置的
Get-ItemProperty 'HKCU:\Environment' | Select-Object -Property *ANTHROPIC*
Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment' |
  Select-Object -Property *ANTHROPIC*
# 预期：两处都查不到；而在 Claude Code 会话内的 shell 里能查到 7 个
```

### 关于「可复现」：锚定版本，不靠冻结文件

本文件记录的是**某一次运行**，它对应的是**某一版**的 `hello_agent.py`。
要复现，用 commit 锚点取回那一版 —— **不要**用「约定这个文件谁都不许改」来保证可复现。
那样每验证一次就多一个不能动的文件，记录反而把代码锁死了。

```bash
# 取回本文件所述的那一版（首笔提交，含上述全部验证代码）
git checkout 64beb30 -- hello_agent.py

# 看完还原：
git checkout HEAD -- hello_agent.py
```

锚点 `64beb30` 是首笔提交 —— 本文件描述的那版 `hello_agent.py` 就在里面。
以后 `hello_agent.py` 再怎么演进（加工具、加参数），这条命令取回的仍是它。
