import json
import pytest
from app import storage, conversation
from app.agent_models import ConversationCreate, MessageCreate


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    monkeypatch.setenv('TRADINGAGENT_NO_WORKER', '1')
    storage.init()


def message(conv, text, key):
    return conversation.submit(conv['id'], MessageCreate(text=text, client_message_id=key,
                                base_requirement_version=conv['requirement_version']))


def test_message_idempotency_includes_policy_and_reply():
    conv = conversation.create(ConversationCreate(mode='demo'))
    request = MessageCreate(text='继续', client_message_id='retry', base_requirement_version=0)
    first = conversation.submit(conv['id'], request)
    assert conversation.submit(conv['id'], request)['id'] == first['id']
    for changes in ({'research_policy':'local_only'}, {'reply_to':'another'}, {'base_requirement_version':1}):
        with pytest.raises(ValueError, match='不同请求'):
            conversation.submit(conv['id'], request.model_copy(update=changes))


def test_cancelled_message_cannot_publish_late_artifact():
    from app.main import cancel_turn
    from app.research_artifacts import publish
    from app.providers import ProviderError
    conv = conversation.create(ConversationCreate(mode='demo'))
    msg = message(conv, '取消测试', 'cancel')
    conversation.clarify(conv, msg, '选择哪只股票？')
    cancel_turn(msg['turn_id'])
    assert conversation.get(conv['id'])['pending'] is None
    msg.update(answer='迟到的回答', citations=[])
    with pytest.raises(ProviderError, match='取消'):
        publish(conv, msg, {'id':'source', 'analysis':{}})
    conversation.update_message(msg, 'completed')
    assert conversation.messages(conv['id'])[0]['status'] == 'cancelled'
    with storage.db() as c:
        assert c.execute('SELECT count(*) FROM research_artifacts').fetchone()[0] == 0


def test_restart_recovers_committed_artifact_without_another_model_call():
    from app.research_artifacts import publish
    from app import agent_store
    conv = conversation.create(ConversationCreate(mode='demo'))
    msg = message(conv,'已有回答','recover')
    conversation.update_message(msg,'interpreting')
    msg.update(answer='已保存的回答',citations=[])
    publish(conv,msg,{'id':'source','analysis':{}})
    before=agent_store.budget('turn:'+msg['turn_id'])
    conversation.recover_messages()
    conversation.recover_messages()
    saved=conversation.messages(conv['id'])[0]
    assert saved['status']=='completed'
    assert saved['answer']=='已保存的回答'
    assert saved['artifact_id']==msg['artifact_id']
    assert agent_store.budget('turn:'+msg['turn_id'])['model']==before['model']


def test_complete_close_answer_is_not_partial_for_scope_caveat():
    from app.research_artifacts import publish
    from app.research_coverage import assess
    from app.main import get_report
    conv=conversation.create(ConversationCreate(mode='demo'))
    msg=message(conv,'收盘价多少','close')
    req={'stocks':['600519.SH'],'trade_date':'2026-11-13','as_of':'2026-11-13T18:00:00+08:00'}
    evidence=[{'id':'e','subject':'600519.SH','kind':'行情','quality':'usable','value':{'date':'2026-11-13','close':10}}]
    coverage=assess(msg['text'],req,evidence)
    msg.update(answer='收盘价为10元',citations=['e'],limitations=['仅为当日收盘价，不构成趋势判断'],coverage=coverage,gaps=coverage['gaps'])
    publish(conv,msg,{'id':'source','analysis':{},'evidence':evidence})
    assert get_report(msg['artifact_id'])['status']=='completed'


def test_turn_crash_keeps_elapsed_and_does_not_charge_all_offline_time():
    from app import agent_store
    import time
    owner='turn:crashed'
    agent_store.ensure_budget(owner,model=6,network=2)
    with storage.db() as c:
        c.execute('UPDATE request_budgets SET elapsed=40,model=3,started=? WHERE owner=?',(time.time()-3600,owner))
    with agent_store.context(owner,message=True): pass
    budget=agent_store.budget(owner)
    assert 42 <= budget['elapsed'] < 45
    assert budget['model']==3


