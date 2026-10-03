"""Prepare native runtime and public images from the audited static build."""
from pathlib import Path
import shutil
import zipfile
from verify_release import verify

ROOT = Path(__file__).resolve().parents[1]
site = ROOT / 'browser/dist'
verify(site)
destination = ROOT / 'workbench/runtime'
destination.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(site / 'planner-runtime.zip') as archive:
    for name in archive.namelist():
        if name in ('browser_runtime.py', 'cp_model.py', 'team_candidates.py', 'sample.py'):
            continue
        target = (destination / name).resolve()
        if not target.is_relative_to(destination.resolve()):
            raise ValueError('Runtime path outside destination')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(name))
shutil.copytree(site / 'card-images', ROOT / 'workbench/web/card-images', dirs_exist_ok=True)
print('Native runtime ready: python -B workbench/app.py')
