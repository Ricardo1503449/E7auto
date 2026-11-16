# 更新分发验证

## 2026-11-05 实现范围

采用用户选择的第三方加速源及独立updates分支方案。客户端并行读取签名文件源，
选择较高有效版本，迁移旧API等待，安装包在完整性保护下顺序换源；失败弹窗提供
发布页，等待时禁止无效即时重试。发布工具绑定既有构建/安装验收及官方Latest附件，
非强制更新独立分支。未改变游戏功能、识别配置、模板或输入能力。

签名身份已在登记的本地私有工具状态初始化并DPAPI保护，客户端固定证书摘要及
公开验签样本。没有新增Python依赖、系统证书或付费资源。目录登记、源码、测试及
当前开发/用户说明同步；原v1.5.0安装包、发布内容和历史验收记录保持原样。

原始输出位于本地
`artifacts/tasks/ui/20261105-203016-signed-update-feed-a7b7add9/`。

## 验证记录

本轮新增清单协议、原生验签、双源及下载切换、发布工具的定向用例，并选择既有
更新请求生命周期、缓存、流式下载、HTTPS跳转、取消及弹窗用例。未运行完整测试套件。
首轮80项通过；一个超长参数名称超过Windows环境变量长度，在setup和teardown分别
报错一次。已改用短参数名称，原始失败输出及缓存写入权限警告保留；后续测试使用
受管任务内缓存位置，没有修改既有缓存权限。

第二轮38项通过，覆盖失败参数及随后修改的重定向、等待持久化、迟到回调和对应
网络路径，另核对签名原生扩展缺失拒绝及源程序自检。此轮曾将同一用例的两个阶段
错误误判为两个用例，因此多补跑了一个此前已通过的重复字段参数，超出最小补跑
范围；这是本轮执行偏差，不能以测试通过替代范围合规。该事实明确保留，不扩大
为其他无关测试的补跑理由。

第三轮27项通过，范围为此前未覆盖的缓存损坏/退避/写入失败、错误弹窗的容器初始
焦点和键盘关闭、同版本说明优先级及幂等发布时的远端引用复核。之后两轮各2项，
覆盖深层JSON、默认CLI不远端写入、实际读取响应上限及新增回调的窗口释放；
最后只扩展并补跑1项原生签名集成用例，将真实临时CMS签名直接交给客户端清单
解码器，验证发布端与客户端的字节协议兼容。

各轮有交集，不相加为新总数。按最终参数和选择清单去重，本轮新增62个用例、
选择56个既有用例，共118个不同用例通过。具体函数和参数清单在任务results的
validation-scope.json；仅为核对范围额外执行过collect-only，没有再次执行用例。

原生验签实际使用Windows CryptoAPI；正例、内容/签名篡改及错误签名者拒绝均通过。
测试使用独立临时签名身份验证DPAPI初始化、签名、验签和禁止覆盖，不使用生产私钥
签署虚构的高版本清单。Qt/WGC与验签在同一进程导入验证，没有分进程规避DLL问题。
发布分支测试使用离线边界，覆盖首次/后续窄范围更新、版本回退、同版本冲突、
并发引用变化和Latest变化拒绝；未向远端写入测试数据。

## 真实加速下载

使用正式Qt流式下载及大小/摘要判断路径，完整读取已发布的
E7auto_v1.5.0_Setup_x64.exe。首轮工具会话中断且没有最终报告，日志和部分文件保留，
不计入通过；确认对应进程退出后，第二轮用隐藏的独立验证进程重测。

第二轮直接通过gh-proxy.org完成87,301,153字节下载，耗时537.344秒。SHA-256为
7baabe25e9fa82b00ef99708fec5b73cac0be72141b0c3740890519f0d3e7907，
与既有封存安装包完全一致，Windows文件版本为1.5.0.0。结果位于
results/download-probe.json；下载文件以.bin保留在本任务scratch中，没有作为新分发包。
不以这次耗时宣称比官方来源更快，也不保证其他网络可用性。

