import copy
import json
import pytest
from app import storage, agent_store, scheduler
from app.models import ReviewInput
from app.investment_research import depth, baseline, artifact, validate_output, next_plan, KINDS
from app.sector_research import index_metrics


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    monkeypatch.setenv('TRADINGAGENT_NO_WORKER', '1')
    storage.init()


def request(**changes):
    return ReviewInput(trade_date='2026-11-12', as_of='2026-11-12T18:00:00+08:00',
                       stocks=['600519.SH'], mode='demo', execution_mode='autonomous',
                       **changes).model_dump(mode='json')


def evidence(**changes):
    return {'id': 'e1', 'subject': '600519.SH', 'kind': '行情', 'quality': 'usable',
            'event_time': '2026-11-12T15:00:00+08:00', 'value': {'date': '2026-11-12', 'close': 100, 'amount': None},
            'units': '价格 元', 'basis': '同日未复权', **changes}


@pytest.mark.parametrize('question,explicit,expected',[
    ('收盘价是多少','auto','quick'),('多空怎么看','auto','deep'),('值得关注吗','auto','deep'),
    ('多空怎么看','quick','quick'),('收盘价是多少','deep','deep'),('这条公告会改判吗','auto','deep'),
    ('只问多空怎么看','auto','deep'),('快速回答多空怎么看','auto','quick')])
def test_depth(question, explicit, expected):
    assert depth(question,explicit)==expected


def test_baseline_time_scope_and_field_conflict():
    a=evidence();b=evidence(id='e2',value={'date':'2026-11-12','close':103,'amount':500})
    future=evidence(id='future',event_time='2026-11-13T12:00:00+08:00')
    other=evidence(id='other',subject='000858.SZ')
    result=baseline(request(),[a,b,future,other])
    assert len(result['excluded'])==2
    assert all(x['value']['close'] is None for x in result['evidence'])
    assert result['evidence'][1]['value']['amount']==500
    assert a['value']['close']==100


def test_identical_reprints_do_not_count_as_independent_sources():
    document={'body_text':'同一份官方文件正文','published_at':'2026-11-12T09:00:00+08:00'}
    rows=[evidence(id='one',kind='政策',source='original',document=document,value='同一份官方文件正文'),
          evidence(id='two',kind='政策',source='reprint',document=document,value='同一份官方文件正文')]
    selected=baseline(request(),rows)['evidence']
    assert selected[0]['independence_group']==selected[1]['independence_group']


def test_document_publication_and_quote_date_override_fetch_time():
    future=evidence(id='policy',kind='政策',document={'published_at':'2026-11-13T12:00:00+08:00','body_text':'未来文件'})
    stale=evidence(id='stale',value={'date':'2026-11-11','close':100})
    result=baseline(request(),[future,stale])
    assert not result['evidence'] and len(result['excluded'])==2


def test_full_demo_runs_required_stages_and_exports():
    from app.reporting import blocks
    row=storage.create_run(request(),'v4demo');scheduler.execute(row['id'])
    state=agent_store.load(row['id'])
    assert KINDS <= {t['kind'] for t in state['tasks'] if t['status']=='succeeded'},state
    assert state['rounds']<=16
    with storage.db() as c: report=json.loads(c.execute('SELECT content FROM reports WHERE id=?',(row['id'],)).fetchone()[0])
    research=report['investment_research']
    assert research['execution_status']=='completed'
    assert research['adjudication']['stance']=='中性观察'
    assert research['rebuttals']
    assert research['risk_review']['approved']
    assert agent_store.budget('run:'+row['id'])['model_limit']==120
    assert '投资论证与裁决' in str(blocks(report))


def test_risk_rejection_and_incomplete_do_not_publish_positive_stance():
    state={'tasks':[{'kind':'adjudicate_theses','status':'succeeded','result':{'stance':'偏积极','sufficiency':'充分','accepted_thesis_ids':['bull1']}}]}
    result=artifact(request(),state)
    assert result['adjudication']['stance']=='暂不形成判断'
    assert result['execution_status']=='partial'
    assert result['research_status']=='insufficient'


def test_cross_object_and_fabricated_number_rejected():
    from app.investment_research import validate_text
    with pytest.raises(ValueError):validate_text('收盘价9999元',['e1'],[evidence()],'600519.SH')
    with pytest.raises(ValueError):validate_text('观察价格',['e1'],[evidence()],'000858.SZ')
    with pytest.raises(ValueError):validate_text('仓位100%',['e1'],[evidence()],'600519.SH')


