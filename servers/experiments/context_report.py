"""context_report.py —— 从 agent 运行产物里统计「上下文成本」与「逐次采用」。

**存在的理由**：在做上下文管理那一层之前，先把问题**量化**清楚。
不量化就动手，等于凭感觉加分层 —— 加完也不知道有没有用。

指标（都来自 `agent-*.json` 的 events）：

  · `tool_chars`          工具返回的字符总量 —— 这个量直接决定进模型上下文的规模
  · `per_tool_chars`      每个工具各自的字符量（找出大头是谁）
  · `read_files`          每个文件被读了几次（**去重机会**）
  · `dup_reads`           同一文件同一行区间被读了几次（**严格去重机会**）
  · `test_chars`          run_tests 输出占总量的比例（测试输出通常是大头）

**逐次采用**（A/B 实验要用）：

  · `used_repo_map`       这一次运行**是否调用过 repo_map**
  · `per_tool_calls`      各工具调用次数

  ⚠️ 判据**只看 `events` 里落盘的 `tool` 字段**（即 tool_use 的序号），
     不看 agent 的自述、也不看 metrics 里的 `used_repo_map`（那只是同一事实的
     第二份拷贝）。理由：自述会说"我用了仓库地图"而实际没调，也会反过来；
     而"只看落盘事实"是本项目已经吃过两次亏换来的纪律（CLAUDE.md 陷阱 17）。

用法：
    python context_report.py <agent-*.json> [更多文件…]
    python context_report.py --glob "out/agent-base-small*.json"

    # A/B 逐次采用表（默认就打印，不需要额外开关）
    python context_report.py --glob "out/agent-ab-*.json"
"""

from __future__ import annotations

import glob as globmod
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ⚠️ 必须在**任何 print 之前**把输出切到 UTF-8（CLAUDE.md 陷阱 16）。
# 本机控制台是 GBK(936)，打印装不下的字符（本文件里就有 ⚠️）会 UnicodeEncodeError
# 直接崩 —— 而且崩在报告打到一半的位置，看起来像"统计脚本坏了"。
# 已实际发生一次（就是加了逐次采用表之后），所以这里按公共模块的规矩补上。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agent.config import force_utf8_output  # noqa: E402


def load_events(path: Path) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("metrics", {}), data.get("events", [])


def pair_calls(events: list[dict]) -> list[dict]:
    """把 tool_use 与紧随其后的 tool_result 配成对。

    事件序列是 [tool_use, tool_result, tool_use, tool_result, …]，
    所以按顺序记住最后一个 tool_use 即可。
    """
    calls: list[dict] = []
    pending: dict | None = None
    for e in events:
        if e.get("type") == "tool_use":
            pending = {"tool": e.get("tool", "?"), "input": e.get("input") or {}}
        elif e.get("type") == "tool_result" and pending is not None:
            pending["chars"] = int(e.get("chars") or len(e.get("text") or ""))
            pending["stored_chars"] = len(e.get("text") or "")
            calls.append(pending)
            pending = None
    return calls


def short_name(tool: str) -> str:
    return tool.replace("mcp__code-tools__", "")


# ── 「被采用」的操作定义（**先定死，不许事后改**）──────────────────────────
# 某次运行的 MCP 工具调用序列里出现至少一次 repo_map，即记为「该次采用了」。
#
# 判据的**唯一来源**是 events 里的 tool_use 条目 —— 那是 SDK 落盘的原始调用序列。
# 刻意**不**读 metrics["used_repo_map"]：那是写 JSON 时顺手记的第二份拷贝，
# 两份不一致时无从判断谁对。判据只留一条通道，才谈得上"口径没改"。
ADOPTION_TOOL = "repo_map"


