import json
import os
import time
import threading
from . import storage
from .engine import execute
from .reporting import export_report


def lock_worker():
    storage.DATA.mkdir(parents=True, exist_ok=True)
    handle = open(storage.DATA / 'worker.lock', 'a+b')
    if handle.tell() == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def process_export(row):
    spec = json.loads(row['request'])
    with storage.db() as c:
        c.execute("UPDATE exports SET status='running',error='' WHERE id=?", (row['id'],))
        table = 'research_artifacts' if row['report_id'].startswith('answer-') else 'reports'
        report = json.loads(c.execute(f'SELECT content FROM {table} WHERE id=?', (row['report_id'],)).fetchone()[0])
    directory = storage.DATA / 'exports'
    directory.mkdir(exist_ok=True)
    target = directory / f'TradingAgents_{report["request"]["trade_date"]}_{row["id"][:8]}_v{report["version"]}_{spec["scope"]}'
    try:
        path = export_report(report, spec, target)
        with storage.db() as c:
            c.execute("UPDATE exports SET status='completed',path=? WHERE id=?", (str(path), row['id']))
    except Exception as exc:
        with storage.db() as c:
            c.execute("UPDATE exports SET status='failed',error=? WHERE id=?", ('导出失败，可保留选项重试（'+type(exc).__name__+'）', row['id']))


def main():
    storage.init()
    handle = lock_worker()
    if handle is None:
        return
    def heartbeat():
        while True:
            with storage.db() as c:
                c.execute('CREATE TABLE IF NOT EXISTS worker_status (id INTEGER PRIMARY KEY, pid INTEGER, heartbeat REAL)')
                c.execute('INSERT OR REPLACE INTO worker_status VALUES(1,?,?)', (os.getpid(), time.time()))
            time.sleep(5)
    threading.Thread(target=heartbeat, daemon=True).start()
    with storage.db() as c:
        c.execute("UPDATE runs SET status='queued' WHERE status IN ('validating','analyzing','generating')")
        c.execute("UPDATE exports SET status='queued' WHERE status='running'")
    from .conversation import loop as message_loop
    threading.Thread(target=message_loop, daemon=True).start()
    while True:
        with storage.db() as c:
            job = c.execute("SELECT * FROM exports WHERE status='queued' LIMIT 1").fetchone()
            review = c.execute("SELECT * FROM runs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        if job:
            process_export(job)
        elif review:
            if review['cancel']:
                storage.update_run(review['id'], status='cancelled')
            else:
                execute(review['id'])
        else:
            time.sleep(.5)


if __name__ == '__main__':
    main()
