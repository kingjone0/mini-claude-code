# 第三阶段 P0 预注册：可复现的 SWE-bench 语义评测（容器无关 harness）

> **本文件的地位**：这是第三阶段所有数字的解释基础。它在**第一条真实实例跑起来之前**提交；
> **§1–§9 一经提交不得事后改写**。跑完之后只允许在文末**追加**「结果节」（§10+），
> 且追加节必须引用它对应的规则节编号。任何对 §1–§9 的修改都意味着**重新做一次预注册**，
> 旧数据全部作废、只能作为「探索性观察」另列一节。
>
> - 仓库：`E:\aicoding\mini-claude-code`（AgentTeams 工作区在仓库之外 ⇒ 本文所有路径写绝对路径或项目根相对 POSIX 路径）
> - 本机解释器（唯一）：`D:\Anaconda3\envs\aicoding\python.exe`，pytest `9.1.1`
>   （裸 `python` 是 WindowsApps 的 0 字节商店占位符，exit 9009，**不作为环境证据**）
> - 控制台代码页 GBK(936) ⇒ 本文与后续所有 UTF-8 产物一律用 Read/Write/Edit 工具读写，不用 shell 重定向
> - 提交方式：`.git/COMMIT_MSG_*.txt` + `git -c i18n.commitEncoding=utf-8 commit -q -F <文件>`；绝不 `git add -A`
> - 任务边界：**本次提交只新增本文档，不改任何代码**（构造器 / harness / `coding_agent.py` 入口都是后续任务）。

---

## §0 术语表（后文所有判据共用，避免歧义）

| 术语 | 定义 |
|---|---|
| **实例（instance）** | 一条待评测任务 = (§1 schema 的那份 JSON)，从某个真实修复提交 revert 构造而来 |
| **基线（baseline）** | 该修复提交的父提交 `commit^` 检出的完整仓库状态 |
| **gold patch** | 该提交里**非测试文件**的改动（源码侧），仅用于 §6 自校验，**永远不进 agent 可见输入** |
| **test patch** | 该提交里**测试文件**的改动，harness 在判定前应用到基线工作区 |
| **红（fail）/ 绿（pass）** | 以独立 pytest 进程的退出码与该 node id 的结果为准，见 §5 |
| **node id** | pytest 报出的测试节点全名 `<相对路径>::<类名>::<函数名>[<参数id>]`，字符串精确匹配 |
| **工作区（workspace）** | 一个实例专属的目录副本（§8），路径 `E:\aicoding\eval-runs\<run_id>\<instance_id>\repo` |
| **判定运行（verdict run）** | 一次「装补丁 → 跑 pytest → 归档原始输出」的完整尝试，产出 §5 的六分类之一 |
| **基础设施错误（infra error）** | §5.4 列举的、与 agent 能力无关的运行故障 |
| **批次（batch）** | 一轮完整评测：12 条实例 × 1 个模型 × 重复次数（§7、§9） |

---

## §1 实例 Schema

### 1.1 落盘形态与命名

