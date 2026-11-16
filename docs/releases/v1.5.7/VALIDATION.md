# v1.5.7 工作区审查与版本验证

日期：2026-11-16。

## 审查结论与范围

基于`d89d828f73324b57fa12e1105ee6263cf76f81db`审查当前全部未提交差异及新增源码、测试和文档，未发现新增阻断问题。三项行为变更分别为统一窗口尺寸准备、悬浮窗中心定位、后台自动下载更新。识别阶段的基准/非基准搜索、评分、模板、ROI和点击策略未变，没有新增运行依赖。未跟踪的本地`pip.ini`仅配置pip缓存和版本检查，保留在提交范围外；既有本地产物保持。

窗口准备以同一物理像素上下文实测完整外框和客户区，按边距设置目标外框；首次精确达标立即继续，否则在共享期限内稳定观察并有限校正。参考客户区仍为2322×1306，非参考模式保留既有目标和高度适配。无效测量及停止、身份、最小化、显示变化或超时均在业务截图/输入开始前停止。

悬浮球与展开面板共用Qt逻辑屏幕中心，负坐标切换不累计偏移；旧版未标记位置仍按左上角读取，后续保存迁移为中心。自动下载默认关闭，后台传输/核验不占用脚本运行守卫，安装仍要求明确操作；手动安装请求及排队期间立即阻止新任务，关闭窗口等待原生核验/安装线程退出。缓存恢复重新验签及核验安装身份、路径、大小、摘要和PE版本，提示按版本记忆“稍后”。

本轮同步架构、行为、尺寸适配、安装和更新分发说明、验收清单及随包说明；版本从1.5.6更新为1.5.7，同步项目/源码版本、README、CHANGELOG、说明标题和安装包名。仅以无索引、无依赖的editable安装刷新项目`.venv`元数据，未安装或升级其他依赖。首次沙箱内替换旧`.pth`被权限限制，随后获准刷新同一本地项目元数据并成功，没有修改文件权限。

## 复用的直接相关验证

复用本对话中此前已通过且源码未再变化的定向结果，不因审查、版本同步或构建重复运行：

