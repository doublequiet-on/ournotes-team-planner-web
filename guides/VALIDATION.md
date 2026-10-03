# 验证说明 · v0.3.0

执行 `python -B tools/run_checks.py`。输出在被 Git 忽略的 `work/validation/`，全部输入为合成养成。

- `tools/test_core.py`：原核心回归，保留计分、技能、缓存与优化基础。
- `tools/test_workbench.py`：七类目标与小池枚举对照；每个剩余卡组的最优目标、条件队长、固定队长／绑定、加成底线及无解；固定队伍直接验算；筛选、缓存指纹与取消续算。生成独立原生对照结果。
- `check_power_modes.py`：普通／挑战参数加成区别与详细综合力计算一致。
- `check_workbench.cjs`：真实浏览器产出 15 套、原生与 WASM 目标相同、条件合法、复制／JSON／图片导出、桌面及 768／390 像素布局、指定队伍验算、无解、完整卡池取消、完整候选续算、多标签页、旧卡库迁移和升级规划档案。
- `check_int64.cjs`：WASM 对大于 JavaScript 安全整数的数值保持精确。
- `tools/check_build.py`：两次生成哈希一致、删除过期 public 文件。
- `tools/verify_release.py`：运行包和可编辑源码逐字节一致、静态清单与许可证完整、不包含私人状态路径。

历史 `check_browser.cjs`、`check_lifecycle.cjs` 等旧歌曲界面验收保留作历史参考，不纳入新版 UI 验收。当前 CI 使用上面新工作台流程，不能用旧报告证明新界面已通过。

浏览器验收不等于真实手机游戏实测，不证明任意歌曲最优，也不验证撃奏、新活动或未来快照。发布后另行检查正式 HTTPS 站点的版本、首次隔离初始化、导入、求解与导出。CI 成功和 Pages 部署成功是两个步骤。
