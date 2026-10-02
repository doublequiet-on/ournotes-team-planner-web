"""Run synthetic native references and real-browser static-site checks."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8878)
args = parser.parse_args()
dest = ROOT / "work/validation"
dest.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, OURNOTES_BROWSER_URL=f"http://127.0.0.1:{args.port}/ournotes-planner/", PYTHONIOENCODING="utf-8")


def run(command):
    subprocess.run(command, cwd=ROOT, env=env, check=True)


for name in ("make_fixtures.py", "make_extra_fixtures.py", "make_judgement_fixture.py"):
    run([sys.executable, "-B", str(ROOT / "browser/tests" / name), str(dest)])
preview = subprocess.Popen([sys.executable, "-B", str(ROOT / "browser/tests/preview_server.py"), "--port", str(args.port)],
    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    line = preview.stdout.readline()
    if not line.startswith("Static preview:"):
        raise RuntimeError("Static preview did not start: " + preview.stderr.read())
    print(line.strip(), flush=True)
    for name in ("check_browser.cjs", "check_lifecycle.cjs", "check_int64.cjs", "check_review.cjs"):
        run(["node", str(ROOT / "browser/tests" / name), str(dest)])
    run([sys.executable, "-B", str(ROOT / "browser/tests/check_score_oracles.py"), str(dest)])
    reports = {name: json.loads((dest / f"{name}-report.json").read_text("utf-8"))
               for name in ("browser", "lifecycle", "int64", "review", "score-oracle")}
    if not all(report["passed"] for report in reports.values()):
        raise RuntimeError("One or more checks failed")
    summary = {"passed": True, "browser_version": json.loads((ROOT / "browser/package.json").read_text("utf-8"))["version"],
               "core_version": reports["score-oracle"]["core_version"],
               "browser_checks": reports["browser"]["reports"], "lifecycle_checks": reports["lifecycle"]["reports"],
               "review_checks": reports["review"]["reports"], "int64": reports["int64"], "score_oracle": reports["score-oracle"],
               "real_mobile_device_verified": False}
    (dest / "validation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    print("All native/browser checks passed", flush=True)
finally:
    preview.terminate()
    try: preview.wait(timeout=10)
    except subprocess.TimeoutExpired: preview.kill(); preview.wait(timeout=10)
