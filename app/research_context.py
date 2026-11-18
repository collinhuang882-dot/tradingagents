"""Conversation-scoped, source-linked context and evidence retrieval."""
import json
import re
import sqlite3
from . import storage
from .models import now


def terms(text):
    text = text.lower()
    words = set(re.findall(r'[a-z0-9_.]+', text))
    for word in re.findall(r'[\u4e00-\u9fff]+', text):
        words.update(word[i:i+2] for i in range(len(word)-1))
    return words


def decode(row):
    return {**json.loads(row['content']), 'seq': row['seq'], 'status': row['status']}


def build_context(conv, msg):
    thread = conv.get('active_thread_id', conv['id'])
    with storage.db() as c:
        current = c.execute('SELECT seq FROM conversation_messages WHERE id=? AND conversation_id=?',
                            (msg['id'], conv['id'])).fetchone()
        before = current['seq'] if current else 2**63-1
        rows = c.execute("SELECT * FROM conversation_messages WHERE conversation_id=? AND seq<? AND COALESCE(json_extract(content,'$.thread_id'),conversation_id)=? ORDER BY seq DESC LIMIT 12",
                         (conv['id'], before, thread)).fetchall()
        history = [decode(row) for row in reversed(rows)]
        referenced = None
        if msg.get('reply_to'):
            row = c.execute('SELECT * FROM conversation_messages WHERE id=? AND conversation_id=? AND seq<?',
                            (msg['reply_to'], conv['id'], before)).fetchone()
            if not row: raise ValueError('引用消息不存在、属于其他会话或晚于当前消息')
            referenced = decode(row)
        # Older text is ranked independently of the recent tail. Original citations survive.
        tail_start = history[0]['seq'] if history else before
        older = c.execute("SELECT * FROM conversation_messages WHERE conversation_id=? AND seq<? AND COALESCE(json_extract(content,'$.thread_id'),conversation_id)=? ORDER BY seq DESC",
                          (conv['id'], tail_start, thread)).fetchall()
    query = terms(msg['text'])
    scored = []
    for row in older:
        item = decode(row)
        score = len(query & terms(item['text'] + ' ' + item.get('answer', '')))
        if score: scored.append((score, item['seq'], item))
    recalled = [x[2] for x in sorted(scored, key=lambda x:(x[0],x[1]), reverse=True)[:4]]
    fields = ('id', 'seq', 'text', 'answer', 'citations', 'report_id', 'claims', 'status', 'received_at')
    def compact(item, full=False):
        if not item: return None
        result = {key:item[key] for key in fields if key in item}
        # Keep stable references when a large message needs its original retrieved later.
        for key in ('text', 'answer'):
            if not full and len(result.get(key, '')) > 1000:
                result[key] = result[key][:1000] + '〔内容截断，可按消息 ID 读取原文〕'
        return result
    summary = None
    context_warnings = []
    if older:
        selected = [decode(row) for row in reversed(older[:8])]
        constraints, corrections, unresolved, failures = [], [], [], []
        for row in reversed(older):
            item = decode(row)
            reference = {'message_id':item['id'],'seq':item['seq']}
            for sentence in re.split(r'(?<=[。！？\n])',item['text']):
                if re.search(r'不要|不需要|仅使用|只根据|只分析|不得|先别|优先|截止|日期不变',sentence):
                    constraints.append({**reference,'text':sentence[:500]})
                if re.search(r'改成|改为|换成|不是.{0,20}是',sentence):
                    corrections.append({**reference,'text':sentence[:500]})
            if item['status']=='awaiting_clarification': unresolved.append({**reference,'question':item.get('answer','')[:300]})
            if item['status']=='failed': failures.append({**reference,'reason':item.get('answer','')[:300],'actions':item.get('actions',[])})
        summary = {'method':'extractive', 'covered_through_seq':tail_start-1,
                   'generation_version':1,'context_version':conv['requirement_version'],'created':now().isoformat(),
                   'goal_scope':conv['requirements'],
                   'constraints':constraints[-20:],'corrections':corrections[-10:],
                   'unanswered':unresolved[-5:],'failures':failures[-5:],
                   'current_artifact':next((x.get('artifact_id') for x in reversed(history) if x.get('artifact_id')),None),
                   'recent_completed':[{'message_id':x['id'], 'text':x['text'][:500],
                                        'answer_excerpt':x.get('answer','')[:500], 'citations':x.get('citations',[])} for x in selected],
                   'original_messages_retained':True,
                   'source_hash':storage.digest([row['content'] for row in older])}
        try:
            with storage.db() as c:
                current_conv=json.loads(c.execute('SELECT content FROM conversations WHERE id=?',(conv['id'],)).fetchone()[0])
                if current_conv['requirement_version']==conv['requirement_version'] and current_conv.get('active_thread_id',conv['id'])==thread:
                    c.execute('INSERT OR REPLACE INTO context_summaries VALUES(?,?,?,?)',
                              (conv['id'],thread,tail_start-1,storage.dumps(summary)))
        except sqlite3.Error:
            context_warnings.append('历史摘要暂未保存；当前范围、近期消息和原始历史仍保留')
    return {'context_version':conv['requirement_version'], 'scope':conv['requirements'],
            'summary':summary,
            'context_warnings':context_warnings,
            'research_policy':conv.get('research_policy', 'auto'),
            'pending':conv.get('pending'), 'recent_messages':[compact(x) for x in history],
            'recalled_messages':[compact(x) for x in recalled], 'referenced_message':compact(referenced, full=True)}


def retrieve(evidence, question, subjects, preferred_ids=(), limit=60):
    query = terms(question)
    preferred = set(preferred_ids)
    requested_kinds = set()
    for words, kind in [(('公告','分红'), '公告'), (('政策','发改委','证监会'), '政策'),
                        (('外围','美股','港股'), '外围'), (('量价','放量','均线','涨跌','收盘','成交'), '行情')]:
        if any(word in question for word in words): requested_kinds.add(kind)
    ranked = []
    for pos, item in enumerate(evidence):
        if item.get('subject') not in (*subjects, 'global'):
            continue
        content = storage.dumps({k:item.get(k) for k in ('title','value','kind','subject')})
        score = len(query & terms(content)) + (30 if item.get('kind') in requested_kinds else 0)
        score += 100 if item['id'] in preferred else 0
        ranked.append((score, -pos, item))
    return [x[2] for x in sorted(ranked, key=lambda x:(x[0],x[1]), reverse=True)[:limit]]
