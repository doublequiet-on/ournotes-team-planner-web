"""Prepare a static browser build from the already sanitized public release.

Never copies the owner's profile, private SQLite cache, or screenshot receipts.
The original application and its Windows packages are not changed.
"""
from pathlib import Path
import hashlib
import io
import json
import re
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
        html = archive.read(prefix + "web/index.html").decode("utf-8").replace("\r\n", "\n")
        app = archive.read(prefix + "web/app.js").decode("utf-8").replace("\r\n", "\n")
        css = archive.read(prefix + "web/style.css")
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
    html = replace_once(html, '<link rel="stylesheet" href="/style.css"><script src="/app.js" defer></script>',
                        '<link rel="stylesheet" href="./style.css"><script type="module" src="./main.js"></script>')
    html = replace_once(html, '<head>', '<head><meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\' \'unsafe-eval\'; worker-src \'self\' blob:; img-src \'self\' data:; connect-src \'self\'; object-src \'none\'; base-uri \'self\';">')
    html = replace_once(html, '<title>', '<link rel="icon" href="./favicon.svg"><title>')
    (PUBLIC / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="#7667de"/><path d="M32 8 38 26 56 32 38 38 32 56 26 38 8 32 26 26Z" fill="white"/></svg>', "utf-8")
    html = html.replace("OUR NOTES / LOCAL PLANNER", "OUR NOTES / BROWSER PLANNER").replace("本地运行", "浏览器计算")
    html = replace_once(html, '<div id="loadError"', '<div id="browserLoading" class="notice" role="status" aria-live="polite">正在准备浏览器计算组件，首次打开需要下载，请稍候…</div>\n <div id="loadError"')
    html = html.replace('data-tab="software">软件更新', 'data-tab="software">关于网页版')
    start = html.index(' <section id="software"')
    end = html.index('</section>', start) + len('</section>')
    html = html[:start] + f''' <section id="software" class="tab-page" hidden><div class="panel">
<div class="section-kicker">无需安装 / 浏览器计算</div><h2>Our Notes 配队网页版 v{VERSION}</h2>
<p>计算在你自己的设备上运行，无需登录或安装程序。个人卡库保存在当前浏览器，计算输入不会上传到计算服务器。</p>
<p>可以导入本地版导出的卡库。换设备、换浏览器或清理网站数据之前，请先导出卡库。</p>
<p>计算时请保持页面打开。手机切到后台或关闭页面可能中断；已保存的完整步骤可在下次继续。</p>
<p>当前沿用本地版 v{core_version} 的公式和 2026-10-01 数据，只支持对应活动、普通单人自由演出及撃奏关闭的计算范围。</p>
<p>大卡库的速度和可用内存取决于设备。只有完成完整搜索与最优验证后才展示方案和前三首乐曲。</p>
<p>网页更新后刷新即可使用；自己的卡库仍保存在同一网址和浏览器。发生加载问题时，可用电脑的近期 Chrome 或 Edge 重新打开。</p>
<a href="./THIRD-PARTY-NOTICES.txt" target="_blank" rel="noopener">数据与第三方软件说明</a>
</div></section>''' + html[end:]
    html = html.replace("卡图已保存在本地", "卡图随网页提供")
    html = html.replace('id="profileBadge" class="badge">截图示例', 'id="profileBadge" class="badge">个人卡库')
    html = replace_once(html, f'Our Notes 配队与收益 · v{core_version}', f'Our Notes 配队网页版 · v{VERSION} · 模型 v{core_version}')
    (HERE / "index.html").write_text(html, "utf-8", newline="\n")
    (HERE / "style.css").write_bytes(css)
    app = replace_once(app, 'const STORE = "ournotes-local-planner-v1-profile";', 'const STORE = "ournotes-browser-planner-v1-profile:" + window.Planner.scope;')
    profile_code = '''import {createProfileStorage} from './profile-storage.js';
function profileWarning(id, text) {
  let panel = document.getElementById(id);
  if (!panel) {
    panel = document.createElement('div'); panel.id = id;
    panel.className = 'notice error'; panel.setAttribute('role', 'status');
    document.getElementById('browserLoading').after(panel);
  }
  panel.textContent = text;
}
const profileStore = createProfileStorage(STORE, () => {
  document.getElementById('inputArea').disabled = true;
  ['demo','new','import'].forEach(id => document.getElementById(id).disabled = true);
  profileWarning('browserProfileConflict', '另一标签页已更新卡库。请刷新本页读取最新卡库；若要保留本页输入，请先导出卡库。');
}, () => profileWarning('browserProfileStorageError', '浏览器无法保存卡库，本次输入仅在当前页面。请使用「导出卡库」备份后再关闭网页。'));
'''
    app = replace_once(app, 'const STORE = "ournotes-browser-planner-v1-profile:" + window.Planner.scope;',
                       'const STORE = "ournotes-browser-planner-v1-profile:" + window.Planner.scope;\n' + profile_code)
    save_start = app.index('function save() {')
    save_end = app.index('function changed()', save_start)
    app = app[:save_start] + 'function save() {profileStore.save(JSON.stringify(state));}\n' + app[save_end:]
    app = replace_once(app, 'const saved = localStorage.getItem(STORE);', 'const saved = profileStore.load();')
    app = replace_once(app, 'function replaceState(x) { if (jobId) return;', 'function replaceState(x) { if (jobId || profileStore.outOfDate) return;')
    app = replace_once(app, '$("inputArea").disabled = on;', '$("inputArea").disabled = on || profileStore.outOfDate;')
    app = replace_once(app, '$(id).disabled = on);', '$(id).disabled = on || profileStore.outOfDate);')
    app = replace_once(app, 'if (jobId || startingJob || updatingSoftware) return;', 'if (jobId || startingJob || updatingSoftware || profileStore.outOfDate) return;')
    app = app.replace("fetch(", "window.plannerFetch(")
    app = replace_once(app, 'if (e.target.matches?.(".card-art img")) e.target.parentElement.classList.add("image-failed");', '''if (e.target.matches?.(".card-art img")) {
    const image = e.target, retries = Number(image.dataset.imageRetries || 0);
    if (retries < 2) {
      image.dataset.imageRetries = String(retries + 1);
      setTimeout(() => {
        if (!image.isConnected) return;
        const url = new URL(image.src); url.searchParams.set('image_retry', String(retries + 1));
        image.src = url.href;
      }, 750 * (retries + 1));
    } else image.parentElement.classList.add("image-failed");
  }''')
    app = replace_once(app, '启动时重新运行校准，已通过综合力与两组结算收益检查。新队伍和推荐乐曲仍属于模型估算。',
                       '${calibration.power_recomputed ? "启动时已重新核对综合力与两组收益。" : "已重新核对公共收益公式；综合力展示历史截图校准记录，当前分享包不含原个人养成。"}新队伍和推荐乐曲仍属于模型估算。')
    start = app.index("function softwareMessage(")
    end = app.index("async function calculate(", start)
    app = app[:start] + app[end:]
    for line in ('  $("updateControls").disabled = on || updatingSoftware || !softwareInfo?.supported;\n',
                 '  $("exitSoftware").disabled = on || updatingSoftware;\n',
                 '    await initSoftwareUpdate();\n', '    softwareBusy(updatingSoftware);\n'):
        app = replace_once(app, line, "")
    (HERE / "generated-app.js").write_text(app, "utf-8")
    report = {"browser_version": VERSION, "core_version": core_version,
              "optimizer_revision": 1,
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
