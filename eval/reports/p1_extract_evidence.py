"""Read-only source extraction; write isolated evidence into existing run artifacts."""
import json, pathlib, hashlib, datetime
ROOT=pathlib.Path(r'E:\aicoding\mini-claude-code')
STATE=pathlib.Path(r'E:\aicoding\eval-runs\p1-20261013T122356-t20-batch\triple_state.jsonl')
def rows(p):
    if not p.exists(): return []
    return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]
def main():
    shared=rows(ROOT/'servers/experiments/out/policy_dispatch.jsonl')
    summary=[]
    for r in rows(STATE):
        if r['arm']!='C': continue
        art=pathlib.Path(r['workspace']).parent/'artifacts'
        selected=[s for s in shared if s.get('label')==r['label'] and datetime.datetime.fromisoformat(s['ts']).timestamp() >= r['started_at_epoch']]
        report=rows(art/'arm_report.jsonl')
        payload={'run_id':r['run_id'],'label':r['label'],'started_at_epoch':r['started_at_epoch'],'dispatch_rows':selected,'arm_report_rows':report,'verdict_class':r['verdict_class'],'note':'Missing end-of-dispatch fields on timeout are retained as missing; no inference.'}
        target=art/'policy_evidence_isolated.json'
        data=(json.dumps(payload,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
        target.write_bytes(data)
        summary.append({'run_id':r['run_id'],'path':str(target),'dispatch_rows':len(selected),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    out=STATE.parent/'policy_extraction_index.json'
    out.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'isolated C evidence: {len(summary)} runs')
if __name__=='__main__': main()
