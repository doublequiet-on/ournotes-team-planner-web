"""Run production-core regressions against public synthetic growth only."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser/tests"))
from baseline import load_baseline
load_baseline(ROOT / "work/core-tests", optimized=True)
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
    raise SystemExit(1)
