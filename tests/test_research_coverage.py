from app.research_coverage import assess


REQUEST = {'stocks':['600519.SH'], 'trade_date':'2026-11-13', 'as_of':'2026-11-13T18:00:00+08:00'}


def evidence(eid, value, subject='600519.SH', kind='行情', quality='usable'):
    return {'id':eid,'subject':subject,'kind':kind,'quality':quality,'value':value}


def test_unrelated_policy_failure_does_not_block_close_question():
    result = assess('收盘价是多少',REQUEST,[evidence('price',{'date':'2026-11-13','close':10})])
    assert result['sufficient']
    assert result['gaps'] == []
    result = assess('不看涨跌幅，只看收盘价，不分析政策',REQUEST,[evidence('price',{'date':'2026-11-13','close':10})])
    assert result['sufficient'] and result['assessment_complete']
    assert [item['field'] for item in result['items']]==['close']


def test_missing_field_is_not_zero_and_null_does_not_overwrite_value():
    result = assess('比较涨跌幅',REQUEST,[evidence('a',{'date':'2026-11-13','pct_change':2}),evidence('b',{'date':'2026-11-13','pct_change':None})])
    assert result['sufficient']
    assert result['items'][0]['evidence_ids'] == ['a']
    result = assess('比较涨跌幅',REQUEST,[evidence('b',{'date':'2026-11-13','pct_change':None})])
    assert not result['sufficient']
    assert result['gaps'][0]['type'] == 'partial_coverage'


def test_conflict_retains_both_values_and_wrong_date_is_excluded():
    rows=[evidence('a',{'date':'2026-11-13','close':10}),evidence('b',{'date':'2026-11-13','close':11}),evidence('future',{'date':'2026-11-14','close':99})]
    result=assess('收盘价',REQUEST,rows)
    assert result['gaps'][0]['type']=='conflict'
    assert result['items'][0]['values']==[{'evidence_id':'a','value':10},{'evidence_id':'b','value':11}]
    assert result['excluded'][0]['evidence_id']=='future'


def test_successful_empty_announcements_is_resolved_with_scope():
    row=evidence('coverage',{'stocks':['600519.SH'],'as_of':REQUEST['as_of'],'record_count':0,'availability':'no_records','issues':[]},'global','公告')
    result=assess('查询公告',REQUEST,[row])
    assert result['sufficient']
    assert result['gaps'][0]['status']=='resolved'
    assert result['gaps'][0]['severity']=='informational'
    assert result['gaps'][0]['evidence_ids']==['coverage']


def test_partial_announcement_window_cannot_be_called_no_records():
    row=evidence('coverage',{'stocks':['600519.SH'],'as_of':REQUEST['as_of'],'record_count':0,'availability':'partial_coverage','issues':['部分日期超时']},'global','公告','limited')
    result=assess('查询公告',REQUEST,[row])
    assert not result['sufficient']
    assert result['gaps'][0]['type']=='partial_coverage'
    assert result['gaps'][0]['status']=='open'


def test_policy_title_cannot_satisfy_body_interpretation():
    row=evidence('policy','正文不可用','global','政策','limited')
    row['document']={'title':'一份政策','body_text':'','published_at':'2026-11-13T10:00:00+08:00'}
    result=assess('解读政策影响',REQUEST,[row])
    assert not result['sufficient']
    assert result['gaps'][0]['type']=='partial_coverage'
    row['document']['body_text']='实际的政策正文'
    row['document']['published_at']=None
    assert assess('解读政策影响',REQUEST,[row])['gaps'][0]['type']=='time_uncertain'
    row['document']['published_at']='2026-11-14T10:00:00+08:00'
    result=assess('解读政策影响',REQUEST,[row])
    assert not result['sufficient']
    assert result['excluded'][0]['reason']=='发布时间晚于研究截止时间'


def test_document_claim_requires_body_and_time_caveat():
    import pytest
    from app.answer_validation import validate_document_claim
    rows=[{'document':{'body_text':'','published_at':None}}]
    with pytest.raises(ValueError,match='正文'): validate_document_claim('文件明确要求企业执行措施',rows)
    with pytest.raises(ValueError,match='时间'): validate_document_claim('该政策已经公开',rows)
    validate_document_claim('仅有标题，发布时间未知，无法确认历史时点是否已公开',rows)
