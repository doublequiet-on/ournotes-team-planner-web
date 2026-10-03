"""Native workbench regressions and browser oracle fixtures, synthetic only."""
from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'browser/tests'))
from baseline import load_baseline
load_baseline(ROOT / 'work/workbench-tests', optimized=True)
sys.path.insert(0, str(ROOT / 'workbench'))
import team_candidates as t
from sample import sample

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'workbench/tests'))
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
    data = t.p.Data()
    dest = ROOT / 'work/validation'
    dest.mkdir(parents=True, exist_ok=True)
    request = sample(data)
    request['team_settings'].update(count=5, strategy='balanced', required_leader_id=59,
        required_bindings=[{'member_id': 59, 'snap_id': 33}], min_event_bonus_10000=100)
    result = t.optimize(request, data)
    (dest / 'workbench-native.json').write_text(json.dumps({'input': request, 'result': result}, ensure_ascii=False), 'utf-8')
