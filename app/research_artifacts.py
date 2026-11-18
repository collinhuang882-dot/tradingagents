"""Immutable answer revisions using the existing report/export representation."""
import copy
from . import storage
from .models import now


def publish(conv, msg, report):
    claims = msg.get('claims') or [{'text':msg['answer'], 'subject':'global',
                                   'evidence_ids':msg.get('citations', []), 'condition':'', 'limitation':''}]
    for i, claim in enumerate(claims):
        claim['claim_id'] = storage.digest([msg['id'], i, {k:v for k,v in claim.items() if k != 'claim_id'}])[:24]
    msg['claims'] = claims
    content = {'message_id':msg['id'], 'answer':msg['answer'], 'citations':msg.get('citations', []),
               'context':msg.get('context'), 'source_report_id':report['id'], 'result_set':msg.get('result_set'),
               'coverage':msg.get('coverage'), 'gaps':msg.get('gaps',[]), 'limitations':msg.get('limitations',[])}
    content['investment_research'] = msg.get('investment_research')
    aid = 'answer-' + storage.digest(content)[:24]
    artifact = copy.deepcopy(report)
    artifact.update(id=aid, run_id=aid, artifact_type='answer', source_report_id=report.get('source_report_id',report['id']),
                    execution_mode='conversation', conversation_id=conv['id'], thread_id=conv.get('active_thread_id',conv['id']),
                    context_version=msg.get('context',{}).get('context_version'),
                    created=now().isoformat(), summary=claims, answer=msg['answer'],
                    limitations=msg.get('limitations', []), result_set=msg.get('result_set'),
                    coverage=msg.get('coverage'), gaps=msg.get('gaps',[]))
    artifact['citations'] = list(msg.get('citations', []))
    # A conversational answer is not a newly executed eight-role analysis.
    artifact['analysis'] = {k:{'claims':[], 'note':'本成果为对话回答，原分析见来源报告'} for k in report['analysis']}
    artifact['analysis']['market'] = {'claims':claims, 'note':'对话回答'}
    artifact.pop('investment_research', None)
    if msg.get('investment_research'):
        artifact['investment_research'] = copy.deepcopy(msg['investment_research'])
    artifact['issues'] = list(dict.fromkeys(msg.get('limitations', []) + [g['detail'] for g in msg.get('gaps',[]) if g['status']!='resolved' and g['severity']!='informational']))
    coverage = msg.get('coverage') or {}
    scoped_complete = coverage.get('assessment_complete') and coverage.get('sufficient')
    artifact['status'] = 'partial' if artifact['issues'] and not scoped_complete else 'completed'
    if msg.get('investment_research') and not scoped_complete: artifact['status'] = 'partial'
    with storage.db() as c:
        c.execute('BEGIN IMMEDIATE')
        message = c.execute('SELECT status FROM conversation_messages WHERE id=?', (msg['id'],)).fetchone()
        if message and message['status'] == 'cancelled':
            from .providers import ProviderError
            raise ProviderError('用户已取消这次请求')
        previous = c.execute("SELECT id,content FROM research_artifacts WHERE conversation_id=? AND json_extract(content,'$.thread_id')=? AND id!=? ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END,rowid DESC LIMIT 1",
                             (conv['id'],artifact['thread_id'],aid,report.get('request',{}).get('parent_artifact_id'))).fetchone()
        artifact['series_id'] = artifact['thread_id']
        if previous:
            import json
            artifact['parent_artifact_id'] = previous['id']
            prior = json.loads(previous['content'])
            latest_version = c.execute("SELECT MAX(json_extract(content,'$.version')) FROM research_artifacts WHERE conversation_id=? AND json_extract(content,'$.thread_id')=? AND id!=?",
                                       (conv['id'],artifact['thread_id'],aid)).fetchone()[0]
            artifact['version'] = (latest_version or 0) + 1
            if artifact.get('investment_research'):
                old = (prior.get('investment_research') or {}).get('adjudication', {})
                current = artifact['investment_research']['adjudication']
                artifact['investment_research']['changes'] = {'parent_artifact_id': previous['id'],
                    'previous_stance': old.get('stance'), 'current_stance': current['stance'],
                    'reason': current['decision_basis'], 'new_evidence_ids': sorted(set(msg.get('citations', [])) - set(prior.get('citations', [])))}
                from difflib import SequenceMatcher
                old_theses = list((prior.get('investment_research') or {}).get('theses', []))
                differences = []
                for thesis in artifact['investment_research']['theses']:
                    matches = [(SequenceMatcher(None, t['statement'], thesis['statement']).ratio(), t) for t in old_theses
                               if t['subject']==thesis['subject'] and t['side']==thesis['side']]
                    score, matched = max(matches, key=lambda pair:pair[0]) if matches else (0, None)
                    if matched and score >= .6:
                        old_theses.remove(matched)
                        change_type = '维持' if matched['statement']==thesis['statement'] and matched['status']==thesis['status'] else '修订'
                        differences.append({'type':change_type,'previous_thesis_id':matched['thesis_id'],'thesis_id':thesis['thesis_id'],
                                            'before':matched['statement'],'after':thesis['statement'], 'status':thesis['status']})
                    else: differences.append({'type':'新增','thesis_id':thesis['thesis_id'],'after':thesis['statement']})
                differences.extend({'type':'本轮未沿用','previous_thesis_id':t['thesis_id'],'before':t['statement']} for t in old_theses)
                artifact['investment_research']['changes']['theses'] = differences
        else: artifact['version'] = 1
        c.execute('INSERT OR IGNORE INTO research_artifacts VALUES(?,?,?,?)', (aid, conv['id'], msg['id'], storage.dumps(artifact)))
        msg['artifact_id'] = aid
        msg['report_id'] = aid
        c.execute('UPDATE conversation_messages SET content=? WHERE id=?',(storage.dumps(msg),msg['id']))
    msg['artifact_id'] = aid
    msg['report_id'] = aid
