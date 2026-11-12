# v1.5.5 重连确认与版本验证

日期：2026-11-12。

## 范围

当前准备范围为此前保留的32个重连改动文件，以及1.5.5版本与说明同步。工作区基于3cbd5d0；此前统计服务与私有网页的提交保持独立，不在本轮重复修改。

公共恢复流程在三类网络信号连续缺失至少三张新帧且持续至少300ms后结束暂停；100ms轮询通常需要四帧。当前业务的只读检测使用这些新帧继续原确认进度，完整或部分结果通过既有Confirmation交接。网络信号重现时清空连续证据，业务状态未知时沿用原业务期限，不延长网络暂停。

商店、企鹅和卡厄思接入同一过程，保留已用次数、已提交动作与响应锁。当前等待期间冻结业务有效时钟，等待耗时只扣除一次；视觉稳定观察继续使用连续时间，奖励卡保留600ms要求。持续转圈仍没有自动等待总上限。

本次不增加点击前截图，不改变输入接口、源帧拒绝条件、模板、遮罩、ROI、颜色/结构门槛或基准/非基准匹配后端。原32个文件的SHA-256均与上一轮验证记录一致。

## 复用的业务验证

复用2026-11-12已完成的定向验证，未因准备提交或版本同步重复运行：126个不同参数用例中，新增20个全部通过，既有105个通过、1个失败。范围包含公共恢复、计时、停止、拖动释放、有限重试、导航与日期处理，以及三个功能当前阶段的确认和已提交状态保留。

唯一失败为tests/automation/test_refresh.py中的test_post_refresh_balance_frames_supply_reusable_stable_top_scan。实际日志同时包含shop_entry和after_refresh_balance，旧断言只期待后者；已在ceb665a的原始Python源码中复现相同失败。本轮没有修改该测试或将此失败计为通过。

业务证据位于本地任务artifacts/tasks/common/20261111-235621-network-business-confirmation-cbd0f2f6/，具体函数、参数、最后一次状态及中间失败见results/validation-summary.json，原提交对比见results/existing-refresh-assertion.json。模板和匹配流程未变化，继续复用原视频、实际ROI正反例、人工缩放及旧文字识别结论，不新增真实非基准样本结论。

## 本轮实际版本验证

版本从1.5.4更新为1.5.5，同步pyproject.toml、源码版本、README、CHANGELOG、使用说明标题与安装包名、文档索引及本记录。通过无索引、无依赖变更的editable安装刷新本项目元数据，未安装或升级其他依赖。

| 既有用例或检查 | 实际范围 | 结果 |
| --- | --- | --- |
| test_active_source_document_and_environment_versions_are_synchronized | 项目、源码、已安装元数据、README、使用说明和更新日志版本 | 通过 |
| test_source_self_check_reports_the_project_version | 替换外部调用后的源码自检版本输出 | 通过 |
| test_environment_verifier_requires_exact_versions | 项目元数据与锁定依赖核对 | 通过 |
| scripts.release.workflow preflight | 版本、说明标题、安装包名及更新日志一致性 | 返回1.5.5 |

上述三个用例均来自tests/scripts/release/test_version_sync.py，未新增测试。审核产物和本轮输出位于本地artifacts/git/20261112-150319-network-version-review-bcd7f9df/。

## 未验证范围

未运行完整测试套件、无关UI测试、实机游戏自动化或新的WGC专项验证。本轮未构建1.5.5安装包、未进行编译版与安装生命周期验收，也未创建提交、推送、标签或Release。版本预检及源码自检单测不替代这些检查。
