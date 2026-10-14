"""Audit an existing P1 batch without altering verdicts, agent state or source repositories."""
import hashlib,json,pathlib,collections
base=pathlib.Path(r'E:\aicoding\eval-runs\p1-20261013T122356-t20-batch')
rows=[json.loads(line) for line in (base/'triple_state.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
def file_record(path):
    if not path.is_file(): return {'path':str(path),'missing':True}
    data=path.read_bytes()
    return {'path':str(path),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
records=[]
for n,r in enumerate(rows,1):
    art=pathlib.Path(r['workspace']).parent/'artifacts'
    names=('console_'+r['arm']+'.log','arm_report.jsonl','result.json','judged.json','verdict.json','verdict_run.txt','candidate_patch_judged.diff','policy_evidence_isolated.json')
    rec={'state_line':n,'instance_id':r['instance_id'],'arm':r['arm'],'run_id':r['run_id'],'resolved':r['resolved'],'verdict_class':r['verdict_class'],'wall_s':r.get('wall_s'),'started_at':r.get('started_at'),'finished_at':r.get('finished_at'),'timed_out':r.get('timed_out'),'exit_code':r.get('exit_code'),'cmd_str':r.get('cmd_str'),'pytest':r.get('pytest'),'judged':r.get('judged'),'tokens':{k:v for k,v in r.items() if 'token' in k or 'cost' in k},'metrics':{k:v for k,v in r.items() if k in ('num_turns','tool_calls_total','tool_calls_by_name','tools','usage','metrics')},'artifacts':[file_record(art/name) for name in names]}
    records.append(rec)
output={'batch':base.name,'state':file_record(base/'triple_state.jsonl'),'manifest':file_record(base/'manifest.json'),'records':records}
p=base/'t20_audit_index.json'
p.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('index',p,'rows',len(records),'bytes',p.stat().st_size,'sha256',hashlib.sha256(p.read_bytes()).hexdigest())
print('classes',dict(collections.Counter(r['verdict_class'] for r in rows)))
for rec in records:
    print(f"{rec['state_line']}|{rec['instance_id']}|{rec['arm']}|{rec['resolved']}|{rec['verdict_class']}|{rec['wall_s']}|{rec['run_id']}")
