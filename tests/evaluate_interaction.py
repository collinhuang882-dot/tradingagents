"""Explicit live-model intent evaluation. Run manually; never part of pytest.

python -m tests.evaluate_interaction is not required; use this file with project root on PYTHONPATH.
Results contain prompts and public model outputs, not credentials.
"""
import json
import sys
import time
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import storage, conversation, agent_store
from app.agent_models import ConversationCreate


def main():
    if '--live' not in sys.argv:
        raise SystemExit('This evaluation calls your configured model. Add --live to run.')
    settings = storage.settings()
    if not settings['llm_key']: raise SystemExit('Configure the model in the application first.')
    target = storage.ROOT / 'data' / ('interaction-eval-' + uuid.uuid4().hex[:8])
    storage.DATA = target
    storage.init()
    storage.settings = lambda: settings
    cases = [
        ('复盘2026-11-12的贵州茅台', 'create_review', '600519.SH'),
        ('帮我看2026-11-12五粮液的量价', 'create_review', '000858.SZ'),
        ('分析2026-11-12宁德时代盘后表现', 'create_review', '300750.SZ'),
        ('帮我复盘昨天的茅台', 'create_review', '600519.SH'),
        ('复盘2026年11月12日的贵州茅台', 'create_review', '600519.SH'),
        ('给我一份2026-11-12贵州茅台和五粮液的对比复盘', 'create_review', '600519.SH'),
        ('帮我复盘茅台', 'clarify', None),
        ('分析一下最近的行情', 'clarify', None),
        ('看看它怎么样', 'clarify', None),
        ('先别执行，我想知道能不能分析茅台', 'clarify', None),
        ('为什么说短期偏弱？', 'explain_report', None),
        ('这个量比是什么意思？', 'explain_report', None),
        ('请列出报告里看空的依据', 'explain_report', None),
        ('概括这份报告的主要风险', 'explain_report', None),
        ('报告的结论引用了哪些数据？', 'explain_report', None),
        ('再查一下相关政策原文', 'supplement_analysis', None),
        ('请补查影响白酒需求的政策', 'supplement_analysis', None),
        ('核实这份报告涉及的政策是否有正文', 'supplement_analysis', None),
        ('补充研究量价背离的风险', 'supplement_analysis', None),
        ('请再次检索这段时间的官方政策', 'supplement_analysis', None),
        ('重点分析政策，少讲技术指标', 'update_focus', None),
        ('调整重点，多讲成交量', 'update_focus', None),
        ('接下来的分析重点放在风险上', 'update_focus', None),
        ('查到哪一步了？', 'get_status', None),
        ('当前任务进度如何？', 'get_status', None),
        ('取消这次分析', 'cancel_run', None),
        ('取消当前任务', 'cancel_run', None),
        ('加入五粮液，改成两家公司对比', 'create_review', '000858.SZ'),
        ('把复盘日期改成2026-11-11', 'create_review', None),
        ('改为分析2026-11-10的贵州茅台', 'create_review', '600519.SH'),
    ]
    def evaluate(item):
        i, (text, expected, stock) = item
        conv = conversation.create(ConversationCreate(mode='demo'))
        if i >= 10:
            conv.update(run_id='bound-run',report_id='bound-report',requirements={
                'stocks':['600519.SH'],'sectors':[],'trade_date':'2026-11-12',
                'as_of':'2026-11-12T18:00:00+08:00','mode':'demo'})
            conversation.save(conv)
        msg={'id':uuid.uuid4().hex,'text':text,'received_at':'2026-11-13T10:00:00+08:00'}
        start=time.monotonic()
        try:
            with agent_store.context('chat:'+conv['id'],message=True):
                output=conversation.interpret(conv,msg)
            intents=output['intents']
            if expected=='clarify':
                ok=any(x.get('clarification') for x in intents) or all(not x.get('trade_date') or not (x.get('stocks') or x.get('sectors')) for x in intents)
            else:
                ok=any(x['type']==expected and (not stock or stock in x.get('stocks',[])) for x in intents)
            if expected=='create_review' and i < 6:
                ok=ok and any(x.get('trade_date')=='2026-11-12' for x in intents)
            result={'index':i+1,'text':text,'expected':expected,'passed':bool(ok),'output':output,'seconds':round(time.monotonic()-start,2)}
        except Exception as exc:
            result={'index':i+1,'text':text,'expected':expected,'passed':False,'error':type(exc).__name__}
        print(json.dumps({'case':i+1,'passed':result['passed']},ensure_ascii=True),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(evaluate,enumerate(cases)))
    output=target/'results.json'
    output.write_text(json.dumps({'passed':sum(x['passed'] for x in results),'total':len(results),'cases':results},ensure_ascii=False,indent=2),encoding='utf8')
    print(str(output),flush=True)


if __name__=='__main__':main()
