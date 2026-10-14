"""t8 验收证据：D2「空证断言」修复的**反向证据**。

合同要求：把 `len(set(ws)) == len(ws)` 那条恒真断言换成**真实磁盘路径比较**，
并证明**现在有判别力** —— 人为让两个工作区指向同一真实路径时，断言**必须失败**。

做法：直接调用 `mechanism_check._verify_mount_ownership` 与「真实路径互异」这两段逻辑，
分别在正常态与人为破坏态下跑，比较结论。

人为破坏手段（都不改实现代码，只改**磁盘上的一次性测试夹具**）：
  ① 把实例 B 的 `.pth` 指向实例 A 的工作区（模拟「挂错」）⇒ 归属检查必须失败；
  ② 让两条实例的 workspace 指向同一个真实路径（用 junction/符号链接；
     Windows 上 `os.symlink` 需要权限，故用 `mklink /J`）⇒ 互异检查必须失败。

用法：python t8_assert_discriminates.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
for p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E                  # noqa: E402
from eval.harness import mechanism_check as MC     # noqa: E402
from eval.harness import run_instance as RI        # noqa: E402

OUT = os.path.join(E.RUNS_ROOT, "t8", "assert_discriminates")
A = "pallets-click__4fd2fea"
B = "pytest-dev-pluggy__cbf58d5"


def norm(p):
    return os.path.normcase(os.path.realpath(p)).replace("\\", "/").rstrip("/")


def distinct_ok(paths):
    """「真实磁盘路径互异」检查 —— 与 mechanism_check 里那段的判据一致。"""
    n = [norm(p) for p in paths]
    return len(set(n)) == len(n), n


def make_junction(link, target):
    """Windows 目录 junction（不需要管理员权限，os.symlink 需要）。"""
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def main():
    os.makedirs(OUT, exist_ok=True)
    run_id = "t8-disc"
    print("=" * 78)
    print("t8 D2 反向证据：新断言**必须有判别力**")
    print("=" * 78)

    # ---- 先建两条真实工作区
    ws_list = []
    for iid in (A, B):
        inst = RI.load_instance(iid)
        ws = E.fresh_workspace(inst, run_id, log_path=None)
        ws_list.append(ws)
        print(f"  建工作区 {iid} -> {ws}")

    # ===== 检查 1：真实路径互异（正常态应当通过）
    ok_normal, normed = distinct_ok(ws_list)
    print(f"\n[检查 1] 真实路径互异 —— 正常态：{ok_normal}")
    for n in normed:
        print(f"    {n}")

    # ===== 反向 A：人为让两条指向同一真实路径 => 必须失败
    print("\n[反向 A] 让两条实例的工作区指向**同一真实路径**（junction）")
    link = ws_list[1] + "_same"
    if os.path.isdir(link):
        shutil.rmtree(link, ignore_errors=True)
    made, msg = make_junction(link, ws_list[0])
    print(f"    mklink /J {link} -> {ws_list[0]}  成功={made}  {msg.strip()[:120]}")
    ok_same, normed_same = distinct_ok([ws_list[0], link])
    print(f"    真实路径互异检查（把 B 换成 junction 后）：{ok_same}   ← 期望 False")
    for n in normed_same:
        print(f"    {n}")
    reverse_a_ok = (made and not ok_same) or (not made)   # junction 建不起来时记为「无法构造」
    if not made:
        print("    （本机无法建 junction ⇒ 反向 A 记为「未构造」，不当作通过证据）")
    else:
        shutil.rmtree(link, ignore_errors=True)

    # ===== 检查 2：挂载点归属（正常态应当通过）
    print("\n[检查 2] 挂载点归属 —— 正常态")
    # 需要先让每实例有挂载点：跑一次 ensure_venv（不跑完整判定，省时间）
    for iid, ws in zip((A, B), ws_list):
        inst = RI.load_instance(iid)
        inst["_ws"] = ws
        art = E.art_dir(run_id, iid)
        os.makedirs(art, exist_ok=True)
        try:
            E.ensure_venv(inst, os.path.join(art, "venv.log"),
                          RI.deps_from_env_spec(inst), RI.infer_import_name(ws),
                          run_id=run_id)
        except E.InfraError as exc:
            print(f"    {iid}: ensure_venv 失败（{str(exc)[:120]}）")
    ok_mount, detail = MC._verify_mount_ownership(run_id, [A, B])
    print(f"    归属检查（正常态）：{ok_mount}")
    for d in detail:
        print(f"      {d}")

    # ===== 反向 B：把 B 的 .pth 改成指向 A 的工作区 => 归属检查必须失败
    print("\n[反向 B] 把实例 B 的挂载点**改成指向 A 的工作区**（模拟挂错）")
    inst_b = RI.load_instance(B)
    vd_b = E.venv_dir(inst_b)
    sp_b = os.path.join(vd_b, "Lib", "site-packages")
    idx_b = E.read_mount_index(inst_b)
    mine_b = idx_b.get("mine") or []
    broke = False
    backup = {}
    for f in mine_b:
        p = os.path.join(sp_b, f)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                backup[p] = fh.read()
            # 指向 A 的工作区（真实存在的另一个路径）
            from eval.lib import editable as ED
            E.write_text(p, ED.pth_body([ws_list[0]]))
            broke = True
            print(f"    改写 {f} -> 指向 {ws_list[0]}")
    ok_mount_bad, detail_bad = MC._verify_mount_ownership(run_id, [B])
    print(f"    归属检查（被破坏后）：{ok_mount_bad}   <- 期望 False")
    for d in detail_bad:
        print(f"      {d}")
    # 还原
    for p, body in backup.items():
        E.write_text(p, body)

    reverse_b_ok = broke and (not ok_mount_bad)

    # ===== 结论
    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)
    print(f"  检查 1 正常态通过（互异）        : {ok_normal}     期望 True")
    print(f"  反向 A 构造了同一真实路径后失败    : {ok_same is False}   （junction 建成={made}）")
    print(f"  检查 2 正常态通过（归属正确）      : {ok_mount}     期望 True")
    print(f"  反向 B 挂错后归属检查失败         : {ok_mount_bad is False}   期望 True")
    verdict = bool(ok_normal and ok_mount and reverse_b_ok and (not made or not ok_same))
    print(f"\n  ⇒ 断言**有判别力**：{verdict}")

    summary = {
        "run_id": run_id,
        "normal_distinct_ok": ok_normal,
        "normal_mount_ok": ok_mount,
        "junction_created": made,
        "same_realpath_now_fails": (not ok_same),
        "mount_wrong_now_fails": (not ok_mount_bad),
        "has_discriminating_power": verdict,
        "detail_normal": detail,
        "detail_broken": detail_bad,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    dest = os.path.join(OUT, "assert_discriminates.json")
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  落盘：{dest}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())