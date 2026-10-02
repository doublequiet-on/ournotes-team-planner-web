# 配队网页版源码 v0.1.1

完整入口见 [仓库 README](../README.md)、[开发说明](../guides/DEVELOPMENT.md)、[验证说明](../guides/VALIDATION.md) 和 [架构说明](../guides/ARCHITECTURE.md)。

在当前目录安装锁定依赖并构建：

```powershell
npx pnpm@11.19.0 install --frozen-lockfile
npx pnpm@11.19.0 build
python -B tests/preview_server.py
```

`upstream/` 已包含经过 SHA-256 校验的公开 v0.2.4 基准 ZIP。`pnpm build` 重新生成公开数据与界面，再构建 `dist/`。运行时使用网页自身的 Pyodide 和 OR-Tools WASM，不需要计算后台。

`public/`、`dist/`、`index.html`、`generated-app.js` 和 `style.css` 为生成文件。卡库、测试输入与下载结果放在被忽略的 `work/`，不能放在这些目录中。发布到 Pages 前，在仓库根目录运行 `python -B tools/publish_site.py`。
