"""Create a reviewed static deployment ZIP and a public validation summary."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile
from verify_release import verify

ROOT = Path(__file__).resolve().parents[1]
report = verify(ROOT / "docs")
checks = ROOT / "work/validation/validation-summary.json"
validation = json.loads(checks.read_text("utf-8"))
if not validation["passed"] or validation["browser_version"] != report["browser_version"] or validation["core_version"] != report["core_version"]:
    raise SystemExit("Validation summary does not match this release")
output = ROOT / "work/release"
output.mkdir(parents=True, exist_ok=True)
archive = output / f"OurNotes-Browser-v{report['browser_version']}-GitHub-Pages.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as package:
    for source in sorted((ROOT / "docs").rglob("*")):
        if source.is_file():
            package.write(source, "docs/" + source.relative_to(ROOT / "docs").as_posix())
    for name in ("guides/DEPLOYMENT.md", "guides/USAGE.md", "LICENSE", "THIRD-PARTY-NOTICES.txt"):
        package.write(ROOT / name, name)
with zipfile.ZipFile(archive) as package:
    if package.testzip() is not None:
        raise ValueError("Release ZIP failed integrity check")
validation.update(static_build=report, deployment_archive={"name": archive.name, "bytes": archive.stat().st_size,
    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()})
(output / "validation-summary.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), "utf-8")
print(json.dumps(validation["deployment_archive"], ensure_ascii=False))
