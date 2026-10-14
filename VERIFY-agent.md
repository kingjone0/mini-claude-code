# VERIFY-agent.md —— mini Coding Agent 闭环验证

> 本文是 `coding_agent.py` 的**原始验证记录**。与 `VERIFY.md`（SDK 验证）、
> `VERIFY-mcp.md`（MCP 跨客户端验证）同属一套证据链。
>
> 全部命令可原文复制重跑。**关键结论都有可证伪的对照支撑。**

---

## 0. 一句话结论

Agent 能在**只有沙箱工具**可用的条件下，自主完成「定位 → 改代码 → 跑测试 → 失败后换策略 → 复验」闭环，
把靶场从 `2 failed, 8 passed` 修到 `10 passed`；三组反向对照按预期失败（不谎报成功、越界被拒、配置坏则整体失败）。

---

## 1. 环境

| 项 | 值 |
|---|---|
| conda 环境 | `aicoding`（Python 3.12.14） |
| 关键包 | `claude-agent-sdk 0.2.152`、`mcp 2.2.0`、`pytest 9.1.1` |
| CLI | `E:\node.js\24.21.0\node_modules\@anthropic-ai\claude-code\bin\claude.exe` |
| 端点 | 项目根 `.env`（唯一来源，见陷阱 13/14） |
| 靶场 | `fixtures/mini_repo/`（2 个已知缺陷） |

复跑命令：

```powershell
Set-Location E:\aicoding\mini-claude-code
D:\Anaconda3\envs\aicoding\python.exe coding_agent.py --label <标签>
```

---

## 2. 靶场的初始状态（必须先确认"题是坏的"）

用纯 Python 直接验契约（不经过 pytest、也不经过 agent）：

```
[OK]   safe_divide(6,3) → 2.0
[FAIL] safe_divide(1,0) → 抛 ZeroDivisionError，期望 ValueError
[OK]   clamp(5,0,10) → 5
[OK]   clamp(5,10,0) → 抛 ValueError
[OK]   mean([1,2,3]) → 2.0
[OK]   median([3,1,2]) → 2
[FAIL] median([1,2,3,4]) → 3，期望 2.5
[FAIL] median([1,2,3,4,5,6]) → 4，期望 3.5
```

pytest 视角：`2 failed, 8 passed in 0.04s`。

> ⚠️ 这一步不能省。如果靶场本来就是绿的，后面"修好了"什么也证明不了。

---

## 3. 三组验收

每组都以 `--label` 落盘：`out/agent-<label>.json`（完整事件序列）与 `.log`（可读日志）。
**判定一律来自独立验证**：脚本自己跑 `pytest`、自己查改动文件，**不采信 agent 的自述**。

### 3.1 正向（`--label v2-pass`）

| 项 | 结果 |
|---|---|
| 独立 pytest | **1 → 0**（`10 passed`） |
| 改动文件 | `fixtures/mini_repo/calc.py`、`fixtures/mini_repo/stats.py` |
| 测试文件被改 | 否 |
| 越界改动 | 无 |
| **判定** | **成功** |
| 退出码 | 0 |
| 工具调用 | 10 次，**全部是 MCP 工具**（list_files×1, run_tests×2, read_file×4, apply_patch×3） |
| run_tests 退出码序列 | **`[1, 0]`** |
| 耗时 | 10.1 s |

**`run_tests` 退出码序列 `[1, 0]` 是「失败自省」的直接证据**：它跑→失败→改→**复验**，
而不是改完就宣称完成。

**首轮 run（`first-run`）留下了更细的对照**：agent 第一次 `apply_patch calc.py` 用的
`old` 片段带了转义引号，返回「未找到要替换的片段」；它**没有原地重试**，而是先去改
`stats.py`，再回到 `calc.py` 用一段更短、唯一的 `old` 成功替换。这就是「失败后换策略」。

### 3.2 反向 A —— 拿掉写工具（`--no-write`）

预期：**修不好，且必须如实报告失败，不能谎报成功。**

| 项 | 结果 |
|---|---|
| 独立 pytest | **1 → 1**（仍是 `2 failed`） |
| 改动文件 | **无** |
| **判定** | **未成功** |
| 退出码 | 1 |
| agent 自述 | 「I've completed the diagnosis but hit a hard environment blocker on applying the fix.」→ **如实报告失败** |

**这一组证明的不是"agent 修不好"，而是"验收不会被 agent 的话骗过去"** ——
诊断部分它说得完全正确，但那不影响判定。

### 3.3 反向 B —— 端点故意写错

