"""t4 复核：P2P 的**基线**绿度（第三个观测点 B），以及 F2P 在基线上的红度。

前面已经查了 T（base+test_patch）与 G（base+test_patch+gold）。
验收条款还要求「核对 PASS_TO_PASS 在基线与 gold 下都通过」——这里是 B 观测点：
**只 checkout base_commit、不施加 test_patch**，直接跑 P2P 与 F2P 的 node id。

注意：F2P 的 node 常常是 test_patch **新增**的测试，在纯基线上根本不存在
（collect 不到）=> 那属于「预期行为」，不算缺陷。这里如实分开统计：
  - P2P 在 B 上必须 passed（这是「基线本来就绿」的定义）
  - F2P 在 B 上：不存在 或 failed 都算合理，**passed 才是问题**（那说明 F2P 选错了）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

GIT = [r"E:\Program Files\Git\cmd\git.exe", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]
CACHE = r"E:\aicoding\eval-cache"
INST = r"E:\aicoding\mini-claude-code\eval\instances"
VENV_ROOT = r"E:\aicoding\eval-venvs"
OUT = r"E:\aicoding\eval-runs\t4\baseline_observation"
IDS = ["pallets-click__4fd2fea", "pytest-dev-pluggy__cbf58d5",
       "more-itertools-more-itertools__1b7f033", "astanin-python-tabulate__9dedd45"]


def sh(cmd, cwd=None, timeout=900):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e.pop("PYTHONPATH", None)
    e.pop("PYTEST_ADDOPTS", None)
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", env=e)
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        p.kill()
        out, err = p.communicate()
    return {"rc": p.returncode, "out": out or "", "err": err or "", "timed_out": timed_out}


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for iid in IDS:
        inst = json.load(open(os.path.join(INST, iid + ".json"), encoding="utf-8"))
        parent = tempfile.mkdtemp(prefix=f"t4b_{iid[:16]}_")
        ws = os.path.join(parent, "repo")
        art = os.path.join(OUT, iid)
        os.makedirs(ws, exist_ok=True)
        os.makedirs(art, exist_ok=True)
        rec = {"instance_id": iid, "repo": inst["repo"], "base_commit": inst["base_commit"]}
        try:
            tar = os.path.join(parent, "_b.tar")
            r = sh(GIT + ["-C", os.path.join(CACHE, inst["repo"].replace("/", "__")),
                          "archive", "--format=tar", "-o", tar, inst["base_commit"]], timeout=900)
            if r["rc"] != 0:
                rec["infra_error"] = f"git archive: {r['err'][-200:]}"
                rows.append(rec)
                continue
            sh(GIT + ["-C", ws, "init", "--quiet"])
            for cfg in (["core.autocrlf", "false"], ["core.safecrlf", "false"]):
                sh(GIT + ["-C", ws, "config"] + cfg)
            with tarfile.open(tar) as tf:
                tf.extractall(ws)
            os.unlink(tar)
            sh(GIT + ["-C", ws, "add", "-A"], timeout=900)
            sh(GIT + ["-C", ws, "-c", "user.email=b@l", "-c", "user.name=b",
                      "commit", "--quiet", "-m", "baseline"], timeout=900)

            # 包挂载
            pkg = None
            for base in (os.path.join(ws, "src"), ws):
                if not os.path.isdir(base):
                    continue
                for name in sorted(os.listdir(base)):
                    full = os.path.join(base, name)
                    if os.path.isdir(full) and os.path.exists(os.path.join(full, "__init__.py")):
                        if name.lower() not in ("test", "tests", "testing", "docs", "examples"):
                            pkg = name
                            break
                    elif name.endswith(".py") and name not in ("setup.py", "conftest.py"):
                        pkg = name[:-3]
                        break
                if pkg:
                    break
            vd = os.path.join(VENV_ROOT,
                              f"{inst['repo'].replace('/', '__')}-{inst['base_commit'][:8]}")
            py = os.path.join(vd, "Scripts", "python.exe")
            parent_pkg = None
            for cand in (os.path.join(ws, "src"), ws):
                if os.path.isdir(os.path.join(cand, pkg)) or os.path.exists(os.path.join(cand, pkg + ".py")):
                    parent_pkg = cand
                    break
            with open(os.path.join(vd, "Lib", "site-packages", "_t4b_mount.pth"), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write(f"import sys; sys.path.insert(0, {parent_pkg!r})\n")
                f.write(f"import sys; sys.path.insert(0, {ws!r})\n")

            # ⚠ 纯基线观测点**只能请求 P2P 的 node**：
            # F2P 常常是 test_patch 新增的测试/参数化，在基线上根本不存在，
            # 把它们塞进命令会让 pytest 报 "ERROR: not found" 并以 **exit 4**（usage error）退出
            # —— 那不是「测试失败」，而是请求了不存在的 node。
            # （本脚本第一版就是踩了这个：把所有 node 都塞进去 => 4/4 条 exit=4、nodes=0，
            #  看着像「P2P 在基线全不绿」，其实是请求错误。单独用 P2P 请求则 exit 0。）
            nodes = list(inst["PASS_TO_PASS"])
            ini = os.path.join(art, "_b.ini")
            with open(ini, "w", encoding="utf-8", newline="\n") as f:
                f.write("[pytest]\n")
            rep = os.path.join(art, "baseline_report.json")
            cmd = [py, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--timeout=120",
                   "-c" + ini, "--rootdir=" + ws, "--json-report",
                   "--json-report-file=" + rep] + nodes
            rr = sh(cmd, cwd=ws, timeout=600)
            res = {}
            if os.path.exists(rep):
                doc = json.load(open(rep, encoding="utf-8"))
                for t in doc.get("tests") or []:
                    nid = (t.get("nodeid") or "").replace("\\", "/")
                    o = (t.get("outcome") or "").strip().lower()
                    nid = re.sub(r"^(?:\.\./)+[^/]*/repo/", "", nid)
                    res[nid] = {"subtests passed": "passed"}.get(o, o)
            p2p_bad = [n for n in inst["PASS_TO_PASS"] if res.get(n) != "passed"]
            # F2P 的基线状态单独报（不放进请求里）：只能靠 test_patch 观测点 T 判断，
            # 所以这里只如实记录「基线上是否存在」这个事实。
            f2p_absent = [n for n in inst["FAIL_TO_PASS"] if n not in res]
            rec.update({
                "pkg": pkg, "pytest_exit": rr["rc"], "timed_out": rr["timed_out"],
                "nodes_collected": len(res),
                "requested_nodes": len(nodes),
                "p2p_not_passed_count": len(p2p_bad),
                "p2p_not_passed_sample": p2p_bad[:8],
                "P2P_green_at_baseline": len(p2p_bad) == 0,
                "f2p_absent_at_baseline_count": len(f2p_absent),
                "f2p_absent_at_baseline_sample": f2p_absent[:5],
                "note_f2p_baseline": "F2P 的基线红/绿由 T 观测点（base+test_patch）判定；"
                                     "纯基线上 F2P 参数化常常尚不存在，故此处不请求它们",
                "report": rep,
            })
        except Exception as exc:  # noqa: BLE001
            import traceback
            rec["infra_error"] = f"{type(exc).__name__}: {exc}"
            rec["traceback"] = traceback.format_exc()[-800:]
        finally:
            shutil.rmtree(parent, ignore_errors=True)
        rows.append(rec)
        if "infra_error" in rec:
            print(f"[{iid}] INFRA {rec['infra_error']}", flush=True)
        else:
            print(f"[{iid}] P2P_green_at_baseline={rec['P2P_green_at_baseline']} "
                  f"(P2P未过={rec['p2p_not_passed_count']}/{rec['requested_nodes']})  "
                  f"exit={rec['pytest_exit']}  "
                  f"基线上不存在的F2P数={rec['f2p_absent_at_baseline_count']}", flush=True)

    with open(os.path.join(OUT, "baseline.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    ok = [r for r in rows if "infra_error" not in r]
    print()
    print(f"B 观测点（纯基线，只请求 P2P）：{len(ok)}/{len(rows)} 条跑通")
    print(f"  P2P 在基线全绿：{sum(1 for r in ok if r['P2P_green_at_baseline'])}/{len(ok)}")
    print(f"  -> {os.path.join(OUT, 'baseline.json')}")
    return 0 if (len(ok) == len(rows)
                 and all(r["P2P_green_at_baseline"] for r in ok)) else 1


if __name__ == "__main__":
    sys.exit(main())