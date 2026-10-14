"""§5.7 接线自检：在第 1 条实例上跑 A→B→C 各一次（标 wiring_probe）。

**目的**：证明 §1.3 W1–W4 在真机上成立，然后才允许发布任何臂间数字。
这三次运行**计入 §7.2 的 ≤39 次上限**，不得当作正式数据用（预注册 §5.7 S1）。

用法：
    python eval/harness/wiring_probe.py --run-tag probe1 [--instance <iid>]

产物：`E:\\aicoding\\eval-runs\\p1-<UTC>-wiring-<tag>/`
退出码：0 = 全过；2 = 任一 W 不过（停批信号）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.dirname(HERE)
PROJECT_ROOT = os.path.dirname(EVAL_DIR)
for p in (PROJECT_ROOT, EVAL_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.harness import arms as A          # noqa: E402
from eval.harness import env as E           # noqa: E402
from eval.harness import scheduler_p1 as SP # noqa: E402
from eval.harness import arm_check as AC    # noqa: E402
from eval.harness import verdict_gate as VG # noqa: E402

DEFAULT_INSTANCE = "astanin-python-tabulate__5373ebf"   # §3.3 第 1 条


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="P1 §5.7 接线自检")
    ap.add_argument("--run-tag", default="wiring", help="批次标签后缀")
    ap.add_argument("--instance", default=DEFAULT_INSTANCE, help="自检用的实例 id")
    ap.add_argument("--stamp", default=None, help="自定义 UTC 时间戳")
    args = ap.parse_args(argv)

    stamp = args.stamp or time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    tag = f"wiring-{args.run_tag}"
    iid = args.instance

    print("=" * 78)
    print(f"P1 §5.7 接线自检  |  instance={iid}  tag={tag}")
    print("=" * 78)

    # 1) 跑三臂（串行即可，自检不要求并发）
    manifest = SP.run_p1_batch(run_tag=tag, stamp=stamp, instance_ids=[iid],
                               arms_list=list(A.ARM_ORDER), max_workers=1, resume=False)
    run_root = E.run_root(manifest["run_id"])

    # 2) 合并三臂的 arm_report.jsonl 到一份供 arm_check 读
    merged = os.path.join(run_root, "arm_report_merged.jsonl")
    with open(merged, "w", encoding="utf-8", newline="\n") as out:
        for arm in A.ARM_ORDER:
            art = E.art_dir(A.make_run_id(iid, arm, stamp=stamp), iid)
            src = os.path.join(art, "arm_report.jsonl")
            if os.path.exists(src):
                with open(src, encoding="utf-8") as f:
                    for line in f:
                        out.write(line)
            else:
                print(f"[警告] 缺 {arm} 的 arm_report.jsonl：{src}", file=sys.stderr)

    # 3) 计算 K_expected
    from eval.harness import run_instance as RI
    inst = RI.load_instance(iid)
    kexp = A.k_expected_from_instance(inst)["k_expected"]

    # 4) 先做独立判定产物门禁，再跑臂接线检查。门禁失败必须阻断自检。
    state_path = os.path.join(run_root, "triple_state.jsonl")
    gate_rc = VG.main(["--state", state_path])
    if gate_rc != 0:
        print("[FAIL] 判定产物门禁未通过，禁止放行 wiring_probe", file=sys.stderr)
        return gate_rc
    # 5) 跑 arm_check
    rc = AC.main(["--arm-report", merged, "--instance-id", iid, "--expected-k", str(kexp)])
    result_path = os.path.join(os.path.dirname(merged), "arm_check_result.json")
    print("\n" + "=" * 78)
    if rc == 0:
        print(f"[PASS] §5.7 接线自检通过  |  结果：{result_path}")
        print("后续正式跑批可从第 2 条实例开始；第 1 条实例的三次运行已标 wiring_probe，")
        print("按 §5.7 S1 处理：要么重跑一次计入 ≤39 次上限，要么如实报告为「只有自检数据」。")
    else:
        print(f"[FAIL] §5.7 接线自检未通过  |  详情：{result_path}")
        print("⇒ 停批修接线，这三次运行作废（仍计入成本，如实报告）。")
    print("=" * 78)
    return rc


if __name__ == "__main__":
    sys.exit(main())
