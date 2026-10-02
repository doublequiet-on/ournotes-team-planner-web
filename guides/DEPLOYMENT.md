# GitHub Pages 发布、更新与回退

正式仓库为 [doublequiet-on/ournotes-team-planner-web](https://github.com/doublequiet-on/ournotes-team-planner-web)，网页地址为 [配队网页版](https://doublequiet-on.github.io/ournotes-team-planner-web/)。部署使用公开仓库的 `main` 分支、`/docs` 目录。

## 部署自己的副本

1. Fork 或克隆源码到自己的公开仓库。
2. 在仓库 Settings → Pages 中选择 **Deploy from a branch**，分支 `main`、目录 `/docs`，保存。
3. 等待 Pages 部署成功，用设置页显示的 HTTPS 地址访问。首次可能自动刷新一次。

仓库已经包含完整静态构建。发布不需要运行 Python/Node 后台，不需要数据库服务或服务器隔离头配置。构建使用相对路径，适用于 `https://用户名.github.io/仓库名/`；不要遗漏 `docs/assets/`、`docs/vendor/`、`docs/planner-runtime.zip`、Service Worker 和 `.nojekyll`。

官方说明：[配置 Pages 发布来源](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)。

## 发布修改

先按开发指南安装依赖，在 `browser/` 构建与验收。随后回到仓库根目录：

```powershell
python -B tools/publish_site.py
python -B tools/verify_release.py
git diff --stat
```

`publish_site.py` 只同步已审计的静态输出，重新生成 `docs/`；说明文档存放在 `guides/`，不会被网站构建覆盖。`docs/` 不要手工编辑，否则下次发布会被覆盖。

检查版本与结果后提交源码、文档和 `docs/`，推送 `main`。Pages 会自动发布该目录；等待部署成功后用正式网址的新浏览器环境验证加载、导入、计算和导出，不能只把 HTTP 200 当作计算成功。

CI 验证源码构建和浏览器行为；Pages 由独立的 branch 部署完成。仓库 CI 与正式站点验收属于不同证据。发布期间旧页面可能继续使用已经载入的程序，用户应保存输入后刷新。

个人卡库按网站路径保存。更新同一地址会保留卡库；迁移仓库名、域名或路径前，提醒用户导出。源码与模型变化可能使旧最优证明不再可复用，属于正常重新计算。

## 回退

用正常 Git revert 撤销有问题的提交，或者从已发布标签取回对应源码和 `docs/`，重新提交并推送。保留现在的用户数据存储路径。等待 Pages 部署成功，再用正式网址检查版本与计算；无需支付或重启计算服务器。

仓库的基准 ZIP 和构建文件较大，建议用 Git 客户端完整提交目录，避免网页上传遗漏文件。不要提交开发机器的整个工作区。
