import json
import pytest
from fastapi.testclient import TestClient
from app import storage, agent_store, conversation
from app.models import ReviewInput
from app.agent_models import ConversationCreate, MessageCreate
from app.scheduler import apply_plan, execute
from app.main import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    monkeypatch.setenv('TRADINGAGENT_NO_WORKER', '1')
    monkeypatch.delenv('TRADING_LLM_KEY', raising=False)
    storage.init()


def req():
    return ReviewInput(trade_date='2026-11-12', as_of='2026-11-12T18:00:00+08:00',
                       stocks=['600519.SH'], sectors=['demo:白酒'], mode='demo', execution_mode='autonomous',
                       research_depth='quick').model_dump(mode='json')  # Preserve the legacy planner contract; V4 has its own suite.


def add(id='data', **kwargs):
    return {'type':'add_task','task':{'id':id,'kind':'fetch_market','subjects':['600519.SH'],
            'source_id':'akshare.em.daily',**kwargs},'reason':'读取关注股票日线'}


def test_demo_dynamic_plan_and_report():
    row=storage.create_run(req(),'demo')
    execute(row['id'])
    run=storage.get_run(row['id'])
    assert run['status'] in ('completed','partial'),run
    state=agent_store.load(row['id'])
    assert state['version']>=4
    assert all(t['status']=='succeeded' for t in state['tasks']),state
    with storage.db() as c:
        report=json.loads(c.execute('SELECT content FROM reports WHERE id=?',(row['id'],)).fetchone()[0])
    assert report['execution_mode']=='autonomous'
    assert report['analysis']['risk']['claims']
    assert report['decision_summary']


@pytest.mark.parametrize('task',[
    add(subjects=['000858.SZ']),add(source_id='unknown'),add(depends_on=['missing']),
    add(depends_on=['data']),add(kind='review_risk',source_id='',depends_on=[]),
    add(kind='fetch_document',source_id='',url='http://127.0.0.1/secret'),
])
def test_invalid_plans_do_not_mutate(task):
    row=storage.create_run(req(),'invalid');state=agent_store.load(row['id'])
    with pytest.raises(ValueError):apply_plan(row['id'],state,{'base_plan_version':0,'actions':[task]})
    assert not agent_store.load(row['id'])['tasks']


def test_plan_duplicate_and_stale_rejected():
    row=storage.create_run(req(),'duplicate');state=agent_store.load(row['id'])
    state=apply_plan(row['id'],state,{'base_plan_version':0,'actions':[add()]})
    with pytest.raises(ValueError):apply_plan(row['id'],state,{'base_plan_version':0,'actions':[add('x')]})
    with pytest.raises(ValueError):apply_plan(row['id'],state,{'base_plan_version':1,'actions':[add('x')]})


def test_revision_invalidates_downstream():
    row=storage.create_run(req(),'revision');state=agent_store.load(row['id'])
    state=apply_plan(row['id'],state,{'base_plan_version':0,'actions':[add(),add('analysis',kind='analyze_market',source_id='',depends_on=['data'])]})
    state['tasks'][0]['status']='succeeded';state['tasks'][1]['status']='succeeded'
    state=apply_plan(row['id'],state,{'base_plan_version':1,'actions':[{'type':'revise_task','task_id':'data','task':add('new',source_id='akshare.sina.daily')['task'],'reason':'切换数据源'}]})
    assert [t['status'] for t in state['tasks']]==['superseded','superseded','pending']


def test_budget_atomic_hard_limit():
    from concurrent.futures import ThreadPoolExecutor
    agent_store.ensure_budget('test',model=3,network=2)
    def consume(_):
        try:agent_store.reserve('network','test');return True
        except agent_store.BudgetExceeded:return False
    with ThreadPoolExecutor(max_workers=5) as pool:assert sum(pool.map(consume,range(10)))==2
    assert agent_store.budget('test')['network']==2