def test_future_condition_is_not_asserted_as_current_fact():
    from app.investment_research import validate_text
    e=evidence(value={'close':100,'ma20':90})
    validate_text('未来收盘价低于MA20时重新核验趋势',['e1'],[e],conditional=True)
    with pytest.raises(ValueError,match='高低比较'):validate_text('收盘价低于MA20',['e1'],[e])
    with pytest.raises(ValueError,match='数字'):validate_text('未来收盘价低于999时买入',['e1'],[e],conditional=True)


def test_benchmark_name_is_not_a_fabricated_measurement():
    from app.investment_research import validate_text
    validate_text('缺少沪深300同区间资料，不能判断相对强弱',['e1'],[evidence()])
    with pytest.raises(ValueError,match='数字'):validate_text('沪深300涨幅999%',['e1'],[evidence()])


def test_stock_relation_does_not_compare_to_global_index_close():
    from app.investment_research import validate_text
    stock=evidence(value={'close':100,'ma20':90})
    index=evidence(id='global',subject='global',kind='外围',value={'close':5000})
    validate_text('收盘价高于MA20',['e1','global'],[stock,index])
    with pytest.raises(ValueError,match='高低比较'):validate_text('收盘价低于MA20',['e1','global'],[stock,index])


def test_adjudication_must_resolve_every_dispute():
    prior={'bull':{'theses':[{'thesis_id':'b1'}]},'debate':{'disputes':[{'dispute_id':'d1'}]}}
    value={'stance':'暂不形成判断','sufficiency':'不足','accepted_thesis_ids':[], 'rejected_thesis_ids':[],
           'resolutions':[], 'decision_basis':'不足', 'evidence_ids':[], 'limitations':[],
           'observation_conditions':[], 'invalidation_conditions':[]}
    with pytest.raises(ValueError,match='争议ID'):validate_output('adjudicate_theses',value,[evidence()],prior,request())


def test_local_only_rejects_fetch_at_executor_boundary():
    row=storage.create_run(request(data_policy='local_only'),'local')
    with pytest.raises(ValueError,match='禁止外部'):
        scheduler.apply_plan(row['id'],agent_store.load(row['id']),{'base_plan_version':0,'actions':[{'type':'add_task',
            'reason':'lookup','task':{'id':'data','kind':'fetch_market','subjects':['600519.SH'],'source_id':'akshare.em.daily'}}]})


def test_local_only_deep_reuses_snapshot_without_fetch(monkeypatch):
    parent=storage.create_run(request(),'parent');scheduler.execute(parent['id'])
    row=storage.create_run(request(parent_run_id=parent['id'],data_policy='local_only',research_question='多空怎么看'),'local-deep')
    actual=scheduler.execute_task
    def checked(rid,task,tasks,focus):
        assert not task['kind'].startswith(('fetch_','search_'))
        return actual(rid,task,tasks,focus)
    monkeypatch.setattr(scheduler,'execute_task',checked)
    scheduler.execute(row['id'])
    assert storage.get_run(row['id'])['status'] in ('completed','partial')
    assert agent_store.budget('run:'+row['id'])['network']==0
    assert KINDS <= {t['kind'] for t in agent_store.load(row['id'])['tasks']}


def test_first_round_cannot_depend_on_opponent():
    row=storage.create_run(request(),'independence')
    tasks=[{'type':'add_task','reason':'x','task':{'id':'data','kind':'fetch_market','subjects':['600519.SH'],'source_id':'akshare.em.daily'}},
           {'type':'add_task','reason':'x','task':{'id':'bull','kind':'argue_bull','depends_on':['data']}},
           {'type':'add_task','reason':'x','task':{'id':'bear','kind':'argue_bear','depends_on':['bull']}}]
    state=agent_store.load(row['id'])
    state=scheduler.apply_plan(row['id'],state,{'base_plan_version':0,'actions':tasks[:1]})
    state['tasks'][0]['status']='succeeded'
    with pytest.raises(ValueError,match='独立'):scheduler.apply_plan(row['id'],state,{'base_plan_version':state['version'],'actions':tasks[1:]})


def test_index_metrics_equal_reference_and_no_gap_fill():
    dates=[f'202610{i:02d}' for i in range(1,22)]
    rows=[{'date':d,'close':100+i} for i,d in enumerate(dates)]
    benchmark=[{'date':d,'close':100} for d in dates]
    result=index_metrics(rows,benchmark,dates,dates[-1])
    assert result['return_20d']==pytest.approx(20)
    assert result['relative_strength_pp']==pytest.approx(20)
    assert result['ma5']==118
    assert result['max_drawdown_pct']==0
    assert result['amount_ratio'] is None
    missing=index_metrics(rows[:-2]+rows[-1:],benchmark,dates,dates[-1])
    assert missing['pct_change'] is None and missing['return_20d'] is None
    no_base=index_metrics(rows,[],dates,dates[-1])
    assert no_base['return_20d'] is not None and no_base['relative_strength_pp'] is None


