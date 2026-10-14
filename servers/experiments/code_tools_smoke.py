"""code_tools_smoke.py —— code_tools_server 的工具级冒烟探针。

与 probe_client.py 的分工：

    probe_client.py        通用 MCP 协议探针（三档协议、原始字节落盘）
                           —— 面向 local_tools_server，产出 VERIFY-mcp.md 的证据
    code_tools_smoke.py    面向 code_tools_server 的**工具级**冒烟
                           —— 逐个调用工具并断言，含沙箱越界对照

为什么要单独写一个：`cmd | python server.py` 这种喂法**会在写完就关 stdin**，
server 检测到 EOF 立刻退出，`tools/call` 的响应根本来不及写回。
本探针发完请求**不关 stdin**，一直等到收到响应为止。

用法：
    python code_tools_smoke.py --out out/ct-smoke -- <server 启动命令…>

产出：
    <out>.stdout.raw            协议线原始字节（逐行）
    <out>.stderr.log            server 的 stderr
    <out>.arm-symbols.stderr.log  13d/13e 那档的 server 日志（AGENT_REPO_MAP_DEFAULT=symbols）
    <out>.arm-bogus.stderr.log    13f/13g 那档的 server 日志（非法值）
    <out>.result.json           结构化的检查结论

⚠️ 探针自己**不设置** AGENT_REPO_MAP_DEFAULT（13 号那一档刻意让"未设置"的默认行为
    也被覆盖到）。父进程若带了这个变量，冒烟结果会被污染 —— 所以启动时显式摘掉它，
    由 13d~13g 在**独立子进程**里按需设置。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

# ⚠️ 必须在任何 print 之前把输出切到 UTF-8（CLAUDE.md 陷阱 16）。
#    本文件里就有 ⚠️ 与中文断言详情，GBK 控制台打到一半会 UnicodeEncodeError，
#    而且会把真正的证据盖住。原先这里自带一份同名函数，现改为复用公共模块那份 ——
#    「新程序一律先调」这条纪律落在一个实现上，比落在多份拷贝上可靠。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agent.config import force_utf8_output  # noqa: E402

TIMEOUT = 30.0

# A/B 实验的唯一变量。断言要用到的两个取值。
REPO_MAP_ENV = "AGENT_REPO_MAP_DEFAULT"
ARM_SYMBOLS = "symbols"
ARM_SUMMARY = "summary"

# 父进程若带了这个变量，会经由 subprocess 继承**污染 13 号那一档**
# （那一档刻意验"未设置时的默认行为"）。子进程的环境是 dict(os.environ) 的拷贝，
# 所以**在这里摘掉**是有效的 —— 改的是本进程的 os.environ，会传导给后续 spawn。
PARENT_REPO_MAP_DEFAULT = os.environ.pop(REPO_MAP_ENV, None)


class Client:
    """保持 stdin 打开的极简 JSON-RPC 客户端。"""

    def __init__(self, cmd: list[str], out_prefix: Path,
                 env: dict[str, str] | None = None, cwd: str | None = None) -> None:
        self.cmd = cmd
        self.out_prefix = out_prefix
        self.lines: list[dict] = []
        self.raw: list[str] = []
        self._lock = threading.Lock()
        self._next_id = 1

        # 拉起一个**带指定环境变量**的 server 进程 —— 对照实验只能这么做：
        # `repo_map` 的默认值是在子进程**启动时**从 os.environ 读的（模块级常量），
        # 所以"换一档默认值"必须换一个进程，改不了已有进程的取值。
        child_env = dict(os.environ)
        child_env.update(env or {})

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=open(f"{out_prefix}.stderr.log", "wb"),
            cwd=cwd or str(Path(cmd[-1]).resolve().parent.parent),  # 仓库根
            env=child_env,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            with self._lock:
                self.raw.append(text)
                with suppress(json.JSONDecodeError):
                    self.lines.append(json.loads(text))

    def send(self, payload: dict) -> bool:
        """返回 False 表示管道已断（对端死了）。"""
        if self.proc.stdin is None or self.proc.stdin.closed:
            return False
        try:
            self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError) as exc:
            print(f"  [写失败] 管道已断：{exc}", file=sys.stderr)
            return False

    def wait_for(self, req_id: int) -> dict | None:
        deadline = time.time() + TIMEOUT
        while time.time() < deadline:
            with self._lock:
                for m in self.lines:
                    if m.get("id") == req_id:
                        return m
            time.sleep(0.05)
        return None

    def call(self, method: str, params: dict | None = None) -> dict | None:
        req_id = self._next_id
        self._next_id += 1
        if not self.send({"jsonrpc": "2.0", "id": req_id, "method": method,
                          "params": params or {}}):
            return None
        return self.wait_for(req_id)

    def notify(self, method: str, params: dict | None = None) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self) -> int:
        if self.proc.stdin and not self.proc.stdin.closed:
            with suppress(OSError):
                self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        time.sleep(0.3)  # 让 reader 线程把尾巴写完
        return self.proc.returncode or 0

    def dump(self) -> None:
        Path(f"{self.out_prefix}.stdout.raw").write_text(
            "\n".join(self.raw), encoding="utf-8"
        )


def tool_text(msg: dict | None) -> str:
    """取出 tools/call 的文本结果；失败时返回可读的错误描述。"""
    if msg is None:
        return "<无响应：超时>"
    if "error" in msg:
        return f"<JSON-RPC 错误> {msg['error']}"
    result = msg.get("result") or {}
    if result.get("isError"):
        return f"<工具报错> {result}"
    content = result.get("content") or []
    return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))


def main() -> int:
    force_utf8_output()

    argv = sys.argv[1:]
    if "--" not in argv:
        print("用法: python code_tools_smoke.py --out <前缀> -- <server 启动命令…>",
              file=sys.stderr)
        return 2
    split = argv.index("--")
    opts, cmd = argv[:split], argv[split + 1:]
    if "--out" not in opts or not cmd:
        print("缺 --out 或 server 启动命令", file=sys.stderr)
        return 2
    out_prefix = Path(opts[opts.index("--out") + 1]).resolve()
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    print(f"=== code_tools_server 冒烟 ===")
    print(f"server: {' '.join(cmd)}")
    print(f"输出前缀: {out_prefix}")
    print(f"父进程 {REPO_MAP_ENV} = {PARENT_REPO_MAP_DEFAULT!r}"
          f"{'（已摘掉，不污染 13 号那一档）' if PARENT_REPO_MAP_DEFAULT else '（未设置）'}\n")

    client = Client(cmd, out_prefix)
    checks: list[tuple[str, bool, str]] = []

    # 1. 握手
    init = client.call("initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "code-tools-smoke", "version": "0"},
    })
    ok = bool(init and "result" in init)
    print(f"1) initialize                  -> {'OK' if ok else '失败'}")
    checks.append(("initialize", ok, "握手成功" if ok else str(init)))
    client.notify("notifications/initialized")

    # 2. tools/list —— 应有 5 个工具
    tl = client.call("tools/list")
    names = [t["name"] for t in (tl or {}).get("result", {}).get("tools", [])]
    expect_tools = {"list_files", "repo_map", "search_code", "read_file",
                    "apply_patch", "run_tests"}
    ok = expect_tools.issubset(set(names))
    print(f"2) tools/list                  -> {names}")
    checks.append(("tools/list 含全部 6 个工具", ok, ", ".join(names)))

    def call_tool(name: str, args: dict) -> str:
        msg = client.call("tools/call", {"name": name, "arguments": args})
        return tool_text(msg)

    # 3. list_files —— 应看到靶场文件
    text = call_tool("list_files", {"subdir": ".", "pattern": "*.py"})
    ok = "calc.py" in text and "stats.py" in text
    print(f"3) list_files('.')             -> {text[:80].replace(chr(10), ' | ')}")
    checks.append(("list_files 列出靶场文件", ok, text[:200]))

    # 4. search_code —— 应按内容定位
    text = call_tool("search_code", {"regex": "def median", "glob": "*.py"})
    ok = "stats.py" in text
    print(f"4) search_code('def median')   -> {text[:80].replace(chr(10), ' | ')}")
    checks.append(("search_code 定位到 stats.py", ok, text[:200]))

    # 5. read_file —— 应带行号
    text = call_tool("read_file", {"path": "calc.py", "start": 1, "end": 12})
    # 断言点：① 头部说明 ② 行号格式 `N| ` ③ 真的读到了内容（safe_divide 定义所在行）
    # ⚠️ 别断言具体行号：改动文件会让行号漂移，断言会变成假 FAIL。
    #    第一版就写成了 `"safe_divide" in text` + 只读 1-6 行 —— 断言错了，不是工具错了。
    ok = ("calc.py" in text) and bool(re.search(r"^\s*\d+\| ", text, re.M)) and ("safe_divide" in text)
    print(f"5) read_file('calc.py')        -> {text[:80].replace(chr(10), ' | ')}")
    checks.append(("read_file 带行号输出", ok, text[:200]))

    # 6. 沙箱：越界读
    text = call_tool("read_file", {"path": "../../../Windows/win.ini"})
    ok = "拒绝" in text
    print(f"6) 越界 read_file              -> {text[:70]}")
    checks.append(("越界读被拒绝", ok, text[:200]))

    # 7. 沙箱：越界写
    text = call_tool("apply_patch", {"path": "../outside.txt", "old": "a", "new": "b"})
    ok = "拒绝" in text
    print(f"7) 越界 apply_patch            -> {text[:70]}")
    checks.append(("越界写被拒绝", ok, text[:200]))

    # 8. 沙箱：绝对路径
    text = call_tool("read_file", {"path": "C:/Windows/win.ini"})
    ok = "拒绝" in text
    print(f"8) 绝对路径 read_file          -> {text[:70]}")
    checks.append(("绝对路径被拒绝", ok, text[:200]))

    # 9. 沙箱：run_tests 的 test_path 越界
    text = call_tool("run_tests", {"test_path": "../../x.py"})
    ok = "拒绝" in text
    print(f"9) run_tests 越界 test_path    -> {text[:70]}")
    checks.append(("run_tests 越界 test_path 被拒绝", ok, text[:200]))

    # 10. 沙箱：run_tests 的 keyword 注入
    text = call_tool("run_tests", {"keyword": "; rm -rf /"})
    ok = "拒绝" in text
    print(f"10) run_tests 注入 keyword     -> {text[:70]}")
    checks.append(("run_tests 非法 keyword 被拒绝", ok, text[:200]))

    # 11. apply_patch 歧义匹配（old 出现多次 -> 应拒绝）
    text = call_tool("apply_patch", {"path": "calc.py", "old": "def ", "new": "def  "})
    ok = "拒绝" in text and "出现" in text
    print(f"11) apply_patch 歧义 old       -> {text[:70]}")
    checks.append(("歧义 old 被拒绝", ok, text[:200]))

    # 12. 真实跑一次测试（会暴露 pytest 是否安装）
    text = call_tool("run_tests", {})
    ok = "退出码" in text
    print(f"12) run_tests()                -> {text[:100].replace(chr(10), ' | ')}")
    checks.append(("run_tests 返回退出码", ok, text[:300]))

    # 13. repo_map 摘要（默认）—— 应列出文件 + 行数 + 符号数，**不展开符号**
    text = call_tool("repo_map", {"subdir": ".", "pattern": "*.py"})
    ok = ("符号" in text) and ("calc.py" in text or "inventory/pricing.py" in text)
    print(f"13) repo_map(summary)          -> {len(text)} 字符 | {text[:70].replace(chr(10), ' | ')}")
    checks.append(("repo_map 摘要含文件与符号数", ok, text[:250]))

    # 13b. 展开符号
    text2 = call_tool("repo_map", {"subdir": ".", "pattern": "*.py", "detail": "symbols"})
    ok2 = "def " in text2
    print(f"13b) repo_map(symbols)         -> {len(text2)} 字符")
    checks.append(("repo_map(symbols) 含 def", ok2, text2[:250]))

    # 13c. 摘要必须比展开**明显便宜**（这是改设计的理由，要守住）
    ok3 = len(text) < len(text2)
    print(f"13c) 摘要 vs 展开              -> {len(text)} vs {len(text2)} 字符")
    checks.append(("摘要比展开便宜", ok3, f"{len(text)} vs {len(text2)}"))

    # ═══════════════════════════════════════════════════════════════════════
    # 13d~13h. 默认值切换对照（A/B 实验的开关）
    #
    # 上面 13/13b/13c 验的是"显式传 detail 有没有用"。但本次实验要动的是
    # **默认值**，而默认值由环境变量在**子进程启动时**决定 —— 所以这里必须
    # **另起两个进程**：一个 AGENT_REPO_MAP_DEFAULT=symbols，一个 =bogus。
    # 在同一进程里改 os.environ 是无效的（模块级常量只读一次）。
    # ═══════════════════════════════════════════════════════════════════════

    def spawn_client(tag: str, env_extra: dict[str, str]) -> Client:
        return Client(cmd, Path(f"{out_prefix}.{tag}"), env=env_extra)

    def ask(client: Client, name: str, args: dict) -> str:
        msg = client.call("tools/call", {"name": name, "arguments": args})
        return tool_text(msg)

    # 13d. AGENT_REPO_MAP_DEFAULT=symbols 时，**不传 detail** 应展开符号
    sym_client = spawn_client("arm-symbols", {REPO_MAP_ENV: ARM_SYMBOLS})
    sym_client.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "smoke-arm", "version": "0"}})
    sym_client.notify("notifications/initialized")
    sym_default = ask(sym_client, "repo_map", {"subdir": ".", "pattern": "*.py"})
    ok = "def " in sym_default and len(sym_default) > len(text)
    print(f"13d) 默认=symbols（不传 detail）-> {len(sym_default)} 字符，含 def = "
          f"{'def ' in sym_default}")
    checks.append(("AGENT_REPO_MAP_DEFAULT=symbols 使默认展开符号", ok,
                   f"{len(sym_default)} 字符；含 'def ' = {'def ' in sym_default}"))

    # 13e. 同一次调用里显式传 detail="summary" 必须**不受环境变量影响**
    #      —— 这是「开关只影响默认值」的核心断言，否则单变量设计被破坏
    sym_explicit = ask(sym_client, "repo_map", {"subdir": ".", "pattern": "*.py",
                                                "detail": "summary"})
    ok = "def " not in sym_explicit and len(sym_explicit) < 2000
    print(f"13e) 同一进程显式传 summary   -> {len(sym_explicit)} 字符，含 def = "
          f"{'def ' in sym_explicit}")
    checks.append(("环境变量不影响显式传 detail（只改默认值）", ok,
                   f"{len(sym_explicit)} 字符；含 'def ' = {'def ' not in sym_explicit}"))
    sym_client.close()

    # 13f. 非法值 -> 明确报错，**不静默回落**
    bad_client = spawn_client("arm-bogus", {REPO_MAP_ENV: "bogus"})
    bad_client.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "smoke-arm", "version": "0"}})
    bad_client.notify("notifications/initialized")
    bad_default = ask(bad_client, "repo_map", {"subdir": ".", "pattern": "*.py"})
    ok = bad_default.startswith("拒绝") and "bogus" in bad_default
    print(f"13f) 默认=bogus（非法）      -> {bad_default[:70]}")
    checks.append(("非法 AGENT_REPO_MAP_DEFAULT 被明确拒绝", ok, bad_default[:250]))

    # 13g. 非法值下显式传 detail 仍应正常工作（影响面只限默认值）
    bad_explicit = ask(bad_client, "repo_map", {"subdir": ".", "pattern": "*.py",
                                                "detail": "summary"})
    ok = "拒绝" not in bad_explicit and "符号" in bad_explicit
    print(f"13g) 非法默认值下显式传      -> {len(bad_explicit)} 字符，被拒 = "
          f"{'拒绝' in bad_explicit}")
    checks.append(("非法默认值不牵连显式传 detail 的调用", ok, bad_explicit[:200]))
    bad_client.close()

    # 13h. 显式 symbols 在真实大仓库上应被 20000 上限截断、并带缩小范围提示。
    #      ⚠️ 只有靶场足够大时才能观察到 —— 小靶场（mini_repo）本来就不到 20000，
    #      "没截断"是正确的，不能因此判 FAIL。所以这里按实际大小分支断言。
    big = text2
    if len(big) >= 20000:
        ok = "已截断" in big and "subdir" in big
        note = f"{len(big)} 字符，含截断提示 = {'已截断' in big}"
    else:
        ok = True
        note = f"本次靶场只有 {len(big)} 字符（<20000），不触发截断 —— 属预期"
    print(f"13h) 大输出截断提示          -> {note}")
    checks.append(("超长输出被截断且带缩小范围提示", ok, note))

    # 14. run_tests 的 detail 参数要校验
    text = call_tool("run_tests", {"detail": "bogus"})
    ok = "拒绝" in text
    print(f"14) run_tests 非法 detail      -> {text[:70]}")
    checks.append(("非法 detail 被拒绝", ok, text[:200]))

    # 15. brief 与 full 的输出大小应有可测差异
    brief = call_tool("run_tests", {"detail": "brief"})
    full = call_tool("run_tests", {"detail": "full"})
    ok = len(full) >= len(brief)
    print(f"15) brief vs full 字符数       -> brief={len(brief)} full={len(full)}")
    checks.append(("brief 不比 full 更长", ok, f"brief={len(brief)} full={len(full)}"))

    code = client.close()
    client.dump()

    print(f"\n=== 结论 ===")
    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(f"  {'[PASS]' if ok else '[FAIL]'} {name}")
    print(f"\n  {passed}/{len(checks)} 项通过；server 退出码 = {code}")
    print(f"  原始 stdout: {out_prefix}.stdout.raw")
    print(f"  stderr 日志: {out_prefix}.stderr.log")

    Path(f"{out_prefix}.result.json").write_text(
        json.dumps(
            {"server": cmd, "exit_code": code,
             "parent_repo_map_default": PARENT_REPO_MAP_DEFAULT,
             "checks": [{"name": n, "pass": o, "detail": d} for n, o, d in checks]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    # ⚠️ 退出码语义：**全部通过才 0**。
    #    早先的写法是"沙箱项过了就 0"，那会让 13d~13g（默认值开关）失败被判成"整体通过"
    #    —— 而这几项正是本次 A/B 实验的开关，它们的失败必须让冒烟整体失败。
    #    "部分通过"不是可以接受的中间态：判据一旦松，实验条件就说不清。
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