def test_cursor_reconnect_replays_changed_messages_without_foreign_events():
    from app.main import sync_conversation
    conv = conversation.create(ConversationCreate(mode='demo'))
    other = conversation.create(ConversationCreate(mode='demo'))
    msg = message(conv,'断线前的问题','sync')
    first = sync_conversation(conv['id'],after_cursor=0,limit=100)
    conversation.update_message(msg,'interpreting')
    message(other,'另一会话不能混入','foreign')
    msg['answer']='断线期间完成的回答'
    conversation.update_message(msg,'completed')
    delta=sync_conversation(conv['id'],after_cursor=first['cursor'],limit=100)
    assert [m['status'] for m in delta['messages']] == ['interpreting','completed']
    assert all(m['id']==msg['id'] for m in delta['messages'])
    again=sync_conversation(conv['id'],after_cursor=delta['cursor'],limit=100)
    assert again['messages']==[]
    assert again['cursor']==delta['cursor']


def test_resume_failed_turn_keeps_identity_budget_and_is_idempotent():
    from app.main import resume_turn
    from app import agent_store
    conv=conversation.create(ConversationCreate(mode='demo'))
    msg=message(conv,'临时失败','resume')
    with storage.db() as c: c.execute('UPDATE request_budgets SET model=2 WHERE owner=?',('turn:'+msg['turn_id'],))
    conversation.update_message(msg,'failed')
    assert resume_turn(msg['turn_id'])['status']=='received'
    assert resume_turn(msg['turn_id'])['status']=='received'
    assert len(conversation.messages(conv['id']))==1
    assert agent_store.budget('turn:'+msg['turn_id'])['model']==2


def test_resume_exhausted_turn_is_rejected_without_reset():
    from app.main import resume_turn
    from fastapi import HTTPException
    conv=conversation.create(ConversationCreate(mode='demo'))
    msg=message(conv,'预算耗尽','exhausted')
    with storage.db() as c: c.execute('UPDATE request_budgets SET model=6 WHERE owner=?',('turn:'+msg['turn_id'],))
    conversation.update_message(msg,'failed')
    with pytest.raises(HTTPException) as error: resume_turn(msg['turn_id'])
    assert error.value.status_code==409
    assert conversation.messages(conv['id'])[0]['status']=='failed'


def test_continue_resumes_failed_turn_without_interpreting_again(monkeypatch):
    conv=conversation.create(ConversationCreate(mode='demo'))
    failed=message(conv,'暂时失败','failed')
    conversation.update_message(failed,'failed')
    current=message(conv,'继续','continue')
    monkeypatch.setattr(conversation,'model_json',lambda *a,**k:pytest.fail('continue must restore known failed request'))
    conversation.process_message(current['id'])
    rows=conversation.messages(conv['id'])
    assert rows[0]['status']=='received'
    assert rows[1]['status']=='completed'
    assert '剩余预算' in rows[1]['answer']


def test_comparison_filters_are_local_immutable_and_missing_last():
    from app.research_results import filtered
    original = {'id':'original', 'version':1, 'rows':[
        {'id':'a', 'amount':10, 'pct_change':2},
        {'id':'b', 'amount':None, 'pct_change':3},
        {'id':'c', 'amount':20, 'pct_change':-1},
        {'id':'d', 'amount':30, 'pct_change':None}]}
    result = filtered(original, only_up=True)
    assert [r['id'] for r in result['rows']] == ['a','b']
    assert len(original['rows']) == 4
    assert [r['id'] for r in filtered(original, descending=False)['rows']] == ['a','c','d','b']
    with pytest.raises(ValueError): filtered(original, field='unsupported')


