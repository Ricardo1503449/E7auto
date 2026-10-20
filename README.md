# E7auto

面向 Windows 64 位电脑的《第七史诗》自动化工具。

**[下载安装包](https://github.com/Ricardo1503449/E7auto/releases/latest)** · [使用说明](docs/user/使用说明.md) · [更新日志](CHANGELOG.md)

当前检入源码版本为 `v1.4.26`，可下载版本以发布页为准。

## 主要功能

| 功能 | 简介 |
| --- | --- |
| 刷新秘密商店 | 自动购买圣约书签、神秘奖牌，可选购买友情点数，支持天空石预算和连续刷新 |
| 红叶换企鹅 | 按设置的次数批量购买企鹅，显示预计红叶消耗 |
| 自动卡厄思 | 选择阶段、预设阵容和完成轮数，按内置策略进行探查 |

## 开始使用

运行需要管理员权限，游戏所在显示器的桌面分辨率至少为 **2560 × 1440**。安装版无需另装 Python。

1. 下载安装包，完成安装后打开 E7auto。
2. 启动《第七史诗》，登录并回到游戏主界面。
3. 选择功能，填好选项，点击“启动脚本”。

需要停止时，点击悬浮窗红色“停止”按钮；悬浮窗收起时先单击悬浮球展开。详细操作和常见问题见[使用说明](docs/user/使用说明.md)。

## 刷商店社区统计

分别展示默认转运与连续刷新的累计统计，数据来自成功上传的匿名记录，不代表游戏官方概率。统计默认开启，可在设置中关闭。

<!-- shop-statistics:start -->
[![刷商店社区统计](https://e7auto-shop-stats.e7auto-stats.workers.dev/stats.svg)](https://e7auto-shop-stats.e7auto-stats.workers.dev/stats)

[近实时详情](https://e7auto-shop-stats.e7auto-stats.workers.dev/stats) · [每日快照](https://github.com/Ricardo1503449/E7auto/tree/stats)
<!-- shop-statistics:end -->

每日快照保留最近365天的历史JSON，图表只保留最新一份；配置方式见[工作流说明](docs/development/SHOP_STATISTICS.md#github每日快照)。

## 注意事项

- 游戏自动化存在账号受限或封禁风险，程序无法保证账号安全。使用前请阅读[风险说明](docs/user/使用说明.md#使用风险)。
- 运行期间不要手动操作、拖动或最小化游戏，也不要修改显示器分辨率或缩放。
- 升级可直接覆盖安装并保留设置和日志；**卸载会删除这些数据**，需要保留时请提前备份。

开发、校准和验证资料见[项目文档](docs/README.md)与[开发指南](docs/development/DEVELOPMENT.md)。
