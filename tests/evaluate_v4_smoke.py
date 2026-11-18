"""Opt-in live-model smoke using an existing snapshot, isolated from user history.

Run: python -m tests.evaluate_v4_smoke --report REPORT_ID
This incurs model API calls; market data remain local-only. Credentials are not copied.
"""
import argparse
import json
from datetime import datetime
from app import storage, agent_store, scheduler
from app.agent_models import Task
from app.models import ReviewInput


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',required=True)
    parser.add_argument('--allow-network',action='store_true',help='Allow registered, bounded dispute lookups')
    parser.add_argument('--fresh',action='store_true',help='Use source report scope but fetch all data anew')
    args=parser.parse_args()
    if args.fresh and not args.allow_network: parser.error('--fresh requires --allow-network')
    settings=storage.settings()
    with storage.db() as c:
        row=c.execute('SELECT content FROM reports WHERE id=?',(args.report,)).fetchone()
        catalog=[tuple(r) for r in c.execute('SELECT * FROM catalog')]
    if not row: raise ValueError('Source report not found')
    report=json.loads(row[0])
    destination=storage.DATA / ('v4-live-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    storage.DATA=destination
    storage.settings=lambda:settings.copy()
    storage.init()
    if catalog:
        with storage.db() as c:
            c.executemany('INSERT OR REPLACE INTO catalog VALUES('+','.join('?' for _ in catalog[0])+')',catalog)
    request=ReviewInput(**(report['request']|{'research_depth':'deep','data_policy':'auto' if args.allow_network else 'local_only',
        'research_question':'请分析多空最强观点，反驳薄弱论点，并判断是否值得关注。',
        'parent_run_id':None,'execution_mode':'autonomous'})).model_dump(mode='json')
    run=storage.create_run(request,'v4-live-smoke')
    state=agent_store.load(run['id'])
    for kind, subjects, result in [
        ('fetch_market',request['stocks'],{'stocks':report['stocks'],'sectors':[],
            'evidence':[e for e in report['evidence'] if e['kind']=='行情'],'issues':[]}),
        ('search_policy',[],{'events':report['events'],'markets':report['markets'],
            'evidence':[e for e in report['evidence'] if e['kind'] in ('政策','公告','外围')], 'issues':[]})]:
        if args.fresh: break
        task=Task(id='snapshot_'+kind,kind=kind,subjects=subjects).model_dump()
        task.update(status='succeeded',result=result,error='',reason='只读复用已保存真实资料',source_id='snapshot')
        state['tasks'].append(task)
    agent_store.save(run['id'],state)
    print(json.dumps({'run_id':run['id'],'directory':str(destination)},ensure_ascii=False),flush=True)
    scheduler.execute(run['id'])
    state=agent_store.load(run['id'])
    with storage.db() as c:
        row=c.execute('SELECT content FROM reports WHERE id=?',(run['id'],)).fetchone()
    result={'run_id':run['id'],'status':storage.get_run(run['id'])['status'],
        'error':storage.get_run(run['id'])['error'],'stop_reason':state.get('stop_reason'),
        'tasks':[{'kind':t['kind'],'status':t['status'],'error':t.get('error')} for t in state['tasks']],
        'budget':agent_store.budget('run:'+run['id']),
        'research':json.loads(row[0]).get('investment_research') if row else None}
    (destination/'result.json').write_text(storage.dumps(result),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='research'},ensure_ascii=False),flush=True)


if __name__=='__main__': main()
