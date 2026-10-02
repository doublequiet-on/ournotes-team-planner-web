"""Synchronize only an audited static build into the tracked Pages directory."""
from pathlib import Path
import json
import shutil
from verify_release import verify

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "browser/dist"
report = verify(source)
target = ROOT / "docs"
if target.resolve() != ROOT / "docs":
    raise SystemExit("Pages output points outside the repository")
if target.exists():
    shutil.rmtree(target)
shutil.copytree(source, target)
verify(target)
print(json.dumps(report, ensure_ascii=False))
