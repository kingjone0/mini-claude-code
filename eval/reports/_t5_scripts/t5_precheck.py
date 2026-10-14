"""t5 冒烟前的前置检查：候选清单、venv/.pth 现状、工作区清洁度。

只读，不改任何实现代码，也不改任何实例定义。
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
for p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E          # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402

print("=" * 78)
print("t5 冒烟前置检查")
print("=" * 78)

print("\n--- pilot-12 候选（选择依据要写进报告）---")
rows = []
for p in sorted(glob.glob(os.path.join(RI.INSTANCES_DIR, "*.json"))):
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    if d.get("set_membership") != "pilot-12":
        continue
    install = d["env_spec"].get("install_cmd") or []
    install_str = install if isinstance(install, str) else " ".join(install)
    rows.append({
        "instance_id": d["instance_id"],
        "repo": d["repo"],
        "f2p": len(d["FAIL_TO_PASS"]),
        "p2p": len(d["PASS_TO_PASS"]),
        "scope": d["collection_scope"],
        "base": d["base_commit"][:8],
        "deps": RI.deps_from_env_spec(d),
        "source": (d["env_spec"].get("requirements_source") or ""),
        "install_len": len(install_str),
    })
for r in rows:
    print(f"  {r['instance_id']:<42} F2P={r['f2p']:<2} P2P={r['p2p']:<2} "
          f"scope={str(r['scope']):<12} base={r['base']} deps={r['deps']}")

print("\n--- 现有 venv（检查 D1 命名规则与是否已有陈旧 .pth）---")
vd_root = E.VENV_ROOT
if os.path.isdir(vd_root):
    names = sorted(os.listdir(vd_root))
    print(f"  venv 总数：{len(names)}")
    for n in names:
        vd = os.path.join(vd_root, n)
        sp = os.path.join(vd, "Lib", "site-packages")
        pths = sorted(f for f in (os.listdir(sp) if os.path.isdir(sp) else []) if f.endswith(".pth"))
        print(f"    {n}")
        for ph in pths:
            body = ""
            try:
                with open(os.path.join(sp, ph), encoding="utf-8") as f:
                    body = f.read().strip().replace("\n", " ; ")
            except OSError as exc:
                body = f"<读取失败 {exc}>"
            print(f"      {ph}  ->  {body[:150]}")
else:
    print("  （无 venv 目录）")

print("\n--- 仓库自身状态 ---")
r = subprocess.run([E.GIT, "-C", PROJECT_ROOT, "status", "--porcelain"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
lines = [l for l in r.stdout.splitlines() if l.strip()]
eval_lines = [l for l in lines if "eval/" in l or "coding_agent" in l]
print(f"  porcelain 总行数 {len(lines)}；其中涉及 eval/ 或 coding_agent.py 的 {len(eval_lines)} 行")
for l in eval_lines:
    print(f"    {l}")
print(f"  HEAD = {subprocess.run([E.GIT, '-C', PROJECT_ROOT, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()}")