def test_message_idempotency_and_version():
    with TestClient(app) as c:
        conv=c.post('/api/conversations',json={'mode':'demo'}).json()
        body={'text':'复盘茅台','client_message_id':'one','base_requirement_version':0}
        one=c.post(f'/api/conversations/{conv["id"]}/messages',json=body)
        two=c.post(f'/api/conversations/{conv["id"]}/messages',json=body)
        assert one.status_code==two.status_code==202 and one.json()['id']==two.json()['id']
        assert c.post(f'/api/conversations/{conv["id"]}/messages',json=body|{'text':'不同文本'}).status_code==409
        assert c.post(f'/api/conversations/{conv["id"]}/messages',json=body|{'client_message_id':'two','base_requirement_version':3}).status_code==409


def send(conv,text='复盘2026-11-12的贵州茅台'):
    return conversation.submit(conv['id'],MessageCreate(text=text,client_message_id='m1',base_requirement_version=conv['requirement_version']))


def test_natural_language_creates_one_run(monkeypatch):
    conv=conversation.create(ConversationCreate(mode='demo'))
    msg=send(conv)
    monkeypatch.setattr(conversation,'interpret',lambda *_:{'intents':[{'type':'create_review','stocks':['贵州茅台'],'trade_date':'2026-11-12'}]})
    conversation.process_message(msg['id']);conversation.process_message(msg['id'])
    current=conversation.get(conv['id'])
    assert storage.get_run(current['run_id'])['request']['stocks']==['600519.SH']
    assert current['requirement_version']==1
    with storage.db() as c:assert c.execute('SELECT count(*) FROM runs').fetchone()[0]==1


def test_missing_date_clarifies_without_run(monkeypatch):
    conv=conversation.create(ConversationCreate(mode='demo'));msg=send(conv,'复盘贵州茅台')
    monkeypatch.setattr(conversation,'interpret',lambda *_:{'intents':[{'type':'create_review','stocks':['贵州茅台']}]})
    conversation.process_message(msg['id'])
    assert conversation.get(conv['id'])['status']=='awaiting_clarification'
    assert not conversation.get(conv['id'])['run_id']


def test_focus_queue_and_cancel(monkeypatch):
    row=storage.create_run(req(),'focus');conv=conversation.create(ConversationCreate(run_id=row['id']))
    msg=send(conv,'重点关注政策')
    monkeypatch.setattr(conversation,'interpret',lambda *_:{'intents':[{'type':'update_focus','focus':['政策']}]})
    conversation.process_message(msg['id'])
    from app.scheduler import consume_changes
    state=consume_changes(row['id'],agent_store.load(row['id']))
    assert state['focus']==['政策']
    current=conversation.get(conv['id'])
    msg2=conversation.submit(conv['id'],MessageCreate(text='取消这次分析',client_message_id='cancel',base_requirement_version=current['requirement_version']))
    monkeypatch.setattr(conversation,'interpret',lambda *_:{'intents':[{'type':'cancel_run'}]})
    conversation.process_message(msg2['id'])
    assert storage.get_run(row['id'])['cancel']==1


def test_report_answer_cannot_cite_other_report(monkeypatch):
    row=storage.create_run(req(),'report');execute(row['id'])
    conv=conversation.create(ConversationCreate(report_id=row['id']))
    msg=send(conv,'为什么')
    from app.agent_models import Answer
    monkeypatch.setattr(conversation,'model_json',lambda *a,**k:Answer(answer='无依据',citations=['foreign']))
    from app.providers import ProviderError
    with pytest.raises(ProviderError):conversation.answer_report(conv,msg,'为什么')


def test_time_without_zone_uses_beijing_and_draft_retains_stock():
    conv=conversation.create(ConversationCreate(mode='demo'))
    msg=send(conv)
    value=conversation.request_from_intent(conv,msg,{'stocks':['茅台'],'trade_date':'2026-11-12','as_of':'2026-11-12T18:00:00'})
    assert value.as_of.isoformat().endswith('+08:00')
    conversation.clarify(conv,msg,'哪一天？',{'stocks':['600519.SH']})
    value=conversation.request_from_intent(conversation.get(conv['id']),msg,{'trade_date':'2026-11-12'})
    assert value.stocks==['600519.SH']


