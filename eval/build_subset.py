#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P0 实例构造器：从真实修复提交 revert 构造「可复现 SWE-bench 语义评测」实例。

判据全部来自 `eval/PREREGISTRATION.md`（§1 schema / §2 仓库纳入 / §3 候选与排除 /
§4 F2P·P2P 推导 / §6 黄金三向自校验 / §7 规模与抽样 / §9 不变量）。本文件只做
**实例构造 + --verify-all**，不做 agent 评测 harness（那是下一个任务）。

用法::

    python eval/build_subset.py --scan                 # 只看候选池统计（不落盘）
    python eval/build_subset.py --build                # 构造（幂等）
    python eval/build_subset.py --verify-all           # 校验全部实例定义，失败非零退出
    python eval/build_subset.py --build --only-repo pallets__click

外部数据一律落在仓外三个目录（§9.3），仓库内只有代码与实例定义：
    E:\\aicoding\\eval-cache   上游克隆 + patch/JUnit/日志工件
    E:\\aicoding\\eval-runs    每实例工作区（目录副本，明确不用 git worktree）
    E:\\aicoding\\eval-venvs   每实例独立 venv（不带 --system-site-packages）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from lib import problem as P          # noqa: E402
from lib import report as R           # noqa: E402
from lib import schema as S           # noqa: E402
from lib import editable as ED        # noqa: E402
from lib.util import classify_files   # noqa: E402

GIT = r"E:\Program Files\Git\cmd\git.exe"
BASE_PY = r"D:\Anaconda3\envs\aicoding\python.exe"
CACHE_ROOT = r"E:\aicoding\eval-cache"
RUNS_ROOT = r"E:\aicoding\eval-runs"
VENV_ROOT = r"E:\aicoding\eval-venvs"

# ⚠ 本机 git 全局 core.autocrlf=true：checkout 把工作树变成 CRLF，而补丁正文是 LF ⇒
# `git apply` 报 "patch does not apply"。所有涉及补丁/工作树的 git 调用一律钉死 autocrlf=false
# （§8.2「环境由本机构造」的一部分，记入 known_limits）。
GIT_BASE = [GIT, "-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]

VERSION = "p0-1.0"
SET_NAME = "pilot-12"
INSTANCES_DIR = os.path.join(PROJECT_ROOT, "eval", "instances")
ARTIFACT_ROOT = os.path.join(CACHE_ROOT, "_artifacts")
BUILD_RUN_ID = "build-p0"
JSONL_PATH = os.path.join(INSTANCES_DIR, f"{SET_NAME}.jsonl")
REJECTED_PATH = os.path.join(INSTANCES_DIR, "_rejected.jsonl")
SAMPLING_LOG = os.path.join(INSTANCES_DIR, f"{SET_NAME}-sampling-log.md")

# §7.3 抽样规则（写死于预注册）
TARGET_TOTAL = 12
QUOTA_PER_REPO = 3
MAX_PER_REPO = 5
RESERVE_PER_REPO = 3
P2P_CAP = 60                      # §4.3 K = min(60, len(scope 内全部 node id))
R5_WALL_LIMIT = 300               # §2 R5：collection_scope 全量墙钟 ≤ 300 s
PER_TEST_WALL_LIMIT = 60          # §3.2 E7：单测 > 60 s
JUDGE_TIMEOUT = 600               # §8.4 判定 pytest 墙钟

# §3.3 C3 时间窗（钉死，覆盖 ≥12 个月且早于本文档提交日 2026-10-12）
WINDOW_START = "2025-04-01"
WINDOW_END = "2026-09-30"

# 环境口径（§8.2 + §10.3 第 2 条）：本机只有 pytest 9.1.1，仓库自带的旧 ini 选项在新版下
# 会触发 INTERNALERROR(exit 3) ⇒ 判定运行用临时 -c ini 替换仓库配置。
ENV_LIMIT_NOTE = (
    "判定命令用实例 artifacts/ 下的临时 _eval_pytest.ini 替换仓库 pytest 配置（本机只有 "
    "pytest 9.1.1，仓库 ini 的旧选项在新版下 INTERNALERROR）；B/T/G 三观测点与三向自校验都用"
    "同一模板，因此实例内部自洽，但与仓库 tox 环境的逐字配置不同（§10.3 第 2 条）"
)

# §9.1 清洁检查的口径 + 本机 git autocrlf 的处理
CLEAN_LIMIT_NOTE = (
    "本机 git 全局 core.autocrlf=true ⇒ 构造器的全部 git 调用钉死 -c core.autocrlf=false，"
    "否则补丁正文（LF）与工作树（CRLF）不匹配、git apply 失败"
) + " ; " + (
    "§9.1 的 git status --porcelain 为空判据不含被仓库 .gitignore 忽略的构建产物"
    "（pip install -e . 留下的 egg-info / __pycache__）——这些是 R4/R7 装依赖的必要结果"
) + " ; " + (
    "§9.1 的「跑后 porcelain 为空」按**未跟踪且未被忽略的文件**判定：施加补丁造成的已跟踪文件"
    "修改不算垃圾（补丁本身是判据的一部分）；基线检出时仍要求完全为空"
)

REPOS = {
    "more-itertools__more-itertools": {
        "owner_name": "more-itertools/more-itertools",
        "url": "https://github.com/more-itertools/more-itertools.git",
        "collection_scope": ["tests"],
        "pip_deps": ["pytest-timeout>=2.1", "pytest-json-report>=1.5"],
        "import_name": "more_itertools",
        "repo_notes": [],
    },
    "pytest-dev__pluggy": {
        "owner_name": "pytest-dev/pluggy",
        "url": "https://github.com/pytest-dev/pluggy.git",
        "collection_scope": ["testing"],
        "pip_deps": ["pytest-timeout>=2.1", "pytest-json-report>=1.5"],
        "import_name": "pluggy",
        "repo_notes": [],
    },
    "pallets__click": {
        "owner_name": "pallets/click",
        "url": "https://github.com/pallets/click.git",
        "collection_scope": ["tests"],
        "pip_deps": ["pytest-timeout>=2.1", "pytest-json-report>=1.5"],
        "import_name": "click",
        "repo_notes": [
            "仓库 addopts 为 -m 'not stress'；构造期用临时 -c ini 替换仓库配置后按 node id 显式跑，"
            "stress 用例不会被选中（node id 精确匹配）",
        ],
    },
    "astanin__python-tabulate": {
        "owner_name": "astanin/python-tabulate",
        "url": "https://github.com/astanin/python-tabulate.git",
        "collection_scope": ["test"],
        "pip_deps": ["pytest-timeout>=2.1", "pytest-json-report>=1.5"],
        "import_name": "tabulate",
        "repo_notes": [
            "仓库 addopts 含 --doctest-modules/--doctest-glob=README.md；构造期用临时 -c ini 替换仓库配置，"
            "doctest 不进 collection_scope",
        ],
    },
}


def log(msg):
    # 控制台代码页 GBK(936)：⚠ / 中文都能编，但为稳妥统一 errors='replace'
    text = f"[build] {msg}"
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", "replace") + b"\n")
        sys.stdout.buffer.flush()


class Reject(Exception):
    """命中预注册排除规则 ⇒ 携带规则代号与细节。"""

    def __init__(self, code, detail):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def normalize_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rel_art(path: str) -> str:
    """相对 E:\\aicoding\\eval-cache 的路径（§1.3 data_paths 的口径）。"""
    return os.path.relpath(path, CACHE_ROOT).replace("\\", "/")


# ---------------------------------------------------------------- 子进程封装

_DROP_ENV = ("PYTHONPATH", "PYTEST_ADDOPTS", "PYTHONHOME", "AGENT_REPO_ROOT")


def minimal_env(extra=None):
    env = dict(os.environ)
    for k in _DROP_ENV:
        env.pop(k, None)
    for k in list(env):
        if k.startswith("ANTHROPIC_"):
            env.pop(k, None)
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


class Res:
    def __init__(self, cmd, rc, out, err, wall, timed_out):
        self.cmd, self.rc, self.stdout, self.stderr = cmd, rc, out, err
        self.wall_seconds, self.timed_out = wall, timed_out


def sh(cmd, cwd=None, timeout=600, env=None):
    started = time.time()
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace",
                         env=env if env is not None else minimal_env())
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True          # §8.4：先置超时标记，再由调用方解释退出码
        p.kill()
        out, err = p.communicate()
    return Res(cmd, p.returncode, out or "", err or "", round(time.time() - started, 3), timed_out)


def git(repo, *args, timeout=600):
    return sh(GIT_BASE + ["-C", repo] + [str(a) for a in args], cwd=repo, timeout=timeout)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 缓存与工作区

def cache_dir(repo_key):
    return os.path.join(CACHE_ROOT, repo_key)


def ensure_cache(repo_key):
    d = cache_dir(repo_key)
    if os.path.isdir(os.path.join(d, ".git")):
        git(d, "fetch", "--all", "--quiet", timeout=1800)
        git(d, "config", "core.autocrlf", "false", timeout=60)
        # 缓存仓库会被 §6.3 的 wrong_patch 生成临时检出 ⇒ 保证回到干净的默认分支，
        # 否则后续 `git show` / `ls-tree HEAD` 读到的是上一个实例的基线。
        br = git(d, "rev-parse", "--abbrev-ref", "origin/HEAD").stdout.strip()
        if not br or br == "origin/HEAD":
            br = "HEAD"
        git(d, "checkout", "--quiet", "--detach", br, timeout=600)
        git(d, "clean", "-fdq", timeout=900)
        return d
    os.makedirs(CACHE_ROOT, exist_ok=True)
    res = sh(GIT_BASE + ["clone", "--quiet", REPOS[repo_key]["url"], d], timeout=3600)
    if res.rc != 0:
        raise Reject("R2", f"clone/fetch 失败：{res.stderr[-300:]}")
    return d


