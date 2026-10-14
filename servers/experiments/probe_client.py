"""MCP 探针：保持 stdin 打开，并把 server 的原始 stdout 逐行落盘。

它解决两个别的办法解决不了的问题：

  (a) 用管道喂 JSON-RPC 时 stdin 会提前 EOF，tools/call 的响应还没读就收摊了。
      本探针发完请求**不关 stdin**，一直等到收到响应为止。

  (b) 想知道「管道里除了 JSON 还有没有别的东西」，就得在解析**之前**把原始字节
      抄一份。本探针不做任何过滤，读到什么字节就原样写进 .stdout.raw。

用法：
    python probe_client.py [--protocol auto|legacy|2026-07-28] <输出前缀> -- <启动命令...>

    --protocol auto        （默认）把版本协商交给 SDK 自己（Client(mode="auto")）
    --protocol legacy      旧式 initialize 握手，raw wire
    --protocol 2026-07-28  现代协议，raw wire（modern 是它的别名）

两条路径是**分开的**，不要混成一套：

  · SDK 路径（auto）—— 端到端可用性最好，但原始 JSON-RPC 字节看不见：
    子进程是 SDK 拉起来的，管道在它手里。所以它**不产出 .stdout.raw**，
    也不假装有（把解析后的消息再序列化一遍叫它 "raw"，那是伪造证据）。
  · raw 路径（legacy / 2026-07-28）—— 自己写 JSON-RPC、自己保存原始字节。
    两条代际的**建连流程**不同，各自成段，谁都不借用谁的流程。

探针的价值是「观察协议行为」，不是「能连上」。两条路径各看一半，谁都不替代谁。

产出（raw 路径，前缀 + 扩展名）：
    <base>.stdout.raw        server 的原始 stdout，逐行、未解析
    <base>.stderr.log        server 的 stderr
    <base>.result.json       探针自己整理的结果（响应、工具名、非 JSON 行数、退出码）
    <base>.server.log        server **自己**写的文件日志（MCP_SERVER_LOG 指过去的）
其中 <base>：legacy 档就是 <前缀> 本身（改造前的名字，昨天的证据仍可原样复现）；
其余档是 <前缀>.<mode>，**不覆盖**任何既有 .raw。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

TIMEOUT = 20.0

# ── 协议代际 ────────────────────────────────────────────────────────────
LEGACY_VERSION = "2025-06-18"
"""legacy 档在 initialize 里提出的版本串。

改造前这个字符串是**写死在 main() 里**的，而且没有第二条路 —— 整个探针因此
只能探测握手时代的协议。现在它只是 legacy 档的一个参数，别的档不再碰它。
"""

MODERN_VERSION = "2026-07-28"
"""现代档的版本串。

2026-07-28 取消了 initialize / notifications/initialized 握手：
建连时发的是 server/discover —— 那是**协议发现（discovery）**，用来发现 server
的能力和版本，**不等于新版握手**；之后版本随**每个请求**的 params._meta 走。
（服务端的代际判定见 mcp/server/runner.py：第一帧带 _meta 信封且不是 initialize
→ 开现代连接；此后在连接上再发 initialize 会被拒 -32022。）
"""

PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"

MODES = ("auto", "legacy", MODERN_VERSION)
MODE_ALIASES = {"modern": MODERN_VERSION}


def force_utf8_output() -> None:
    """把探针自己的 stdout/stderr 固定成 UTF-8。

    不这么做的话，输出编码跟着**控制台代码页**走（本机是 GBK），于是：
      · 探针打印的内容里只要有 GBK 装不下的字符就**直接 UnicodeEncodeError 崩掉**
        —— 已实测踩到：中文字符能过，`⚠️` 过不去。
      · 探针会把 server 返回的**用户文件内容**打出来（read_study_file 的回显），
        那里面出现什么字符不由我们决定 —— 崩掉就把原始证据盖住了。
    errors="backslashreplace" 保证即使真有装不下的字符也降级成转义，不丢信息、不中断。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def pump(stream, sink_queue: queue.Queue | None, raw_file) -> None:
    """独占一个线程读流：原始字节落盘，同时（可选）把行丢进队列供主线程消费。"""
    try:
        for line in stream:
            raw_file.write(line)
            raw_file.flush()
            if sink_queue is not None:
                sink_queue.put(line)
    except Exception as exc:  # noqa: BLE001 - 探针要如实记录，不能吞
        if sink_queue is not None:
            sink_queue.put(exc)
    finally:
        raw_file.flush()
        if sink_queue is not None:
            sink_queue.put(None)  # 哨兵：流结束


