"""Prepare a static browser build from the already sanitized public release.

Never copies the owner's profile, private SQLite cache, or screenshot receipts.
The original application and its Windows packages are not changed.
"""
from pathlib import Path
import hashlib
import io
import json
import shutil
import zipfile
import argparse
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tools"))
from optimized_sources import sources
PUBLIC = HERE / "public"
VERSION = json.loads((HERE / "package.json").read_text("utf-8"))["version"]


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"UI source changed: {old[:70]}")
    return text.replace(old, new)


def add_runtime(archive, name, raw):
    entry = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = 0o644 << 16
    archive.writestr(entry, raw)


def main():
    upstream = json.loads((HERE / "upstream.json").read_text("utf-8"))
    core_version = upstream["version"]
    source = HERE / "upstream" / upstream["archive"]
    if not source.is_file():
        source = ROOT / upstream["archive"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != upstream["sha256"]:
        raise ValueError("Public baseline archive has changed; update and revalidate upstream.json explicitly")
    prefix = f"OurNotes-配队程序-v{core_version}/"
    # This is exclusively a generated directory. An old asset or accidental
    # profile file must never enter the next Vite build through public/.
    if PUBLIC.resolve() != HERE / "public":
        raise ValueError("Generated public directory points outside the build directory")
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir()
    payload = io.BytesIO()
    included = []
    overrides = sources()
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as runtime:
        for info in archive.infolist():
            relative = info.filename.removeprefix(prefix)
            if relative == info.filename or ".." in Path(relative).parts:
                raise ValueError("Invalid public archive path")
            if any(word in relative for word in ("player_growth_observations", "party_sample", "challenge_receipt", "private", ".sqlite", "ui-exported")):
                raise ValueError("Private file in public release")
            if relative in ("planner_core.py", "search_cache.py", "solver_search.py", "score_bounds.py") or relative.startswith("research/"):
                raw = overrides.get(relative, archive.read(info))
                add_runtime(runtime, relative, raw)
                included.append(relative)
            elif relative.startswith("web/card-images/"):
                target = PUBLIC / relative.removeprefix("web/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
            elif relative.startswith("licenses/") or relative == "THIRD-PARTY-NOTICES.txt":
                target = PUBLIC / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        for name in ("browser_runtime.py", "cp_model.py"):
            add_runtime(runtime, name, (HERE / name).read_bytes())
            included.append(name)
        for name in ("team_candidates.py", "sample.py"):
            add_runtime(runtime, name, (ROOT / "workbench" / name).read_bytes())
            included.append(name)
        for name, raw in overrides.items():
            if name not in included:
                add_runtime(runtime, name, raw)
                included.append(name)
    (PUBLIC / "planner-runtime.zip").write_bytes(payload.getvalue())
    pyodide = HERE / "node_modules/pyodide"
    destination = PUBLIC / "vendor/pyodide"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"):
        shutil.copyfile(pyodide / name, destination / name)
    licenses = PUBLIC / "licenses"
    for package, name in (("or-tools-wasm", "OR-Tools-WASM-LICENSE.txt"), ("protobufjs", "protobufjs-LICENSE.txt"), ("long", "long-LICENSE.txt")):
        shutil.copyfile(HERE / "node_modules" / package / "LICENSE", licenses / name)
    for license_file in (HERE / "license-source").glob("*.txt"):
        shutil.copyfile(license_file, licenses / license_file.name)
    shutil.copyfile(ROOT / "LICENSE", licenses / "project-LICENSE.txt")
    with (PUBLIC / "THIRD-PARTY-NOTICES.txt").open("a", encoding="utf-8") as notices:
        notices.write("\n\n===== BROWSER RUNTIME =====\n")
        notices.write("Pyodide 314.0.7 (MPL-2.0 and bundled component notices): https://github.com/pyodide/pyodide/tree/314.0.7\n")
        notices.write("or-tools-wasm 0.9.1 (Apache-2.0): https://github.com/Axelwickm/or-tools-wasm\n")
        notices.write("protobufjs 7.5.4 (BSD-3-Clause): https://github.com/protobufjs/protobuf.js\n")
        notices.write("long 5.3.2 (Apache-2.0): https://github.com/dcodeIO/long.js\n")
        notices.write("Full added license texts are in licenses/. Runtime assets are self-hosted; no cloud solving is used.\n")
    shutil.copyfile(HERE / "service-worker.js", PUBLIC / "service-worker.js")
    (PUBLIC / ".nojekyll").write_text("", "utf-8")
    html = (ROOT / "workbench/web/index.html").read_text("utf-8")
    html = replace_once(html, '<link rel="stylesheet" href="/style.css"><script src="/app.js" defer></script>',
                        '<link rel="stylesheet" href="./style.css"><script type="module" src="./main.js"></script>')
    html = replace_once(html, '<head>', '<head><meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\' \'unsafe-eval\'; worker-src \'self\' blob:; img-src \'self\' data: blob:; connect-src \'self\'; object-src \'none\'; base-uri \'self\';">')
    html = html.replace('本地版 0.3.0', '网页版 ' + VERSION).replace('本地测试', '浏览器计算')
    html = html.replace('独立测试版不会覆盖线上网页。', '本版本发布后项目暂停维护，数据不会自动更新。')
    html = html.replace('<button id="exit" class="secondary">退出本地程序</button>', '<button id="exit" hidden>退出</button>')
    html = replace_once(html, '<div id="loadError"', '<div id="browserLoading" class="notice" role="status">正在准备计算组件…</div><div id="loadError"')
    (HERE / "index.html").write_text(html, "utf-8", newline="\n")
    (HERE / "style.css").write_bytes((ROOT / "workbench/web/style.css").read_bytes())
    app = (ROOT / "workbench/web/app.js").read_text("utf-8")
    app = app.replace("const STORE = 'ournotes-team-lab-v1-profile', ARCHIVES = 'ournotes-team-lab-v2-archives', JOB = 'ournotes-team-lab-v1-job';",
        "const STORE = 'ournotes-browser-workbench-v1-profile:' + window.Planner.scope, ARCHIVES = 'ournotes-browser-workbench-v2-archives:' + window.Planner.scope, JOB = 'ournotes-browser-workbench-job:' + window.Planner.scope;")
    app = app.replace('const legacy=localStorage.getItem(STORE)', "const legacy=localStorage.getItem(STORE)||localStorage.getItem('ournotes-browser-planner-v1-profile:'+window.Planner.scope)")
    app = app.replace("fetch(", "window.plannerFetch(")
    # A browser worker ends on reload; persisted complete rows are resumed by a new job.
    app = app.replace('const saved=sessionStorage.getItem(JOB);if(saved){resultInput=clone(state);await follow(saved);}', "if(sessionStorage.getItem(JOB)){sessionStorage.removeItem(JOB);notice('上次计算已中断。再次计算相同条件可复用已保存的完整候选。');}")
    (HERE / "generated-app.js").write_text(app, "utf-8")
    report = {"browser_version": VERSION, "core_version": core_version,
              "optimizer_revision": 2,
              "core_overrides": {name: hashlib.sha256(raw).hexdigest() for name, raw in overrides.items()},
              "public_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "runtime_sha256": hashlib.sha256(payload.getvalue()).hexdigest(),
              "python_runtime": "Pyodide 314.0.7", "solver": "or-tools-wasm 0.9.1",
              "payload_files": included, "private_files_included": False,
              "static_files": {p.relative_to(PUBLIC).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted(PUBLIC.rglob("*")) if p.is_file() and p.name != "build-info.json"}}
    (PUBLIC / "build-info.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8", newline="\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("payload_files", "static_files")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true", help="Also build the prepared site with Vite")
    args = parser.parse_args()
    main()
    if args.build:
        subprocess.run(["node", str(HERE / "node_modules/vite/bin/vite.js"), "build"], cwd=HERE, check=True)
