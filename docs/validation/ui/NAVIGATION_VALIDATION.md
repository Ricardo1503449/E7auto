# 主窗口导航与设置页验证

2026-09-27：保持白色主题，加入默认收起的侧边栏和浅灰内容区。
启动时选中功能中心，保留四张功能卡片；侧边栏底部为关于和设置。
标题栏左侧统一返回箭头，点击后返回功能中心，保留侧边栏当前收展状态和原功能参数。
Logo 与应用名称右移，在名称右侧显示源码版本；设置页读取同一版本来源。

## 本次修改文件

| 文件 | 修改 |
| --- | --- |
| `src/e7auto/ui/main_window.py` | 统一外壳、选中状态、页面切换、运行中导航守卫及配色 |
| `src/e7auto/ui/window_chrome.py` | 返回图标、Logo 位置与版本号 |
| `src/e7auto/ui/navigation.py` | 三横线、导航图标、展开与收起 |
| `src/e7auto/ui/updates.py` | 手动异步查询最新正式版、版本比较、超时和取消 |
| `src/e7auto/ui/pages/application.py` | 关于、四项设置卡片、打开日志目录及项目/发布页面 |
| `src/e7auto/ui/pages/function_center.py` | 配合侧边栏的内容边距 |
| `src/e7auto/ui/pages/shop.py` | 移除页内返回文字，统一边距 |
| `src/e7auto/ui/pages/penguin.py` | 移除页内返回文字，统一边距 |
| `src/e7auto/ui/pages/chaos.py` | 移除页内返回文字，统一边距，窄窗口滚动 |
| `tests/ui/test_navigation.py` | 导航、版本位置、最小窗口、日志及设置操作 |
| `tests/ui/test_updates.py` | 正式版解析、异步结果、失败、重试、超时和取消 |
| `tests/ui/test_main_window.py` | 更新统一返回按钮调用 |
| `tests/ui/test_penguin.py` | 更新统一返回及停止后悬浮窗检查 |
| `docs/user/使用说明.txt` | 同步导航和设置入口使用方式 |

本清单只描述本次修改；工作区其他既有修改和未跟踪文件未清理、未提交。

## 验证结果

Qt offscreen 下运行直接相关用例，50 项通过，6 项按范围排除：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.venv/Scripts/python.exe -m pytest tests/ui/test_navigation.py tests/ui/test_updates.py tests/ui/test_main_window.py tests/ui/test_window_lifecycle.py tests/ui/test_import_boundaries.py tests/ui/test_penguin.py tests/ui/test_chaos.py -q -k 'not worker and not penguin_overlay and not windows11_stage_popup and not repeated_mouse'
```

覆盖默认导航、键盘收展、各页面返回、运行中禁止离开、设置值保留、760×560
窗口收展两种宽度下控件可达且没有横向滚动、停止后悬浮窗隐藏、版本号来源和位置、
包含空格及中文的日志目录、目录创建/打开失败、更新结果和重试、窗口析构与 WGC 延迟导入。
原有窗口生命周期压力检查共覆盖两种清理方式，每种 30 次。

更新状态测试使用模拟网络响应；另用生产 UpdateChecker 完成真实 GitHub 查询，
得到最新正式版 v1.3.4，update_available=False。默认沙箱首次请求被权限策略拒绝，
只读网络请求获准后成功；未下载或安装发布附件。

目录及源码边界检查 `python -m scripts.project.check_layout` 通过；相关 Git diff
空白检查通过。未跑全套测试，未截图，未执行游戏自动化，未构建发行包或提交/推送。

## 2026-09-27 界面细节调整

本轮修改 `main_window.py`、`window_chrome.py`、`navigation.py`、
`pages/application.py`、`tests/ui/test_navigation.py`、使用说明及本记录。

- 标题栏启用自定义 QWidget 背景绘制，实际显示与侧边栏一致的白色；标题旁版本字号从13px增至14px。
- 返回和三横线按钮不显示选中边框；三横线改为普通操作按钮。导航条目使用蓝色竖条、浅蓝背景和中性色图标，不显示焦点边框。
- 返回功能中心保留展开/收起状态；仅首次启动默认收起。
- 关于和设置标题统一为卡片内24px粗体，关于页的应用名称调整为20px。
- 三个可点击设置条目只保留名称和右侧箭头，版本行保留实际版本值。更新进度、成功和失败信息显示在卡片下方。

直接相关回归9项通过：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.venv/Scripts/python.exe -m pytest tests/ui/test_navigation.py tests/ui/test_main_window.py::test_main_window_has_approved_shop_controls_and_resizable_shell tests/ui/test_main_window.py::test_main_window_switches_between_module_and_shop_pages -q
```

覆盖返回后两种收展状态、卡片内标题及字号、条目文字简化、更新结果提示、原有窗口控制和最小窗口布局。
此前更新查询实现及网络检查结果沿用，不重复请求 GitHub 或执行无关回归。

## 2026-09-27 更新结果弹窗与加载动画

