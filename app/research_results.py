"""Deterministic operations on the explicitly researched stock set."""
from . import storage

FIELDS = {'name':'股票', 'close':'收盘（元）', 'pct_change':'涨跌幅（%）',
          'amount':'成交额（元）', 'volume_ratio':'量比（前五日均量）'}


def from_report(report):
    rows = []
    for stock in report['stocks']:
        refs = [e['id'] for e in report['evidence'] if e['subject'] == stock['id'] and e['kind'] == '行情']
        rows.append({'id':stock['id'], 'name':stock['name'], 'close':stock.get('close'),
                     'pct_change':stock.get('pct_change'), 'amount':stock.get('amount'),
                     'volume_ratio':stock.get('metrics', {}).get('volume_ratio'), 'evidence_ids':refs})
    return {'id':storage.digest([report['id'],rows])[:24], 'source_report_id':report['id'],
            'original_rows':rows,
            'columns':FIELDS, 'rows':rows, 'trade_date':report['request']['trade_date'],
            'as_of':report['request']['as_of'], 'filters':{}, 'version':1}


def filtered(original, field='amount', descending=True, only_up=False):
    if field not in FIELDS: raise ValueError('结果集不支持该排序字段')
    rows = [r for r in original['rows'] if not only_up or (r.get('pct_change') is not None and r['pct_change'] > 0)]
    present = sorted([r for r in rows if r.get(field) is not None], key=lambda r:r[field], reverse=descending)
    rows = present + [r for r in rows if r.get(field) is None]
    filters = {'sort':field, 'descending':descending, 'only_up':only_up}
    return {**original, 'id':storage.digest([original['id'],filters])[:24], 'parent_id':original['id'],
            'rows':rows, 'filters':filters, 'version':original['version']+1}


def previous_result(conv, msg):
    import json
    with storage.db() as c:
        seq = c.execute('SELECT seq FROM conversation_messages WHERE id=?', (msg['id'],)).fetchone()[0]
        rows = c.execute("SELECT content FROM conversation_messages WHERE conversation_id=? AND seq<? AND COALESCE(json_extract(content,'$.thread_id'),conversation_id)=? AND json_extract(content,'$.result_set') IS NOT NULL ORDER BY seq DESC",
                         (conv['id'],seq,conv.get('active_thread_id',conv['id']))).fetchall()
    for row in rows:
        item = json.loads(row[0])
        if not msg.get('reply_to') or item['id'] == msg['reply_to']: return item
    raise ValueError('当前主题还没有可操作的比较表，请先明确要比较的股票')


def operate(conv, msg, intent):
    import json
    import copy
    from .research_artifacts import publish
    previous = previous_result(conv,msg)
    result = previous['result_set']
    if intent.get('restore'):
        result = {**result, 'rows':result['original_rows'], 'filters':{}, 'version':result['version']+1}
        result['id'] = storage.digest([previous['id'],'restore',result['version']])[:24]
    else:
        result = filtered(result,intent.get('sort_field') or 'amount',intent.get('descending',True),intent.get('only_up',False))
    with storage.db() as c:
        row = c.execute('SELECT content FROM research_artifacts WHERE id=?',(previous['artifact_id'],)).fetchone()
    if not row: raise ValueError('原比较成果不存在，无法核对结果来源')
    report = copy.deepcopy(json.loads(row[0]))
    by_id = {s['id']:s for s in report['stocks']}
    # Restore may need rows removed by the immediately previous filter.
    source_id = result['source_report_id']
    with storage.db() as c:
        source = c.execute('SELECT content FROM reports WHERE id=?',(source_id,)).fetchone()
    if source: by_id.update({s['id']:s for s in json.loads(source[0])['stocks']})
    report['stocks'] = [by_id[r['id']] for r in result['rows'] if r['id'] in by_id]
    refs = list(dict.fromkeys(e for r in result['rows'] for e in r['evidence_ids']))
    msg.update(result_set=result, answer='已恢复原比较表。' if intent.get('restore') else '已按指定条件整理比较表；缺失值不参与上涨筛选，排序时放在最后。',
               citations=refs,limitations=[],report_id=source_id)
    from .research_coverage import assess
    coverage_request={**report['request'],'stocks':[r['id'] for r in result['rows']]}
    coverage=assess('比较收盘价、涨跌幅、成交额和量比',coverage_request,report['evidence'],
                    conv.get('active_thread_id',conv['id']),msg.get('turn_id',msg['id']))
    msg.update(coverage=coverage,gaps=coverage['gaps'])
    report['parent_artifact_id'] = previous['artifact_id']
    publish(conv,msg,report)
