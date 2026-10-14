"""子进程：读一行 stdin 并回显。

用来定论一件事 —— conda run 默认模式下，**子进程到底收不收得到 stdin**。

MCP 场景里有个对不上的观察：探针明明把 stdin 保持打开、也发了 initialize，
但 read 端很快就 EOF 了，且 stdout 是 0 字节。如果 stdin 根本没被转发给子进程，
那子进程会立刻读到 EOF 然后正常退出（退出码 0，不写任何 stdout）——
「stdout 0 字节 + stderr 有启动横幅 + 退出码 0」这三件事就同时解释得通了。
"""

import sys

sys.stderr.write("child: 准备读 stdin\n")
sys.stderr.flush()

line = sys.stdin.readline()

sys.stderr.write(f"child: readline 返回 {line!r}\n")
sys.stderr.flush()
print(f"GOT-LINE: {line.rstrip()}", flush=True)