def recv(lines: queue.Queue, what: str) -> dict | None:
    """取一行并解析。返回 None 表示流结束或超时。"""
    try:
        line = lines.get(timeout=TIMEOUT)
    except queue.Empty:
        print(f"  [超时 {TIMEOUT}s] 没等到 {what} 的响应", file=sys.stderr)
        return None
    if line is None:
        print(f"  [EOF] {what}: server 的 stdout 提前关闭", file=sys.stderr)
        return None
    if isinstance(line, Exception):
        print(f"  [读错误] {what}: {line}", file=sys.stderr)
        return None
    try:
        return json.loads(line.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  [解析失败] {what}: {exc} <- 原始行 {line!r}", file=sys.stderr)
        return None


def send(proc: subprocess.Popen, payload: dict) -> bool:
    """发一行 JSON-RPC。返回 False 表示管道已经断了（对端死了或把流收走了）。

    这条路径是真会走到的：conda run 不加 --no-capture-output 时，
    server 的 stdout 被 conda 吞掉，探针这边先是读到 EOF，接着再写就 OSError。
    探针要如实报告，不能自己崩掉——崩掉就把原始证据盖住了。
    """
    if proc.stdin is None or proc.stdin.closed:
        return False
    try:
        proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        proc.stdin.flush()
        return True
    except (OSError, ValueError) as exc:
        print(f"  [写失败] 管道已断：{exc}", file=sys.stderr)
        return False


def artifact_paths(prefix: str, mode: str) -> dict[str, str]:
    """产出文件名。

    legacy 档沿用改造前的名字（`<前缀>.stdout.raw` …）—— 昨天的证据要能原样复现。
    其余档加 mode 后缀 —— **不覆盖**任何既有 .raw。
    """
    base = prefix if mode == "legacy" else f"{prefix}.{mode}"
    return {
        "base": base,
        "raw": f"{base}.stdout.raw",
        "err": f"{base}.stderr.log",
        "result": f"{base}.result.json",
        "server_log": f"{base}.server.log",
    }


def reset_probe_server_log(server_log: str) -> None:
    """每次实验一份新日志：spawn 之前先把上次的删掉。

    logging.FileHandler 默认 append —— 对**长期运行的真实 server** 这是对的，日志本该累积，
    所以**不动它的全局行为**。但探针产出的 <前缀>.<mode>.server.log 是**一次实验的产物**：
    不删的话，同一个 prefix 连跑两次会看到上一轮的 PID 和 handler 记录，
    读的人分不清哪条属于本轮 —— 那就不是证据了。

    ⚠️ 只删**探针自己指定的**那一个文件。本体默认的 local-tools-server.log 不归探针管，
    它仍然照常追加（真实 server 的运行日志不该被一次实验清掉）。
    """
    Path(server_log).unlink(missing_ok=True)


def modern_envelope(version: str = MODERN_VERSION) -> dict:
    """2026-07-28 的 per-request 信封。

    服务端的分类阶梯要求 protocolVersion 和 clientCapabilities 两个键**都在**，
    少一个会被判 INVALID_PARAMS（mcp/shared/inbound.py）。
    """
    return {
        PROTOCOL_VERSION_META_KEY: version,
        CLIENT_INFO_META_KEY: {"name": "probe-client", "version": "0"},
        CLIENT_CAPABILITIES_META_KEY: {},
    }


def request_params(modern: bool, params: dict | None = None) -> dict:
    """每个请求的 params 形状：现代档套信封，legacy 档就是裸 params。"""
    if not modern:
        return params or {}
    return {**(params or {}), "_meta": modern_envelope()}


def legacy_initialize_payload(req_id: int) -> dict:
    """旧式握手请求。legacy 档用它建连；现代档只用它做反向对照。"""
    return {
        "jsonrpc": "2.0", "id": req_id, "method": "initialize",
        "params": {
            "protocolVersion": LEGACY_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "probe-client", "version": "0"},
        },
    }