def test_ambiguous_pronoun_stops_before_model(monkeypatch):
    conv = conversation.create(ConversationCreate(mode='demo'))
    conv['requirements'] = {'stocks':['600519.SH','000858.SZ']}
    msg = message(conv, '它为什么上涨', 'ambiguous')
    monkeypatch.setattr(conversation, 'model_json', lambda *a, **k: pytest.fail('ambiguous scope must not call model'))
    parsed = conversation.interpret(conv, msg)
    assert '哪只股票' in parsed['intents'][0]['clarification']


def test_model_cannot_add_unmentioned_stock(monkeypatch):
    from app.agent_models import Interpretation
    conv = conversation.create(ConversationCreate(mode='demo'))
    msg = message(conv, '复盘2026-11-13贵州茅台', 'scope')
    monkeypatch.setattr(conversation, 'model_json', lambda *a, **k: Interpretation(intents=[
        {'type':'create_review','stocks':['600519.SH','000858.SZ'],'trade_date':'2026-11-13'}]))
    parsed = conversation.interpret(conv, msg)
    assert parsed['intents'][0]['clarification']


def test_catalog_normalizes_spaced_names_and_prioritizes_explicit_match(monkeypatch):
    rows = [{'id':f'{i:06}.SZ','name':f'公司{i}信息','kind':'stock'} for i in range(80)]
    rows.append({'id':'000858.SZ','name':'五 粮 液','kind':'stock'})
    monkeypatch.setattr(conversation, 'catalog', lambda mode:rows)
    matches = conversation.candidates({'mode':'live','requirements':{}}, '和五粮液比较，信息截止时间不变')
    assert matches == [rows[-1]]
    assert conversation.resolve(['五粮液'],'stock','live') == ['000858.SZ']


def test_new_research_does_not_inherit_old_supplement_question():
    conv = conversation.create(ConversationCreate(mode='demo'))
    conv['requirements'] = {'stocks':['600519.SH'], 'sectors':[], 'trade_date':'2026-11-13',
                            'as_of':'2026-11-13T18:00:00+08:00','research_question':'查旧公告'}
    msg = message(conv, '和五粮液比较量价', 'compare')
    request = conversation.request_from_intent(conv, msg, {'stocks':['600519.SH','000858.SZ']})
    assert request.research_question == ''
    assert request.stocks == ['600519.SH','000858.SZ']


def test_empty_supplement_cannot_report_completed(monkeypatch):
    from app import scheduler
    from app.models import ReviewInput
    req = ReviewInput(trade_date='2026-11-13', as_of='2026-11-13T18:00:00+08:00',
                      stocks=['600519.SH'], mode='demo', execution_mode='autonomous',
                      research_question='查询行情').model_dump(mode='json')
    run = storage.create_run(req, 'empty')
    monkeypatch.setattr(scheduler,'demo_plan',lambda req,state,gaps:{'base_plan_version':state['version'],
        'actions':[{'type':'finish','reason':'没有成功执行工具'}]})
    scheduler.execute(run['id'])
    saved = storage.get_run(run['id'])
    assert saved['status'] == 'failed'
    assert '没有取得可核验资料' in saved['error']


def test_comparison_markdown_preserves_order_and_columns(tmp_path):
    from app.reporting import export_report
    report = {'artifact_type':'answer', 'version':2, 'source_report_id':'original',
              'request':{'as_of':'2026-11-13T18:00:00+08:00','mode':'demo'},
              'answer':'比较结果', 'summary':[], 'evidence':[], 'limitations':[], 'stocks':[],
              'result_set':{'columns':{'name':'股票','amount':'成交额','close':'收盘'}, 'filters':{},
                            'rows':[{'name':'乙','amount':20,'close':2,'evidence_ids':[]},
                                    {'name':'甲','amount':10,'close':None,'evidence_ids':[]}]}}
    path = export_report(report, {'scope':'full','charts':False,'format':'md'}, tmp_path/'result')
    text = path.read_text('utf-8')
    assert '| --- | --- | --- |' in text
    assert text.index('| 乙') < text.index('| 甲')
    assert '不可用' in text


