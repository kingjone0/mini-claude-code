"""t9：D7 机理钉死 + t5 自述核对。

① `E.git()` 用 `subprocess(..., text=True)` 读子进程输出 = **universal newlines**，
   会把 git diff 正文里的 CRLF 折成 LF。这一步发生在 harness 自己的读法里，
   不是 git diff 的行为。用同一次 diff 的两种读法对照即可钉死。
② agent 在 t5 里的自述判定（从 console.log 里取），与 harness 判定对照。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
GIT = [r"E:\Program Files\Git\cmd\git.exe", "-c", "core.autocrlf=false",
       "-c", "core.safecrlf=false"]
WS = r"E:\aicoding\eval-runs\t9-d7\t5copy"

print("=== ① D7 机理：同一次 git diff，两种读法 ===")
r = subprocess.run(GIT + ["-C", WS, "status", "--porcelain"], capture_output=True,
                   text=True, encoding="utf-8")
for line in r.stdout.splitlines():
    if not line.strip():
        continue
    p = line[3:].strip().strip('"')
    full = os.path.join(WS, *p.split("/"))
    if not os.path.isfile(full):
        continue
    d = open(full, "rb").read()
    if d.count(b"\n") == d.count(b"\r\n"):
        continue
    open(full, "wb").write(d.replace(b"\n", b"\r\n"))
    print("  注入 CRLF:", p)
b = subprocess.run(GIT + ["-C", WS, "diff", "--binary"], capture_output=True)
t = subprocess.run(GIT + ["-C", WS, "diff", "--binary"], capture_output=True,
                   text=True, encoding="utf-8")
tb = t.stdout.encode("utf-8")
print(f"  字节模式（no text=True）        : {len(b.stdout)} 字节, CRLF={b.stdout.count(chr(13).encode()+chr(10).encode())}")
print(f"  文本模式（text=True，即 E.git 的读法）: {len(tb)} 字节, CRLF={tb.count(chr(13).encode()+chr(10).encode())}")
print("  ⇒ git diff 本身**没有**归一化行尾；是 harness 的 text=True 读法把 CRLF 折成 LF。")
print("     （所以 t8 的结论「真因在工作树」成立，但「git diff 自己会归一化」这个机理说法不成立）")

print("\n=== ② t5 agent 自己的判定 vs harness 判定 ===")
cl = open(r"E:\aicoding\eval-runs\t5-smoke\console.log", encoding="utf-8",
          errors="replace").read()
hits = re.findall(r".{0,60}判定.{0,140}", cl)
for h in hits[-6:]:
    print("  ", h.replace("\n", " | ")[:220])
print("  --- 末尾 4 行 ---")
for l in cl.splitlines()[-4:]:
    print("  ", l[:200])
