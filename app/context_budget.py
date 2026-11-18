"""Bound model input while retaining the current request and explicit references."""
import copy
import math
from . import storage
from .providers import ProviderError


def estimate_tokens(text):
    # Deliberately conservative heuristic; provider usage is recorded separately.
    ascii_count = sum(ord(c)<128 for c in text)
    return math.ceil(ascii_count/3)+(len(text)-ascii_count)*2


def fit_input(instructions, schema, content, limit=8000):
    packed = copy.deepcopy(content)
    removed, excerpts = [], []
    def size():
        return estimate_tokens(instructions+storage.dumps(schema)+storage.dumps(packed))+32
    original = size()
    omitted_sections=[]
    if size()>limit and 'analysis' in packed:
        packed.pop('analysis')
        omitted_sections.append('analysis')
    context = packed.get('context') or {}
    # Claims duplicate answer text. Keep identity and evidence links for older turns.
    if size()>limit:
        for item in context.get('recent_messages',[])[:-2]+context.get('recalled_messages',[]):
            if item.get('claims'):
                item['claim_refs']=[{'claim_id':c.get('claim_id'),'evidence_ids':c.get('evidence_ids',[])} for c in item.pop('claims')]
    for collection in ('recalled_messages','recent_messages'):
        items=context.get(collection,[])
        minimum=1 if collection=='recent_messages' else 0
        while size()>limit and len(items)>minimum:
            removed.append(items.pop(0).get('id'))
    summary=context.get('summary') or {}
    while size()>limit and summary.get('recent_completed'):
        removed.append(summary['recent_completed'].pop(0).get('message_id'))
    evidence=packed.get('evidence',[])
    if size()>limit:
        for item in evidence:
            document=item.get('document')
            if isinstance(document,dict) and 'body_text' in document:
                document.pop('body_text')  # The same body is retained in value below.
            value=item.get('value')
            if isinstance(value,str) and len(value)>1200:
                from .research_context import terms
                hits=[value.find(t) for t in terms(packed.get('question','')) if value.find(t)>=0]
                start=max(0,min(hits)-200) if hits else 0
                item['value']=value[start:start+1200]
                item['excerpt']={'start':start,'end':start+len(item['value']),'original_length':len(value)}
                excerpts.append({'evidence_id':item['id'],**item['excerpt']})
    omitted_evidence=[]
    while size()>limit and len(evidence)>1:
        omitted_evidence.append(evidence.pop()['id'])
    if size()>limit:
        raise ProviderError('当前问题、显式引用和必要上下文超过保守输入上限，请缩小问题或引用范围；原文仍已保留')
    audit={'method':'ASCII字符/3向上取整，非ASCII字符按2 tokens估算，含schema与32 tokens封装余量',
           'limit':limit,'estimated_tokens':size(),'original_estimated_tokens':original,
           'context_version':context.get('context_version'),
           'history_message_ids':[m['id'] for m in context.get('recent_messages',[])],
           'referenced_message_id':(context.get('referenced_message') or {}).get('id'),
           'evidence_ids':[e['id'] for e in evidence], 'omitted_message_ids':removed,
           'omitted_evidence_ids':omitted_evidence,'excerpts':excerpts,
           'omitted_sections':omitted_sections,
           'input_hash':storage.digest(packed)}
    return packed,audit
