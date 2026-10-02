"""Expand the hash-pinned public model into ignored work/model/ for reading."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / "browser/upstream.json").read_text("utf-8"))
source = ROOT / "browser/upstream" / config["archive"]
if hashlib.sha256(source.read_bytes()).hexdigest() != config["sha256"]:
    raise SystemExit("Public model SHA-256 mismatch")
target = ROOT / "work/model"
if target.exists():
    raise SystemExit("work/model already exists; choose a separate copy before unpacking again")
prefix = f"OurNotes-配队程序-v{config['version']}/"
with zipfile.ZipFile(source) as archive:
    for item in archive.infolist():
        relative = item.filename.removeprefix(prefix)
        path = Path(relative)
        if relative == item.filename or path.is_absolute() or ".." in path.parts:
            raise ValueError("Invalid public model path")
        destination = target / path
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(item))
print(target)