在上述界面调整基础上，继续修改 `pages/application.py`、`main_window.py`、
`tests/ui/test_navigation.py`、使用说明及本记录。更新结果不再显示于卡片下方，
改用独立的白色窗口级模态弹窗：无更新显示当前与最新版本，新版本提供“前往发布页”，
失败提供“重试”。“关闭”或 Esc 返回设置页；弹窗不自动下载或安装。

检查过程中禁用该条目、文字置灰，箭头替换为40ms刷新一次的旋转弧线。
成功、失败、取消及主窗口关闭均停止动画；关闭窗口也取消待完成请求并关闭结果弹窗。
日志目录或浏览器打开失败仍使用设置页的状态提示。

最终直接相关回归18项通过，包含本轮新增弹窗及动画的生命周期检查：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.venv/Scripts/python.exe -m pytest tests/ui/test_navigation.py tests/ui/test_main_window.py::test_main_window_has_approved_shop_controls_and_resizable_shell tests/ui/test_main_window.py::test_main_window_switches_between_module_and_shop_pages tests/ui/test_window_lifecycle.py -q
```

目录/源码边界检查和相关 Git diff 空白检查通过。更新查询协议与版本比较未变，
沿用之前的网络验证；本轮只通过模拟信号验证弹窗结果和按钮动作，不再次联网。

## 最终调整：改动日志与三个条目的加载反馈

用户进一步确认后，新版本弹窗移除“前往发布页”，最终只显示新版本号、GitHub
Release body 中的改动日志及关闭按钮。日志按 Markdown 展示、长内容可滚动；
空日志明确提示缺失，正文不打开链接、不加载链接中的文件或图片。
无更新与失败弹窗保留版本说明、关闭以及失败重试。

日志目录、GitHub 项目地址和检查更新三个条目均使用同一加载状态：点击即禁用并
旋转右侧加载环，先留出绘制时间再处理操作，拒绝重复点击。文件夹/浏览器打开请求
返回后恢复原状，失败也恢复并提示；更新请求在结果返回后恢复。主窗口关闭会取消
尚未执行的条目操作、停止动画并取消待完成的更新请求。

本轮继续修改 `pages/application.py`、`updates.py`、`main_window.py`、
`tests/ui/test_navigation.py`、`tests/ui/test_updates.py`、使用说明及本记录。
最终覆盖34项相关用例通过：首次发现加载环在请求开始时重置，修正忙碌状态的
重复设置后，仅重跑受影响的14项导航用例，全部通过；其余20项已通过结果沿用。

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.venv/Scripts/python.exe -m pytest tests/ui/test_navigation.py tests/ui/test_updates.py tests/ui/test_window_lifecycle.py::test_chrome_does_not_keep_window_alive -q
# 修正动画重置后，仅重跑受影响的导航/设置测试：
.venv/Scripts/python.exe -m pytest tests/ui/test_navigation.py -q
```

日志读取已通过真实生产接口验证：最新正式版v1.3.4，update_available=False，
Release body为233字符。没有伪造新版本；新版本展示、空日志、长日志滚动及错误类型
通过离线响应测试。目录/源码边界检查和相关空白检查通过，未运行全套测试或提交代码。

## 2026-09-27 六项UI优化

保持现有白色界面、蓝色导航、卡片内标题、无悬浮提示、更新弹窗及暗色遮罩。

| 项目 | 实现与边界 |
| --- | --- |
| 日志异常兜底 | AutomationWorker将日志创建纳入异常保护；诊断写入及关闭失败也会发出且仅发出一次终止结果。已经发布的最终统计保留，不因日志失败重置计数。 |
| 线程收尾 | 结果信号只更新显示；QThread.finished后非阻塞wait(0)确认原生线程退出，才清空引用并恢复按钮、导航。 |
| 后台准备与打开操作 | RunRequest冻结本次输入；PreparationThread后台执行原有配置/资源验证。成功且未取消才由GUI启动原自动化工作线程。准备取消后不创建游戏会话；拒绝启动原因显示在功能页，配置拒绝日志尽力保留。目录创建和系统打开在独立线程执行，Windows默认处理器调用前后在同线程初始化/释放COM STA。关闭时取消未执行操作、等待在途调用返回，不强杀线程。 |
| 高清图片缓存 | 缓存按逻辑区域乘DPR生成，裁剪同步换算物理像素，缓存键包含尺寸及DPR。原PNG、等比例填充和居中构图保持不变。 |
| 样式集中 | 主窗口样式移入styles.py；共用颜色、字号、页面边距、表单间距及输入框尺寸集中定义。 |
| 公共功能页 | 三个页面共用展示层FeaturePage，统一滚动区、卡片、标题、表单行、整数校验和启动按钮；各功能业务实现保持独立。 |

已清理旧pageHeading样式、主窗口内嵌样式方法、商店专用跳转/校验包装、重复控件别名、
旧同步打开路径、旧逻辑尺寸缓存字段、重复页面布局及不再使用的功能专用限额读取包装。
新的通用整数校验器保留原ASCII整数、上限及各功能最小值约束。

本批修改文件：

