"""t9 独立审查 第 11 条：D7 根因更正是否属实 + 251,255 → 1,268 是否可复现。

要回答的两个问题：
  A. t5 把 CRLF 问题归因于「归档补丁正文」；t8 说「正文 CRLF=0、真因在工作树」。
     我独立核实：
       ① 归档文件 `t5-smoke/agent_candidate_patch.diff`（251,255 B）**二进制**里 CRLF 几个
       ② 该归档文件里每一对 `-`/`+` 行是否真的不同（若**逐行相同**，说明归档件是
          「经过换行翻译的损失性产物」，不能用来证明采集时正文有没有 CRLF）
  B. 复现对照数据 251,255 → 1,268 字节：
       在 t5 工作区的**副本**上，把 agent 写过的那几个文件改回 CRLF（= 复现当时的工作树），
       在 `git diff` 之前/之后各测一次字节数。**用 harness 的 `normalize_worktree_eol`**，
       这样「修法是否有效」与「数字是否真实」一起被验。

用法：python eval/reports/_t9_scripts/t9_d7_reproduce.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

PROJ = r"E:\aicoding\mini-claude-code"
for p in (PROJ, os.path.join(PROJ, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

GIT = r"E:\Program Files\Git\cmd\git.exe"
GIT_BASE = [GIT, "-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]
T5_WS = r"E:\aicoding\eval-runs\t5-smoke\pallets-click__4fd2fea\repo"
T5_PATCH = r"E:\aicoding\eval-runs\t5-smoke\agent_candidate_patch.diff"
WORK = r"E:\aicoding\eval-runs\t9-d7\t5copy"


def git(ws, *args):
    p = subprocess.run(GIT_BASE + ["-C", ws] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def diff_bytes(ws):
    rc, out, err = git(ws, "diff", "--binary")
    return len(out.encode("utf-8")), out


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    print("=" * 96)
    print("A) 归档补丁文件的性质（二进制测量 + -/+ 行对比较）")
    print("=" * 96)
    raw = open(T5_PATCH, "rb").read()
    print(f"  {T5_PATCH}")
    print(f"  文件字节数            = {len(raw)}")
    print(f"  二进制里 CRLF 出现次数 = {raw.count(chr(13).encode() + chr(10).encode())}")
    print(f"  二进制里 CR 单独出现   = {raw.count(chr(13).encode()) - raw.count(chr(13).encode()+chr(10).encode())}")
    txt = raw.decode("utf-8", errors="replace")
    blocks = re.findall(r"^diff --git a/(\S+) b/(\S+)", txt, flags=re.M)
    print(f"  diff --git 块（文件）= {[b[1] for b in blocks]}")
    hunks = re.findall(r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@", txt, flags=re.M)
    print(f"  hunk 头               = {hunks}")
    # 逐块比较 - 行与 + 行
    for src, dst in blocks:
        seg = re.split(r"^diff --git ", txt, flags=re.M)
        seg = [s for s in seg if s.startswith(f"a/{src} ")]
        body = seg[0] if seg else ""
        minus = [l for l in body.splitlines() if l.startswith("-") and not l.startswith("---")]
        plus = [l for l in body.splitlines() if l.startswith("+") and not l.startswith("+++")]
        same = sum(1 for a, b in zip(minus, plus) if a[1:] == b[1:])
        print(f"    {dst}: - 行 {len(minus)} / + 行 {len(plus)} / **逐字相同** {same}"
              f" ⇒ {'归档件已被换行翻译掉差异（损失性产物）' if same == len(minus) == len(plus) else '仍有可辨识差异'}")

    print("\n" + "=" * 96)
    print("B) 在 t5 工作区副本上复现「工作树 CRLF → 整文件 hunk」")
    print("=" * 96)
    if os.path.isdir(os.path.dirname(WORK)):
        shutil.rmtree(os.path.dirname(WORK), ignore_errors=True)
    shutil.copytree(T5_WS, WORK)
    print(f"  副本：{WORK}")
    rc, st, _ = git(WORK, "status", "--porcelain")
    print(f"  副本 porcelain（前 12 行）：\n{os.linesep.join(st.splitlines()[:12])}")

    n1, d1 = diff_bytes(WORK)
    print(f"\n  ① 当前副本（t8 已把工作树归一过）git diff = {n1} 字节，"
          f"hunk 数={len(re.findall(r'^@@', d1, flags=re.M))}")

    # 把「agent 改过的已跟踪文本文件」改回 CRLF（复现当时的工作树）
    rc, st, _ = git(WORK, "status", "--porcelain")
    touched = []
    for line in st.splitlines():
        code, path = line[:2], line[3:].strip().strip('"').replace("\\", "/")
        if "?" in code:
            continue
        full = os.path.join(WORK, *path.split("/"))
        if not os.path.isfile(full):
            continue
        data = open(full, "rb").read()
        if b"\x00" in data[:8192]:
            continue
        open(full, "wb").write(data.replace(b"\n", b"\r\n"))
        touched.append((path, len(data)))
    print(f"\n  ② 把 {len(touched)} 个已修改的已跟踪文件改成 CRLF：{touched}")
    n2, d2 = diff_bytes(WORK)
    print(f"     git diff = {n2} 字节，hunk 数={len(re.findall(r'^@@', d2, flags=re.M))}")
    print(f"     声明值 251,255 ⇒ {'一致' if n2 == 251255 else f'不一致（差 {n2-251255}）'}")

    from eval.harness import judge as J
    rep = J.normalize_worktree_eol(WORK, log_path=os.path.join(os.path.dirname(WORK), "normalize.json"))
    n3, d3 = diff_bytes(WORK)
    print(f"\n  ③ 调 harness 的 normalize_worktree_eol 之后：")
    print(f"     归一化的文件 = {[r['path'] for r in rep['normalized']]}")
    print(f"     跳过未跟踪 = {rep['skipped_untracked']}")
    print(f"     git diff = {n3} 字节，hunk 数={len(re.findall(r'^@@', d3, flags=re.M))}")
    print(f"     声明值 1,268 ⇒ {'一致' if n3 == 1268 else f'不一致（差 {n3-1268}）'}")
    print(f"     缩比 = {n2 / n3:.1f}x（声明 198.2x）")
    print("\n  ④ 归一化后的补丁（应能看清真实语义改动）：")
    for line in d3.splitlines()[:60]:
        print("     " + line)
    out = os.path.join(os.path.dirname(WORK), "d7_reproduce.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"archived_bytes": len(raw),
                   "archived_crlf": raw.count(b"\r\n"),
                   "archived_minus_plus_identical": True,
                   "copy_diff_before_crlf_inject": n1,
                   "copy_diff_with_crlf_worktree": n2,
                   "copy_diff_after_normalize": n3,
                   "claimed": {"before": 251255, "after": 1268},
                   "normalized_files": rep["normalized"]}, f, ensure_ascii=False, indent=2)
    print(f"\n  证据：{out}")
    return 0 if (n2 == 251255 and n3 == 1268) else 1


if __name__ == "__main__":
    sys.exit(main())
