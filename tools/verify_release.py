"""Verify that a static site contains exactly generated public assets."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import zipfile
from optimized_sources import sources

ROOT = Path(__file__).resolve().parents[1]


def verify(site):
    site = Path(site).resolve()
    config = json.loads((ROOT / "browser/upstream.json").read_text("utf-8"))
    package = json.loads((ROOT / "browser/package.json").read_text("utf-8"))
    info = json.loads((site / "build-info.json").read_text("utf-8"))
    archive = ROOT / "browser/upstream" / config["archive"]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != config["sha256"]:
        raise ValueError("Public baseline SHA-256 mismatch")
    if (info["browser_version"], info["core_version"], info["public_source_sha256"]) != (package["version"], config["version"], config["sha256"]):
        raise ValueError("Site version or baseline differs from source")
    files = {p.relative_to(site).as_posix(): p for p in site.rglob("*") if p.is_file()}
    for name, digest in info["static_files"].items():
        if name not in files or hashlib.sha256(files[name].read_bytes()).hexdigest() != digest:
            raise ValueError(f"Static asset missing or modified: {name}")
    extra = set(files) - set(info["static_files"]) - {"index.html", "build-info.json"}
    if any(not re.fullmatch(r"assets/[A-Za-z0-9_-]+\.(js|css|wasm)", name) for name in extra):
        raise ValueError(f"Unexpected static files: {sorted(extra)}")
    forbidden = ("player_growth_observations", "party_sample", "challenge_receipt", "ui-exported", ".sqlite", ".env")
    runtime = site / "planner-runtime.zip"
    if hashlib.sha256(runtime.read_bytes()).hexdigest() != info["runtime_sha256"]:
        raise ValueError("Runtime archive SHA-256 mismatch")
    with zipfile.ZipFile(runtime) as payload:
        if payload.testzip() is not None or sorted(payload.namelist()) != sorted(info["payload_files"]):
            raise ValueError("Runtime payload differs from manifest")
        if any(word in name for name in payload.namelist() for word in forbidden):
            raise ValueError("Private state path in runtime")
        for name in ("browser_runtime.py", "cp_model.py"):
            if payload.read(name) != (ROOT / "browser" / name).read_bytes():
                raise ValueError("Runtime contains an outdated browser adapter")
        for name in ('team_candidates.py', 'sample.py'):
            if payload.read(name) != (ROOT / 'workbench' / name).read_bytes():
                raise ValueError('Runtime contains an outdated workbench source')
        for name, raw in sources().items():
            if (payload.read(name) != raw or info["core_overrides"].get(name) != hashlib.sha256(raw).hexdigest()):
                raise ValueError(f"Runtime contains an outdated optimized source: {name}")
    if any(word in name for name in files for word in forbidden):
        raise ValueError("Private state path in site")
    if any(p.stat().st_size >= 100 * 1024 * 1024 for p in files.values()):
        raise ValueError("A static file exceeds GitHub's individual file limit")
    html = (site / "index.html").read_text("utf-8")
    for relative in re.findall(r'(?:src|href)="(\./assets/[^\"]+)"', html):
        if not (site / relative).is_file():
            raise ValueError(f"Missing entry asset: {relative}")
    return {"passed": True, "browser_version": info["browser_version"], "core_version": info["core_version"],
            "runtime_sha256": info["runtime_sha256"], "static_files": len(files),
            "static_bytes": sum(p.stat().st_size for p in files.values())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, default=ROOT / "docs")
    args = parser.parse_args()
    print(json.dumps(verify(args.site), ensure_ascii=False))
