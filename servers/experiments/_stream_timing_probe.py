"""计时探针：区分「输出被丢掉」和「输出被缓冲到退出」。

它在子进程**还在运行**的时候去看管道里有没有东西 —— 这是关键。
普通的 `cmd | cat` 只能看到最终结果，分不出这两种情况。

用法：
    python _stream_timing_probe.py <标签> -- <启动命令...>
"""

import subprocess
import sys
import threading
import time

CHECK_AT = 3.0  # 在子进程跑完之前查看（子进程总共约 6 秒）


def main() -> int:
    argv = sys.argv[1:]
    if "--" not in argv:
        print(__doc__, file=sys.stderr)
        return 2
    split = argv.index("--")
    label = argv[:split][0] if argv[:split] else "probe"
    cmd = argv[split + 1:]

    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

    chunks: list[tuple[str, float, bytes]] = []
    lock = threading.Lock()

    def pump(stream, name: str) -> None:
        for line in stream:
            with lock:
                chunks.append((name, time.monotonic() - t0, line))

    threading.Thread(target=pump, args=(proc.stdout, "OUT"), daemon=True).start()
    threading.Thread(target=pump, args=(proc.stderr, "ERR"), daemon=True).start()

    def out_bytes() -> int:
        with lock:
            return sum(len(c[2]) for c in chunks if c[0] == "OUT")

    time.sleep(CHECK_AT)
    during = out_bytes()
    print(f"[{label}] 运行中 t={CHECK_AT:.0f}s：stdout 已收到 {during} 字节")

    proc.wait()
    time.sleep(0.4)
    after = out_bytes()
    print(f"[{label}] 进程退出后：stdout 共收到 {after} 字节")

    with lock:
        for name, ts, line in chunks:
            print(f"[{label}]   {name} @ t={ts:5.2f}s  {line!r}")

    verdict = (
        "输出被『缓冲到退出』—— 运行中拿不到，退出时一次性到达"
        if during == 0 and after > 0
        else "输出运行中就可见" if during > 0
        else "整个生命周期都没有输出"
    )
    print(f"[{label}] 判定：{verdict}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
