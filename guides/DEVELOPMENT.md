# 开发说明

## 环境与构建

使用 Git、Python 3.12、Node.js 22 或更新版本。本次在 Windows、Python 3.12 和 Node.js 24 验证。pnpm 固定为 11.19.0，依赖与解析结果保存在 `browser/package.json` 和 `browser/pnpm-lock.yaml`。

`browser/pnpm-workspace.yaml` 明确配置依赖的安装脚本：允许锁定版本 esbuild 的构建检查，跳过无需执行的 Protobuf 安装脚本。全新安装和 CI 无需手动交互批准；保留此文件，避免 pnpm 11 因未配置安装脚本而退出。设置说明见 [pnpm 官方文档](https://pnpm.io/settings/build#allowbuilds)。

从仓库根目录执行：

```powershell
cd browser
npx pnpm@11.19.0 install --frozen-lockfile
npx pnpm@11.19.0 build
python -B tests/preview_server.py
```

打开 `http://127.0.0.1:8877/ournotes-planner/`。预览服务器只读取静态 `dist/`，没有计算 API，也不设置隔离响应头，可以验证 Pages 的项目子目录和 Service Worker 首次刷新流程。不要双击 `index.html` 或使用 `file://`。

`build_browser.py` 校验公开基准 ZIP 的 SHA-256，重新生成 `public/`、`index.html`、`generated-app.js` 和 `style.css`，随后调用 Vite。输出 `dist/` 包含全部运行组件，无需运行 Python 后端。`public/` 与上述生成文件每次构建会被覆盖，修改应落在源文件或生成器。

需要开发预览时，先执行 `npx pnpm@11.19.0 prepare-data`，再执行 `npx pnpm@11.19.0 dev`。发布验收使用构建后的静态预览，避免把开发服务器行为当作 Pages 行为。

## 文件入口

| 文件 | 用途 |
| --- | --- |
| `browser/main.js` | 初始化隔离环境、IndexedDB、任务状态、取消与 Web Locks |
| `browser/profile-storage.js` | 卡库保存与旧标签页覆盖保护 |
| `browser/python-worker.js` | Pyodide 初始化、SQLite 快照与同步求解桥接 |
| `browser/browser_runtime.py` | 固定 Python 计算核心的浏览器适配与缓存恢复 |
| `browser/cp_model.py` | 本项目使用的 CP-SAT 建模接口，64 位整数序列化 |
| `browser/solver-worker.js` | OR-Tools WASM 校验、求解与精确整数响应 |
| `browser/service-worker.js` | 项目范围内静态响应的隔离头 |
| `browser/build_browser.py` | 从公开基准生成数据与界面、调用 Vite |
| `browser/vite.config.js` | 相对路径构建，仅打包 CP-SAT 两种运行组件 |
| `browser/upstream.json` | 模型版本、公开包名称及 SHA-256 |
| `browser/tests/` | 原生基准生成、真实浏览器验收 |
| `tools/publish_site.py` | 审计并同步构建输出到 Pages 的 `docs/` |

## 查看模型源码

公开基准 ZIP 已随仓库提交，不依赖作者电脑。以下命令在仓库根目录将其展开到被忽略的 `work/model/`：

```powershell
python -B tools/unpack_model.py
```

展开后可阅读固定基准及公开研究数据。v0.2.0 的可编辑优化源码在 `core/`；`tools/optimized_sources.py` 明确列出允许进入运行包的八个文件。构建先校验 `upstream.json` 的 ZIP，再使用这些文件覆盖对应核心，并加入两个新模块。`build-info.json` 记录每个覆盖文件的哈希，发布审计逐字节核对运行包。修改 `work/model/` 不会改变网页。

等价搜索优化修改 `core/` 并运行 `python -B tools/test_core.py`。不可改变原始 `note_score`、`factor_commands`、`ap_factor_samples` 等对照函数来迎合测试。计分公式、活动或基准界面的改变仍需要新的公开基准与完整 [升级流程](DATA.md)。

纯网页改动可以编辑浏览器适配器及 `build_browser.py`。生成器针对固定界面使用唯一匹配检查；基准界面变化时会停止构建，避免静默生成不完整页面。

本项目对公开基准的模式修复另提供可读差异 [0.2.5-power-modes.patch](../model-fixes/0.2.5-power-modes.patch)，方便审查普通/挑战综合力的变化。实际构建读取固定 ZIP 并应用上述优化层；模式修复差异文件用于说明，不在构建时重复应用。

## 测试与提交

验收命令见 [验证说明](VALIDATION.md)。生成文件只写入 `work/`，使用合成养成；不需要作者个人卡库。修改完成后重新构建，运行相关验收，再从仓库根目录执行：

```powershell
python -B tools/publish_site.py
python -B tools/verify_release.py
git diff --stat
```

查看源码差异和生成网站，再提交。基准 Python 使用原生 OR-Tools，网页版使用 WASM，两者的运行环境不同，需实际比较结果。