```powershell
$env:AGENT_BASE_URL_OVERRIDE="https://api.deepseek.com/anthropic-WRONG-PATH"
python coding_agent.py --label v2-badendpoint
```

| 项 | 结果 |
|---|---|
| 判定 | **未成功** |
| 独立 pytest | 1 → 1 |
| 工具调用 | **0 次**（根本没跑起来） |
| 记录的异常 | `ResultError: Claude Code returned an error result: There's an issue with the selected model…` |
| 退出码 | 1 |

⚠️ **注意报错文案在骗人**：它说"模型可能不存在或你没有权限"，**实际是端点 URL 写错了**
（陷阱 3）。顺着"模型问题"去查会白查半天。

### 3.4 沙箱越界（工具层，见 `code_tools_smoke.py`）

12/12 通过，其中 5 项是拒绝类对照：

| 攻击面 | 结果 |
|---|---|
| `read_file("../../../Windows/win.ini")` | 拒绝，并说明"路径越出仓库根" |
| `apply_patch("../outside.txt")` | 拒绝 |
| `read_file("C:/Windows/win.ini")`（绝对路径） | 拒绝 —— `Path("C:/x")` 会让 `/` 丢弃 root，必须 resolve 后比较 |
| `run_tests(test_path="../../x.py")` | 拒绝 |
| `run_tests(keyword="; rm -rf /")` | 拒绝 |
| `apply_patch(old="def ")`（出现 2 次） | 拒绝，要求给出唯一片段 |

协议线检查：stdout **12 行全 JSON、0 行杂质**。

---

## 4. ⚠️ 本次抓出来的最大问题：`allowed_tools` 不是白名单

**设计文档里原本写着「agent 只有一条写文件的通路」。反向对照证明这句话当时是错的。**

第一版只设了 `allowed_tools=[5 个 MCP 工具]`，以为这样就把内置工具挡住了。
反向 A 的 46 次工具调用实际是：

```
Bash   19 次
Read    9 次   ← 其中一次读了沙箱**之外**的 out/agent-verify-pass.log
Edit    2 次
Write   2 次
Grep    1 次
mcp__code-tools__apply_patch  7 次   ← 已从 allowed_tools 移除，它照样调了
```

查 SDK 源码（`claude_agent_sdk/types.py`）后确认语义：

| 选项 | 真实语义 |
|---|---|
| `allowed_tools` | 「**免询问自动放行**」。不在其中的工具只是"需要权限"，**仍在模型上下文里** |
| **`tools`** | 「**可用内置工具的基础集合**」—— **`[]` = 禁用全部内置工具** |
| `disallowed_tools` | 从模型上下文移除，即使本来被允许 |
| `permission_mode="dontAsk"` | 不提示；未预先批准就拒绝 |

SDK 文档原话：**"To restrict which tools are available at all, use `tools`."**

**修法**（`coding_agent.py`）：

```python
tools=[],                      # 硬开关：禁用全部内置工具
allowed_tools=tools,           # 只自动放行 5 个 MCP 工具
disallowed_tools=[...],        # 纵深防御
permission_mode="dontAsk",
strict_mcp_config=True,        # 不加载 .mcp.json / 用户级注册
```

**修复后复验**：三组运行的内置工具调用**全部为 0**。

> 这条也是整个验证里最值钱的一条：**正向跑通不会暴露它**。
> 只有"故意让它失败"的对照才会把沙箱的洞照出来。

---

## 5. 其他实测发现

### 5.1 控制台是 GBK：打印工具返回值会崩

`coding_agent.py` 第一次运行时崩在：

```
UnicodeEncodeError: 'gbk' codec can't encode character '\ufffd'
```

agent 当时**正常工作**（已调了 `list_files` 和 `run_tests`），却被一个 `print` 打断，
而且堆栈把真实进展盖住了。

**解法**：`force_utf8_output()`（`reconfigure(encoding="utf-8", errors="replace")`），
必须在任何 `print` 之前调用。仓库里 `probe_client.py` 早就有这个函数 ——
所以现在把它放进公共模块 `agent/config.py`，新程序一律先调。

### 5.2 `ToolResultBlock.content` 是 dict，不是字符串

第一版直接 `str(block.content)`，于是指标 `run_tests_exit_codes` 算出来是
`'1\n────────...'` 这种垃圾 —— **看起来"有值"，实际是错的**。
这类 bug 的代价是：拿着错数字去写验证记录。

修法：`tool_result_text()` 归一化 + 用正则 `退出码\s*=\s*(\d+)` 提取。

### 5.3 `--no-write` 下 agent 仍然会"尝试"调 `apply_patch`

