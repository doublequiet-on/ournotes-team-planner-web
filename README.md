# Our Notes 配队网页版

[打开网页版](https://doublequiet-on.github.io/ournotes-team-planner-web/) · [使用说明](guides/USAGE.md) · [开发入门](guides/DEVELOPMENT.md)

在浏览器中录入 BanG Dream! Our Notes 日服卡库，计算普通演出与挑战演出的队伍、活动 PT、商店 PT 和不同歌曲前三名。Python 公式与完整搜索在访问者的设备上运行，网站只提供静态文件，适合 GitHub Pages，无需维护计算服务器。

当前网页版 **v0.2.0**，计分模型固定为公开源码 **v0.2.5**，数据快照 **2026-10-01**。初次使用为空白卡库，可导入本地版导出的 JSON。个人卡库和续算进度保存在同一网址、同一浏览器；换设备或清理网站数据之前请导出备份。

**v0.2.0 减少多歌曲和大卡池的重复准备、建模与 AP 精算。** 保留完整候选、两个收益目标及四组歌曲 Top-3，增加分段诊断和有界缓存。请刷新网页重新计算；个人卡库可继续使用。[优化范围与验证](guides/PERFORMANCE.md)。普通与挑战继续分别计算综合力，[模式规则](guides/POWER-MODES.md)不变。

![使用合成养成完成 AP 计算的网页示例](guides/images/preview.png)

目前按固定活动 ID 1、普通单人自由演出、撃奏关闭计算。AP 使用模型的最不利技能顺序评级；推荐结果属于模型估算。普通与挑战阶段各使用固定队伍和歌曲，完整比较资源循环，不计算逐场混队或混歌。新活动、撃奏和未验证技能需要先补充模型与证据。

## 本地构建

需要 Node.js 22 或更新版本、Python 3.12 和 Git。本次验证使用 Node.js 24；使用网页的人无需安装开发工具。

```powershell
git clone https://github.com/doublequiet-on/ournotes-team-planner-web.git
cd ournotes-team-planner-web/browser
npx pnpm@11.19.0 install --frozen-lockfile
npx pnpm@11.19.0 build
python -B tests/preview_server.py
```

通过 [本机预览](http://127.0.0.1:8877/ournotes-planner/) 打开。必须通过 HTTP/HTTPS 访问；首次打开可能自动刷新一次。建议使用电脑的近期 Chrome 或 Edge。首次下载运行组件需要网络，本版不承诺完整离线运行。

## 源码与说明

| 内容 | 入口 |
| --- | --- |
| 录入、计算、保存与续算 | [使用说明](guides/USAGE.md) |
| 独立构建、修改源码、测试 | [开发说明](guides/DEVELOPMENT.md) |
| 浏览器 Worker、模型桥接与存储 | [架构说明](guides/ARCHITECTURE.md) |
| 数据来源、固定基准与升级步骤 | [数据说明](guides/DATA.md) |
| GitHub Pages 发布、更新与回退 | [部署说明](guides/DEPLOYMENT.md) |
| 验收命令与证据边界 | [验证说明](guides/VALIDATION.md) |
| 加载、保存、性能问题 | [故障排查](guides/TROUBLESHOOTING.md) |
| 提交修改 | [贡献说明](CONTRIBUTING.md) |
| 版本变化 | [变更记录](CHANGELOG.md) |

`browser/` 包含可编辑的网页与 Python/WASM 适配源码、锁定依赖、测试和公开基准 ZIP。`docs/` 是已经构建的 Pages 网站，不需要服务器程序。开发指南提供展开基准 Python 公式与研究数据的命令，构建不依赖作者机器上的其他目录。

本项目新增代码采用 [MIT 许可证](LICENSE)。原模型、转换工具和运行组件保留各自许可证；游戏数据、卡图和谱面不由本项目授予版权许可。来源与完整说明见 [第三方说明](THIRD-PARTY-NOTICES.txt)、[许可证目录](licenses/) 和 [数据说明](guides/DATA.md)。这是非官方工具。