def test_debate_budget_maximum_is_enforced():
    row=storage.create_run(request(),'lookups')
    state=agent_store.load(row['id'])
    state['tasks']=[{'id':'debate','kind':'rebut_theses','status':'succeeded','depends_on':[],
                     'result':{'disputes':[{'dispute_id':'d1'}]}}]
    for i in range(2):
        state['tasks'].append({'id':f'l{i}','kind':'fetch_market','status':'failed','depends_on':[], 'dispute_id':'d1'})
    with pytest.raises(ValueError,match='次数'):
        scheduler.apply_plan(row['id'],state,{'base_plan_version':0,'actions':[{'type':'add_task','reason':'x',
            'task':{'id':'extra','kind':'fetch_market','dispute_id':'d1','subjects':['600519.SH'],'source_id':'akshare.em.daily'}}]})


def test_deep_conversation_versions_filters_and_exports(tmp_path):
    from app import conversation
    from app.agent_models import ConversationCreate, MessageCreate
    from app.reporting import filter_report, export_report
    parent=storage.create_run(request(),'chat-parent');scheduler.execute(parent['id'])
    conv=conversation.create(ConversationCreate(mode='demo',report_id=parent['id']))
    artifacts=[]
    for index in range(2):
        conv=conversation.get(conv['id'])
        msg=conversation.submit(conv['id'],MessageCreate(text='多空怎么看' if index==0 else '反方论据会改变判断吗',
             client_message_id=str(index),base_requirement_version=conv['requirement_version'],research_depth='deep',research_policy='local_only'))
        conv['research_policy']='local_only';conversation.save(conv)
        conversation.answer_report(conv,msg,msg['text'])
        scheduler.execute(msg['supplement_run_id'])
        conversation.finish_supplement(msg['id'])
        saved=conversation.messages(conv['id'])[-1]
        assert saved['status']=='completed',saved
        with storage.db() as c:
            artifact=json.loads(c.execute('SELECT content FROM research_artifacts WHERE id=?',(saved['artifact_id'],)).fetchone()[0])
        assert artifact['investment_research']['theses']
        assert saved['citations']
        artifacts.append(artifact)
    assert artifacts[1]['parent_artifact_id']==artifacts[0]['id']
    assert artifacts[1]['version']==2
    assert artifacts[1]['investment_research']['changes']['theses']
    selected=filter_report(artifacts[1],{'investment_side':'bear'})
    assert all(t['side']=='bear' for t in selected['investment_research']['theses'])
    assert selected['investment_research']['adjudication']==artifacts[1]['investment_research']['adjudication']
    assert selected['investment_research']['view']['hidden_count']>0
    for fmt in ('md','docx'):
        path=export_report(artifacts[1],{'format':fmt,'scope':'filtered','filters':{'investment_side':'bear'},'charts':False},tmp_path/('research-'+fmt))
        assert path.exists() and path.stat().st_size>100
        if fmt=='md':
            text=path.read_text(encoding='utf-8')
            assert '论点筛选摘录' in text and '总体裁决和限制' in text and '投资论证与裁决' in text


def test_seed_old_workflow_report_for_local_deep():
    from app import engine
    parent=storage.create_run(request()|{'execution_mode':'workflow'},'old-workflow');engine.execute(parent['id'])
    row=storage.create_run(request(parent_run_id=parent['id'],research_depth='deep',data_policy='local_only'),'old-seed')
    scheduler.execute(row['id'])
    assert storage.get_run(row['id'])['status'] in ('completed','partial')
    assert agent_store.budget('run:'+row['id'])['network']==0
    assert any(t['id']=='parent_fetch_market' for t in agent_store.load(row['id'])['tasks'])


def test_sector_compatible_benchmark_fallback(monkeypatch):
    from app.ak_adapter import AKShare
    from app.providers import ProviderError
    dates=[f'202610{i:02d}' for i in range(1,22)]
    calls=[]
    def fetch(fn,**kwargs):
        calls.append(fn)
        if fn=='index_hist_sw':return [{'日期':d,'收盘':100+i,'成交额':100} for i,d in enumerate(dates)]
        if fn=='stock_zh_index_daily_em':raise ProviderError('primary failed')
        return [{'date':d,'close':100} for d in dates]
    monkeypatch.setattr('app.ak_adapter.fetch',fetch)
    result=AKShare().sector({'id':'ak:sw:801125','name':'白酒Ⅱ'},dates[-1],dates)['aggregate']['index_research']
    assert calls==['index_hist_sw','stock_zh_index_daily_em','stock_zh_index_daily']
    assert result['relative_strength_pp']==pytest.approx(20)
    assert result['benchmark_source']=='AKShare/stock_zh_index_daily'
    assert result['source_attempts']==['primary failed']


