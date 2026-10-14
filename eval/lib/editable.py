"""editable 安装：把实例工作区的包挂进该实例专属 venv，且不在工作树里留任何产物。

为什么不用 `pip install -e .`：它会往 repo/ 写 `<pkg>.egg-info`（部分仓库的 .gitignore 不覆盖它）
⇒ 与预注册 §9.1「判定后 `git status --porcelain` 为空」直接打架。

⚠ 两个必须同时满足的约束（实测踩过）：
1. **路径优先级**：被测包常常就是 pytest 自己的依赖（pluggy 由 pytest 直接拖进来，
   more-itertools / click / tabulate 也可能被间接拖进来）。普通 `.pth` 只是把目录
   **追加**到 sys.path 末尾，而 site-packages 排在前面 ⇒ `import pluggy` 解析到 PyPI 副本，
   判定用的根本不是工作区基线代码。
2. **不能卸载**：`pip uninstall pluggy` 会让 pytest 自己在 `importlib.metadata` 上抛
   `PackageNotFoundError: No package metadata was found for pluggy`（INTERNALERROR, exit 3）。

⇒ 用 **import 行式的 `.pth`**：`.pth` 里的 `import` 行在 site 初始化时执行，把工作区路径
**插到 sys.path 最前**（`insert(0, ...)`），这样既保留 site-packages 里的 dist 元数据
（pytest 可用），又让工作区代码优先被导入。手写 meta_path finder 会被 pytest 的
assertion-rewrite 绕开，实测不稳，故不用。
"""

from __future__ import annotations

import os


def venv_dir_for(root, iid, tag="py312"):
    return os.path.join(root, f"{iid}-{tag}")


def venv_py(root, iid, tag="py312"):
    return os.path.join(venv_dir_for(root, iid, tag), "Scripts", "python.exe")


def editable_pth(root, iid, digest):
    sp = os.path.join(venv_dir_for(root, iid), "Lib", "site-packages")
    return os.path.join(sp, f"_eval_editable_{digest}.pth")


def pth_body(entries):
    """生成 `.pth` 内容：把每个目录**插到 sys.path 最前**（顺序与 entries 一致）。

    写成一行 import 语句：site 模块会在处理 `.pth` 时 exec 它。用 `insert(0, ...)` 逆序插入
    以保证 entries[0] 最终排在最前。
    """
    lines = []
    for entry in reversed(list(entries)):
        lines.append(f"import sys; sys.path.insert(0, {entry!r})")
    return "\n".join(lines) + "\n"


def package_parent(ws, pkg):
    """返回包含 `pkg` 的那个目录（src/<pkg>/ 的 src，或扁平布局的工作区根）。"""
    for cand in (f"src/{pkg}", pkg, f"lib/{pkg}"):
        p = os.path.join(ws, *cand.split("/"))
        if os.path.exists(os.path.join(p, "__init__.py")):
            return os.path.dirname(p)
    # 单文件模块（module.py 而非 package/）
    for cand in (f"src/{pkg}.py", f"{pkg}.py"):
        p = os.path.join(ws, *cand.split("/"))
        if os.path.exists(p):
            return os.path.dirname(p)
    return None


def write_pth(path, entries, writer):
    writer(path, pth_body(entries))
    return path
