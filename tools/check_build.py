"""Check deterministic runtime generation and stale-public-file removal."""
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
browser = ROOT / "browser"
def build():
    subprocess.run([sys.executable, "-B", str(browser / "build_browser.py")], cwd=browser, check=True)
    return json.loads((browser / "public/build-info.json").read_text("utf-8"))

first = build()
stray = browser / "public/unexpected-profile.json"
stray.write_text('{"synthetic_stale_file":true}', "utf-8")
second = build()
if stray.exists() or first["runtime_sha256"] != second["runtime_sha256"] or first["static_files"] != second["static_files"]:
    raise SystemExit("Build is not deterministic or stale public file remained")
print("Generated runtime is deterministic; stale public files are excluded")
