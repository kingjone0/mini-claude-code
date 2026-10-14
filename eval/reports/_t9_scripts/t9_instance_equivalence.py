"""t9 独立审查：实例构造等价性（不引用构造器任何函数，只用 git 原始输出）。

对每条抽样实例核对四件事：
  1. base_commit == `git rev-parse <fix_commit>^`（完整 40 位）
  2. gold `patch`  == `git show <fix> -- <非测试 .py 文件>`（内容等价，index 行缩写属允许差异）
  3. `test_patch`   == `git show <fix> -- <测试文件>`
  4. `FAIL_TO_PASS` 每个 node id 的「文件」在该提交的测试改动文件集内，
     且其函数名出现在 test_patch 的 `+` 行里（= 该测试由本提交新增/改动）

另：列出该提交改到的**非 .py 文件**（§3.1 分类规则第 3 条要求「丢弃并记入 known_limits」），
逐条核对实例的 `known_limits` 是否提到 —— 这是 captain 抽查 README.md 疑点的判据。

用法：python eval/reports/_t9_scripts/t9_instance_equivalence.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

GIT = r"E:\Program Files\Git\cmd\git.exe"
GIT_BASE = [GIT, "-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]
PROJ = r"E:\aicoding\mini-claude-code"
CACHE = r"E:\aicoding\eval-cache"
INST = os.path.join(PROJ, "eval", "instances")

SAMPLES = [
    "pallets-click__4fd2fea",
    "pallets-click__ac6a2ac",
    "more-itertools-more-itertools__1b7f033",
    "pytest-dev-pluggy__20d8143",
    "astanin-python-tabulate__5373ebf",
    "astanin-python-tabulate__9dedd45",
]


def git(repo, *args):
    p = subprocess.run(GIT_BASE + ["-C", repo] + list(args),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


# §3.1 的分类规则（**我自己照预注册原文实现**，不 import 构造器）
TEST_DIR_RE = re.compile(r"(^|/)(tests|test|testing)(/|$)")
TEST_BASE_RE = re.compile(r"(^|/)(test_[^/]+\.py|[^/]+_test\.py|conftest\.py)$")


def classify(path):
    p = path.replace("\\", "/")
    if TEST_DIR_RE.search(p) or TEST_BASE_RE.search(p):
        return "test"
    if p.endswith(".py"):
        return "src"
    return "other"


def strip_index(text):
    """删掉 `index abc..def` 行（缩写在构造期与复查期可能不同，属允许差异）。"""
    out = []
    for line in (text or "").splitlines():
        if line.startswith("index ") and re.match(r"^index [0-9a-f]{7,40}\.\.[0-9a-f]{7,40}", line):
            continue
        out.append(line.rstrip("\r"))
    return "\n".join(out).strip()


def norm_block(text):
    """归一化分块：按「文件块」排序比较顺序敏感性（git show 与构造器的顺序可能不同）。"""
    blocks, cur = [], None
    for line in (text or "").splitlines():
        if line.startswith("diff --git "):
            if cur is not None:
                blocks.append("\n".join(cur))
            cur = [line]
        elif cur is not None:
            cur.append(line.rstrip("\r"))
    if cur is not None:
        blocks.append("\n".join(cur))
    return sorted(blocks)


def f2p_provenance(f2p, tp_text, tp_files):
    """每个 F2P 的 node id 是否真的来自 test_patch 的测试改动。"""
    added_funcs = set()
    for line in tp_text.splitlines():
        if not line.startswith("+"):
            continue
        m = re.match(r"^\+\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
        if m:
            added_funcs.add(m.group(1))
    bad = []
    for n in f2p:
        parts = n.split("::")
        fpath = parts[0]
        fname = parts[-1].split("[")[0]
        if fpath not in tp_files:
            bad.append((n, f"文件 {fpath} 不在 test_patch 改动文件集"))
        elif fname not in added_funcs:
            bad.append((n, f"函数 {fname} 未出现在 test_patch 的 `+def` 行里"))
    return sorted(added_funcs), bad


def scan_all_other_files():
    """全部实例（23 条）：该提交改到的非 .py 文件 vs known_limits 是否点名。"""
    print("\n" + "=" * 100)
    print("全量扫描：commit 改到的非 .py 文件 是否被 §3.1 第 3 条要求记入 known_limits")
    print("=" * 100)
    rows = []
    for name in sorted(os.listdir(INST)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        with open(os.path.join(INST, name), encoding="utf-8") as f:
            inst = json.load(f)
        repo = inst["repo"]
        cache = os.path.join(CACHE, repo.replace("/", "__"))
        fix = inst["fix_commit"]
        rc, out, err = git(cache, "show", "--name-status", "--format=", fix)
        others = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and classify(parts[-1]) == "other":
                others.append(parts[-1])
        kl = " || ".join(inst.get("known_limits") or [])
        missing = [p for p in others if os.path.basename(p) not in kl]
        rows.append((inst["instance_id"], others, missing))
        o_txt = ",".join(others) if others else "[]"
        print(f"  {inst['instance_id']:<46} other={o_txt:<28}"
              f" 未记入 known_limits={missing if missing else '(无)'}")
    miss_any = [(i, m) for i, _o, m in rows if m]
    print(f"\n有非 .py 改动未被 known_limits 点名的实例数：{len(miss_any)}")
    for i, m in miss_any:
        print(f"  - {i}: {m}")
    return rows


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    print("=" * 100)
    print("t9 独立审查：实例构造等价性（只用 git 原始输出，不 import 构造器）")
    print("=" * 100)
    report = {}
    for iid in SAMPLES:
        with open(os.path.join(INST, f"{iid}.json"), encoding="utf-8") as f:
            inst = json.load(f)
        repo = inst["repo"]
        cache = os.path.join(CACHE, repo.replace("/", "__"))
        fix = inst["fix_commit"]
        rec = {"instance_id": iid, "repo": repo, "fix_commit": fix}
        print("\n" + "-" * 100)
        print(f"[{iid}]  repo={repo}  fix={fix}")
        if not os.path.isdir(os.path.join(cache, ".git")):
            print(f"  !! 缓存仓库不存在：{cache}")
            rec["error"] = "cache missing"
            report[iid] = rec
            continue

        rc, out, err = git(cache, "rev-parse", f"{fix}^")
        parent = out.strip()
        rc2, out2, err2 = git(cache, "rev-parse", fix)
        rec["fix_full"] = out2.strip()
        rec["parent"] = parent
        rec["base_commit"] = inst["base_commit"]
        rec["base_ok"] = (rc == 0 and parent == inst["base_commit"])
        print(f"  git rev-parse {fix}^  -> {parent}   exit={rc}")
        print(f"  instance.base_commit    -> {inst['base_commit']}")
        print(f"  1) base_commit == fix^ : {'PASS' if rec['base_ok'] else 'FAIL'}")

        rc, out, err = git(cache, "show", "--name-status", "--format=", fix)
        files = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                files.append((parts[0], parts[-1]))
        src_f, test_f, other_f = [], [], []
        for st, path in files:
            cls = classify(path)
            (src_f if cls == "src" else test_f if cls == "test" else other_f).append(path)
        rec["files"] = {"src": src_f, "test": test_f, "other": [p for _s, p in other_f] if other_f and isinstance(other_f[0], tuple) else other_f}
        print(f"  git show --name-status：src={src_f} test={test_f} other={rec['files']['other']}")

        # 2) gold patch
        rc, g_src, err = git(cache, "show", "--format=", fix, "--", *src_f) if src_f else (0, "", "")
        rec["gold_match"] = (strip_index(g_src) == strip_index(inst.get("patch", "")))
        rec["gold_sorted_match"] = (norm_block(g_src) == norm_block(inst.get("patch", "")))
        print(f"  2) patch == git show <fix> -- <src文件> : {rec['gold_match']}"
              f"（按文件块排序后 {rec['gold_sorted_match']}）"
              f"  字节 {len(g_src)} vs 实例 {len(inst.get('patch',''))}")

        # 3) test_patch
        rc, g_test, err = git(cache, "show", "--format=", fix, "--", *test_f) if test_f else (0, "", "")
        rec["test_match"] = (strip_index(g_test) == strip_index(inst.get("test_patch", "")))
        rec["test_sorted_match"] = (norm_block(g_test) == norm_block(inst.get("test_patch", "")))
        print(f"  3) test_patch == git show <fix> -- <测试文件> : {rec['test_match']}"
              f"（排序后 {rec['test_sorted_match']}）"
              f"  字节 {len(g_test)} vs 实例 {len(inst.get('test_patch',''))}")

        # 4) F2P 溯源
        added, bad = f2p_provenance(inst["FAIL_TO_PASS"], inst.get("test_patch", ""), set(test_f))
        rec["f2p_bad"] = bad
        rec["f2p_added_funcs"] = sorted(added)
        rec["f2p_ok"] = not bad
        print(f"  4) F2P 溯源：{len(inst['FAIL_TO_PASS'])} 条，test_patch 新增/改动的 def：{sorted(added)}")
        print(f"     不一致的 F2P：{bad if bad else '(无)'}  => {'PASS' if not bad else 'FAIL'}")

        # 5) 非 .py 文件 vs known_limits
        other_paths = [p for p in (rec["files"]["other"] or [])]
        kl = " || ".join(inst.get("known_limits") or [])
        missing = [p for p in other_paths if os.path.basename(p) not in kl]
        rec["other_files"] = other_paths
        rec["other_not_in_known_limits"] = missing
        print(f"  5) 被丢弃的非 .py 文件：{other_paths if other_paths else '(无)'}")
        print(f"     未出现在 known_limits 里的：{missing if missing else '(无)'}")
        print(f"     known_limits 原文：{kl[:600]}")
        report[iid] = rec

    scan_all_other_files()

    print("\n" + "=" * 100)
    print("汇总（6 条深查）")
    print("=" * 100)
    ok = True
    for iid, r in report.items():
        if r.get("error"):
            print(f"  {iid}: ERROR {r['error']}")
            ok = False
            continue
        flags = {
            "base^": r["base_ok"], "gold": r["gold_match"], "test": r["test_match"],
            "f2p": r["f2p_ok"], "other_in_kl": not r["other_not_in_known_limits"],
        }
        line = "  ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in flags.items())
        print(f"  {iid:<48} {line}")
        ok &= all(flags.values())
    print(f"\n全部通过：{ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
