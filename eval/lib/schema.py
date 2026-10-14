"""实例 schema（预注册 §1）的声明式定义与校验。

`validate()` 是 `--verify-all` 的核心：必填字段 + 类型 + §1.4 六条自检 +
证据/工件文件存在 + 规模规则（§7.1）。任何一条不过 ⇒ 调用方非零退出。
"""

from __future__ import annotations

import hashlib
import os
import re

VERSION = "p0-1.0"
MEMBERSHIP = ("pilot-12", "reserve", "rejected")
SELF_CHECK_KEYS = ("gold", "empty", "wrong", "evidence_paths", "checked_at")
REQUIRED_DATA_PATHS = ("patch_file", "test_patch_file", "wrong_patch_file", "build_log_dir")
REQUIRED_EVIDENCE = (
    "fix_commit_show", "B1_baseline", "T1_test_only", "G1_test_plus_gold",
    "selfcheck_gold", "selfcheck_empty", "selfcheck_wrong", "venv_log",
)

# 字段名 -> 允许的类型元组；第三项是否必填
FIELDS = {
    "instance_id": ((str,), True),
    "repo": ((str,), True),
    "base_commit": ((str,), True),
    "problem_statement": ((str,), True),
    "patch": ((str,), True),
    "test_patch": ((str,), True),
    "FAIL_TO_PASS": ((list,), True),
    "PASS_TO_PASS": ((list,), True),
    "version": ((str,), True),
    "env_spec": ((dict,), True),
    "test_cmd_template": ((str,), True),
    "collection_scope": ((list,), True),
    "flaky_known": ((bool,), True),
    "flaky_evidence": ((type(None), dict), True),
    "source_repo_url": ((str,), True),
    "fix_commit": ((str,), True),
    "fix_commit_date": ((str,), True),
    "construction_method": ((str,), True),
    "gold_selfcheck": ((dict,), True),
    "wrong_patch": ((str,), True),
    "excluded_tests": ((list,), True),
    "set_membership": ((str,), True),
    "sample_rationale": ((str,), True),
    "known_limits": ((list,), True),
    "data_paths": ((dict,), True),
    "schema_sha256": ((str,), True),
    "evidence": ((dict,), True),
}

_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+__[0-9a-f]{7,40}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _sha_without(obj, normalize_fn):
    clone = {k: v for k, v in obj.items() if k != "schema_sha256"}
    return hashlib.sha256(normalize_fn(clone).encode("utf-8")).hexdigest()


def missing_fields(obj):
    errs = []
    for name, (types, required) in FIELDS.items():
        if name not in obj:
            if required:
                errs.append(f"缺必填字段 {name}")
            continue
        if not isinstance(obj[name], types):
            errs.append(f"{name} 类型应为 {types}，实为 {type(obj[name]).__name__}")
    return errs