def ws_path(iid):
    return os.path.join(RUNS_ROOT, BUILD_RUN_ID, iid, "repo")


def cache_checkout_base(repo_key, base_commit):
    """把缓存仓库 detached 检出到 base_commit（仅用于 §6.3 wrong_patch 的生成与试 apply）。

    缓存仓库在 `E:\\aicoding\\eval-cache` 下，不在 mini-claude-code 仓库内 ⇒ 不违反 §9.1/§9.3。
    ⚠ 部分仓库带 `.gitattributes`（如 more-itertools 的 `merge=union`）会让 checkout 后的工作树
    相对 index 显示为「已修改」⇒ 每次检出后必须硬重置 + 清未跟踪文件，否则后续实例的
    `git show <fix> -- paths` 会拿到被污染的基线内容、补丁永远 apply 不上。
    """
    d = cache_dir(repo_key)
    res = git(d, "checkout", "--quiet", "--force", "--detach", base_commit, timeout=600)
    if res.rc != 0:
        raise Reject("E0", f"缓存仓库检出 base_commit 失败：{res.stderr[-200:]}")
    res = git(d, "reset", "--hard", "-q", base_commit, timeout=600)
    if res.rc != 0:
        raise Reject("E0", f"缓存仓库硬重置失败：{res.stderr[-200:]}")
    git(d, "clean", "-fdq", timeout=900)
    return d


def cache_restore_head(repo_key):
    """用完缓存检出后回到远端默认分支并重置，保证下一个候选读到干净对象/工作树。"""
    d = cache_dir(repo_key)
    br = git(d, "rev-parse", "--abbrev-ref", "origin/HEAD").stdout.strip()
    if not br or br.startswith("origin/"):
        pass
    elif br == "HEAD":
        br = ""
    target = br or "FETCH_HEAD"
    for ref in (target, "HEAD"):
        if git(d, "checkout", "--quiet", "--force", "--detach", ref, timeout=600).rc == 0:
            git(d, "reset", "--hard", "-q", ref, timeout=600)
            git(d, "clean", "-fdq", timeout=900)
            return d
    raise Reject("INFRA", f"无法把缓存仓库 {repo_key} 恢复到默认状态")


def fresh_workspace(iid, repo_key, base_commit):
    """§8.1：从缓存仓库 base_commit 做目录副本（git archive → git init + commit）。"""
    parent = os.path.dirname(ws_path(iid))
    if os.path.isdir(parent):
        shutil.rmtree(parent, ignore_errors=True)
    os.makedirs(parent, exist_ok=True)
    d = cache_dir(repo_key)
    tar = os.path.join(parent, "_base.tar")
    res = git(d, "archive", "--format=tar", "-o", tar, base_commit, timeout=900)
    if res.rc != 0:
        raise Reject("E0", f"git archive 失败：{res.stderr[-300:]}")
    ws = ws_path(iid)
    os.makedirs(ws, exist_ok=True)
    if git(ws, "init", "--quiet").rc != 0:
        raise Reject("C4", "git init 失败")
    for cfg in (["user.email", "eval@local"], ["user.name", "eval-builder"],
                ["core.autocrlf", "false"], ["core.safecrlf", "false"],
                ["commit.gpgsign", "false"]):
        git(ws, "config", *cfg, timeout=60)
    with tarfile.open(tar) as tf:
        tf.extractall(ws)
    os.unlink(tar)
    if git(ws, "add", "-A", timeout=900).rc != 0:
        raise Reject("C4", "基线 git add 失败")
    if git(ws, "commit", "--quiet", "-m", f"baseline {base_commit}", timeout=900).rc != 0:
        raise Reject("C4", "基线 git commit 失败")
    porcelain = run_porcelain(ws)
    if porcelain:
        raise Reject("C4", f"基线工作区不为空：{porcelain[:300]}")
    return ws


def run_porcelain(ws):
    return git(ws, "status", "--porcelain", timeout=180).stdout.strip()


def clean_check_repo(ws):
    """§9.1 的严格清洁检查（基线检出时）：`git status --porcelain` 必须完全为空。"""
    return run_porcelain(ws) == ""


def workspace_clean(ws):
    """判定**之后**的检查：允许工作区仍带着本轮施加的补丁（已跟踪文件的修改）。

    §9.1 的「跑后为空」指的是没有把**垃圾**留在 `repo/` 内（临时文件写在 artifacts/）；
    补丁本身是判据的一部分，不能算垃圾。⇒ 这里只要求：没有未跟踪且未被忽略的文件、
    没有合并残留。基线检出时（fresh_workspace）用严格版 clean_check_repo。
    """
    out = git(ws, "status", "--porcelain", timeout=180).stdout
    for line in out.splitlines():
        code = line[:2]
        if "??" in code or "U" in code:
            return False
    return True


def reset_ws(ws):
    git(ws, "checkout", "--", ".", timeout=300)
    # 只清「未跟踪」文件/目录（不带 -x）⇒ 仓库 .gitignore 里的构建产物（egg-info / __pycache__，
    # R4/R7 装依赖的必要结果）保持不动，porcelain 本来就不显示它们；
    # 判定用临时文件写在 repo/ 外的 artifacts/，不依赖这里的例外。
    git(ws, "clean", "-fdq", timeout=900)


# ---------------------------------------------------------------- venv（§8.2）

def venv_dir_for(iid):
    return os.path.join(VENV_ROOT, f"{iid}-py312")


def instance_id_for(repo_key, sha7):
    """§1.2：`<repo_owner>-<repo_name>__<fix_commit 前 7 位小写十六进制>`。

    owner/name 取自 remote（去掉 .git），即 REPOS[rk]["owner_name"] = `<owner>/<name>`。
    ⚠ 只把其中的 `/` 换成 `-`；不能把 owner 里已有的 `-` 再换成 `_`，否则
    `more-itertools/more-itertools` 会变成 `more_itertools-more-itertools`，不合 §1.2 格式。
    """
    return f"{REPOS[repo_key]['owner_name'].replace('/', '-')}__{sha7}"


def venv_py(iid):
    return ED.venv_py(VENV_ROOT, iid)


def install_editable_finder(iid, ws, pkg, art_dir):
    """把实例工作区的包挂进该实例专属 venv（产物只落在 venv 目录内，不落 repo/）。"""
    parent = ED.package_parent(ws, pkg)
    if parent is None:
        raise Reject("R4/R7", f"在工作区里找不到包 {pkg}（既不是 src/ 布局也不是扁平布局）")
    entries = [parent]
    if os.path.basename(ws) == "repo":
        entries.append(ws)          # 扁平布局兜底：工作区根也进 sys.path
    pth = write_text(ED.editable_pth(VENV_ROOT, iid, sha_of(iid)[:16]), ED.pth_body(entries))
    _assert_pkg_from_workspace(iid, ws, pkg, pth)
    return pth


def refresh_editable_pth(iid, repo_key, ws):
    """工作区被删除重建后（路径不变但内容重铺），重新校验 editable 安装仍指向它。

    `ensure_venv` 会复用已存在的 venv ⇒ 若上一轮的工作区已被删掉重建，pth 里的绝对路径虽然
    同名，也必须确认包**确实从当前工作区导入**，否则判定用的就不是基线代码了。
    """
    py = venv_py(iid)
    pth = ED.editable_pth(VENV_ROOT, iid, sha_of(iid)[:16])
    parent = ED.package_parent(ws, REPOS[repo_key]["import_name"])
    if parent is None:
        raise Reject("R4/R7", f"工作区里找不到包 {REPOS[repo_key]['import_name']}")
    entries = [parent] + ([ws] if os.path.basename(ws) == "repo" else [])
    write_text(pth, ED.pth_body(entries))
    _assert_pkg_from_workspace(iid, ws, REPOS[repo_key]["import_name"], pth, python=py)


def _assert_pkg_from_workspace(iid, ws, pkg, pth, python=None):
    """§2 R4 以实测为准：**真的 import 成功**且来自本实例工作区才算装好。

    ⚠ 不要用 `importlib.util.find_spec` —— 它只找 spec 不执行模块，半挂上的环境也会返回非
    None ⇒ 自检假绿。⚠ 也不要用 `'repo' in path.split('/')` 这种写死目录名的判据：
    §8.1 规定的工作区路径是 `E:\\aicoding\\eval-runs\\<run_id>\\<iid>\\repo`，末段确实是
    `repo`，但正确做法是直接比对**解析出的文件是否位于该工作区内**。
    """
    probe = sh([python or venv_py(iid), "-c",
                "import os, sys, importlib;"
                f"m = importlib.import_module({pkg!r});"
                "p = os.path.realpath(getattr(m, '__file__', '') or '');"
                "root = os.path.realpath(" + repr(ws) + ");"
                "sys.stdout.write(p);"
                "raise SystemExit(0 if p and (p == root or p.startswith(root + os.sep)) else 1)"],
               cwd=os.path.expanduser("~"), timeout=300)
    if probe.rc != 0:
        raise Reject("R4", f"editable 安装自检失败（{pkg} 未从本实例工作区导入）：pth={pth} "
                           f"解析到={probe.stdout.strip()[-200:]} stderr={probe.stderr[-200:]}")