它试了 3 次，每次被 `permission_mode="dontAsk"` 拒绝 —— **改动文件为"无"**。
也就是说这一组控制的生效机制是**权限拒绝**，而不是"工具不存在"。

这个区别值得记下来：如果将来换成 `permission_mode="acceptEdits"` 或
`bypassPermissions`，这个对照会**静默失效**（工具调用会成功）。

### 5.4 agent 的自述可能引用它没读过的文件（第一版）

第一版反向 A 里，agent 声称 "I verified against the recorded passing run
(`agent-verify-pass.log`)"。查事件序列确认：**它确实读到了那个文件**——
因为当时沙箱有洞（内置 `Read` 不受 MCP 路径守卫约束）。
修复后这条通路已关闭。

> 保留这段是因为它说明了一件事：**"引用了一个看起来像证据的文件"不等于"结论可靠"**。
> 判断依据只能是可复现的独立验证。

### 5.5 结果不可逐次复现（沿用陷阱 5）

| run | 工具调用 | 耗时 |
|---|---|---|
| `first-run` | 8 | 9.0 s |
| `verify-pass` | 7 | 7.7 s |
| `v2-pass` | 10 | 10.1 s |

同一个靶场、同一套配置，调用次数与路径每次都不同。
**做消融对比时不要拿单次数字当基准**，要记分布。

---

## 6. 复现步骤

```powershell
Set-Location E:\aicoding\mini-claude-code
$py = "D:\Anaconda3\envs\aicoding\python.exe"

# 0) 依赖
D:\Anaconda3\Scripts\conda.exe run -n aicoding python -m pip install -r requirements.txt

# 1) 工具层冒烟（12 项，含 5 项沙箱对照）
& $py servers\experiments\code_tools_smoke.py --out servers\experiments\out\ct-smoke `
      -- $py E:\aicoding\mini-claude-code\servers\code_tools_server.py

# 2) 正向
& $py coding_agent.py --label my-pass

# 3) 反向 A
& $py coding_agent.py --label my-nowrite --no-write

