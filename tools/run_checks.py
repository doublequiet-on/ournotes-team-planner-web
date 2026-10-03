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


run([sys.executable, "-B", str(ROOT / "tools/test_core.py")])
run([sys.executable, '-B', str(ROOT / 'tools/test_workbench.py')])
for name in ("check_power_modes.py",):
    run([sys.executable, "-B", str(ROOT / "browser/tests" / name), str(dest)])
preview = subprocess.Popen([sys.executable, "-B", str(ROOT / "browser/tests/preview_server.py"), "--port", str(args.port)],
    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    line = preview.stdout.readline()
    if not line.startswith("Static preview:"):
        raise RuntimeError("Static preview did not start: " + preview.stderr.read())
    print(line.strip(), flush=True)
    for name in ("check_workbench.cjs", "check_int64.cjs"):
        run(["node", str(ROOT / "browser/tests" / name), str(dest)])
    reports = {name: json.loads((dest / f"{name}-report.json").read_text("utf-8"))
               for name in ("workbench", "int64", "power-modes")}
    if not all(report["passed"] for report in reports.values()):
        raise RuntimeError("One or more checks failed")
    summary = {"passed": True, "browser_version": json.loads((ROOT / "browser/package.json").read_text("utf-8"))["version"],
               "core_version": '0.2.5',
               "browser_checks": reports["workbench"]["reports"],
               "int64": reports["int64"],
               "power_modes": reports["power-modes"],
               "real_mobile_device_verified": False}
    (dest / "validation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    print("All native/browser checks passed", flush=True)
finally:
    preview.terminate()
    try: preview.wait(timeout=10)
    except subprocess.TimeoutExpired: preview.kill(); preview.wait(timeout=10)
