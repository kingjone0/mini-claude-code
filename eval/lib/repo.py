"""仓库画像与纳入检查（预注册 §2 R1–R8）。"""

from __future__ import annotations

import os
import time

from .util import (
    BASE_PY,
    CACHE_ROOT,
    RUNS_ROOT,
    VENV_ROOT,
    collect_node_ids,
    git,
    minimal_env,
    parse_junit,
    run,
)


def repo_dir(repo_key: str) -> str:
    return os.path.join(CACHE_ROOT, repo_key)


def venv_python(venv_dir: str) -> str:
    return os.path.join(venv_dir, "Scripts", "python.exe")


def ensure_venv(instance_id: str, tag: str, install_cmds, log_path: str):
    """§8.2：每实例独立 venv，不带 --system-site-packages。返回 (venv_dir, py, ok, tail)。"""
    venv_dir = os.path.join(VENV_ROOT, f"{instance_id}-{tag}")
    py = venv_python(venv_dir)
    lines = []
    if not os.path.exists(py):
        res = run([BASE_PY, "-m", "venv", venv_dir], timeout=900)
        lines.append(f"$ {' '.join(res.cmd)}\nexit={res.returncode} {res.stdout}{res.stderr}\n")
        if res.returncode != 0:
            _dump(log_path, lines)
            return venv_dir, py, False, "".join(lines)
    else:
        lines.append(f"venv exists: {venv_dir}\n")
    for cmd in install_cmds or []:
        argv = cmd.split() if isinstance(cmd, str) else list(cmd)
        # 统一用 venv 的 python -m pip，避免 Scripts\pip.exe 缺失
        if argv and argv[0].lower().endswith("pip"):
            argv = [py, "-m"] + argv[-2:] if len(argv) >= 2 else [py, "-m", "pip"] + argv[1:]
        elif argv and os.path.basename(argv[0]).lower() in ("pip", "pip.exe"):
            argv = [py, "-m", "pip"] + argv[1:]
        elif argv and argv[0] == "pip":
            argv = [py, "-m", "pip"] + argv[1:]
        res = run(argv, cwd=os.path.dirname(venv_dir), timeout=1800)
        lines.append(f"$ {' '.join(res.cmd)}\nexit={res.returncode}\n{res.stdout}\n{res.stderr}\n")
        if res.returncode != 0:
            _dump(log_path, lines)
            return venv_dir, py, False, "".join(lines)
    ver = run([py, "-V"], timeout=120)
    lst = run([py, "-m", "pip", "list", "--disable-pip-version-check", "--format=json"], timeout=300)
    lines.append(f"python: {ver.stdout.strip()}\npip list: {lst.stdout[:4000]}\n")
    _dump(log_path, lines)
    return venv_dir, py, True, "".join(lines)


def _dump(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(lines))


def pytest_run(workspace, py, node_ids, junit_path, scope=None, timeout=600, extra=()):
    """独立 pytest 判定运行；返回 dict（含逐 node 结果、退出码、超时标记）。

    ⚠ §8.4：先看 timed_out 再解释退出码。
    """
    cmd = [
        py, "-m", "pytest",
        "-p", "no:cacheprovider",
        "-q",
        "--timeout=120",
        "-o", "addopts=",
        "-o", "cache_dir=" + os.path.join(RUNS_ROOT, "_pytest_cache"),
        f"--junit-xml={junit_path}",
        *extra,
    ]
    if node_ids is None:
        cmd += list(scope or [])
    else:
        cmd += list(node_ids)
    res = run(cmd, cwd=workspace, timeout=timeout)
    results, meta = parse_junit(junit_path)
    return {
        "cmd": cmd,
        "returncode": res.returncode,
        "timed_out": res.timed_out,
        "wall_seconds": res.wall_seconds,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "junit": junit_path,
        "results": results,
        "meta": meta,
    }