- 一条实例 = 一个 JSON 对象；整个批次 = JSONL（每行一条），落在**仓库内** `eval/instances/<set_name>.jsonl`。
- 文件名固定：第一段正式集为 `eval/instances/pilot-12.jsonl`。
- 大字段（patch / test_patch / 日志）不落进 JSONL，而是落进仓外数据目录，JSON 里存**相对 `E:\aicoding\eval-cache\` 的路径 + SHA-256**（§9.4）。
- 实例一旦纳入正式集，其 JSON 内容视为冻结；发现构造错误时按 §7.5 处理，不允许原地改。

### 1.2 官方对齐字段（九个，逐字段定义与来源）

| 字段 | 类型 | 是什么 | 怎么产生（来源） | 什么情况下判不了 / 缺失怎么办 |
|---|---|---|---|---|
| `instance_id` | str | 全局唯一主键 | 固定格式 `<repo_owner>-<repo_name>__<fix_commit 前 7 位小写十六进制>`，例：`more-itertools-more-itertools__a1b2c3d`。由构造器拼出，不接受人工命名 | 若同一基线出现两个同前 7 位 commit（碰撞概率极低但可判）⇒ 加长到 12 位并记入 §10 追加节；无法去重则该候选排除 |
| `repo` | str | 上游仓库标识 | `<owner>/<name>`，取自构造时克隆的 remote URL（去掉 `.git`），不使用本地路径 | remote 不是 github/unknown ⇒ 按 §2 R2 排除 |
| `base_commit` | str | 基线版本 | 修复提交的父 commit 的**完整 40 位** SHA（`git rev-parse <fix>^`）。必须是可从已 fetch 的历史真实检出的对象 | `git cat-file -e` 失败 / GC 掉了 ⇒ 该候选排除（§3.2 E0） |
| `problem_statement` | str | 给 agent 的自然语言问题描述 | **来自该修复提交的提交信息**（subject + body，原样保留换行，不做润色、不补 issue 链接、不加提示）。详见 §3.4 —— 这是与官方的**已知差异** | 提交信息为空、或只有 `wip`/`fix typo`/纯版本号这类零信息量文本 ⇒ 按 §3.2 E9 排除（不许代笔重写） |
| `patch` | str | gold patch（源码侧改动，unified diff 文本） | `git show <fix> -- <该提交里的非测试 .py 文件路径集合>`，只含源码侧文件 | 该提交没有非测试 `.py` 改动 ⇒ 根本不成为候选（§3.1 C1）；diff 为空串 ⇒ 排除（E3） |
| `test_patch` | str | 测试侧改动（unified diff 文本） | `git show <fix> -- <该提交里的测试文件路径集合>`，只含测试侧文件 | 该提交没有测试文件改动 ⇒ 不成为候选（C1）；diff 为空 ⇒ 排除（E3） |
| `FAIL_TO_PASS` | list[str] | 期望「红转绿」的 node id 集合 | 实测推导，规则见 §4.2（基线绿 ∧ test_patch 后红 ∧ 再叠 gold 后绿，三向一致） | 推导结果为空集 ⇒ 排除（E5）；含 collection error 无法定位到 node id ⇒ 排除（E6） |
| `PASS_TO_PASS` | list[str] | 期望「一直绿」的回归保护集 | 实测推导，规则见 §4.3（基线绿、test_patch 后仍绿、gold 后仍绿；选取范围受 §4.3 上限约束） | 为空 ⇒ 允许，但 `known_limits` 必须写明「该实例无回归保护集」；若因 collection error 拿不到 ⇒ 排除（E6） |
| `version` | str | schema 版本号 | 常量 `"p0-1.0"`。**任何字段增删改语义都必须升版本**，并在 §10 追加节说明；低版本实例不与高版本混算 | 缺字段 ⇒ 该实例整条作废重建，不做兼容读取 |

### 1.3 我们的扩展字段（官方没有、但容器无关 harness 必须自带）

| 字段 | 类型 | 是什么 / 怎么判 / 什么时候判不了 |
|---|---|---|
| `env_spec` | obj | 环境定义（替代官方 Docker 镜像的角色）：`{python: "D:\\Anaconda3\\envs\\aicoding\\python.exe", python_version_constraint, install_cmd: [pip 命令行], venv_root: "E:\\aicoding\\eval-venvs", requirements_source: "repo:<path>" }`。安装命令必须**逐字可复制执行**；`install_cmd` 需要联网才能装齐 ⇒ 记录 `requires_network: true` 并按 §2 R5 处理（首次建 venv 允许联网下载依赖，判定阶段禁止网络） |
| `test_cmd_template` | str | 该仓库跑测试的命令模板（默认 `"python -m pytest -p no:cacheprovider -q --timeout=120 {node_ids}"`），由 §2 纳入时的实测确定。模板不含具体 node id |
| `collection_scope` | list[str] | F2P/P2P 允许扫描的测试路径白名单（如 `["tests"]`）。超出范围的测试不参与选取，防止 P2P 无限膨胀 |
| `flaky_known` | bool | 是否命中 §4.5 的 flaky 标记。判不了的情形：未跑够 §4.5 要求的 3 次一致性验证 ⇒ 必须为 `true` 并排除 |
| `flaky_evidence` | obj/null | 命中 flaky 时的实测证据（哪几次结果不一致、原始输出路径） |
| `source_repo_url` / `fix_commit` / `fix_commit_date` | str | 溯源三元组：上游 URL、完整 40 位修复 commit SHA、其 author date（ISO8601）。用于任何人复算 patch |
| `construction_method` | str | 常量 `"revert_of_real_fix_commit"` —— 显式声明实例由真实修复提交反向构造（基线 = 缺陷态），不是手写 bug |
| `gold_selfcheck` | obj | §6 三向自校验记录：`{gold: "resolved", empty: "unresolved", wrong: "unresolved", evidence_paths: [...], checked_at}`。三向不全对 ⇒ 该实例**不得进入正式集**（§6.4） |
| `wrong_patch` | str | §6.3 用的错误补丁文本（构造规则见 §6.3），随实例冻结落盘，保证第三方能复算第三向 |
| `excluded_tests` | list[obj] | 被排除的测试 node id + 原因（慢、flaky、需网络、需编译…）。每条 `{node_id, reason}`，reason 取 §2/§4.5 的枚举值 |
| `set_membership` | str | `"pilot-12"` \| `"reserve"` \| `"rejected"`。抽样规则见 §7.3 |
| `sample_rationale` | str | 为什么入选（必须是 §7.3 决定的**机械理由**，例如「按 commit 日期升序的第 3 个通过全部筛选的候选」），**禁止**出现任何与预期难度相关的表述 |
| `known_limits` | list[str] | 该实例的已知局限（如「无 P2P」「P2P 截断至上限」「依赖来自 lockfile 而非 setup.py」） |
| `data_paths` | obj | 外部大文件位置：`{patch_file, test_patch_file, wrong_patch_file, build_log_dir}`，全部相对 `E:\aicoding\eval-cache\` |
| `schema_sha256` | str | 除本字段外该 JSON 对象的规范化（`sort_keys=True`, `ensure_ascii=False`, 紧凑分隔符）UTF-8 序列化 SHA-256。任何下游读取必须先重算此哈希，不一致即拒绝使用 |

### 1.4 实例的自检（构造器交付前必须全过）

1. 必填字段齐全、类型正确、`version == "p0-1.0"`；
2. `patch` 与 `test_patch` 的文件路径集合**不相交**，且二者并集 == 该 commit 改到的、属于「非测试 `.py` ∪ 测试文件」的全部文件（其余类型文件如 CI/docs 一律丢弃并记入 `known_limits`）；
3. `FAIL_TO_PASS` ∩ `PASS_TO_PASS` == ∅；
4. `gold_selfcheck` 三向齐全且结论符合 §6；
5. `schema_sha256` 可重算复现；
6. 任何一条不满足 ⇒ 该实例状态置 `rejected`，不进正式集（§7.5）。

---

## §2 仓库纳入 / 排除规则

### 2.1 纳入条件（全部为**可判定谓词**，任一不满足即排除该仓库）

| # | 条件 | 怎么判（可执行的检查） |
|---|---|---|
| R1 | 纯 Python 项目 | 仓库存在 `setup.py` / `pyproject.toml` / `setup.cfg` 之一，且 `pyproject.toml` 的 `[build-system].requires` 不含 Cython / setuptools 之外的编译工具链；`grep` 无 `.pyx` / `.c` / `.cpp` / `.rs` 参与构建（存在但不参与测试包导入亦可接受，须记 `known_limits`） |
| R2 | 公开可克隆、host 可达 | `git ls-remote <url>` 退出码 0；owner/name 可从 remote 解析 |
| R3 | 测试框架是 pytest | 仓库有 `tests/` 或 `test_*.py` 且能被 `python -m pytest --collect-only -q` 收集出 ≥ 20 个 node id |
| R4 | 能在本机装依赖 | 用 §8.2 的命令建 venv 后，`install_cmd` 退出码 0，随后 `python -c "import <pkg>"` 退出码 0 |
| R5 | 基线测试可在时限内跑完 | 在 `collection_scope` 内跑一次全量 pytest：**墙钟 ≤ 300 s**、退出码 ∈ {0,1}（1 表示有 fail 但没崩）。超时 / exit 2 (usage) / exit 3-5 (内部错、中断、收集错) ⇒ 排除 |
| R6 | 不需要网络即可跑测试 | 基线测试在**关闭网络**（本机不额外授予代理变量，且不访问外网域名）下重跑一次，结果与 R5 那次一致 |
| R7 | 不需要编译步骤 | 装完依赖后直接 import 成功，无需 `build_ext` / `make` / C 编译器；`pip install -e .` 若触发 MSVC/CMake 需求 ⇒ 排除 |
| R8 | 有可 revert 的真实修复提交 | 存在 ≥ 1 个满足 §3.1 C1–C4 的提交（用 `git log` 扫近 24 个月的提交即可判定） |

### 2.2 排除标准（仓库级，命中即弃用该仓库，不看结果再决定）

- 测试本身依赖服务/端口/Docker/GPU/大模型权重；
- 快照测试（snapshot / golden file）占比 > 30% —— 这类补丁几乎必然要同时改 golden 文件，diff 拆分不干净；
- 仓库把测试放在源码包内且与源码同文件（无法按 §3.1 拆 `patch` / `test_patch`）；
- 历史里 merge commit 占绝大多数、squash-merge 风格缺失（导致 §3.1 C2 单改动主题难以成立）；
- 许可证要求我们不得镜像其测试数据（本文档场景下直接排除最省事）。

### 2.3 判定不了的兜底

若某条 R 在本机无法验证（例如无法真正断网），**保守处理：当作不满足并排除**，同时在 §10 追加节写明「R6 因 … 未能验证」。
**不允许**用「大概没问题」放行 —— 这条是本节的核心纪律。

---

## §3 候选提交与构造法

### 3.1 候选提交的条件

| # | 条件 | 怎么判 |
|---|---|---|
| C1 | **同时改到「非测试 `.py`」与「测试文件」** | `git show --name-only <fix>` 后按 §3.2 分类规则切分，两侧文件集合都非空 |
| C2 | 单一改动主题 | subject 不以 `Merge` 开头；parent 数 == 1（非 merge commit）；不是纯格式化/纯 bump |
| C3 | 时间窗 | author date 在 `[窗口起, 窗口止]` 内；窗口在 §7.3 预先钉死，覆盖 ≥ 12 个月，早于本文档提交日 |
| C4 | 基线可检出 | `git worktree`/副本能干净检出 `<fix>^`，`git status --porcelain` 为空 |

**文件分类规则（写死，按顺序首条命中即定类）：**

1. 路径含 `test` 段或 basename 匹配 `test_*.py` / `*_test.py`，或位于 `tests/` / `testing/` / `conftest.py` ⇒ **测试侧**（进 `test_patch`）；
2. 其余 `.py` ⇒ **非测试 `.py`**（进 `patch`）；
3. 非 `.py` 文件（CI、docs、changelog、toml/cfg、二进制）**一律丢弃**，不入任一侧；丢弃情况记入 `known_limits`。

> 注：`conftest.py` 归测试侧。若某提交只改 `conftest.py` + 源码而没有任何测试用例文件改动，
> 则 C1 的第二侧不成立（`conftest.py` 单独不算「测试文件」，须在分类规则第 1 条之后追加一句判定：
> **测试侧文件集合去掉 `conftest.py` 后仍非空**才算 C1 成立）。

### 3.2 排除规则（提交级，命中即弃该候选）

| 代号 | 排除条件 | 怎么判 |
|---|---|---|
| E0 | 基线对象不可达 | `git cat-file -e <fix>^{commit}` 失败 |
| E1 | 测试侧含删除测试/大规模 skip | `test_patch` 里 `-` 行中含 `skip`/`xfail`/删除整个测试函数 |
| E2 | 测试侧改动依赖新引入的 fixture/plugin 且不在仓库内 | 收集期报 `fixture '...' not found` 且该 fixture 未在 `test_patch` 中定义 |
| E3 | 任一侧 diff 为空 | 字符串长度为 0 |
| E4 | patch 触及超过 8 个源码文件或改动行数 > 400 行 | `git show --numstat` 求和（过大 ⇒ 更像重构而非定点修复，语义不清） |
| E5 | 推导出的 `FAIL_TO_PASS` 为空 | §4.2 结果为空集 |
| E6 | 收集错误 / pytest 崩溃 | exit code ≥ 2，或 `errors != 0` 且落在 `collection_scope` 内 |
| E7 | 单次测试耗时 > 60 s | 计时来自 §4.1 的原始输出 |
| E8 | flaky | §4.5 判定命中 |
| E9 | `problem_statement` 零信息量 | subject 命中黑名单正则 `^(wip|typo|bump|format|lint|update|fix(up)?\.?$)`，或全文（subject+body）< 20 个非空白字符 |
| E10 | 三向自校验不全对 | §6 任一不符 ⇒ 该实例 `rejected`（**这是排除，不是"调参让它过"**） |

### 3.3 构造流程（基线 = `commit^`）

```
1. clone/fetch 上游到 E:\aicoding\eval-cache\<repo_key>          （bare 或带工作树均可，永不落进 mini-claude-code 仓库）
2. base = git rev-parse <fix>^                                     → 写入 base_commit
3. 复制 base 的工作树到 E:\aicoding\eval-runs\<run_id>\<iid>\repo  （§8.1，目录副本，不用 git worktree 指向缓存）
4. patch      = git show <fix> -- <非测试 .py 路径...>              → gold，落 data_paths.patch_file
5. test_patch = git show <fix> -- <测试文件路径...>                  → 落 data_paths.test_patch_file
6. problem_statement = 提交信息原文（§3.4）
7. 按 §4 推导 F2P / P2P；按 §6 跑三向自校验
8. 全过 ⇒ set_membership = pilot-12 或 reserve（§7.3）
```

### 3.4 `problem_statement` 的生成规则（**与官方的差异，必须写明**）

- **来源**：修复提交的提交信息（subject + 空行 + body），原样保留，不翻译、不缩写、不扩写。
- **不做的事**：不抓取 issue/PR 正文、不链接 issue 编号对应的网页、不由人或模型代笔补充现象描述、不添加「提示应该改哪个文件」的任何内容。
- **与官方的差异声明**：官方 SWE-bench 的 `problem_statement` 取自 GitHub **issue 正文**；本评测取自**提交信息**。因此本报告的数字**不可与官方 leaderboard 或官方子集分数直接比较**（§10.3）。提交信息通常比 issue 更短、更接近实现层措辞，可能让任务显得更容易定位 ⇒ 这一偏差方向在结果节必须显式讨论。
- **泄漏检查（硬规则）**：若提交信息中出现 `FAIL_TO_PASS` 里任何一个 node id、或出现 `patch` 里被改动的**函数名/符号名**，则按 §3.2 E9 的同级纪律处理：**删除该句之后的文本才可用**；删完不足 20 字符 ⇒ 排除该候选。此检查由构造器自动执行，人工不得豁免。
  > 说明：官方也有「issue 文本可能泄漏答案」的问题；这里给出的是我们能实际执行的判据，而不是宣称已消除偏差。

---

## §4 FAIL_TO_PASS / PASS_TO_PASS 的推导与判定

### 4.1 三个观测点（同一工作区、同一 venv、同一命令模板）

| 观测点 | 工作区状态 | 用途 |
|---|---|---|
| **B**（baseline） | 基线，无任何补丁 | 建立「原本就绿」的集合 |
| **T**（test-only） | 基线 + `test_patch` | 找出「因为新测试而变红」的集合 |
| **G**（gold） | 基线 + `test_patch` + `patch` | 确认这些红能转绿 |

每个观测点都要跑两次（间隔至少一次进程重启）用于 §4.5 稳定性核对；原始 stdout/stderr + JUnit XML 全部归档到 `data_paths.build_log_dir`，**判定只认归档下来的结果，不认任何 agent 自述**。

### 4.2 `FAIL_TO_PASS`（写死的推导）

```
F2P = { n ∈ T 中结果为 failed 或 error 的 node id
        | B 中 n 存在且 passed
        | G 中 n 存在且 passed }
