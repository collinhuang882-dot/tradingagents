"""Explicit opt-in, real-source autonomous smoke run; keeps its report in local history."""
import sys
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import storage,agent_store
from app.models import ReviewInput
from app.scheduler import execute

if __name__=='__main__':
    if '--live' not in sys.argv:raise SystemExit('Use --live to call configured data sources and model.')
    storage.init()
    request=ReviewInput(trade_date='2026-11-12',as_of='2026-11-12T18:00:00+08:00',
        stocks=['600519.SH'],sectors=[],mode='live',execution_mode='autonomous',focus=['量价','风险']).model_dump(mode='json')
    with storage.db() as c:
        c.execute('BEGIN IMMEDIATE')
        run=storage.create_run(request,'live-evaluation:'+uuid.uuid4().hex,connection=c)
        c.execute("UPDATE runs SET status='validating' WHERE id=?",(run['id'],))
    print('run_id',run['id'],flush=True)
    execute(run['id'])
    result=storage.get_run(run['id']);state=agent_store.load(run['id'])
    print(storage.dumps({'status':result['status'],'error':result['error'],'stop_reason':state['stop_reason'],
                        'tasks':[{k:t.get(k) for k in ('id','kind','status','source_id','error')} for t in state['tasks']],
                        'budget':agent_store.budget('run:'+run['id'])}),flush=True)
