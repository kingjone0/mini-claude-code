"""独立重算 P1 t20-batch；只读 eval-runs 产物与 usage.jsonl。"""
from __future__ import annotations
import json, statistics
from pathlib import Path
ROOT=Path(r"E:\aicoding\eval-runs\p1-20261013T122356-t20-batch")
PROJECT=Path(r"E:\aicoding\mini-claude-code")
USAGE=PROJECT/"servers/experiments/out/usage.jsonl"
rows=[json.loads(x) for x in (ROOT/"triple_state.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
usage=[json.loads(x) for x in USAGE.read_text(encoding="utf-8").splitlines() if x.strip()]
# t24 待核的门禁逐 node 口径；保留两套统计，绝不改标
GATE_SUSPECT={10,11,12,13,15,16,17,21} # triple_state 1-based rows listed by verdict_gate

def parent(r):
    xs=[u for u in usage if u.get("script")=="coding_agent.py" and u.get("label")==r.get("label")]
    return xs[-1] if xs else None

def scout(r):
    p=Path(r["artifacts"][0]).parent/"policy_evidence_isolated.json"
    if not p.exists(): return {"input_uncached":0,"cache_read":0,"cache_creation":0,"cost_usd":0,"events":0}
    d=json.loads(p.read_text(encoding="utf-8")); seen=set(); out={"input_uncached":0,"cache_read":0,"cache_creation":0,"cost_usd":0,"events":0}
    for row in d.get("dispatch_rows",[]):
        for a in row.get("attempts",[]):
            acc=a.get("scout_accounting") or {}
            key=acc.get("task_id") or (row.get("ts"),row.get("group"),a.get("n"))
            if key in seen: continue
            seen.add(key); out["events"]+=1
            for k,src in [("input_uncached","input_uncached"),("cache_read","cache_read"),("cache_creation","cache_creation"),("cost_usd","cost_usd")]: out[k]+=float(acc.get(src) or 0)
    return out

def vals(rs,key):
    v=[]
    for x in rs:
      z=x.get(key)
      if isinstance(z,dict): z=z.get('input_uncached')
      if z is not None: v.append(float(z))
    return (statistics.median(v),min(v),max(v)) if v else None
for i,r in enumerate(rows,1):
    r["row_no"]=i; u=parent(r); s=scout(r)
    r["parent_usage"]=u or {}; r["scout_usage"]=s
    if u:
      r["parent_tokens"]={k:u.get(k) for k in ("input_uncached","cache_read","cache_creation")}
      r["parent_cost"]=u.get("total_cost_usd"); r["num_turns"]=u.get("num_turns")
    else: r["parent_tokens"]={}; r["parent_cost"]=None; r["num_turns"]=None
    r["e2e_tokens"]={k:(float((u or {}).get(k) or 0)+s[k]) for k in ("input_uncached","cache_read","cache_creation")}
    r["e2e_cost"]=(float((u or {}).get("total_cost_usd") or 0)+s["cost_usd"]) if u else None
    r["gate_suspect"]=i in GATE_SUSPECT
for r in rows:
    print(json.dumps({k:r.get(k) for k in ("row_no","instance_id","arm","resolved","verdict_class","wall_s","num_turns","parent_tokens","scout_usage","e2e_tokens","parent_cost","e2e_cost","gate_suspect")},ensure_ascii=False))
print("SUMMARY")
for arm in "ABC":
 x=[r for r in rows if r["arm"]==arm]; valid=[r for r in x if r["verdict_class"] in {"agent_result","empty_patch","patch_conflict"}]
 print(arm,"all",len(x),"resolved",sum(r["resolved"] for r in valid),"valid",len(valid),"timeout",sum(r["verdict_class"]=="timeout" for r in x),"parent_uncached",vals(valid,"parent_tokens"))
 for k in ("parent_cost","e2e_cost","num_turns","wall_s"):
  print(" ",k,vals(valid,k))
 print(" e2e_uncached",vals(valid,"e2e_tokens"))
clean=[r for r in rows if not r["gate_suspect"]]
print("CLEAN",len(clean))
for arm in "ABC":
 x=[r for r in clean if r["arm"]==arm]; v=[r for r in x if r["verdict_class"] in {"agent_result","empty_patch","patch_conflict"}]; print(arm,sum(r["resolved"] for r in v),len(v))