- `src/e7auto/ui/main_window.py`、`worker.py`、`widgets.py`、`navigation.py`。
- `src/e7auto/ui/preparation.py`、`desktop_actions.py`、`styles.py`（新增）。
- `src/e7auto/ui/pages/feature.py`（新增）、`shop.py`、`penguin.py`、`chaos.py`、`application.py`、`function_center.py`。
- `src/e7auto/platform/desktop.py`（新增）。
- `tests/ui/test_optimization.py`、`tests/platform/test_desktop.py`（新增），以及`tests/ui/test_main_window.py`、`test_navigation.py`、`test_worker.py`、`test_chaos.py`、`test_penguin.py`、`tests/helpers/qt.py`。
- `docs/design/ARCHITECTURE.md`、`docs/development/DEVELOPMENT.md`、`docs/user/使用说明.txt`及本记录。

### 验证范围和结果

最终83项不同的直接相关用例通过，未运行全套测试。首轮集中验证为81通过、1项旧固定延时
断言失败；改为等待真实完成条件后，该项与新增的配置拒绝日志失败用例分别通过。
清理限额读取包装后，仅重跑受影响的两个功能页用例，均通过，其余结果复用。

覆盖日志创建/写入/关闭失败、最终统计保留、准备失败反馈、冻结参数、重复启动拒绝、
准备取消、线程延迟退出、后台打开期间GUI计时器持续响应、关闭时等待后台回收、
COM初始化/释放平衡、100%/125%/150%/200%缓存尺寸/裁剪/复用、现有导航和窗口生命周期。
原生窗口清理压力用例仍覆盖两种清理方式各30轮；WGC保持延迟导入。

实际资源准备另行只读验证4条路径：商店、企鹅、卡厄思暗双子队、卡厄思平民替换队；
均返回有效配置且无错误。没有调用生产游戏会话、抓取画面或发送游戏输入。
测试中的系统打开接口均被替换，不真实启动浏览器或文件夹；此次未重新构建发行包。

原始测试报告、资源验证及本批文件清单位于
本地任务结果（本地证据：`artifacts/tasks/ui/20260927-184717-ui-six-optimizations-e4514ab3/results`）。
其中`related-tests.xml`保留首轮结果，`final-targeted-tests.xml`与`cleanup-tests.xml`记录最终相关复验，
`resource-preparation.json`记录真实资源准备结果。目录/源码边界和修改文件空白检查通过。

