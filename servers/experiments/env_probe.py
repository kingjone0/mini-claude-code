"""env_probe.py —— 验证「AGENT_REPO_MAP_DEFAULT 是否真的到达 MCP 子进程」。

背景：`coding_agent.py` 在 `mcp_servers[name]["env"]` 里显式放了 `AGENT_REPO_ROOT`，
但它**没有**放 `AGENT_REPO_MAP_DEFAULT`。而 SDK 给子进程的环境是`{**os.environ, **env}`
（CLAUDE.md 陷阱 14）—— 也就是说：**父进程里没设**时它就不存在，设了才可能继承。
**不验证就断言「能透传」是猜。** 本脚本把两种情形都跑一遍，用子进程自己打印的
os.environ 当证据。

三个对照：
  C0  父进程不设该变量        -> 子进程应看到 None（说明不设时确实没有）
  C1  父进程设了该变量        -> 观察子进程是否看到（决定要不要显式传）
  C2  options.env 里显式传    -> 必须看到（这才是我们要的实现）

用法：
    python servers/experiments/env_probe.py
产出：`out/env-probe.json` + 控制台逐条对照。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "servers" / "experiments"))

from agent.config import (  # noqa: E402
    CLI_PATH,
    ConfigError,
    force_utf8_output,
    isolate_from_parent_env,
    load_endpoint_env,
)

from claude_agent_sdk import ClaudeAgentOptions, query  # noqa: E402

SERVER = PROJECT_ROOT / "servers" / "experiments" / "env_probe_server.py"
KEY = "AGENT_REPO_MAP_DEFAULT"
EVIDENCE = PROJECT_ROOT / "servers" / "experiments" / "out" / "env-probe-child.jsonl"


def flat(block_or_msg) -> str:
    """把工具结果块归一成纯文本（dict / list / str 三种形状都可能）。"""
    content = getattr(block_or_msg, "content", block_or_msg)
    if isinstance(content, dict):
        return str(content.get("result") or content.get("text") or content)
    if isinstance(content, list):
        out = []
        for item in content:
            if isinstance(item, dict):
                out.append(str(item.get("text") or item.get("result") or item))
            else:
                out.append(str(item))
        return "\n".join(out)
    return str(content)


async def probe(label: str, *, set_parent: str | None, pass_explicitly: str | None) -> dict:
    """跑一次最小 query，只调 ping，看子进程报出来的环境值。"""
    if set_parent is None:
        os.environ.pop(KEY, None)
    else:
        os.environ[KEY] = set_parent

    env, _ = load_endpoint_env()
    isolate_from_parent_env()

    mcp_env: dict[str, str] = {
        # 让**子进程自己**把实测值写到证据文件（不经模型转述）
        "ENV_PROBE_CASE": label,
        "ENV_PROBE_EVIDENCE": str(EVIDENCE),
    }
    if pass_explicitly is not None:
        mcp_env[KEY] = pass_explicitly

    options = ClaudeAgentOptions(
        cli_path=CLI_PATH,
        mcp_servers={
            "env-probe": {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(SERVER)],
                "env": mcp_env,
            }
        },
        tools=[],
        allowed_tools=["mcp__env-probe__ping"],
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        env=env,
        max_turns=3,
    )

    child_value: str | None = None
    tool_called = False
    texts: list[str] = []

    def harvest(text: str) -> None:
        """从工具结果里抠出 `KEY=...` 这一行。

        ⚠️ 只认 `AGENT_REPO_MAP_DEFAULT=` 开头那一行的**最后一个引号对**
        —— 直接 split("=",1) 会把 `'symbols'` 的引号带进来，也会把后面
        `None`（未设置）这类中文备注混入，判据就不干净了。
        """
        nonlocal child_value
        for line in text.splitlines():
            line = line.strip().lstrip("|").strip()
            if line.startswith(KEY + "="):
                val = line.split("=", 1)[1].strip()
                # 去掉 markdown 表格的收尾竖线与中文备注
                val = val.split("|")[0].strip()
                child_value = val
                return

    try:
        async for message in query(prompt="调用 ping 工具，只要一次。", options=options):
            content = getattr(message, "content", None)
            texts.append(f"<MSG {type(message).__name__} content={type(content).__name__}>")
            if content is None:
                continue
            blocks = content if isinstance(content, list) else [content]
            for block in blocks:
                raw = flat(block)
                texts.append(f"  BLOCK {type(block).__name__}: {raw[:200]}")
                if KEY + "=" in raw:
                    tool_called = True
                    harvest(raw)
    except Exception as exc:  # noqa: BLE001 —— 探针要报出失败，不要崩
        return {"case": label, "device": "error", "error": f"{type(exc).__name__}: {exc}",
                "transcript": texts[-10:]}

    return {
        "case": label,
        "parent_env": set_parent,
        "passed_in_mcp_env": pass_explicitly,
        "child_seen": child_value,
        "tool_called": tool_called,
        "transcript": texts[-8:],
    }


async def main() -> int:
    force_utf8_output()
    out_dir = PROJECT_ROOT / "servers" / "experiments" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 每次重跑都清空：证据文件是**子进程自己追加**的，不清会串台
    if EVIDENCE.exists():
        EVIDENCE.unlink()

    cases = [
        ("C0 父进程未设 + 未显式传", None, None),
        ("C1 父进程设了 + 未显式传", "symbols", None),
        ("C2 父进程设了 + 显式传", "symbols", "symbols"),
        ("C3 父进程未设 + 显式传", None, "summary"),
    ]

    results = []
    for label, set_parent, explicit in cases:
        print(f"\n=== {label} ===")
        try:
            r = await probe(label, set_parent=set_parent, pass_explicitly=explicit)
        except ConfigError as exc:
            print(f"  配置错误：{exc}")
            return 2
        results.append({
            "case": label,
            "parent_env": set_parent,
            "passed_in_mcp_env": explicit,
            "error": r.get("error"),
        })
        print(f"  父进程 os.environ[{KEY}] = {set_parent!r}")
        print(f"  mcp_servers.env[{KEY}]  = {explicit!r}")
        if r.get("error"):
            print(f"  [错误] {r['error']}")

    # ── 权威判据：子进程自己写下的实测值 ──────────────────────────────────
    seen: dict[str, dict] = {}
    if EVIDENCE.is_file():
        for line in EVIDENCE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                seen[rec["case"]] = rec

    for r in results:
        rec = seen.get(r["case"]) or {}
        r["child_seen"] = rec.get("AGENT_REPO_MAP_DEFAULT")
        print(f"  子进程实测 AGENT_REPO_MAP_DEFAULT = {r['child_seen']!r}")

    (out_dir / "env-probe.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 结论（子进程自己落盘的 os.environ 取值，不是模型的转述）===")
    for r in results:
        print(f"  {r['case']:<28} -> 子进程实测 {r.get('child_seen')!r}")

    def get(prefix: str) -> str | None:
        return next((r["child_seen"] for r in results if r["case"].startswith(prefix)), None)

    c1, c3 = get("C1"), get("C3")
    print("\n  读法：")
    print(f"    · 只靠父进程继承、不显式传（C1）-> 子进程实测 {c1!r}")
    print(f"    · options.env 显式传（C3）      -> 子进程实测 {c3!r}")
    verdict = (
        "两种通道都到得了；父进程继承（C1）已经足够，显式传是**加固**不是必需"
        if c1 and c3 else
        "父进程继承不可靠 -> 必须显式传（C3）"
    )
    print(f"    判定：{verdict}")
    print(f"\n  子进程原始证据：{EVIDENCE}")
    print(f"  汇总：{out_dir / 'env-probe.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))