```
- 「B 中 n 存在且 passed」排除了「基线上本来就红的测试」——它们不属于本实例要修的东西；若数量异常多 ⇒ 记入 `known_limits` 并触发 §2.2 复查。
- 「G 中 n 存在且 passed」是纳入前的**强制实测门槛**：应用 test patch 指定测试变红，再应用 gold 后变红者必须变绿。
- 若 `F2P == ∅` ⇒ E5 排除。若 T 红但 G 仍红 ⇒ 该候选不满足「gold 真修好了它」，排除（等价 E10）。
- 名字含参数化 id 的（`test_x[a-b]`）按完整 node id 计入，不做前缀合并。

### 4.3 `PASS_TO_PASS`

```
候选池 = { n | B 中 n 存在且 passed }            （只在 collection_scope 内收集）
剔除    = { n | n ∈ F2P } ∪ { n | T 中 n 不为 passed } ∪ { n | G 中 n 不为 passed }
P2P     = 剔除后的集合，按 node id 字典序排序后取前 K 个，K = min(60, len(collection_scope 内全部 node id))
```
- **为什么截断**：P2P 太大 ⇒ 每条实例判定耗时线性膨胀，且回归噪声上升。截断规则是**纯字典序**，与结果无关，可复算。
- 被截断掉的数量记入 `known_limits`（`"P2P truncated to 60 of N"`）。
- `T` 或 `G` 中由绿转红的测试**绝不进 P2P**（那是回归信号，属于实例自身的问题，走 §3.2 排除路径）。
- 若 P2P 为空 ⇒ 允许，但必须在 `known_limits` 标注「无回归保护集」，且在结果节里把该实例的 resolved 强度单独说明。

### 4.4 匹配的严格度

- node id **字符串精确匹配**；不做大小写折叠、不做模糊匹配、不把「同类不同参数」视为等价。
- 若 G 中某 F2P node id 消失（改名/参数化空间变化）⇒ 视为未通过 ⇒ 该候选排除（E10），并在 `excluded_tests` 留痕。
- xfail/xpass/skipped 一律**不算 passed**（严格取 pytest 报告中的 `passed`）。

### 4.5 flaky 判定与标记

- 同一状态下重复跑 2 次（§4.1 的双跑），任一 node id 的两次结果不同 ⇒ 该 node id 标 flaky。
- 命中 F2P 或 P2P 的 flaky node ⇒ 整个候选按 **E8 排除**（不做「把它从集合里摘掉继续用」这种宽松处理，除非摘掉后 F2P 仍非空且规则允许，此时必须记 `flaky_known=true` + `flaky_evidence`）。
- `flaky_known=false` 只有在构造时两次全量一致、且 §9.2 的 3 次重复稳定验证通过后才允许写。

---

## §5 `resolved` 判据

### 5.1 唯一定义

对一次 agent 运行产出的补丁 `p`（由 harness 从工作区 diff 采集，**不接受 agent 自己贴的 diff 文本作为判定输入**）：

```
resolved(p) = True  ⟺  在「基线 + test_patch」的工作区上应用 p 后，
                        独立 pytest 进程报告：
                          · FAIL_TO_PASS 中每个 node id 结果 == passed，且
                          · PASS_TO_PASS  中每个 node id 结果 == passed
                        并且该次运行的 verdict_class == "agent_result"
