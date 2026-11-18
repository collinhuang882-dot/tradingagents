"""Opt-in live conversation verification; retains messages and trace for audit."""
import argparse
import json
import time
import uuid
from pathlib import Path
import httpx


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--report-id')
    parser.add_argument('--conversation')
    parser.add_argument('--steps', type=int, default=6)
    parser.add_argument('--start', type=int, default=1, choices=range(1,7))
    args = parser.parse_args()
    if not args.live: raise SystemExit('Use --live to call the configured model and data tools.')
    client = httpx.Client(base_url='http://127.0.0.1:8765/api', trust_env=False, timeout=30)
    cid = args.conversation
    if not cid:
        response = client.post('/conversations',json={'report_id':args.report_id})
        response.raise_for_status(); cid = response.json()['id']
    questions = [
        ('只根据这份报告，用两点说明贵州茅台的量价表现，请分别引用证据。', 'local_only'),
        ('你刚才第二点的依据是什么？说明它对应哪只股票和哪一天。', 'inherit'),
        ('开启自动补查，再核实这只股票的公告，沿用原日期和截止时间。', 'auto'),
        ('和五粮液比较一下量价，日期和信息截止时间不变。', 'auto'),
        ('把刚才的比较表按成交额从高到低排序，只保留上涨的。', 'inherit'),
        ('继续刚才的比较，概括两家共同风险，不要扩展行业。', 'inherit'),
    ]
    output = Path(__file__).resolve().parents[1]/'data'/('v3-journey-'+uuid.uuid4().hex[:8]+'.json')
    results = {'conversation_id':cid, 'results':[]}
    print('conversation',cid,flush=True)
    for text, policy in questions[args.start-1:args.start-1+args.steps]:
        conv = client.get('/conversations/'+cid).json()
        response = client.post('/conversations/'+cid+'/messages',json={
            'text':text,'research_policy':policy,'client_message_id':uuid.uuid4().hex,
            'base_requirement_version':conv['requirement_version']})
        response.raise_for_status(); mid = response.json()['id']
        deadline = time.monotonic()+1100
        while time.monotonic()<deadline:
            rows = client.get('/conversations/'+cid+'/messages',params={'latest':'true'}).json()
            msg = next(x for x in rows if x['id']==mid)
            if msg['status'] not in ('received','queued','interpreting','researching'): break
            time.sleep(2)
        results['results'].append(msg)
        output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:msg.get(k) for k in ('id','status','answer','citations','artifact_id')},ensure_ascii=False),flush=True)
        if msg['status']!='completed': break
    print('trace',str(output),flush=True)
