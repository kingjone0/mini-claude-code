"""mutate.py —— 变异测试：找出「哪些改动会被现有测试抓到」。

**为什么需要它**：给大靶场注入缺陷时，要求三条 ——
  ① 改动本身合理（像真会犯的错）
  ② **被现有测试确定抓住**（否则等于没注入）
  ③ 只影响少数测试（失败铺满一屏就失去"定位"的意义）
凭感觉挑容易挑到根本没人测的地方。**让测试自己回答哪条能中。**

做法：对每个候选改动，改一次 → 跑一次测试 → 记录失败数 → 改回去。

用法：
    python mutate.py --repo <目标仓库> --candidates <候选.json> [--test-cmd "..."]
    python mutate.py --repo <目标仓库> --candidates cand.json --timeout 900

候选文件（UTF-8 JSON）：
    [
      {"name": "chunked strict: != -> >",
       "file": "more_itertools/more.py",
       "old": "            if len(chunk) != n:",
       "new": "            if len(chunk) > n:"}
    ]

⚠️ 只做**等长或近等长**的单点替换；跑完一定还原（用原文覆盖，不依赖 git）。
⚠️ 文件读写一律显式 encoding="utf-8" —— 本机控制台是 GBK，走 shell 会双重编码损坏。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

# 复用公共模块里的 force_utf8_output —— 新程序一律在第一次 print 之前调用。
# ⚠️ 本文件第一版忘了加，print 一个 ✗ 就 UnicodeEncodeError 崩了（GBK 代码页）。
#    这正是把它放进公共模块的理由：**复制粘贴的兜底会被漏掉，import 的不会。**
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agent.config import force_utf8_output  # noqa: E402

DEFAULT_TEST_CMD = [
    r"D:\Anaconda3\envs\aicoding\python.exe",
    "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
]

SUMMARY_PAT = re.compile(r"(\d+) (failed|passed|error)")

# ⚠️ more-itertools 的测试大量用 pytest-subtests：失败行长这样 ——
#     SUBFAILED(expected=[(0, 1, 2)]) tests/test_recipes.py::SlidingWindowTests::test_islice_version
# **不是** `FAILED ...`。只认 FAILED|ERROR 会把「3 个子测试失败」报成
# 「一个名字都没有」，看起来像根本没失败（与陷阱 4 同型：两类事实压成一个数）。
FAILNAME_PAT = re.compile(r"^(?:FAILED|ERROR|SUBFAILED\S*)\b.*?(\S+::\S+)", re.M)

OUT_DIR = Path(__file__).resolve().parent / "out"


def _dump(path: Path, text: str) -> None:
    """把 pytest 的原始输出留一份。

    内存里那份一关进程就没了，而「失败测试叫什么名字」正是判断一处变异
    够不够格当靶场缺陷的关键证据（名字若直接等于函数名，这题就废了）。
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def parse_summary(out: str) -> tuple[int, int]:
    """从 pytest 的收尾统计行里取 (失败数, 通过数)。"""
    failed = passed = 0
    tail = out[-1500:]
    for m in SUMMARY_PAT.finditer(tail):
        n, kind = int(m.group(1)), m.group(2)
        if kind == "failed":
            failed = max(failed, n)
        elif kind == "passed":
            passed = max(passed, n)
    return failed, passed


def run_tests(repo: Path, cmd: list[str], timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "<超时>"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    force_utf8_output()  # 必须在任何 print 之前（控制台是 GBK，见上）

    argv = sys.argv[1:]
    if "--repo" not in argv or "--candidates" not in argv:
        print(__doc__)
        return 2
    repo = Path(argv[argv.index("--repo") + 1]).resolve()
    cands = json.loads(Path(argv[argv.index("--candidates") + 1]).read_text(encoding="utf-8"))
    timeout = int(argv[argv.index("--timeout") + 1]) if "--timeout" in argv else 900
    cmd = DEFAULT_TEST_CMD

    print(f"目标仓库: {repo}")
    print(f"候选数: {len(cands)}\n")

    # 先看工作区干不干净。不干净 = 这份代码已经被改过，那么"基线全绿"是假的，
    # 而且上次被强杀（Stop-Process）留下的那处变异会让后续结果整批不可比。
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()
    if dirty:
        print("  ⚠️ 目标仓库不是干净状态 —— 先还原再跑，否则测的是别人改过的代码：")
        print("      git checkout -- . && git clean -fd")
        for line in dirty.splitlines()[:10]:
            print(f"      {line}")
        return 4

    print("--- 先测基线（未变异时应全绿）---")
    t0 = time.time()
    code, out = run_tests(repo, cmd, timeout)
    base_failed, base_passed = parse_summary(out)
    print(f"  退出码={code}  失败={base_failed}  通过={base_passed}  用时 {time.time() - t0:.0f}s")
    _dump(OUT_DIR / "mutate-0-baseline.txt", out)
    if code != 0 or base_failed:
        print("  ⚠️ 基线不是全绿 —— 后面的变异结果不可比，先修基线。")
        return 3

    results = []
    for i, c in enumerate(cands, 1):
        path = repo / c["file"]
        original = path.read_text(encoding="utf-8")
        hits = original.count(c["old"])
        print(f"\n[{i}/{len(cands)}] {c['name']}")
        if hits != 1:
            print(f"  ✗ 跳过：old 在文件中出现 {hits} 次（必须恰好 1 次，否则不知道改了哪处）")
            results.append({"name": c["name"], "status": "skip", "hits": hits})
            continue

        try:
            path.write_text(original.replace(c["old"], c["new"], 1), encoding="utf-8")
            t0 = time.time()
            code, out = run_tests(repo, cmd, timeout)
            elapsed = time.time() - t0
        finally:
            # 无论成败都还原 —— 用原文覆盖，不依赖 git（目标仓库可能不在干净状态）
            path.write_text(original, encoding="utf-8")

        safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", c["name"])[:50]
        _dump(OUT_DIR / f"mutate-{i}-{safe}.txt", out)

        failed, passed = parse_summary(out)
        # 超时 ≠ 没抓到。超时说明这处变异让测试**挂住**了（可能是死循环），
        # 它既不是"被干净地抓住"，更不能记成"没抓到"——那是把两类完全不同的
        # 事实压成同一个数（见 CLAUDE.md 陷阱 4）。这类候选一律弃用。
        if code == -1:
            status = "超时(测试挂起)"
        elif failed > 0:
            status = "抓到"
        else:
            status = "**没抓到**"
        print(f"  {status}：失败 {failed}  通过 {passed}  用时 {elapsed:.0f}s")
        if failed:
            names = FAILNAME_PAT.findall(out)
            for n in names[:6]:
                print(f"      {n}")
            if len(names) > 6:
                print(f"      …另有 {len(names) - 6} 个")
        results.append({"name": c["name"], "status": status, "failed": failed,
                        "passed": passed, "seconds": round(elapsed)})

    print("\n" + "=" * 70)
    print(f"{'候选':<44}{'失败数':>8}")
    for r in results:
        n = r.get("failed", "-")
        if r.get("status") == "超时(测试挂起)":
            n = "超时"
        print(f"{r['name'][:43]:<44}{str(n):>8}")
    caught = [r for r in results if r.get("failed")]
    print(f"\n被抓住 {len(caught)} / {len(results)} 条")
    print("挑缺陷的标准：**被抓住** + 失败数**少**（多了就不是定位问题，是满屏红）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