```

四个条件缺一即 `not_resolved`。

### 5.2 谁来判（独立性要求）

- 判定必须由 **harness 自己启动的 pytest 子进程**完成（`subprocess` + 独立 cwd + 独立 venv 的 python），退出码与 JUnit XML 是唯一证据。
- **绝不采信 agent 自述**：agent 说「我跑过测试都绿了」「我认为已修复」对 verdict **零影响**；agent 自己的终端输出只作为 trace 存档，不参与判定。
- agent 的输出补丁由 harness 用 `git diff --binary` 从工作区采集；若 agent 未留下任何 diff ⇒ `empty_patch` ⇒ 直接 `not_resolved`（正常结局，不是 infra 错误）。
- 判定进程**不复用** agent 会话内的任何 shell；两者甚至不共享环境变量（§8.3）。

### 5.3 应用补丁失败怎么办

- `p` 无法 apply（冲突）⇒ `not_resolved`（这是 agent 的实质失败，计入分母）。
- `p` 里包含对测试文件的改动 ⇒ harness **剥离测试侧改动后再判定**（防止 agent 改测试来骗绿），并把「试图改测试」作为单独一列计入结果统计。剥离后为空 ⇒ `empty_patch` ⇒ `not_resolved`。
- 无论哪种情况，原始 pytest 输出与 diff 都要归档。

### 5.4 六分类 verdict（互斥，覆盖每次判定运行）

| verdict_class | 含义 | 计入 resolved 分母？ | 计作 agent 失败？ |
|---|---|---|---|
| `agent_result` | 判定链路健康，pytest 正常给出逐 node 结果 | ✅ | 按 §5.1 二分为 resolved / not_resolved |
| `empty_patch` | agent 什么都没改 | ✅ | 算 not_resolved（实质失败） |
| `patch_conflict` | 补丁无法应用 | ✅ | 算 not_resolved（实质失败） |
| `infra_error` | 网络不可达 / 依赖装不上 / venv 损坏 / pytest 退出码 ≥2 或收集错误 / 磁盘或权限失败 | ❌ **不计入分母** | ❌ **不计作 agent 失败** |
| `timeout` | 超过 §8.4 的超时上限（agent 阶段或判定阶段各自计时） | ❌ 不计入分母，但**单独报告 timeout 率** | ❌ 不计作 agent 失败 |
| `harness_bug` | §9 不变量被破坏（工作区不干净、hash 不匹配、并发串扰） | ❌ 该批次整体作废重跑 | ❌ |

- `infra_error` / `timeout` 的实例允许**有限重试**：同一实例最多 2 次 infra 重试；仍失败 ⇒ 该实例标 `excluded_infra`，批次规模相应减 1 并在结果节写明「12 条中有 m 条因基础设施被剔除」。
- **禁止**为了凑分母把 `infra_error` 改标成 `not_resolved`，也禁止反过来。分类由程序按上表判据决定，人工只能在结果节追加解释。

### 5.5 报告口径

- 主指标：`resolved_rate = resolved 数 / (agent_result + empty_patch + patch_conflict 数)`。
- 同时报告：分母构成、`infra_error`/`timeout`/`excluded_infra` 计数、以及「gold 三向自校验通过率」（应为 12/12）。

---

## §6 黄金三向自校验（harness 可用性门槛）

**目的**：证明 harness 判得准，然后才允许用它评 agent。三条都在**同一个实例、同一 venv** 上跑，全部由独立 pytest 判定。

| # | 向 | 施加的补丁 | 期望 verdict | 期望分类 |
|---|---|---|---|---|
| 1 | **gold** | `test_patch` + `patch` | `resolved` | `agent_result` |
| 2 | **空补丁** | `test_patch` + 零字节 diff | `not_resolved` | `empty_patch` |
| 3 | **错补丁** | `test_patch` + `wrong_patch` | `not_resolved` | `agent_result`（补丁能 apply，只是没修好） |

### 6.3 `wrong_patch` 的构造规则（写死，随实例冻结落盘）

按顺序取第一个可行者：

1. **无关文件法**：在该仓库 `collection_scope` 之外挑一个源码 `.py`，插入一行无害注释（`# eval wrong-patch probe`）——补丁可干净 apply，但对 F2P 零作用；
2. **改错函数法**：把 `patch` 中**最后一个 hunk** 替换为对同一文件另一个函数的无害改动（不影响任何被 import 的行为）；
3. **反向法**：把 `patch` 反向应用（`git apply -R`）得到「基线的缺陷更深一层」的补丁。

