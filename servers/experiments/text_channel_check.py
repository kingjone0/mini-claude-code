"""text_channel_check.py —— 对照 text_channel_probe.py 产出的几份协议层快照。

纪律（t8 的教训）：**断言必须从产物里读出来，不能把结论硬编码在脚本里** ——
硬编码了期望值的核对脚本只是一份快照，它验证不了任何东西。
本脚本只做"两份快照之间的差集"：不假设任何一方的具体内容，只断言**差在哪**。

要证明的四件事：
  A. 改动之后，v1 那一档与**改动前**的基线逐字段相同（否则"臂 A"已经不是原来的臂 A）；
  B. `AGENT_SERVER_INSTRUCTIONS=v2` **只**改 instructions，五个工具的描述一字不动；
  C. `AGENT_REPO_MAP_DESC=v2` **只**改 repo_map 的描述，instructions 与其余四个工具一字不动；
  D. 两个开关同时打开，结果等于各自单独打开的叠加（互不干扰）。

外加两条**实质前提**（假设成不成立，取决于它们）：
  E. v1 的 instructions 里**不出现** repo_map —— 这样"典型用法漏了它"才是真的；
  F. v2 的 instructions 里**出现** repo_map —— 这样这个开关才真的是在补那处缺失。

用法：
    python text_channel_check.py
退出码：全部通过 0，否则 1。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ⚠️ 必须在任何 print 之前把输出切到 UTF-8（CLAUDE.md 陷阱 16）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agent.config import force_utf8_output  # noqa: E402

force_utf8_output()

OUT = Path(__file__).resolve().parent / "out"
FILES = {
    "baseline": "desc-baseline.json",   # 改动**前**跑的
    "v1v1": "desc-v1v1.json",           # 改动后，两个开关都不设
    "i2": "desc-i2.json",               # AGENT_SERVER_INSTRUCTIONS=v2
    "d2": "desc-d2.json",               # AGENT_REPO_MAP_DESC=v2
    "i2d2": "desc-i2d2.json",           # 两个都 v2
}


def load(name: str) -> dict:
    path = OUT / FILES[name]
    if not path.exists():
        raise SystemExit(f"缺产物：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def diff(a, b, prefix: str = "") -> list[str]:
    """返回两份快照里**取值不同**的路径（递归到标量）。"""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for key in sorted(set(a) | set(b)):
            out += diff(a.get(key), b.get(key), f"{prefix}.{key}" if prefix else str(key))
        return out
    if isinstance(a, list) and isinstance(b, list):
        out = []
        for i in range(max(len(a), len(b))):
            out += diff(a[i] if i < len(a) else None,
                        b[i] if i < len(b) else None, f"{prefix}[{i}]")
        return out
    return [] if a == b else [prefix]


def content(snap: dict) -> dict:
    """快照里"协议层实际发出的文本"那部分 —— 不含 env（那是自证字段，本就该不同）。"""
    return {k: snap[k] for k in ("serverInfo", "instructions", "tools")}


def main() -> int:
    snap = {name: load(name) for name in FILES}
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    # A. v1/v1 与基线逐字段相同（含 env —— 两次都没设，应当连 env 也一样）
    d = diff(snap["baseline"], snap["v1v1"])
    check("A v1/v1 与改动前的基线逐字段相同", not d, f"差异路径：{d}")

    # B. i2：内容只差 instructions；env 只差 AGENT_SERVER_INSTRUCTIONS
    d = diff(content(snap["baseline"]), content(snap["i2"]))
    check("B instructions=v2 只改 instructions",
          d == ["instructions"], f"内容差异路径：{d}")
    d = diff(snap["baseline"]["env"], snap["i2"]["env"])
    check("B2 环境自证字段恰好是 AGENT_SERVER_INSTRUCTIONS",
          d == ["AGENT_SERVER_INSTRUCTIONS"], f"env 差异路径：{d}")

    # C. d2：内容只差 repo_map 的描述；env 只差 AGENT_REPO_MAP_DESC
    d = diff(content(snap["baseline"]), content(snap["d2"]))
    check("C desc=v2 只改 repo_map 的描述",
          d == ["tools.repo_map.description"], f"内容差异路径：{d}")
    d = diff(snap["baseline"]["env"], snap["d2"]["env"])
    check("C2 环境自证字段恰好是 AGENT_REPO_MAP_DESC",
          d == ["AGENT_REPO_MAP_DESC"], f"env 差异路径：{d}")

    # D. 两个开关互不干扰：i2d2 的 instructions 应等于 i2 的，描述应等于 d2 的
    ok = (snap["i2d2"]["instructions"] == snap["i2"]["instructions"]
          and snap["i2d2"]["tools"] == snap["d2"]["tools"])
    check("D 两个开关可叠加、互不干扰", ok,
          f"instructions 同 i2={snap['i2d2']['instructions'] == snap['i2']['instructions']}，"
          f"tools 同 d2={snap['i2d2']['tools'] == snap['d2']['tools']}")

    # E/F. 假设的实质前提：v1 没提 repo_map，v2 提了
    v1_text = snap["baseline"]["instructions"] or ""
    v2_text = snap["i2"]["instructions"] or ""
    check("E v1 的 instructions 里不出现 repo_map", "repo_map" not in v1_text,
          f"v1 instructions = {v1_text!r}")
    check("F v2 的 instructions 里出现 repo_map", "repo_map" in v2_text,
          f"v2 instructions = {v2_text!r}")

    # 计量：描述长度必须真的变了，否则这个开关什么都没做
    len_v1 = len(snap["baseline"]["tools"]["repo_map"]["description"] or "")
    len_v2 = len(snap["d2"]["tools"]["repo_map"]["description"] or "")
    check("G v2 的 repo_map 描述确实换了一份文本", len_v1 > 0 and len_v2 > 0 and len_v1 != len_v2,
          f"v1 {len_v1} 字符 / v2 {len_v2} 字符")

    # 顺带证明"只改了目标工具"：其余四个工具两档完全一致
    others = {k: v for k, v in snap["baseline"]["tools"].items() if k != "repo_map"}
    others2 = {k: v for k, v in snap["d2"]["tools"].items() if k != "repo_map"}
    check("H 其余四个工具的描述与 schema 未被牵连", others == others2,
          f"工具：{sorted(others)}")

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(f"  {'[PASS]' if ok else '[FAIL]'} {name}")
        if not ok:
            print(f"         {detail}")
    print(f"\n  {passed}/{len(checks)} 项通过")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
