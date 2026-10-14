# servers/experiments —— 实验脚本与落盘证据

这个目录放两样东西：

- **脚本**：为了做某组对照而写的小工具，**可以重新跑**
- **`out/`**：那些脚本跑出来的**原始字节**，`VERIFY-mcp.md` 直接引用它们

> **不要删 `out/` 里的文件。** 它们是原始证据 —— 没有它们，读者只能看到我转抄的结论，
> 看不到当时的真实输出。文件都很小（最大的 17 KB）。

这些实验**不是**生产代码。生产 server 是上一级的 `local_tools_server.py`。

---

## 脚本

| 脚本 | 干什么 | 用在 `VERIFY-mcp.md` |
|---|---|---|
| `probe_client.py` | **手工 JSON-RPC 探针**。两个关键能力：① 发完请求**不关 stdin**（用管道喂 JSON 时 stdin 会提前 EOF，`tools/call` 的响应还没读就收摊）；② 把 server 的**原始 stdout 逐行落盘**，不做任何过滤 | 第 2 节（server 自检）、第 4.4 节 |
| `_stream_timing_probe.py` | **计时探针**。在子进程**还活着**的时候去看管道里有多少字节 —— 这是区分「输出被丢掉」和「输出被缓冲到退出」的唯一办法 | 第 4.4 节 (b-1) |
| `_stdin_echo_child.py` | 子进程：读一行 stdin 并回显。用来定论 conda 到底有没有把 stdin 转发进去 | 第 4.4 节 (b-2) |
| `_buffered_output_child.py` | 计时用的子进程：t=0 打一行、睡 6 秒、t=6 再打一行。**两个 `print` 都带 `flush=True`** —— 不 flush 的话，「管道 0 字节」就分不清是 conda 缓冲还是 Python 自己缓冲 | 第 4.4 节 (b-1) |
| `_stray_in_handler_server.py` | **实验专用「脏」server**（复制自生产 server）：在 tool handler 里故意往 stdout 写东西，两种写法各来一次 —— `print()`（走 Python 对象）和 `os.write(1,…)`（绕过对象直接写 fd） | 第 4.1–4.3 节 |
| `_env_dump_server.py` | 探针：把 Claude Code 拉起 MCP server 时**真实传进来的环境** dump 出来。⚠️ **只记键名和白名单前缀的值** —— 环境里带着 `ANTHROPIC_AUTH_TOKEN`，全量 dump 会把密钥写进仓库 | 第六节第 2 条 |

> ⚠️ `_stray_in_handler_server.py` **不要注册给任何客户端**，它是脏的。

## `out/` —— 落盘证据

### 探针三档（第 4.4 节）：同一个 server，只换启动方式

| 前缀 | 启动方式 | 关键结果 |
|---|---|---|
| `direct.*` | 直连 `D:\Anaconda3\envs\aicoding\python.exe` | 5 行 stdout，全 JSON ✅ |
| `conda.*` | `conda run --no-capture-output` | 5 行，与直连**逐字相同** ✅ |
| `conda-nocap.*` | `conda run`（**不加** flag） | **0 字节** ❌ |

每个前缀三件套：

- `*.stdout.raw` —— server 的原始 stdout 字节（这就是"协议线"，一眼能看出干不干净）
- `*.stderr.log` —— server 的 stderr
- `*.result.json` —— 探针整理的结果（响应、工具名、非 JSON 行数、退出码）

### 计时与 stdin（第 4.4 节 (b)）

| 文件 | 内容 |
|---|---|
| `timing-three-ways.log` | 三档启动方式下，子进程输出**什么时候**到达管道（t=3s 运行中 vs 退出后） |
| `stdin-forwarding.log` | 三档启动方式下，子进程的 `readline` 到底收到了什么 |

### stdout 污染实验（第 4.1–4.3 节）

| 文件 | 内容 |
|---|---|
| `stray-handler.*` | 探针连「脏」server：stdout 3 行全 JSON，`STRAY` 两行落在 `stderr.log` |
| `stray-handler-clientB.log` | **真实客户端**（`hello_agent_stdio.py`）连同一个脏 server 的完整日志 |

### 两个客户端（第 1、2 节）

| 文件 | 内容 |
|---|---|
| `client-a-claude-code.json` | 客户端 A（`claude -p`）的完整 JSON 输出 |
| `client-a-claude-code.stderr.log` | 客户端 A 的 stderr |

客户端 B 的输出是直接跑出来的，未单独落盘（`stray-handler-clientB.log` 是它的实验版运行）。

### 反向对照用的临时配置（第 3.2 节）

| 文件 | 内容 |
|---|---|
| `mcp-good.json` | 指向真实 server 的 `--mcp-config` 配置 → 期望 `FOUND` |
| `mcp-bad.json` | 指向不存在的 `.py` → 期望 `NOT_LOADED` |
| `mcp-expand.json` | 用 `${MCP_TEST_PYTHON}` / `${MCP_TEST_SERVER}`，测变量展开 |
| `mcp-projdir.json` | 2×2 对照：`projdir` 用 `${CLAUDE_PROJECT_DIR:-.}/…`，`abs` 用绝对路径。两个 server 放同一个文件，**一次提问同时拿到两侧答案**（省一半 API 调用） |
| `mcp-novar.json` | 同上但去掉 `:-` 默认值 → 用来判定**变量本身**是否被设置 |

