import pytest
from app.context_budget import fit_input
from app.providers import ProviderError


def test_budget_preserves_question_scope_and_explicit_reference():
    content={'question':'原始依据呢','context':{'scope':{'stocks':['600519.SH']},'context_version':2,
        'summary':{'constraints':[{'message_id':'constraint','text':'不要扩展行业'}]},
        'recent_messages':[{'id':str(i),'text':'旧问题'*500,'answer':'旧回答'*500} for i in range(12)],
        'referenced_message':{'id':'reference','text':'原始问题','answer':'原始回答'}},
        'evidence':[{'id':'e','value':{'close':10}}]}
    packed,audit=fit_input('回答',{},content)
    assert audit['estimated_tokens']<=8000
    assert audit['omitted_message_ids']
    assert packed['question']==content['question']
    assert packed['context']['scope']==content['context']['scope']
    assert packed['context']['referenced_message']==content['context']['referenced_message']
    assert packed['context']['summary']['constraints'][0]['text']=='不要扩展行业'
    assert len(content['context']['recent_messages'])==12


def test_large_explicit_reference_is_not_silently_discarded():
    with pytest.raises(ProviderError,match='显式引用'):
        fit_input('回答',{}, {'context':{'referenced_message':{'id':'r','answer':'原始依据'*8000}}})


def test_large_document_excerpt_records_original_offsets():
    text='背景'*5000+'现金分红方案'+'正文'*5000
    packed,audit=fit_input('回答',{}, {'question':'现金分红方案','evidence':[{'id':'doc','value':text}]})
    assert audit['excerpts']
    location=packed['evidence'][0]['excerpt']
    assert packed['evidence'][0]['value']==text[location['start']:location['end']]
    assert location['original_length']==len(text)
