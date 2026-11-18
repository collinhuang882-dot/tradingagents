"""Local, report-scoped evidence search; original snapshots remain immutable."""
import json
from . import storage
from .research_context import terms


def index_report(report_id, evidence):
    with storage.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM research_indexed_reports WHERE report_id=?',(report_id,)).fetchone():
            return
        for item in evidence:
            if item.get('quality') == 'excluded': continue
            cursor = c.execute('INSERT INTO research_evidence(report_id,evidence_id,subject,kind,content) VALUES(?,?,?,?,?)',
                               (report_id,item['id'],item.get('subject','global'),item.get('kind',''),storage.dumps(item)))
            tokens = ' '.join(sorted(terms(storage.dumps({k:item.get(k) for k in ('title','value','kind','subject')}))))
            c.execute('INSERT INTO research_evidence_fts(rowid,tokens) VALUES(?,?)',(cursor.lastrowid,tokens))
        c.execute('INSERT INTO research_indexed_reports VALUES(?)',(report_id,))


def search(report_ids, question, subjects, preferred_ids=(), limit=60):
    if not report_ids: return []
    report_slots = ','.join('?' for _ in report_ids)
    scopes = list(dict.fromkeys([*subjects,'global']))
    scope_slots = ','.join('?' for _ in scopes)
    where = f'e.report_id IN ({report_slots}) AND e.subject IN ({scope_slots})'
    parameters = [*report_ids,*scopes]
    rows = []
    with storage.db() as c:
        if preferred_ids:
            preferred_ids = list(dict.fromkeys(preferred_ids))[:100]
            slots = ','.join('?' for _ in preferred_ids)
            rows.extend(c.execute(f'SELECT e.content FROM research_evidence e WHERE {where} AND e.evidence_id IN ({slots}) LIMIT ?',
                                  [*parameters,*preferred_ids,limit]).fetchall())
        query = sorted(terms(question))[:80]
        if query:
            expression = ' OR '.join('"'+token.replace('"','""')+'"' for token in query)
            rows.extend(c.execute(f'''SELECT e.content FROM research_evidence_fts f
                JOIN research_evidence e ON e.rowid=f.rowid
                WHERE research_evidence_fts MATCH ? AND {where}
                ORDER BY bm25(research_evidence_fts) LIMIT ?''', [expression,*parameters,limit*2]).fetchall())
        # Scope fallback answers questions such as "这份报告还有什么依据".
        rows.extend(c.execute(f'SELECT e.content FROM research_evidence e WHERE {where} ORDER BY e.rowid DESC LIMIT ?',
                              [*parameters,limit]).fetchall())
    unique = {}
    for row in rows:
        item = json.loads(row['content'])
        unique.setdefault(item['id'],item)
    from .research_context import retrieve
    return retrieve(list(unique.values()),question,subjects,preferred_ids,limit)
