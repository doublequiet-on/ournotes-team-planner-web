# 开发说明 · v0.3.0

项目已安排在本版本发布后暂停；重新启动开发前先阅读 [暂停交接](PAUSED.md)。

安装 Node.js 22+、Python 3.12，并在仓库根目录执行 `python -m pip install -r requirements-test.txt`。然后：

```powershell
cd browser
npx pnpm@11.19.0 install --frozen-lockfile
npx pnpm@11.19.0 build
npx pnpm@11.19.0 exec playwright install chromium
cd ..
python -B tools/run_checks.py
```

Windows 测试默认使用 Edge；Linux 使用 Playwright Chromium。可以设置 `OURNOTES_BROWSER_CHANNEL`。`run_checks.py` 启动本机静态预览并自动关闭，不需要后台服务器。

| 可编辑来源 | 用途 |
| --- | --- |
| `workbench/team_candidates.py` | 无歌曲配队、条件验证、目标及固定队伍验算 |
| `workbench/web/` | 五页工作台 HTML、CSS、JS |
| `workbench/sample.py` | 公开合成卡库 |
| `workbench/app.py` | 可选原生本机 HTTP 入口 |
| `workbench/tests/` | 小池穷举、约束、缓存、取消测试 |
| `core/` | 固定基准之上的 ON 核心修正与优化 |
| `browser/browser_runtime.py`、`cp_model.py` | 浏览器 Python 调度与模型桥接 |
| `browser/main.js`、`python-worker.js`、`solver-worker.js` | 生命周期、存储与 WASM |
| `browser/build_browser.py` | 基准校验、允许列表打包、UI 适配与 Vite 构建 |

`browser/index.html`、`generated-app.js`、`style.css`、`public/`、`dist/` 和 `docs/` 都是生成文件，不能只改输出。构建从已校验的公开 ZIP 取数据和图片，用 `core/` 的允许列表覆盖核心，再加入配队模块与适配器。仓库可独立构建，不依赖作者的本地实验目录。

本机原生界面：先完成网页构建，再执行 `python -B tools/prepare_local.py` 和 `python -B workbench/app.py`。本机入口使用原生 OR-Tools；图片与 runtime 为生成文件，不提交。网页版和本机版的缓存签名分开。

发布前执行 `python -B tools/check_build.py`、`python -B tools/verify_release.py --site browser/dist`；所有验收通过后用 `python -B tools/publish_site.py` 同步 `docs/`。[发布说明](DEPLOYMENT.md)给出合并及回退边界。