def ensure_venv(iid, repo_key, art_dir, ws=None):
    py = venv_py(iid)
    vd = venv_dir_for(iid)
    logp = os.path.join(art_dir, "venv.log")
    lines = []
    if not os.path.exists(py):
        res = sh([BASE_PY, "-m", "venv", vd], timeout=1200)
        lines.append(f"$ {' '.join(res.cmd)}\nexit={res.rc}\n{res.stdout}{res.stderr}\n")
        if res.rc != 0:
            write_text(logp, "".join(lines))
            raise Reject("R4", f"venv 创建失败：{res.stderr[-300:]}")
    else:
        lines.append(f"reuse existing venv: {vd}\n")
    deps = REPOS[repo_key]["pip_deps"]
    res = sh([py, "-m", "pip", "install", "--disable-pip-version-check", *deps], timeout=1800)
    lines.append(f"$ pip install {' '.join(deps)}\nexit={res.rc}\n{res.stdout[-2500:]}\n{res.stderr[-1500:]}\n")
    if res.rc != 0:
        write_text(logp, "".join(lines))
        raise Reject("R4", f"依赖安装失败：{res.stderr[-300:]}")
    pkg = REPOS[repo_key]["import_name"]
    # §9.1 要求「判定后工作区 porcelain 为空」，而 `pip install -e .` 会往 repo/ 里写 egg-info
    # ⇒ 用只落在 venv 目录内的 editable pth（见 lib/editable.py，用 import 行式 .pth 把工作区
    # 路径插到 sys.path 最前，压过 site-packages 里 pytest 依赖拖进来的同名副本）。
    if ws is not None:
        pth = install_editable_finder(iid, ws, pkg, art_dir)
        lines.append(f"editable pth written to {pth} (workspace untouched)\n")
    imp = sh([py, "-c", f"import {pkg}; print({pkg}.__file__)"], cwd=os.path.expanduser("~"), timeout=300)
    lines.append(f"$ python -c 'import {pkg}'\nexit={imp.rc}\n{imp.stdout}{imp.stderr}\n")
    if imp.rc != 0:
        write_text(logp, "".join(lines))
        raise Reject("R4/R7", f"import {pkg} 失败（无编译步骤仍装不起来）：{imp.stderr[-300:]}")
    ver = sh([py, "-V"], timeout=120)
    lst = sh([py, "-m", "pip", "list", "--disable-pip-version-check"], timeout=300)
    lines.append(f"\npython: {ver.stdout.strip()}\n\npip list:\n{lst.stdout}\n")
    write_text(logp, "".join(lines))
    return py


# ---------------------------------------------------------------- pytest 观测点

def collect_scope_ids(ws, py, scope, cfg, art_dir, tag="collect_only"):
    """`--collect-only -q` 收集 collection_scope 内的全部 node id（§1.3 白名单）。

    用途：①确定 scope 内有多少 node（§4.3 的 K 上限）；②把 F2P/P2P 与「真实可收集到的
    node id」对齐 —— 判据必须是能复算的字符串，不是从报告里捞出来的自由文本。
    与判定运行共用同一个 `-c` ini 与 `--timeout`，保证 rootdir 与收集集合一致。
    """
    cmd = [py, "-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q",
           "-c" + cfg, "-o", "cache_dir=" + os.path.join(RUNS_ROOT, "_pcache"),
           "--rootdir=" + ws,
           "--json-report", "--json-report-file=" + os.path.join(art_dir, f"_{tag}.json")] + list(scope)
    res = sh(cmd, cwd=ws, timeout=300)
    ids = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if "::" not in line or line.startswith(("=", "E ", "W ", "----", "<")):
            continue
        nid = R.normalize_nodeid(line.replace("\\", "/"), ws)
        if nid:
            ids.append(nid)
    return res, ids


def pytest_run(ws, py, node_ids, report_name, art_dir, scope=None, timeout=JUDGE_TIMEOUT,
               collected=None):
    """一次判定运行：逐 node 结果**只**取 pytest JSON report 里的完整 nodeid。

    ⚠ 本机只有 pytest 9.1.1，而仓库的 tox/uv.lock 钉的是旧版；旧 ini 里的选项在新版下会
    `_validate_config_options` INTERNALERROR（exit 3）⇒ 用 `-c <临时 ini>` **替换**配置文件
    （保留 `[pytest]` 段头），而不是 `--override-ini`（它只改值、不阻止未知键校验）。
    这是 §8.2「环境由本机构造」的直接后果，记入实例 known_limits。

    ⚠ JUnit XML 在 pytest 9 上不输出 `file` 属性 ⇒ 无法还原 node id，不能当判据；
    JSON report 给出与 `--collect-only -q` 同一字符串空间的完整 nodeid（§4.4 精确匹配）。

    预注册 §9.1：跑前跑后工作区必须无垃圾 ⇒ 判定用的临时文件（ini / diff / report）全部写在
    实例目录下的 artifacts/，不在 `repo/` 内。
    """
    cfg = os.path.join(art_dir, INI_NAME)
    write_text(cfg, "[pytest]\n")
    cmd = [py, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--timeout=120",
           "-c" + cfg, "-o", "cache_dir=" + os.path.join(RUNS_ROOT, "_pcache"),
           "--rootdir=" + ws,
           "--json-report", f"--json-report-file={os.path.join(art_dir, report_name)}"]
    cmd += list(node_ids) if node_ids is not None else list(scope or [])
    res = sh(cmd, cwd=ws, timeout=timeout)
    results, meta = R.parse_json_report(os.path.join(art_dir, report_name), ws)
    return {"cmd": cmd, "returncode": res.rc, "timed_out": res.timed_out,
            "wall_seconds": res.wall_seconds, "stdout": res.stdout, "stderr": res.stderr,
            "report": os.path.join(art_dir, report_name), "results": results, "meta": meta,
            "collected": list(collected or [])}

INI_NAME = "_eval_pytest.ini"


def save_run_log(art_dir, name, r):
    # 日志里不塞 durations 全表（上千条，会把证据文件撑成几十 KB 不可读）；
    # 只留计数与「有非绿结果的 node」——完整逐 node 结果在 json report 里，同目录可取。
    meta = {k: v for k, v in r["meta"].items() if k != "durations"}
    slow = {k: v for k, v in (r["meta"].get("durations") or {}).items() if v > 1.0}
    body = ("$ " + " ".join(r["cmd"]) + "\n"
            f"exit_code={r['returncode']}  timed_out={r['timed_out']}  wall_seconds={r['wall_seconds']}\n"
            f"json_report_meta={json.dumps(meta, ensure_ascii=False)}\n"
            f"collected_count={len(r['collected'])}\n"
            f"slow_tests_over_1s={len(slow)}\n"
            "---- per-node outcome (workspace-relative node id) ----\n"
            + "".join(f"{k}\t{v}\n" for k, v in sorted(r["results"].items()))
            + "---- failed/error nodes ----\n"
            + "".join(f"{k}\t{v}\n" for k, v in sorted(r["results"].items()) if v != "passed")
            + "\n---- pytest stdout ----\n" + r["stdout"]
            + "\n---- pytest stderr ----\n" + r["stderr"] + "\n")
    return write_text(os.path.join(art_dir, f"{name}.txt"), body)


def sanity_point(r, label):
    """§2 R5 / §3.2 E6·E7 / §8.4：先查超时标记，再解释退出码。"""
    if r["timed_out"]:
        raise Reject("E7", f"{label} 观测点墙钟超过判定上限 {JUDGE_TIMEOUT}s（超时优先于退出码解释）")
    if r["returncode"] >= 2:
        raise Reject("E6", f"{label} pytest 退出码 {r['returncode']}（usage/内部错/收集错误）")
    if r["meta"]["errors"]:
        raise Reject("E6", f"{label} json-report errors={r['meta']['errors']}（收集期错误）")
    if r["meta"].get("collection_errors"):
        raise Reject("E6", f"{label} 有 {r['meta']['collection_errors']} 条无 nodeid 的 report 条目")
    if r["wall_seconds"] > R5_WALL_LIMIT:
        raise Reject("R5", f"{label} 观测点墙钟 {r['wall_seconds']}s > {R5_WALL_LIMIT}s")
    if not r["results"]:
        raise Reject("E6", f"{label} 没有任何逐 node 结果（json report 为空）")
    slow = sorted(n for n, d in (r["meta"].get("durations") or {}).items() if d > PER_TEST_WALL_LIMIT)
    if slow:
        raise Reject("E7", f"{label} 有单测耗时 > {PER_TEST_WALL_LIMIT}s：{[(n, r['meta']['durations'][n]) for n in slow[:4]]}")


def diff_runs(runs):
    a = runs[0]["results"]
    out = []
    for other in runs[1:]:
        b = other["results"]
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                out.append({"node_id": k, "first": a.get(k), "second": b.get(k)})
    return out


# ---------------------------------------------------------------- wrong_patch（§6.3）