> ⚠️ `mcp-projdir` / `mcp-novar` 这两组实验**最初得出的结论是错的**，别参考旧版记录。
> 当时 `.mcp.json` 是用 `cat > … <<'EOF'` 写的，**JSON 里的反斜杠被吃掉一层**，
> `command` 字段本身就损坏了 → 一律 `NOT_LOADED`，而我把它错归因给了 `args`。
> （完整复盘见 `VERIFY-mcp.md` 第六节第 3 条。写 JSON 一律用 `python -c "json.dump(…)"`。）
>
> **用正确 JSON 重跑后的结论**：绝对路径、裸相对路径、`${CLAUDE_PROJECT_DIR:-.}` **三种写法都可用**；
> 后两者**完全等价**（该变量本机未设置，`:-` 回退到 cwd），且**基准都是 cwd** ——
> 只在「cwd = 项目根」时成立。证据见 `out/q2-q3-mcpjson-path-forms.log`。

### `.mcp.json` 的受控实验（第六节第 0、2、3 条）

| 文件 | 内容 |
|---|---|
| `q1-mcpjson-loading.log` | Q1：`.mcp.json` 在场 / 移走 两侧，**每次运行都带 cwd** |
| `q2-q3-mcpjson-path-forms.log` | Q2/Q3：三种路径写法、判别档、子目录作用域档（**保留了被污染的那一段并标注作废原因**） |
| `q4-cache-vs-path.log` | Q4：good/bad 同文件 → 只改 bad 的路径（同样保留被污染段） |

> 这几个 log 里**故意保留了作废的实验段**。它们记录的是「一次错误归因长什么样」——
> 比只留正确结论更有价值：被判据打脸的那一步，才是这次实验真正新增的信息。

### 转义实验（第六节第 3 条）

| 文件 | 内容 |
|---|---|
| `backslash-escape-channels.log` | 同一段含反斜杠的内容，改走双引号 / 单引号 / printf / heredoc / `chr(92)` 构造五种通道，看 bash 实际收到什么 |

> **结论**：只有 `chr(92)` 构造的反斜杠能完整到达，其余通道全部丢一层。
> 所以规则是「**不要手写反斜杠字面量**」，**不是**「别用 heredoc」——
> 带引号的 heredoc 是完全忠实的，它只是忠实地写下了送进来的那一层。
>
> 无引号 heredoc (`<<EOF`) 会塌掉一层，那是**另一条**独立的真规则（输入相同时 8→6 字节），
> 与本次污染无关。

---

### 运行时状态 vs 冻结快照（`out/usage.jsonl`）

`out/usage.jsonl` **不进仓库**（已进 `.gitignore`，2026-09-16 起 `git rm --cached` 停止跟踪）。

理由不是"它不重要"，而是它的**性质**和这个目录里其他文件根本不同：

| | 这个目录里的其他文件 | `usage.jsonl` |
|---|---|---|
| 性质 | **冻结快照** —— 某次实验的原始输出，写完就不再变 | **运行时状态** —— 每次运行往后追加一行 |
| 进仓库吗 | 进。它们是 `VERIFY-mcp.md` 直接引用的证据 | **不进** |
| 为什么 | 证据要能被引用、被复算 | ① 每跑一次实验就污染工作区（实测：2 次追加对应 2 次提交）；② 它是本仓库里**唯一会被运行时写入**的文件 —— 移出跟踪，「写入时夹带凭据」这一整类风险直接消失 |

**里程碑时怎么办**：把它**冻结**成带名字的快照再提交，并在对应的 `VERIFY*.md` 里引用：

```bash
cp servers/experiments/out/usage.jsonl servers/experiments/out/usage-<里程碑名>.jsonl
# 例：usage-keyrouting-v1.jsonl —— key 分流改造完成时的那份
```

**冻结快照进仓库，运行时状态不进。** 这条分界线对所有"会持续增长的文件"都适用，
不只 `usage.jsonl`。

---

## 怎么重新生成

全部命令都在 `VERIFY-mcp.md` 第七节。这里只列最常用的两条：

```bash
# 探针自检（不花钱，不接客户端）
D:/Anaconda3/envs/aicoding/python.exe servers/experiments/probe_client.py \
  --protocol legacy \
  "servers/experiments/out/direct" -- \
  "D:/Anaconda3/envs/aicoding/python.exe" "E:/aicoding/mini-claude-code/servers/local_tools_server.py"

# 三档计时对照（约 25 秒）
D:/Anaconda3/envs/aicoding/python.exe servers/experiments/_stream_timing_probe.py direct -- \
  "D:/Anaconda3/envs/aicoding/python.exe" servers/experiments/_buffered_output_child.py
```

涉及真实客户端（`claude -p` / `hello_agent_stdio.py`）的会**产生 API 费用**，
单次约 $0.13–0.18，别无脑重跑。

## 两个容易误读的点

1. `*.result.json` 里的 `exit_code` 是 **server 进程**的退出码，不是探针的。
   `conda-nocap` 那档 server 退出码是 **0** —— 它是**正常退出**的
   （stdin 立即 EOF 对命令行程序就是「收工」信号），不是崩溃。别把它读成"失败得很有道理"。

2. `stray-handler.stderr.log` 里的 `fstat(1) 与 fstat(2) 同源？ True` 只是**辅助**证据 ——
   Windows 上管道的 `st_ino`/`st_dev` 都是 0，这个比较并不严格。
   真正的判据是**行为**：`STRAY` 两行出现在 stderr 文件里、而 stdout 里一行都没有。
