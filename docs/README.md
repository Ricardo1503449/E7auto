# 项目文档

- [使用说明](user/使用说明.md)
- [架构](design/ARCHITECTURE.md)与[行为规则](design/BEHAVIOR.md)
- [自动卡厄思实施计划](design/CHAOS_IMPLEMENTATION_PLAN.md)（已同步模板复用与登记结果）
- [自动卡厄思运行验证](validation/chaos/RUNTIME_VALIDATION.md)（各批次离线、模拟、实机证据及未覆盖场景）
- [自动卡厄思阶段拖动与切换](validation/chaos/STAGE_SELECTION_VALIDATION.md)
- [日期变更共享流程验证](validation/common/DAILY_RESET_VALIDATION.md)（签到、可选登录奖励/增益效果及卡厄思恢复）
- [原图匹配与尺寸适配](design/NATIVE_MATCHING.md)及[验证记录](validation/common/NATIVE_MATCHING_VALIDATION.md)
- [开发指南](development/DEVELOPMENT.md)
- [目录与产物约定](development/LAYOUT.md)
- [模板组织](development/TEMPLATES.md)与[候选登记流程](development/TEMPLATE_WORKFLOW.md)
- [校准指南](development/CALIBRATION.md)与[校准记录](calibration/README.md)
- [发布检查](development/RELEASE_CHECKLIST.md)、[v1.3.4验证记录](releases/v1.3.4/VALIDATION.md)、[v1.3.3源码与本机构建记录](releases/v1.3.3/VALIDATION.md)与[v1.3.2历史构建记录](releases/v1.3.2/VALIDATION.md)
- [源码分层与UI生命周期验证](validation/common/SOURCE_LAYERING_VALIDATION.md)
- [后台能力与Qt/PyWinRT运行库共存验证](validation/platform/BACKGROUND_VALIDATION.md)
- [更新检查、应用内更新和界面验证](validation/ui/NAVIGATION_VALIDATION.md)

`validation/<主题>/` 保存验证结论、范围和限制。原始输出保存在本地 `artifacts/`，不复制到规范文档中。

- [刷商店匿名统计设计](design/SHOP_STATISTICS.md)、[Cloudflare部署与GitHub每日快照](development/SHOP_STATISTICS.md)
- [刷商店匿名统计本地验证](validation/shop/SHOP_STATISTICS.md)

新版本的验证记录在最终提交前集中整理；构建与上传的阶段状态留在本地产物，公开下载状态以 [GitHub Releases](https://github.com/Ricardo1503449/E7auto/releases/latest) 为准。历史版本记录保留原有验收事实。

- [Windows安装包与安装/卸载验收](development/INSTALLER.md)

- [v1.4.27 版本同步与验证范围](releases/v1.4.27/VALIDATION.md)
- [v1.4.26 安装包与版本验证](releases/v1.4.26/VALIDATION.md)
- [v1.4.0 历史构建与开发验证](releases/v1.4.0/VALIDATION.md)