def raw_probe(prefix: str, cmd: list[str], mode: str) -> int:
    """raw wire 路径：自己写 JSON-RPC，自己留原始字节。

    mode 决定**建连方式**和**每个请求的 params 形状**。两条代际分开成段，互不借用。
    """
    modern = mode == MODERN_VERSION
    arts = artifact_paths(prefix, mode)
    out_base = Path(arts["base"])
    out_base.parent.mkdir(parents=True, exist_ok=True)
    # 本次实验的 server 日志必须是干净的（真实 server 的默认日志不受影响）
    reset_probe_server_log(arts["server_log"])

    print(f"=== 探针启动（raw 路径）===")
    print(f"  启动命令: {' '.join(cmd)}")
    print(f"  请求的 mode: {mode}（{'现代' if modern else 'legacy'} 协议）")
    print(f"  输出前缀: {out_base}")

    result: dict = {
        "command": cmd,
        "path": "raw",
        "protocol_mode": mode,
        "raw_capture": True,
        "responses": {},
        "raw_stdout_lines": [],
        "notes": [],
    }

    # ⚠️ 把 server 自己的文件日志指到探针名下的文件：验证不污染真实日志，
    #    而且这份日志是**独立于 stderr** 的证据（Claude Code 会吞掉 server 的 stderr）。
    child_env = {**os.environ, "MCP_SERVER_LOG": arts["server_log"]}

    with (
        open(arts["raw"], "wb") as raw_out,
        open(arts["err"], "wb") as raw_err,
    ):
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,  # 不缓冲，逐行实时
            env=child_env,
        )

        lines: queue.Queue = queue.Queue()
        threading.Thread(target=pump, args=(proc.stdout, lines, raw_out), daemon=True).start()
        threading.Thread(target=pump, args=(proc.stderr, None, raw_err), daemon=True).start()

        time.sleep(0.5)  # 给 server 一点启动时间，顺便让启动期的杂输出先落盘

        # ── 建连：两代协议在这里分道 ────────────────────────────────
        # 注意用词：现代档这一步是**协议发现（discovery）**，不是握手 ——
        # 2026-07-28 已取消旧式 initialize 握手；server/discover 只是用来发现
        # server 的能力/版本，两代建连流程不是同一件事。
        if modern:
            print("  协议发现：server/discover（2026-07-28 没有 initialize 握手）")
            send(proc, {
                "jsonrpc": "2.0", "id": 1, "method": "server/discover",
                "params": {"_meta": modern_envelope()},
            })
            opening = recv(lines, "server/discover")
            result["responses"]["server/discover"] = opening
            if opening and "result" in opening:
                supported = opening["result"].get("supportedVersions")
                result["discover_supported_versions"] = supported
                # 现代协议不是"协商"出来的：客户端在**每个请求**里声明版本，
                # 服务端要么照办要么回 -32022。所以这里记 asserted，不记 negotiated。
                result["asserted_protocol_version"] = MODERN_VERSION
                print(f"  server/discover OK: supportedVersions={supported} "
                      f"serverInfo={opening['result'].get('serverInfo')}")
            else:
                print(f"  server/discover 失败: {opening}")
        else:
            send(proc, legacy_initialize_payload(1))
            opening = recv(lines, "initialize")
            result["responses"]["initialize"] = opening
            if opening and "result" in opening:
                # legacy 这边是**真的协商**：服务端在 result 里回它选定的版本。
                result["negotiated_protocol_version"] = opening["result"].get("protocolVersion")
                print(f"  initialize OK: server={opening['result'].get('serverInfo')} "
                      f"protocol={opening['result'].get('protocolVersion')}")
            else:
                print(f"  initialize 失败: {opening}")

        opening_ok = bool(opening and "result" in opening)

        if not opening_ok:
            result["notes"].append(
                "建连未成功 —— 后续请求全部跳过。这正是反向对照期望的形态。"
            )
            print("  建连未成功，跳过后续请求")
        else:
            if not modern:
                # 现代协议取消了这条通知；legacy 必须有它，否则工具调用会被拒。
                send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            else:
                result["notes"].append("现代档未发送 notifications/initialized：2026-07-28 已取消该通知。")

            send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                        "params": request_params(modern)})
            tools = recv(lines, "tools/list")
            result["responses"]["tools/list"] = tools
            names = [t["name"] for t in tools["result"]["tools"]] if tools and "result" in tools else []
            result["tool_names"] = names
            print(f"  tools/list -> {names}")

            # 关键一步：调工具之后**不关 stdin**，让 handler 里的 print 有机会发生。
            send(proc, {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": request_params(modern, {"name": "word_count", "arguments": {"text": "hello world foo"}}),
            })
            call = recv(lines, "tools/call word_count")
            result["responses"]["tools/call:word_count"] = call
            if call and "result" in call:
                text = call["result"]["content"][0]["text"]
                result["word_count_text"] = text
                print(f"  tools/call word_count -> {text}")
            else:
                print(f"  tools/call word_count 失败: {call}")
                result["notes"].append("word_count 调用未拿到合法响应")

            if "read_study_file" in names:
                send(proc, {
                    "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": request_params(modern, {"name": "read_study_file", "arguments": {"relpath": "大纲.md"}}),
                })
                call2 = recv(lines, "tools/call read_study_file")
                result["responses"]["tools/call:read_study_file"] = call2
                if call2 and "result" in call2:
                    head = call2["result"]["content"][0]["text"][:60].replace("\n", " ")
                    print(f"  tools/call read_study_file -> {head}…")
                else:
                    print(f"  tools/call read_study_file 失败: {call2}")

                # 越界对照：这个必须被拒绝，否则沙箱等于没有
                send(proc, {
                    "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": request_params(modern, {"name": "read_study_file",
                                                      "arguments": {"relpath": "../../Windows/win.ini"}}),
                })
                call3 = recv(lines, "tools/call 越界")
                result["responses"]["tools/call:escape_attempt"] = call3
                if call3 and "result" in call3:
                    text3 = call3["result"]["content"][0]["text"]
                    result["escape_attempt_text"] = text3
                    print(f"  越界尝试 -> {text3[:80]}")

            if modern:
                # 反向对照：在**已经建立的现代连接**上再发一次旧式 initialize。
                # 规定上必须被拒（-32022）。这一条才真正证明"现代档没走 initialize" ——
                # 光看"连上了"证明不了：建连成功本身不说明走的是哪一代。
                # ⚠️ 顺序不能反：stdio 连接由**第一帧**决定代际，initialize 放前面会直接开成 legacy。
                send(proc, legacy_initialize_payload(6))
                reject = recv(lines, "initialize（现代连接上的反向对照）")
                result["responses"]["initialize_on_modern"] = reject
                if reject and "error" in reject:
                    result["initialize_on_modern_code"] = reject["error"].get("code")
                    print(f"  反向对照 initialize -> 被拒 code={reject['error'].get('code')} "
                          f"{reject['error'].get('message')}")
                else:
                    result["notes"].append("⚠️ 现代连接上的 initialize 没被拒 —— 与预期不符")
                    print(f"  ⚠️ 反向对照 initialize 没被拒: {reject}")

        # 到这里才收工：全程 stdin 一直开着
        if proc.stdin is not None and not proc.stdin.closed:
            with suppress(OSError):
                proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        result["exit_code"] = proc.returncode
        print(f"  server 退出码: {proc.returncode}")

        time.sleep(0.3)  # 让两个 pump 线程把尾巴写完

    raw_lines = Path(arts["raw"]).read_bytes().splitlines()
    result["raw_stdout_lines"] = [ln.decode("utf-8", errors="replace") for ln in raw_lines]

    # 这是 (b) 的核心判据：管道里到底有几种行？
    non_json = [ln for ln in result["raw_stdout_lines"] if ln.strip() and not ln.lstrip().startswith("{")]
    result["non_json_stdout_lines"] = non_json

    print(f"\n=== 管道体检 ===")
    print(f"  stdout 原始行数: {len(result['raw_stdout_lines'])}")
    print(f"  其中不是 JSON 的行: {len(non_json)}")
    for ln in non_json:
        print(f"    ! {ln!r}")
    if not result["raw_stdout_lines"]:
        print("    （管道里一个字节都没有 —— 这本身就是异常信号）")
    elif not non_json:
        print("    （干净：每一行都是 JSON）")

    Path(arts["result"]).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已落盘: {arts['raw']} / {arts['err']} / {arts['result']}")
    print(f"  server 自己的文件日志: {arts['server_log']}")

    ok = bool(result.get("word_count_text"))
    print(f"\n=== 判定：{'协议正常' if ok else '协议异常'} ===")
    return 0 if ok else 1