def analyze(path: Path) -> dict:
    metrics, events = load_events(path)
    calls = pair_calls(events)

    per_tool: Counter[str] = Counter()
    read_paths: Counter[str] = Counter()
    read_ranges: Counter[tuple] = Counter()

    for c in calls:
        name = short_name(c["tool"])
        per_tool[name] += c["chars"]
        if name == "read_file":
            p = str(c["input"].get("path", "?"))
            read_paths[p] += 1
            # 只有指定了区间才算"同一区间"；整读用 0/0 代表
            rng = (p, c["input"].get("start", 0), c["input"].get("end", 0))
            read_ranges[rng] += 1

    total = sum(per_tool.values())
    dup_reads = sum(v - 1 for v in read_ranges.values() if v > 1)

    per_tool_calls = Counter(short_name(c["tool"]) for c in calls)

    # ⚠️ 采用判据：直接数 tool_use，**不是** pair_calls 的结果。
    #    两者在"调了但没等到结果"（超时 / 中断 / server 死掉）时会分叉 ——
    #    pair_calls 只收成对的调用，那种情况下会漏掉一次**真实发生过的调用**。
    #    实验要的是"agent 有没有想去用这个工具"，所以以 tool_use 为准。
    tool_use_calls = Counter(short_name(e.get("tool", "?"))
                             for e in events if e.get("type") == "tool_use")
    repo_map_calls = tool_use_calls[ADOPTION_TOOL]

    return {
        "label": metrics.get("label") or path.stem,
        "path": str(path),
        "success": metrics.get("success"),
        "tool_calls": len(calls),
        "tool_chars": total,
        "per_tool": dict(per_tool),
        "per_tool_calls": dict(per_tool_calls),
        "test_chars": per_tool.get("run_tests", 0),
        "read_files": dict(read_paths),
        "distinct_files_read": len(read_paths),
        "dup_reads": dup_reads,
        # ── 逐次采用（A/B 的被解释变量）──────────────────────────────────
        "used_repo_map": repo_map_calls > 0,
        "repo_map_calls": repo_map_calls,
        "tool_use_calls": dict(tool_use_calls),
        "tool_use_total": sum(tool_use_calls.values()),
        # ── A/B 实验条件（来自 metrics，第 4 步落盘）─────────────────────
        "repo_map_default": metrics.get("repo_map_default"),
        "input_tokens": metrics.get("input_tokens"),
        "cost_usd": metrics.get("cost_usd"),
    }


def report(paths: list[Path]) -> int:
    reports = [analyze(p) for p in paths]
    if not reports:
        print("没有可分析的产物")
        return 2

    print("=" * 78)
    print("上下文成本报告")
    print("=" * 78)
    print(f"{'样本':<18}{'判定':<6}{'调用':<6}{'工具返回字符':<14}{'不同文件':<10}{'重复读':<8}{'测试占比'}")
    for r in reports:
        ratio = (r["test_chars"] / r["tool_chars"] * 100) if r["tool_chars"] else 0
        print(f"{r['label']:<18}{str(bool(r['success'])):<6}{r['tool_calls']:<6}"
              f"{r['tool_chars']:<14}{r['distinct_files_read']:<10}{r['dup_reads']:<8}{ratio:.0f}%")

    def stat(key: str) -> tuple[int, int, float]:
        vals = [r[key] for r in reports]
        return min(vals), max(vals), sum(vals) / len(vals)

    print()
    for key, name in (("tool_chars", "工具返回字符总量"), ("tool_calls", "工具调用次数"),
                      ("dup_reads", "重复读次数")):
        lo, hi, avg = stat(key)
        print(f"  {name:<16} 最小 {lo:<8} 最大 {hi:<8} 均值 {avg:.0f}")

    print("\n--- 各工具返回的字符量（按样本聚合）---")
    agg: Counter[str] = Counter()
    agg_calls: Counter[str] = Counter()
    for r in reports:
        for k, v in r["per_tool"].items():
            agg[k] += v
        for k, v in r["per_tool_calls"].items():
            agg_calls[k] += v
    grand = sum(agg.values()) or 1
    for k, v in agg.most_common():
        print(f"  {k:<14} {v:>7} 字符 ({v / grand * 100:4.1f}%)   调用 {agg_calls[k]:>3} 次"
              f"   平均 {v / max(1, agg_calls[k]):.0f} 字符/次")

    print("\n--- 文件被读的次数（去重机会）---")
    files: Counter[str] = Counter()
    for r in reports:
        for k, v in r["read_files"].items():
            files[k] += v
    for k, v in files.most_common(10):
        mark = "  <= 读过多次" if v > len(reports) else ""
        print(f"  {k:<34} {v} 次{mark}")

    print_per_run(reports)
    return 0