def test_live_scheduler_drains_existing_plan_before_replanning(monkeypatch):
    from app import scheduler,engine
    row=storage.create_run(req()|{'mode':'live','sectors':[], 'research_depth':'quick'},'drain')
    actual=engine.build_data(row['id'],req())
    calls=[]
    def plan(rid,request,state,gaps,correction=''):
        calls.append(state['version'])
        if not state['tasks']:
            return {'base_plan_version':0,'actions':[
                add(),add('policy',kind='search_policy',source_id='official.columns'),
                add('market',kind='analyze_market',source_id='',depends_on=['data']),
                add('policy_analysis',kind='analyze_policy',source_id='',depends_on=['policy']),
                add('bull',kind='argue_bull',source_id='',depends_on=['market']),
                add('bear',kind='argue_bear',source_id='',depends_on=['market']),
                add('risk',kind='review_risk',source_id='',depends_on=['bull','bear'])]}
        return {'base_plan_version':state['version'],'actions':[{'type':'finish','reason':'全部职责已尝试'}]}
    def tool(rid,task,tasks,focus):
        if task['kind']=='fetch_market':return actual
        if task['kind']=='search_policy':return {'events':[],'markets':[],'evidence':[],'issues':[]}
        return {'outcome':'supported','claims':[{'subject':'600519.SH','text':'依据已有行情观察','evidence_ids':[actual['evidence'][0]['id']]}]}
    monkeypatch.setattr(scheduler,'propose',plan);monkeypatch.setattr(scheduler,'execute_task',tool)
    scheduler.execute(row['id'])
    assert calls==[0,1]
    assert storage.get_run(row['id'])['status'] in ('completed','partial')


def test_finish_cannot_skip_required_analyses():
    row=storage.create_run(req(),'early-finish');state=agent_store.load(row['id'])
    state=apply_plan(row['id'],state,{'base_plan_version':0,'actions':[add()]})
    state['tasks'][0].update(status='succeeded',result={'stocks':[{'id':'600519.SH'}]})
    with pytest.raises(ValueError,match='必须尝试'):
        apply_plan(row['id'],state,{'base_plan_version':1,'actions':[{'type':'finish','reason':'不想分析'}]})


def test_document_url_must_be_observed():
    row=storage.create_run(req(),'doc');state=agent_store.load(row['id'])
    with pytest.raises(ValueError,match='未出现在'):
        apply_plan(row['id'],state,{'base_plan_version':0,'actions':[add('doc',kind='fetch_document',source_id='',url='https://www.ndrc.gov.cn/invented.html')]})