禁止手工挑选「看起来会让测试崩」的补丁 —— 目标是测 harness 的**判别力**，不是制造崩溃。

### 6.4 门槛与后果

- **三向全对 ⇒ harness 对该实例可用**；任一向不符 ⇒ harness/构造存在缺陷：
  - 第 1 向不过 ⇒ 实例本身不成立（gold 没修好）⇒ 排除该候选（E10）；
  - 第 2 向不过（空补丁被判 resolved）⇒ **harness 严重失效**：立即停止评测，修 harness，重跑全部三向；
  - 第 3 向不过（错补丁被判 resolved）⇒ harness 判别力不足（多半是 F2P 选得太松）⇒ 同样停机重修。
- 三向记录写入实例的 `gold_selfcheck`；结果节必须报告「三向自校验 12/12 通过」这一行的原始证据路径。
- **顺序硬约束**：三向自校验必须在 agent 评测之前完成并通过；未通过的批次不得发布任何 resolved 数字。

---

## §7 规模与跨仓库规则

### 7.1 第一段规模

- **12 条实例**，不多不少。凑不满 12 条 ⇒ 如实报告「可得 N 条（N<12）」并说明排除流水，**不许降低任何判据来凑数**。
- **跨 ≥ 3 个仓库**；单仓库最多 5 条（防单仓库分布主导结果）。
- 仓库优先级：优先选**已在第二阶段用过或有交集的生态**（如 more-itertools 系）以便横向讲故事，但**必须至少 1 个全新仓库**，避免「只在熟仓库上调过参数」。

