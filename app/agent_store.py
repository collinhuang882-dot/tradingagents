"""Durable plans and request budgets. All plan changes have one transactional writer."""
import json
import time
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from . import storage
from .models import now

scope = ContextVar('agent_budget_scope', default=None)
_timers = {}


class BudgetExceeded(Exception):
    pass


def init(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS context_summaries (conversation_id TEXT, thread_id TEXT, covered_through_seq INTEGER, content TEXT, PRIMARY KEY(conversation_id,thread_id,covered_through_seq));
    CREATE TABLE IF NOT EXISTS research_inputs (id INTEGER PRIMARY KEY, owner TEXT, label TEXT, content TEXT, created TEXT);
    CREATE INDEX IF NOT EXISTS research_input_owner ON research_inputs(owner,id);
    CREATE TABLE IF NOT EXISTS research_artifacts (id TEXT PRIMARY KEY, conversation_id TEXT, message_id TEXT, content TEXT);
    CREATE TABLE IF NOT EXISTS research_evidence (rowid INTEGER PRIMARY KEY, report_id TEXT, evidence_id TEXT, subject TEXT, kind TEXT, content TEXT, UNIQUE(report_id,evidence_id));
    CREATE INDEX IF NOT EXISTS research_evidence_scope ON research_evidence(report_id,subject,kind);
    CREATE TABLE IF NOT EXISTS research_indexed_reports (report_id TEXT PRIMARY KEY);
    CREATE VIRTUAL TABLE IF NOT EXISTS research_evidence_fts USING fts5(tokens);
    CREATE TABLE IF NOT EXISTS agent_states (run_id TEXT PRIMARY KEY, content TEXT);
    CREATE TABLE IF NOT EXISTS run_plans (run_id TEXT, version INTEGER, content TEXT, PRIMARY KEY(run_id,version));
    CREATE TABLE IF NOT EXISTS request_budgets (owner TEXT PRIMARY KEY, model INTEGER DEFAULT 0,
      network INTEGER DEFAULT 0, elapsed REAL DEFAULT 0, started REAL, model_limit INTEGER, network_limit INTEGER);
    CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, content TEXT);
    CREATE TABLE IF NOT EXISTS conversation_messages (seq INTEGER PRIMARY KEY AUTOINCREMENT,
      id TEXT UNIQUE, conversation_id TEXT, client_id TEXT, content TEXT, status TEXT,
      UNIQUE(conversation_id,client_id));
    CREATE INDEX IF NOT EXISTS conversation_message_order ON conversation_messages(conversation_id,seq);
    CREATE TABLE IF NOT EXISTS conversation_events (
      cursor INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, message_id TEXT, content TEXT);
    CREATE INDEX IF NOT EXISTS conversation_event_cursor ON conversation_events(conversation_id,cursor);
    CREATE TRIGGER IF NOT EXISTS conversation_message_insert_event AFTER INSERT ON conversation_messages BEGIN
      INSERT INTO conversation_events(conversation_id,message_id,content) VALUES(NEW.conversation_id,NEW.id,
        json_set(json_remove(NEW.content,'$.context','$.submission'),'$.seq',NEW.seq,'$.status',NEW.status));
    END;
    CREATE TRIGGER IF NOT EXISTS conversation_message_update_event AFTER UPDATE OF status,content ON conversation_messages
      WHEN OLD.status != NEW.status OR OLD.content != NEW.content BEGIN
      INSERT INTO conversation_events(conversation_id,message_id,content) VALUES(NEW.conversation_id,NEW.id,
        json_set(json_remove(NEW.content,'$.context','$.submission'),'$.seq',NEW.seq,'$.status',NEW.status));
    END;
    CREATE TABLE IF NOT EXISTS conversation_requirements (conversation_id TEXT, version INTEGER, content TEXT,
      PRIMARY KEY(conversation_id,version));
    CREATE TABLE IF NOT EXISTS conversation_changes (id TEXT PRIMARY KEY, conversation_id TEXT,
      run_id TEXT, status TEXT, content TEXT);
    ''')


def ensure_budget(owner, model=60, network=80):
    with storage.db() as c:
        c.execute('INSERT OR IGNORE INTO request_budgets(owner,model_limit,network_limit) VALUES(?,?,?)',
                  (owner, model, network))


def budget(owner):
    with storage.db() as c:
        row = c.execute('SELECT * FROM request_budgets WHERE owner=?', (owner,)).fetchone()
    return dict(row) if row else {}


def reserve(kind, owner=None):
    ctx = scope.get()
    owner = owner or (ctx or {}).get('owner')
    if not owner:
        return
    with storage.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM request_budgets WHERE owner=?', (owner,)).fetchone()
        if not row:
            raise BudgetExceeded('预算未初始化')
        if ctx and time.monotonic() > ctx.get('deadline', float('inf')):
            raise BudgetExceeded('处理时间预算已用尽')
        elapsed = row['elapsed'] + (time.time() - row['started'] if row['started'] else 0)
        if owner.startswith('turn:') and elapsed >= 120:
            raise BudgetExceeded('本轮对话累计处理时间预算已用尽')
        if owner.startswith('run:'):
            run = c.execute('SELECT cancel,request,config FROM runs WHERE id=?', (owner[4:],)).fetchone()
            if run and run['cancel']:
                raise BudgetExceeded('用户已取消')
            from .investment_research import enabled
            deep = bool(run and enabled({'request': json.loads(run['request']), 'config': json.loads(run['config'])}))
            seconds = 1800 if deep else 900
            if elapsed >= (seconds if ctx and ctx.get('finalizing') else seconds - 60):
                raise BudgetExceeded('执行时间进入收尾预算')
        limit = row[kind + '_limit']
        if kind == 'model' and owner.startswith('run:') and not (ctx and ctx.get('finalizing')):
            limit -= 2
        if row[kind] >= limit:
            raise BudgetExceeded('请求预算已用尽')
        if kind == 'model' and ctx and ctx.get('message_calls', 0) >= (6 if owner.startswith('turn:') else 3):
            raise BudgetExceeded('单条消息调用预算已用尽')
        c.execute(f'UPDATE request_budgets SET {kind}={kind}+1 WHERE owner=?', (owner,))
    if kind == 'model' and ctx and 'message_calls' in ctx:
        ctx['message_calls'] += 1


@contextmanager
def context(owner, message=False, finalizing=False):
    data = {'owner': owner, 'finalizing': finalizing}
    if message:
        data.update(message_calls=0, deadline=time.monotonic() + (120 if owner.startswith('turn:') else 60))
    if owner.startswith('turn:'):
        _start_budget(owner)
        with storage.db() as c:
            row = c.execute('SELECT elapsed FROM request_budgets WHERE owner=?', (owner,)).fetchone()
            remaining = max(0, 120 - row['elapsed']) if row else 0
            data['deadline'] = time.monotonic() + remaining
    token = scope.set(data)
    try:
        yield data
    finally:
        if owner.startswith('turn:'):
            _end_budget(owner)
        scope.reset(token)


def load(rid):
    with storage.db() as c:
        row = c.execute('SELECT content FROM agent_states WHERE run_id=?', (rid,)).fetchone()
    return json.loads(row[0]) if row else {'version': 0, 'tasks': [], 'rounds': 0, 'no_progress': 0,
                                        'stop_reason': '', 'requirement_version': 0}


def save(rid, state, plan=False):
    with storage.db() as c:
        c.execute('INSERT OR REPLACE INTO agent_states VALUES(?,?)', (rid, storage.dumps(state)))
        if plan:
            c.execute('INSERT INTO run_plans VALUES(?,?,?)', (rid, state['version'], storage.dumps(state)))


def action(rid, kind, message, **detail):
    storage.event(rid, '自主规划', kind, 'completed', {'message': message, **detail})


def begin(rid):
    _start_budget('run:' + rid)


def _start_budget(owner):
    from .investment_research import enabled
    run = storage.get_run(owner[4:]) if owner.startswith('run:') else None
    deep = bool(run and enabled(run))
    ensure_budget(owner, model=6 if owner.startswith('turn:') else 120 if deep else 60,
                  network=2 if owner.startswith('turn:') else 160 if deep else 80)
    with storage.db() as c:
        # A one-second heartbeat bounds the unobserved crash interval; offline time is not active work.
        c.execute('UPDATE request_budgets SET elapsed=elapsed+CASE WHEN started IS NULL THEN 0 ELSE MIN(2,MAX(0,?-started)) END, started=? WHERE owner=?',
                  (time.time(), time.time(), owner))
    stop = threading.Event()
    def tick():
        while not stop.wait(1):
            try:
                with storage.db() as c:
                    stamp = time.time()
                    c.execute('UPDATE request_budgets SET elapsed=elapsed+?-started,started=? WHERE owner=? AND started IS NOT NULL',
                              (stamp, stamp, owner))
            except Exception:
                # Request reservation still checks elapsed wall time if the heartbeat cannot write.
                continue
    thread = threading.Thread(target=tick, daemon=True)
    _timers[owner] = (stop, thread)
    thread.start()


def end(rid):
    _end_budget('run:' + rid)


def _end_budget(owner):
    timer = _timers.pop(owner, None)
    if timer:
        timer[0].set()
        timer[1].join(timeout=3)
    with storage.db() as c:
        c.execute('UPDATE request_budgets SET elapsed=elapsed+CASE WHEN started IS NULL THEN 0 ELSE ?-started END, started=NULL WHERE owner=?',
                  (time.time(), owner))
