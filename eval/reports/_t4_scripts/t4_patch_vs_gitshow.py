"""t4 复核：declared patch/test_patch 与 `git show <fix_commit> -- <paths>` 的正文差异溯源。

目标：回答「构造器落盘的补丁是不是就是修复提交的源码/测试部分」。
做法：把两者做逐行 unified diff，把差异行**分类**（index 行缩写 / 其它），
只有出现非 index 类差异才算真实分歧。
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys

GIT = [r"E:\Program Files\Git\cmd\git.exe", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]
INST = r"E:\aicoding\mini-claude-code\eval\instances"
OUT = r"E:\aicoding\eval-runs\t4\provenance"

# git show 默认缩成 7 位（core.abbrev）；构造器落盘的是完整 40 位
_INDEX_RE = re.compile(r"^index ([0-9a-f]+)\.\.([0-9a-f]+)(\s.*)?$")


def blocks(text):
    out, cur = [], None
    for line in (text or "").splitlines(keepends=True):
        if line.startswith("diff --git "):
            if cur is not None:
                out.append(cur)
            cur = [line]
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        out.append(cur)
    res = []
    for b in out:
        h = "".join(b)
        m = re.match(r"^diff --git a/(\S+) b/(\S+)", h)
        res.append(((m.group(2) if m else ""), h))
    return res


def normalize_index_lines(text: str):
    """把 index 行的 sha 截成 7 位（对齐 git show 的默认缩写），其余不动。"""
    lines = []
    for line in text.splitlines(keepends=True):
        m = _INDEX_RE.match(line.rstrip("\n"))
        if m:
            line = f"index {m.group(1)[:7]}..{m.group(2)[:7]}{m.group(3) or ''}\n"
        lines.append(line)
    return "".join(lines)


def classify(diff_lines):
    """把差异行分类。返回 {类别: [行]}。"""
    kinds = {"index_abbrev": [], "other": []}
    for l in diff_lines:
        if l.startswith("@@") or l.startswith("---") or l.startswith("+++"):
            continue
        body = l[1:]
        if body.startswith("index "):
            kinds["index_abbrev"].append(l)
        else:
            kinds["other"].append(l)
    return kinds


def pilot_ids():
    out = []
    for f in sorted(os.listdir(INST)):
        if not f.endswith(".json") or f.startswith("_"):
            continue
        o = json.load(open(os.path.join(INST, f), encoding="utf-8"))
        if o.get("set_membership") == "pilot-12":
            out.append(o["instance_id"])
    return out


def main():
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        try:
            iids = json.load(open(sys.argv[1], encoding="utf-8"))
        except UnicodeDecodeError:
            iids = json.load(open(sys.argv[1], encoding="utf-16"))
    elif len(sys.argv) > 1:
        iids = json.loads(sys.argv[1])
    else:
        iids = pilot_ids()
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for iid in iids:
        o = json.load(open(os.path.join(INST, iid + ".json"), encoding="utf-8"))
        rd = os.path.join(r"E:\aicoding\eval-cache", o["repo"].replace("/", "__"))
        for field in ("patch", "test_patch"):
            bl = blocks(o[field])
            paths = [p for p, _ in bl]
            p = subprocess.run(GIT + ["-C", rd, "show", "--format=", "--no-renames",
                                      o["fix_commit"], "--"] + paths,
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            real = p.stdout
            dec = o[field]
            dl = list(difflib.unified_diff(real.splitlines(True), dec.splitlines(True),
                                           "git_show", "declared", n=0))
            kinds = classify(dl)
            rows.append({
                "instance_id": iid, "field": field, "paths": paths,
                "git_show_rc": p.returncode,
                "raw_byte_equal": real.strip("\n") == dec.strip("\n"),
                "index_normalized_equal": normalize_index_lines(real).strip("\n") == dec.strip("\n"),
                "index_abbrev_diffs": len(kinds["index_abbrev"]),
                "other_diffs": kinds["other"][:20],
                "other_diff_count": len(kinds["other"]),
            })
    with open(os.path.join(OUT, "patch_vs_gitshow.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    bad = [r for r in rows if r["other_diff_count"] > 0]
    lines = []
    for r in rows:
        lines.append(f"{r['instance_id']:42s} {r['field']:11s} "
                     f"raw={str(r['raw_byte_equal']):5s} idxnorm={str(r['index_normalized_equal']):5s} "
                     f"index_diffs={r['index_abbrev_diffs']} other_diffs={r['other_diff_count']}")
        for d in r["other_diffs"]:
            lines.append("      OTHER: " + repr(d))
    lines.append("")
    lines.append(f"index 归一后仍不等（真实分歧）的条目数：{len(bad)}")
    for r in bad:
        lines.append(f"  - {r['instance_id']} / {r['field']}")
    with open(os.path.join(OUT, "patch_vs_gitshow.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()