def build_wrong_patch(cache_repo, repo_key, source_files, gold_patch, profile, art_dir, iid):
    """§6.3 wrong_patch：按顺序取第一个可行者 ⇒ (text, method)。

    在**缓存仓库**（detached 到 base_commit）上生成，绝不碰实例工作区 —— 否则 §9.1 的
    「跑后无垃圾」会被探测用的临时文件破坏。
    """
    listing = git(cache_repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    inside = set(source_files)
    outside = []
    for p in (l.strip().replace("\\", "/") for l in listing):
        if not p.endswith(".py") or p in inside:
            continue
        if re.search(r"(^|/)(tests?|testing|docs|doc|benchmarks?|scripts|examples)(/|$)", p):
            continue
        if os.path.basename(p) in ("conftest.py", "setup.py"):
            continue
        outside.append(p)
    for path in sorted(outside):
        content = git(cache_repo, "show", f"HEAD:{path}").stdout
        if not content.strip() or "\n" not in content:
            continue
        nl = content.find("\n")
        new = content[: nl + 1] + "# eval wrong-patch probe\n" + content[nl + 1:]
        diff = make_diff(path, content, new)
        if diff and apply_patch(cache_repo, diff, check_only=True, art_dir=art_dir).rc == 0:
            return diff, "unrelated_file_comment"
    alt = method_two(cache_repo, gold_patch)
    if alt:
        return alt, "wrong_function_hunk"
    return gold_patch, "reverse_of_gold"      # 施加时用 git apply -R


def make_diff(path, old, new):
    import difflib

    lo = old.splitlines(keepends=True)
    ln = new.splitlines(keepends=True)
    if lo and not lo[-1].endswith("\n"):
        lo[-1] += "\n"
        ln = (new + "\n").splitlines(keepends=True)
    d = list(difflib.unified_diff(lo, ln, fromfile=f"a/{path}", tofile=f"b/{path}", n=3))
    return "".join(d)


def method_two(cache_repo, gold_patch):
    blocks = split_hunks(gold_patch)
    if len(blocks) < 2:
        return ""
    file_path, hunks = blocks[-1]
    content = git(cache_repo, "show", f"HEAD:{file_path}").stdout
    if not content:
        return ""
    defs = re.findall(r"^def\s+([A-Za-z_]\w*)", content, flags=re.M)
    if len(defs) < 2:
        return ""
    last_target = ""
    m = re.search(r"@@[^\n]*@@\s*.*?\b(?:def|class)\s+([A-Za-z_]\w*)", hunks[-1])
    if m:
        last_target = m.group(1)
    target = next((d for d in reversed(defs) if d != last_target), "")
    idx = content.rfind(f"def {target}")
    if idx < 0:
        return ""
    nl = content.find("\n", idx)
    new = content[: nl + 1] + "    # eval wrong-patch probe\n" + content[nl + 1:]
    return make_diff(file_path, content, new)


def split_hunks(patch_text):
    """[(file, [hunk,...]), ...]；hunk 以 @@ 开头并带上该文件的 ---/+++ 头。"""
    blocks = []
    cur_file, cur_header, cur_hunks = None, [], []
    for line in patch_text.splitlines(keepends=True):
        if line.startswith("diff --git"):
            if cur_file and cur_hunks:
                blocks.append((cur_file, cur_hunks))
            m = re.match(r"diff --git a/(\S+) b/\S+", line)
            cur_file = m.group(1) if m else None
            cur_header, cur_hunks = [line], []
            continue
        if cur_file is None:
            continue
        if line.startswith("@@"):
            cur_hunks.append(list(cur_header) + [line])
            cur_header = []
        elif cur_hunks:
            cur_hunks[-1].append(line)
        else:
            cur_header.append(line)
    if cur_file and cur_hunks:
        blocks.append((cur_file, cur_hunks))
    return [(f, ["".join(h) for h in hs]) for f, hs in blocks]


def apply_patch(ws, patch_text, reverse=False, check_only=False, art_dir=None):
    """在 `ws` 应用补丁。临时 diff 写在 `art_dir`（§9.1：repo/ 内不留判定用临时文件）。"""
    if not patch_text.strip():
        return Res(["git", "apply", "(empty)"], 0, "empty patch: nothing to do", "", 0.0, False)
    tmp_dir = art_dir or os.path.dirname(ws)
    os.makedirs(tmp_dir, exist_ok=True)
    tmp = os.path.join(tmp_dir, "_tmp_probe.diff")
    write_text(tmp, patch_text)
    cmd = GIT_BASE + ["-C", ws, "apply", "--whitespace=nowarn"]
    if reverse:
        cmd.append("-R")
    if check_only:
        cmd.append("--check")
    cmd.append(tmp.replace("\\", "/"))
    res = sh(cmd, cwd=ws, timeout=300)
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return res


# ---------------------------------------------------------------- 候选扫描

def scan_candidates(repo_key, window_start=WINDOW_START, window_end=WINDOW_END):
    d = cache_dir(repo_key)
    head_ref = git(d, "rev-parse", "HEAD").stdout.strip()      # 记录扫描基准，供 verify-all 复算
    fmt = "%H|%aI|%s"
    res = git(d, "log", "--since", window_start, "--until", window_end, "--no-merges",
              "--reverse", f"--format={fmt}", timeout=900)
    if res.rc != 0:
        raise RuntimeError(res.stderr[-300:])
    out = []
    for line in res.stdout.splitlines():
        if "|" not in line:
            continue
        sha, iso, subject = line.split("|", 2)
        names = git(d, "show", "--pretty=format:", "--name-only", sha).stdout
        paths = [l.strip().replace("\\", "/") for l in names.splitlines() if l.strip()]
        src, test, case_tests, dropped = classify_files(paths)
        parents = git(d, "show", "-s", "--format=%P", sha).stdout.split()
        entry = {"sha": sha, "subject": subject, "date_iso": iso, "src": src, "test": test,
                 "case_tests": case_tests, "dropped": dropped, "parents": len(parents)}
        if not src or not case_tests:
            entry["skip"] = "C1"
        elif subject.startswith("Merge") or len(parents) != 1:
            entry["skip"] = "C2"
        out.append(entry)
    return [e for e in out if "skip" not in e], out, head_ref


def diff_for_paths(repo_key, sha, paths):
    """`git show <sha> -- <paths>` 的 unified diff（源码侧 / 测试侧拆分用）。

    ⚠ 必须丢掉 `git show` 默认的提交信息头：那几行以 **40 位 SHA + 四个连续减号** 开头，
    `git apply` 会把它们当成补丁正文里的删除行 ⇒ 「patch does not apply」。⇒
    从第一个 `diff --git` 起截取，并去掉尾部的 `-- `\n签名行。
    """
    if not paths:
        return ""
    res = git(cache_dir(repo_key), "show", "--full-index", "--no-color", sha, "--", *paths, timeout=300)
    if res.rc != 0:
        return ""
    text = res.stdout
    idx = text.find("diff --git ")
    if idx < 0:
        return ""
    text = text[idx:]
    m = re.search(r"\n-- \n$", text)
    if m:
        text = text[: m.start()]
    if not text.endswith("\n"):
        text += "\n"
    return text


def numstat(repo_key, sha):
    res = git(cache_dir(repo_key), "show", "--numstat", "--format=", sha)
    files, adds, dels = [], 0, 0
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            a, dl, f = parts[0], parts[1], parts[2]
            files.append(f.replace("\\", "/"))
            adds += int(a) if a.isdigit() else 0
            dels += int(dl) if dl.isdigit() else 0
    return files, adds, dels


# ---------------------------------------------------------------- 单候选构造

def build_candidate(repo_key, cand, profile, head_ref=""):
    """跑完整 §3–§6 流程；成功返回实例 dict，失败抛 Reject。

    ⚠ 补丁文本必须在检出基线**之前**取（`git show` 读对象库不受工作树影响，但缓存仓库的
    detached 检出会改变 `ls-tree HEAD` / `show HEAD:path` 的内容 ⇒ 顺序固定为：
    取 diff → 建工作区 → 观测点 → 检出基线造 wrong_patch → 恢复缓存）。
    """
    sha = cand["sha"]
    short = sha[:7]
    iid = instance_id_for(repo_key, short)
    art_dir = os.path.join(ARTIFACT_ROOT, repo_key, iid)
    os.makedirs(art_dir, exist_ok=True)
    d = cache_dir(repo_key)

    if git(d, "cat-file", "-e", f"{sha}^{{commit}}").rc != 0:
        raise Reject("E0", "修复提交对象不可达")
    base = git(d, "rev-parse", f"{sha}^").stdout.strip()
    if git(d, "cat-file", "-e", f"{base}^{{commit}}").rc != 0:
        raise Reject("E0", "base_commit 对象不可达")

    show_stat = git(d, "show", "--stat", "--format=%H%n%ci%n%s%n%n%b", sha)
    write_text(os.path.join(art_dir, "fix_commit_show.txt"),
               "$ git -C %s show --stat --format=%%H%%n%%ci%%n%%s%%n%%n%%b %s\n" % (d, sha)
               + show_stat.stdout + show_stat.stderr)
    # §3.4 要求：problem_statement 用提交信息原文（subject + body）
    cand["body"] = cand.get("body") or git(d, "show", "-s", "--format=%b", sha).stdout

    src_files, test_files = cand["src"], cand["test"]
    patch = diff_for_paths(repo_key, sha, src_files)
    test_patch = diff_for_paths(repo_key, sha, test_files)
    if not patch.strip():
        raise Reject("E3", "gold patch 文本为空")
    if not test_patch.strip():
        raise Reject("E3", "test_patch 文本为空")
    if set(src_files) & set(test_files):
        raise Reject("§1.4.2", "patch 与 test_patch 文件集合相交")

    files, adds, dels = numstat(repo_key, sha)
    src_changed = [f for f in files if f in set(src_files)]
    if len(src_changed) > 8 or (adds + dels) > 400:
        raise Reject("E4", f"源码文件数 {len(src_changed)} / 改动行数 {adds + dels} 超上限")

    # §3.2 E1：测试侧含删除测试 / 大规模 skip。判据（写死）：
    #   ① `-` 行里出现 `@pytest.mark.skip` / `@pytest.mark.xfail` / `pytest.skip(` / `pytest.xfail(`；
    #   ② `-` 行里删除了一整个测试函数定义（`-def test_*`）。
    # 只做「本候选是否引入禁用」的判定，不做启发式猜测 —— 命中的候选直接淘汰并落 _rejected.jsonl。
    e1_hits = []
    for line in test_patch.splitlines():
        if not line.startswith("-") or line.startswith("---"):
            continue
        body = line[1:]
        if re.search(r"@pytest\.mark\.(skip|xfail)", body) or \
           re.search(r"\bpytest\.(skip|xfail)\s*\(", body):
            e1_hits.append(body.strip()[:80])
        elif re.match(r"\s*def\s+test_\w+", body):
            e1_hits.append(body.strip()[:80])
    if e1_hits:
        raise Reject("E1", f"test_patch 删除/禁用了测试：{e1_hits[:3]}")

    ws = fresh_workspace(iid, repo_key, base)
    if not clean_check_repo(ws):
        raise Reject("C4", "基线工作区不为空")
    try:
        py = ensure_venv(iid, repo_key, art_dir, ws)
    except Reject as exc:
        if exc.code.startswith("R4"):
            raise Reject(exc.code, f"{exc.detail}；仓库 .gitignore 未覆盖可编辑安装产物，"
                                   "§9.1「跑后 porcelain 为空」在该仓库不可满足（§2.3 兜底：判不了即排除）")
        raise
    # 工作区每轮重建 ⇒ 复用 venv 时也要确认包仍从**当前**工作区导入
    refresh_editable_pth(iid, repo_key, ws)
    scope = profile["collection_scope"]

    # ---------------- 先收集 collection_scope 内的全部 node id（§1.3 / §4.3）
    # 判据必须落在**可复算的字符串**上：F2P/P2P 全部要能在这份 collect 输出里找到，
    # 否则判定 harness 用同一条命令也可能选不中同一个 node。
    cfg = os.path.join(art_dir, INI_NAME)
    write_text(cfg, "[pytest]\n")
    # 收集要分两次：基线（无补丁）的收集集合是 §4.3 的 K 基数；
    # **加了 test_patch 之后**的收集集合才是 F2P/P2P 的合法取值域 —— 新增测试只在后者里出现。
    reset_ws(ws)
    cres, collected = collect_scope_ids(ws, py, scope, cfg, art_dir, tag="collect_only")
    write_text(os.path.join(art_dir, "collect_only.txt"),
               f"$ {' '.join(cres.cmd)}\nexit_code={cres.rc}\ntimed_out={cres.timed_out}\n"
               f"collected={len(collected)}\n" + "\n".join(collected) + "\n"
               + "\n---- stdout ----\n" + cres.stdout + "\n---- stderr ----\n" + cres.stderr + "\n")
    if cres.timed_out or cres.rc >= 2:
        raise Reject("E6", f"collection_scope 收集失败：exit={cres.rc} timed_out={cres.timed_out} "
                           f"{cres.stderr[-200:]}")
    if len(collected) < 20:      # §2 R3：pytest 可收集 node id ≥ 20
        raise Reject("R3", f"collection_scope {scope} 只收集到 {len(collected)} 个 node id (< 20)")
    collected_set = set(collected)
    # 施加 test_patch 后再收集一次（含新增测试）
    reset_ws(ws)
    ap0 = apply_patch(ws, test_patch, art_dir=art_dir)
    if ap0.rc != 0:
        raise Reject("E3/test_apply", f"收集前 test_patch 无法应用 {ap0.stderr[-300:]}")
    cres_t, collected_t = collect_scope_ids(ws, py, scope, cfg, art_dir, tag="collect_test_only")
    write_text(os.path.join(art_dir, "collect_test_only.txt"),
               f"$ {' '.join(cres_t.cmd)}\nexit_code={cres_t.rc}\ntimed_out={cres_t.timed_out}\n"
               f"collected={len(collected_t)}\n" + "\n".join(collected_t) + "\n"
               + "\n---- stdout ----\n" + cres_t.stdout + "\n---- stderr ----\n" + cres_t.stderr + "\n")
    if cres_t.timed_out or cres_t.rc >= 2:
        raise Reject("E6", f"test_patch 后收集失败：exit={cres_t.rc} timed_out={cres_t.timed_out} "
                           f"{cres_t.stderr[-200:]}")
    collected_t_set = set(collected_t)
    reset_ws(ws)

    # ---------------- 观测点 B / T / G（各双跑核 flaky）
    runs = {}
    for label in ("B", "T", "G"):
        reps = []
        for i in (1, 2):
            reset_ws(ws)
            if label in ("T", "G"):
                ap = apply_patch(ws, test_patch, art_dir=art_dir)
                if ap.rc != 0:
                    raise Reject("E3/test_apply", f"{label}：test_patch 无法应用 {ap.stderr[-300:]}")
            if label == "G":
                gp = apply_patch(ws, patch, art_dir=art_dir)
                if gp.rc != 0:
                    raise Reject("E3/gold_apply", f"gold patch 无法应用 {gp.stderr[-300:]}")
            r = pytest_run(ws, py, None, f"{label}{i}.json", art_dir, scope=scope,
                           collected=collected)
            save_run_log(art_dir, f"{label}{i}_{ {'B':'baseline','T':'test_only','G':'test_plus_gold'}[label] }", r)
            sanity_point(r, label)
            reps.append(r)
        runs[label] = reps
        if not workspace_clean(ws):
            raise Reject("§9.1", f"{label} 观测点后工作区不为空")
    flaky = (diff_runs(runs["B"]) + diff_runs(runs["T"]) + diff_runs(runs["G"]))
    B, T, G = runs["B"][0]["results"], runs["T"][0]["results"], runs["G"][0]["results"]

    # §4.3 的 K = min(60, len(collection_scope 内全部 node id))
    k = min(P2P_CAP, len(collected))

    # ---------------- F2P（§4.2）
    # §4.2 的「B 中 n 存在且 passed」写作排除判据：排除的是「**基线就存在、且本来就红**」
    # 的测试（它们不属于本实例要修的东西）。**新加的测试**在基线上不存在（B 中查无此 node），
    # 那是 SWE-bench 语义里最标准的 FAIL_TO_PASS 形态，不能被这条排除掉 —— 否则凡是
    # 「修复提交顺手加了回归测试」的真实修复都会被误杀（实测这是 E5 的主因）。
    # ⇒ 判据写成：B 中该 node **不存在**（新测试）或 **存在且 passed**（老测试没坏），
    #    二者取其一即可；B 中「存在但非 passed」= 基线本来就有病，排除（§4.2 原文目的）。
    def b_ok(n):
        return n not in B or B[n] == "passed"

    f2p = sorted(n for n, o in T.items()
                 if o in ("failed", "error") and b_ok(n) and G.get(n) == "passed")
    red_T = sorted(n for n, o in T.items() if o in ("failed", "error"))
    baseline_already_red = sorted(n for n in red_T if n in B and B[n] != "passed")
    # §4.4：node id 必须真的存在于「施加 test_patch 后」的收集集合里（否则判定 harness 选不中它）
    not_collected = [n for n in f2p if n not in collected_t_set]
    if not_collected:
        raise Reject("E6", f"F2P 里有 node id 不在 collection_scope 的收集集合内：{not_collected[:4]}")
    # §1.4 自检：F2P 必须真的来自 test_patch 改到的测试文件，否则「红」与新测试无关
    tp_files = set(re.findall(r"^\+\+\+ b/(\S+)", test_patch, flags=re.M))
    outside_tp = [n for n in f2p if n.split("::")[0] not in tp_files]
    if outside_tp:
        raise Reject("E5", f"F2P 里有 node id 不在 test_patch 改动的测试文件内：{outside_tp[:4]}")
    if not f2p:
        why = ("T 观测点无红灯（新测试也没变红）" if not red_T
               else f"T 红灯全部属于「基线本来就有病」（{len(baseline_already_red)} 条）或在 G 不为绿")
        raise Reject("E5", f"FAIL_TO_PASS 为空集（{why}；T 红灯示例={red_T[:4]}）")
    still_red = sorted(n for n in red_T if n in G and G[n] != "passed")
    if still_red:
        raise Reject("E10", f"应用 gold 后仍红（gold 没修好）：{still_red[:4]}")
    gone = sorted(n for n in f2p if n not in G)
    if gone:
        raise Reject("E10", f"F2P node id 在 G 观测点消失（改名/参数空间变化）：{gone[:4]}")

    # ---------------- P2P（§4.3）
    # 候选池 = B 中 passed 且**在基线的收集集合内**（§4.3 的 collection_scope 白名单）。
    pool = sorted(n for n, o in B.items() if o == "passed" and n in collected_set)
    def green_in_TG(n):
        return (n not in T or T[n] == "passed") and (n not in G or G[n] == "passed")
    p2p_pool = [n for n in pool if n not in f2p and green_in_TG(n)]
    p2p = p2p_pool[:k]
    truncated = max(0, len(p2p_pool) - len(p2p))
    flaky_set = {f["node_id"] for f in flaky}
    hit_flaky = sorted(flaky_set & (set(f2p) | set(p2p)))
    if hit_flaky:
        raise Reject("E8", f"F2P/P2P 命中 flaky：{hit_flaky[:4]}")

    # ---------------- §6.3 wrong_patch（在缓存仓库上生成，不碰实例工作区）
    cache_checkout_base(repo_key, base)
    wrong, wrong_method = build_wrong_patch(d, repo_key, src_files, patch, profile, art_dir, iid)
    if not wrong.strip():
        raise Reject("§6.3", "wrong_patch 三种方法都造不出来")

    # ---------------- §6 三向自校验（同一工作区、同一 venv）
    sc_evidence = []
    # 第 1 向 gold：test_patch + patch ⇒ resolved
    reset_ws(ws)
    apply_patch(ws, test_patch, art_dir=art_dir)
    g1 = apply_patch(ws, patch, art_dir=art_dir)
    if g1.rc != 0:
        raise Reject("E10", f"三向第1向 gold 无法应用：{g1.stderr[-200:]}")
    sel = f2p + p2p
    r_gold = pytest_run(ws, py, sel, "selfcheck_gold.json", art_dir, scope=scope, collected=collected)
    save_run_log(art_dir, "SELFGOLD_gold_three_way", r_gold)
    verdict_gold = judge(r_gold, f2p, p2p)
    sc_evidence.append(rel_art(os.path.join(art_dir, "SELFGOLD_gold_three_way.txt")))
    if verdict_gold != "resolved":
        raise Reject("E10", f"三向第1向未过（gold 期望 resolved，实为 {verdict_gold}）")

    # 第 2 向 空补丁：只 test_patch ⇒ not_resolved / empty_patch
    reset_ws(ws)
    apply_patch(ws, test_patch, art_dir=art_dir)
    r_empty = pytest_run(ws, py, sel, "selfcheck_empty.json", art_dir, scope=scope, collected=collected)
    save_run_log(art_dir, "SELMPTY_zero_byte_diff", r_empty)
    verdict_empty = judge(r_empty, f2p, p2p)
    sc_evidence.append(rel_art(os.path.join(art_dir, "SELMPTY_zero_byte_diff.txt")))
    if verdict_empty != "not_resolved":
        raise Reject("§6.4", f"三向第2向不过：空补丁被判 {verdict_empty}（harness 严重失效）")

    # 第 3 向 错补丁 ⇒ not_resolved 但补丁能 apply
    reset_ws(ws)
    apply_patch(ws, test_patch, art_dir=art_dir)
    if wrong_method == "reverse_of_gold":
        # §6.3 方法 3「反向法」的字面含义是「在 gold 之上再反向应用 gold」= 回到基线。
        # 但那样得到的是**完全没有修复**的缺陷态，与第 2 向（空补丁）等价 ⇒ 判别力信息量重复，
        # 且直接 `git apply -R <gold>` 会因为工作区还没打 gold 而必然失败。
        # 正确做法：test_patch + gold，再 `git apply -R <gold>` ⇒ 等价于基线（缺陷态）。
        gg = apply_patch(ws, patch, art_dir=art_dir)
        if gg.rc != 0:
            raise Reject("§6.4", f"三向第3向（反向法）gold 无法应用：{gg.stderr[-200:]}")
        a3 = apply_patch(ws, patch, reverse=True, art_dir=art_dir)
    else:
        a3 = apply_patch(ws, wrong, art_dir=art_dir)
    if a3.rc != 0:
        raise Reject("§6.4", f"三向第3向 wrong_patch 无法 apply（期望能 apply）：{a3.stderr[-200:]}")
    r_wrong = pytest_run(ws, py, sel, "selfcheck_wrong.json", art_dir, scope=scope, collected=collected)
    save_run_log(art_dir, f"SELFWRONGLY_{wrong_method}", r_wrong)
    verdict_wrong = judge(r_wrong, f2p, p2p)
    sc_evidence.append(rel_art(os.path.join(art_dir, f"SELFWRONGLY_{wrong_method}.txt")))
    if verdict_wrong != "not_resolved":
        raise Reject("§6.4", f"三向第3向不过：错补丁被判 {verdict_wrong}（判别力不足）")
    reset_ws(ws)
    if not workspace_clean(ws):
        raise Reject("§9.1", "三向自校验后工作区不为空")

    # ---------------- problem_statement（§3.4，泄漏检查自动执行）
    ps, ps_status, ps_detail = P.build_problem_statement(cand["subject"], cand.get("body", ""), f2p, patch)
    if ps_status == "rejected":
        raise Reject("E9", ps_detail)

    known_limits = list(profile.get("repo_notes", []))
    if cand["dropped"]:
        known_limits.append("丢弃非 .py 改动文件（CI/docs/changelog/toml）：" + ", ".join(cand["dropped"][:6]))
    if not p2p:
        known_limits.append("该实例无回归保护集（PASS_TO_PASS 为空）")
    if truncated:
        known_limits.append(f"P2P truncated to {len(p2p)} of {len(p2p_pool)}")
    baseline_not_green = sorted(n for n, o in B.items() if o not in ("passed",))
    if len(baseline_not_green) > 5:
        known_limits.append(f"基线上有 {len(baseline_not_green)} 个非绿测试（不计入 F2P/P2P）")
    if baseline_already_red:
        known_limits.append(
            f"{len(baseline_already_red)} 个 T 红灯在基线本来就非绿（按 §4.2 不属本实例要修的东西，已排除）")
    new_nodes = sorted(n for n in f2p if n not in B)
    if new_nodes:
        known_limits.append(
            f"F2P 全部/部分为**新增测试**（基线上不存在该 node）：{len(new_nodes)}/{len(f2p)} 条，"
            "符合 SWE-bench 的 FAIL_TO_PASS 语义")
    if ps_status == "trimmed":
        known_limits.append(f"problem_statement 按 §3.4 删句：{ps_detail}")
    excluded_tests = [{"node_id": n, "reason": "E10/GREEN_TO_RED_IN_G"} for n in gone]
    for f in flaky:
        excluded_tests.append({"node_id": f["node_id"], "reason": "flaky_between_repeats"})

    inst = {
        "instance_id": iid,
        "repo": profile["owner_name"],
        "base_commit": base,
        "problem_statement": ps,
        "patch": patch,
        "test_patch": test_patch,
        "FAIL_TO_PASS": f2p,
        "PASS_TO_PASS": p2p,
        "version": VERSION,
        "env_spec": {
            "python": BASE_PY,
            "venv_python": py,
            "python_version_constraint": ">=3.10",
            "install_cmd": [f"{py} -m pip install --disable-pip-version-check " + " ".join(profile["pip_deps"])],
            "venv_root": VENV_ROOT,
            "requirements_source": "repo:requirements.txt" if os.path.exists(os.path.join(ws, "requirements.txt")) else "repo:pyproject.toml",
            "requires_network": True,
            "network_note": "仅建 venv/装依赖阶段允许联网；agent 运行与 pytest 判定阶段不访问外网域名",
        },
        "test_cmd_template": ("<venv_python> -m pytest -p no:cacheprovider -q --timeout=120 "
                              "-c _eval_pytest.ini --rootdir <repo> --json-report "
                              "--json-report-file <artifacts>/<name>.json {node_ids}"),
        "collection_scope": scope,
        "flaky_known": bool(flaky),
        "flaky_evidence": {"differences": flaky, "evidence_paths": sc_evidence} if flaky else None,
        "source_repo_url": profile["url"],
        "fix_commit": sha,
        "fix_commit_date": cand["date_iso"],
        "construction_method": "revert_of_real_fix_commit",
        "gold_selfcheck": {
            "gold": "resolved", "empty": "unresolved", "wrong": "unresolved",
            "verdict_classes": {"gold": "agent_result", "empty": "empty_patch", "wrong": "agent_result"},
            "wrong_patch_method": wrong_method,
            "evidence_paths": sc_evidence,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
        "wrong_patch": wrong,
        "excluded_tests": excluded_tests,
        "set_membership": "pending",
        "sample_rationale": "",
        "known_limits": known_limits + [ENV_LIMIT_NOTE, CLEAN_LIMIT_NOTE],
        "data_paths": {
            "patch_file": rel_art(os.path.join(art_dir, "gold.patch")),
            "test_patch_file": rel_art(os.path.join(art_dir, "test.patch")),
            "wrong_patch_file": rel_art(os.path.join(art_dir, "wrong.patch")),
            "build_log_dir": rel_art(art_dir),
        },
        "evidence": {
            "scan_head_ref": head_ref,
            "fix_commit_show": rel_art(os.path.join(art_dir, "fix_commit_show.txt")),
            "B1_baseline": rel_art(os.path.join(art_dir, "B1_baseline.txt")),
            "B2_baseline": rel_art(os.path.join(art_dir, "B2_baseline.txt")),
            "T1_test_only": rel_art(os.path.join(art_dir, "T1_test_only.txt")),
            "T2_test_only": rel_art(os.path.join(art_dir, "T2_test_only.txt")),
            "G1_test_plus_gold": rel_art(os.path.join(art_dir, "G1_test_plus_gold.txt")),
            "G2_test_plus_gold": rel_art(os.path.join(art_dir, "G2_test_plus_gold.txt")),
            "selfcheck_gold": rel_art(os.path.join(art_dir, "SELFGOLD_gold_three_way.txt")),
            "selfcheck_empty": rel_art(os.path.join(art_dir, "SELMPTY_zero_byte_diff.txt")),
            "selfcheck_wrong": rel_art(os.path.join(art_dir, f"SELFWRONGLY_{wrong_method}.txt")),
            "venv_log": rel_art(os.path.join(art_dir, "venv.log")),
            "workspace": ws,
        },
        "metrics_snapshot": {
            "baseline_wall_seconds": runs["B"][0]["wall_seconds"],
            "test_only_wall_seconds": runs["T"][0]["wall_seconds"],
            "gold_wall_seconds": runs["G"][0]["wall_seconds"],
            "collected_in_scope": len(runs["B"][0]["collected"]),
            "f2p_count": len(f2p),
            "p2p_pool_count": len(p2p_pool),
        },
    }
    write_text(os.path.join(art_dir, "gold.patch"), patch)
    write_text(os.path.join(art_dir, "test.patch"), test_patch)
    write_text(os.path.join(art_dir, "wrong.patch"), wrong)
    inst["_observed"] = {"B": B, "T": T, "G": G, "f2p": f2p, "p2p": p2p}
    return inst


def judge(r, f2p, p2p):
    """§5.1 的判定逻辑（构造期用于三向自校验）。"""
    if r["timed_out"]:
        return "timeout"
    if r["returncode"] >= 2 or r["meta"]["errors"]:
        return "infra_error"
    res = r["results"]
    bad = [n for n in f2p + p2p if res.get(n) != "passed"]
    return "not_resolved" if bad else "resolved"


# ---------------------------------------------------------------- 落盘 / 抽样

def instance_path(iid):
    return os.path.join(INSTANCES_DIR, f"{iid}.json")


def dump_instance(inst):
    obj = {k: v for k, v in inst.items() if not k.startswith("_")}
    obj["schema_sha256"] = sha_of(normalize_json({k: v for k, v in obj.items() if k != "schema_sha256"}))
    write_text(instance_path(obj["instance_id"]), json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return obj


def append_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_existing_instances():
    out = {}
    if not os.path.isdir(INSTANCES_DIR):
        return out
    for name in sorted(os.listdir(INSTANCES_DIR)):
        if name.endswith(".json") and not name.startswith("_"):
            try:
                obj = json.loads(read_text(os.path.join(INSTANCES_DIR, name)))
            except Exception as exc:  # noqa: BLE001
                log(f"读取 {name} 失败：{exc}")
                continue
            out[obj.get("instance_id", name[:-5])] = obj
    return out


def rejected_history():
    rows = []
    if os.path.exists(REJECTED_PATH):
        for line in read_text(REJECTED_PATH).splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def record_rejection(repo_key, cand, code, detail, stage="candidate"):
    row = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "stage": stage,
        "repo": REPOS[repo_key]["owner_name"],
        "repo_key": repo_key,
        "fix_commit": cand.get("sha"),
        "fix_commit_date": cand.get("date_iso"),
        "subject": cand.get("subject"),
        "rule": code,
        "reason": detail,
        "prereg_reference": f"eval/PREREGISTRATION.md {code.split('/')[0] if code.startswith('E') else code}",
    }
    with open(REJECTED_PATH, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    log(f"REJECT {repo_key} {row['fix_commit'][:7] if row['fix_commit'] else '-'} {code}: {detail[:120]}")
    return row


def rebuild_jsonl(instances):
    chosen = [i for i in instances.values() if i.get("set_membership") in ("pilot-12", "reserve")]
    append_jsonl(JSONL_PATH, chosen)
    return len(chosen)


def write_sampling_log(per_repo, timeline, totals):
    lines = [
        f"# {SET_NAME} 抽样日志（由 eval/build_subset.py 生成，人不得改）",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        f"- 对应规则节：§7.2 候选池构建 / §7.3 抽样规则 / §3.1 C1–C4 / §3.2 E0–E10",
        f"- 时间窗（§3.3 C3 钉死）：[{WINDOW_START}, {WINDOW_END}]",
        f"- 排序键：`(fix_commit_date 升序, instance_id 字典序)`；quota={QUOTA_PER_REPO}，"
        f"单仓库上限 {MAX_PER_REPO}，目标 {TARGET_TOTAL} 条",
        "",
        "## 候选池与各步计数",
        "",
        "| 仓库 | 窗内提交 | C1+C2 通过 | 进入实测 | 三向全过 | 入选 pilot | reserve |",
        "|---|---|---|---|---|---|---|",
    ]
    for rk, prof in REPOS.items():
        st = per_repo.get(rk, {})
        lines.append(f"| {prof['owner_name']} | {st.get('scanned', '-')} | {st.get('candidates', '-')} | "
                     f"{st.get('attempted', '-')} | {st.get('qualified', '-')} | {st.get('pilot', '-')} | "
                     f"{st.get('reserve', '-')} |")
    lines += ["", "## 排除流水（规则代号见 §3.2）", ""]
    for row in timeline:
        lines.append(f"- `{row.get('repo_key', '-')}` `{(row.get('fix_commit') or '-')[:7]}` "
                     f"**{row.get('rule')}** — {row.get('reason')}")
    if not timeline:
        lines.append("- （本轮无排除）")
    lines += [
        "",
        "## 入选序列（机械理由）",
        "",
    ]
    for iid, why in totals.items():
        lines.append(f"- `{iid}` — {why}")
    lines += [
        "",
        "## 判定不了的兜底（§2.3）",
        "",
        "- R6「不需要网络即可跑测试」：本机无法真正断网 ⇒ 按 §2.3 **保守处理**：判定阶段不主动访问外网域名，"
        "并在结果节声明「网络未强制隔离」这一局限。",
        "- 快照测试占比（§2.2）：四个仓库均无 golden-file 快照机制（tabulate 的期望值写在测试源码字符串里，"
        "属于普通断言而非外部 golden 文件），故不按 §2.2 第二条弃用。",
        "- §4.2 的实现口径（必须在结果节复述）：F2P 的准入是「B 中该 node **不存在**（= 本提交新增的测试）"
        "**或**存在且 passed」，排除的只是「B 中已存在且本来就非绿」的 node。原文「B 中 n 存在且 passed」"
        "的字面读法会把「修复提交顺手加回归测试」这一类**最标准**的 FAIL_TO_PASS 全部误杀；"
        "本构造器按该条**写明的目的**（排除基线上本来就红的测试）实现，差异已记入本节与实例 known_limits。",
        "",
    ]
    write_text(SAMPLING_LOG, "\n".join(lines) + "\n")


# ---------------------------------------------------------------- build 主流程

def build(args):
    os.makedirs(INSTANCES_DIR, exist_ok=True)
    os.makedirs(ARTIFACT_ROOT, exist_ok=True)
    existing = load_existing_instances()
    rejected_rows = rejected_history()
    seen_reject_keys = {(r.get("repo_key"), (r.get("fix_commit") or "")[:7], r.get("rule")) for r in rejected_rows}

    per_repo = {}
    timeline = []
    qualified_by_repo = {}

    repo_keys = [args.only_repo] if args.only_repo else list(REPOS.keys())
    for rk in repo_keys:
        profile = REPOS[rk]
        try:
            ensure_cache(rk)
        except Reject as exc:
            timeline.append(record_rejection(rk, {}, exc.code, exc.detail, stage="repo"))
            continue
        cands, all_entries, head_ref = scan_candidates(rk)
        per_repo[rk] = {"scanned": len(all_entries), "candidates": len(cands),
                        "attempted": 0, "qualified": 0, "pilot": 0, "reserve": 0}
        qualified_by_repo[rk] = []
        ordered = sorted(cands, key=lambda c: (c["date_iso"], c["sha"][:7]))
        limit = args.max_per_repo or MAX_PER_REPO
        for cand in ordered:
            # 停止条件只有两条：①本仓库已凑够「配额 + reserve 名额」的合格候选；
            # ②已试完候选序列（或达到 --max-attempts 的显式上限）。
            # ⚠ 不要用 `limit*3` 这种「按已尝试次数」的隐式上限 —— §7.3 要求按排序**取到足够的
            # 合格候选为止**，而多数真实修复提交会因 E1/E5/E9 被淘汰，提前截断会让本来能凑满的
            # 仓库假性不足（实测 more-itertools 因此只拿到 1 条）。
            if per_repo[rk]["qualified"] >= QUOTA_PER_REPO + RESERVE_PER_REPO \
                    or per_repo[rk]["attempted"] >= args.max_attempts:
                break
            iid = instance_id_for(rk, cand['sha'][:7])
            if iid in existing and existing[iid].get("set_membership") in ("pilot-12", "reserve"):
                qualified_by_repo[rk].append(existing[iid])
                per_repo[rk]["qualified"] += 1
                per_repo[rk]["attempted"] += 1
                log(f"SKIP(idempotent reuse) {iid}")
                continue
            key = (rk, cand["sha"][:7], "built")
            if key in seen_reject_keys:
                continue
            per_repo[rk]["attempted"] += 1
            cand["body"] = git(cache_dir(rk), "show", "-s", "--format=%b", cand["sha"]).stdout
            log(f"TRY {iid}  ({cand['date_iso']})  {cand['subject'][:60]}")
            try:
                inst = build_candidate(rk, cand, profile, head_ref)
            except Reject as exc:
                row = record_rejection(rk, cand, exc.code, exc.detail)
                timeline.append(row)
                seen_reject_keys.add((rk, cand["sha"][:7], exc.code))
                continue
            except Exception as exc:  # noqa: BLE001
                row = record_rejection(rk, cand, "INFRA", f"{type(exc).__name__}: {exc}")
                timeline.append(row)
                log(traceback.format_exc()[-1500:])
                continue
            obj = dump_instance(inst)
            existing[iid] = obj
            qualified_by_repo[rk].append(obj)
            per_repo[rk]["qualified"] += 1
            log(f"QUALIFIED {iid}  F2P={len(obj['FAIL_TO_PASS'])} P2P={len(obj['PASS_TO_PASS'])}")

    # ---------------- §7.3 抽样：按 (fix_commit_date 升序, instance_id 字典序)
    # ⚠ 抽样必须是**全局**的：候选池 = 磁盘上**全部**已构造的实例（不只是本轮触及的那些）。
    # 否则 `--only-repo X` 的局部运行会拿「只有 X 的候选池」重跑 §7.3，把其它仓库的实例
    # 挤掉/误标（实测会把 pilot-12.jsonl 从 23 行缩成 1 行）。
    # 磁盘上的实例都是「已通过 §3–§6 全部判据」才会落盘的，故直接入池；
    # 形审是否通过由 `--verify-all` 独立负责（职责分离）。
    rk_of_repo = {v["owner_name"]: k for k, v in REPOS.items()}
    qualified_all = {}
    for iid, o in load_existing_instances().items():
        rk = rk_of_repo.get(o.get("repo"))
        if rk is None:
            continue                      # 不属于本清单的仓库：不参与本文件的抽样
        qualified_all.setdefault(rk, []).append(o)
    flat = []
    for rk, objs in qualified_all.items():
        for o in objs:
            flat.append((o["fix_commit_date"], o["instance_id"], rk, o))
    flat.sort(key=lambda t: (t[0], t[1]))
    counts = {}
    chosen = []
    for date, iid, rk, obj in flat:
        if len(chosen) >= TARGET_TOTAL:
            break
        if counts.get(rk, 0) >= QUOTA_PER_REPO:
            continue
        counts[rk] = counts.get(rk, 0) + 1
        obj["set_membership"] = "pilot-12"
        obj["sample_rationale"] = (
            f"§7.3：仓库 {REPOS[rk]['owner_name']} 排序后候选序列中第 {counts[rk]} 个通过 §6 三向自校验的候选"
        )
        chosen.append(obj)
    # 补足：各仓库第 4、5 个合格候选，单仓库上限 5
    if len(chosen) < TARGET_TOTAL:
        for date, iid, rk, obj in flat:
            if len(chosen) >= TARGET_TOTAL:
                break
            if any(c["instance_id"] == iid for c in chosen):
                continue
            if counts.get(rk, 0) >= MAX_PER_REPO:
                continue
            counts[rk] = counts.get(rk, 0) + 1
            obj["set_membership"] = "pilot-12"
            obj["sample_rationale"] = (
                f"§7.3 补足段：前导仓库配额未满 {TARGET_TOTAL} 时，按排序取 {REPOS[rk]['owner_name']} 的第 "
                f"{counts[rk]} 个合格候选（单仓库上限 {MAX_PER_REPO}）"
            )
            chosen.append(obj)
    chosen_ids = {c["instance_id"] for c in chosen}
    reserve_assigned = {}
    for date, iid, rk, obj in flat:
        if iid in chosen_ids:
            continue
        if reserve_assigned.get(rk, 0) >= RESERVE_PER_REPO:
            continue
        reserve_assigned[rk] = reserve_assigned.get(rk, 0) + 1
        obj["set_membership"] = "reserve"
        obj["sample_rationale"] = (
            f"§7.3：紧随其后的第 {reserve_assigned[rk]} 个合格候选，标 reserve，仅在正式实例判 "
            f"excluded_infra 时按顺序顶替"
        )
        chosen.append(obj)

    for rk in per_repo:
        per_repo[rk]["pilot"] = sum(1 for c in chosen if c["set_membership"] == "pilot-12"
                                   and c["repo"] == REPOS[rk]["owner_name"])
        per_repo[rk]["reserve"] = sum(1 for c in chosen if c["set_membership"] == "reserve"
                                     and c["repo"] == REPOS[rk]["owner_name"])
    # 局部运行（--only-repo）时 per_repo 只含被触及的仓库 ⇒ 计数表要补齐其余仓库，
    # 否则抽样日志会显示「-」而让人误以为那些仓库没扫过（§7.2 要求计数落盘）。
    for rk, prof in REPOS.items():
        per_repo.setdefault(rk, {"scanned": "-", "candidates": "-", "attempted": "-",
                                 "qualified": "-", "pilot": 0, "reserve": 0})
        st = per_repo[rk]
        st["pilot"] = sum(1 for c in chosen if c["set_membership"] == "pilot-12"
                          and c["repo"] == prof["owner_name"])
        st["reserve"] = sum(1 for c in chosen if c["set_membership"] == "reserve"
                            and c["repo"] == prof["owner_name"])
    # 重新冻结 JSON（membership 变了要重算 hash）
    for obj in chosen:
        dump_instance(obj)
    # 未被本轮触及的既有实例保持原样
    all_objs = load_existing_instances()
    rebuilt = rebuild_jsonl(all_objs)
    rationale_map = {c["instance_id"]: c["sample_rationale"] for c in chosen}
    # 排除流水来自**全量** `_rejected.jsonl`（不是本轮新增的那些）—— 抽样日志是累积记录，
    # 局部运行不能把它截断成本轮的几条。
    write_sampling_log(per_repo, rejected_history(), rationale_map)
    n_pilot = sum(1 for c in chosen if c["set_membership"] == "pilot-12")
    repos_used = len({c["repo"] for c in chosen if c["set_membership"] == "pilot-12"})
    log(f"RESULT pilot={n_pilot}/{TARGET_TOTAL} reserve={len(chosen) - n_pilot} "
        f"repos={repos_used} jsonl_rows={rebuilt}")
    if n_pilot < TARGET_TOTAL:
        log("⚠ 未凑满 12 条：按 §7.1 如实报告可得数量，不降低判据")
    return n_pilot


# ---------------------------------------------------------------- verify-all

def verify_all(args):
    ok = True
    objs = load_existing_instances()
    if not objs:
        print("verify-all: eval/instances/*.json 为空 —— 无实例可校验")
        return 1
    pilot = []
    for iid, obj in sorted(objs.items()):
        errs = S.validate(obj, normalize_json, CACHE_ROOT)
        # §1.4.2：patch/test_patch 并集应等于该 commit 的「非测试 .py ∪ 测试文件」全集
        rk = next((k for k, v in REPOS.items() if v["owner_name"] == obj.get("repo")), None)
        if rk and os.path.isdir(cache_dir(rk)):
            d = cache_dir(rk)
            if git(d, "cat-file", "-e", f'{obj.get("fix_commit")}^{{commit}}').rc != 0:
                errs.append(f"仓库/commit 不可解析：fix_commit={obj.get('fix_commit')}")
            if git(d, "cat-file", "-e", f'{obj.get("base_commit")}^{{commit}}').rc != 0:
                errs.append(f"仓库/commit 不可解析：base_commit={obj.get('base_commit')}")
            names = git(d, "show", "--pretty=format:", "--name-only", obj["fix_commit"]).stdout
            paths = [l.strip().replace("\\", "/") for l in names.splitlines() if l.strip()]
            src, test, _ct, dropped = classify_files(paths)
            union = set(re.findall(r"^\+\+\+ b/(\S+)", obj["patch"], flags=re.M)) | \
                set(re.findall(r"^\+\+\+ b/(\S+)", obj["test_patch"], flags=re.M))
            expect = {p for p in src + test}
            if union != expect:
                errs.append(f"§1.4.2 patch∪test_patch 文件集合 {sorted(union)} != 该 commit 的非测试.py∪测试 {sorted(expect)}")
            if dropped:
                pass
        # §4.4：F2P 必须能在归档的「test_patch 之后」collect 证据里找到；P2P 在基线 collect 里找
        logdir = os.path.join(CACHE_ROOT, (obj.get("data_paths") or {}).get("build_log_dir") or "")
        def _read_ids(name):
            p = os.path.join(logdir, name)
            if not os.path.exists(p):
                return None
            out = set()
            for line in read_text(p).splitlines():
                line = line.strip()
                if "::" in line and not line.startswith(("$", "----", "exit_", "timed_", "collected=")):
                    out.add(line)
            return out
        coll_base = _read_ids("collect_only.txt")
        coll_t = _read_ids("collect_test_only.txt")
        if coll_base is None:
            errs.append(f"缺少 collection_scope 收集证据：{os.path.join(logdir, 'collect_only.txt')}")
        if coll_t is None:
            errs.append(f"缺少 test_patch 后收集证据：{os.path.join(logdir, 'collect_test_only.txt')}")
        if coll_base is not None:
            if len(coll_base) < 20:
                errs.append(f"§2 R3 collection_scope 只收集到 {len(coll_base)} 个 node id (< 20)")
            if len(obj.get("PASS_TO_PASS") or []) > min(P2P_CAP, len(coll_base)):
                errs.append("§4.3 PASS_TO_PASS 超过 K = min(60, len(collection_scope)) 上限")
            miss_p = [n for n in (obj.get("PASS_TO_PASS") or []) if n not in coll_base]
            if miss_p:
                errs.append(f"§4.3 有 P2P node id 不在基线收集集合里：{miss_p[:4]}")
        if coll_t is not None:
            miss_f = [n for n in (obj.get("FAIL_TO_PASS") or []) if n not in coll_t]
            if miss_f:
                errs.append(f"§4.2 有 F2P node id 不在 test_patch 后收集集合里：{miss_f[:4]}")
        if obj.get("set_membership") == "pilot-12":
            pilot.append(iid)
        if errs:
            ok = False
            print(f"FAIL {iid}")
            for e in errs:
                print(f"      - {e}")
        else:
            print(f"OK   {iid}  membership={obj.get('set_membership')} "
                  f"F2P={len(obj['FAIL_TO_PASS'])} P2P={len(obj['PASS_TO_PASS'])}")
    n_pilot = len(pilot)
    repos = {objs[i]["repo"] for i in pilot}
    print(f"verify-all: 实例总数={len(objs)} pilot-12 成员={n_pilot} 跨仓库={len(repos)} "
          f"{sorted(repos)}")
    if n_pilot != TARGET_TOTAL:
        print(f"      - pilot-12 成员数 {n_pilot} != {TARGET_TOTAL}（§7.1：不如实凑满需在结果节声明）")
        ok = False
    per_repo = {}
    for i in pilot:
        per_repo[objs[i]["repo"]] = per_repo.get(objs[i]["repo"], 0) + 1
    if len(repos) < 3:
        print(f"      - 跨仓库数 {len(repos)} < 3（§7.1）")
        ok = False
    for r, c in per_repo.items():
        if c > MAX_PER_REPO:
            print(f"      - 仓库 {r} 有 {c} 条 > 单仓库上限 {MAX_PER_REPO}（§7.1）")
            ok = False
    if not os.path.exists(JSONL_PATH):
        print(f"      - 缺少批次 JSONL：{JSONL_PATH}")
        ok = False
    print("verify-all:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def scan(args):
    for rk in REPOS:
        ensure_cache(rk)
        cands, all_entries, head_ref = scan_candidates(rk)
        print(f"== {REPOS[rk]['owner_name']}  窗内提交 {len(all_entries)} 条，C1+C2 通过 {len(cands)} 条")
        for c in sorted(cands, key=lambda x: (x["date_iso"], x["sha"][:7]))[: args.limit]:
            print(f"   {c['date_iso']}  {c['sha'][:7]}  src={len(c['src'])} test={len(c['case_tests'])}  {c['subject'][:70]}")


def main(argv=None):
    # GBK 控制台兜底：打印不下的字符用 '?'，不改文件读写（UTF-8 一律走 write/read 工具）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="P0 实例构造器（判据见 eval/PREREGISTRATION.md）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--build", action="store_true", help="构造实例（幂等）")
    g.add_argument("--verify-all", action="store_true", help="校验全部实例定义，失败非零退出")
    g.add_argument("--scan", action="store_true", help="只打印候选池统计")
    ap.add_argument("--only-repo", help="只处理某个 repo_key")
    ap.add_argument("--max-per-repo", type=int, default=MAX_PER_REPO, help="单仓库最多**入选**条数")
    ap.add_argument("--max-attempts", type=int, default=60,
                    help="单仓库最多**尝试构造**的候选数（防单仓库扫太久；不参与入选规则）")
    ap.add_argument("--limit", type=int, default=20, help="--scan 每仓库打印条数")
    args = ap.parse_args(argv)
    if args.verify_all:
        return verify_all(args)
    if args.scan:
        scan(args)
        return 0
    build(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
