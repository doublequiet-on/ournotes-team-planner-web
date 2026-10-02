"""Check the game's live-mode rule against the exact public release core."""
from pathlib import Path
import json
import sys
import unittest
from baseline import load_baseline

destination = Path(sys.argv[1]).resolve()
p = load_baseline(destination / "power-modes")
from test_power_modes import PowerModeTests

result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PowerModeTests))
report = {"passed": result.wasSuccessful(), "core_version": p.VERSION, "tests": result.testsRun,
          "rule_source": "MasterText:Help_SubCategory_Description_140002",
          "synthetic_profiles_only": True, "new_real_game_power_sample_verified": False}
(destination / "power-modes-report.json").write_text(json.dumps(report, indent=2), "utf-8")
if not result.wasSuccessful():
    raise SystemExit("Live-mode power checks failed")
