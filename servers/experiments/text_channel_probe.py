"""text_channel_probe.py —— 记录 code_tools_server 在**协议层实际发出**的两份文本。

为什么不能靠读源码：A/B 的"唯一变量"必须是**协议层的事实**，不是"我以为设了这个
环境变量它就会变"。本探针直接和 server 讲 MCP，把 `initialize` 结果里的
`instructions` 与 `tools/list` 里每个工具的 `description` 原样落盘，供逐字节对照。

它回答三个问题：
  1. 改之前和改之后，v1 那一档是不是**逐字节一致**（否则"臂 A"已经不是原来的臂 A）；
  2. 两个开关各自**只**改了自己那一份文本（没有连带改动别的工具）；
  3. 非法取值是不是真的被拒绝（而不是静默回落到 v1）。

用法：
    python text_channel_probe.py --out out/desc-baseline.json
    $env:AGENT_SERVER_INSTRUCTIONS="v2"; python text_channel_probe.py --out out/desc-i2.json

产出：一份规范化 JSON（instructions / serverInfo / 每个工具的 description 与 inputSchema）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# ⚠️ 必须在任何 print 之前把输出切到 UTF-8（CLAUDE.md 陷阱 16）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agent.config import force_utf8_output  # noqa: E402

force_utf8_output()

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVER = REPO_ROOT / "servers" / "code_tools_server.py"
TRACKED_ENV = ("AGENT_SERVER_INSTRUCTIONS", "AGENT_REPO_MAP_DESC", "AGENT_REPO_MAP_DEFAULT")


def rpc(proc: subprocess.Popen, payload: dict, expect_id: int) -> dict:
    """发一条请求，读到 id 匹配的那条响应为止。

    ⚠️ 只按 id 匹配、不按顺序：server 也可能先吐出别的通知。
    """
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            raise SystemExit(f"server 关掉了 stdout（在等 id={expect_id}）—— 多半是启动即失败")
        obj = json.loads(line.decode("utf-8", errors="replace"))
        if obj.get("id") == expect_id:
            if "error" in obj:
                raise SystemExit(f"id={expect_id} 返回错误：{obj['error']}")
            return obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="规范化结果的输出路径")
    args = ap.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = Path(__file__).resolve().parent / out

    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
        env=dict(os.environ),
    )
    try:
        init = rpc(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "text-channel-probe", "version": "1.0.0"}},
        }, 1)
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        ) + "\n").encode("utf-8"))
        proc.stdin.flush()
        listed = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, 2)
    finally:
        proc.kill()

    result = init["result"]
    tools = listed["result"]["tools"]
    record = {
        # 把生效的环境变量一起记下来：产物必须能自证"这是哪一档"（陷阱 14）。
        "env": {k: os.environ.get(k) for k in TRACKED_ENV},
        "serverInfo": result.get("serverInfo"),
        "instructions": result.get("instructions"),
        "tools": {
            t["name"]: {"description": t.get("description"),
                        "inputSchema": t.get("inputSchema")}
            for t in tools
        },
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")

    print(f"server 环境：{record['env']}")
    print(f"instructions            = {len(record['instructions'] or '')} 字符")
    for name, tool in sorted(record["tools"].items()):
        print(f"  {name:12s} description = {len(tool['description'] or ''):5d} 字符")
    print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
