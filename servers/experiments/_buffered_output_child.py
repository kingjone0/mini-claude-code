"""对照组子进程：t=0 打一行，睡 6 秒，t=6 再打一行。

两个 print 都带 flush=True —— 这是刻意的单变量设计。

如果子进程**不** flush，Python 往管道写时默认是块缓冲，
那「管道里 0 字节」就分不清是 conda 缓冲的还是 Python 自己缓冲的。
两个都用 flush，剩下的差异才只能归因到**启动方式**。
"""

import sys
import time

print("LINE-ONE-AT-T0", flush=True)
time.sleep(6)
print("LINE-TWO-AT-T6", flush=True)
sys.stderr.write("child: 两行都打完了\n")