| 变更 | 新增/既有范围 | 结果与边界 |
| --- | --- | --- |
| 窗口准备 | 新增42个参数用例；既有13个用例。涉及`test_window_preparation.py`、`test_platform_windows.py`、`test_resolution_adaptation.py`及原窗口守卫 | 55个不同用例通过。覆盖基准/非基准首次到位、实测非对称边距、21×12差值、DPI信息差异、负坐标、适高边界、延迟达标、重新测量、三次设置上限、统一超时及停止/环境变化；失败不开始截图或业务输入。原生DPI上下文进入和恢复探针通过 |
| 悬浮窗 | 扩展5个既有测试函数，共9个参数用例 | 负坐标反复收起/展开、拖动、中心持久化、旧数据读取/保存迁移、不同面板重启定位及屏外回退通过；未作原生桌面视觉验收 |
| 后台更新 | 新增33个用例；23个既有函数的53个参数用例 | 86个不同用例通过。完整函数范围、首轮等待失败及定向复测见[更新分发验证](../../validation/common/UPDATE_DELIVERY_VALIDATION.md#2026-11-16-后台自动下载实现与离线验证)；合成签名及隔离安装身份、PE和安装交接边界不代替真实安装升级 |

三组共150个不同用例。悬浮窗既有函数为`test_stats_overlay_collapses_to_draggable_logo_and_click_restores`、`test_each_overlay_configuration_restores_the_full_panel`、`test_overlay_position_store_round_trips_and_rejects_invalid_data`、`test_overlay_uses_saved_position_then_falls_back_for_offscreen_state`及`test_overlay_is_always_draggable_and_saves_position_after_move`，均位于`tests/ui/test_overlay.py`。窗口与悬浮窗原工作区布局/差异检查结果也已通过；本轮新增版本文档后另做最终检查。

## 本轮实际执行的版本检查

仅执行`tests/scripts/release/test_version_sync.py`中的3个既有用例，全部通过，没有新增测试或运行完整套件：

- `test_active_source_document_and_environment_versions_are_synchronized`：项目、源码、安装元数据及当前文档版本一致。
- `test_source_self_check_reports_the_project_version`：源码自检输出1.5.7，并保留源码模式跳过安装身份核验的规则。
- `test_environment_verifier_requires_exact_versions`：项目版本和锁定依赖精确一致。

`scripts.release.workflow preflight`返回1.5.7；构建入口的环境检查确认锁定依赖无缺失或版本差异，未启用用户级site-packages。

## 复用的本机实测

核对用户此前运行日志`logs/run-20261116-160945-848361-98ec40a56fdc.log`：3120×2080桌面、DPI192，实测边距13/58/13/13，首次外框请求2348×1377，回读客户区2322×1306且宽高差值均为0。`reference_path=True`、`scale_x=scale_y=1`，931次截图全部成功，最终以停止按钮触发的`manual_stop`结束。

该记录证明本机基准模式首次调整到位；没有在本轮重新运行游戏，不能据此确认客户4K/225%环境的偏差来源或校正效果。

## 构建与完整安装验收

构建ID为`20261116-190153-installer-07ddff9b`，使用登记的Nuitka 4.1.3、GCC 15.2.0和Inno Setup 6.7.3。构建入口完成版本/环境检查、输入捕获、编译、原生运行库归一化、payload冻结和安装EXE封存；构建中核对输入未变化，后续仅补充未打包的版本记录和索引。

发布工具以`lifecycle_scope_changed`判定完整模式，变化为版本及受保护输入，未复用旧安装生命周期证据。在Windows 11 10.0.22631 AMD64、本次构建专属中文空格路径实际执行14项检查，全部通过：自定义路径、默认不选桌面图标、开始菜单入口、实际安装内容及编译版自检、运行中安装/卸载拒绝、覆盖保留数据、停用文件清理、桌面图标选择、卸载删除数据与登记、保留无关用户文件、空根目录删除和环境变量不变。

实际安装及覆盖后的程序逐文件核对配置、模板、UI资源、原生库和拥有文件清单，报告无问题；编译版版本为1.5.7，`compiled`、`wgc_importable`、`update_installation_valid`、`update_signature_ready`及`statistics_runtime_ready`均为true，相关错误字段为空。这是实际安装副本中的生产更新身份和固定样本验签自检，不是完整应用内升级链路。

验收前确认本机没有既有E7auto安装登记、通知身份或同名快捷方式；通过隐藏管理员助手执行验收，退出码为0。测试副本完整卸载后，工具将`E7auto_v1.5.7_Setup_x64.exe`提升至dist，仅删除其直属旧版标准包`E7auto_v1.5.6_Setup_x64.exe`。本次nuitka和staging已按工具边界清理，历史证据、缓存及其他文件保持。

最终安装包为87,712,852字节，实际SHA-256与封存记录一致；再次验证安装生命周期报告绑定有效。完整本地证据位于`artifacts/releases/v1.5.7/20261116-190153-installer-07ddff9b/`，包含构建输入/产物、payload、安装/覆盖自检、14项验收、dist提升结果、管理员助手退出码及原始日志。实际摘要保存在构建产物和验收记录中。

## 未验证及Git边界

最终工作区布局、源码依赖边界和Markdown本地链接检查通过，`mode=worktree`、`problems=[]`；差异空白检查通过。布局检查使用获准的只读权限读取既有冻结归档，没有改动归档、ACL或目录豁免。

未运行完整测试套件、无关功能回归、实机游戏自动化或新截图；未覆盖客户4K/225%故障机、原生桌面悬浮窗视觉验收、干净虚拟机和其他Windows版本。自动下载后的真实安装版“后台下载—跨进程恢复—确认升级—成功重启清理”全链路未实测；离线边界、编译版自检和安装器生命周期验收分别报告，不互相替代。未使用生产私钥签署虚构高版本清单或向远端写入测试数据。

未暂存、提交、推送、创建标签、上传Release或更新远端清单。最终`workflow check --revision HEAD`必须在获准提交且提交包含相同构建输入后执行，本轮不以当前旧HEAD冒充新包对应提交。

## 建议提交拆分及文件范围

按下面顺序形成四个可独立审查/回退的提交；本轮只给候选，不暂存、提交、推送或发布。共享文档按对应段落拆分，随包说明的行为段落跟随各自功能，版本标题/包名归最后一批。全部范围共36个仓库文件，不含`pip.ini`及忽略的构建证据。

1. `fix(window): 统一基准与非基准窗口尺寸准备`

   正文候选：根据实测客户区与完整外框边距计算目标窗口，在统一期限内回读并有限校正，处理首次调整未达到目标尺寸的情况。保留参考目标、显示环境守卫及识别阶段的原有规则。

   文件：`src/e7auto/core/ports.py`、`src/e7auto/platform/windows.py`、`src/e7auto/runtime/windows.py`；`tests/helpers/services.py`、`tests/platform/test_platform_windows.py`、`tests/platform/test_resolution_adaptation.py`、`tests/platform/test_window_preparation.py`；`docs/design/NATIVE_MATCHING.md`及`docs/design/ARCHITECTURE.md`、`docs/design/BEHAVIOR.md`、`docs/development/RELEASE_CHECKLIST.md`中的窗口段落。

2. `fix(overlay): 围绕中心收起并保存悬浮窗位置`

   正文候选：收起和展开共用屏幕中心，并按中心保存拖动位置，使悬浮球、不同功能面板和重启后的定位一致。兼容旧版左上角记录，在后续保存时迁移为带中心标记的数据。

   文件：`src/e7auto/ui/overlay.py`、`src/e7auto/ui/position.py`、`tests/ui/test_overlay.py`；`docs/design/ARCHITECTURE.md`、`docs/design/BEHAVIOR.md`、`docs/development/RELEASE_CHECKLIST.md`、`docs/user/使用说明.md`中的悬浮窗段落。

3. `feat(update): 支持后台下载与恢复待安装更新`

   正文候选：增加默认关闭的后台自动下载和空闲安装提示，保留签名清单与安装包以便重启后重新核验和离线安装。安装始终由明确操作触发，手动更新可接管同目标下载，“稍后”按版本记忆并保留设置入口。

   文件：`src/e7auto/core/update_feed.py`、`src/e7auto/platform/update_install.py`；`src/e7auto/ui/auto_updates.py`、`src/e7auto/ui/updates.py`、`src/e7auto/ui/update_install.py`、`src/e7auto/ui/preferences.py`、`src/e7auto/ui/main_window.py`、`src/e7auto/ui/pages/application.py`、`src/e7auto/ui/styles.py`；`tests/ui/test_auto_updates.py`、`tests/ui/test_updates.py`、`tests/platform/test_update_install.py`；`docs/development/INSTALLER.md`、`docs/development/UPDATE_DELIVERY.md`、`docs/validation/common/UPDATE_DELIVERY_VALIDATION.md`及架构、行为、发布清单、使用说明中的更新段落。

4. `chore(release): 同步1.5.7版本与说明`

   正文候选：将项目及源码版本统一为1.5.7，同步README、更新日志、随包说明和文档索引，使当前版本标识与本批行为说明一致。

   文件：`pyproject.toml`、`src/e7auto/__init__.py`、`README.md`、`CHANGELOG.md`、`docs/README.md`、`docs/user/使用说明.md`中的版本标题/安装包名及本记录。
