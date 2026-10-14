"""instructions_probe.py —— 判定「MCP 服务端的 instructions 到底有没有送到模型手里」。

**为什么必须先做这一步**：本次实验的「服务端指令」那一臂，前提是 CLI 真的把
`initialize` 返回的 `instructions` 注入了模型上下文。MCP 规范只说客户端
**MAY** 使用它 —— 如果这个 CLI 不用，那一臂就是**结构上的空操作**，
它跑出来的"没效果"会被误读成"指令对采用率没用"。这两种解释天差地别，
而它们在实验数据里长得一模一样。

方法沿用仓库已有的「可判定的问题」手法（CLAUDE.md 里查某个文件有没有被加载）：

  把内置工具全部禁用，只问两个**只能靠启动时上下文回答**的问题：

    Q1（**阳性对照**）上下文里有没有出现 `AGENT_REPO_MAP_DEFAULT`？
        这个词在两档里都只出现在 **repo_map 的工具描述**里（instructions 里没有）。
        若它答 NO，说明"这套问法本身不成立"，后面的结论一律作废 ——
        必须先证明"这个探针能看见工具清单"，才谈得上"它看不见 instructions"。

    Q2（**判别项**）上下文里有没有出现「推荐的工作顺序」这七个字？
        这七个字**只在 v2 的 instructions 里**：v1 的 instructions 没有，
        五个工具描述里也没有。

于是预期：
    v1 档 -> Q1=YES, Q2=NO
    v2 档 -> Q1=YES, Q2=YES      <- Q2 在 v2 档翻成 YES 就是"instructions 确实送达"的判据

用法：
    python instructions_probe.py              # 两档都跑
    python instructions_probe.py --arm v2     # 只跑一档

产出：out/instructions-probe-<臂>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

# ⚠️ 必须在任何 print 之前把输出切到 UTF-8（CLAUDE.md 陷阱 16）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agent.config import force_utf8_output  # noqa: E402

force_utf8_output()

import coding_agent as ca  # noqa: E402  （只取它的常量与装配方式，不跑它的 main）

OUT_DIR = Path(__file__).resolve().parent / "out"

PROMPT = """不要调用任何工具，也不要读取任何文件。只依据你启动时已经加载的上下文（系统提示、工具清单、服务端说明等任何已经注入给你的内容）回答下面两个问题：

Q1. 那部分内容里，是否出现过字符串 `AGENT_REPO_MAP_DEFAULT`？
Q2. 那部分内容里，是否出现过「推荐的工作顺序」这七个字？

请严格按两行输出，不要任何解释：
Q1=YES 或 Q1=NO
Q2=YES 或 Q2=NO
无法确定就写 Q1=UNKNOWN / Q2=UNKNOWN。
"""


def collect_text(message) -> str:
    """取一条 AssistantMessage 里的纯文本（按类名判断，避免依赖具体导入路径）。"""
    parts = []
    for block in getattr(message, "content", []) or []:
        if type(block).__name__ == "TextBlock":
            parts.append(getattr(block, "text", "") or "")
    return "\n".join(parts)


async def run_arm(arm: str, env: dict[str, str]) -> dict:
    options = ca.ClaudeAgentOptions(
        cli_path=ca.CLI_PATH,
        mcp_servers={
            ca.SERVER_NAME: {
                "type": "stdio",
                "command": ca.PYTHON,
                "args": [str(ca.SERVER_SCRIPT)],
                "env": {
                    "AGENT_REPO_ROOT": str(ca.FIXTURE),   # mini_repo，探针不碰它
                    ca.REPO_MAP_ENV_KEY: "summary",
                    # 被检验的那个变量
                    "AGENT_SERVER_INSTRUCTIONS": arm,
                },
            }
        },
        tools=[],
        allowed_tools=[],
        disallowed_tools=ca.DISALLOWED_TOOLS,
        permission_mode=ca.PERMISSION_MODE,
        strict_mcp_config=True,
        setting_sources=ca.SETTING_SOURCES,
        env=env,
        max_turns=4,
        stderr=lambda line: print(f"[cli stderr] {line}", file=sys.stderr, flush=True),
    )

    started = time.time()
    texts: list[str] = []
    result_msg = None
    async for message in ca.query(prompt=PROMPT, options=options):
        if type(message).__name__ == "AssistantMessage":
            got = collect_text(message)
            if got.strip():
                texts.append(got)
        elif type(message).__name__ == "ResultMessage":
            result_msg = message

    final = "\n".join(texts).strip()
    elapsed = time.time() - started
    parsed = {q: (m.group(1) if (m := re.search(rf"{q}\s*=\s*(YES|NO|UNKNOWN)", final, re.I))
                  else "UNPARSED") for q in ("Q1", "Q2")}
    return {
        "arm": arm,
        "prompt": PROMPT,
        "final_text": final,
        "parsed": parsed,
        "elapsed_s": round(elapsed, 1),
        "num_turns": getattr(result_msg, "num_turns", None),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default=None, choices=("v1", "v2"),
                    help="只跑一档；省略则两档都跑")
    args = ap.parse_args()

    env, key_source = ca.load_endpoint_env()
    print(f"  key_source = {key_source}")
    arms = [args.arm] if args.arm else ["v1", "v2"]

    records = []
    for arm in arms:
        print(f"── AGENT_SERVER_INSTRUCTIONS={arm} ──")
        rec = await run_arm(arm, env)
        records.append(rec)
        print(f"  Q1(阳性对照)={rec['parsed']['Q1']}  Q2(判别项)={rec['parsed']['Q2']}"
              f"  turns={rec['num_turns']}  {rec['elapsed_s']}s")
        print(f"  原文：{rec['final_text'][:300]!r}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for rec in records:
        (OUT_DIR / f"instructions-probe-{rec['arm']}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    by_arm = {r["arm"]: r["parsed"] for r in records}
    if len(by_arm) == 2:
        q1_ok = all(p["Q1"] == "YES" for p in by_arm.values())
        delivered = by_arm["v2"]["Q2"] == "YES" and by_arm["v1"]["Q2"] == "NO"
        print(f"  阳性对照（两档都应 Q1=YES）：{'成立' if q1_ok else '不成立 —— 本探针的结论一律作废'}")
        print(f"  instructions 是否送达：{'送达（v2 的 Q2 翻成 YES 而 v1 是 NO）' if delivered else '未证实'}")
        return 0 if (q1_ok and delivered) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
