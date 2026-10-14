"""t4 复核：venv 共享关系普查 + .pth 命名规则验证。

回答两件事：
  (1) 当前 23 条实例里，有没有两条会映射到**同一个 venv**？（若有 => 并发必然抢 .pth）
  (2) .pth 文件名到底由什么决定：构造出「同实例不同 run_id」与「同 base 不同实例」
      两种情形下的 .pth 路径，看是否相同。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

PROJ = r"E:\aicoding\mini-claude-code"
for p in (PROJ, os.path.join(PROJ, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E  # noqa: E402

INST = os.path.join(PROJ, "eval", "instances")
OUT = r"E:\aicoding\eval-runs\t4\venv_sharing"


def pth_path_of(inst, run_id):
    """复刻 run_instance.py:301 的命名规则（只依赖 instance_id）。"""
    venv = E.venv_dir(inst)
    digest = E.sha256_text(inst["instance_id"])[:16]
    return os.path.join(venv, "Lib", "site-packages", f"_eval_editable_{digest}.pth")


def main():
    os.makedirs(OUT, exist_ok=True)
    insts = []
    for f in sorted(os.listdir(INST)):
        if not f.endswith(".json") or f.startswith("_"):
            continue
        insts.append(json.load(open(os.path.join(INST, f), encoding="utf-8")))

    # (1) venv 共享分组
    groups = {}
    for o in insts:
        groups.setdefault(os.path.basename(E.venv_dir(o)), []).append(o["instance_id"])
    shared = {v: ids for v, ids in groups.items() if len(ids) > 1}

    print("=" * 78)
    print(f"(1) venv 共享普查：{len(insts)} 条实例 -> {len(groups)} 个 venv")
    print(f"    共享 venv 的分组：{shared if shared else '（无：每条实例各自一个 venv）'}")
    print("=" * 78)

    # (2) .pth 命名规则
    print()
    print("(2) .pth 路径命名规则验证")
    for o in insts[:2]:
        p1 = pth_path_of(o, "runA")
        p2 = pth_path_of(o, "runB")
        print(f"    实例 {o['instance_id']}")
        print(f"      run_id=runA -> {p1}")
        print(f"      run_id=runB -> {p2}")
        print(f"      路径相同（=> 并发会互相覆盖）：{p1 == p2}")

    # (3) 同一实例、不同 run_id：工作区不同但 .pth 相同
    print()
    print("(3) 关键对照：run_id 影响工作区路径，但**不影响** .pth 路径")
    o = insts[0]
    iid = o["instance_id"]
    for rid in ("t4-venv-A", "t4-venv-B"):
        print(f"    run_id={rid}")
        print(f"       工作区 = {E.ws_path(rid, iid)}")
        print(f"       .pth  = {pth_path_of(o, rid)}")

    res = {
        "instances": len(insts), "venv_count": len(groups),
        "shared_venv_groups": shared,
        "any_two_instances_share_venv": bool(shared),
        "pth_depends_only_on_instance_id": pth_path_of(o, "runA") == pth_path_of(o, "runB"),
        "sample_pth": pth_path_of(o, "runA"),
        "sample_workspaces": {r: E.ws_path(r, iid) for r in ("t4-venv-A", "t4-venv-B")},
    }
    with open(os.path.join(OUT, "venv_sharing.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print()
    print(f"  两条实例共享 venv：{res['any_two_instances_share_venv']}")
    print(f"  .pth 只依赖 instance_id（不含 run_id）：{res['pth_depends_only_on_instance_id']}")
    print(f"-> {os.path.join(OUT, 'venv_sharing.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())