验证进程仅为下载旧版样本，替换了安装目录预检及当前版本比较边界，并拦截安装交接；
其余下载、换源、写盘、摘要和资源释放使用生产代码。没有运行安装器、注册安装、
管理员确认、游戏输入或截图，因此这不是编译版安装升级验收。首轮部分文件及
所有原始结果保留，没有重写v1.5.0历史构建证据。

## 变更文件

本轮28个仓库文件，完整摘要清单在任务results/changed-files.json：

- 运行代码：src/e7auto/app.py、core/update_feed.py、platform/update_signing.py，
  以及ui/main_window.py、ui/pages/application.py、ui/styles.py、ui/update_install.py、ui/updates.py。
- 发布及目录：scripts/project/layout.json、scripts/release/update-signing.ps1、
  scripts/release/update_feed.py、scripts/release/verify_release.py。
- 测试：tests/core/test_update_feed.py、tests/helpers/updates.py、tests/platform/test_update_signing.py、
  tests/scripts/release/test_update_feed.py，及tests/ui下的test_navigation.py、test_update_install.py、
  test_update_sources.py、test_updates.py。
- 文档：docs/README.md、design/ARCHITECTURE.md、development/DEVELOPMENT.md、INSTALLER.md、
  RELEASE_CHECKLIST.md、UPDATE_DELIVERY.md、user/使用说明.md及本记录。

## 交付边界

源码实现不代表已经部署：本轮没有创建或更新远端updates分支，没有新增CI、Git提交、
推送、构建或Release。首次联网签名清单验证及新安装包真实升级全链路留待后续发布
授权与验收。项目版本保持1.5.0；没有将本次改动写入已发布版本的历史CHANGELOG。
签名私有状态及任务产物不纳入Git，本地pip.ini保留。

最终28个文件中的18个Python文件通过AST检查；Git差异空白、工作区布局、源码
依赖及Markdown本地链接检查通过，暂存区为空。收尾Git远端读取遇到TLS握手失败，
随后通过官方GitHub API只读确认updates分支不存在；没有远端写入。

## 2026-11-16 后台自动下载实现与离线验证

新增默认关闭的“自动下载更新”，不显示常驻说明或悬浮提示。后台准备与明确授权的
安装交接分开；运行期间允许下载，准备及运行线程完全退出后才显示内容区提示卡。
完整包保留原始签名清单及唯一任务指针，跨重启重新验签和核验后提供离线安装入口。
本版本的“稍后”跨重启记忆，保留红点及设置安装入口。手动更新仍一次点击完成。

最终用例清单去重：新增33个用例（UI文件32个、平台清理边界1个），既有23个测试函数
的53个参数用例，共86个不同用例通过。多轮仅按具体函数/参数运行，未运行完整套件；
复跑限于新修改或此前失败所涉及的用例。新增UI文件仅首次集中执行，之后按函数选择。

新增覆盖：启动延迟、开关写入失败及其他偏好保留；后台准备不安装、运行期间可启动任务；
线程原生退出后才提示、键盘确认、小/正常窗口布局及无悬浮提示；缓存离线恢复、签名重验、
请求/摘要/大小/路径/PE/已安装版本/部分文件异常拒绝；稍后按版本记忆及关闭开关保留包；
手动接管、UAC失败复用、核验期间排队立即阻止新任务、关闭等待缓存线程；新目标切换、
已核验高版本不被旧来源覆盖、目标变化后重新确认、取消与迟到回调、限定任务清理。

既有实际选择范围：

- `tests/ui/test_updates.py`：`test_automatic_check_is_silent_and_manual_request_joins_it`、
  `test_failure_preserves_confirmed_result_and_only_manual_check_reports_it`、
  `test_fresh_cache_is_reused_then_checked_at_six_hours`、
  `test_backoff_survives_restart_caps_at_six_hours_and_resets_after_success`、
  `test_release_asset_is_delivered_with_notes_and_older_feed_preserves_reminder`。