### 7.2 候选池构建

- 每仓库扫描 §3.3 C3 时间窗内的全部提交，按 §3.1/§3.2 过滤，得到「通过全部筛选的候选序列」，**按 `(fix_commit_date 升序, instance_id 字典序)` 排序**。
- 候选池总数与各步排除计数必须落盘（`eval/instances/pilot-12-sampling-log.md`，由构造器生成、人不得改）。

### 7.3 抽样规则（写死于本文档，看到结果前不可更改）

```
对每个纳入仓库 r：
  quota_r = 3                                # 3 个仓库 × 3 条 = 9
  取排序后候选序列的前 quota_r 个通过 §6 三向自校验的候选
再从第 4 个候选仓库（若有）取排序后的前 3 个合格候选补足到 12；
若不足 12：依次从各仓库的第 4、5 个合格候选补足，单仓库上限 5。
```
- **选择完全由排序 + 资格决定**，与「这条看起来难/容易/好不好讲故事」无关。
- 不合格者跳过取下一个，跳过原因必须写进 sampling log。
- 允许预留 `reserve` 池：紧随其后的 3 个合格候选标 `reserve`，仅在正式实例被判 `excluded_infra` 时**按顺序**顶上（不许挑）。

### 7.4 禁止事项

- 禁止在看到任何 agent 评测结果后增删/替换实例；
- 禁止因某条实例「没被解决」而回溯修改它的 F2P/P2P 或 `problem_statement`；
- 禁止把 `rejected` 候选换个说法重新纳入。

### 7.5 例外通道（唯一允许的变更）

只有当发现**客观事实错误**（如 base_commit 不存在、patch 与 commit 不符、命中了未预见的 §2.2 排除项）时才允许替换实例，且必须：
① 在结果节新增「替换记录」小节，写明原 instance_id、替换原因、证据路径；
② 替补者从 `reserve` 池**按 §7.3 顺序**取，不得新造规则；
③ 替换数 > 2 时，本节规模声明改为「有效样本 N 条」并明示偏离。

---

## §8 隔离与并发方案

### 8.1 每实例独立工作区

- 路径：`E:\aicoding\eval-runs\<run_id>\<instance_id>\repo`（`run_id` = ISO 时间戳 + 批次标签）。
- **方式**：从 `E:\aicoding\eval-cache\<repo_key>` 的 `base_commit` 做**目录副本**（`git archive` 解包后再 `git init` + commit 到 base，或直接复制 working tree 后保留 `.git`）。
  - **明确不用 `git worktree` 指向缓存仓库**：worktree 会共享 `.git` 对象库与 refs，一旦被污染的 index/refs 会跨实例传播；宁可多花磁盘也要物理隔离。
- 同一实例的每次判定运行都在**新建的工作区**里进行；复用工作区必须先通过 §9.1 的清洁检查，否则删除重建。
- agent 阶段与判定阶段使用**同一个工作区**（agent 在此打补丁），但判定 pytest 由 harness 进程发起（§5.2）。

### 8.2 每实例独立 venv

```
D:\Anaconda3\envs\aicoding\python.exe -m venv --system-site-packages? NO
→ 实际命令：D:\Anaconda3\envs\aicoding\python.exe -m venv E:\aicoding\eval-venvs\<instance_id>-<pytag>
   （不带 --system-site-packages，保证站点包不泄漏）
   随后：<venv>\Scripts\python.exe -m pip install -r <repo 内依赖文件>   # 或 env_spec.install_cmd
```
- venv 与实例**一一对应**，命名含 `instance_id`，绝不共享。
- venv 创建日志（含 pip 输出的每个包版本）归档到 `data_paths.build_log_dir/venv.log`；判定阶段读取该文件核对版本一致。
- 装依赖需要联网 ⇒ 只在**建 venv 阶段**允许网络；agent 运行与 pytest 判定阶段不允许外网访问（无法强制断网时，按 §2.3 保守处理并在结果节声明「网络未强制隔离」这一局限）。

### 8.3 进程与环境隔离