def test_budget_and_cancel_preserve_completed_research(monkeypatch):
    row=storage.create_run(request(),'cancel-deep')
    original=scheduler.execute_task
    def cancelled(rid,task,tasks,focus):
        result=original(rid,task,tasks,focus)
        if task['kind']=='rebut_theses':storage.update_run(rid,cancel=1)
        return result
    monkeypatch.setattr(scheduler,'execute_task',cancelled)
    scheduler.execute(row['id'])
    assert storage.get_run(row['id'])['status']=='cancelled'
    with storage.db() as c:assert c.execute('SELECT count(*) FROM reports WHERE id=?',(row['id'],)).fetchone()[0]==0
    assert any(t['kind']=='argue_bull' and t['status']=='succeeded' for t in agent_store.load(row['id'])['tasks'])


def test_invalid_lookup_does_not_discard_valid_rebuttal():
    from app.investment_research import constrain_lookups
    rebuttal={'target_thesis_id':'bull1','reason_type':'assumption','explanation':'还缺少持续性证据','evidence_ids':['e1']}
    output={'rebuttals':[rebuttal], 'gaps':[], 'disputes':[{'dispute_id':'d1','thesis_ids':['bull1'],
        'question':'是否持续','materiality':'影响判断','required_fields':['close'],'lookups':[
            {'kind':'fetch_market','source_id':'akshare.em.daily','subjects':['600519.SH'],'query_terms':[],
             'url':'','expected_effect':'查询后续交易日行情'},
            {'kind':'fetch_market','source_id':'akshare.sina.daily','subjects':['600519.SH'],'query_terms':[],
             'url':'','expected_effect':'核验截止时点已有历史价格'}]}]}
    rejected=constrain_lookups(output,request())
    assert len(rejected)==1 and '截止时点之后' in rejected[0]['reason']
    assert output['rebuttals']==[rebuttal]
    assert len(output['disputes'][0]['lookups'])==1
    validate_output('rebut_theses',output,[evidence()],{'bull':{'theses':[{'thesis_id':'bull1'}]}},request())


def test_missing_catalog_is_actionable_not_keyerror(monkeypatch):
    from app.ak_adapter import live_bundle, AKShare
    from app.providers import ProviderError
    monkeypatch.setattr(AKShare,'trade_dates',lambda *args:['20261112'])
    monkeypatch.setattr('app.providers.catalog',lambda *args:[])
    with pytest.raises(ProviderError,match='同步证券目录'):live_bundle(request()|{'mode':'live'})


def test_deep_plan_cannot_analyze_before_data_is_known():
    row=storage.create_run(request(),'no-premature-debate')
    with pytest.raises(ValueError,match='先完成取证'):
        scheduler.apply_plan(row['id'],agent_store.load(row['id']),{'base_plan_version':0,'actions':[
            {'type':'add_task','reason':'x','task':{'id':'data','kind':'fetch_market','subjects':['600519.SH'],'source_id':'akshare.em.daily'}},
            {'type':'add_task','reason':'x','task':{'id':'bull','kind':'argue_bull','depends_on':['data']}}]})
    assert not agent_store.load(row['id'])['tasks']


def test_reply_to_older_artifact_keeps_monotonic_version():
    from app import conversation
    from app.agent_models import ConversationCreate, MessageCreate
    from app.research_artifacts import publish
    conv=conversation.create(ConversationCreate(mode='demo'))
    ids=[]
    for i in range(3):
        msg=conversation.submit(conv['id'],MessageCreate(text='版本核验',client_message_id=str(i),base_requirement_version=0))
        msg.update(answer='资料不足',citations=[])
        publish(conv,msg,{'id':'source','analysis':{},'request':{'parent_artifact_id':ids[0] if i==2 else None}})
        ids.append(msg['artifact_id'])
    with storage.db() as c:
        last=json.loads(c.execute('SELECT content FROM research_artifacts WHERE id=?',(ids[-1],)).fetchone()[0])
        original=json.loads(c.execute('SELECT content FROM research_artifacts WHERE id=?',(ids[0],)).fetchone()[0])
    assert last['version']==3 and last['parent_artifact_id']==ids[0]
    assert original['version']==1