实现边界参考[Qt线程结束与清理](https://doc.qt.io/qt-6/qthread.html#finished)、
[Qt高分辨率绘制](https://doc.qt.io/qt-6/qpainter.html#drawing-high-resolution-versions-of-pixmaps-and-images)
及[Windows ShellExecute的COM要求](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shellexecutew#remarks)。

## 2026-09-28 任务栏最小化

主窗口原先仅设置Window和FramelessWindowHint，原生样式为0x86000000，缺少
WS_MINIMIZEBOX、WS_MAXIMIZEBOX和WS_SYSMENU。补充WindowMinMaxButtonsHint后，
样式为0x860b0000，三个系统能力标志均具备，WS_CAPTION仍未设置，保留自定义标题栏。
对应映射见[Qt Windows窗口实现](https://github.com/qt/qtbase/blob/6.11/src/plugins/platforms/windows/qwindowswindow.cpp)。

修改main_window.py、test_main_window.py及本记录。使用原生Windows Qt后端运行3项检查通过：
普通状态、最大化状态分别接收WM_SYSCOMMAND的最小化/还原命令，Windows和Qt状态一致，
还原后保留此前的最大化状态；现有自定义标题栏最大化/还原、尺寸和外壳检查通过。
系统菜单的最小化、最大化、关闭项均处于启用状态。
本次没有操纵Explorer任务栏进行鼠标点击测试，也没有截图或启动游戏自动化。

```powershell
$env:QT_QPA_PLATFORM = 'windows'
.venv/Scripts/python.exe -m pytest tests/ui/test_main_window.py::test_windows_native_minimize_restore_with_frameless_chrome tests/ui/test_main_window.py::test_main_window_has_approved_shop_controls_and_resizable_shell -q
```

## 2026-09-28 主窗口原生过渡与圆角

上一项补回了任务栏最小化能力，但主窗口仍是WS_EX_LAYERED透明分层窗口，缺少原生
标题栏/调整边框样式，DWM非客户区绘制未启用。本次仅对原生Windows主窗口改用不透明
表面，由platform/window_frame.py保留WS_CAPTION、WS_THICKFRAME、系统菜单和最小化/
最大化能力；WM_NCCALCSIZE隐藏系统标题栏并限制最大化客户区到目标显示器工作区。
WM_NCHITTEST按当前DPI提供边缘缩放，标题栏按钮区域仍是客户区，由现有Qt控件处理。

Qt层仍保留FramelessWindowHint用于客户区尺寸计算，避免直接切换Qt标准框架时引入的
尺寸偏移。Windows 11圆角交给DWM，相关Qt外层圆角/边框不重复绘制；DWM过渡不被本窗口
强制禁用，不修改系统全局动画开关。原生设置仅对首次/新HWND应用，最小化还原时不会
反复重设框架。悬浮窗和更新弹窗的透明方式保持原样。Windows 10不支持的圆角偏好会由
系统忽略，实际观感验证范围为当前Windows 11 build 22631。

本批修改文件：

- `src/e7auto/platform/window_frame.py`（新增原生适配器）。
- `src/e7auto/ui/main_window.py`、`src/e7auto/ui/styles.py`（接入及原生模式外观）。
- `tests/platform/test_window_frame.py`、`tests/ui/test_native_frame.py`（新增），以及`tests/ui/test_main_window.py`。
- `docs/design/ARCHITECTURE.md`及本记录。

原生Windows后端51项直接相关检查通过：普通/最大化状态的最小化及还原、DWM非客户区
绘制启用、Windows 11圆角偏好、非分层窗口样式、客户区与工作区相等、连续最大化/还原
尺寸稳定、标题栏按钮与边缘命中、100%/150%/200%缩放算法、负坐标显示器边界、现有
布局、更新弹窗和窗口生命周期。没有运行全套测试或游戏自动化，也没有截图/录屏。

本机原生检查记录：主窗口样式为0x96cf0000（含可见标志），扩展样式0x100，无
WS_EX_LAYERED；DWM非客户区绘制为true。1180×760逻辑尺寸显示和还原后保持一致；
最大化客户区为[0,0,3120,1984]，与显示器工作区完全一致。真实任务栏点击及动画观感
未通过屏幕采集验证，需要用户重启加载新窗口后观察，不能以状态检查代替视觉验收。

本批原生检查结果（本地证据：`artifacts/tasks/ui/20260928-135241-native-window-animation-b65b8d5d/results`）
包含native-frame.json和native-tests.xml。目录/源码边界及修改文件空白检查通过。
实现参考[微软自定义窗口框架](https://learn.microsoft.com/en-us/windows/win32/dwm/customframe)
和[Windows 11圆角条件](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/ui/apply-rounded-corners)。

## 2026-09-28 运行结束通知

按用户最终确认采用带Windows默认提示音的系统通知。设置卡片在当前版本号前增加
“运行结束通知”，右侧复用功能页的_ToggleSwitch，默认开启，选择原子保存到
state/ui_preferences.json；保存失败回退开关并显示错误，损坏/缺失设置使用默认值。
正常完成、资源不足/刷新策略结束和异常停止分别说明原因并显示简短统计；手动停止
和取消准备不通知，未取消的启动失败通知一次。不添加结果卡片、悬浮提示或点击通知
后的窗口激活动作，保留原有导航、线程收尾、错误信息及悬浮统计行为。

主窗口仅在QThread.finished且非阻塞wait(0)成功后发送，按run_id去重。
Windows适配器使用已有pywin32的Shell_NotifyIcon，未设置NIIF_NOSOUND，不更改
全局音量/勿扰配置。通知所需临时图标在关闭、超时、替换、禁用、退出时释放，
120秒定时器处理未收到系统回调的情况；旧通知的延迟回调不会删除新通知。
系统接口不可用只记录发送失败，不阻止脚本结束或按钮恢复。

本批修改：ui/main_window.py、ui/pages/application.py；新增ui/notifications.py、
ui/preferences.py、platform/notifications.py，以及tests/ui/test_notifications.py、
tests/platform/test_notifications.py；同步架构、开发指南、使用说明及本记录。

71项不同的直接相关检查最终全部通过，涵盖所有停止原因、手动停止抑制、禁用及去重、
线程退出时序、设置保存/损坏/写入失败、键盘切换、最小窗口布局、原生图标资源回收、
UTF-16边界、通知声音标志和窗口生命周期。首次检查有3项回调测试因MSG字段名失败，
修正为hWnd后10项相关复查通过；其他已通过检查沿用。未运行全套测试或游戏自动化。

真实Windows桌面验证收到NIN_BALLOONSHOW(1026)和NIN_BALLOONTIMEOUT(1028)，
Shell通知添加、版本设置、显示及删除请求全部成功，图标已释放，未加载WGC。
声音标志为0x81（信息通知并尊重系统安静策略，不含静音标志）；未做人工听音验证，
实际提示音仍受系统通知/音量/勿扰设置控制。隔离环境没有Shell_TrayWnd，原生验证
因此在获准的真实桌面执行一次明确标注的测试通知。没有截图或录屏。

本批验证结果（本地证据：`artifacts/tasks/ui/20260928-203039-run-end-notifications-d18807ab/results`）
包含related-tests.xml、final-targeted-tests.xml、native-notification.json和验证摘要。
目录/源码边界及本批文件空白检查通过。接口行为参考
[微软Shell通知文档](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw)
及[通知声音标志](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-notifyicondataa)。

## 2026-09-28 Toast通知中心留存修复

用户实测确认旧Shell_NotifyIcon横幅在Windows 11消失后不进入通知中心。
本次改为Windows.UI.Notifications.ToastNotificationManager发送ToastGeneric；
沿用原通知条件、设置开关、默认提示音和手动停止不通知规则，不增加结果卡片。
关闭窗口或提醒开关不删除已发送记录，历史管理交给Windows和用户。

修改platform/notifications.py、ui/notifications.py和ui/main_window.py，新增
platform/toast_runtime.py，更新两个notifications测试文件及架构/开发/使用文档。
删除旧托盘图标、回调消息处理、HWND参数、120秒清理定时器及旧接口专用测试。
新适配器仅登记HKCU/Software/Classes/AppUserModelId/E7auto.E7auto的显示名称、
PNG图标及CustomActivator；COM回调为空操作，只在当前进程存在，不注册LocalServer32
或启动项。WinRT通过系统ABI访问，HSTRING/COM接口按作用域释放，不加载PyWinRT，
不影响既有Qt/WGC延迟导入规则，不增加安装依赖。

68项直接相关检查通过：通知分类/开关/去重/线程退出时序、关闭后保留记录、XML转义
及非法字符过滤、中文和非BMP字符、每用户身份登记范围、登记失败可重试、COM回调契约、
真实XML ABI、COM初始化平衡、HSTRING异常清理、导入边界及窗口生命周期。
最初真实XML测试主动传入畸形XML，Windows错误发起机制产生诊断输出但测试正常通过；
移除该无必要的畸形原生输入后，单独复查有效XML ABI。错误资源清理继续由可控单元测试覆盖。

真实桌面已发送标明“通知中心留存测试”的有声Toast，发送调用约116ms，随后运行事件
循环18秒并退出。独立进程通过GetHistory('E7auto.E7auto')在22:04:27读取到1条匹配
history-probe-f6f7abf3的记录，证明其在短横幅展示期及发送进程退出后仍保留。
首次读历史因自动审批服务连接中断被拒；核对只读范围后原命令重试成功，没有扩大读取
范围或清除历史。测试未启动游戏、未截图/录屏，未重新构建发行包，也未做人工听音验证。

本批验证结果（本地证据：`artifacts/tasks/ui/20260928-215326-toast-notification-history-f6f7abf3/results`）
保存related-tests.xml、有效XML复查、native-toast-send.json、native-toast-history.json
和验证摘要。依据[微软桌面Toast说明](https://learn.microsoft.com/en-us/windows/apps/design/shell/tiles-and-notifications/send-local-toast-desktop-cpp-wrl)
及[Windows SDK接口元数据](https://github.com/microsoft/windows-rs/tree/master/crates/libs/windows/src/Windows/UI/Notifications)。

## 2026-10-03 新版本红点提醒

按用户确认采用首次显示主窗口1秒后的静默检查，成功结果缓存6小时，持续运行每6小时
复查。设置齿轮和“检查更新”文字右上角显示同色7逻辑像素红点，展开/收起、窗口缩放、
忙碌和禁用状态均保留正确位置。查看设置或改动日志不清除；确认没有更高正式版或升级
到已知版本后清除。没有悬浮提示，保留键盘操作、加载圆环及无障碍描述。

主窗口持有UpdateController，复用现有异步UpdateChecker并原子缓存至
state/update_status.json。自动结果不弹窗，手动请求才展示现有结果/重试弹窗；用户点击
立即加入正在进行的查询，取消条目80毫秒延迟动作，防止后台提前完成后重复联网。
超时仍为10秒，失败保留最后成功结果；连续失败按30分钟、1/2/4/6小时退避并跨重启
保留。明确限流遵循Retry-After及额度耗尽时的X-RateLimit-Reset，手动检查明确说明
等待时间。缓存损坏、缺失和写入失败不阻止结果展示；关闭取消请求/定时器并忽略迟到结果。

本批修改5个UI源码文件：updates、navigation、pages/application、main_window、styles；
更新UI公共fixture及两个直接相关测试文件，同步架构、开发指南、使用说明和本记录。
没有修改版本、识别、游戏流程、正式模板、安装器或依赖，没有提交、推送或重新构建。

82项不同的直接相关检查最终全部通过，包括数字版本边界、正式版过滤、两处状态同步、
查看后保留/升级后清除、缓存恢复/损坏/写入失败、退避重启/封顶/恢复、限流响应解析
及完整传递、真实1秒启动定时器、显示恢复不重置、手动/自动请求合并和80毫秒竞态、
关闭/超时资源回收、窗口生命周期，以及100%/150%/200%绘制缩放。
红点位置通过控件在内存中的QImage绘制像素核对，没有屏幕截图或录屏。
更新请求使用模拟传输与受控时钟，没有使用真实GitHub访问作为验收结果，也没有运行全套测试。

首轮70项通过、1项因真实时钟前进1毫秒导致精确间隔断言失败；改为受控时钟后专项
复查通过。后续12项、7项、6项分别覆盖修正/新增边界、点击竞态及限流提示，已通过且
实现未变的结果复用；原生生命周期包括仅close及显式销毁各30轮。
目录与源码边界检查通过，本批新增行的空白检查及相关Git diff检查通过。

本批验证结果（本地证据：`artifacts/tasks/ui/20261003-183238-update-indicators-6f52b8a1/results`）
保留四份JUnit报告、去重测试摘要、相对本任务开始状态的changes.txt和文件摘要；
先前工作区的其他修改与未跟踪文件均保留。

## 2026-10-18 更新检查限流响应修复

当前PySide6 6.11.2的QNetworkReply.rawHeader要求字符串参数，更新检查原先以bytes读取Retry-After、X-RateLimit-Remaining及X-RateLimit-Reset。真实Qt响应对象在403／429分支抛出TypeError，检查器虽释放请求，但没有发出失败信号，导致控制器保留checking状态且不再安排自动检查。本次仅将三处响应头名称改为字符串，保留既有限流判断、缓存、退避和手动提示规则。

测试传输仍在本地替代，但FakeReply改为继承QNetworkReply，使用Qt原生响应头存储和读取接口，不再自定义rawHeader。四个手动／后台与403／429组合在修改前均复现异常；修改后23项直接相关用例全部通过（1.67秒）。覆盖Retry-After秒数／HTTP日期、额度耗尽重置时间、缺失／非法响应头及普通403、失败信号和检查状态释放、自动定时器与缓存、等待期间阻止手动联网、等待结束后恢复成功、取消／超时和设置页加载反馈。后台限流保持静默，手动限流显示等待时间；成功后恢复六小时检查周期。

本次实际运行上述23项检查，未将历史82项结果计入本次通过数量。没有真实GitHub联网、游戏操作、截图、全套测试或安装包重建；当前安装包不包含本次修复。未提交、推送或发布。证据位于本次任务（本地证据：`artifacts/tasks/ui/20261018-200726-update-rate-limit-fix-22f42069/results`），保留修改前失败、修改后JUnit与文本日志、范围差异和验证摘要。

## 2026-10-18 弹窗下载、覆盖更新及重启

用户选择自动下载安装并重启、保留用户设置和记录。新版本弹窗保留可滚动日志及关闭/Esc，增加“更新”按钮和固定说明；下载中显示进度、禁用重复操作并支持取消，安装交接期间等待Windows授权/启动结果。下载失败、校验失败或UAC取消不会关闭现有窗口；只有安装器成功启动且Qt工作线程退出后才按原流程关闭。更新与自动化准备/运行共用忙碌门控；统计线程及其他异步操作沿原有关闭流程收尾。

下载资产与版本/日志绑定同一次正式Release响应，只接受唯一对应Windows安装包、GitHub HTTPS地址、上传完成状态、大小和SHA-256摘要。下载逐块写入state/updates/<版本>-<任务编号>/中的.part文件；请求/总时长、内存缓冲和重定向次数均有界，重定向只允许HTTPS GitHub资源主机。完整性失败/取消删除部分文件，清理失败报告路径。安装启动前复核安装登记、当前EXE、清单、路径、文件大小、摘要及安装EXE内嵌版本。UAC取消时同次运行可重用已校验包；发现内容变化则重新下载。

现有Inno安装器增加专用更新参数及任务ID验证。旧实例有60秒正常退出窗口，超时仍拒绝安装，不强杀进程。成功安装后以nowait启动带本次ID的新EXE；新程序先有界等待安装锁释放，再建立正常运行锁。主窗口显示后后台核对当前版本和本次包身份再删除EXE，短暂文件占用最多重试20次；request.json和install.log保留，其他任务和用户文件不清理。原手动安装行为、目录保护、拒绝降级和logs/state保留规则继续适用；程序配套config、模板按新版更新。

136个不同的直接相关用例最终全部通过，按实际用例ID去重。首轮121项中119通过、2项发现pywin32不导出SEE_MASK_NOASYNC；改用其等值SEE_MASK_FLAG_DDEWAIT后，后续52项通过。补充边界和界面检查后63项通过，最后安装EXE版本核对影响的平台范围23项通过；重复项不累计为新用例，未受后续变更影响的本轮结果保留复用，未引用历史82项或前次23项替代新行为验证。范围包含原限流/更新查询回归、原生Qt响应头、下载/重定向/截断/超量/摘要/磁盘错误、取消/迟到结果、UAC取消及缓存重试、启动前版本/路径拒绝、锁等待/超时、包清理/文件占用/记录保留、窗口关闭和对象释放、760×560及1180×760布局与滚动日志。

另用真实本机HTTP服务向Qt网络栈分块传输测试内容，确认落盘字节、摘要和交接；没有公网下载或执行这些测试内容。项目已登记的Inno Setup 6.7.3对隔离占位payload编译成功，并通过真实Windows文件版本API确认安装器版本为9.0.0.0；该产物仅作语法/元数据检查，没有运行安装器，也不是正式发行包。目录/源码边界检查problems为空，源码语法和本次新增行空白检查通过。

本次未运行项目全套、操作游戏、截图、提交、推送、发布或重建正式安装包。真实GitHub/CDN网络、UAC及安装版覆盖→重启→清理全链路仍须在后续新包受控验收中执行；源码与模拟检查不代表已完成该实机验收。现有dist安装包不包含本次功能。任务results（本地证据：`artifacts/tasks/ui/20261018-202215-in-app-update-install-f0da9f82/results`）保留分批JUnit、去重用例表、ISCC输出与内嵌版本、修改前摘要、范围差异和最终检查结论。

## 2026-10-18 更新日志标题字号与间距

按用户图审意见，将Markdown一级标题“更新内容”调整为与弹窗标题共用的24逻辑像素字号，一级标题下增加20逻辑像素间距。清除Markdown相对字号属性，再按控件逻辑DPI换算文档点数；仅处理一级标题，子标题、正文、滚动和更新操作不变。首轮以FontPixelSize和相对字号0合并的方案在图片中仍表现为正文大小，已由最终明确点数方案替代，不将首轮属性断言作为字体正确的证据。

中途独立字体测量命令使用QTextDocument/glyphRuns查询字形，异常退出（执行记录退出码1），用户同时观察到python.exe读取空地址的Windows提示；具体故障DLL未取得，不能仅凭提示确定底层根因。该测量命令没有进入运行源码，已停止使用。最终沿用此前成功的QWidget离屏渲染方式，加载现有Windows字体，仅在隔离预览目录模拟v1.4.1；最终渲染退出0，字号/间距核对及图片目视检查通过。

实际仅复验更新日志/按钮流程以及760×560、1180×760窗口布局共3项既有相关测试，全部通过（0.61秒），没有新增仅复述实现的测试。未运行全套、联网检查、安装或重建。修改文件为ui/pages/application.py及本记录；原始预览、模拟数据说明、修改前摘要、分阶段输出与最终检查位于本次任务（本地证据：`artifacts/tasks/ui/20261018-210934-update-heading-spacing-af5e77a1/results`）。

后续图审微调：一级标题下方间距由20改为15逻辑像素，“更新”按钮沿用“关闭”按钮的88×40最小尺寸，初始显示实际尺寸一致；其他检查结果弹窗的“重试”按钮保持原尺寸，下载/安装状态文字仍按内容布局。字号保持24逻辑像素。仅复验同三项相关用例，全部通过（0.70秒）；离屏预览逐项核对字号、15像素间距及两按钮实际尺寸，并目视检查通过。未使用前述异常字形测量命令。修改前快照保留在同任务inputs/revision2，最终预览为previews/new-version-dialog-15px.png，验证输出使用results/revision2前缀。

## 2026-10-18 统一弹窗初始焦点

按用户要求，现有及后续应用内弹窗统一使用ui/widgets.py的AppDialog。每次显式打开时将初始焦点放在容器，清除按钮default/autoDefault；无需更新、检查失败、发现新版本（含无日志）全部接入。日志和按钮保留可访问焦点及原焦点样式，初始不再显示文字光标或按钮焦点边框。容器/只读日志聚焦时回车不触发动作，Tab/Shift+Tab选到按钮后回车、小键盘Enter或空格仍可操作；鼠标、文字选择复制和Esc关闭保留，安装交接期间的关闭守卫继续有效。系统自发显示事件不抢回已选控件焦点。

27个不同的直接相关用例最终通过：新增20项检查四类内容初始焦点/无隐式操作、正反向Tab、关闭/重试/更新的键盘激活、文字选择、重新打开、忙碌Esc及后续含输入框弹窗继承公共规则；既有7项检查更新反馈、窗口关闭、下载取消、安装线程退出及对象释放。首轮25通过、2失败：离屏窗口重复打开后未激活，改为核对其保存的焦点目标；既有异步关闭用例独立原样复查通过后，将固定120ms等待改为等待请求真正开始，防止提前注入完成信号。随后仅复验这两处及增强的后续弹窗用例，3项全部通过（0.42秒），其余未变用例沿用本轮结果，不重复测试。

用当前生产控件、隔离用户状态及现有Windows字体完成离屏预览，确认初始焦点为弹窗本身、按钮无默认状态，并目视核对日志开头竖线消失；24像素标题、15像素间距及88×40按钮保持。模拟版本为v1.4.1，没有联网、启动安装器或操作游戏。规则同步AGENTS.md、架构、开发指南、使用说明及CHANGELOG；未运行全套、提交、推送或重建安装包。原始结果、修改前摘要、范围差异及预览见本次任务（本地证据：`artifacts/tasks/ui/20261018-213248-dialog-initial-focus-ca637c2f/results`）。


## 2026-10-18 只读更新日志隐藏文本光标

更新日志控件增加setCursorWidth(0)，仅隐藏文本定位光标；鼠标/键盘文本选择、复制、滚动和只读限制保持。与弹窗初始焦点规则独立：即使用户之后点击或Tab进入日志，也不绘制该竖线。

实际内存渲染对照确认聚焦后的30个光标像素消失，键盘选择、复制用MIME文本、鼠标双击选择、拒绝编辑及长内容滚动均通过；没有修改系统剪贴板或输出截图。既有更新日志/按钮流程、空日志提示和长日志滚动共3项直接相关回归通过（0.86秒），未运行全套。仅修改ui/pages/application.py的一处控件属性及本记录；未调整共享弹窗焦点实现、未构建、提交或推送。证据位于artifacts/tasks/ui/20261018-213808-hide-release-notes-caret-82908bd7/results。

## 2026-10-18 更新日志圆条滚动条与右侧布局

更新日志使用专属8逻辑像素宽、4像素圆角滑块，透明轨道、无上下箭头，保留悬停/按下反馈。样式仅作用于updateReleaseNotes，不改变其他滚动条。弹窗内容改用两列网格，日志跨列延伸至右侧4像素布局内边距；包含边框后实测滚动条距弹窗外缘5像素。标题、说明、下载进度及底部按钮仍保留原来的左右留白，只有日志区滚动，24像素标题、15像素标题下间距及88×40更新/关闭按钮保持。

9项直接相关既有用例全部通过（0.90秒），覆盖空/长日志、最小与常规窗口、下载反馈布局、更新按钮流程及四种弹窗内容的初始焦点。使用真实Qt控件离屏渲染并目视确认圆条和右侧位置；原生滚动条范围、键盘向上/向下滚动以及首尾位置可用。预览脚本最初将Ctrl+Home/End的文本定位等同于滚动条精确最小/最大值，出现两次断言失败；最终分别核对键盘滚动方向和原生滚动条上下限，生产滚动实现没有为测试改变。没有新增仅复述样式的测试，也未运行全套或重复更新下载安装测试。

修改文件为ui/pages/application.py、ui/styles.py及本记录。模拟v1.4.1预览、位置/尺寸数据、修改前摘要、差异和检查结果位于本次任务（本地证据：`artifacts/tasks/ui/20261018-214904-update-log-scrollbar-f7b51d55/results`）。未联网、操作游戏、安装、重建、提交或推送；旧安装包保持原样。

## 2026-10-18 检查失败弹窗重试按钮尺寸

按用户要求，检查更新失败弹窗的“重试”按钮也复用“关闭”按钮的88×40最小尺寸，移除其原120像素宽度分支。真实Qt控件在760×560和1180×760两档窗口下核对两按钮实际尺寸均为88×40，初始容器焦点及重试信号保持；既有更新反馈/失败重试流程1项回归通过（0.45秒）。未增加样式镜像测试，未运行全套、联网、安装或重建。仅修改ui/pages/application.py及本记录；沿用标题与按钮样式任务，当前基线在inputs/retry-button，结果使用results/retry-button前缀（本地证据：`artifacts/tasks/ui/20261018-210934-update-heading-spacing-af5e77a1/results`）。

## 2026-10-19 小字双向平滑试用

用户提供的16px、200%缩放文字截图与本机Qt 6.11.2默认字体策略的内存绘制结果逐像素一致。实际中文字形为Microsoft YaHei UI，Windows字体平滑及ClearType已开启；透明和不透明白底均能复现。Qt在高DPI下默认对不大于16px的文字选择仅水平方向平滑，较大字号选择双向平滑。单独指定PreferAntialias不改变结果。

本次通过ui/widgets.py的_use_smooth_text显式设置PreferNoHinting，仅接入更新弹窗正文、日志、安装提示、按钮，以及统计说明和重新上传按钮；字号、颜色和禁用状态保留。日志文档默认字体也继承该策略。新增小字是否统一采用此方案，等待用户确认本次实屏观感后再确定，不修改全局应用字体或系统设置。

8项既有相关回归通过，覆盖统计状态和布局、页面最小尺寸、更新弹窗操作、空/长日志及下载按钮布局。另在Windows平台以100%、150%、200%三档DPR核对90项实际字体/颜色属性及18组弹窗布局；内存像素核对确认接入后的正文使用新策略。检查脚本最初遇到未展示控件尚未完成布局和清理方法名错误，修正检查方式后通过，生产代码未为这些检查问题增加分支。

未生成对照图或截图，未运行全套、联网、操作游戏、安装、重建、提交或推送。用户实屏观感尚未验收。修改前快照、相关回归JUnit、原生属性/布局及像素核对结果位于本次任务（本地证据：`artifacts/tasks/ui/20261019-004922-small-text-smoothing-370f69c3/results`）。

后续确认（2026-10-19）：用户实际打开脚本后反馈“可以，效果很好”，本次实屏观感验收通过。按此前“效果好则后续新增小字也采用”的要求，将新增不大于16逻辑像素文字复用_use_smooth_text的规则写入AGENTS.md与开发指南。此次仅更新规范和验收记录，源码未变，复用上述8项相关回归和三档DPR检查结果，未重复运行测试；原交付时的pending摘要保留为历史记录。

## 2026-10-18 更新弹窗关闭后桌面无法点击（已搁置）

本节于2026-10-20补录此前对话中的排查结论，不代表重新开展诊断。用户确认源码程序停留设置页、未启动自动化，点击检查更新并关闭结果弹窗后，任务栏及其他软件均无法点击，但鼠标指针仍可移动；短按电源进入待机，再唤醒登录后恢复。

此前只读系统记录显示：23:49:01，ShellExperienceHost.exe出现Application Hang 1002及对应MoAppHang报告；23:49:46因Power Button进入新型待机，23:49:49退出。系统没有在此期间重启，所查时段未发现蓝屏、显示驱动恢复或WHEA事件。恢复后读取E7auto界面线程时，鼠标捕获及菜单模式为空，主窗口未被禁用；这些仅代表恢复后的状态，不能反推卡住瞬间。

此前隔离的正常更新查询、取消、超时处理、TLS提前断开和握手异常检查未复现`QIODevice::read (QSslSocket): device not open`警告。警告、系统界面挂起和关闭弹窗之间的因果关系未确认，也未排除E7auto的关联。用户于10月19日明确决定暂不处理；没有添加诊断开关、修改源码或声称问题已修复。再次调查须以新的明确请求为准。