- 并发单位 = 实例；由 **`concurrent.futures.ProcessPoolExecutor`** 调度（进程池，不是线程池），worker 之间不共享内存状态。
- 每个子进程显式设置：`cwd` = 该实例工作区；`env` 从最小基线构造（不透传父进程的 `PYTHONPATH`、`PYTEST_ADDOPTS`、`AGENT_REPO_ROOT`、`ANTHROPIC_*` 等）；`PYTHONIOENCODING=utf-8`（GBK 控制台的既有坑）。
- 每个子进程只允许读写自己的 `E:\aicoding\eval-runs\<run_id>\<instance_id>\` 与对应 venv；跨目录写视为 §9.3 违规。

### 8.4 并发与超时上限（写死数值）

| 项 | 上限 | 依据 |
|---|---|---|
| 并发实例数 | **3**（`max_workers=3`） | 本机磁盘 IO 与内存；venv 安装是 IO 密集，>3 会把 pytest 计时拖长造成系统性 timeout 偏置 |
| 单实例 agent 阶段墙钟 | **900 s** | 超时 ⇒ verdict_class=`timeout` |
| 单实例判定 pytest 墙钟 | **600 s**（内含单测试 120 s `--timeout`） | 同上 |
| 单实例总墙钟（含建 venv） | **2400 s** | 超限即 kill 并标 `timeout` |
| 全批次预算 | **8 小时** | 超预算停批，如实报告已完成部分 |

- 超时用 `subprocess` 计时 + 进程终止实现；kill 后必须清理该实例工作区与临时文件。
- ⚠️ Windows 上被强杀的进程以退出码 1 收尾，与 pytest「有失败」的退出码 1 **不可区分** ⇒ 判定必须**先查是否超时**（计时器标记），再看退出码；顺序颠倒会把 timeout 误记成 not_resolved。此规则写进 harness 验收。

### 8.5 状态落盘与断点续跑

- 每个实例一个状态文件：`E:\aicoding\eval-runs\<run_id>\<instance_id>\state.json`，字段：
  `{instance_id, stage, verdict_class, attempts, started_at, finished_at, workspace, venv, artifacts[], schema_sha256}`；
  `stage ∈ {pending, venv_ready, workspace_ready, agent_done, judged, failed_infra, excluded_infra}`。
- 批次级索引：`E:\aicoding\eval-runs\<run_id>\manifest.json`（run_id、批次参数、实例清单、每个 state 的摘要）。
- **断点续跑语义**：重启后按 manifest 遍历，`stage == judged` 且工件 hash 齐备 ⇒ **跳过**；其余从当前 stage 继续，但**判定阶段一律在新工作区重跑**（不从中间态复用可能被污染的工作区）。
- 幂等要求：同一 `run_id` 重跑不产生第二份判定结果，只覆盖同一 `state.json` 并保留历史副本 `state.<timestamp>.json`。
- 状态文件只由 harness 写；agent 无权写（§9.2）。

---

## §9 不变量（每条都有可执行的检查，违反即 `harness_bug`）

### 9.1 工作区清洁

- 每个实例**跑前与跑后**都要执行 `git status --porcelain`（在实例工作区内），**期望为空**。
  - 跑前为空 = 工作区刚从基线检出、未被上一次运行残留污染；
  - 跑后为空 = 本次运行没有把垃圾留在工作区（判定用的临时文件写在实例目录下的 `artifacts/`，不在 `repo/` 内）。
- 不为空 ⇒ 该次结果标 `harness_bug`，工作区删除重建，该实例重跑一次；连续两次不为空 ⇒ 停机排查。

### 9.2 判定只认独立 pytest + 重复稳定性

- **判定唯一来源是 harness 启动的独立 pytest**（§5.2）；任何 agent 自述、trace 里的 echo、人眼判断都不算。
- **同实例重复 3 次结论稳定**：对每条正式实例，在无 agent 参与的 gold 判定上重复 3 次（可分散在不同并发槽），三次 verdict 必须完全一致；不一致 ⇒ 该实例按 E8（flaky）移出正式集，从 reserve 顶替并记录。
- 对 agent 评测：同一实例同一配置的 3 次重复若 resolved 结论翻转，**如实报告为不稳定**（记 `resolved@k` 比例），不得取「最好的一次」当结论。

### 9.3 外部克隆不落进仓库 & 目录约定

- 三个仓外目录（本文档钉死，后续代码不得改用别处）：
  - 上游缓存 `E:\aicoding\eval-cache\`
  - 运行工作区 `E:\aicoding\eval-runs\`
  - venv `E:\aicoding\eval-venvs\`
- **`E:\aicoding\mini-claude-code` 内绝不出现外部仓库的克隆、venv、pytest 缓存、JUnit 报告、原始日志**。仓内只允许：`eval/PREREGISTRATION.md`、`eval/instances/*.jsonl`（不含大 diff 正文，只存路径+hash）、`eval/instances/*-sampling-log.md`、`eval/*.py`（代码）、`eval/README.md`。
- 检查：批次结束后在 `E:\aicoding\mini-claude-code` 跑 `git status --porcelain`，除有意提交的文档/代码外不得出现任何未跟踪的大体积数据目录；违例 ⇒ `harness_bug`。
- 若确需在仓内留小样本示例，必须 < 20 KB 且在 `eval/README.md` 点名。

### 9.4 可追溯性

- 每个数字都能回溯到一份归档的原始 pytest 输出 + 一个 `schema_sha256` 匹配的实例 JSON；
- 报告里的每条 resolved/not_resolved 结论必须附 `state.json` 路径；
- 缺证据的数字**不写进报告**。

### 9.5 冻结性

- 本文 §1–§9 提交后不得改写（§10 追加节除外）；
- 实例 JSON 一经纳入不得原地编辑 —— 需要变更就走 §7.5。

---

## §10 口径与非外推声明

### 10.1 三个 input token 口径（沿用第二阶段 `AB-SUBAGENT-V2.md` §0.2 / P0 的做法）

同一次运行同时存在三个不同的「input tokens」，**任何引用都必须点名是哪一个**：

| 口径 | 字段名 | 来源 | 用途 |
|---|---|---|---|
| 未缓存输入 | `input_uncached` | `model_usage[*].inputTokens` 合计 | **主指标**（与第二阶段 P0 一致） |
| 缓存读 | `cache_read` | `model_usage[*].cacheReadInputTokens` 合计 | 单列，解释成本结构 |
| 缓存写 | `cache_creation` | `model_usage[*].cacheCreationInputTokens` 合计 | 单列 |

- 另有第三个视角：SDK `ResultMessage.usage.input_tokens` 与 trace 逐回合 Σ —— 第二阶段已实测 trace 逐回合 Σ 属**另一口径**且曾出现 ≈2× 关系，故本报告**只用 `model_usage` 系三个口径**，trace 逐回合值仅作诊断，不进任何对比表格。
- 每个实例的 metrics 必须三口径齐备；缺任一 ⇒ 该实例的 token 数字整体作废（可以只报 resolved）。

### 10.2 两条成本口径

| 口径 | 定义 | 说明 |
|---|---|---|
| **父层（parent-side）** | 主 agent 会话自身的 usage 与 `cost_usd` | 与第二阶段 A/D 臂对比同源，可比方向 |
| **端到端（end-to-end）** | 父层 + 所有子进程/scout/子代理侧 usage 之和 | **scout / 子进程侧必须单列一行**，不许只给合并数 |

- 报告必须同时给两条口径的中位数与极差；**禁止**把父层的下降解读为端到端的节省（子进程侧可能反向增加）。
- 跨批次只允许比**方向**，不允许比绝对值（量具/模型/靶场任一变化即不可比）—— 沿用第二阶段 §1 的规则。

### 10.3 非外推声明（本报告的边界，必须原文出现在最终报告里）

1. **未运行官方 SWE-bench harness**：本评测用的是自研的容器无关 harness，与官方实现在依赖安装、测试选择、超时策略上都不同 ⇒ 数字与官方不可互换。
2. **未使用官方 Docker 镜像 / conda 环境**：环境由本机 `D:\Anaconda3\envs\aicoding\python.exe` + 每实例 venv 构造 ⇒ 环境相关失败（官方镜像里不会出现的）会体现在我们的 `infra_error` 率上，而不是 agent 能力上。
3. **`problem_statement` 来自提交信息而非 issue**（§3.4）：任务文本分布与官方不同，**难度偏向未知**（提交信息可能更贴近实现、更易定位，也可能信息更少、更难定位）。因此本报告分数**不得与官方 leaderboard 或他人 SWE-bench 子集分数并列比较**。
4. **单模型**：第一段只跑一个模型的一个配置 ⇒ 不构成「模型间比较」，也不支持任何「改进带来 X 个点提升」的说法，除非同批次内有第二个配置的对照运行。
5. **小样本**：n = 12 ⇒ Wilson 95% 置信区间宽度约 ±25–29 个百分点（p≈0.5 时）。**任何小于 ~15 个百分点的差异在本样本量下都不应被解读为效应**。
6. **跨仓库不代表全生态**：≥3 个「纯 Python + pytest + 免编译 + 免网络」的仓库是被我们的纳入规则（§2）筛出来的，天然偏向小型纯 Python 库 ⇒ 结论不外推到 numpy/pandas 类重编译仓库、不外推到 JS/Go 生态、不外推到企业单体仓库。
7. **实例由真实修复提交 revert 构造**：缺陷分布受「哪些提交同时改了源码和测试」这一筛选条件支配，不代表线上真实 bug 分布。
8. **不能证明的事**：本报告不能证明 agent 具备通用软件工程能力、不能证明其在官方基准上的表现、不能证明成本的长期趋势（缓存命中率随批次剧烈波动）。

### 10.4 追加结果的规矩

- 追加节编号从 §11 起（本文档占用 §0–§10；§10 的口径与声明属于冻结内容，结果节只引用不改写）。
- 每个追加节首行必须写「对应规则节：§X.Y」，并给出证据路径。
- 若结果与某条预注册规则冲突，处理方式是**如实报告冲突**，不是回头改规则。

---

## 附：本文档的自检清单（提交前逐项核）

- [x] §1 九个官方字段 + 扩展字段，逐字段「是什么 / 怎么判 / 判不了怎么办」
- [x] §2 仓库纳入 R1–R8 + 排除标准 + 兜底（判不了即排除）
- [x] §3 候选 C1–C4、文件分类规则、排除 E0–E10、构造流程、`problem_statement` 来源差异声明
- [x] §4 B/T/G 三观测点、F2P/P2P 集合公式、匹配严格度、flaky 规则
- [x] §5 resolved 四条件、独立 pytest、不采信 agent 自述、六分类且 infra 不计作 agent 失败
- [x] §6 gold/空/错三向 + wrong_patch 构造规则 + 三向全对方可用
- [x] §7 12 条 / ≥3 仓库 / 单仓库 ≤5 / 排序抽样规则 / 禁止看结果挑实例 / 例外通道
- [x] §8 独立工作区 + 独立 venv + ProcessPool + 状态落盘断点续跑 + 并发 3 / 超时数值
- [x] §9 跑前跑后 `git status --porcelain` 空、重复 3 次稳定、三个仓外目录、仓内不落外部数据
- [x] §10 三 input 口径 + 两成本口径（scout/子进程单列）+ 八条非外推声明