def test_cancel_stops_controlled_sdk_process(monkeypatch):
    import sys,subprocess
    from app.ak_data import controlled_run
    row=storage.create_run(req(),'cancel-sdk');storage.update_run(row['id'],cancel=1)
    agent_store.ensure_budget('run:'+row['id'])
    with agent_store.context('run:'+row['id']):
        with pytest.raises(agent_store.BudgetExceeded,match='取消'):
            controlled_run([sys.executable,'-c','import time; time.sleep(20)'],input='',cwd=storage.ROOT,
                           env=None,capture_output=True,text=True,encoding='utf-8',timeout=30,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def test_message_budget_does_not_reset_by_reentering_context():
    agent_store.ensure_budget('chat:test',model=4,network=1)
    with agent_store.context('chat:test',message=True):
        for _ in range(3):agent_store.reserve('model')
        with pytest.raises(agent_store.BudgetExceeded):agent_store.reserve('model')

    with agent_store.context('chat:test',message=True):
        agent_store.reserve('model')
        with pytest.raises(agent_store.BudgetExceeded):agent_store.reserve('model')


def test_explicit_stock_source_never_automatically_falls_back(monkeypatch):
    from app.ak_adapter import AKShare
    from app.providers import ProviderError
    calls=[]
    def broken(fn,**kwargs):calls.append(fn);raise ProviderError('源失败')
    monkeypatch.setattr('app.ak_adapter.fetch',broken)
    with pytest.raises(ProviderError):AKShare().history('600519.SH','20261101','20261112',source='stock_zh_a_hist')
    assert calls==['stock_zh_a_hist']


def test_conflicting_prices_suppress_claims_and_remain_visible():
    from app.scheduler import materialize
    row=storage.create_run(req(),'conflict')
    ev={'id':'e1','kind':'行情','subject':'600519.SH','quality':'usable','value':{'close':100},'snapshot_id':'s1'}
    state=agent_store.load(row['id'])
    state['tasks']=[
        {'id':'one','kind':'fetch_market','subjects':['600519.SH'],'status':'succeeded','result':{'evidence':[ev]}},
        {'id':'two','kind':'fetch_market','subjects':['600519.SH'],'status':'succeeded','result':{'evidence':[ev|{'id':'e2','value':{'close':101}}]}},
        {'id':'market','kind':'analyze_market','subjects':['600519.SH'],'status':'succeeded','result':{'claims':[{'text':'收盘100','evidence_ids':['e1']}],'outcome':'supported'}}]
    for _ in range(2):
        report=materialize(row['id'],state)
        assert any('来源冲突' in x for x in report['gaps'])
        assert not report['market']['claims']
        assert all(e['quality']=='conflicting' for e in report['data']['evidence'])


@pytest.mark.parametrize('published,valid',[('2026-11-10 09:00:00',True),('2026-11-13 09:00:00',False)])
def test_document_tool_enforces_information_cutoff(monkeypatch,published,valid):
    from app.agent_tools import execute_task
    from app.providers import ProviderError
    from types import SimpleNamespace
    row=storage.create_run(req(),'document')
    html=f'<meta name="pubdate" content="{published}"><h1>测试政策</h1><div class="TRS_Editor">这是用于测试的官方正文内容。</div>'
    monkeypatch.setattr('app.agent_tools.fetch',lambda url:SimpleNamespace(text=html,headers={'content-type':'text/html'}))
    task={'kind':'fetch_document','url':'https://www.ndrc.gov.cn/test.html','depends_on':[]}
    if valid:
        result=execute_task(row['id'],task,[],[])
        assert result['evidence'][0]['quality']=='usable'
        assert '官方正文' in result['evidence'][0]['value']
    else:
        with pytest.raises(ProviderError,match='时点'):execute_task(row['id'],task,[],[])


def test_pending_focus_can_be_withdrawn_without_leaking_into_later_request(monkeypatch):
    row=storage.create_run(req(),'withdraw');conv=conversation.create(ConversationCreate(run_id=row['id']))
    msg=send(conv,'重点关注政策')
    monkeypatch.setattr(conversation,'interpret',lambda *_:{'intents':[{'type':'update_focus','focus':['政策']}]})
    conversation.process_message(msg['id'])
    with TestClient(app) as c:
        changes=c.get(f'/api/conversations/{conv["id"]}/changes').json()
        assert c.post(f'/api/conversations/{conv["id"]}/changes/{changes[0]["id"]}/withdraw').status_code==200
        updated=c.get(f'/api/conversations/{conv["id"]}').json()
        assert updated['requirements']['focus']==[] and updated['requirement_version']==2


def test_supplement_preserves_original_report(monkeypatch):
    row=storage.create_run(req(),'original');execute(row['id'])
    with storage.db() as c:before=c.execute('SELECT content FROM reports WHERE id=?',(row['id'],)).fetchone()[0]
    conv=conversation.create(ConversationCreate(report_id=row['id']));msg=send(conv,'补充分析政策')
    monkeypatch.setattr(conversation,'interpret',lambda *_:{'intents':[{'type':'supplement_analysis','focus':['政策']}]})
    conversation.process_message(msg['id'])
    updated=conversation.get(conv['id']);new=storage.get_run(updated['run_id'])
    assert new['id']!=row['id'] and new['request']['parent_run_id']==row['id']
    with storage.db() as c:assert c.execute('SELECT content FROM reports WHERE id=?',(row['id'],)).fetchone()[0]==before


def test_resume_budget_does_not_charge_offline_hour():
    import time
    row=storage.create_run(req(),'offline');agent_store.ensure_budget('run:'+row['id'])
    with storage.db() as c:c.execute('UPDATE request_budgets SET elapsed=20,started=? WHERE owner=?',(time.time()-3600,'run:'+row['id']))
    agent_store.begin(row['id']);agent_store.end(row['id'])
    elapsed=agent_store.budget('run:'+row['id'])['elapsed']
    assert 22 <= elapsed < 25


def test_iso_close_time_and_named_ma_are_valid_but_other_times_rejected():
    from app.engine import unsupported_numbers
    source=storage.dumps({'close_at':'2026-11-12T05:00:00+08:00','ma20':1400})
    assert not unsupported_numbers('05:00 收盘，20日均线为1400元',source)
    assert unsupported_numbers('16:00 收盘',source)==['16']


def test_failed_analysis_revision_can_retry_once():
    row = storage.create_run(req(), 'retry-analysis')
    state = apply_plan(row['id'], agent_store.load(row['id']), {'base_plan_version': 0, 'actions': [add(), add('analysis', kind='analyze_market', source_id='', depends_on=['data'])]})
    state['tasks'][0]['status'] = 'succeeded'
    state['tasks'][1].update(status='failed', error='引用校验失败')
    revision = {'type': 'revise_task', 'task_id': 'analysis', 'task': add('retry', kind='analyze_market', source_id='', depends_on=['data'])['task'], 'reason': '修复引用格式'}
    state = apply_plan(row['id'], state, {'base_plan_version': 1, 'actions': [revision]})
    assert state['tasks'][-1]['status'] == 'pending'
    assert state['tasks'][-1]['result'] is None
    state['tasks'][-1].update(status='failed', error='再次失败')
    revision.update(task_id='retry', task=revision['task'] | {'id': 'third'})
    with pytest.raises(ValueError):
        apply_plan(row['id'], state, {'base_plan_version': 2, 'actions': [revision]})


def test_materialize_preserves_analysis_failure():
    from app.scheduler import materialize
    row = storage.create_run(req(), 'failed-note')
    state = agent_store.load(row['id'])
    state['tasks'] = [{'id': 'policy', 'kind': 'analyze_policy', 'status': 'failed', 'error': '引用日期校验失败'}]
    assert materialize(row['id'], state)['policy']['analysis']['note'] == '引用日期校验失败'


def test_compact_market_date_supports_display_date():
    from app.engine import unsupported_numbers
    cited = json.dumps([{'trade_date': '20261112', 'close_at': '2026-11-13T05:00:00+08:00'}])
    assert unsupported_numbers('美股2026-11-12交易日收盘', cited) == []
    assert unsupported_numbers('成交量987654321', cited)


def test_announcements_scoped_failure_does_not_hide_other_stock(monkeypatch):
    from app.ak_adapter import AKShare
    from app.providers import ProviderError
    calls = []
    def fetch(fn, **kwargs):
        calls.append((fn, kwargs))
        if kwargs['security'] == '600519': raise ProviderError('暂时不可用')
        return [{'代码':'000858','公告日期':'2026-11-12','名称':'五粮液','公告标题':'公告','网址':'https://example.com'}]
    monkeypatch.setattr('app.ak_adapter.fetch', fetch)
    rows, issues = AKShare().announcements(['600519.SH','000858.SZ'], '2026-11-06', '2026-11-13')
    assert len(calls) == 2 and len(rows) == 1
    assert all(fn == 'stock_individual_notice_report' and kw['begin_date'] == '2026-11-06' for fn, kw in calls)
    assert any('600519.SH' in x for x in issues)


def test_announcements_empty_is_not_a_gap(monkeypatch):
    from app.ak_adapter import AKShare
    monkeypatch.setattr('app.ak_adapter.fetch', lambda *a, **kw: [])
    assert AKShare().announcements(['600519.SH'], '2026-11-06', '2026-11-13') == ([], [])


@pytest.mark.parametrize('confirmed_empty', [True, False])
def test_akshare_empty_notice_bug_requires_verified_response(monkeypatch, capsys, confirmed_empty):
    import io, sys, types, requests
    from app import ak_runner
    def send(session, prepared, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'data': {'total_hits': 0 if confirmed_empty else 1, 'list': []}}).encode()
        return response
    monkeypatch.setattr(requests.sessions.Session, 'send', send)
    def sdk(**kwargs):
        requests.get('https://np-anotice-stock.eastmoney.com/api/security/ann', params={'stock_list':'600519'})
        raise KeyError('代码')
    monkeypatch.setitem(sys.modules, 'akshare', types.SimpleNamespace(stock_individual_notice_report=sdk))
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'function':'stock_individual_notice_report'})))
    if confirmed_empty:
        ak_runner.main()
        assert json.loads(capsys.readouterr().out) == []
    else:
        with pytest.raises(KeyError): ak_runner.main()


