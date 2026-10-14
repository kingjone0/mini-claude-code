"""t4 独立复核：红/绿重跑（**完全不使用 eval/harness 与 eval/lib**）。

为了满足「不采信 t2/t3 自述」，这里自己实现一遍最小链路：

   1) 从 eval-cache 缓存仓库 `git archive base_commit | tar` 到临时目录（自建工作区）
   2) 复用**已存在的 venv**（E:\\aicoding\\eval-venvs\\<repo_key>-<base[:8]>），
      只做 editable 挂载（import 行式 .pth），不用 harness 的 ensure_venv
   3) 施加 test_patch -> 跑 F2P/P2P -> 记录逐 node 结果（T 观测点，期望 F2P 红）
   4) 再叠加 gold patch -> 再跑 -> 记录（G 观测点，期望 F2P 绿且 P2P 绿）
   5) 判定完全按预注册 §5.1：F2P 全 passed ∧ P2P 全 passed => resolved

与 harness 的唯一共同点是**外部契约**（目录约定、node id 形式、判据），
代码路径独立实现，因此可以拿来做交叉核对。

用法：
    python t4_independent_redgreen.py <ids.json> <outdir> [--py <venv python 覆盖>]
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
VENV_ROOT = r"E:\aicoding\eval-venvs"
INST = r"E:\aicoding\mini-claude-code\eval\instances"


def sh(cmd, cwd=None, timeout=900, env=None):
    t0 = time.time()
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e.pop("PYTHONPATH", None)
    e.pop("PYTEST_ADDOPTS", None)
    if env:
        e.update(env)
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", env=e)
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        p.kill()
        out, err = p.communicate()
    return {"cmd": cmd, "rc": p.returncode, "out": out or "", "err": err or "",
            "timed_out": timed_out, "wall": round(time.time() - t0, 2)}


def repo_dir(repo):
    return os.path.join(CACHE, repo.replace("/", "__"))


def make_ws(inst, parent):
    """git archive base_commit -> tar -> 解包 -> git init/commit。自建，不用 env.py。"""
    ws = os.path.join(parent, "repo")
    os.makedirs(ws, exist_ok=True)
    tar = os.path.join(parent, "_base.tar")
    r = sh(GIT + ["-C", repo_dir(inst["repo"]), "archive", "--format=tar", "-o", tar,
                  inst["base_commit"]], timeout=900)
    if r["rc"] != 0:
        raise RuntimeError(f"git archive 失败: {r['err'][-300:]}")
    r = sh(GIT + ["-C", ws, "init", "--quiet"])
    if r["rc"] != 0:
        raise RuntimeError("git init 失败")
    for cfg in (["user.email", "t4@local"], ["user.name", "t4-verifier"],
                ["core.autocrlf", "false"], ["core.safecrlf", "false"],
                ["commit.gpgsign", "false"]):
        sh(GIT + ["-C", ws, "config"] + cfg)
    with tarfile.open(tar) as tf:
        tf.extractall(ws)
    os.unlink(tar)
    sh(GIT + ["-C", ws, "add", "-A"], timeout=900)
    r = sh(GIT + ["-C", ws, "-c", "user.email=t4@local", "-c", "user.name=t4-verifier",
                  "commit", "--quiet", "-m", "baseline"], timeout=900)
    if r["rc"] != 0:
        raise RuntimeError(f"基线 commit 失败: {r['err'][-300:]}")
    return ws


def infer_pkg(ws):
    for base in (os.path.join(ws, "src"), ws):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            full = os.path.join(base, name)
            if os.path.isdir(full) and os.path.exists(os.path.join(full, "__init__.py")):
                if name.lower() not in ("test", "tests", "testing", "docs", "doc",
                                        "examples", "benchmarks", "scripts"):
                    return name
            if name.endswith(".py") and name not in ("setup.py", "conftest.py"):
                return name[:-3]
    return None


def venv_py(inst):
    tag = f"{inst['repo'].replace('/', '__')}-{inst['base_commit'][:8]}"
    return os.path.join(VENV_ROOT, tag, "Scripts", "python.exe")


def mount_pkg(py, ws, pkg, venv_dirpath):
    """自建 editable：import 行式 .pth，把包父目录插到 sys.path 最前。"""
    parent = None
    for cand in (os.path.join(ws, "src"), ws):
        if os.path.isdir(os.path.join(cand, pkg)) or os.path.exists(os.path.join(cand, pkg + ".py")):
            parent = cand
            break
    if parent is None:
        raise RuntimeError(f"找不到包 {pkg} 的父目录")
    sp = os.path.join(venv_dirpath, "Lib", "site-packages")
    pth = os.path.join(sp, "_t4_mount.pth")
    with open(pth, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"import sys; sys.path.insert(0, {parent!r})\n")
        f.write(f"import sys; sys.path.insert(0, {ws!r})\n")
    probe = sh([py, "-c",
                "import os,sys,importlib;"
                f"m=importlib.import_module({pkg!r});"
                "p=os.path.realpath(getattr(m,'__file__','') or '');"
                f"root=os.path.realpath({ws!r});"
                "sys.stdout.write(p);"
                "raise SystemExit(0 if p.startswith(root+os.sep) else 1)"],
               cwd=os.path.expanduser("~"))
    return {"pth": pth, "parent": parent, "probe_rc": probe["rc"],
            "probe_out": probe["out"].strip()}


def apply_patch(ws, text, tag, tmpdir, reverse=False, extra=None):
    """用 git apply（自建）。extra 用来做「先叠 gold 再反向」。"""
    if not (text or "").strip():
        return {"rc": 0, "skip": "empty"}
    path = os.path.join(tmpdir, f"_{tag}.diff")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    cmd = GIT + ["-C", ws, "apply", "--whitespace=nowarn"]
    if reverse:
        cmd.append("-R")
    cmd.append(path.replace("\\", "/"))
    return sh(cmd, cwd=ws, timeout=300)


def run_pytest(ws, py, art, node_ids, timeout=600, tag="run"):
    """自建 pytest 调用：临时 ini + rootdir + json-report。"""
    ini = os.path.join(art, f"_t4_{tag}.ini")
    with open(ini, "w", encoding="utf-8", newline="\n") as f:
        f.write("[pytest]\n")
    rep = os.path.join(art, f"_t4_{tag}_report.json")
    cmd = [py, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--timeout=120",
           "-c" + ini, "--rootdir=" + ws, "--json-report", "--json-report-file=" + rep]
    cmd += list(node_ids)
    r = sh(cmd, cwd=ws, timeout=timeout)
    r["json_report"] = rep
    res = {}
    if os.path.exists(rep):
        doc = json.load(open(rep, encoding="utf-8"))
        for t in doc.get("tests") or []:
            nid = (t.get("nodeid") or "").replace("\\", "/")
            out = (t.get("outcome") or "").strip().lower()
            out = {"subtests passed": "passed"}.get(out, out)
            # 归一 node id 到工作区相对
            if os.path.isabs(nid):
                nid = os.path.relpath(nid, ws).replace("\\", "/")
            nid = re.sub(r"^(?:\.\./)+[^/]*/repo/", "", nid)
            res[nid] = out
    r["results"] = res
    return r


def judge_(f2p, p2p, results):
    bad_f = [n for n in f2p if results.get(n) != "passed"]
    bad_p = [n for n in p2p if results.get(n) != "passed"]
    return {"resolved": not bad_f and not bad_p,
            "f2p_total": len(f2p), "f2p_not_passed": bad_f,
            "p2p_total": len(p2p), "p2p_not_passed": bad_p,
            "f2p_missing": [n for n in f2p if n not in results],
            "p2p_missing": [n for n in p2p if n not in results]}


def main():
    ids = json.load(open(sys.argv[1], encoding="utf-8"))
    outdir = sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    only_id = None
    if len(sys.argv) > 3 and sys.argv[3] != "-":
        only_id = sys.argv[3]
    report = []
    for iid in ids:
        if only_id and iid != only_id:
            continue
        inst = json.load(open(os.path.join(INST, iid + ".json"), encoding="utf-8"))
        art = os.path.join(outdir, iid)
        os.makedirs(art, exist_ok=True)
        rec = {"instance_id": iid, "repo": inst["repo"], "base_commit": inst["base_commit"],
               "f2p": inst["FAIL_TO_PASS"], "p2p_count": len(inst["PASS_TO_PASS"]),
               "p2p": inst["PASS_TO_PASS"]}
        parent = tempfile.mkdtemp(prefix=f"t4_{iid[:18]}_")
        tmpdir = os.path.join(parent, "tmp")
        os.makedirs(tmpdir, exist_ok=True)
        try:
            ws = make_ws(inst, parent)
            py = venv_py(inst)
            if not os.path.exists(py):
                rec["infra_error"] = f"venv 不存在: {py}"
                report.append(rec)
                continue
            pkg = infer_pkg(ws)
            rec["pkg"] = pkg
            rec["mount"] = mount_pkg(py, ws, pkg, os.path.dirname(os.path.dirname(py)))
            if rec["mount"]["probe_rc"] != 0:
                rec["infra_error"] = "editable 自检失败"
                report.append(rec)
                continue

            # --- T 观测点：base + test_patch
            a = apply_patch(ws, inst["test_patch"], "testpatch", tmpdir)
            rec["test_patch_apply"] = {"rc": a.get("rc"), "err": (a.get("err") or "")[-400:]}
            if a.get("rc") != 0:
                rec["infra_error"] = "test_patch 应用失败"
                report.append(rec)
                continue
            nodes = list(inst["FAIL_TO_PASS"]) + list(inst["PASS_TO_PASS"])
            rt = run_pytest(ws, py, art, nodes, tag="T")
            # 判 T 观测点：F2P 应当红
            t_res = rt["results"]
            t_p2p_bad = [n for n in inst["PASS_TO_PASS"] if t_res.get(n) != "passed"]
            rec["T"] = {
                "pytest_exit": rt["rc"], "timed_out": rt["timed_out"], "wall": rt["wall"],
                "f2p_outcomes": {n: t_res.get(n, "<missing>") for n in inst["FAIL_TO_PASS"]},
                "f2p_all_red_or_missing": all(t_res.get(n) != "passed"
                                              for n in inst["FAIL_TO_PASS"]),
                "p2p_not_passed_count": len(t_p2p_bad),
                "p2p_not_passed_sample": t_p2p_bad[:10],
                "nodes_collected": len(t_res),
                "report": rt["json_report"],
            }

            # --- G 观测点：base + test_patch + gold
            g = apply_patch(ws, inst["patch"], "gold", tmpdir)
            rec["gold_apply"] = {"rc": g.get("rc"), "err": (g.get("err") or "")[-400:]}
            if g.get("rc") != 0:
                rec["infra_error"] = "gold 应用失败"
                report.append(rec)
                continue
            rg = run_pytest(ws, py, art, nodes, tag="G")
            jg = judge_(inst["FAIL_TO_PASS"], inst["PASS_TO_PASS"], rg["results"])
            rec["G"] = {
                "pytest_exit": rg["rc"], "timed_out": rg["timed_out"], "wall": rg["wall"],
                "judged": jg,
                "f2p_outcomes": {n: rg["results"].get(n, "<missing>")
                                 for n in inst["FAIL_TO_PASS"]},
                "p2p_not_passed_count": len(jg["p2p_not_passed"]),
                "p2p_not_passed_sample": jg["p2p_not_passed"][:10],
                "f2p_not_passed_detail": {n: rg["results"].get(n, "<missing>")
                                          for n in jg["f2p_not_passed"]},
                "nodes_collected": len(rg["results"]),
                "report": rg["json_report"],
            }
            rec["verdict"] = {
                "T_red": rec["T"]["f2p_all_red_or_missing"],
                "G_resolved": jg["resolved"],
                "expected_both": rec["T"]["f2p_all_red_or_missing"] and jg["resolved"],
            }
        except Exception as exc:  # noqa: BLE001
            import traceback
            rec["infra_error"] = f"{type(exc).__name__}: {exc}"
            rec["traceback"] = traceback.format_exc()[-1500:]
        finally:
            shutil.rmtree(parent, ignore_errors=True)
        report.append(rec)
        v = rec.get("verdict")
        print(f"[{iid}] " + (f"T_red={v['T_red']} G_resolved={v['G_resolved']} "
                             f"OK={v['expected_both']}  "
                             f"T.exc={rec['T']['pytest_exit']} G.exc={rec['G']['pytest_exit']}  "
                             f"T.p2p_fail={rec['T']['p2p_not_passed_count']} "
                             f"G.p2p_fail={rec['G']['p2p_not_passed_count']}"
                             if v else f"INFRA: {rec.get('infra_error')}"), flush=True)

    with open(os.path.join(outdir, "redgreen.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nJSON -> {os.path.join(outdir, 'redgreen.json')}")


if __name__ == "__main__":
    main()