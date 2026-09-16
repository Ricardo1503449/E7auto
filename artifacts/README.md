# 本地产物

使用项目根目录的 `python -m scripts.project.artifacts` 创建任务目录，遵循[目录约定](../docs/development/LAYOUT.md)。

| 目录 | 用途 |
| --- | --- |
| `tasks/<功能>/<任务ID>/` | inputs、previews、results、scratch；任务根目录只有所有权记录及可选README |
| `releases/<版本>/<构建ID>/` | 构建日志、结果、上传证据；辅助脚本进入scratch |
| `git/<任务ID>/` | 提交清单、补丁、信息 |
| `maintenance/<任务ID>/` | 迁移、清理及完整性证据 |
| `template-candidates/<导出ID>/` | 候选PNG、sidecar、来源和登记计划 |
| `template-registration/` | 固定的写锁和事务恢复记录，不移动 |
| `archive/<类别>/<原目录名>/` | 冻结历史资料，`_archive.json`记录完整文件清单与摘要 |

除本说明外，内容默认不提交Git。`check_layout`仍扫描这些被忽略的文件。迁移任务中的 `migration-plan.json` 提供旧路径到新位置的索引；旧报告和登记证据中的历史路径不改写。

不要删除原始样本、唯一资料或登记事务。清理和归档均须用户明确授权，不自动按日期清除。
