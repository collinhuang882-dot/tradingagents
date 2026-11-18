"""Opt-in planner fault evaluation against controlled observations; no market requests."""
import copy
import json
import sys
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import storage,agent_store
from app.models import ReviewInput
from app.planner import propose
from app.scheduler import apply_plan


def task(id,kind,status='succeeded',source='',deps=None,result=None):
    return dict(id=id,kind=kind,subjects=['600519.SH'],source_id=source,depends_on=deps or [],
                input_refs=[],gap_id='',trigger_event_id='',priority=50,url='',status=status,
                result=result or {},error='模拟来源超时' if status=='failed' else '',reason='受控评估输入')


def scenario(name):
    em=task('market_em','fetch_market','failed','akshare.em.daily')
    policy=task('policy','search_policy',source='official.columns',result={'events':[],'evidence':[]})
    if name=='source_switch':return [em,policy],['600519.SH 主来源超时，请选择兼容备用来源补取当日日线。']
    if name=='document':
        policy['result']['events']=[{'title':'测试政策（评估夹具）','source_url':'https://www.ndrc.gov.cn/xxgk/zcfb/tz/test-fixture.html','body_text':''}]
        return [policy],['测试政策只有标题，须新增 fetch_document 获取已观察到的官方原文。']
    if name=='conflict':
        em.update(status='succeeded',error='',result={'stocks':[{'id':'600519.SH'}], 'issues':['同日未复权收盘价与新浪来源相差1元，待核验']})
        sina=task('market_sina','fetch_market',source='akshare.sina.daily',result={'stocks':[{'id':'600519.SH'}]})
        return [em,sina,policy],['两个同日同口径来源有冲突，先新增 compare_sources 依赖 market_em 和 market_sina。']
    if name=='revision':
        market=task('market','fetch_market',source='akshare.sina.daily',result={'stocks':[{'id':'600519.SH'}]})
        old=task('policy_analysis','analyze_policy',deps=['policy'],result={'claims':[{'text':'旧版本观点'}]})
        bull=task('bull','argue_bull',deps=['market','policy_analysis'])
        bear=task('bear','argue_bear',deps=['market','policy_analysis'])
        risk=task('risk','review_risk',deps=['bull','bear'])
        new=task('new_policy','fetch_document',result={'evidence':[{'id':'new-evidence','snapshot_id':'new-snapshot','quality':'usable'}]})
        return [market,policy,old,bull,bear,risk,new],['新政策证据 new-evidence 已核验；原政策分析和多空/风险未引用它，须修订相关分析任务的依赖并重算。']
    return [em,task('market_sina','fetch_market','failed','akshare.sina.daily'),policy],['两家已注册日线来源均失败，没有其他兼容来源，重复查询没有新证据，请停止补充。']


def main():
    if '--live' not in sys.argv:raise SystemExit('Use --live to call the configured model.')
    config=storage.settings()
    target=storage.ROOT/'data'/('planner-eval-'+uuid.uuid4().hex[:8])
    storage.DATA=target;storage.init();storage.settings=lambda:config
    names=['source_switch','document','conflict','revision','no_progress']
    def evaluate(pair):
        name,iteration=pair
        request=ReviewInput(trade_date='2026-11-12',as_of='2026-11-12T18:00:00+08:00',stocks=['600519.SH'],mode='live',execution_mode='autonomous').model_dump(mode='json')
        run=storage.create_run(request,str(uuid.uuid4()));agent_store.ensure_budget('run:'+run['id'])
        tasks,gaps=scenario(name)
        state={'version':1,'tasks':tasks,'rounds':1,'no_progress':0,'stop_reason':'','focus':['量价','政策'],'requirement_version':0,'budget':agent_store.budget('run:'+run['id'])}
        correction='';plans=[];passed=False
        with agent_store.context('run:'+run['id']):
            for _ in range(2):
                try:
                    plan=propose(run['id'],request,state,gaps,correction);plans.append(plan)
                    apply_plan(run['id'],state,plan)
                    actions=plan['actions']
                    if name=='source_switch':passed=any(a.get('task',{}).get('source_id')=='akshare.sina.daily' for a in actions)
                    elif name=='document':passed=any(a.get('task',{}).get('kind')=='fetch_document' for a in actions)
                    elif name=='conflict':passed=any(a.get('task',{}).get('kind')=='compare_sources' for a in actions)
                    elif name=='revision':passed=any(a['type']=='revise_task' for a in actions)
                    else:passed=any(a['type']=='finish' for a in actions)
                    break
                except Exception as exc:correction=str(exc)[:700]
        result={'scenario':name,'iteration':iteration,'passed':passed,'plans':plans,'validation_error':correction}
        print(json.dumps({k:result[k] for k in ('scenario','iteration','passed')}),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(evaluate,[(n,i) for n in names for i in range(1,4)]))
    output=target/'results.json';output.write_text(json.dumps({'passed':sum(r['passed'] for r in results),'total':len(results),'cases':results},ensure_ascii=False,indent=2),encoding='utf8')
    print(str(output),flush=True)


if __name__=='__main__':main()