@pytest.mark.parametrize('explicit', [False, True])
def test_sina_percentage_enrichment_respects_explicit_source(monkeypatch, explicit):
    from app.ak_adapter import AKShare
    from app.providers import ProviderError
    calls = []
    def fetch(fn, **kw):
        calls.append((fn, kw))
        if fn == 'stock_zh_a_daily':
            return [{'date': '2026-11-13', 'open': 10, 'high': 12, 'low': 9, 'close': 11, 'volume': 100, 'amount': 1100}]
        if kw['start_date'] == '20261101':
            return [{'日期': '2026-11-13', '收盘': 11, '涨跌幅': 2.5}]
        raise ProviderError('主来源不可用')
    monkeypatch.setattr('app.ak_adapter.fetch', fetch)
    rows, _, source, notes = AKShare().history('600519.SH', '20260101', '20261113', source='stock_zh_a_daily' if explicit else None)
    if explicit:
        assert all(fn == 'stock_zh_a_daily' for fn, _ in calls)
        assert rows[-1]['pct_chg'] is None
    else:
        assert rows[-1]['pct_chg'] == 2.5
        assert '同日收盘核对' in source


@pytest.mark.parametrize('quote_date,quote_close,expected', [('2026-11-12',10,2),('2026-11-11',10,None),('2026-11-12',11,None)])
def test_materialize_keeps_verified_percentage(quote_date, quote_close, expected):
    from app.scheduler import materialize
    row = storage.create_run(req(), 'merge-percentage')
    state = agent_store.load(row['id'])
    quote = {'id':'em-evidence','kind':'行情','subject':'600519.SH','quality':'usable','source':'东方财富',
             'value':{'date':quote_date,'close':quote_close,'pct_change':2}}
    stock = {'id':'600519.SH','available':True,'close':10,'pct_change':None}
    state['tasks'] = [
        {'id':'em','kind':'fetch_market','subjects':['600519.SH'],'status':'succeeded','result':{'evidence':[quote]}},
        {'id':'sina','kind':'fetch_market','subjects':['600519.SH'],'status':'succeeded',
         'result':{'stocks':[stock],'issues':['600519.SH：备用来源未提供交易所当日涨跌幅，该字段显示不可用']}}]
    result = materialize(row['id'], state)
    assert result['data']['stocks'][0]['pct_change'] == expected
    assert stock['pct_change'] is None
    if expected is not None:
        assert result['data']['stocks'][0]['pct_change_source'] == 'em-evidence'
        assert not any('涨跌幅' in issue for issue in result['data']['issues'])
