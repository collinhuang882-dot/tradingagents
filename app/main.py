import json
import os
import subprocess
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import storage
from .models import ReviewInput, ExportInput, SettingsInput, ReportFilters, now, TERMINAL, TZ
from .providers import catalog, sync_catalog, AKShare, ProviderError, request_json
from .policy import source_status, sync_policies
from .reporting import filter_report


@asynccontextmanager
async def lifespan(app):
    storage.init()
    stop = threading.Event()
    if os.getenv('TRADINGAGENT_NO_WORKER') != '1':
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        def launch():
            # Avoid Windows venv redirector processes so shutdown owns the actual worker.
            executable = sys._base_executable if os.name == 'nt' else sys.executable
            environment = os.environ | {'PYTHONPATH': os.pathsep.join(sys.path)}
            return subprocess.Popen([executable, '-m', 'app.worker'], cwd=storage.ROOT, env=environment,
                                    creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        app.state.worker = launch()
        def supervise():
            while not stop.wait(2):
                # A clean exit means another process already owns the worker lock.
                if app.state.worker.poll() not in (None, 0):
                    app.state.worker = launch()
        threading.Thread(target=supervise, daemon=True).start()
        threading.Thread(target=sync_policies, daemon=True).start()
    try:
        yield
    finally:
        stop.set()
        if hasattr(app.state, 'worker') and app.state.worker.poll() is None:
            app.state.worker.terminate()
            app.state.worker.wait(timeout=10)


app = FastAPI(title='TradingAgents A 股复盘', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'])


@app.middleware('http')
async def local_origin(request: Request, call_next):
    origin = request.headers.get('origin')
    if origin and urlparse(origin).hostname not in {'localhost', '127.0.0.1', '::1', 'testserver'}:
        return JSONResponse({'detail': '仅允许本机界面访问'}, status_code=403)
    return await call_next(request)


@app.exception_handler(ProviderError)
async def provider_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


def get_run(rid):
    row = storage.get_run(rid)
    if not row:
        raise HTTPException(404, '任务不存在')
    return row


def get_report(rid):
    if rid.startswith('answer-'):
        with storage.db() as c:
            artifact = c.execute('SELECT content FROM research_artifacts WHERE id=?', (rid,)).fetchone()
        if not artifact: raise HTTPException(404, '研究成果不存在')
        return json.loads(artifact[0])
    run = get_run(rid)
    if run['status'] not in ('completed', 'partial'):
        raise HTTPException(409, '任务尚未形成可用报告')
    with storage.db() as c:
        row = c.execute('SELECT content FROM reports WHERE id=?', (rid,)).fetchone()
    if not row:
        raise HTTPException(404, '报告不存在')
    return json.loads(row[0])


@app.get('/api/health')
def health():
    return {'status': 'ok', 'version': '4.0.0-preview.1', 'now': now().isoformat()}


@app.get('/api/defaults')
def defaults(mode: str = 'live'):
    today = now()
    day = today.date()
    if today.hour < 15:
        day -= timedelta(days=1)
    if mode == 'live':
        try:
            dates = AKShare().trade_dates((day-timedelta(days=30)).strftime('%Y%m%d'), day.strftime('%Y%m%d'))
            day = datetime.strptime(max(dates), '%Y%m%d').date()
        except (ProviderError, ValueError):
            pass
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    cutoff = datetime.combine(day, datetime.min.time()).replace(hour=18, tzinfo=TZ)
    cutoff = min(cutoff, today)
    return {'trade_date': day.isoformat(), 'as_of': cutoff.isoformat(), 'time': cutoff.strftime('%H:%M')}


@app.get('/api/settings')
def settings():
    return {**storage.public_settings(), 'sources': source_status()}


@app.put('/api/settings')
def update_settings(values: SettingsInput):
    url = urlparse(values.llm_url)
    if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise HTTPException(422, '模型地址必须是有效的 HTTP(S) 服务地址，不得包含凭证或查询参数')
    storage.save_settings(values.model_dump())
    return storage.public_settings()


@app.post('/api/settings/ifind/test')
def test_ifind():
    from .ifind import IFinD
    try:
        rows, factors, _, notes = IFinD().history('600519.SH', '20260105', '20260109')
        return {'status': 'ok' if len(factors) == len(rows) else 'limited',
                'note': f'iFinD 历史行情连接成功：{len(rows)} 条日线。' + '；'.join(notes)}
    except ProviderError as exc:
        raise HTTPException(422, str(exc)) from None


@app.post('/api/settings/test')
def test_connections():
    results = []
    client = AKShare()
    for name, operation in [
        ('股票目录', lambda: client.catalog('stock')),
        ('行业目录', lambda: client.catalog('sector')),
        ('交易日历', lambda: client.trade_dates('20260101', '20260110')),
        ('行情', lambda: client.history('600519.SH', '20260101', '20260110')[0]),
    ]:
        try:
            rows = operation()
            results.append({'name': name, 'status': 'ok' if rows else 'limited', 'note': f'AKShare 返回 {len(rows)} 条记录'})
        except ProviderError as exc:
            results.append({'name': name, 'status': 'error', 'note': str(exc)})
    config = storage.settings()
    try:
        if not config['llm_key']:
            raise ProviderError('尚未填写模型密钥')
        request_json(config['llm_url'].rstrip('/')+'/chat/completions', {'model': config['llm_model'],
                     'messages': [{'role': 'user', 'content': 'Reply OK'}], 'max_tokens': 8},
                     {'Authorization': 'Bearer '+config['llm_key']}, retries=0)
        results.append({'name': '模型', 'status': 'ok', 'note': '调用成功'})
    except ProviderError as exc:
        results.append({'name': '模型', 'status': 'error', 'note': str(exc)})
    return results


@app.post('/api/catalog/sync')
def refresh_catalog():
    return sync_catalog()


@app.post('/api/policies/sync')
def refresh_policies():
    return sync_policies(force=True)


@app.get('/api/instruments')
def instruments(q: str = '', kind: str = '', mode: str = 'live'):
    return [r for r in catalog(mode) if (not kind or r['kind']==kind) and
            q.lower() in (r['id']+r['name']+r.get('sector', '')).lower()][:60]


def validate_request(value):
    request = value.model_dump(mode='json')
    objects = {x['id']: x for x in catalog(request['mode'])}
    errors, warnings = [], []
    for kind, ids in [('stock', request['stocks']), ('sector', request['sectors'])]:
        if any(i not in objects or objects[i]['kind'] != kind for i in ids):
            errors.append('证券或行业代码无效，请先同步目录并重新选择')
    if request['mode'] == 'demo':
        if value.trade_date.weekday() >= 5:
            errors.append('样例日历仅支持周一至周五，请选择其他日期')
        warnings.append('演示使用合成数据和工作日日历，不代表真实交易数据')
    else:
        cfg = storage.settings()
        if not cfg['llm_key']:
            errors.append('请配置模型密钥')
        if not errors:
            try:
                day = value.trade_date.strftime('%Y%m%d')
                if day not in AKShare().trade_dates(day, day):
                    errors.append('该日不是交易日，请选择有效交易日')
            except ProviderError as exc:
                errors.append(str(exc))
        warnings.append('AKShare 无需行情密钥；上游网络及数据覆盖以实际取数为准，受限模块会显示缺口')
    return {'ok': not errors, 'errors': list(dict.fromkeys(errors)), 'warnings': warnings, 'sources': source_status()}


@app.post('/api/reviews/preflight')
def preflight(value: ReviewInput):
    return validate_request(value)


@app.post('/api/reviews', status_code=202)
def create_review(value: ReviewInput, idempotency_key: str = Header(...)):
    if not 1 <= len(idempotency_key) <= 150:
        raise HTTPException(422, '幂等键长度无效')
    with storage.db() as c:
        old = c.execute('SELECT * FROM runs WHERE idem=?', (idempotency_key,)).fetchone()
    if old:
        if json.loads(old['request']) != value.model_dump(mode='json'):
            raise HTTPException(409, '幂等键对应不同参数')
        return storage.run(old)
    validation = validate_request(value)
    if not validation['ok']:
        raise HTTPException(422, '；'.join(validation['errors']))
    return storage.create_run(value.model_dump(mode='json'), idempotency_key)


@app.get('/api/reviews')
def reviews(start: str = '', end: str = '', stocks: str = '', sectors: str = '', status: str = '', page: int = Query(1, ge=1)):
    with storage.db() as c:
        rows = [storage.run(r) for r in c.execute('SELECT * FROM runs')]
    rows.sort(key=lambda r: (r['request']['trade_date'], r['created']), reverse=True)
    all_count = len(rows)
    rows = [r for r in rows if (not start or r['request']['trade_date'] >= start)
            and (not end or r['request']['trade_date'] <= end)
            and (not stocks or bool(set(stocks.split(',')) & set(r['request']['stocks'])))
            and (not sectors or bool(set(sectors.split(',')) & set(r['request']['sectors'])))
            and (not status or r['status'] in status.split(','))]
    return {'items': rows[(page-1)*20:page*20], 'count': len(rows), 'total': all_count}


@app.get('/api/reviews/{rid}')
def review(rid: str):
    row = get_run(rid)
    row['usage'] = storage.usage(rid)
    with storage.db() as c:
        row['events'] = [dict(r) for r in c.execute('SELECT * FROM events WHERE run_id=? ORDER BY id DESC LIMIT 60', (rid,))]
    return row


@app.post('/api/reviews/{rid}/cancel')
def cancel(rid: str):
    row = get_run(rid)
    if row['status'] not in TERMINAL:
        storage.update_run(rid, cancel=1, **({'status': 'cancelled'} if row['status']=='queued' else {}))
    return get_run(rid)


@app.post('/api/reviews/{rid}/resume')
def resume(rid: str):
    row = get_run(rid)
    if row['status'] != 'failed':
        raise HTTPException(409, '仅失败任务可恢复，已完成任务请创建新版本')
    if row['request']['mode'] == 'live' and row['config'].get('data_provider') != 'akshare-1.20.38':
        raise HTTPException(409, '行情来源已切换至 AKShare，请复制参数创建新任务，避免混用旧快照')
    if row['request'].get('execution_mode') == 'autonomous' and row['config'].get('planner_version') != '2.0.0-preview.2':
        raise HTTPException(409, '自主执行器版本已改变，请复制参数创建新版本')
    config = storage.settings()
    if row['config']['llm_model'] != config['llm_model'] or row['config']['llm_url'] != config['llm_url']:
        raise HTTPException(409, '模型配置已改变，请复制参数创建新版本')
    storage.update_run(rid, status='queued', cancel=0, error='')
    return get_run(rid)


@app.get('/api/reports/{rid}')
def report(rid: str):
    return get_report(rid)


@app.post('/api/reports/{rid}/filter')
def filtered(rid: str, filters: ReportFilters):
    return filter_report(get_report(rid), filters.model_dump())


@app.get('/api/reports/{rid}/evidence/{eid}')
def report_evidence(rid: str, eid: str):
    found = next((e for e in get_report(rid)['evidence'] if e['id']==eid), None)
    if not found:
        raise HTTPException(404, '证据不存在于当前报告')
    return found


@app.post('/api/reports/{rid}/exports', status_code=202)
def create_export(rid: str, spec: ExportInput):
    get_report(rid)
    key = storage.digest([rid, spec.model_dump()])
    with storage.db() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT * FROM exports WHERE request_key=?', (key,)).fetchone()
        if old:
            if old['status']=='failed':
                c.execute("UPDATE exports SET status='queued',error='' WHERE id=?", (old['id'],))
            return {'id': old['id']}
        eid = uuid.uuid4().hex
        c.execute('INSERT INTO exports(id,request_key,report_id,request,status) VALUES(?,?,?,?,?)',
                  (eid, key, rid, storage.dumps(spec.model_dump()), 'queued'))
    return {'id': eid}


@app.get('/api/exports/{eid}')
def export_status(eid: str):
    with storage.db() as c:
        row = c.execute('SELECT id,status,error FROM exports WHERE id=?', (eid,)).fetchone()
    if not row:
        raise HTTPException(404, '导出任务不存在')
    return dict(row)


@app.get('/api/exports/{eid}/download')
def download(eid: str):
    with storage.db() as c:
        row = c.execute('SELECT * FROM exports WHERE id=?', (eid,)).fetchone()
    if not row or row['status'] != 'completed':
        raise HTTPException(409, '导出尚未完成')
    path = Path(row['path'])
    if not path.is_file():
        raise HTTPException(410, '导出文件不存在，请重新生成')
    return FileResponse(path, filename=path.name)



from . import conversation, agent_store
from .agent_models import ConversationCreate, MessageCreate


@app.post('/api/conversations', status_code=201)
def create_conversation(value: ConversationCreate):
    try:
        return conversation.create(value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get('/api/conversations')
def list_conversations(q: str = '', before: int | None = Query(None, ge=1), limit: int = Query(30, ge=1, le=100)):
    with storage.db() as c:
        rows = c.execute('SELECT rowid,content FROM conversations WHERE rowid<? ORDER BY rowid DESC',
                         (before if before is not None else 2**63-1,)).fetchall()
    items = []
    for row in rows:
        conv = json.loads(row['content'])
        title = conv.get('title') or ' / '.join(conv.get('requirements', {}).get('stocks', [])) or '新研究'
        if q and q.lower() not in (title + storage.dumps(conv.get('requirements', {}))).lower(): continue
        items.append({'id':conv['id'], 'cursor':row['rowid'], 'title':title, 'created':conv['created'],
                      'mode':conv['mode'], 'requirements':conv.get('requirements', {})})
        if len(items) == limit: break
    return items


@app.get('/api/conversations/{cid}')
def get_conversation(cid: str):
    try:
        result = conversation.get(cid)
        with storage.db() as c:
            budget = c.execute("SELECT COALESCE(SUM(b.model),0) AS model,COALESCE(SUM(b.network),0) AS network FROM request_budgets b WHERE b.owner IN (SELECT DISTINCT 'turn:' || COALESCE(json_extract(content,'$.turn_id'),id) FROM conversation_messages WHERE conversation_id=?)", (cid,)).fetchone()
        result['budget'] = dict(budget)
        result['legacy_budget'] = agent_store.budget('chat:' + cid)
        return result
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@app.post('/api/conversations/{cid}/messages', status_code=202)
def send_message(cid: str, value: MessageCreate):
    try:
        return conversation.submit(cid, value)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get('/api/conversations/{cid}/messages')
def conversation_messages(cid: str, after_seq: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
                          before_seq: int | None = Query(None, ge=1), latest: bool = False):
    get_conversation(cid)
    if after_seq and (before_seq is not None or latest):
        raise HTTPException(422, '前向与后向分页不能混用')
    return conversation.messages(cid, after_seq, limit, before_seq, latest)


@app.get('/api/conversations/{cid}/threads')
def conversation_threads(cid: str):
    conv = get_conversation(cid)
    return conv.get('threads', [])


@app.get('/api/conversations/{cid}/context')
def conversation_context(cid: str):
    conv = get_conversation(cid)
    return {'context_version':conv['requirement_version'], 'thread_id':conv.get('active_thread_id',cid),
            'scope':conv['requirements'], 'research_policy':conv.get('research_policy','auto'),
            'pending':conv.get('pending'), 'run_id':conv.get('run_id'), 'report_id':conv.get('report_id')}


@app.get('/api/conversations/{cid}/sync')
def sync_conversation(cid: str, after_cursor: int = Query(0,ge=0), limit: int = Query(100,ge=1,le=200)):
    with storage.db() as c:
        c.execute('BEGIN')
        if not c.execute('SELECT 1 FROM conversations WHERE id=?',(cid,)).fetchone():
            raise HTTPException(404,'会话不存在')
        maximum=c.execute('SELECT COALESCE(MAX(cursor),0) FROM conversation_events WHERE conversation_id=?',(cid,)).fetchone()[0]
        if after_cursor==0 or after_cursor>maximum:
            rows=c.execute('SELECT content,status,seq FROM conversation_messages WHERE conversation_id=? ORDER BY seq DESC LIMIT 50',(cid,)).fetchall()
            messages=[{**json.loads(row['content']),'seq':row['seq'],'status':row['status']} for row in reversed(rows)]
            return {'messages':messages,'cursor':maximum,'reset':True,'has_more':False}
        rows=c.execute('SELECT cursor,content FROM conversation_events WHERE conversation_id=? AND cursor>? ORDER BY cursor LIMIT ?',
                       (cid,after_cursor,limit)).fetchall()
        return {'messages':[json.loads(row['content']) for row in rows],
                'cursor':rows[-1]['cursor'] if rows else after_cursor,'reset':False,
                'has_more':bool(rows and rows[-1]['cursor']<maximum)}


@app.get('/api/turns/{turn_id}')
def get_turn(turn_id: str):
    with storage.db() as c:
        row = c.execute("SELECT content,status FROM conversation_messages WHERE COALESCE(json_extract(content,'$.turn_id'),id)=? ORDER BY seq DESC LIMIT 1", (turn_id,)).fetchone()
    if not row: raise HTTPException(404, '研究轮次不存在')
    msg = json.loads(row['content'])
    result = {k:msg.get(k) for k in ('turn_id','conversation_id','thread_id','answer','artifact_id','supplement_run_id','retrieval','limitations','context','coverage','gaps')}
    result['status'] = row['status']
    result['budget'] = agent_store.budget('turn:'+turn_id)
    owners=['turn:'+turn_id]
    if msg.get('supplement_run_id'):
        result['run_budget'] = agent_store.budget('run:'+msg['supplement_run_id'])
        owners.append('run:'+msg['supplement_run_id'])
    with storage.db() as c:
        rows=c.execute('SELECT label,content FROM research_inputs WHERE owner IN ('+','.join('?' for _ in owners)+') ORDER BY id DESC LIMIT 50',owners).fetchall()
    result['input_audits']=[{'label':r['label'],**json.loads(r['content'])} for r in reversed(rows)]
    return result


@app.post('/api/turns/{turn_id}/cancel')
def cancel_turn(turn_id: str):
    get_turn(turn_id)
    with storage.db() as c:
        c.execute('BEGIN IMMEDIATE')
        rows = c.execute("SELECT id,content,status FROM conversation_messages WHERE COALESCE(json_extract(content,'$.turn_id'),id)=?",(turn_id,)).fetchall()
        for row in rows:
            if row['status'] in ('completed','failed','cancelled'): continue
            msg = json.loads(row['content'])
            if msg.get('supplement_run_id'):
                c.execute('UPDATE runs SET cancel=1 WHERE id=?',(msg['supplement_run_id'],))
            msg.update(status='cancelled',answer='这次请求已停止，已获取的资料仍保留。')
            c.execute("UPDATE conversation_messages SET status='cancelled',content=? WHERE id=?",(storage.dumps(msg),row['id']))
            conv_row = c.execute('SELECT content FROM conversations WHERE id=?', (msg['conversation_id'],)).fetchone()
            conv = json.loads(conv_row['content'])
            pending = conv.get('pending') or {}
            if pending.get('turn_id') == turn_id:
                conv.update(pending=None, status='ready')
                conversation.save(conv, c)
            elif msg.get('supplement_run_id') and conv.get('run_id') == msg['supplement_run_id']:
                conv['status'] = 'ready'
                conversation.save(conv, c)
    return get_turn(turn_id)


@app.post('/api/turns/{turn_id}/resume')
def resume_turn(turn_id: str):
    with storage.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute("SELECT id,content,status FROM conversation_messages WHERE COALESCE(json_extract(content,'$.turn_id'),id)=? ORDER BY seq DESC LIMIT 1",(turn_id,)).fetchone()
        if not row: raise HTTPException(404,'研究轮次不存在')
        if row['status'] in ('received','queued','interpreting','researching'): return {'turn_id':turn_id,'status':row['status']}
        if row['status']!='failed': raise HTTPException(409,'仅失败的轮次可以恢复；已停止或完成的请求请创建新轮次')
        msg = json.loads(row['content'])
        conv = json.loads(c.execute('SELECT content FROM conversations WHERE id=?',(msg['conversation_id'],)).fetchone()[0])
        if msg.get('thread_id',conv['id']) != conv.get('active_thread_id',conv['id']):
            raise HTTPException(409,'请先切回原研究主题')
        rid = msg.get('supplement_run_id')
        if rid:
            if conv.get('run_id') != rid: raise HTTPException(409,'研究范围已变化，请在当前范围新建请求')
            run = storage.run(c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())
            if not run or run['status']=='cancelled' or run['cancel']: raise HTTPException(409,'关联任务已停止，不能恢复')
            settings = storage.settings()
            if any(run['config'][key] != settings[key] for key in ('llm_model','llm_url')):
                raise HTTPException(409,'模型配置已改变，请新建研究轮次')
            if run['status']=='failed':
                state_row=c.execute('SELECT content FROM agent_states WHERE run_id=?',(rid,)).fetchone()
                state=json.loads(state_row[0]) if state_row else {}
                from .investment_research import enabled
                if state.get('rounds',0)>=(16 if enabled(run) else 8) or state.get('no_progress',0)>=2:
                    raise HTTPException(409,'本轮已达到规划或无进展上限，请调整问题后新建轮次')
                state['stop_reason']=''
                if state_row: c.execute('UPDATE agent_states SET content=? WHERE run_id=?',(storage.dumps(state),rid))
                c.execute("UPDATE runs SET status='queued',error='' WHERE id=?",(rid,))
            owner='run:'+rid
            status='researching'
        else:
            if msg['base_requirement_version']!=conv['requirement_version']: raise HTTPException(409,'研究版本已变化，请新建请求')
            owner='turn:'+turn_id
            status='received'
        budget=c.execute('SELECT * FROM request_budgets WHERE owner=?',(owner,)).fetchone()
        from .investment_research import enabled
        seconds = (1800 if enabled(run) else 900) if rid else 120
        if budget and (budget['model']>=budget['model_limit'] or budget['elapsed'] >= seconds):
            raise HTTPException(409,'原轮次预算已耗尽，恢复不会重置预算')
        msg.update(status=status,answer='已恢复本轮，继续使用已取得的资料和原预算。')
        c.execute('UPDATE conversation_messages SET status=?,content=? WHERE id=?',(status,storage.dumps(msg),row['id']))
        conv['status']=status
        conversation.save(conv,c)
    return get_turn(turn_id)


@app.get('/api/artifacts/{revision_id}')
def get_artifact(revision_id: str):
    report = get_report(revision_id)
    if report.get('artifact_type') != 'answer': raise HTTPException(404,'不是对话成果')
    return report


@app.post('/api/artifacts/{revision_id}/exports', status_code=202)
def export_artifact(revision_id: str, spec: ExportInput):
    get_artifact(revision_id)
    return create_export(revision_id,spec)


@app.post('/api/conversations/{cid}/threads/{tid}/activate')
def activate_thread(cid: str, tid: str):
    with storage.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT content FROM conversations WHERE id=?', (cid,)).fetchone()
        if not row: raise HTTPException(404, '会话不存在')
        conv = json.loads(row[0])
        target = next((t for t in conv.get('threads', []) if t['id'] == tid), None)
        if not target: raise HTTPException(404, '研究主题不存在')
        busy = c.execute("SELECT id FROM conversation_messages WHERE conversation_id=? AND status IN ('interpreting','researching')", (cid,)).fetchone()
        run = c.execute('SELECT status FROM runs WHERE id=?', (conv.get('run_id'),)).fetchone()
        if busy or (run and run['status'] not in ('completed','partial','failed','cancelled')):
            raise HTTPException(409, '当前研究仍在执行，请先停止或等待完成再切换主题')
        conv.update({k:v for k,v in target.items() if k != 'id'})
        conv['active_thread_id'] = tid
        conv['requirement_version'] += 1
        conversation.save(conv, c)
    return conv


@app.get('/api/conversations/{cid}/changes')
def conversation_changes(cid: str):
    get_conversation(cid)
    with storage.db() as c:
        return [{**dict(r), 'content': json.loads(r['content'])} for r in
                c.execute('SELECT * FROM conversation_changes WHERE conversation_id=? ORDER BY rowid', (cid,))]


@app.post('/api/conversations/{cid}/changes/{change_id}/withdraw')
def withdraw_change(cid: str, change_id: str):
    get_conversation(cid)
    with storage.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute("SELECT * FROM conversation_changes WHERE id=? AND conversation_id=? AND status='pending'", (change_id, cid)).fetchone()
        if not row: raise HTTPException(409, '变更已应用或不存在，不能撤回')
        detail = json.loads(row['content'])
        conv = json.loads(c.execute('SELECT content FROM conversations WHERE id=?', (cid,)).fetchone()[0])
        if conv['requirement_version'] != detail['version']:
            raise HTTPException(409, '已有较新的需求，请发送修正要求，不能撤回中间版本')
        prior = c.execute('SELECT content FROM conversation_requirements WHERE conversation_id=? AND version=?', (cid, detail['version']-1)).fetchone()
        conv['requirements'] = json.loads(prior[0]) if prior else get_run(row['run_id'])['request']
        conv['requirement_version'] += 1
        c.execute('INSERT INTO conversation_requirements VALUES(?,?,?)', (cid,conv['requirement_version'],storage.dumps(conv['requirements'])))
        conversation.save(conv,c)
        c.execute("UPDATE conversation_changes SET status='withdrawn' WHERE id=?", (change_id,))
    return {'status': 'withdrawn'}


@app.get('/api/reviews/{rid}/plan')
def review_plan(rid: str, version: int | None = None):
    get_run(rid)
    if version is not None:
        with storage.db() as c:
            row = c.execute('SELECT content FROM run_plans WHERE run_id=? AND version=?', (rid, version)).fetchone()
        if not row: raise HTTPException(404, '计划版本不存在')
        state = json.loads(row[0])
    else:
        state = agent_store.load(rid)
        import time
        for task in state['tasks']:
            if task['status']=='running' and task.get('started_at'):
                task['waiting_seconds']=max(0,int(time.time()-task['started_at']))
    return {**state, 'budget': agent_store.budget('run:' + rid)}


@app.get('/api/reviews/{rid}/actions')
def review_actions(rid: str, after_seq: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    get_run(rid)
    with storage.db() as c:
        return [{**dict(r), 'seq': r['id'], 'detail': json.loads(r['detail'])} for r in
                c.execute('SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT ?', (rid, after_seq, limit))]


@app.get('/api/reviews/{rid}/gaps')
def review_gaps(rid: str):
    get_run(rid)
    from .scheduler import materialize
    return materialize(rid, agent_store.load(rid))['gaps']


if (storage.ROOT / 'dist').exists():
    app.mount('/', StaticFiles(directory=storage.ROOT / 'dist', html=True), name='ui')
