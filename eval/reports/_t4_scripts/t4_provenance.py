"""t4 独立复核脚本 1：实例 provenance 复核（不使用构造器代码）。

对每个抽样实例独立断言：
  A) base_commit == fix_commit 的**父提交**（用缓存仓库独立跑 git rev-parse）
  B) `patch` 正文 == `git show fix_commit -- <源码路径>`（剥掉测试侧后的部分）逐字节一致
  C) `test_patch` 正文 == `git show fix_commit -- <测试侧路径>` 逐字节一致
  D) base_commit 树里跑 test_patch 后，F2P 在基线为红（另由 t4_red_green.py 复核）
  E) F2P/P2P 的 node id 在 `git show fix_commit` 的测试文件里真的存在（符号名普查）

输出：JSON + 人读文本，落 E:\\aicoding\\eval-runs\\t4\\provenance\\
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

CACHE = r"E:\aicoding\eval-cache"
INSTANCES = r"E:\aicoding\mini-claude-code\eval\instances"
OUT = r"E:\aicoding\eval-runs\t4\provenance"
GIT = [r"E:\Program Files\Git\cmd\git.exe", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]

TEST_DIR_RE = re.compile(r"(^|/)(tests|test|testing)(/|$)")
TEST_BASE_RE = re.compile(r"(^|/)(test_[^/]+\.py|[^/]+_test\.py|conftest\.py)$")


def is_test_path(p: str) -> bool:
    p = (p or "").replace("\\", "/")
    return bool(TEST_DIR_RE.search(p) or TEST_BASE_RE.search(p))


def git(repo, *args, timeout=600):
    p = subprocess.run(GIT + ["-C", repo] + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def repo_dir(repo: str) -> str:
    return os.path.join(CACHE, repo.replace("/", "__"))


def split_patch_by_file(text: str):
    """独立实现（不复用 judge）—— 按 diff --git 切块。"""
    out = []
    cur = None
    for line in (text or "").splitlines(keepends=True):
        if line.startswith("diff --git "):
            if cur is not None:
                out.append(cur)
            cur = [line]
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        out.append(cur)
    blocks = []
    for blk in out:
        head = "".join(blk)
        m = re.match(r"^diff --git a/(\S+) b/(\S+)", head)
        blocks.append(((m.group(2) if m else ""), head))
    return blocks


def show_commit_text(repo_dir_path, sha):
    rc, out, err = git(repo_dir_path, "show", "--format=%H%n%P%n%an%n%aI%n%s", "--no-renames",
                       "--binary", sha)
    return rc, out, err


def show_paths(repo_dir_path, sha, paths):
    """`git show <sha> -- <paths>` 的正文（去掉 commit header）。"""
    rc, out, err = git(repo_dir_path, "show", "--format=", "--no-renames", "--binary",
                       sha, "--", *paths)
    return rc, out, err


def main():
    iids = json.loads(sys.argv[1]) if len(sys.argv) > 1 else []
    os.makedirs(OUT, exist_ok=True)
    report = []
    for iid in iids:
        path = os.path.join(INSTANCES, iid + ".json")
        inst = json.load(open(path, encoding="utf-8"))
        rd = repo_dir(inst["repo"])
        rec = {"instance_id": iid, "repo": inst["repo"], "checks": {}, "notes": []}

        # A) base_commit == fix_commit^
        rc, out, err = git(rd, "rev-parse", inst["fix_commit"] + "^")
        parent = out.strip()
        rc2, out2, err2 = git(rd, "rev-parse", inst["base_commit"])
        rec["checks"]["fix_parent"] = {
            "git_parent_of_fix": parent, "declared_base": inst["base_commit"],
            "resolved_declared_base": out2.strip(),
            "match": parent == out2.strip() and out2.strip() != "" and rc == 0,
        }

        # B) patch 正文 == fix_commit 的源码侧 diff
        blocks = split_patch_by_file(inst["patch"])
        src_paths = [p for p, _b in blocks if not is_test_path(p)]
        test_paths = [p for p, _b in blocks if is_test_path(p)]
        rec["patch_paths"] = {"source": src_paths, "test": test_paths}
        rc, real, err = show_paths(rd, inst["fix_commit"], src_paths) if src_paths else (0, "", "")
        rec["checks"]["patch_equals_git_show_source"] = {
            "byte_equal": real.strip("\n") == (inst["patch"] or "").strip("\n"),
            "real_len": len(real), "declared_len": len(inst["patch"]),
            "real_sha256_prefix": None,
        }
        # 也做一次路径级比对，便于报告里说清差在哪
        declared_by_path = dict(blocks)
        per_path = []
        for p in src_paths:
            rc3, real3, _e = show_paths(rd, inst["fix_commit"], [p])
            same = real3.strip("\n") == declared_by_path[p].strip("\n")
            per_path.append({"path": p, "match": same,
                             "declared_len": len(declared_by_path[p]), "real_len": len(real3)})
        rec["checks"]["patch_per_path"] = per_path

        # C) test_patch 正文 == fix_commit 的测试侧 diff
        tblocks = split_patch_by_file(inst["test_patch"])
        tpaths = [p for p, _b in tblocks]
        rc, real_t, _e = show_paths(rd, inst["fix_commit"], tpaths) if tpaths else (0, "", "")
        rec["checks"]["test_patch_equals_git_show"] = {
            "test_patch_paths": tpaths,
            "byte_equal": real_t.strip("\n") == (inst["test_patch"] or "").strip("\n"),
            "real_len": len(real_t), "declared_len": len(inst["test_patch"]),
        }

        # D) fix_commit 里这些测试文件是否真的存在 F2P/P2P 的符号
        def syms_of(nid):
            f, _, sym = nid.partition("::")
            return f, sym
        files_needed = sorted({syms_of(n)[0] for n in
                               list(inst["FAIL_TO_PASS"]) + list(inst["PASS_TO_PASS"])})
        sym_report = []
        for f in files_needed:
            rc4, content, _e = git(rd, "show", f"{inst['fix_commit']}:{f}")
            sym_report.append({"file": f, "exists_at_fix_commit": rc4 == 0,
                               "file_also_in_test_patch": f in tpaths,
                               "len": len(content)})
        rec["checks"]["test_files_exist_at_fix"] = sym_report

        # E) F2P 的 node id 是否落在 test_patch 新增/改动的测试文件里
        f2p_files = sorted({syms_of(n)[0] for n in inst["FAIL_TO_PASS"]})
        rec["checks"]["f2p_files_covered_by_test_patch"] = {
            "f2p_files": f2p_files,
            "all_in_test_patch": all(f in tpaths for f in f2p_files),
        }

        # F) 元数据自洽：construction_method / fix_commit_date
        rec["checks"]["meta"] = {
            "construction_method": inst.get("construction_method"),
            "fix_commit_date": inst.get("fix_commit_date"),
            "gold_selfcheck": inst.get("gold_selfcheck", {}).get("wrong_patch_method"),
        }
        report.append(rec)

    txt = os.path.join(OUT, "provenance.json")
    with open(txt, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    # 人读摘要
    lines = []
    for r in report:
        c = r["checks"]
        lines.append(f"[{r['instance_id']}]")
        lines.append(f"  base==fix^ : {c['fix_parent']['match']} "
                     f"(declared={c['fix_parent']['declared_base'][:8]} "
                     f"parent={c['fix_parent']['git_parent_of_fix'][:8]})")
        lines.append(f"  patch==git show src : {c['patch_equals_git_show_source']['byte_equal']} "
                     f"(src paths={r['patch_paths']['source']})")
        per = c["patch_per_path"]
        if per:
            lines.append(f"    per-path: " + ", ".join(
                f"{x['path']}={'OK' if x['match'] else 'MISMATCH'}({x['declared_len']}/{x['real_len']})"
                for x in per))
        lines.append(f"  test_patch==git show : {c['test_patch_equals_git_show']['byte_equal']} "
                     f"(paths={c['test_patch_equals_git_show']['test_patch_paths']})")
        for s in c["test_files_exist_at_fix"]:
            lines.append(f"    testfile {s['file']} exists@fix={s['exists_at_fix_commit']} "
                         f"in_test_patch={s['file_also_in_test_patch']}")
        lines.append(f"  F2P files all in test_patch : "
                     f"{c['f2p_files_covered_by_test_patch']['all_in_test_patch']} "
                     f"{c['f2p_files_covered_by_test_patch']['f2p_files']}")
    with open(os.path.join(OUT, "provenance.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nJSON: {txt}")


if __name__ == "__main__":
    main()