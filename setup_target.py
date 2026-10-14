"""setup_target.py —— 拉取并准备「大靶场」：more-itertools 的**固定版本 + 注入缺陷**。

━━ 为什么目标仓库放在本仓库之外 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  · 那是第三方代码（MIT）。vendor 进来既臃肿，又把"谁的代码"搞混。
  · 它有自己独立的 git 历史 —— 而我们要的**正是这个**：
    `git checkout` 能把 agent 的改动还原掉。

━━ 关键设计：靶场的"坏"必须是它自己 HEAD 的不变量 ━━━━━━━━━━━━━━━━━━━━

见本仓库 CLAUDE.md 陷阱 18。流程必须是三步，缺一不可：

  ① clone 到**固定 SHA**（可复现；上游改了也不影响）
  ② 注入缺陷
  ③ **在目标仓库里提交一次**，让 HEAD 持有"坏"的状态
  ④ 校验：跑一次测试，断言失败数 == 期望值

**没有第 ③ 步的话**，`coding_agent.py` 的 `reset_fixture()` 用 `git checkout`
还原时会**把注入的缺陷一起还原掉** —— 于是每轮都从"全绿"开始，
基线 0→0、判定"成功"，**而且不报错**。这个失效模式已经踩过一次。

━━ 为什么缺陷是这一处 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

不是拍脑袋选的，是**变异测试**结果（`mutate.py` + `candidates-more-itertools.json`）：

  候选                                       失败数
  chunked strict: != -> >                       1
  windowed: n<=0 -> n<0                         1
  windowed: step<1 -> step<0                    **0（没抓到 —— 上游没有 step=0 的测试）**
  windowed: padding step-1 -> step              8
  windowed: 首窗 <n -> <=n                     24
  **windowed: 填充数 n-len -> n-len+1         2**  <= 选它

选它的理由：这是**输出错误**（多补一个 None），比"少抛一个异常"更像真 bug；
而且只挂 2 个测试 —— 失败铺满一屏就失去"定位"的意义。

用法：
    python setup_target.py                      # 默认位置
    python setup_target.py --dest <路径>
    python setup_target.py --check              # 只校验现状，不拉取不注入
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent.config import force_utf8_output  # noqa: E402

DEFAULT_DEST = Path(r"E:\aicoding\targets\more-itertools")

TARGET = {
    "name": "more-itertools",
    "url": "https://github.com/more-itertools/more-itertools.git",
    # 固定到这个提交。上游之后怎么改都不影响本实验的可复现性。
    "sha": "81c21a8db1c922260cd673841257fb9b112c1318",
    "file": "more_itertools/more.py",
    # 缺陷：首窗不满时**多补了一个 fillvalue**。
    # windowed([1,2,3], 4) 契约是 (1,2,3,None)，改后变成 (1,2,3,None,None)。
    "old": "    if len(window) < n:\n"
           "        yield tuple(window) + ((fillvalue,) * (n - len(window)))\n"
           "        return",
    "new": "    if len(window) < n:\n"
           "        yield tuple(window) + ((fillvalue,) * (n - len(window) + 1))\n"
           "        return",
    "expect_failed": 2,
    "commit_msg": "靶场：注入已知缺陷（windowed 首窗填充多一个 fillvalue）",
}

PYTHON = sys.executable
TEST_CMD = [PYTHON, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"命令失败：{' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r


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


def inject(dest: Path) -> bool:
    """注入缺陷。返回 True 表示这次真的改了。"""
    path = dest / TARGET["file"]
    if not path.is_file():
        raise SystemExit(f"找不到目标文件：{path}（上游结构可能已变）")

    text = path.read_text(encoding="utf-8")
    if TARGET["new"] in text:
        print("  缺陷已在文件里，跳过注入")
        return False
    hits = text.count(TARGET["old"])
    if hits != 1:
        raise SystemExit(
            f"锚点在 {TARGET['file']} 中出现了 {hits} 次（应为恰好 1 次）。\n"
            f"上游代码在这个 SHA 上应当只有一处 —— 若不然，说明路径或 SHA 记错了。"
        )
    path.write_text(text.replace(TARGET["old"], TARGET["new"], 1), encoding="utf-8")
    print("  已注入缺陷")
    return True


def commit(dest: Path) -> None:
    """把缺陷提交进目标仓库的 HEAD —— 这一步不能省，见模块开头的说明。"""
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
                       encoding="utf-8", errors="replace", timeout=900)
    out = (r.stdout or "") + (r.stderr or "")
    failed = passed = 0
    import re
    for m in re.finditer(r"(\d+) (failed|passed)", out[-1500:]):
        n, kind = int(m.group(1)), m.group(2)
        if kind == "failed":
            failed = max(failed, n)
        else:
            passed = max(passed, n)
    return failed, passed, out


def already_ready(dest: Path) -> bool:
    """靶场是否已处于「缺陷在文件里，且已提交进 HEAD」的状态。

    重跑 setup_target.py 时若不先判这一点，pin() 会把 HEAD 从缺陷提交挪回上游 SHA
    （缺陷随之消失），随后 inject + commit 会造出一个**新的提交哈希** —— 而
    d21dd31c3f1bde3a9a5de9e044bff7b2045af465 冻结在 README.md、AB-REPO-MAP.md、
    VERIFY-agent.md 与 12 份运行产物里，一挪就对不上了。
    """
    path = dest / TARGET["file"]
    if not path.is_file():
        return False
    if TARGET["new"] not in path.read_text(encoding="utf-8"):
        return False
    dirty = run(["git", "-C", str(dest), "status", "--porcelain"]).stdout.strip()
    return not dirty


def main() -> int:
    force_utf8_output()
    argv = sys.argv[1:]
    dest = Path(argv[argv.index("--dest") + 1]).resolve() if "--dest" in argv else DEFAULT_DEST
    check_only = "--check" in argv

    print(f"靶场：{TARGET['name']}  →  {dest}")
    print(f"固定提交：{TARGET['sha']}\n")

    if not check_only:
        if already_ready(dest):
            print("  已处于就绪状态（缺陷已在文件里、工作区干净），跳过 clone/checkout/注入/提交")
        else:
            clone(dest)
            pin(dest)
            changed = inject(dest)
            if changed:
                commit(dest)
            else:
                print("  （未改动，不提交）")

    print("\n--- 校验初始状态 ---")
    cur = run(["git", "-C", str(dest), "rev-parse", "--short", "HEAD"]).stdout.strip()
    dirty = run(["git", "-C", str(dest), "status", "--porcelain"]).stdout.strip()
    print(f"  HEAD: {cur}   工作区: {'有改动' if dirty else '干净'}")

    failed, passed, out = failed_count(dest)
    print(f"  pytest: 失败 {failed}  通过 {passed}")
    for line in out.splitlines():
        if line.startswith(("FAILED", "ERROR")):
            print(f"    {line.strip()}")

    if failed != TARGET["expect_failed"]:
        print(f"\n  ✗ 失败数 {failed} != 期望 {TARGET['expect_failed']}")
        print("    靶场状态不对。若客户端跑过，用 git checkout 复位；若上游变了，核对 SHA。")
        return 1

    print(f"\n  ✓ 靶场就绪：恰好 {failed} 个失败，且缺陷已在 HEAD 里（reset 还原得掉、也保得住）")
    print(f"\n  用 agent 跑：")
    print(f"    python coding_agent.py --fixture {TARGET['name']} --label <标签>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
