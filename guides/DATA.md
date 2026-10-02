# 数据、模型与许可证

网页版 v0.1.1 固定读取公开本地版 v0.2.4：

```text
OurNotes-配队程序-v0.2.4.zip
SHA-256: 4e6154e2030bd9c7b4a1049519e0405f67a32fdc71fcef2445f2fcff1d6235e5
数据快照: 2026-10-01
活动 ID: 1
成员卡: 63
Snap: 64
歌曲: 85（转换谱面 340）
```

ZIP 位于 `browser/upstream/`。构建在 SHA-256 不一致时停止。公开包不含原玩家卡库、私人养成观察、截图结算或 SQLite；初始化时使用空白个人卡库。测试生成器只使用合成养成。

## 来源

| 内容 | 公开来源与固定版本 |
| --- | --- |
| Master 数据 | [StarMoe-org/moenotes-masterdata](https://github.com/StarMoe-org/moenotes-masterdata)，`0f8644dd85b7c3d21794fb3f718e48773cd1cc60` |
| 养成字段参考 | [StarMoe-org/moenotes](https://github.com/StarMoe-org/moenotes)，`bb43324feff14e1295f5e798eb3948e60535aa21` |
| 综合力、收益、跳过与 AP 计算参考 | [empty-sekai/ournotes-deck](https://github.com/empty-sekai/ournotes-deck)，`dbd9cf01a4854808dceb6aedcd4041af372faeee` |
| 谱面转换参考 | [MetaSekaiLab/nnnotes](https://github.com/MetaSekaiLab/nnnotes)，`b1e12c4ab31cfed6cd453e562aeeb66c3821af83` |
| 谱面与卡图公共地址 | [bdon / Moenotes](https://bdon.moe/)，实际字节哈希保留在公开源清单 |

收益、综合力、事件加成与谱面计算沿用固定模型；界面、资源循环、搜索和不同歌曲 Top-3 是本项目增加的功能。来源清单位于基准的 `research/2026-10-01/` 与 `licenses/source_manifest.json`。静态网站的 `build-info.json` 记录模型、浏览器版本和运行数据哈希。

当前只覆盖固定活动、普通单人自由演出、撃奏关闭。AP 按模型最不利技能顺序估算评级，截图校准只能证明已核对的样例。公开包启动时复算公共收益公式；原个人综合力截图属于历史校准记录，没有在公开包重新导入该玩家养成计算。页面对此分别说明。

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
