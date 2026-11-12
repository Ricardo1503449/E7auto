# v1.5.6 大厅返回修复与版本验证

日期：2026-11-12。

## 工作区审查与范围

基于`bfeadd2`审查本对话保留的5个改动文件，未发现新增阻断问题。卡厄思大厅结果按身份和位置确认，识别分数波动不再重置三帧计数；通关、战败和清理旧探查共用该关闭流程。企鹅等待接口要求显式提供比较规则，卡厄思拒绝没有定义比较规则的结果类型，包括递归元组中的未知对象。

现有企鹅调用及字符串状态测试均显式传入比较规则。卡厄思当前返回的识别结果、地图、字符串/自动战斗状态、整数和元组均有对应规则。模板、ROI、颜色/结构门槛、匹配后端、点击次数、页面期限和网络恢复策略没有变化。原有未跟踪`pip.ini`及本地产物保留。

## 复用的定向验证

复用本对话已完成的18个不同参数用例，新增4例、既有14例全部通过，不因版本更新或构建重复执行。

- 大厅修复：新增2322×1306及1536×864的分数波动用例2例，既有多轮通关、第三层首领战败、清理旧探查、大厅缺失超时、关闭后重连及手动停止6例。
- 接口约束：新增未知结果与嵌套未知结果首帧拒绝2例；既有企鹅导航三帧、企鹅重连确认、卡厄思阶数整数元组及自动战斗状态4例。
- 超时诊断：既有网络图标持续存在无自动总上限、网络消失后未知页面按原期限停止、已过业务期限在恢复后停止及手动中断网络等待4例。

前两组具体函数和参数见[点击确认验证](../../validation/common/CLICK_RETRY_VALIDATION.md)。超时诊断来自`tests/automation/test_network.py`的`test_continuously_visible_icon_has_no_automatic_wait_limit`、`test_unknown_business_page_does_not_extend_network_pause_or_renew_its_deadline`、`test_expired_business_deadline_is_applied_after_network_clearance`及`test_manual_stop_interrupts_icon_wait_without_retry_or_status_restore`。普通识别不稳定不会刷新截止时间，网络等待仍按现有设计扣除业务计时；用户提供的日志在旅程关闭后约10秒手动停止，不能认定15秒有效期限失效。

## 本轮实际版本检查

版本从1.5.5更新为1.5.6，同步项目及源码版本、README、CHANGELOG、随包说明标题和安装包名、文档索引。通过无索引、无依赖变更的editable安装刷新项目元数据，未安装或升级其他依赖。

仅执行`tests/scripts/release/test_version_sync.py`中的以下3个既有用例，全部通过：

- `test_active_source_document_and_environment_versions_are_synchronized`：项目、源码、已安装元数据及使用文档版本一致。
- `test_source_self_check_reports_the_project_version`：源码自检输出1.5.6。
- `test_environment_verifier_requires_exact_versions`：项目版本及锁定依赖一致。

`scripts.release.workflow preflight`返回1.5.6。

## 构建与完整安装验收

构建ID为`20261112-203849-installer-7839c544`，使用项目登记的Nuitka 4.1.3、GCC 15.2.0及Inno Setup 6.7.3。构建入口实际完成版本和环境核对、输入快照、编译、原生运行库归一化、payload冻结及安装EXE封存，源码输入与封存快照一致。

发布工具以`lifecycle_scope_changed`判定完整模式，变化范围为版本及受保护输入，未复用旧安装生命周期证据。在Windows 11 10.0.22631 AMD64上，以本次构建专属中文空格路径执行全部14项安装检查，全部通过：安装路径、默认无桌面图标、开始菜单、实际安装内容与编译版自检、运行中安装/卸载拒绝、覆盖保留数据、停用文件清理、桌面图标选择、卸载删除数据与登记、保留无关文件、空根目录删除及环境变量不变。

实际安装及覆盖后的编译版核对通过，版本为1.5.6；`compiled`、`wgc_importable`、`update_installation_valid`、`update_signature_ready`及`statistics_runtime_ready`均为true，更新安装身份和签名错误字段为空。安装目录逐文件内容、配置、模板、UI资源、原生库及拥有文件清单均由发布验证工具核对，报告无问题。

测试副本完整卸载后，发布工具将`E7auto_v1.5.6_Setup_x64.exe`提升到`dist`，并只删除dist直属旧版标准包`E7auto_v1.5.5_Setup_x64.exe`。本次编译及staging目录按工具边界清理，验收报告绑定新包摘要；再次核对dist中安装EXE的实际SHA-256与封存记录一致，安装生命周期报告绑定有效。

本地证据位于`artifacts/releases/v1.5.6/20261112-203849-installer-7839c544/`，包含`build-inputs.json`、`build-artifact.json`、`build-result.json`、`release-verification.json`、`installer-verification.json`、`distribution-result.json`及构建/安装原始日志。原始摘要及阶段输出保留在该目录，不改写旧版本证据。

## 未验证范围

未运行完整测试套件、无关界面测试、实机游戏自动化或新的图像/WGC专项验证，未覆盖干净虚拟机及其他Windows版本。最终Git输入与安装包的一致性由本批提交后执行的`workflow check --revision HEAD`核对，结果写入本次构建证据。