def test_comparison_restore_keeps_original_and_filtered_revisions():
    import copy
    from app.main import get_report
    from app.research_results import from_report, operate
    from app.research_artifacts import publish
    conv=report_conversation()
    report=get_report(conv['report_id'])
    report['stocks'][0].update(pct_change=2,amount=10)
    second=copy.deepcopy(report['stocks'][0]);second.update(id='000858.SZ',name='五粮液',pct_change=None,amount=20)
    report['stocks'].append(second)
    with storage.db() as c: c.execute('UPDATE reports SET content=? WHERE id=?',(storage.dumps(report),report['id']))
    original=message(conv,'比较两只股票','original')
    original.update(answer='比较表',citations=[],result_set=from_report(report))
    publish(conv,original,report);conversation.update_message(original,'completed')
    filtered=message(conv,'只保留上涨','filtered')
    operate(conv,filtered,{'only_up':True});conversation.update_message(filtered,'completed')
    assert len(filtered['result_set']['rows'])==1
    restored=message(conv,'恢复原表','restored')
    operate(conv,restored,{'restore':True});conversation.update_message(restored,'completed')
    assert [r['id'] for r in restored['result_set']['rows']]==['600519.SH','000858.SZ']
    assert len(get_report(filtered['artifact_id'])['result_set']['rows'])==1
    assert get_report(restored['artifact_id'])['parent_artifact_id']==filtered['artifact_id']
    assert len(get_report(original['artifact_id'])['result_set']['rows'])==2


def test_history_reads_latest_prior_messages_not_first_200():
    from app.research_context import build_context
    conv = conversation.create(ConversationCreate(mode='demo'))
    for i in range(240):
        msg = message(conv, f'第{i}轮', str(i))
        msg['answer'] = f'回答{i}'
        conversation.update_message(msg, 'completed')
    current = message(conv, '它的依据呢', 'current')
    message(conv, '未来消息不得提前读取', 'future')
    ctx = build_context(conv, current)
    assert ctx['recent_messages'][-1]['text'] == '第239轮'
    assert len(ctx['recent_messages']) <= 12
    assert '未来消息不得提前读取' not in storage.dumps(ctx)


def test_reply_to_retrieves_old_answer_with_citations():
    from app.research_context import build_context
    conv = conversation.create(ConversationCreate(mode='demo'))
    first = message(conv, '早期问题', 'first')
    first.update(answer='第二个依据是公告', citations=['policy-evidence'])
    conversation.update_message(first, 'completed')
    for i in range(15): message(conv, f'插入{i}', str(i))
    current = message(conv, '第二个依据呢', 'last'); current['reply_to'] = first['id']
    ctx = build_context(conv, current)
    assert ctx['referenced_message']['citations'] == ['policy-evidence']


def test_retrieval_finds_evidence_after_first_30():
    from app.research_context import retrieve
    rows = [{'id':str(i), 'subject':'600519.SH','kind':'行情','title':'行情','value':{'close':10},'quality':'usable'} for i in range(40)]
    rows.append({'id':'announcement','subject':'600519.SH','kind':'公告','title':'分红公告正文','value':'分红方案已公布','quality':'usable'})
    result = retrieve(rows, '分红公告是什么', ['600519.SH'])
    assert result[0]['id'] == 'announcement'


def test_indexed_retrieval_is_scoped_idempotent_and_excludes_rejected_evidence():
    from app.evidence_index import index_report, search
    rows = [{'id':str(i),'subject':'600519.SH','kind':'行情','title':'行情','value':{'close':10},'quality':'usable'} for i in range(80)]
    rows.append({'id':'dividend','subject':'600519.SH','kind':'公告','title':'现金分红方案','value':'每股分红','quality':'limited'})
    index_report('bound', rows)
    index_report('bound', rows)
    index_report('foreign', [{'id':'foreign','subject':'600519.SH','kind':'公告','title':'现金分红方案','value':'不能泄露','quality':'usable'}])
    index_report('rejected', [{'id':'future','subject':'600519.SH','kind':'公告','title':'现金分红方案','value':'未来文件','quality':'excluded'}])
    found = search(['bound','rejected'],'现金分红方案',['600519.SH'])
    assert found[0]['id'] == 'dividend'
    assert not {'foreign','future'} & {e['id'] for e in found}
    assert search(['bound'],'现金分红方案',['000858.SZ']) == []
    with storage.db() as c:
        assert c.execute("SELECT count(*) FROM research_evidence WHERE report_id='bound'").fetchone()[0] == len(rows)


