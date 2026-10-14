"""setup_hard_target.py —— 准备「硬靶场」：more-itertools 固定版本 + **3 处藏在私有辅助函数里的缺陷**。

━━ 为什么要第三个靶场：上一个靶场的快速路径没被堵住 ━━━━━━━━━━━━━━━━━

`more-itertools-multi` 上跑完 A/B 后回看运行日志，发现 12 次里有 6 次
第一步就是 `search_code {"regex": "^def (chunked|windowed|sieve|tail)\\b"}`
—— 一次命中。原因是**缺陷注入在公开函数里，而失败测试的类名就等于函数名**
（`ChunkedTests::test_strict_being_true` …），等于把答案写在报错里。
在那样的靶场上，"要不要把定位外包出去"这个需求根本没被造出来。

所以硬靶场只改一件事：**缺陷注入在私有辅助函数里**，让失败测试的名字
指向"正确的公开包装函数"，而 grep 那个名字**不会**命中缺陷。

  失败测试的名字（稳定）                                          真正的缺陷在
  DiscreteFourierTransformTests::test_basic / test_roundtrip      more.py    _complex_sumprod
  SlidingWindowTests::test_islice_version（3 个子测试）            recipes.py _sliding_window_islice
  IsliceExtendedTests::test_all / test_elements_lifecycle（27 个） more.py    _islice_helper

━━ 缺陷是怎么选的（全部来自变异测试，不是拍脑袋）━━━━━━━━━━━━━━━━━━━

`mutate.py` 的标准：**被测试抓住** + **失败数少**（失败铺满一屏就失去"定位"的意义）。
三处都在私有辅助函数里，且都**只有一个或两个消费者**：

  文件                        辅助函数                  变异                        失败数
  more_itertools/more.py     _complex_sumprod          r2 的实部/虚部对调              2
  more_itertools/recipes.py  _sliding_window_islice    islice(i,i) -> islice(i,i+1)    3
  more_itertools/more.py     _islice_helper            负 start: len_iter 少减 1      27
                                                                                  合计 32

被**否掉**的候选（都实测过，记在这里免得下次重踩）：
  · `_islice_helper` 的 `maxlen=-start + 1` —— 134 个失败，满屏红，失去定位意义。
  · `_extract_buffered` 的 `islice(advance-1) -> islice(advance)` —— 测试**挂住**
    （跑到 73% 卡死、900 s 超时），不是失败。mutate.py 现在把超时单列成一种状态。
  · `_islice_helper` 里 `len_iter=next(counter)-1` 的第一版锚点 —— 在文件里出现 2 次，
    被 `hits != 1` 拦下；改成带上一行的两行锚点才唯一。
  · `_shift_to_odd` —— 自己有 assert，任何变异都会踩断言，traceback 里直接出现
    辅助函数名，太容易，而且那是"响亮失败"不是"静默错值"。

━━ 关键设计：靶场的"坏"必须是它自己 HEAD 的不变量 ━━━━━━━━━━━━━━━━━━━━

见仓库 CLAUDE.md 陷阱 18。必须 clone 到固定 SHA → 注入 → **在目标仓库里提交一次**
→ 校验失败数。少了提交这一步，`coding_agent.py` 的 `reset_fixture()` 会把缺陷
一起还原掉，于是每轮都从"全绿"开始、判定"成功"，**而且不报错**。

━━ 为什么要往靶场里放一个 README.md ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

`coding_agent.py` 的 TASK（第 1 条硬约束）写的是"先读仓库根目录的 `README.md`"。
more-itertools 上游只有 `README.rst` —— 于是**每一次跑都白花一轮**去读一个不存在的
文件（上一轮 12/12 次都发生了）。约束本来就是 prompt 的一部分，所以这里补一个
README.md 把"允许改哪、不许改哪"写清楚，让那一轮不是白花的。

用法：
    python setup_hard_target.py                 # 默认位置
    python setup_hard_target.py --dest <路径>
    python setup_hard_target.py --check         # 只校验现状，不拉取不注入
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent.config import force_utf8_output  # noqa: E402

DEFAULT_DEST = Path(r"E:\aicoding\targets\more-itertools-hard")

TARGET = {
    "name": "more-itertools-hard",
    "url": "https://github.com/more-itertools/more-itertools.git",
    "sha": "81c21a8db1c922260cd673841257fb9b112c1318",
    "commit_msg": "靶场：注入 3 处已知缺陷（都在私有辅助函数里）\n\n"
                  "由 setup_hard_target.py 生成。三处缺陷都注入在只有一两个消费者的\n"
                  "私有辅助函数里（_complex_sumprod / _sliding_window_islice /\n"
                  "_islice_helper），失败测试的名字指向的是公开包装函数，grep 那个\n"
                  "名字不会命中缺陷。每处都先经 mutate.py 量过失败数（2 / 3 / 27,\n"
                  "合计 32），且都提交进 HEAD —— 否则 reset_fixture() 会把它们一起\n"
                  "还原掉。",
}

DEFECTS: list[dict] = [
    {
        "name": "_complex_sumprod 的 r2 实部/虚部顺序对调（dft 与 idft 共用）",
        "file": "more_itertools/more.py",
        "old": "    r2 = chain(map(real, v2), map(imag, v2))",
        "new": "    r2 = chain(map(imag, v2), map(real, v2))",
        "expect_failed": 2,
    },
    {
        "name": "_sliding_window_islice 每个 tee 分支多消费 1 个元素",
        "file": "more_itertools/recipes.py",
        "old": "        next(islice(iterator, i, i), None)",
        "new": "        next(islice(iterator, i, i + 1), None)",
        "expect_failed": 3,
    },
    {
        "name": "_islice_helper 负 start 分支的 len_iter 少减 1",
        "file": "more_itertools/more.py",
        "old": "            cache = deque(wrapper, maxlen=-start)\n"
               "            len_iter = next(counter) - 1",
        "new": "            cache = deque(wrapper, maxlen=-start)\n"
               "            len_iter = next(counter)",
        "expect_failed": 27,
    },
]

README_MD = """# more-itertools（硬靶场）