- `tests/ui/test_update_sources.py`：`test_parallel_sources_choose_verified_version_and_emit_once`、
  `test_download_failover_restarts_bytes_and_keeps_the_same_asset`、
  `test_failover_is_bounded_and_cancel_never_installs`。
- `tests/ui/test_update_install.py`：`test_verified_native_reply_download_hands_off_exact_file_once`、
  `test_failed_or_cancelled_download_never_installs_and_discards_partial`、
  `test_cancelled_uac_keeps_verified_package_and_retry_does_not_download_again`、
  `test_changed_verified_package_is_not_reused_forever`、
  `test_dialog_download_progress_escape_cancellation_and_run_guard`、
  `test_ui_failed_download_keeps_notes_and_reenables_retry`、
  `test_successful_installer_launch_closes_application_only_after_handoff`、
  `test_real_loopback_http_stream_reaches_verified_handoff`、
  `test_window_close_waits_for_installer_thread_and_does_not_kill_it`、
  `test_update_ui_objects_are_released_with_window`、
  `test_update_progress_and_buttons_fit_small_and_normal_window`。
- `tests/platform/test_update_install.py`：`test_launch_verifies_file_and_passes_quoted_update_only_arguments`、
  `test_bad_package_or_cancellation_never_invokes_windows`、
  `test_success_cleanup_removes_only_matching_package_and_retains_records`、
  `test_installer_embedded_version_must_match_release_metadata`。

既有清单凭据和成功清理函数补充断言，其余既有用例直接复用。新增用例使用合成签名、
隔离安装身份与PE版本边界，并拦截安装交接；文件、下载、包核验和状态流程使用生产函数。
原验签算法及信任锚点未变化，不重跑此前未受影响的原生验签或Qt/WGC回归。

首轮新增用例有17个等待超时、3个通过。检查确认包核验线程已正常工作，但测试的Qt等待
没有充分释放Python执行时间；仅在新增测试的等待适配中增加让出执行时间，未放宽生产
核验或超时。定向复测通过。之后新加入的竞争/取消用例分别运行；没有将首轮超时计为通过。

源码AST、差异空白及工作区布局检查通过。布局检查沿用已批准的只读权限读取既有冻结归档，
没有改动归档或权限。未构建、运行真实安装器、创建测试安装、操作游戏、提交、推送或发布；
版本仍为1.5.6。安装版完整后台下载—重启恢复—确认升级—成功清理链路尚未实测，不用离线
结果替代。原窗口尺寸与悬浮窗中心改动、pip.ini及其他本地产物保留。

本次后台更新共18个文件，建议作为窗口尺寸修复、悬浮窗中心定位之后的独立逻辑提交；
共享BEHAVIOR.md按对应段落拆分，不包含此前任务的其他改动：

- 核心与平台：[update_feed.py](../../../src/e7auto/core/update_feed.py)、
  [update_install.py](../../../src/e7auto/platform/update_install.py)。
- 更新调度及下载：[updates.py](../../../src/e7auto/ui/updates.py)、
  [update_install.py](../../../src/e7auto/ui/update_install.py)、
  [auto_updates.py](../../../src/e7auto/ui/auto_updates.py)、
  [preferences.py](../../../src/e7auto/ui/preferences.py)。
- 界面接入：[main_window.py](../../../src/e7auto/ui/main_window.py)、
  [application.py](../../../src/e7auto/ui/pages/application.py)、
  [styles.py](../../../src/e7auto/ui/styles.py)。
- 测试：[test_auto_updates.py](../../../tests/ui/test_auto_updates.py)、
  [test_updates.py](../../../tests/ui/test_updates.py)、
  [test_update_install.py](../../../tests/platform/test_update_install.py)。
- 说明：[ARCHITECTURE.md](../../design/ARCHITECTURE.md)、[BEHAVIOR.md](../../design/BEHAVIOR.md)、
  [INSTALLER.md](../../development/INSTALLER.md)、[UPDATE_DELIVERY.md](../../development/UPDATE_DELIVERY.md)、
  [使用说明.md](../../user/使用说明.md)，及本验证记录。