def test_explicit_reply_restores_text_beyond_summary_limit():
    from app.research_context import build_context
    conv = conversation.create(ConversationCreate(mode='demo'))
    first = message(conv, '原问题', 'long')
    first['answer'] = '背景。' * 400 + '最后的原始依据不能丢失。'
    conversation.update_message(first, 'completed')
    current = message(conv, '最后的依据是什么', 'reply')
    current['reply_to'] = first['id']
    context = build_context(conv, current)
    assert context['referenced_message']['answer'] == first['answer']


def test_old_user_constraint_survives_summary_with_source_reference():
    from app.research_context import build_context
    conv=conversation.create(ConversationCreate(mode='demo'))
    first=message(conv,'不要扩展行业，只分析我指定的股票','constraint')
    conversation.update_message(first,'completed')
    for i in range(30):
        msg=message(conv,f'第{i}个问题',str(i)); conversation.update_message(msg,'completed')
    current=message(conv,'继续刚才的分析','current')
    context=build_context(conv,current)
    assert any(x['message_id']==first['id'] and '不要扩展行业' in x['text'] for x in context['summary']['constraints'])
    assert context['summary']['context_version']==conv['requirement_version']


def test_v3_upgrade_backs_up_without_rewriting_old_reports():
    import sqlite3
    with storage.db() as c:
        c.execute('PRAGMA user_version=2')
        c.execute('INSERT INTO reports VALUES(?,?,?)', ('legacy','legacy','{"untouched":true}'))
    storage.init()
    backups = list((storage.DATA/'backups').glob('pre-v3-*.db'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as c:
        assert c.execute('SELECT content FROM reports WHERE id=?',('legacy',)).fetchone()[0] == '{"untouched":true}'
    storage.init()
    assert len(list((storage.DATA/'backups').glob('pre-v3-*.db'))) == 1
    with storage.db() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0] == 3
        assert c.execute('SELECT content FROM reports WHERE id=?',('legacy',)).fetchone()[0] == '{"untouched":true}'


def test_context_does_not_read_foreign_reply():
    from app.research_context import build_context
    first = conversation.create(ConversationCreate(mode='demo'))
    other = conversation.create(ConversationCreate(mode='demo'))
    secret = message(other, '其他会话', 'one')
    msg = message(first, '引用', 'two'); msg['reply_to'] = secret['id']
    with pytest.raises(ValueError): build_context(first, msg)


def report_conversation():
    from app.models import ReviewInput
    from app.scheduler import execute
    req = ReviewInput(trade_date='2026-11-13',as_of='2026-11-13T18:00:00+08:00',
                      stocks=['600519.SH'],mode='demo',execution_mode='autonomous').model_dump(mode='json')
    run = storage.create_run(req, 'baseline')
    execute(run['id'])
    return conversation.create(ConversationCreate(report_id=run['id']))


def test_answer_uses_history_and_publishes_immutable_artifact(monkeypatch):
    from app.agent_models import Answer
    conv = report_conversation()
    first = message(conv,'请解释量比','one')
    first['answer'] = '这里的量比是相对于前五个交易日均量。'
    conversation.update_message(first, 'completed')
    msg = message(conv,'刚才的基准是什么','two')
    captured = []
    def answer(*args, **kwargs):
        captured.append(args[4])
        return Answer(answer='当前引用证据不足以说明该基准。',limitations=['资料不足'])
    monkeypatch.setattr(conversation,'model_json',answer)
    conversation.answer_report(conv,msg,msg['text'])
    assert captured[0]['context']['recent_messages'][0]['answer'] == first['answer']
    assert msg['artifact_id'] == msg['report_id']
    from app.main import get_report
    saved = get_report(msg['artifact_id'])
    assert saved['source_report_id'] == conv['report_id']
    assert saved['artifact_type'] == 'answer'


def test_narrow_question_rejects_unrequested_metric_then_repairs(monkeypatch):
    from app.agent_models import Answer
    from app.main import get_report
    conv=report_conversation()
    report=get_report(conv['report_id'])
    eid=next(e['id'] for e in report['evidence'] if e['kind']=='行情')
    msg=message(conv,'贵州茅台的收盘价依据是什么','narrow')
    calls=[]
    def answer(*args,**kwargs):
        calls.append(args[4])
        text='成交额可从行情来源核查' if len(calls)==1 else '收盘价可从行情来源核查'
        return Answer(answer=text,citations=[eid],claims=[{'subject':'600519.SH','text':text,'evidence_ids':[eid]}])
    monkeypatch.setattr(conversation,'model_json',answer)
    conversation.answer_report(conv,msg,msg['text'])
    assert len(calls)==2
    assert '未询问' in calls[1]['validation_error']
    assert '成交额' not in msg['answer']


def test_local_only_never_creates_supplement(monkeypatch):
    from app.agent_models import Answer
    conv = report_conversation(); conv['research_policy'] = 'local_only'
    msg = message(conv,'公告呢','one')
    monkeypatch.setattr(conversation,'model_json',lambda *a,**k:Answer(answer='引用证据不足。',research_needed=True,research_reason='查询公告'))
    conversation.answer_report(conv,msg,msg['text'])
    assert not msg.get('supplement_run_id')
    with storage.db() as c: assert c.execute('SELECT count(*) FROM runs').fetchone()[0] == 1


def test_supplement_finishes_in_original_message(monkeypatch):
    from app.agent_models import Answer
    from app.scheduler import execute
    from app import agent_store
    conv = report_conversation()
    msg = message(conv,'检查公告','one')
    conversation.start_supplement(conv,msg,msg['text'],'需要公告证据')
    rid = msg['supplement_run_id']
    execute(rid)
    tasks = agent_store.load(rid)['tasks']
    assert any(t['kind']=='search_announcement' for t in tasks)
    assert not any(t['kind'] in ('argue_bull','argue_bear','review_risk') for t in tasks)
    monkeypatch.setattr(conversation,'model_json',lambda *a,**k:Answer(answer='公告正文证据不足，不能判断影响。',limitations=['正文不足']))
    conversation.finish_supplement(msg['id'])
    result = conversation.messages(conv['id'])[-1]
    assert result['status'] == 'completed'
    assert result['artifact_id']
    conversation.finish_supplement(msg['id'])
    with storage.db() as c:
        assert c.execute('SELECT count(*) FROM research_artifacts').fetchone()[0] == 1


def test_empty_announcement_has_query_evidence(monkeypatch):
    from app.agent_tools import execute_task
    conv = report_conversation()
    run = storage.get_run(conv['run_id'])
    with storage.db() as c:
        request = run['request'] | {'mode':'live'}
        c.execute('UPDATE runs SET request=? WHERE id=?',(storage.dumps(request),run['id']))
    monkeypatch.setattr('app.agent_tools.collect_announcements',lambda request:([], []))
    result = execute_task(run['id'],{'id':'ann','kind':'search_announcement','source_id':'akshare.em.announcements'},[],[])
    coverage = result['evidence'][-1]
    assert coverage['value']['availability'] == 'no_records'
    assert coverage['value']['record_count'] == 0
    assert coverage['quality'] == 'usable'


def test_turn_budget_cannot_reset_across_clarification():
    from app import agent_store
    owner = 'turn:one'
    agent_store.ensure_budget(owner,model=6,network=2)
    with agent_store.context(owner,message=True):
        for _ in range(4): agent_store.reserve('model')
    with agent_store.context(owner,message=True):
        for _ in range(2): agent_store.reserve('model')
        with pytest.raises(agent_store.BudgetExceeded): agent_store.reserve('model')