def validate(obj, normalize_fn, artifacts_root):
    """返回错误列表（空 = 通过）。"""
    errs = missing_fields(obj)
    if obj.get("version") != VERSION:
        errs.append(f'version 必须为 "{VERSION}"，实为 {obj.get("version")!r}')
    iid = obj.get("instance_id") or ""
    if not _INSTANCE_ID_RE.match(iid):
        errs.append(f"instance_id 格式不符 <owner>-<name>__<7+位小写hex>：{iid!r}")
    for key in ("base_commit", "fix_commit"):
        v = obj.get(key) or ""
        if not _SHA_RE.match(v):
            errs.append(f"{key} 必须是 40 位小写十六进制完整 SHA：{v!r}")
    for key in ("patch", "test_patch", "wrong_patch"):
        if not str(obj.get(key) or "").strip():
            errs.append(f"{key} 为空（E3）")
    if not str(obj.get("problem_statement") or "").strip():
        errs.append("problem_statement 为空（§3.4）")
    f2p = list(obj.get("FAIL_TO_PASS") or [])
    p2p = list(obj.get("PASS_TO_PASS") or [])
    if not f2p:
        errs.append("FAIL_TO_PASS 为空集（E5）")
    if len(set(f2p)) != len(f2p):
        errs.append("FAIL_TO_PASS 含重复 node id")
    if len(set(p2p)) != len(p2p):
        errs.append("PASS_TO_PASS 含重复 node id")
    inter = set(f2p) & set(p2p)
    if inter:
        errs.append(f"§1.4.3 FAIL_TO_PASS ∩ PASS_TO_PASS 非空：{sorted(inter)[:3]}")
    # patch / test_patch 文件集合不相交（§1.4.2）
    pf = set(re.findall(r"^\+\+\+ b/(\S+)", str(obj.get("patch") or ""), flags=re.M))
    tf = set(re.findall(r"^\+\+\+ b/(\S+)", str(obj.get("test_patch") or ""), flags=re.M))
    if pf & tf:
        errs.append(f"§1.4.2 patch 与 test_patch 文件集合相交：{sorted(pf & tf)[:3]}")
    if not tf:
        errs.append("test_patch 未解析出任何目标文件路径")
    if not pf:
        errs.append("patch 未解析出任何目标文件路径")
    if obj.get("construction_method") != "revert_of_real_fix_commit":
        errs.append("construction_method 必须是 revert_of_real_fix_commit（§1.3）")
    if obj.get("set_membership") not in MEMBERSHIP:
        errs.append(f"set_membership 非法：{obj.get('set_membership')!r}")
    if obj.get("set_membership") in ("pilot-12", "reserve") and not str(obj.get("sample_rationale") or "").strip():
        errs.append("入选/reserve 但 sample_rationale 为空（§7.3 要求机械理由）")
    env = obj.get("env_spec") or {}
    for k in ("python", "python_version_constraint", "install_cmd", "venv_root", "requirements_source"):
        if k not in env:
            errs.append(f"env_spec 缺 {k}（§1.3）")
    if env.get("python") and not os.path.exists(env["python"]):
        errs.append(f"env_spec.python 不存在：{env['python']}")
    if not obj.get("collection_scope"):
        errs.append("collection_scope 为空（§1.3）")
    sc = obj.get("gold_selfcheck") or {}
    for k in SELF_CHECK_KEYS:
        if k not in sc:
            errs.append(f"gold_selfcheck 缺 {k}（§6）")
    expect = {"gold": "resolved", "empty": "unresolved", "wrong": "unresolved"}
    for k, v in expect.items():
        if sc.get(k) != v:
            errs.append(f"三向自校验 {k} 应为 {v}，实为 {sc.get(k)!r}（§6.4）")
    if not sc.get("evidence_paths"):
        errs.append("gold_selfcheck.evidence_paths 为空（§6.4 需原始证据）")
    if obj.get("flaky_known") is False and not (sc.get("evidence_paths")):
        errs.append("flaky_known=false 但无 §4.5 双跑证据（§1.3：未跑够一致性验证必须为 true）")
    if not p2p and not any(("回归保护集" in s) or ("no regression" in s.lower())
                           for s in (obj.get("known_limits") or [])):
        errs.append("PASS_TO_PASS 为空但 known_limits 未标注「该实例无回归保护集」（§4.3）")
    # 证据/工件文件存在性
    for label, path in [(f"data_paths.{k}", (obj.get("data_paths") or {}).get(k))
                        for k in REQUIRED_DATA_PATHS] + \
                       [(f"evidence.{k}", (obj.get("evidence") or {}).get(k))
                        for k in REQUIRED_EVIDENCE]:
        if not path:
            errs.append(f"{label} 缺失")
            continue
        full = path if os.path.isabs(path) else os.path.join(artifacts_root, path)
        if not os.path.exists(full):
            errs.append(f"{label} 文件不存在：{full}")
    for p in (sc.get("evidence_paths") or []):
        full = p if os.path.isabs(p) else os.path.join(artifacts_root, p)
        if not os.path.exists(full):
            errs.append(f"gold_selfcheck.evidence_paths 文件不存在：{full}")
    if obj.get("schema_sha256") != _sha_without(obj, normalize_fn):
        errs.append("schema_sha256 不可重算复现（§1.4.5）")
    return errs