这是 [more-itertools](https://github.com/more-itertools/more-itertools) 在提交
`{sha}` 上的一份拷贝，**被人为注入了几处缺陷**，用来作为 mini Coding Agent 的
修复任务靶场。

## 任务

**让 `pytest` 全部通过。**

```bash
python -m pytest -q
```

初始状态下应当有 **{total_failed} 个子测试失败**，来自 **{n_defects} 处互相独立的缺陷**。

## 硬约束

| # | 约束 | 为什么 |
|---|---|---|
| 1 | **不要修改 `tests/` 下的任何文件** | 改测试让它通过是作弊，不是修复。验收会检查测试文件未被改动 |
| 2 | 只允许修改 `more_itertools/` 下的文件 | 限制改动面，让「是否修好」可判定 |
| 3 | 不要新增第三方依赖 | 靶场要能在任意环境跑起来 |
| 4 | 修完必须让测试真的通过 | 验收以 pytest 的真实退出码为准，不采信任何自述 |

## 结构

```
more_itertools/
├─ more.py          5644 行，绝大多数公开 API 在这里
├─ recipes.py       1627 行，itertools 官方文档里的那批 recipe
└─ __init__.py      它们都是 .recipes 的别名
tests/
├─ test_more.py     6327 行
└─ test_recipes.py  1464 行
```

## 提示（不含答案）

- 失败信息会指向**表象**，根因可能在它的上游：报错里出现的名字**不一定**是坏掉的那种函数。
- 每个函数的**契约写在 docstring 里**，以它为准。
- 缺陷不在同一处，也**不一定在同一个文件里**。
- 单文件很大（`more.py` 有 181,000 字符），一次读不完；先用 `search_code`
  或 `repo_map` 定位，再按区间读。
- 有的缺陷表现为**多输出一个元素**，有的表现为**从可迭代对象里多消费了一个元素** ——
  两者都会让测试变红，但改法不一样。

## 关于「重置靶场」

靶场有自己独立的 git 历史，缺陷已经提交进 HEAD。每轮实验前还原：

```bash
git -C <靶场路径> checkout -- .
git -C <靶场路径> clean -fd
```

这样「同一道题」可以被反复跑，指标才有可比性。
"""

PYTHON = sys.executable
TEST_CMD = [PYTHON, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"命令失败：{' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r


def total_expected() -> int:
    return sum(d["expect_failed"] for d in DEFECTS)


def clone(dest: Path) -> None:
    if (dest / ".git").is_dir():
        print(f"  已存在，跳过 clone：{dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  clone {TARGET['url']} → {dest}")
    run(["git", "clone", "--quiet", TARGET["url"], str(dest)])


def pin(dest: Path) -> None:
    print(f"  checkout {TARGET['sha'][:12]}")
    run(["git", "-C", str(dest), "checkout", "--quiet", TARGET["sha"]])


def write_readme(dest: Path) -> bool:
    """补上 README.md（约束说明）。TASK 的第 1 条硬约束要求它存在。"""
    path = dest / "README.md"
    body = README_MD.format(sha=TARGET["sha"], total_failed=total_expected(),
                            n_defects=len(DEFECTS))
    if path.is_file() and path.read_text(encoding="utf-8") == body:
        print("  README.md 已是当前内容，跳过")
        return False
    path.write_text(body, encoding="utf-8")
    print(f"  已写 README.md（{len(body)} 字符）")
    return True


def inject_all(dest: Path) -> list[str]:
    """注入全部缺陷。返回这次**真的改了**的缺陷名。

    ⚠️ 每个锚点都必须**恰好出现 1 次** —— 出现 0 次说明 SHA 或路径记错了，
    出现多次说明替换会打到别的地方。两种都不许静默放过。
    """
    applied: list[str] = []
    for d in DEFECTS:
        path = dest / d["file"]
        if not path.is_file():
            raise SystemExit(f"找不到目标文件：{path}（上游结构可能已变）")
        text = path.read_text(encoding="utf-8")
        # ⚠️ 判「是否已注入」**不能**只看 `d["new"] in text`：`new` 可能是 `old` 的前缀延伸。
        # 本例第 3 处缺陷的第二行 `len_iter = next(counter)` 正好是原文
        # `len_iter = next(counter) - 1` 的前缀 —— 子串判定会说"已经在里面了"，
        # 于是**静默跳过注入**：靶场少了 27 个失败，日志看起来却一切正常。
        # 正确的判据是"旧文本还在不在"。
        if d["old"] not in text and d["new"] in text:
            print(f"  [{d['name']}] 已是注入后的样子，跳过")
            continue
        hits = text.count(d["old"])
        if hits != 1:
            raise SystemExit(
                f"[{d['name']}] 锚点在 {d['file']} 中出现了 {hits} 次（应为恰好 1 次）。"
            )
        path.write_text(text.replace(d["old"], d["new"], 1), encoding="utf-8")
        print(f"  [{d['name']}] 已注入 {d['file']}")
        applied.append(d["name"])
    return applied


def commit(dest: Path) -> None:
    """把缺陷与 README.md 提交进目标仓库的 HEAD —— 这一步不能省。"""
    run(["git", "-C", str(dest), "add", "-A"])
    r = run(["git", "-C", str(dest), "commit", "-m", TARGET["commit_msg"]], check=False)
    if r.returncode == 0:
        print("  已提交（HEAD 现在持有'坏'的状态）")
    elif "nothing to commit" in (r.stdout + r.stderr):
        print("  无改动可提交（HEAD 已经是'坏'的状态）")
    else:
        raise SystemExit(f"提交失败：{r.stdout}\n{r.stderr}")


def failed_count(dest: Path) -> tuple[int, int, str]:
    r = subprocess.run(TEST_CMD, cwd=str(dest), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    out = (r.stdout or "") + (r.stderr or "")
    failed = passed = 0
    for m in re.finditer(r"(\d+) (failed|passed)", out[-1500:]):
        n, kind = int(m.group(1)), m.group(2)
        if kind == "failed":
            failed = max(failed, n)
        else:
            passed = max(passed, n)
    return failed, passed, out


def already_ready(dest: Path) -> bool:
    """靶场是否已处于「全部缺陷在文件里 + README.md 在 + 已提交」的状态。

    重跑本脚本时若不先判这一点，pin() 会把 HEAD 挪回上游 SHA（缺陷随之消失），
    随后注入 + 提交会造出一个**新的提交哈希** —— 而旧哈希会冻结在
    预注册与运行产物里，一挪就对不上了（陷阱 32）。
    """
    if not (dest / "README.md").is_file():
        return False
    for d in DEFECTS:
        path = dest / d["file"]
        if not path.is_file():
            return False
        # 判据同样是「旧文本还在不在」（见 inject_all 里的说明）
        if d["old"] in path.read_text(encoding="utf-8"):
            return False
    dirty = run(["git", "-C", str(dest), "status", "--porcelain"]).stdout.strip()
    return not dirty


def main() -> int:
    force_utf8_output()
    argv = sys.argv[1:]
    dest = Path(argv[argv.index("--dest") + 1]).resolve() if "--dest" in argv else DEFAULT_DEST
    check_only = "--check" in argv

    print(f"靶场：{TARGET['name']}  →  {dest}")
    print(f"固定提交：{TARGET['sha']}")
    print(f"缺陷 {len(DEFECTS)} 处，期望失败 {total_expected()} 个\n")

    if not check_only:
        if already_ready(dest):
            print("  已处于就绪状态（缺陷都在、README.md 在、工作区干净），跳过全部准备步骤")
        else:
            clone(dest)
            pin(dest)
            write_readme(dest)
            applied = inject_all(dest)
            if applied:
                commit(dest)
            else:
                print("  （缺陷都已在文件里，不提交）")

    print("\n--- 校验初始状态 ---")
    cur = run(["git", "-C", str(dest), "rev-parse", "HEAD"]).stdout.strip()
    dirty = run(["git", "-C", str(dest), "status", "--porcelain"]).stdout.strip()
    print(f"  HEAD: {cur}")
    print(f"  工作区: {'有改动' if dirty else '干净'}")

    failed, passed, out = failed_count(dest)
    print(f"  pytest: 失败 {failed}  通过 {passed}")
    for line in out.splitlines():
        # ⚠️ more-itertools 大量用 pytest-subtests，失败行是 SUBFAILED(...) 而不是 FAILED(...)。
        # 只认 FAILED|ERROR 会在校验步骤里一个名字都不打印，看起来像"没有失败"。
        if line.startswith(("FAILED", "ERROR", "SUBFAILED")):
            print(f"    {line.strip()[:120]}")

    if failed != total_expected():
        print(f"\n  ✗ 失败数 {failed} != 期望 {total_expected()}")
        print("    可能原因：①缺陷之间不独立（有交互）②靶场状态不对（被客户端跑过，先 git checkout 复位）③上游变了，核对 SHA。")
        return 1

    print(f"\n  ✓ 靶场就绪：恰好 {failed} 个失败（{len(DEFECTS)} 处缺陷，逐处预期 "
          f"{' + '.join(str(d['expect_failed']) for d in DEFECTS)} = {total_expected()}）")
    print(f"    缺陷已提交进 HEAD —— reset_fixture() 还原得掉、也保得住")
    print(f"\n  用 agent 跑：")
    print(f"    python coding_agent.py --fixture {TARGET['name']} --label <标签>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
