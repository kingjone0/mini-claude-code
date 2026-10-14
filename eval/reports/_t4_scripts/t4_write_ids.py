"""把 pilot-12 的 instance_id 列表写成 UTF-8 JSON（避开 PowerShell 重定向的 UTF-16）。"""
import json
import os

INST = r"E:\aicoding\mini-claude-code\eval\instances"
OUT = r"E:\aicoding\eval-runs\t4\pilot_ids.json"
os.makedirs(os.path.dirname(OUT), exist_ok=True)
ids = []
for f in sorted(os.listdir(INST)):
    if not f.endswith(".json") or f.startswith("_"):
        continue
    o = json.load(open(os.path.join(INST, f), encoding="utf-8"))
    if o.get("set_membership") == "pilot-12":
        ids.append(o["instance_id"])
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(ids, fh, ensure_ascii=False, indent=2)
print(OUT, len(ids))
for i in ids:
    print(" ", i)