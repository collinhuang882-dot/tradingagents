"""Question-specific coverage from observed evidence, never from model assurances."""
import re
from datetime import datetime
from . import storage


FIELDS = {'close':'收盘价','pct_change':'涨跌幅','amount':'成交额','volume':'成交量',
          'volume_ratio':'量比','ma5':'MA5','ma10':'MA10','ma20':'MA20','ma60':'MA60',
          'dif':'DIF','dea':'DEA','macd':'MACD'}


def assess(question, request, evidence, thread_id='', turn_id=''):
    question = re.sub(r'(?:不需要|无需|不用|不要|不看|不分析|不涉及)[^，。；！？\n]*','',question)
    fields = [key for key,label in FIELDS.items() if label.lower() in question.lower()]
    if '收盘' in question and 'close' not in fields: fields.append('close')
    if re.search(r'量价|日线',question): fields = list(dict.fromkeys(fields+['close','pct_change','amount']))
    if '均线' in question: fields = list(dict.fromkeys(fields+['ma5','ma10','ma20','ma60']))
    items, gaps, excluded = [], [], []
    cutoff = datetime.fromisoformat(request['as_of'])
    eligible = []
    for row in evidence:
        reason = ''
        value = row.get('value')
        if row.get('quality') == 'excluded': reason = '来源记录已排除'
        if row.get('kind') == '行情' and isinstance(value,dict):
            day = value.get('date') or value.get('trade_date')
            if day and str(day).replace('-','') != request['trade_date'].replace('-',''): reason = '交易日与当前研究不符'
        document = row.get('document') or {}
        published = document.get('published_at')
        if published:
            try:
                stamp = datetime.fromisoformat(published)
                if stamp.tzinfo is None: reason = '发布时间缺少时区'
                elif stamp > cutoff: reason = '发布时间晚于研究截止时间'
            except ValueError: reason = '发布时间无法解析'
        if reason: excluded.append({'evidence_id':row['id'],'reason':reason})
        else: eligible.append(row)

    def gap(item, kind, detail, resolved=False):
        gaps.append({'gap_id':storage.digest([thread_id,turn_id,item['subject'],item['field']])[:24],
            'thread_id':thread_id,'turn_id':turn_id,'coverage_item':item['id'],'type':kind,
            'severity':'informational' if resolved else 'blocking','affected_claims':[],
            'evidence_ids':item['evidence_ids'],'attempts':[], 'detail':detail,
            'resolution':detail if resolved else '', 'next_action':'' if resolved else '核对对应来源与字段，无法补齐时保留缺口',
            'status':'resolved' if resolved else 'open'})

    for subject in request.get('stocks',[]):
        rows = [e for e in eligible if e.get('subject')==subject and e.get('kind')=='行情' and isinstance(e.get('value'),dict)]
        for field in fields:
            values = [{'evidence_id':e['id'],'value':e['value'][field]} for e in rows if e['value'].get(field) is not None]
            item = {'id':subject+':'+field,'subject':subject,'field':field,'label':FIELDS[field],
                    'values':values,'evidence_ids':[v['evidence_id'] for v in values], 'status':'available'}
            if not values:
                item['status'] = 'partial_coverage' if rows else 'not_fetched'
                gap(item,item['status'],subject+' 的'+FIELDS[field]+'没有匹配当前交易日的有效值')
            elif len({storage.dumps(v['value']) for v in values}) > 1:
                item['status'] = 'conflict'
                gap(item,'conflict',subject+' 的'+FIELDS[field]+'存在多个来源值，保留各值并暂停确定性结论')
            items.append(item)
    if '公告' in question:
        for subject in request.get('stocks',[]):
            rows = [e for e in eligible if e.get('kind')=='公告' and isinstance(e.get('value'),dict)
                    and subject in e['value'].get('stocks',[]) and e['value'].get('as_of')==request['as_of']
                    and e['value'].get('lookback',request.get('lookback'))==request.get('lookback')]
            item = {'id':subject+':announcements','subject':subject,'field':'announcements','label':'公告检索覆盖',
                    'evidence_ids':[e['id'] for e in rows], 'status':'not_fetched'}
            # A successful later query can supersede a failed attempt; keep the query IDs.
            complete = [e for e in rows if e['value'].get('availability') in ('available','no_records') and not e['value'].get('issues')]
            if complete:
                latest = complete[-1]
                item['status'] = latest['value']['availability']
                if item['status']=='no_records':
                    gap(item,'no_records','本次记录的来源与区间未检出公告，不代表其他来源或区间无公告',resolved=True)
            elif rows:
                item['status']='partial_coverage'
                gap(item,'partial_coverage','公告查询仅部分覆盖，不能据此断言整个区间无公告')
            else:
                gap(item,'not_fetched','未找到当前对象及截止时间的公告检索覆盖记录')
            items.append(item)
    wants_body = bool(re.search(r'正文|解读|影响',question))
    document_kinds = []
    if wants_body and '公告' in question: document_kinds.append('公告')
    if wants_body and re.search(r'政策|发改委|证监会',question): document_kinds.append('政策')
    for kind in document_kinds:
        subjects=request.get('stocks',[]) if kind=='公告' else ['global']
        for subject in subjects:
            rows=[e for e in eligible if e.get('kind')==kind and e.get('subject') in (subject,'global')]
            bodies=[e for e in rows if (e.get('document') or {}).get('body_text')]
            known=[e for e in bodies if (e.get('document') or {}).get('published_at') and e.get('quality') not in ('unverified','excluded')]
            field='announcement_body' if kind=='公告' else 'policy_body'
            item={'id':subject+':'+field,'subject':subject,'field':field,'label':kind+'正文',
                  'evidence_ids':[e['id'] for e in known],'status':'available' if known else 'time_uncertain' if bodies else 'partial_coverage' if rows else 'not_fetched'}
            if not known:
                detail=kind+'正文的发布时间无法核验，不能当作截止时点已知事实' if bodies else kind+'缺少可用正文，不能仅凭标题解读文件细节'
                gap(item,item['status'],detail)
            items.append(item)
    return {'items':items,'gaps':gaps,'excluded':excluded,
            'assessment_complete':bool(items) and not re.search(r'为什么|原因|影响|风险|估值|盈利|政策|预期|建议|趋势|正文',question),
            'sufficient':not any(g['severity']=='blocking' and g['status']!='resolved' for g in gaps)}
