"""t9 独立审查 第 5 条：mechanism_check 的**新并发断言**是否真有判别力（不是新的空证）。

做法（自己造反例，不引用 t8 的 junction 脚本）：
  ① 正常态：对一次真实并发批跑（t9-mech-parallel，我自己跑的）重算断言 => 期望 True
  ② 反例 A（**junction**）：造一个 run 目录，让实例 B 的工作区路径是实例 A 的 junction
     => 真实磁盘路径不再互异 => 断言必须**失败**
  ③ 反例 B（**挂载点被换掉**）：把某个实例 venv 里的 `.pth` 指向**另一个**实例的工作区
     => 归属检查必须**失败**
  ④ 对照：老的（D2 报的）空证表达式 `len(set(ws_path(...))) == len(ws_path(...))`
     在新路径下**恒为真** —— 用同一个 junction 场景证明它测不出任何东西

用法：python eval/reports/_t9_scripts/t9_assert_power.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

PROJ = r"E:\aicoding\mini-claude-code"
for p in (PROJ, os.path.join(PROJ, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

RUN_ID = "t9-mech-parallel"          # 我自己跑出来的并发批
IDS = ["pallets-click__4fd2fea", "pytest-dev-pluggy__cbf58d5",
       "more-itertools-more-itertools__1b7f033", "astanin-python-tabulate__9dedd45"]
SCRATCH = r"E:\aicoding\eval-runs\t9-assert-power"


def distinct_expr(paths):
    """新断言的核心表达式（与 mechanism_check.py:204-219 同形）。"""
    reals = [os.path.realpath(p) for p in paths]
    normed = [os.path.normcase(r) for r in reals]
    return len(set(normed)) == len(normed), normed


def old_vacuous_expr(paths):
    """老断言的表达式（D2 报的空证）：比的是拼出来的字符串。"""
    return len(set(paths)) == len(paths)


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    from eval.harness import env as E
    from eval.harness import mechanism_check as MC

    print("=" * 96)
    print("① 正常态（我自己跑的并发批 t9-mech-parallel）")
    print("=" * 96)
    paths = [E.ws_path(RUN_ID, i) for i in IDS]
    ok_distinct, normed = distinct_expr(paths)
    print(f"  新断言「真实磁盘路径互异」 = {ok_distinct}（{len(set(normed))} 互异 / {len(normed)}）")
    print(f"  老空证表达式结果             = {old_vacuous_expr(paths)}（恒真，与隔离无关）")
    mount_ok, detail = MC._verify_mount_ownership(RUN_ID, IDS)
    print(f"  新断言「挂载点归属正确」 = {mount_ok}")
    for line in detail:
        print("      " + line)

    print("\n" + "=" * 96)
    print("② 反例 A：junction 让两条路径指向同一真实目录")
    print("=" * 96)
    if os.path.isdir(SCRATCH):
        shutil.rmtree(SCRATCH, ignore_errors=True)
    a = os.path.join(SCRATCH, "instA", "repo")
    b = os.path.join(SCRATCH, "instB", "repo")
    os.makedirs(a, exist_ok=True)
    os.makedirs(os.path.dirname(b), exist_ok=True)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", b, a], capture_output=True, text=True)
    print(f"  mklink /J 退出码={r.returncode}  输出={r.stdout.strip()[:120]}{r.stderr.strip()[:120]}")
    fake_paths = [a, b]
    ok2, normed2 = distinct_expr(fake_paths)
    print(f"  新断言结果 = {ok2}（期望 **False**）   realpath(A)={os.path.realpath(a)}")
    print(f"                                       realpath(B)={os.path.realpath(b)}")
    print("  老空证表达式结果 = %s（期望 True —— 这就是「空证」的含义："
          "路径字符串不同、磁盘上是同一个目录，它照样报通过）" % old_vacuous_expr(fake_paths))

    print("\n" + "=" * 96)
    print("③ 反例 B：把 B 的挂载点改成指向 A 的工作区")
    print("=" * 96)
    victim = IDS[1]
    other = IDS[0]
    inst = json.load(open(os.path.join(PROJ, "eval", "instances", f"{victim}.json"),
                          encoding="utf-8"))
    pth = E.editable_pth_path(inst, RUN_ID)
    backup = open(pth, encoding="utf-8").read()
    print(f"  目标 .pth = {pth}")
    print(f"  原内容（前 120 字符）= {backup[:120]!r}")
    other_ws = E.ws_path(RUN_ID, other)
    try:
        with open(pth, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"import sys; sys.path.insert(0, {other_ws!r})\n")
        mount_ok2, detail2 = MC._verify_mount_ownership(RUN_ID, [victim])
        print(f"  改后归属检查 = {mount_ok2}（期望 **False**）")
        for line in detail2:
            print("      " + line[:200])
    finally:
        with open(pth, "w", encoding="utf-8", newline="\n") as f:
            f.write(backup)
        mount_ok3, _d3 = MC._verify_mount_ownership(RUN_ID, [victim])
        print(f"  已还原；还原后归属检查 = {mount_ok3}（期望 True）")

    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    good = ok_distinct and mount_ok and (not ok2) and (not mount_ok2) and mount_ok3
    print(f"  正常态通过 + 两个反例都被抓 + 还原后恢复 = {good}")
    out = os.path.join(SCRATCH, "assert_power.json")
    os.makedirs(SCRATCH, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"normal_distinct": ok_distinct, "normal_mount_ok": mount_ok,
                   "junction_distinct": ok2, "old_vacuous_on_junction":
                       old_vacuous_expr(fake_paths),
                   "tampered_mount_ok": mount_ok2, "restored_mount_ok": mount_ok3}, f,
                  ensure_ascii=False, indent=2)
    print(f"  证据：{out}")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
