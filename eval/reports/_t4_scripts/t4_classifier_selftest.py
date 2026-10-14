"""自检：验证 normalize_index_lines 真的能把 index 行归一，
以及分类器不会把「index 差异」误算进 other。

用一个已知的最小样例，避免「分类器坏了 => 全 0 差异 => 假通过」。
"""

from __future__ import annotations

import difflib
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("t4p", os.path.join(HERE, "t4_patch_vs_gitshow.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["t4p"] = m
spec.loader.exec_module(m)

fails = []

# 1) normalize_index_lines 必须把 40 位缩成 7 位
long_idx = ("diff --git a/x.py b/x.py\n"
            "index 6986b6008176ca706ceb5811c96b0f1244db2da2..ad093b722585902b14faffeaa391c9a93fbcbe70 100755\n"
            "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n")
short_idx = ("diff --git a/x.py b/x.py\n"
             "index 6986b60..ad093b7 100755\n"
             "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n")
got = m.normalize_index_lines(long_idx)
print("normalize_index_lines ->", repr(got.splitlines()[1]))
if got != short_idx:
    fails.append("normalize_index_lines 没能把 index 行缩成 7 位")

# 2) 只有 index 差异时：index_normalized_equal 必须为 True，other_diffs 必须为 0
dl = list(difflib.unified_diff(long_idx.splitlines(True), short_idx.splitlines(True),
                               "a", "b", n=0))
k = m.classify(dl)
print("classify(only-index):", {kk: len(vv) for kk, vv in k.items()})
if len(k["other"]) != 0:
    fails.append(f"只有 index 差异时 other 应为 0，实为 {k['other']}")
if len(k["index_abbrev"]) != 2:
    fails.append(f"index 差异应被识别为 2 行（unified_diff 的 -/+ 各一行），实为 {k['index_abbrev']}")

# 3) 内容差异必须落进 other（负对照）
c1 = long_idx
c2 = long_idx.replace("+b\n", "+c\n")
dl2 = list(difflib.unified_diff(c1.splitlines(True), c2.splitlines(True), "a", "b", n=0))
k2 = m.classify(dl2)
print("classify(content-diff):", {kk: len(vv) for kk, vv in k2.items()})
if len(k2["other"]) == 0:
    fails.append("内容差异未被识别（分类器会漏报真实分歧 => 假通过）")

# 4) 构造器式「40 位 index + 相同正文」应与 7 位形态归一后相等
if m.normalize_index_lines(long_idx).strip("\n") != short_idx.strip("\n"):
    fails.append("归一后应相等")

print()
if fails:
    for f in fails:
        print("SELFTEST FAIL:", f)
    sys.exit(1)
print("SELFTEST PASS：分类器对 index 差异与内容差异都能正确区分")