def print_per_run(reports: list[dict]) -> None:
    """逐次采用表 —— **每个样本一行**，这是判定「某一次运行是否采用」的地方。

    为什么必须逐次而不是聚合：聚合只能回答"这些运行一共调了几次 repo_map"，
    回答不了"6 次里有几次用了"。而 A/B 的被解释变量恰恰是**后者**
    （采用率 = 采用了的那几次 / 总次数）。聚合数字相同、逐次分布可以完全不同
    （一次调 3 次 vs 三次各调 1 次），把这两种情况混起来就是口径错。

    两栏分开列，因为它们的**不等本身就是证据**：
      · `调用`  = tool_use 次数（"agent 想去用"）
      · `成对`  = 拿到了结果的次数（"真的用上了"）
    调了却没回结果 = 超时 / 中断 / server 挂了 —— 这属于值得单独看的异常。
    """
    print("\n--- 逐次采用（A/B 的被解释变量）---")
    print("  ⚠️ 判据：events 里出现至少一次 tool_use 名为 repo_map 即算「采用」。")
    print("     只看落盘的调用序列，不看 agent 自述，也不读 metrics 里的同名拷贝。\n")

    # 工具列：把所有出现过的工具按聚合调用次数排序，动态生成表头
    tools = sorted({t for r in reports for t in r["tool_use_calls"]},
                   key=lambda t: (-sum(r["tool_use_calls"].get(t, 0) for r in reports), t))
    head = f"  {'样本':<20}{'臂':<10}{'采用':<6}{'repo_map':<10}"
    head += "".join(f"{t[:9]:<11}" for t in tools)
    print(head)
    print("  " + "-" * (len(head) - 2))

    for r in reports:
        arm = r.get("repo_map_default") or "?"
        used = "是" if r["used_repo_map"] else "否"
        row = (f"  {r['label']:<20}{arm:<10}{used:<6}{r['repo_map_calls']:<10}")
        row += "".join(f"{r['tool_use_calls'].get(t, 0):<11}" for t in tools)
        print(row)

    # ── 采用率 ────────────────────────────────────────────────────────────
    adopted = [r for r in reports if r["used_repo_map"]]
    print(f"\n  采用：{len(adopted)}/{len(reports)} 次"
          f"  =  {len(adopted) / len(reports) * 100:.0f}%")

    # 有臂标记时，按臂分开算 —— 这才是 A/B 要的那张表
    arms = {r["repo_map_default"] for r in reports} - {None, ""}
    if len(arms) > 1:
        print("\n  --- 按臂分（A/B 对照）---")
        for arm in sorted(arms):
            group = [r for r in reports if r.get("repo_map_default") == arm]
            n = sum(1 for r in group if r["used_repo_map"])
            rm = sum(r["repo_map_calls"] for r in group)
            chars = sum(r["per_tool"].get("repo_map", 0) for r in group)
            print(f"    repo_map_default={arm:<10} 采用 {n}/{len(group)}"
                  f"  = {n / len(group) * 100:3.0f}%"
                  f"   repo_map 调用 {rm} 次，返回 {chars} 字符"
                  f"   工具调用合计 {sum(r['tool_use_total'] for r in group)} 次")
    else:
        print("  （所有样本的 repo_map_default 相同或缺失 —— 不是 A/B 对照，只是采用率统计）")


def main() -> int:
    force_utf8_output()   # 必须在第一个 print 之前（陷阱 16）
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "--glob":
        paths = [Path(p) for p in sorted(globmod.glob(argv[1]))]
    else:
        paths = [Path(p) for p in argv]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        for p in missing:
            print(f"找不到：{p}", file=sys.stderr)
        return 2
    return report(paths)


if __name__ == "__main__":
    raise SystemExit(main())
