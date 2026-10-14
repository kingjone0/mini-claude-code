"""t9 独立审查 第 10 条 ③：不传 `trace` 时 `log_usage` 的写盘行为是否「逐字不变」。

做法（**真实对照**，不是代码比对）：
  · 从 git 里取出 t8（7e64816）**之前**的 `servers/experiments/usage_log.py`（= 14bdd10 那份）
  · 与新版本**在同一个进程里分别 import**（文件写到本目录下，不污染仓库）
  · 用同一个人造 ResultMessage（含 model_usage）分别调用，写进两个独立 tmp 目录
  · 逐 key 比较两行 JSON：新增 / 删除 / 值变化，分三类报

用法：python eval/reports/_t9_scripts/t9_usage_log_default_path.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = r"E:\aicoding\mini-claude-code"
HERE = os.path.dirname(os.path.abspath(__file__))
GIT = r"E:\Program Files\Git\cmd\git.exe"
OLD_REV = "14bdd10"          # t8 之前的版本
NEW_FILE = os.path.join(PROJ, "servers", "experiments", "usage_log.py")
OLD_COPY = os.path.join(HERE, "_t8_before_usage_log.py")


class FakeResult:
    def __init__(self):
        self.model_usage = {
            "m1": {"inputTokens": 100, "outputTokens": 20,
                   "cacheReadInputTokens": 5, "cacheCreationInputTokens": 7,
                   "totalCostUSD": 0.5, "canonicalModel": "x"},
        }
        self.usage = {"input_tokens": 111, "output_tokens": 20}
        self.num_turns = 3
        self.subtype = "success"
        self.is_error = False
        self.total_cost_usd = 0.5
        self.result = "ok"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def read_jsonl(p):
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    src = subprocess.run([GIT, "-C", PROJ, "show", f"{OLD_REV}:servers/experiments/usage_log.py"],
                         capture_output=True, text=True, encoding="utf-8").stdout
    with open(OLD_COPY, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print(f"旧版（{OLD_REV}）落盘到 {OLD_COPY}（{len(src)} 字节）；新版={NEW_FILE}")

    old = load("t9_old_usage_log", OLD_COPY)
    new = load("t9_new_usage_log", NEW_FILE)

    d = tempfile.mkdtemp(prefix="t9-usage-")
    p_old = os.path.join(d, "old.jsonl")
    p_new = os.path.join(d, "new.jsonl")
    kw = dict(script="probe.py", key_source="test", extra={"label": "t9"})
    r_old = old.log_usage(FakeResult(), log_path=Path(p_old), **kw)
    r_new = new.log_usage(FakeResult(), log_path=Path(p_new), **kw)   # 不传 trace（默认路径）

    lo, ln = read_jsonl(p_old)[0], read_jsonl(p_new)[0]
    added = sorted(set(ln) - set(lo))
    removed = sorted(set(lo) - set(ln))
    changed = sorted(k for k in set(lo) & set(ln) if lo[k] != ln[k])
    print("\n写盘行对比（都不传 trace）：")
    print(f"  新增键：{added if added else '(无)'}")
    print(f"  删除键：{removed if removed else '(无)'}")
    print(f"  值变化的键：{changed if changed else '(无)'}")
    print(f"  逐字节相同的行？{json.dumps(lo, ensure_ascii=False, sort_keys=True) == json.dumps(ln, ensure_ascii=False, sort_keys=True)}")
    if changed:
        for k in changed:
            print(f"    {k}: 旧={lo[k]!r}  新={ln[k]!r}")
    print(f"  返回值对比（函数返回的 row）新增键：{sorted(set(r_new) - set(r_old))}")
    print(f"\n旧行（key 数={len(lo)}）：{json.dumps(lo, ensure_ascii=False)[:400]}")
    print(f"\n新行（key 数={len(ln)}）：{json.dumps(ln, ensure_ascii=False)[:400]}")

    # 消费者是否会因为多一个键而坏：列出 usage.jsonl 的读取方
    print("\n使用 usage.jsonl 的位置（grep）：")
    g = subprocess.run([r"E:\Program Files\Git\cmd\git.exe", "-C", PROJ, "grep", "-n",
                        "usage.jsonl"],
                       capture_output=True, text=True, encoding="utf-8")
    for line in (g.stdout or "").splitlines():
        print("   " + line[:180])
    return 0


if __name__ == "__main__":
    sys.exit(main())
