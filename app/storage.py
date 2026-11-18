import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from .models import now, STAGES

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('TRADINGAGENT_DATA', ROOT / 'data'))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA / 'app.db', timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA journal_mode=WAL')
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init():
    database = DATA / 'app.db'
    if database.exists():
        with sqlite3.connect(database) as original:
            if original.execute('PRAGMA user_version').fetchone()[0] < 3 and original.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'").fetchone():
                backups = DATA / 'backups'
                backups.mkdir(exist_ok=True)
                path = backups / ('pre-v3-' + now().strftime('%Y%m%d-%H%M%S-%f') + '.db')
                with sqlite3.connect(path) as backup:
                    original.backup(backup)
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, idem TEXT UNIQUE, signature TEXT, version INTEGER,
          request TEXT, config TEXT, status TEXT, stages TEXT, error TEXT DEFAULT '',
          created TEXT, updated TEXT, cancel INTEGER DEFAULT 0, state TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS checkpoints (
          run_id TEXT, stage TEXT, result TEXT, PRIMARY KEY(run_id,stage));
        CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, run_id TEXT UNIQUE, content TEXT);
        CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, content TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS policies (
          id TEXT PRIMARY KEY, identity TEXT, body_hash TEXT, content TEXT,
          UNIQUE(identity,body_hash));
        CREATE TABLE IF NOT EXISTS sources (name TEXT PRIMARY KEY, content TEXT);
        CREATE TABLE IF NOT EXISTS exports (
          id TEXT PRIMARY KEY, request_key TEXT UNIQUE, report_id TEXT, request TEXT,
          status TEXT, path TEXT DEFAULT '', error TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS catalog (id TEXT PRIMARY KEY, content TEXT);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, run_id TEXT, stage TEXT, event_type TEXT,
          status TEXT, created TEXT, detail TEXT);
        ''')

        from .agent_store import init as init_agent
        init_agent(c)
        c.execute('PRAGMA user_version=3')

def settings():
    path = DATA / 'settings.json'
    saved = json.loads(path.read_text('utf-8')) if path.exists() else {}
    return {
        'ifind_enabled': saved.get('ifind_enabled', False),
        'ifind_access_token': os.getenv('IFIND_ACCESS_TOKEN', saved.get('ifind_access_token', '')),
        'ifind_refresh_token': os.getenv('IFIND_REFRESH_TOKEN', saved.get('ifind_refresh_token', '')),
        'tushare_token': os.getenv('TUSHARE_TOKEN', saved.get('tushare_token', '')),
        'llm_key': os.getenv('TRADING_LLM_KEY', saved.get('llm_key', '')),
        'llm_url': os.getenv('TRADING_LLM_URL', saved.get('llm_url', 'https://api.deepseek.com/v1')),
        'llm_model': os.getenv('TRADING_LLM_MODEL', saved.get('llm_model', 'deepseek-chat')),
    }


def save_settings(values):
    current = settings()
    for key, value in values.items():
        if value is not None:
            current[key] = value
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / 'settings.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(dumps(current), 'utf-8')
    os.replace(temporary, path)


def public_settings():
    s = settings()
    return {'data_provider': 'akshare', 'market_key_required': False, 'llm_configured': bool(s['llm_key']),
            'ifind_enabled': s['ifind_enabled'],
            'ifind_configured': bool(s['ifind_access_token'] or s['ifind_refresh_token']),
            'llm_url': s['llm_url'], 'llm_model': s['llm_model']}


def run(row):
    if not row:
        return None
    r = dict(row)
    for field in ['request', 'config', 'stages', 'state']:
        r[field] = json.loads(r[field])
    return r


def get_run(run_id):
    with db() as c:
        return run(c.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone())


def create_run(request, idem, connection=None):
    s = settings()
    config = {'llm_url': s['llm_url'], 'llm_model': s['llm_model'], 'pipeline_version': '2.0.0' if request.get('execution_mode') == 'autonomous' else '1.0.0',
              'indicator_version': '1.0.0', 'prompt_version': '1.0.3', 'data_provider': 'akshare-1.20.38'}
    if request.get('execution_mode') == 'autonomous':
        config.update(planner_version='2.0.0-preview.2', tool_version='registered-ak-v1', schema_version=2)
        from .investment_research import VERSION
        config.update(research_version=VERSION)
    signature = digest({'request': request, 'config': config})
    with (nullcontext(connection) if connection is not None else db()) as c:
        if connection is None:
            c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT * FROM runs WHERE idem=?', (idem,)).fetchone()
        if old:
            if json.loads(old['request']) != request:
                raise ValueError('同一幂等键不能提交不同参数')
            return run(old)
        version = c.execute('SELECT COALESCE(MAX(version),0)+1 FROM runs WHERE signature=?', (signature,)).fetchone()[0]
        rid = uuid.uuid4().hex
        stages = {name: {'status': 'waiting', 'note': ''} for name in STAGES}
        stamp = now().isoformat()
        c.execute('INSERT INTO runs(id,idem,signature,version,request,config,status,stages,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (rid, idem, signature, version, dumps(request), dumps(config), 'queued', dumps(stages), stamp, stamp))
        return run(c.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone())


def update_run(run_id, **values):
    allowed = {'status', 'stages', 'error', 'cancel', 'state'}
    assert set(values) <= allowed
    values['updated'] = now().isoformat()
    params = [dumps(v) if isinstance(v, dict) else v for v in values.values()]
    with db() as c:
        c.execute('UPDATE runs SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?', params + [run_id])


def stage_status(run_id, name, status, note=''):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT stages FROM runs WHERE id=?', (run_id,)).fetchone()
        stages = json.loads(row[0])
        stages[name] = {'status': status, 'note': note}
        c.execute('UPDATE runs SET stages=?,updated=? WHERE id=?', (dumps(stages), now().isoformat(), run_id))


def snapshot(content):
    sid = digest(content)
    with db() as c:
        c.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?)', (sid, dumps(content), now().isoformat()))
    return sid


def checkpoint(run_id, stage, result=None):
    with db() as c:
        if result is not None:
            c.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)', (run_id, stage, dumps(result)))
            return result
        row = c.execute('SELECT result FROM checkpoints WHERE run_id=? AND stage=?', (run_id, stage)).fetchone()
        return json.loads(row[0]) if row else None


def event(run_id, stage, event_type, status, detail):
    # Callers pass only metrics and public messages. Never persist request headers or secrets.
    with db() as c:
        c.execute('INSERT INTO events(run_id,stage,event_type,status,created,detail) VALUES(?,?,?,?,?,?)',
                  (run_id, stage, event_type, status, now().isoformat(), dumps(detail)))


def usage(run_id):
    with db() as c:
        rows = list(c.execute('SELECT event_type,detail FROM events WHERE run_id=?', (run_id,)))
    requests = sum(r['event_type'] == 'llm_request' for r in rows)
    with db() as c:
        budget_row = c.execute('SELECT model FROM request_budgets WHERE owner=?', ('run:' + run_id,)).fetchone()
    if budget_row:
        requests = budget_row['model']
    tokens = [json.loads(r['detail']) for r in rows if r['event_type'] == 'llm_usage']
    return {'model_calls': requests, 'input_tokens': sum(t.get('prompt_tokens', 0) or 0 for t in tokens),
            'output_tokens': sum(t.get('completion_tokens', 0) or 0 for t in tokens),
            'token_usage_reported': bool(tokens), 'cost': None, 'cost_note': '价格未配置'}


def policy_upsert(document):
    document.setdefault('source_urls', [document['source_url']])
    identity = document.get('document_no') or document['source_url']
    identity = document['issuer'] + ':' + identity
    body_hash = digest({'body': document['body_text'], 'title': document['title'], 'published_at': document.get('published_at')})
    pid = digest([identity, body_hash])
    with db() as c:
        old = c.execute('SELECT content FROM policies WHERE id=?', (pid,)).fetchone()
        if old:
            previous = json.loads(old[0])
            document['first_seen_at'] = min(previous['first_seen_at'], document['first_seen_at'])
            document['source_urls'] = sorted(set(previous.get('source_urls', [previous['source_url']]) + [document['source_url']]))
        document['id'] = pid
        c.execute('INSERT OR REPLACE INTO policies VALUES(?,?,?,?)', (pid, identity, body_hash, dumps(document)))
    return document
