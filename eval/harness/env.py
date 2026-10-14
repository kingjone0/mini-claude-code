"""工作区与 venv 的生命周期（预注册 §8.1 / §8.2 / §9.1）。

**为什么另起一份而不 import build_subset**：`build_subset.py` 是构造器，模块级就带着
「四个目标仓库的画像 + 扫描窗口 + 抽样规则」这些**构造期**状态；harness 只吃已经冻结的
实例 JSON，把构造器的仓库清单拖进来会让「评测时用哪个仓库」变成可以偷偷改的东西。
两边共享的是**约定**（目录、git 参数、pth 挂载法）与 `eval/lib/` 里的纯函数，不是模块。

三条硬约定（与预注册 §9.3 一致，改这里等于改全局）：
    E:\\aicoding\\eval-cache   上游克隆（harness **只读**它，绝不往它里面写）
    E:\\aicoding\\eval-runs    运行工作区（harness 唯一的写目标）
    E:\\aicoding\\eval-venvs   每实例独立 venv
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time

GIT = r"E:\Program Files\Git\cmd\git.exe"
BASE_PY = r"D:\Anaconda3\envs\aicoding\python.exe"
CACHE_ROOT = r"E:\aicoding\eval-cache"
RUNS_ROOT = r"E:\aicoding\eval-runs"
VENV_ROOT = r"E:\aicoding\eval-venvs"

# ⚠ 本机 git 全局 core.autocrlf=true（E:\Program Files\Git\etc\gitconfig）：checkout 会把工作树
# 变成 CRLF，而补丁正文是 LF => `git apply` 报 "patch does not apply"。所有涉及补丁/工作树的
# git 调用一律钉死 autocrlf=false（与构造器同一处理，见 t2 的 CLEAN_LIMIT_NOTE）。
GIT_BASE = [GIT, "-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]

# 判定用的临时 pytest 配置：本机只有 pytest 9.1.1，仓库自带 ini 的旧选项在新版下会
# `_validate_config_options` INTERNALERROR(exit 3) => 用 `-c <临时 ini>` **替换**仓库配置。
INI_NAME = "_eval_pytest.ini"

_DROP_ENV = ("PYTHONPATH", "PYTEST_ADDOPTS", "PYTHONHOME", "AGENT_REPO_ROOT")


class InfraError(Exception):
    """基础设施错误：网络 / 依赖 / venv / 磁盘 / 权限 / 缓存缺失。

    ⚠ 与「agent 改错了」严格分开（§5.4）：infra 错误**不计入 resolved 分母**，
    也不计作 agent 失败。抛这个异常即表示「这次判定链路没跑通」。
    """


# ---------------------------------------------------------------- 子进程

class Res:
    """一次子进程运行的结果。`timed_out` 与 `rc` **分开存**，这是 §8.4 的前提。"""

    def __init__(self, cmd, rc, out, err, wall, timed_out):
        self.cmd, self.rc, self.stdout, self.stderr = cmd, rc, out, err
        self.wall_seconds, self.timed_out = wall, timed_out

    def to_dict(self):
        return {"cmd": self.cmd, "exit_code": self.rc, "timed_out": self.timed_out,
                "wall_seconds": self.wall_seconds}


def minimal_env(extra=None):
    """§8.3：最小基线环境 —— 不透传父进程的站点/pytest/agent 变量，强制 UTF-8。

    ⚠ 控制台是 GBK(936)：子进程输出必须显式 UTF-8，否则中文/⚠ 会 UnicodeEncodeError，
    把真正的报错盖住（第二阶段踩过）。
    """
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


def sh(cmd, cwd=None, timeout=600, env=None):
    """跑一条命令。

    ⚠ §8.4：Windows 上被强杀的进程以**退出码 1** 收尾，与 pytest「有测试失败」的 exit 1
    不可区分 => 这里只负责**如实记下** `timed_out` 标记，**解释顺序由 judge 决定**
    （先查超时、再看退出码）。顺序颠倒会把 timeout 误记成 not_resolved。
    """
    started = time.time()
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace",
                         env=env if env is not None else minimal_env())
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
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


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 实例与路径

def repo_key_of(repo: str) -> str:
    """`<owner>/<name>` -> 缓存目录名（与构造器的 repo_key 同一约定）。"""
    return repo.replace("/", "__")


def venv_tag(base_commit: str) -> str:
    """venv 命名里的版本号：base_commit 前 8 位（**保留**，用于人读与旧目录识别）。"""
    return base_commit[:8]


def venv_dir(instance: dict, run_id: str = "") -> str:
    """§8.2：每实例独立 venv —— **D1 修复后按 (实例, run) 隔离**。

    ⚠ 为什么改成含 run_id（t8 / D1）：
    原命名 `eval-venvs/<repo>-<base_commit[:8]>` **只由 repo+base 决定**，于是
    「两个 run 跑同一实例」共用**同一个 venv**，而 editable 挂载点是写在 venv 里的
    `.pth` ⇒ 两个 run 抢同一个挂载位。`.pth` 在解释器启动时按**文件名排序**逐个 exec
    并 `insert(0, ...)`，胜负由文件名决定、与「谁先跑」无关 —— 必然有一个 run 拿到
    别人的代码（实测复现率 6/6 轮，含**静默误判**）。

    试过的两条「绕过」。都**不行**，记录在此以免重蹈：
      · 把 run_id 加进 `.pth` 文件名 ⇒ 只是不再同名覆盖，**两个挂载仍同时存在**，
        胜负依旧由排序决定；
      · 用 `PYTHONPATH` 指定自己的工作区 ⇒ **实测无效**：`.pth` 的 `insert(0,...)`
        发生在 site 阶段，把 PYTHONPATH 的条目挤到后面（实测 PYTHONPATH=A/.pth=B
        时导入到 BBB）。

    唯一干净的办法是**消掉共享可变状态本身**：venv 也按 run 隔离。
    代价实测仅 **4.8s**（建 venv 2.2s + 装依赖 2.5s，走本地 pip 缓存），
    相比「判定结果不可信」这个代价可以忽略。
    """
    base = f"{repo_key_of(instance['repo'])}-{venv_tag(instance['base_commit'])}"
    if not run_id:
        return os.path.join(VENV_ROOT, base)          # 向后兼容：不带 run 的旧路径
    tag = sha256_text(run_id)[:8]
    return os.path.join(VENV_ROOT, f"{base}-{tag}")


def run_root(run_id: str) -> str:
    return os.path.join(RUNS_ROOT, run_id)


def instance_dir(run_id: str, iid: str) -> str:
    """§8.1：`eval-runs/<run_id>/<instance_id>/`；工作区在其下的 `repo/`。"""
    return os.path.join(run_root(run_id), iid)


def ws_path(run_id: str, iid: str) -> str:
    return os.path.join(instance_dir(run_id, iid), "repo")


def art_dir(run_id: str, iid: str) -> str:
    """本实例的工件目录。**判定用的临时文件全写这里，不写进 repo/**（§9.1）。"""
    return os.path.join(instance_dir(run_id, iid), "artifacts")


def venv_py(instance: dict, run_id: str = "") -> str:
    return os.path.join(venv_dir(instance, run_id), "Scripts", "python.exe")


def editable_pth_name(instance_id: str, run_id: str) -> str:
    """editable 挂载点的文件名 —— **含 instance_id 与 run_id**。

    ⚠ D1（t4 独立复核报的 blocker，t8 修）：原实现是
    `_eval_editable_<sha256(instance_id)[:16]>.pth` —— **只由 instance_id 决定**。
    而 venv 是按 `<repo_key>-<base_commit[:8]>` 共享的 ⇒ **同一 base 的多条实例共用一个 venv**，
    于是「两个 run 跑同一实例」会并发写**同一个 .pth 文件**，后写者覆盖先写者。
    实测复现率 6/6 轮（4 轮被 import 自检拦成 infra_error，2 轮**静默误判**）。

    文件名含 run_id 只是**消掉同名覆盖**；真正保证隔离的是
    `mount_slots` 机制（见下）—— 它让「同一实例同一时刻只有一个活跃挂载」，
    并在冲突时让后来者**主动让位**（判 infra 重试），而不是去抢。
    """
    key = sha256_text(f"{instance_id}|{run_id}")[:16]
    return f"_eval_editable_{key}.pth"


def editable_pth_path(instance: dict, run_id: str) -> str:
    vd = venv_dir(instance, run_id)
    return os.path.join(vd, "Lib", "site-packages", editable_pth_name(instance["instance_id"], run_id))


def pkg_path_env(ws: str, import_name: str) -> dict:
    """判定子进程的环境：把本工作区也放进 `PYTHONPATH`。

    ⚠ **这不是隔离的实现手段**，只是锦上添花。实测结论写在这里以免被误当成保证：
    当共享 venv 里存在一个指向**别的工作区**的 `.pth` 时，`PYTHONPATH` **压不过它** ——
    `.pth` 的 `insert(0, ...)` 发生在 site 阶段，会把 PYTHONPATH 的条目挤到后面
    （实测 PYTHONPATH=A 且 .pth=B 时 `import click` 得到 B）。
    D1 的真正修法是**每个 run 一个 venv**（见 venv_dir），共享可变状态被消掉，
    根本不存在第二个挂载点。

    `PYTHONPATH` 保留是因为它对「扁平布局、包里没有 `__init__.py` 的场景」仍有帮助，
    且对本进程独有、不引入竞态。
    """
    from ..lib import editable as ED

    parent = ED.package_parent(ws, import_name)
    entries = []
    if parent:
        entries.append(parent)
    if os.path.basename(ws) == "repo":
        entries.append(ws)
    return {"PYTHONPATH": os.pathsep.join(entries)}


def pytest_env(ws: str, import_name: str) -> dict:
    """判定子进程的环境（见 pkg_path_env 的说明）。"""
    return pkg_path_env(ws, import_name)


def assert_pkg_from_workspace(py: str, ws: str, import_name: str, extra_env=None):
    """**无条件**核对：`import <pkg>` 必须解析到本实例工作区内，否则 InfraError。

    ⚠ 为什么要在 `ensure_venv` 的自检之外**再**做（D3）：
    `ensure_venv` 的自检发生在**挂载之后、判定之前**。并发场景下另一个 run 可以在
    这次自检通过**之后**改动挂载 ⇒ 自检当时是对的，判定时已经不是了。
    实测（t4 round 4 / t8 复现 round 4）正是「跑完了但判的是别人的工作区、notes 为空」。
    ⇒ 判定**前后**各核一次，输入是**当前磁盘上的真实解析结果**。

    返回解析到的真实路径（供证据留档）。
    """
    probe = sh([py, "-c",
                "import os, sys, importlib;"
                f"m = importlib.import_module({import_name!r});"
                "p = os.path.realpath(getattr(m, '__file__', '') or '');"
                "root = os.path.realpath(" + repr(ws) + ");"
                "sys.stdout.write(p);"
                "raise SystemExit(0 if p and (p == root or p.startswith(root + os.sep)) else 1)"],
               cwd=os.path.expanduser("~"), timeout=300)
    resolved = (probe.stdout or "").strip()
    if probe.rc != 0:
        raise InfraError(
            f"隔离失败：{import_name} 的导入路径不在本实例工作区内"
            f"（解析到 {resolved[-200:] or '(空)'}，期望在 {ws} 内）"
            f"；stderr={(probe.stderr or '')[-200:]}")
    return resolved


# ---------------------------------------------------------------- 工作区

def run_porcelain(ws):
    return git(ws, "status", "--porcelain", timeout=180).stdout.strip()


def hard_clean_check(ws) -> bool:
    """§9.1 的严格版本（基线检出后）：`git status --porcelain` 必须**完全为空**。

    这是「工作区刚从 base_commit 干净检出」的判据。跑完判定后用宽松版（见 garbage_free）。
    """
    return run_porcelain(ws) == ""


def garbage_free(ws) -> bool:
    """§9.1 的**跑后**判据：`repo/` 里不得留垃圾（未跟踪且未被忽略的新文件、合并残留）。

    ⚠ 为什么不能沿用严格版：判定链路本身要往工作区施加 `test_patch` 与候选补丁，
    这些**已跟踪文件的修改就是判据的一部分**，不是垃圾。harness 的临时文件
    （ini / diff / json report）一律写 `<instance>/artifacts/`，本来就不进 `repo/`。
    """
    out = git(ws, "status", "--porcelain", timeout=180).stdout
    for line in out.splitlines():
        code = line[:2]
        if "??" in code or "U" in code:
            return False
    return True


def fresh_workspace(instance: dict, run_id: str, log_path: str | None = None) -> str:
    """§8.1：从 `eval-cache/<repo_key>` 的 `base_commit` 做**目录副本**。

    **明确不用 `git worktree` 指向缓存仓库** —— worktree 共享 `.git` 对象库与 refs，
    被污染的 index/refs 会跨实例传播。宁可多花磁盘也要物理隔离（这条是预注册写死的）。

    实现：`git archive <base> | tar` 解包 -> 在该目录 `git init` + 一次 baseline commit。
    这样 `git diff` 能在工作区里用（采集 agent 补丁的前提），又不碰缓存仓库。
    """
    iid = instance["instance_id"]
    parent = instance_dir(run_id, iid)
    if os.path.isdir(parent):
        # 复用工作区必须先通过清洁检查，否则删除重建（§8.1）。调用方通常在
        # 需要「全新判定」时直接删掉整个实例目录，这里是兜底。
        shutil.rmtree(parent, ignore_errors=True)
    os.makedirs(parent, exist_ok=True)
    ws = ws_path(run_id, iid)
    os.makedirs(ws, exist_ok=True)

    repo_key = repo_key_of(instance["repo"])
    cache = os.path.join(CACHE_ROOT, repo_key)
    if not os.path.isdir(os.path.join(cache, ".git")):
        raise InfraError(f"缓存仓库不存在或不是 git 仓库：{cache}（先跑 eval/build_subset.py）")
    base = instance["base_commit"]

    tar = os.path.join(parent, "_base.tar")
    r = git(cache, "archive", "--format=tar", "-o", tar, base, timeout=900)
    if r.rc != 0:
        raise InfraError(f"git archive {base[:8]} 失败：{r.stderr[-300:]}")
    if git(ws, "init", "--quiet").rc != 0:
        raise InfraError("工作区 git init 失败")
    for cfg in (["user.email", "eval@local"], ["user.name", "eval-harness"],
                ["core.autocrlf", "false"], ["core.safecrlf", "false"],
                ["commit.gpgsign", "false"], ["advice.detachedHead", "false"]):
        git(ws, "config", *cfg, timeout=60)
    with tarfile.open(tar) as tf:
        tf.extractall(ws)
    try:
        os.unlink(tar)
    except OSError:
        pass
    if git(ws, "add", "-A", timeout=900).rc != 0:
        raise InfraError("基线 git add 失败")
    if git(ws, "commit", "--quiet", "-m", f"baseline {base}", timeout=900).rc != 0:
        raise InfraError("基线 git commit 失败")
    if not hard_clean_check(ws):
        raise InfraError(f"基线工作区不为空（§9.1）：{run_porcelain(ws)[:200]}")
    if log_path:
        write_text(log_path, f"workspace={ws}\nbase_commit={base}\n"
                             f"$ git -C {cache} archive --format=tar -o <tmp> {base}\nexit={r.rc}\n"
                             f"porcelain={run_porcelain(ws)!r}\n")
    return ws


def reset_ws(ws):
    """回到 baseline commit 的干净状态（保留 `.git`，只还原已跟踪文件 + 删未跟踪）。

    ⚠ 不带 `-x`：仓库 `.gitignore` 覆盖的构建产物（`__pycache__` 等）保持不动，
    porcelain 本来就不显示它们。
    """
    git(ws, "checkout", "--", ".", timeout=300)
    git(ws, "clean", "-fdq", timeout=900)


# ---------------------------------------------------------------- venv（§8.2）


def ensure_venv(instance: dict, log_path: str, pip_deps, import_name: str,
                run_id: str = "") -> str:
    """§8.2：每 (实例, run) 独立 venv（不带 `--system-site-packages`）+ editable 挂载。

    ⚠ D1（t4 报的 blocker，t8 修）：venv 曾经只按 `<repo>-<base_commit[:8]>` 命名，
    **同一 base 的所有 run 共用一个 venv**，而 editable 挂载点写在 venv 里 ⇒ 两个 run
    跑同一实例时抢同一个挂载位。现在 venv 也按 run 隔离（见 venv_dir），
    共享可变状态被彻底消掉：本 venv 里**只有本 run 一个挂载点**。

    于是这里不再需要任何锁 / 清理 / 让位逻辑 —— 那套东西只在「共享 venv」这个前提下
    才必要，而那正是被修掉的根因。保留它反而会引入新的死锁（第一版就在这里栽过：
    两个 run 互相等对方的声明，6 轮全部卡死、零结果）。

    复用策略：venv 存在就复用（同 run 重跑时），每次重写自己的 `.pth` 并重新自检。
    """
    vd = venv_dir(instance, run_id)
    py = os.path.join(vd, "Scripts", "python.exe")
    ws = instance.get("_ws")
    lines = [f"venv={vd}\nrun_id={run_id!r}\n"]
    if not os.path.exists(py):
        r = sh([BASE_PY, "-m", "venv", vd], timeout=1200)
        lines.append(f"$ {' '.join(r.cmd)}\nexit={r.rc}\n{r.stdout}{r.stderr}\n")
        if r.rc != 0:
            write_text(log_path, "".join(lines))
            raise InfraError(f"venv 创建失败：{r.stderr[-300:]}")
    else:
        lines.append("reuse existing venv\n")

    deps = list(pip_deps or [])
    if deps:
        r = sh([py, "-m", "pip", "install", "--disable-pip-version-check", *deps], timeout=1800)
        lines.append(f"$ pip install {' '.join(deps)}\nexit={r.rc}\n{r.stdout[-2500:]}\n{r.stderr[-1500:]}\n")
        if r.rc != 0:
            write_text(log_path, "".join(lines))
            raise InfraError(f"依赖安装失败：{r.stderr[-300:]}")

    # editable 挂载：用 import 行式 `.pth` 把工作区路径插到 sys.path 最前。
    # 见 eval/lib/editable.py 的长注释（被测包常是 pytest 自身依赖，PyPI 副本会压过工作区；
    # 且不能 pip uninstall —— pytest 会 PackageNotFoundError）。
    from ..lib import editable as ED

    if ws:
        parent = ED.package_parent(ws, import_name)
        if parent is None:
            write_text(log_path, "".join(lines))
            raise InfraError(f"工作区里找不到包 {import_name!r}（既不是 src/ 也不是扁平布局）")
        entries = [parent] + ([ws] if os.path.basename(ws) == "repo" else [])
        sp = os.path.join(vd, "Lib", "site-packages")
        pth = os.path.join(sp, editable_pth_name(instance["instance_id"], run_id))
        write_text(pth, ED.pth_body(entries))
        lines.append(f"editable pth -> {pth}\n  entries={entries}\n")
        # 列出本 venv 里**全部** .pth（应当只有本 run 这一个 editable 挂载点）
        others = sorted(f for f in os.listdir(sp)
                        if f.endswith(".pth") and f != os.path.basename(pth))
        lines.append(f"同 venv 的其它 .pth：{others or '(无)'}\n")

        resolved = assert_pkg_from_workspace(py, ws, import_name)
        lines.append(f"$ import 自检\nexit=0\nresolved={resolved}\n")

    ver = sh([py, "-V"], timeout=120)
    lst = sh([py, "-m", "pip", "list", "--disable-pip-version-check"], timeout=300)
    lines.append(f"\npython: {ver.stdout.strip()}\n\npip list:\n{lst.stdout}\n")
    write_text(log_path, "".join(lines))
    return py


def clean_workspace(run_id: str, iid: str, keep_artifacts: bool = True):
    """清理该实例的工作区（§8.4：kill 后必须清理）。

    `keep_artifacts=True` 时只删 `repo/`，保留 `artifacts/` 与 `state.json`
    —— 证据比工作区值钱，而且断点续跑要靠证据复算。
    """
    d = instance_dir(run_id, iid)
    ws = ws_path(run_id, iid)
    if os.path.isdir(ws):
        shutil.rmtree(ws, ignore_errors=True)
    if not keep_artifacts and os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    return ws