# 验证说明

验收使用公开 v0.2.4 基准和明确的合成养成，在真正的 Pyodide/OR-Tools WASM 中计算，再与原生 Python 结果比较。没有原玩家卡库或结算截图依赖。

## 运行验收

先按开发说明完成 `browser/` 的依赖安装与构建。在仓库根目录创建原生参考环境：

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-test.txt
.venv/Scripts/python -B tools/run_checks.py
```

Windows 默认使用已安装的 Edge。Linux/macOS 默认使用 Playwright 自带 Chromium，先在 `browser/` 执行 `npx pnpm@11.19.0 exec playwright install chromium`；Linux CI 可以使用 `install --with-deps chromium`。使用其他已安装浏览器可设置 `OURNOTES_BROWSER_CHANNEL=chrome` 或 `msedge`。已有 Playwright 模块可由 `OURNOTES_PLAYWRIGHT_MODULE` 指定，正常安装本仓库依赖无需设置。

Linux/macOS 的原生环境命令是 `.venv/bin/python`。浏览器测试需要可启动浏览器的环境，不能用 Python 单测替代。

完整验收需要数分钟。`run_checks.py` 自动生成原生结果，启动无计算 API/无隔离头的本机静态预览，顺序执行浏览器验收，最后停止预览。生成数据、截图和结果全部写到 `work/validation/`，不进入源码提交或静态网站。

## 覆盖范围

| 检查 | 验证内容 |
| --- | --- |
| `check_browser.cjs` | Pages 子目录、首次刷新与隔离、空白卡库、AP、85 首普通 EXPERT 跳过、CP-SAT AP、混合与取整预算、2004 判定目标技能、收益与四组歌曲 Top-3 |
| `check_lifecycle.cjs` | 取消不展示部分最优、刷新后卡库与证明保留、恢复复用步骤、四组不同歌曲 Top-3、多标签页计算锁、全 63 成员/64 Snap 跳过卡池 |
| `check_int64.cjs` | WASM 最优解 `9007199254740997` 不因 JavaScript 精度而舍入 |
| `check_review.cjs` | 旧标签页覆盖保护、损坏缓存恢复、Worker 退出及时报错和导出保留 |
| `check_images.cjs` | 模拟卡图临时 503，有限重试后恢复显示 |
| `check_score_oracles.py` | 浏览器所选队伍的报告得分用原生公式重新计算 |
| `verify_release.py` | 固定源 SHA-256、源码/网站版本一致、公开资源清单与哈希、完整运行归档、无个人状态路径 |
| `check_build.py` | 相同源码生成相同运行 ZIP，误放在生成 public 目录的文件不会进入下一次构建 |

浏览器测试比较目标收益、另一种收益、剩余 CP、普通/挑战综合力、歌曲 ID 和 Top-3，并要求最优证明。报告得分还由原生公式独立复算。布局覆盖 1440、768、390 像素和全部 127 张卡图。

测试阻断其他来源请求，确认计算在网站自身静态文件下完成。开发机器防护软件注入的请求单独记录，不混同于应用请求；这不是系统级网络抓包证明。

## 证据边界

桌面浏览器的真实计算通过，不等于手机实机、QQ 内置浏览器或所有 AP 全卡池都可以快速完成。全卡池性能用的是合成养成与跳过方式。没有完成搜索时，程序不会把部分结果作为全局最优展示。

CI 验证源码；本机静态预览验证运行；正式 Pages 验证部署路径与实际资源。这三项需要分别记录。网页首次下载需要网络，本版不承诺完整离线运行。新活动和撃奏仍需独立机制验证。

本次修复前已分别复现旧页覆盖、损坏缓存阻断加载、死 Worker RPC 超时，修复后以上回归检查通过。公开发布的结果摘要附在 Release，不附带测试输入和浏览器下载文件。
