"""eval 子包的共享工具：git / patch / pytest 执行与 JUnit 解析。

被 eval/build_subset.py（实例构造器）使用。所有对外部仓库的写操作都限定在
E:\\aicoding\\eval-cache 与 E:\\aicoding\\eval-runs 之下（预注册 §9.3）。
控制台是 GBK ⇒ 子进程统一 PYTHONIOENCODING=utf-8，输出按 UTF-8 解码。
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time

GIT = r"E:\Program Files\Git\cmd\git.exe"
BASE_PY = r"D:\Anaconda3\envs\aicoding\python.exe"
CACHE_ROOT = r"E:\aicoding\eval-cache"
RUNS_ROOT = r"E:\aicoding\eval-runs"
VENV_ROOT = r"E:\aicoding\eval-venvs"

# 判定阶段不透传的环境变量（预注册 §8.3）
_DROP_ENV = (
    "PYTHONPATH",
    "PYTEST_ADDOPTS",
    "PYTHONHOME",
    "AGENT_REPO_ROOT",
)


def minimal_env(extra: dict | None = None) -> dict:
    """最小基线环境：丢掉站点/pytest 泄漏变量，强制 UTF-8 输出。"""
    env = dict(os.environ)
    for k in _DROP_ENV:
        env.pop(k, None)
    for k in list(env):
        if k.startswith("ANTHROPIC_"):
            env.pop(k, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C.UTF-8"
    if extra:
        env.update(extra)
    return env


class CommandResult:
    def __init__(self, cmd, returncode, stdout, stderr, wall_seconds, timed_out):
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.wall_seconds = wall_seconds
        self.timed_out = timed_out

    def to_dict(self):
        return {
            "cmd": self.cmd,
            "returncode": self.returncode,
            "wall_seconds": self.wall_seconds,
            "timed_out": self.timed_out,
        }


def run(cmd, cwd=None, timeout=600, env=None):
    """运行子进程并捕获 stdout/stderr（UTF-8）。

    ⚠ 预注册 §8.4：Windows 上被强杀的进程以退出码 1 收尾，与 pytest「有测试失败」
    的 exit 1 不可区分 ⇒ **先置 timed_out 标记，再由调用方解释退出码**。
    """
    started = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env if env is not None else minimal_env(),
    )
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        out, err = proc.communicate()
    return CommandResult(cmd, proc.returncode, out or "", err or "", round(time.time() - started, 3), timed_out)


def git(repo_dir, *args, timeout=300):
    cmd = [GIT, "-C", repo_dir, *[str(a) for a in args]]
    return run(cmd, cwd=repo_dir, timeout=timeout)


def git_ok(repo_dir, *args, timeout=300):
    res = git(repo_dir, *args, timeout=timeout)
    return res.returncode == 0, res


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- test 侧文件分类（预注册 §3.1）

_TEST_DIR_RE = re.compile(r"(^|/)(tests|test|testing)(/|$)")
_TEST_BASENAME_RE = re.compile(r"(^|/)(test_[^/]+\.py|[^/]+_test\.py|conftest\.py)$")


def classify_files(paths):
    """按 §3.1 的分类规则把提交里的文件切成 (source, test, dropped)。

    顺序首条命中即定类；conftest.py 归测试侧但不单独构成 C1 的第二侧。
    """
    source, test, dropped = [], [], []
    for p in paths:
        pl = p.replace("\\", "/")
        if _TEST_DIR_RE.search(pl) or _TEST_BASENAME_RE.search(pl):
            test.append(pl)
        elif pl.endswith(".py"):
            source.append(pl)
        else:
            dropped.append(pl)
    case_tests = [t for t in test if os.path.basename(t) != "conftest.py"]
    return source, test, case_tests, dropped


# ---------------------------------------------------------------- patch 应用


def apply_patch(repo_dir, patch_text, reverse=False, check_only=False, strip=1, timeout=300):
    if not patch_text.strip():
        return CommandResult(["git", "apply", "(empty)"], 0, "empty patch: skipped", "", 0.0, False)
    fd, tmp = _write_temp_diff(repo_dir, patch_text)
    try:
        cmd = [GIT, "-C", repo_dir, "apply", f"--strip={strip}", "--whitespace=nowarn"]
        if reverse:
            cmd.append("-R")
        if check_only:
            cmd.append("--check")
        cmd.append(tmp)
        return run(cmd, cwd=repo_dir, timeout=timeout)
    finally:
        os.unlink(tmp)


def _write_temp_diff(cwd, text):
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".diff", dir=cwd)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return fd, path


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def venv_python(venv_dir):
    return os.path.join(venv_dir, "Scripts", "python.exe")


def normalize(obj):
    """规范化 JSON 文本（sort_keys + ensure_ascii=False + 紧凑分隔符），用于 schema_sha256。"""
    import json

    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