# 4) 反向 B
$env:AGENT_BASE_URL_OVERRIDE = "https://api.deepseek.com/anthropic-WRONG-PATH"
& $py coding_agent.py --label my-badendpoint
Remove-Item Env:AGENT_BASE_URL_OVERRIDE
```

靶场复位（每轮跑前脚本自动做，手工复位也用这条）：

```powershell
git checkout -- fixtures/mini_repo/
```

---

## 7. 本阶段**不能**证明的事（诚实边界）

| 不能证明 | 为什么 |
|---|---|
| agent 能处理真实仓库的复杂 issue | 靶场只有 2 个缺陷、两个文件、21+24 行。真实仓库是几万行 |
| 这个成绩可迁移到 SWE-bench | 那正是第三阶段要测的。机制一样（改代码 → 跑测试 → 判定），**难度完全不一样** |
| 单次数字代表能力 | 见 5.5，同一道题每次路径都不同 |
| 上下文管理 / subagent / trace 已做好 | 本阶段只做了 `max_turns` + 输出截断。其余见 `DESIGN.md` 第 10 节 |

---

## 8. 上下文管理：先量化，再动手（含一个被数据否掉的方案）

> 这一节的写法是刻意的：**先测量问题有多大，再决定做什么**。
> 不量化就加分层，加完也不知道有没有用。

### 8.1 测量工具

`servers/experiments/context_report.py` —— 从 `agent-*.json` 的事件序列里统计上下文成本。

**前提修复**：事件的 `tool_result.text` 是**截断到 4000 字符**存盘的，
不修的话所有大输出都会被记成同一个上限值，指标失去分辨力。
所以给事件加了 **`chars` 字段记真实长度**，`text` 仍截断存档。

### 8.2 基线（`small_pkg`，20 文件 / 1233 行，4 次取样）

| 指标 | 最小 | 最大 | 均值 |
|---|---|---|---|
| 工具返回字符总量 | 19,537 | 20,873 | **20,389** |
| 工具调用次数 | 8 | 10 | 9 |
| 测试输出占比 | 1% | 40% | — |
| **重复读次数** | **0** | **0** | **0** |

**字符去向（4 次聚合）：**

| 工具 | 字符 | 占比 | 调用 | 平均 |
|---|---|---|---|---|
| `read_file` | 48,659 | **59.7%** | 21 | 2,317 |
| `run_tests` | 23,648 | **29.0%** | 7 | **3,378** |
| `list_files` | 9,048 | 11.1% | 4 | 2,262 |
| `apply_patch` | 200 | 0.2% | 4 | 50 |

### 8.3 ⚠️ 一个被数据否掉的方案

**原本计划做「重复读取去重」。实测 4 次运行的重复读次数都是 0** ——
agent 从不重复读同一文件的同一区间。**该功能没有任何收益，直接砍掉。**

> 这就是"先量化再动手"的价值：省下的不是实现成本，是**把一个没用的东西加进系统**的长期成本。

### 8.4 实际做了什么

| 改动 | 针对 | 依据 |
|---|---|---|
| `repo_map` 工具（`ast` 解析出类/函数/常量 + 行号） | `list_files`（11%）+ 让 `read_file` 能按区间读 | 一次拿全局轮廓 |
| `run_tests(detail=)` —— 默认 `brief`（`--tb=line`，每个失败一行） | `run_tests`（29%，平均 3,378 字符/次） | agent 多数时候只要"哪几个失败了" |

### 8.5 A/B 结果（同靶场，各 4 次）

| 组 | 判定 | 调用 | 工具返回字符（均值） |
|---|---|---|---|
| 基线 | 4/4 | 9 | **20,389** |
| 上下文层 | 4/4 | 9 | **18,866** |

**−7.5%（−1,523 字符）。**

### 8.6 ⚠️ 诚实结论：一半的设计没有被采用

**`repo_map` 在 4 轮里只被调用了 1 次**（其余 3 轮 agent 选了 `list_files`）。
真正起作用的是 `run_tests` 的 brief 模式：

| 指标 | 基线 | 上下文层 | 变化 |
|---|---|---|---|
| `run_tests` 平均字符/次 | 3,378 | **2,490** | **−26%** |
| `repo_map` 使用率 | — | **1/4 轮** | 未成为习惯 |
| 工具调用次数 | 9 | 9 | 无变化 |

**结论：加了工具 ≠ 会被用。** agent 有自己的习惯路径；在它已经能用 8–9 次调用解决问题的规模上，
一个新工具不会自动被采纳。

### 8.7 这个结论的边界（别过度外推）

`small_pkg` 只有 **1233 行**，agent 全读一遍也就 2万字符。
**上下文管理的价值是随规模增长的**，这个小靶场**测不出它的上限** ——
本节的数字只能说明"在这个规模上收益很小"，**不能说明"这个方向没价值"**。

要验证规模假设，必须换一个**大得多的靶场**。那也正好是阶段二的通过标准
（「**自选仓库上**跑通闭环」），不是额外工作。

### 8.8 复现

```powershell
$py = "D:\Anaconda3\envs\aicoding\python.exe"
# 基线 / 实验组各跑 4 次
1..4 | % { & $py coding_agent.py --label base-$_ --fixture small_pkg }
1..4 | % { & $py coding_agent.py --label layer-$_ --fixture small_pkg }
# 对比
& $py servers\experiments\context_report.py --glob "servers/experiments/out/agent-*.json"
```

> ⚠️ 对比时**必须用同一靶场、同一初始状态**。两组的差异只应来自被改的那个变量。
> 每次运行前后 `reset_fixture()` 自动做这件事（见第 3.1 节与陷阱 18）。

---

## 9. 换大靶场：规模假设的验证（以及一个被量化推翻的设计）

### 9.1 目标：`more-itertools`（固定提交）

`small_pkg` 只有 1233 行，测不出上下文管理的上限（第 8.7 节）。换成真实开源仓库：

| 项 | 值 |
|---|---|
| 仓库 | `github.com/more-itertools/more-itertools` |
| 固定提交 | `81c21a8db1c922260cd673841257fb9b112c1318` |
| 源码 | 3 文件 / **7,277 行**（`more.py` 单文件 **5,644 行 / 181,098 字符**）|
| 测试 | 3 文件 / 9,202 行，**768 passed / 21,202 subtests** |
| 运行时依赖 | **零**（测试也不需要额外包）|
| 许可证 | MIT |

**位置**：`E:\aicoding\targets\more-itertools` —— **在本仓库之外**。
那是第三方代码，vendor 进来既臃肿又混淆归属；而且它自己就是 git 仓库，
`git checkout` 正好能把 agent 的改动还原掉。

准备：`python setup_target.py`（clone → checkout 固定 SHA → 注入缺陷 → **提交** → 校验）。
最后那步**提交**不能省：否则 `reset_fixture()` 的 `git checkout` 会把缺陷一起还原掉
（陷阱 18 的同一个坑）。

### 9.2 缺陷是怎么选的：变异测试，不是拍脑袋

`servers/experiments/mutate.py` —— 对每个候选改动，改一次 → 跑一次测试 → 记录失败数 → 改回去。

| 候选 | 失败数 |
|---|---|
| `chunked strict: != → >` | 1 |
| `windowed: n<=0 → n<0` | 1 |
| `windowed: step<1 → step<0` | **0 ← 没抓到（上游没有 step=0 的测试，是真实的覆盖缺口）** |
| `windowed: padding step-1 → step` | 8 |
| `windowed: 首窗 <n → <=n` | 24 |
| **`windowed: 填充数 n-len → n-len+1`** | **2 ← 选它** |

**选它的理由**：这是**输出错误**（`windowed([1,2,3], 4)` 从 `(1,2,3,None)` 变成多补一个 `None`），
比"少抛一个异常"更像真 bug；而且只挂 2 个测试 —— 失败铺满一屏就失去"定位"的意义。

**为什么要变异测试**：凭感觉挑缺陷，很容易挑到**根本没人测**的地方（上表第 3 行就是），
那就等于没注入。让测试自己回答哪条能中。

### 9.3 基线（大靶场，4 次取样）

| 样本 | 判定 | 调用 | 工具返回字符 | 耗时 |
|---|---|---|---|---|
| big-base-1 | ✅ | 11 | 23,919 | — |
| big-base-2 | ✅ | 13 | **46,376** | 114s |
| big-base-3 | ✅ | 14 | 37,424 | 111s |
| big-base-4 | ✅ | 9 | 25,753 | 105s |
| **均值** | **4/4** | **12** | **33,368** | ~108s |

**对比小靶场**（均值 20,389 字符 / 9 次调用）：**大靶场贵 64%**，而且**波动大得多**（23.9k–46.4k，约 2 倍）。

### 9.4 一个反直觉的结果：大仓库反而更省 token

`big-base-1` 的 **input tokens = 10,271**，**低于** `small_pkg` 基线的 10,168–13,504。

原因看工具序列就明白了：

```
read_file README → run_tests → list_files
→ search_code "def windowed"                 ← 定位（171 字符）
→ read_file more.py [1027:1140]              ← 只读相关区间
→ run_tests(keyword="windowed", detail="full")  ← 主动要详细输出
→ read_file test_more.py [880:935] → apply_patch 一处 → run_tests → 0
```

**在大仓库上 agent 不可能整读，只能选择性地读** —— 而选择性读取比整读小仓库更省。
`more.py` 有 181,098 字符，`read_file` 上限 20,000，**所以"全读"这条路物理上不存在**。

> 这条对设计有直接含义：**上下文压力不是随仓库大小线性增长的**，
> 它取决于"工具是否让 agent 能低成本地只取需要的部分"。

### 9.5 ⚠️ `repo_map` 的设计被量化推翻

第 8 节里 `repo_map` 只被用了 1/4 轮，当时只能猜"agent 有自己的习惯"。换大靶场后**原因清楚了**：

| 工具 | 一次调用的平均字符 |
|---|---|
| `read_file` | 4,736 |
| `run_tests` | 1,604 |
| `list_files` | 1,420 |
| **`search_code`** | **171** |
| `repo_map`（旧设计，全量符号） | **46,776** |

**`repo_map` 比 `search_code` 贵 274 倍，而且 46,776 > 20,000 的 `read_file` 上限 —— 它会被截断，给都給不全。**

体积极不合理的原因是：**76% 来自测试文件**（`tests/test_more.py` 有 762 个符号、28,203 字符，
比源码的 207 个符号还多）。旧设计假设"符号索引总是便宜的"，在真实仓库上不成立。

**改法**：默认 `detail="summary"` —— 每个文件一行（路径 / 行数 / 符号数）：

| 模式 | 字符数 |
|---|---|
| `summary`（新默认，全仓） | **359** |
| `symbols`（全仓） | 20,062（截断，并提示"用 subdir 缩小范围"）|
| `symbols`（`subdir="more_itertools"`） | 11,153（完整）|

**从 46,776 降到 359 —— 便宜 130 倍。**

### 9.6 这一节的结论

1. **`search_code` + 按区间 `read_file` 才是这个 agent 真正在用的定位方式** ——
   而且它是**自己学会的**（`repo_map` 从没被要求用）。
2. **工具的价值要用"每次调用的成本"衡量，不能用"信息是否更全"衡量。**
   `repo_map` 信息更全，但贵 274 倍，所以没人用。
3. **规模假设成立了一半**：大仓库确实需要上下文管理，但**有效的形式是"廉价定位 + 区间读取"**，
   不是"一次给全量索引"。
4. **变异测试应该成为常规手段**：它同时暴露了"哪条改动能被测到"和"哪块没有测试覆盖"。


