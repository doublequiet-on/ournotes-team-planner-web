# 数据、模型与许可证

网页版 v0.3.0 的养成与技能基准固定为公开源码 v0.2.5，应用仓库 `core/` 修正层及 `workbench/` 无歌曲配队模块：

```text
OurNotes-配队程序-v0.2.5.zip
SHA-256: 396a5b670f8e15f7ce3e5582ca9ed114658d40de801f9d7233351027888ca914
数据快照: 2026-10-01
活动 ID: 1
成员卡: 63
Snap: 64
历史基准内歌曲: 85（转换谱面 340）；新版配队不使用歌曲或谱面
```

ZIP 位于 `browser/upstream/`。构建在 SHA-256 不一致时停止。优化层的八个源码文件及其 SHA-256 另记录在 `build-info.json` 的 `core_overrides` 中；运行包必须逐字节匹配这些文件。公开包不含原玩家卡库、私人养成观察、截图结算或 SQLite；初始化时使用空白个人卡库。测试生成器只使用合成养成。

## 来源

| 内容 | 公开来源与固定版本 |
| --- | --- |
| Master 数据 | [StarMoe-org/moenotes-masterdata](https://github.com/StarMoe-org/moenotes-masterdata)，`0f8644dd85b7c3d21794fb3f718e48773cd1cc60` |
| 养成字段参考 | [StarMoe-org/moenotes](https://github.com/StarMoe-org/moenotes)，`bb43324feff14e1295f5e798eb3948e60535aa21` |
| 综合力、收益、跳过与 AP 计算参考 | [empty-sekai/ournotes-deck](https://github.com/empty-sekai/ournotes-deck)，`dbd9cf01a4854808dceb6aedcd4041af372faeee` |
| 谱面转换参考 | [MetaSekaiLab/nnnotes](https://github.com/MetaSekaiLab/nnnotes)，`b1e12c4ab31cfed6cd453e562aeeb66c3821af83` |
| 谱面与卡图公共地址 | [bdon / Moenotes](https://bdon.moe/)，实际字节哈希保留在公开源清单 |

综合力、事件加成与技能合同沿用固定模型；新版界面和无歌曲配队是本项目功能，旧资源循环和歌曲 Top-3 已退出使用流程。来源清单位于基准的 `research/2026-10-01/` 与 `licenses/source_manifest.json`。静态网站的 `build-info.json` 记录模型、浏览器版本和运行数据哈希。

v0.2.5 按游戏帮助修正本项目调用上下文：普通演出关闭成员与 Snap 的活动参数加成，挑战开启；PT 加成保持独立。详见 [模式修复说明](POWER-MODES.md) 与 [基准差异](../model-fixes/0.2.5-power-modes.patch)。固定 Master 与原始参考源码未更换。

当前覆盖固定活动、普通／挑战单人、撃奏关闭。新版不计算具体谱面评级和结算收益；潜力指标假设 AP/PERFECT、正生命值、每名成员发动一次且均匀覆盖，不包含顺序与截断。历史截图校准不能代替新版游戏实测。项目暂停后不会自动更新数据。

## 更新固定基准

1. 先核对新活动或机制的数据、公式和来源，展开现有基准阅读调用路径。
2. 在独立目录完成模型/原界面修改，生成新的公开分享 ZIP；移除个人输入、截图、缓存和安装文件。
3. 将新的 ZIP 放入 `browser/upstream/`，更新 `upstream.json` 的版本、文件名和实际 SHA-256。
4. 更新 `build_browser.py` 的界面匹配及活动/数据说明。匹配失败必须修正，不能删除检查以强行通过。
5. 重新生成原生基准，运行浏览器结果、整数精度、续算与多标签页验收；比较收益、实际得分和歌曲 Top-3。
6. 更新版本、文档和 `docs/`，发布新版本，保留可回退的旧版本。

本地版本或公共源发生更新不会自动进入网页。不要将新数据替换到已验证的旧公式后直接宣布支持新活动。

## 许可证

本项目新增源码与文档采用根目录 MIT 许可证。原模型保留 MIT/Apache-2.0 文本，nnnotes 参考转换源码保留 MIT。Pyodide、CPython、Emscripten、OR-Tools WASM、Protobuf 和 Long 的许可证随构建发布，位于网站 `licenses/`；新增运行许可证源位于 `browser/license-source/`。

moenotes 养成参考快照没有根许可证文件，本项目没有为其声明许可授予。游戏数据、卡图和谱面的权利归各权利人；本项目代码许可证不授予这些素材的版权。完整记录见根目录和网站的 `THIRD-PARTY-NOTICES.txt`。再次分发时保留相应说明与许可证。
