import json
import os
import subprocess
import sys
import time
from . import storage


def controlled_run(args, *, input, cwd, env, capture_output, text, encoding, timeout, creationflags):
    from .agent_store import scope, BudgetExceeded
    ctx = scope.get() or {}
    deadline = min(time.monotonic() + timeout, ctx.get('deadline', float('inf')))
    process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding=encoding, creationflags=creationflags)
    payload = input
    try:
        while True:
            if ctx.get('owner', '').startswith('run:'):
                run = storage.get_run(ctx['owner'][4:])
                if run and run['cancel']:
                    raise BudgetExceeded('用户已取消')
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                out, err = process.communicate(payload, timeout=min(.5, deadline-time.monotonic()))
                return subprocess.CompletedProcess(args, process.returncode, out, err)
            except subprocess.TimeoutExpired:
                payload = None
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def fetch(function, *, ttl=3600, timeout=55, **params):
    from .providers import ProviderError
    key = storage.digest(['akshare-1.20.38', function, params])
    with storage.db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS ak_cache (id TEXT PRIMARY KEY, created REAL, content TEXT)')
        row = c.execute('SELECT * FROM ak_cache WHERE id=?', (key,)).fetchone()
    if row and time.time() - row['created'] < ttl:
        return json.loads(row['content'])
    from .agent_store import scope
    budget_context = scope.get()
    try:
        result = (controlled_run if budget_context else subprocess.run)(
            [sys._base_executable if os.name == 'nt' else sys.executable, '-m', 'app.ak_runner'],
            input=json.dumps({'function': function, 'params': params, 'budget_owner': (budget_context or {}).get('owner'), 'budget_data': str(storage.DATA)}),
            cwd=storage.ROOT, env=os.environ | {'PYTHONPATH': os.pathsep.join(sys.path), 'PYTHONIOENCODING': 'utf-8'},
            capture_output=True, text=True, encoding='utf-8', timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except subprocess.TimeoutExpired:
        raise ProviderError(f'AKShare {function} 来源超时，请稍后重试') from None
    if result.returncode:
        raise ProviderError(f'AKShare {function} 获取失败，来源可能暂时不可用或返回格式已变化')
    try:
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            raise ValueError()
    except ValueError:
        raise ProviderError(f'AKShare {function} 返回格式无效') from None
    with storage.db() as c:
        c.execute('INSERT OR REPLACE INTO ak_cache VALUES(?,?,?)', (key, time.time(), storage.dumps(rows)))
    return rows
