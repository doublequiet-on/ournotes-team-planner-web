"""Song-independent workbench in a private Python/WASM worker."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import types

import cp_model as browser_cp
from js import browser_cancelled, browser_progress, browser_persist

for name in ('ortools', 'ortools.sat', 'ortools.sat.python'):
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
sys.modules['ortools.sat.python.cp_model'] = browser_cp
sys.modules['ortools.sat.python'].cp_model = browser_cp

import team_candidates as teams
from sample import sample


class BrowserData(teams.p.Data):
    def fingerprint(self):
        parts = [super().fingerprint(), 'or-tools-wasm-0.9.1']
        parts += [hashlib.sha256(Path(path).read_bytes()).hexdigest()
                  for path in (__file__, browser_cp.__file__)]
        return hashlib.sha256('|'.join(parts).encode()).hexdigest()


class BrowserCache:
    def __init__(self):
        self.db = sqlite3.connect('/state/search-v1.sqlite3')
        self.db.execute('CREATE TABLE IF NOT EXISTS team_candidates_v1 (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.commit()

    def get(self, key):
        row = self.db.execute('SELECT body FROM team_candidates_v1 WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO team_candidates_v1 VALUES (?,?)',
                        (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))
        self.db.commit()
        browser_persist(True)


def native_solve(self, model):
    self.check()
    solver = browser_cp.CpSolver()
    status = solver.solve(model)
    self.check()
    self.calls += 1
    if status not in (browser_cp.OPTIMAL, browser_cp.INFEASIBLE):
        raise teams.p.InputError('尚未完成最优证明，请继续计算；不会显示未完成队伍。')
    return status, solver


teams.Search.native_solve = native_solve
data = BrowserData()
cache = None


def close_cache():
    global cache
    if cache is not None:
        cache.db.close()
        cache = None


def restore_cache():
    global cache
    close_cache()
    try:
        cache = BrowserCache()
        if cache.db.execute('PRAGMA quick_check(1)').fetchall() != [('ok',)]:
            raise sqlite3.DatabaseError('Invalid resume cache')
        return False
    except sqlite3.DatabaseError:
        close_cache()
        for name in ('search-v1.sqlite3', 'search-v1.sqlite3-journal'):
            (Path('/state') / name).unlink(missing_ok=True)
        cache = BrowserCache()
        return True


def bootstrap():
    catalog = teams.workbench_catalog(data)
    for kind in ('members', 'snaps'):
        for card in catalog[kind]:
            card['thumbnail'] = card['thumbnail'].lstrip('/')
    return {'catalog': catalog, 'sample': sample(data), 'token': 'browser-local',
            'version': teams.VERSION, 'defaults': teams.DEFAULT, 'bdon_url': teams.BDON,
            'engine_signature': hashlib.sha256(Path(teams.__file__).read_bytes()).hexdigest() + ':' + data.fingerprint()}


def invoke(method, raw, job_id=None):
    body = json.loads(raw) if raw else {}
    if method == 'bootstrap':
        value = bootstrap()
    elif method == 'check-growth':
        value = teams.growth_check(body, data)
    elif method == 'evaluate':
        value = teams.evaluate(body, data)
    elif method == 'optimize':
        begin = time.monotonic()

        def progress(**changes):
            changes.setdefault('elapsed_seconds', round(time.monotonic() - begin, 1))
            browser_progress(json.dumps(changes, ensure_ascii=False))

        try:
            result = teams.optimize(body, data, progress, lambda: bool(browser_cancelled()), cache)
            if browser_cancelled():
                raise teams.p.Cancelled()
            value = {'status': 'complete', 'result': result}
        except teams.p.Cancelled:
            value = {'status': 'cancelled'}
    else:
        raise teams.p.InputError('找不到此操作。')
    return json.dumps(value, ensure_ascii=False, allow_nan=False)