def _first_text(call_result) -> str | None:
    """SDK 的 CallToolResult -> 第一块文本（SDK 给的是对象，不是裸 dict）。"""
    for block in getattr(call_result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return text
    return None


def sdk_probe(prefix: str, cmd: list[str], mode: str) -> int:
    """SDK 路径（auto 档）：版本协商交给 SDK 自己，不重新实现。

    ⚠️ 这条路径**拿不到原始 JSON-RPC 字节** —— 子进程由 SDK 拉起，管道在它手里。
    所以它不产出 .stdout.raw，也不假装有。要看字节请走 raw 路径。
    它给的是另一种证据：SDK 报告的**实际** protocol_version，以及这次到底走了
    discover 还是 initialize —— 这两个槽位是 SDK 自己填的，不是我们照常量猜的。
    """
    import asyncio

    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    arts = artifact_paths(prefix, mode)
    Path(arts["base"]).parent.mkdir(parents=True, exist_ok=True)
    # 本次实验的 server 日志必须是干净的（真实 server 的默认日志不受影响）
    reset_probe_server_log(arts["server_log"])

    result: dict = {
        "command": cmd,
        "path": "sdk",
        "requested_mode": mode,
        "raw_capture": False,
        "responses": {},
        "notes": ["SDK 路径不产出 .stdout.raw：原始字节在 SDK 手里，看不见。"],
    }

    async def run() -> None:
        params = StdioServerParameters(
            command=cmd[0],
            args=cmd[1:],
            # SDK 默认只透传一份环境白名单。显式给全量环境（同 raw 路径的继承行为），
            # 否则 SDK 档和 raw 档的子进程环境不一样，两条路的结果没法对比。
            env={**os.environ, "MCP_SERVER_LOG": arts["server_log"]},
        )
        async with Client(params, mode=mode) as client:
            negotiated = client.protocol_version
            result["negotiated_protocol_version"] = negotiated
            result["server_info"] = str(client.server_info)

            # 直接证据：SDK 自己记的两个**协商结果**槽位，同一时刻最多一个非空
            # （discover 是协议发现，initialize 才是握手 —— 两者不是一回事）。
            try:
                if client.session.discover_result is not None:
                    went = "discover"
                elif client.session.initialize_result is not None:
                    went = "initialize"
                else:
                    went = "unknown"
            except AttributeError:  # SDK 换了属性名也不能让探针崩掉
                went = "unknown"
            result["negotiated_via"] = went

            requested = client.session.discover_result if went == "discover" else None
            if requested is not None:
                result["discover_supported_versions"] = list(requested.supported_versions)

            if went == "initialize":
                result["notes"].append(
                    "auto 回退到了 initialize 握手 —— 说明对端不是现代协议 server，或 discover 未被接受。"
                )

            tools = await client.list_tools()
            names = [t.name for t in tools.tools]
            result["tool_names"] = names

            call = await client.call_tool("word_count", {"text": "hello world foo"})
            text = _first_text(call)
            if text is not None:
                result["word_count_text"] = text

            if "read_study_file" in names:
                call2 = await client.call_tool("read_study_file", {"relpath": "大纲.md"})
                result["read_study_file_text"] = _first_text(call2)
                call3 = await client.call_tool(
                    "read_study_file", {"relpath": "../../Windows/win.ini"}
                )
                escaped = _first_text(call3)
                if escaped is not None:
                    result["escape_attempt_text"] = escaped

    print(f"=== 探针启动（SDK 路径）===")
    print(f"  启动命令: {' '.join(cmd)}")
    print(f"  请求的 mode: {mode}")

    sdk_error = False
    try:
        asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - 探针要如实记录，不能自己崩掉
        sdk_error = True
        result["notes"].append(f"SDK 路径异常：{type(exc).__name__}: {exc}")
        print(f"  ✗ SDK 路径失败：{type(exc).__name__}: {exc}", file=sys.stderr)
    else:
        print(f"  实际协商到 protocol_version = {result.get('negotiated_protocol_version')}"
              f"（走去的是 {result.get('negotiated_via')}）")
        print(f"  serverInfo = {result.get('server_info')}")
        print(f"  tools/list -> {result.get('tool_names')}")
        if result.get("word_count_text"):
            print(f"  tools/call word_count -> {result['word_count_text']}")
        if result.get("escape_attempt_text"):
            print(f"  越界尝试 -> {result['escape_attempt_text'][:80]}")
        if result.get("discover_supported_versions") is not None:
            print(f"  server 支持的现代版本: {result['discover_supported_versions']}")

    Path(arts["result"]).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已落盘: {arts['result']}")
    print(f"  server 自己的文件日志: {arts['server_log']}")
    print("  ⚠️ 本档不产出 .stdout.raw（原始字节在 SDK 手里）。要看字节："
          f"--protocol legacy 或 --protocol {MODERN_VERSION}")

    # 出过异常就不算"正常"，哪怕 word_count 恰好在异常之前已经拿到了 ——
    # 否则半截成功的运行会被读成通过。
    ok = bool(result.get("word_count_text")) and not sdk_error
    print(f"\n=== 判定：{'协议正常' if ok else '协议异常'} ===")
    return 0 if ok else 1


def main() -> int:
    force_utf8_output()

    argv = sys.argv[1:]
    if "--" not in argv:
        print(__doc__, file=sys.stderr)
        return 2

    split = argv.index("--")
    head, cmd = argv[:split], argv[split + 1:]

    # 手写解析而不是 argparse：`--` 之后是要**原样透传**的启动命令，
    # argparse 会去解析它（把 server 自己的 --flag 当成探针的参数）。
    mode, rest = "auto", []
    i = 0
    while i < len(head):
        tok = head[i]
        if tok == "--protocol":
            if i + 1 >= len(head):
                print("错误：--protocol 后面要跟一个值", file=sys.stderr)
                return 2
            mode, i = head[i + 1], i + 2
            continue
        if tok.startswith("--protocol="):
            mode, i = tok.split("=", 1)[1], i + 1
            continue
        rest.append(tok)
        i += 1

    mode = MODE_ALIASES.get(mode, mode)
    if mode not in MODES:
        print(f"错误：--protocol 只认 {list(MODES)}（modern 是 {MODERN_VERSION} 的别名），"
              f"收到 {mode!r}", file=sys.stderr)
        return 2

    prefix = rest[0] if rest else "probe"
    if not cmd:
        print("错误：-- 后面没有启动命令", file=sys.stderr)
        return 2

    if mode == "auto":
        return sdk_probe(prefix, cmd, mode)
    return raw_probe(prefix, cmd, mode)


if __name__ == "__main__":
    raise SystemExit(main())
