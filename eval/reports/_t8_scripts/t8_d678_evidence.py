"""t8 验收证据：D6（用量回填）/ D7（行尾归一化）/ D8（回合上限）三条一次性取证。

三条都靠**真实运行**取证，不靠单元桩：

- **D6**：跑一次**故意撞回合上限**的真实运行（`--max-turns` 设成极小值），
  然后核对 `usage.jsonl` 那行**非 null**，且三个口径与 trace 逐项**求和一致**。
- **D7**：拿 t5 冒烟实测的那个 CRLF 候选补丁，过一遍 `judge.normalize_eol_patch`，
  给出**归一化前后**的字节数与 hunk 数对照。
- **D8**：给出参数名、默认值（fixture 路 / 实例路各是什么），并用一次真实运行证明
  「不再被上限截断」——用 `--max-turns 3` 制造截断（应当有显式标记），
  再用实例默认值证明预算确实被抬高。

用法：python t8_d678_evidence.py <instance_id> <run_id>
⚠ 本脚本会**真实调用模型**（D6 与 D8 需要）。D7 是纯函数、不花钱。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
for p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import env as E            # noqa: E402
from eval.harness import judge as J          # noqa: E402
from eval.harness import run_instance as RI  # noqa: E402

OUT = os.path.join(E.RUNS_ROOT, "t8")
PY = E.BASE_PY


def t8_d7(instance_id=None):
    """D7：行尾归一化前后对照。

    ⚠ 第一版量错了对象（记录在此以免重蹈）：它去量 t5 归档的**补丁正文**，得到 1.0x ——
    因为 `git diff` 输出的正文里 CRLF 已经是 0，归一化自然无变化。
    真正的问题在**工作树**：agent 把 `core.py` 写成 CRLF 而 HEAD 是 LF ⇒ git 认为每行都变。
    ⇒ 必须在一个**真实带 CRLF 的工作区**上量：先 diff、再归一化工作树、再 diff。
    """
    print("=" * 78)
    print("D7 行尾归一化：同一工作区的 diff，归一化前后的字节数 / hunk 数对照")
    print("=" * 78)
    ws = os.path.join(E.RUNS_ROOT, "t5-smoke", "pallets-click__4fd2fea", "repo")
    if not os.path.isdir(ws):
        print(f"  ⚠ 找不到 t5 那个 CRLF 工作区：{ws}")
        return None
    art = os.path.join(OUT, "d7_demo")
    os.makedirs(art, exist_ok=True)

    # ⚠ 归一化是**幂等**的：第一次跑过之后工作区就是 LF 了，再量只会得到 1.0x
    # （第一版正是在这里得到 1.0x 而误以为「没修好」）。为了让证据**自包含、可复算**，
    # 这里先**重建 CRLF 状态**（把被修改的已跟踪文件按 git 里的原样 CRLF 化），
    # 再走一遍「diff -> 归一化 -> diff」。重建只改我自己这次临时造的行尾，
    # 且随后立刻归一化 ⇒ 不引入新的不一致。
    rebuilt = []
    st = E.git(ws, "status", "--porcelain", timeout=180).stdout or ""
    for line in st.splitlines():
        if len(line) < 4 or "?" in line[:2]:
            continue
        path = line[3:].strip().strip('"').replace("\\", "/")
        full = os.path.join(ws, *path.split("/"))
        if not os.path.isfile(full):
            continue
        with open(full, "rb") as f:
            data = f.read()
        if b"\x00" in data[:8192] or b"\r\n" in data:
            continue
        with open(full, "wb") as f:
            f.write(data.replace(b"\n", b"\r\n"))
        rebuilt.append(path)
    if rebuilt:
        print(f"  （先把这些文件重建为 CRLF 以复现故障态：{rebuilt}）")

    before = J.eol_stats(E.git(ws, "diff", "--binary").stdout or "")
    rep = J.normalize_worktree_eol(ws, log_path=os.path.join(art, "collect_eol.log"))
    after = J.eol_stats(E.git(ws, "diff", "--binary").stdout or "")
    print(f"  工作区：{ws}")
    print(f"  {'':<12}{'字节':>10}{'hunk':>7}{'文件':>6}")
    print(f"  {'归一化前':<12}{before['bytes']:>10}{before['hunks']:>7}{before['files']:>6}")
    print(f"  {'归一化后':<12}{after['bytes']:>10}{after['hunks']:>7}{after['files']:>6}")
    ratio = (before["bytes"] / after["bytes"]) if after["bytes"] else None
    if ratio:
        print(f"\n  体积比 = {ratio:.1f}x（归一化前 / 归一化后）")
    print(f"  被归一的文件：")
    for x in rep["normalized"]:
        print(f"      {x['path']}  CRLF {x['crlf_before']} 行  "
              f"{x['bytes_before']} -> {x['bytes_after']} 字节")
    print(f"  未跟踪文件被跳过（不该改写 agent 新建的文件）：{rep['skipped_untracked']}")
    return {"before": before, "after": after, "size_ratio": ratio,
            "normalized_files": rep["normalized"]}


def run_agent_once(instance_id, label, max_turns, extra=None):
    """真实跑一次 coding_agent（实例模式）。返回 (rc, 日志尾, usage 行)。"""
    rid = f"t8-d678-{label}"
    inst = RI.load_instance(instance_id)
    ws = E.fresh_workspace(inst, rid, log_path=None)
    pf = os.path.join(E.run_root(rid), "_problem.txt")
    E.write_text(pf, inst["problem_statement"] + "\n")
    cmd = [PY, "-X", "utf8", os.path.join(PROJECT_ROOT, "coding_agent.py"),
           "--repo-root", ws, "--problem-file", pf, "--label", label,
           "--max-turns", str(max_turns)]
    if extra:
        cmd += extra
    print(f"\n  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=PROJECT_ROOT, timeout=3000)
    tail = "\n".join((r.stdout or "").splitlines()[-12:])
    print(f"  rc={r.returncode}")
    for line in tail.splitlines():
        print(f"    | {line}")
    return r.returncode, r.stdout or "", rid


def read_usage_row(label):
    p = os.path.join(PROJECT_ROOT, "servers", "experiments", "out", "usage.jsonl")
    rows = [json.loads(l) for l in E.read_text(p).splitlines() if l.strip()]
    for row in reversed(rows):
        if row.get("label") == label:
            return row
    return None


def t8_d6(instance_id):
    """D6：故意撞回合上限，核对 usage.jsonl 被 trace 回填且逐项一致。"""
    print()
    print("=" * 78)
    print("D6 用量回填：故意撞回合上限后，usage.jsonl 必须非 null 且与 trace 一致")
    print("=" * 78)
    label = "t8-d6-capped"
    rc, out, rid = run_agent_once(instance_id, label, max_turns=2)

    # 从 trace 求和（独立算一遍，用来核对 log_usage 的回填值）
    agent_json = os.path.join(PROJECT_ROOT, "servers", "experiments", "out",
                              f"agent-{label}.json")
    trace = None
    if os.path.exists(agent_json):
        trace = json.loads(E.read_text(agent_json)).get("trace")
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "servers", "experiments"))
    from usage_log import usage_from_trace  # noqa: E402
    expect = usage_from_trace(trace)

    row = read_usage_row(label)
    print("\n  --- usage.jsonl 里这一行 ---")
    if row is None:
        print("  ⚠ 没找到该 label 的行")
        return None
    keys = ("input", "input_uncached", "cache_read", "cache_creation", "output",
            "num_turns", "total_cost_usd", "usage_source", "tool_calls_total")
    for k in keys:
        print(f"    {k:<18} = {row.get(k)!r}")

    print("\n  --- 与 trace 独立求和逐项核对 ---")
    checks = {
        "input_uncached": (row.get("input_uncached"), expect.get("input_uncached")),
        "cache_read": (row.get("cache_read"), expect.get("cache_read")),
        "cache_creation": (row.get("cache_creation"), expect.get("cache_creation")),
        "output": (row.get("output"), expect.get("output")),
        "num_turns": (row.get("num_turns"), expect.get("num_turns")),
        "tool_calls_total": (row.get("tool_calls_total"), expect.get("tool_calls_total")),
    }
    all_ok = True
    for k, (got, want) in checks.items():
        ok = got == want
        all_ok &= ok
        print(f"    {'OK ' if ok else 'BAD'} {k:<18} usage={got!r}  trace={want!r}")
    print(f"\n  非 null 且逐项一致：{all_ok}")
    return {"usage_row": {k: row.get(k) for k in keys}, "trace_expect": expect,
            "all_match": all_ok}


def t8_d8(instance_id):
    """D8：回合上限参数化 —— 给参数名、默认值，并实测截断有显式标记。"""
    print()
    print("=" * 78)
    print("D8 回合上限参数化")
    print("=" * 78)
    import re
    src = E.read_text(os.path.join(PROJECT_ROOT, "coding_agent.py"))
    m1 = re.search(r"^MAX_TURNS = (\d+)", src, flags=re.M)
    m2 = re.search(r"^INSTANCE_MAX_TURNS = (\d+)", src, flags=re.M)
    print(f"  MAX_TURNS（fixture 路默认）          = {m1.group(1) if m1 else '?'}")
    print(f"  INSTANCE_MAX_TURNS（实例路默认）      = {m2.group(1) if m2 else '?'}")
    print(f"  CLI 参数名                            = --max-turns")
    print(f"  优先级：--max-turns > 实例模式默认 > fixture 默认")
    # --help 里能看到
    r = subprocess.run([PY, "-X", "utf8", os.path.join(PROJECT_ROOT, "coding_agent.py"),
                        "--help"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=PROJECT_ROOT)
    has = "--max-turns" in (r.stdout or "")
    print(f"  --help 里出现 --max-turns             = {has}")

    # 真实跑一次，证明截断有显式标记
    label = "t8-d8-capped"
    rc, out, rid = run_agent_once(instance_id, label, max_turns=2)
    capped = "maximum number of turns" in out
    print(f"\n  --max-turns 2 的真实运行被截断且有显式标记：{capped}")
    usage_row = read_usage_row(label)
    tr = (usage_row or {}).get("usage_source")
    print(f"  该轮 usage_source = {tr!r}（应为 trace ⇒ 证明 D6 在真实截断轮生效）")

    # 跑一次实例默认值，证明预算被抬高（不再用 40）
    label2 = "t8-d8-instance-default"
    rc2, out2, rid2 = run_agent_once(instance_id, label2, max_turns=int(m2.group(1)) if m2 else 120)
    capped2 = "maximum number of turns" in out2
    print(f"\n  用实例默认 {m2.group(1) if m2 else '?'} 回合跑：被截断={capped2}")
    return {"fixture_default": int(m1.group(1)) if m1 else None,
            "instance_default": int(m2.group(1)) if m2 else None,
            "cli_flag": "--max-turns", "help_has_flag": has,
            "capped_at_2_has_marker": capped, "capped_at_2_usage_source": tr,
            "instance_default_capped": capped2}


def main():
    iid = sys.argv[1] if len(sys.argv) > 1 else "pallets-click__4fd2fea"
    os.makedirs(OUT, exist_ok=True)
    print("=" * 78)
    print(f"t8 D6/D7/D8 取证   实例={iid}")
    print("=" * 78)
    d7 = t8_d7()
    d6 = t8_d6(iid)
    d8 = t8_d8(iid)
    summary = {"instance_id": iid, "D7": d7, "D6": d6, "D8": d8}
    dest = os.path.join(OUT, "d678_evidence.json")
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n落盘：{dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())