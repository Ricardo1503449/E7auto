# 开发指南

## 可逆点击的有限重试

2026-10-31新增共用`runtime/action_retry.click_and_wait`，只用于显式接入的页面、选中、返回及同页关闭。业务回调通过`Transition`返回完成结果、已有响应或阻止补点状态；共享层不引用功能模块，也不在鼠标输入中自动循环。首次使用调用方已有证据；补点必须在原页面重新识别原目标并连续三张新帧稳定，使用最新坐标。可靠响应一旦出现便永久禁用本次补点，最终结果仍满足各功能原确认规则。原缓冲只执行守卫，不累计后续证据。

`internal.yaml.click_retry`包含`interval_ms: 3000`、`max_attempts: 2`和`minimum_timeout_ms: 6000`。省略整个区块的旧独立配置使用同样默认值；显式区块须完整、无未知键且为正整数，次数不得超过2，最短总期限不得小于次数乘间隔。总期限从首次输入前开始，取原阶段期限与6000毫秒的较大值，并受外层剩余期限限制；间隔从前次输入返回后计算。重连仅清稳定证据，保留次数、响应记录及有效时间期限。原有专用重试不变。

检测、输入、缓冲和日志后均检查停止与期限。输入异常直接停止，不重放不确定输入。新增补点事件`click_retry`及每次调用一次的`click_transition`汇总，不逐帧记录或新增自动截图。接入须指定原目标及后续页面，不以结果未识别到单独授权补点。覆盖与验证见[点击重试验证](../validation/common/CLICK_RETRY_VALIDATION.md)。

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `src/e7auto/app.py` | 启动、环境检查及资源根目录解析 |
| `src/e7auto/ui/` | 主窗口、悬浮窗、功能页面、控件与 Qt 工作线程 |
| `src/e7auto/bootstrap.py` | 显式选择功能、构造流程与生产服务 |
| `src/e7auto/core/` | 几何基础类型、运行状态、公共观察结果和平台服务接口 |
| `src/e7auto/runtime/` | 会话、窗口守卫、截图、输入锁、网络恢复、导航、快照与性能统计 |
| `src/e7auto/features/shop/` | 商店流程、商品扫描/购买、刷新策略、滚动及专用识别 |
| `src/e7auto/features/penguin/` | 企鹅流程、专用识别、资源配置校验及接口 |
| `src/e7auto/features/chaos/events/` | 通用前置文本入口、显式事件分派和每事件独立处理器；公共能力通过EventContext注入 |
| `src/e7auto/vision/` | 共用模板匹配、数字字形、帧适配和网络识别 |
| `src/e7auto/resources/` | 模板清单完整性校验、图片读取 |
| `src/e7auto/configuration/` | 配置数据模型、YAML结构规则与加载；文件格式和参数保持原约定 |
| `src/e7auto/platform/`、`src/e7auto/logging/` | Windows窗口/消息输入/WGC，以及运行日志/异常截图 |
| `scripts/calibration/` | 模板提取、校准、校准证据归档；悬浮窗位置校准需要实机 |
| `scripts/validation/` | 实机验证器及管理员启动脚本 |
| `scripts/release/` | Nuitka / Inno Setup 构建与安装产物校验 |
| `scripts/common/` | 工具共用的项目路径和离线图片读写 |
| `tests/ui/`、`tests/automation/`、`tests/scripts/` | 对应职责的回归测试 |
| `tests/helpers/`、`tests/fixtures/` | 配置工厂、替代服务和固定测试图片 |
| `config/`、`assets/` | 当前运行配置、模板与 UI 资源 |

UI入口仍在 `ui/__init__.py`；源码调用使用明确模块。旧 `automation/`、根部 `config.py`、`vision.py` 等入口已迁移，项目内脚本和测试已同步，不保留无调用方的转发实现。机器规则中的legacy_modules是禁止新代码引用的旧入口名单。测试替换依赖时，替换实际使用者，例如 `e7auto.ui.main_window.QThread`、`e7auto.bootstrap.TemplateRepository`。

`bootstrap.AutomationSession` 保留商店/企鹅启动请求，`runtime.session.RuntimeSession` 接收流程工厂和停止策略，管理单轮生命周期。`ShopFlow` 与 `PenguinFlow` 平级组合 `RuntimeContext`，互不导入或继承。网络恢复只更新共享恢复代次、计时和状态，通过回调使商店失效自己的缓存；企鹅保留恢复后重新截图策略。`StopController` 分离短时停止状态锁与输入串行锁，停止请求不等待原生输入及日志；输入取得串行锁后检查停止状态，已经获准的单次操作可完成必要的释放。商店 `scrolling.py` 通过窄回调使用受控输入、截图、计时与识别，不持有流程对象。

初始状态通知在会话的try/finally保护范围内，仍先于停止检查和流程创建；通知异常不得开始功能流程，后续仍尝试清缓存、关闭截图服务和日志。会话收尾分别尝试功能清理和截图服务关闭，清理异常追加到原始停止详情，不覆盖业务停止原因。最终状态回调或收尾日志失败也不能跳过日志关闭；日志关闭错误仍交给worker兜底。卡厄思即使重置识别或一个线程池关闭失败，也继续尝试另一线程池及最后一帧释放。

所有功能通过FrameView在原始客户区像素上识别，搜索ROI向外取整，子视图保留全局原点。基准尺寸走独立的已验证识别实现；非基准模板按真实客户区比例与有限像素格相位按需生成；画布取整不改变比例，透明边距不计入内容定位；每次运行共用一个默认64 MiB的候选缓存，包含生成缓冲预留和新增预处理数组。ClientPoint/ClientRect表示已测得的客户区坐标，输入守卫不再重复缩放；配置Point/Rect仍为基准坐标。事件与战利品组合部件必须共用父模板采样网格，禁止将部件独立缩放后拼接定位。截图不缩回基准尺寸，不保留旧非基准缩放开关或兜底；已验证的基准分支长期保留。非基准优化不得默认改变基准行为，修改公共层须核对影响，改变基准行为需用户明确授权。三选一每帧仍对完整原图锚点区域计算SHA-256，缓存身份必须重新验证当前图标和全名。数字字形归一化、索引特征和粗定位缩图属于专门算法，继续保留；最终模板证据在原图验证。详见[原图匹配设计](../design/NATIVE_MATCHING.md)。

四个相同升阶路线和五个相同战斗直领奖励路线分别调用`EventContext.run_rank_reward`和`run_battle_reward`。各事件保留自己的入口和点击绑定；这两类已选路线不产生探查经验，仍使用普通文本收尾。经验事件继续显式调用自身的可选升级收尾能力。

`ui/worker.py` 负责Qt线程、信号及启动失败上报，在 `run()` 中调用 `bootstrap.create_production_session`。WGC/PyWinRT只在该工厂被工作线程调用后导入，或在独立的 `app.validate_wgc_import` 自检入口导入；UI及包导入不得提前加载。功能流程、识别算法和共享运行层不得直接调用Windows实现。

UI启动先在主线程冻结本次参数，由 `ui/preparation.py` 后台读取配置、校验资源；
准备线程完全退出且未取消后，主线程才配置悬浮窗并创建自动化工作线程。准备阶段
关闭窗口会取消后续启动，等待当前文件操作结束后关闭；不得强行终止线程。
任务结束信号只更新最终显示，按钮和导航在QThread.finished后以非阻塞wait(0)
确认退出才恢复。日志创建、写入和关闭异常不得阻止终止信号或UI恢复。

运行结束通知在同一线程退出确认后由ui/notifications.py统一分类和去重；
正常完成、预期停止和异常停止发送一次，手动停止及取消准备不通知。设置页
“运行结束通知”复用_ToggleSwitch，默认开启，ui/preferences.py原子保存到
state/ui_preferences.json；保存失败恢复开关原值并给出错误。Windows适配器
platform/notifications.py在源码运行时登记当前用户下E7auto.E7auto的显示名、
PNG图标和COM通知回调身份；安装版使用由安装器管理的机器级E7auto.Installed身份，
运行时只读核对实际图标路径，卸载由安装器删除。platform/toast_runtime.py直接调用系统WinRT ABI，
不导入PyWinRT、不增加二进制依赖；各次发送的COM接口和HSTRING在当前线程成对释放。
点击通知使用进程内空操作COM回调，不恢复/启动主窗口；不登记LocalServer32或开机启动。
ToastGeneric使用系统默认提示音，音量和勿扰策略由Windows控制。历史记录由Windows
管理：横幅收起、超时、主窗口关闭、通知开关关闭均不调用Hide/Remove/Clear。
已移除旧托盘图标、原生通知消息钩子和120秒清理定时器，不添加结果卡片或悬浮提示。
普通单元测试替换发送/身份登记接口；系统XML ABI测试不发送通知。真实验证应在可用
Windows桌面发送明确标注的测试通知，等待横幅结束及进程退出，再通过GetHistory仅
读取E7auto自身的记录。仅Show成功不能代替通知中心留存验证。

三个功能页共用 `ui/pages/feature.py` 的展示布局及整数校验，业务实现仍独立。
商店页通过ui/preferences.py的ContinuousRefreshPreferences将“连续刷新”原子保存至
state/ui_preferences.json的shop_continuous_refresh，并保留其他偏好键；缺失、损坏或
非布尔值默认关闭。创建页面时先恢复开关再连接切换信号，不因启动读取而重写文件。
切换成功立即保存，失败阻断信号回滚至上次成功值并使用页面状态提示；其他启动参数
不随此设置保存。点击启动时仍冻结本次RunRequest，准备/运行禁用及结束后恢复规则保持。
主窗口样式及公共颜色、字号、间距在 `ui/styles.py`，沿用无悬浮提示规则。
卡片图片缓存按逻辑尺寸乘DPR生成，源裁剪坐标同步换算为物理像素；缓存键包含
尺寸和DPR，保留PNG字节和原构图。目录及浏览器打开经 `ui/desktop_actions.py`
调用Windows后台适配器，COM初始化和释放发生在同一线程。测试替换该模块实际
使用的 `open_default`，并等待异步完成；不得真实打开外部程序代替离线测试。

用户已于2026-10-19确认小字双向平滑效果。后续新增不大于16逻辑像素的界面文字（说明、
状态、按钮文案等）统一在控件初始化时调用`ui/widgets.py`的`_use_smooth_text`，显式设置
`QFont.PreferNoHinting`；字体族、字号、字重、颜色及禁用状态仍由样式管理。富文本控件需
确认文档默认字体也继承此策略，不以全局修改应用字体或系统ClearType设置替代局部接入。

所有现有及以后新增的应用内弹窗统一继承`ui/widgets.py`的`AppDialog`。每次显式打开时，
初始焦点放在弹窗容器，不显示日志开头光标或某个按钮的焦点边框；不要给容器增加焦点装饰，
不要用禁用控件焦点或移除按钮focus样式实现这一效果。基类统一清除按钮default/autoDefault，
容器或文本区域聚焦时，回车不会触发操作；通过Tab/Shift+Tab选择按钮后，回车/空格可激活，
鼠标操作和文字选择复制保持。Esc沿用reject及具体弹窗已有的关闭守卫。窗口系统的自发显示
事件不重置用户已选控件；不要在每次激活窗口时抢回容器焦点。新增弹窗不得直接绕过该基类。

AppDialog.open统一使用非阻塞的NonModal展示，由主窗口的AppDialogHost提供内容区限制，
不调用QDialog.open/exec或自行设置WindowModal。弹窗以主窗口或其子控件为父级，沿父级
链自动接入公共宿主；不再由具体页面/控制器分别发信号开关遮罩。宿主只禁用appBody，
标题栏保留拖动、最小化、最大化/还原和关闭，返回导航、页面切换与启动脚本继续受限。
主窗口移动、调整大小及还原时按弹窗完整frameGeometry居中跟随，并合并下一轮原生几何
复核；Esc在焦点回到主窗口后仍交给当前弹窗。显式隐藏或销毁最后一个弹窗才解除限制，
恢复内容区原启用状态；最小化产生的系统隐藏不视为关闭。主窗口关闭仍遵守弹窗reject
及更新安装交接守卫，公告关闭主窗口时仍不记为已读。新增弹窗沿用此公共路径即可接入。

主窗口持有`ui/announcements.py`的AnnouncementController，在首次显示后的事件循环中
展示本地“公告”，引导用户进入“关于 → 使用说明”并展示反馈交流QQ群。公告仅提供
“我知道了”，正文及关于页功能介绍下方的群号均支持选中复制；群号文案共用同一常量。
公告继承AppDialog，正文及按钮接入_use_smooth_text，复用白色圆角弹窗和公共内容区遮罩。
点击按钮或Esc关闭后，将独立公告编号原子保存至`state/ui_preferences.json`的
`announcement_seen_id`，读写保留通知、统计和其他设置；普通版本升级不重弹，确需再次
提醒时显式更新ANNOUNCEMENT_ID。缺失/损坏状态视为未读；保存失败仍关闭且本次不重弹，
下次启动可再次提醒。最小化恢复、切换页面及重复show不重新调度；主窗口关闭取消待显示
或正在显示的公告，不将尚未确认的内容记为已读。UI公共fixture默认禁用公告，专项测试
恢复真实调度，所有测试使用隔离状态目录，不改动开发者自己的已读记录。

主窗口持有`ui/updates.py`的UpdateController，设置页和设置导航图标共用更新状态。
首次显示后1秒启动调度，成功结果缓存6小时；后台静默检查，手动检查才显示现有弹窗，
并可加入正在进行的后台请求。版本/日志、检查时间和退避状态原子保存至
`state/update_status.json`，红点始终与当前运行版本重新比较，不保存独立布尔值。
失败保留最后成功结果，连续失败按30分钟、1/2/4/6小时退避，封顶6小时；
明确限流时读取Retry-After及额度耗尽时的X-RateLimit-Reset，手动请求也遵守等待时间。
窗口关闭统一取消请求和定时器，迟到结果忽略；缓存损坏/写入失败不影响脚本运行。
两处红点使用7逻辑像素的同色圆点，条目圆点锚定文字右上角，保持无悬浮提示及无障碍描述。
UI公共fixture默认禁用自动调度以避免无关用例联网；启动调度专项用例恢复真实定时器，
替换网络传输并使用受控时钟。识别及自动化功能不依赖更新状态。

手动更新安装使用ui/update_install.py：安装包元数据须来自与弹窗版本/日志相同的Release响应，
只接受对应版本、唯一已上传的Setup_x64.exe、正整数大小及GitHub SHA-256摘要；缺少可校验
安装包时显示原因，不猜测下载地址或降级安装。下载限制512 MiB，Qt缓冲1 MiB，逐块写入
并计算摘要；30秒传输空闲超时、15分钟总期限、最多5次HTTPS GitHub资源主机跳转。
取消、网络及写入错误释放请求并清理本次部分文件；不得仅凭HTTP 200执行下载内容。
传输测试保留Qt真实响应头接口，至少覆盖真实本机HTTP流、截断/超量/错误摘要、重定向及取消。

platform/update_install.py在独立Qt线程内重新验证安装登记、当前EXE、安装清单、路径、包摘要及内嵌版本，
通过成对COM初始化及ShellExecuteEx启动现有安装器。UAC取消/启动异常不关闭当前窗口；
成功交接后才沿现有关闭流程退出。更新期间禁止新自动化任务，不强杀工作线程。
新增安装模式由e7auto.iss实现，保留正常手动安装行为；--after-update只增加有界等待安装锁，
不绕过锁。成功后的安装包清理须绑定该次任务和当前版本，保留安装日志/元数据，拒绝路径
跳转或不匹配包。真实发布需验证新包覆盖、重启、数据保留及清理，源码模拟不能代替安装验收。

## 源码依赖与后续扩展

商店流程显式组装`InventoryScanner`、`RefreshController`及类型化`ShopState`；库存与刷新不再接收整个ShopFlow。刷新仅接收所需库存能力和进出商店回调。卡厄思的`StageSelection`、`HeroRecruitment`接收窄接口回调，战斗、原楼层及恢复状态仍由ChaosFlow管理。配置加载入口调用各职责解析及交叉校验函数，保持原YAML结构和累计错误顺序。

三选一入口等待在`config/chaos.yaml`中独立配置：精英及第一/二层首领使用`timing.loot_entry_settle_ms`，默认1000ms；补给使用`timing.supply_loot_entry_settle_ms`，默认3000ms，基准与非基准尺寸共用。两项均为必需的正整数，仅作用于各自领奖后的进入三选一步骤，替换这些点击原有300ms缓冲；其他点击保持原值。等待复用`_delay`，网络恢复暂停有效计时，结束后重新取帧开始识别。调整此值只需进行入口时间和守卫相关验证，不改模板、索引、ROI或识别阈值。

发布工具的`capture`保存两种摘要；`seal`仅计算原始SHA-256；验收与最终提交核对仅计算输入比较所需的Git对象摘要。Git摘要使用`hash-object --stdin-paths`按过滤策略分为至多两个批次，文本继续按Git过滤规则计算，图片使用`--no-filters`保留原始字节；UTF-8路径按Git的C引用格式传入，哈希后复核输入集合和字节，拒绝计算期间变化。模板历史/暂存区和目录暂存区检查通过`scripts.common.git_objects.GitBlobReader`按对象ID读取，一个检查复用一个按需启动的`cat-file --batch`进程；二进制按长度读取，缓存只在本次检查内生效，结束及异常均回收进程。

- 新功能先登记layout.json的功能名，在 `features/<feature>/` 放流程、识别及契约；在bootstrap显式接入工厂、配置/资源选择、完成策略，再接入UI和发布校验。登记目录名不会自动启用功能。
- 功能间禁止导入和继承；共享层禁止反向导入功能实现或UI。跨功能复用先提取语义确实一致的公共组件，专用滚动/金额规则继续留在所属功能。
- 日期变更接入使用`configuration.daily_reset.load_daily_reset`按需加载共享配置/模板，再向`runtime.daily_reset.DailyResetHandler`传入受控runtime、按键识别回调和主界面检测回调。识别回调返回公共`Observation`，不传入完整功能流程。功能在自己的安全页面边界调用`wait_for_ready(ready_detector)`；返回READY继续原流程，返回HOME由功能决定结束或重新导航。默认大厅观察为2秒，`observe_ms=0`只用于已经观察后的临点击复核或入口确认；未识别到任何有效页面仍按页面超时停止。轮数、阶段、启动唤醒及功能恢复策略不下沉到共享模块，不在每次截图时全局拦截日期弹窗。
- 2026-10-06用户确认日期变更弹窗只在次元探查大厅出现。正常日期监测保留在大厅等待、开局前及退出大厅的边界；阶段选择面板等待、拖动、选中和提交不得逐帧查询date_change_title，返回大厅后恢复。已进入日期恢复时，原弹窗关闭、签到及奖励/增益状态保护继续由共享处理器负责。模拟游戏也应让面板期间到期的日期变更延后到大厅触发，不用不符合游戏行为的面板弹窗测试维持无用扫描。
- 签到后的登录奖励、增益效果和主界面在同一新截图上判别；两个弹窗标题冲突或关闭证据残留时继续有界等待。增益可直接出现或跟随登录奖励，不允许增益之后返回登录奖励分支。`buff_effect_close`和`login_reward_close`共用识别图片，但每种弹窗的关闭次数、稳定签名和期限独立，不能按共用PNG来合并动作状态。
- 新bug修改所属模块；不建立根部helper、misc或fix副本，不把修复脚本塞入运行包。公共fixture仍留在tests/helpers。
- `scripts.project.check_layout` 同时执行源码位置和依赖检查。它解析绝对/相对导入、函数内部导入和字面量动态导入；工作区与staged使用各自的源码、规则视图。动态拼接模块名不在静态检查保证内，不应使用它绕开依赖规则。
- 边界测试位于 `tests/core/test_source_boundaries.py`，包括跨功能导入、底层反向依赖、旧入口引用、Win32越界和WGC提前导入；索引与工作区差异测试仍在tests/scripts/project。不会安装hook或配置CI。
- 配置YAML和RuntimeSnapshot继续维持兼容的聚合格式；此次拆分源码职责，不改变运行配置格式、UI字段、识别阈值或模板字节。

## 环境与运行

WGC运行期正常取帧期限由 `config/internal.yaml` 的 `timing.capture_frame_timeout_ms` 设置，默认1000毫秒。已有成功帧后，只有适配器专用取帧超时才允许每次请求重建一次会话，再按 `capture_recovery_timeout_ms`（默认3000毫秒）等待新帧；首次启动仍为2秒，不进入运行期恢复。重建后3秒从新会话启动完成后开始，不包含前面的正常等待和原生重建耗时。窗口、身份、尺寸、手动停止和其他原生错误不触发重建。

`capture_rebuild_cooldown_ms` 默认60000、`capture_rebuild_window_ms` 默认600000、`capture_rebuild_max_attempts` 默认3。使用单调时钟统计重建开始时间，达到60秒可以再次重建，达到10分钟的旧记录移除；失败尝试计次，成功取帧不清零，每次运行独立。受限时原会话额外等待一次3秒，不重建；仍超时或重建失败则停止。这四个新增字段与原取帧期限一样，缺失时使用默认值，显式值必须为正整数。

会话重建仅重新创建目标、帧池、会话和回调，复用原工作线程、COM与D3D设备；旧回调绑定旧会话事件，不会唤醒新会话。清理失败时尝试其余清理并拒绝重建。保留输入时间边界、最后已接受帧时间及100毫秒帧龄限制，各阶段拒绝不合格帧不会延长期限。商店、企鹅、卡厄思及两种尺寸共用恢复策略，但识别算法和坐标不变。采集恢复不重发业务输入、不暂停或重置页面和战斗计时，不返回缓存图。窗口/停止守卫覆盖队列读取、短等待、原生初始化后及复制后检查。原生调用不能强制取消，返回后仍须复核期限和帧龄。

失败诊断的 `capture_elapsed_ms` 是本次调用总耗时，`capture_wait_ms` 是等帧/时钟追赶耗时，`capture_copy_ms` 是原生位图复制和像素读取耗时；`expired_before_copy`、`expired_after_copy` 区分本次丢帧阶段。复制后的裁剪及检查计入总耗时，不计入位图复制耗时。计数每次采集清零，不增加逐帧日志。

`wgc_recovery_started/skipped/completed` 记录重建或额外等待、限频原因、窗口内次数、初始/重建/恢复耗时和结果；汇总分别统计重建尝试、成功、失败、中断、受限与额外等待成功。只有最终请求失败才记录 `wgc_capture_failed`。成功恢复的结束日志在下次采集前或最终关闭输出，保留实际测得的耗时，避免日志写盘使返回帧过期；失败和中断立即记录。日志异常不阻断采集或清理。

在项目根目录执行命令，运行和开发统一使用项目 `.venv` 中的 Python 3.12。首次准备环境时创建虚拟环境、安装锁定依赖和本地项目：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --cache-dir .\.pip-cache -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

只有创建虚拟环境的第一条命令使用系统Python；后续安装、运行和测试均使用上述项目解释器，不启用用户级site-packages。环境就绪后核对版本并启动：

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_environment
.\.venv\Scripts\python.exe -m e7auto
```

Python 开发工具统一使用 `python -m scripts.<分类>.<模块>`。不要直接运行分类目录中的 `.py` 文件；模块模式会正确解析 `scripts.common`。Python 工具通过 `scripts/common/paths.py` 定位资源，不使用进程当前目录推算资源路径。PowerShell 启动脚本自行定位项目根目录并设置工作目录。

## 规划记录与恢复

`task_plan.md`、`findings.md`、`progress.md` 留在项目根目录，使用 `.gitignore` 中的三个根目录规则排除提交。

- `task_plan.md` 记录当前任务、阶段和约束。
- `findings.md` 保存发现、依据和已确认决策。
- `progress.md` 记录操作、验证结果和错误处理。

恢复任务时先读取三个文件，再结合当前 Git 差异继续。追加新阶段并更新其状态，不清空历史。当前安装的 planning-with-files 恢复脚本直接检查项目根目录；不要将这三个文件移动到自定义子目录。Git 忽略不会阻止技能读取文件，但也不会为本地记录提供远程备份。

`artifacts/` 和 `pip.ini` 是现有本地文件，整理代码时不自动删除或提交。运行日志仍在安装根目录的 `logs/`、用户状态仍在 `state/`；开发任务产物和构建输出遵循[目录与产物约定](LAYOUT.md)。

## 地图节点进入与重试

七类节点统一调用`ChaosFlow._enter_node`，基准与非基准共用控制逻辑，保留各自原图匹配后端。首次选择继续消费紧接着的稳定地图一次性交接；重试从新帧调用现有map_view，不重用旧MapView或重新运行choose_node。原楼层、原类型、当前图标中心位于首次检测框内且唯一，连续stable_frames帧稳定才授权补点；框和锚点均使用已有检测的客户区单位，不额外换算。未知地图、异层、缺失、歧义和移动不授权补点。

每帧先检测node_enter，可靠出现一次即保持本阶段已有响应状态，之后不再补点节点，即使按钮暂时漏识别或发生重连。按钮继续按原三帧规则确认并用最新锚点提交一次；提交后的event_entry_settle_ms/action_settle_ms保持原值。timing新增map_node_retry_ms=1500、map_node_max_attempts=3（含首次），严格正整数校验，并要求action_settle_ms < map_node_retry_ms < page_timeout_ms。阶段从首次节点点击前开始计算page_timeout_ms（15000），补点和重连不重新计时，恢复只清稳定证据，不恢复次数或清除已响应记录。匹配和重试日志后再次检查期限，输入沿用原停止/窗口守卫。超限保留RECOGNITION_TIMEOUT并附节点、楼层、次数及node_selection/enter_button阶段；chaos_node_click_retry只记录补点尝试，不逐帧写日志。进入按钮本身不增加重试。

## 事件入口展开检查

公共事件入口在完整发现前共用`features/chaos/event_readiness.py`，基准与非基准使用同一就绪规则。现有二／三选项标题元数据生成所有标题的局部灰度对比参考，灰态及索引零权重标题也参与；每套组合内所有标题须按同一偏移对齐，取最弱标题分数，不能用整组平均值掩盖缺项。连续新帧满足`timing.stable_frames`（当前3），整组描述符及偏移稳定后才启动原完整发现。就绪检查只返回布尔值，原索引继续只排序，所有候选、原匹配后端和完整身份三帧确认保持。文本三角优先，未就绪继续截图，不延长15秒期限。

参数位于`event_recognition.readiness`：`contrast_kernel: 9`为基准像素的局部灰度形态学核，按真实客户区比例映射成奇数核；`feature_shift: 3`为描述符格子的共同偏移半径；`alignment_tolerance: 1`为各标题独立最佳偏移之间允许的最大跨度，同样以描述符格子计，防止整组折中对齐掩盖单项仍在移动；`similarity_min: 0.55`为最弱标题结构分数；`contrast_ratio_min: 0.60`为标题槽位对比特征相对参考的最低覆盖；`feature_change_max: 0.025`为相邻特征的相对绝对变化上限。这些仅用于预检查，不是正式模板颜色/结构门槛。配置拒绝缺项、未知字段、非有限值和非法范围。失效回调及几何变化、候选复核失败、入口结束均清空数值描述符和计数，不保存截图或跨帧匹配分数。无begin/end的单次只读识别保留当前帧完整性检查；生产入口通过既有begin/end负责连续就绪检查。

## 事件卡片关闭

战斗结算的“升级”弹窗与事件奖励卡片分别处理。普通/精英/第一二层首领战在战斗阶段按`battle_signal_poll_ms`（1000ms）先识别升级标题，未命中时在同一帧补查VICTORY，复用网络/FAILED守卫取得的画面，不另起奖励入口的5秒轮询。升级标题独立授权进入观察，不要求此前命中过VICTORY或发生网络恢复。自动模式确认期间使用同一节流探测，自动状态自身的采样保持。升级标题或VICTORY首次单帧命中后启动`battle_result_settle_ms`（3000ms）观察窗口，按`poll_ms`（100ms）取得新帧；升级标题和关闭按钮共同连续三帧稳定后，直接点击最后一帧的关闭位置并提前结束窗口，不另起关闭按钮确认。任一升级标志残留或不完整时阻止后方结算；窗口到期也不绕过弹窗。无升级则等待窗口满3秒后稳定确认本场结果。关闭只提交一次，保留300ms缓冲，随后直接确认后续结果。重连清空稳定证据、暂停有效计时但不重置窗口或关闭标记；仅发生本场网络恢复时，前置探测额外接受关闭提示/结算信号直至接续成功，覆盖VICTORY已错过且恢复后仍有过渡帧的情况。所有阶段受原战斗期限约束，识别完成后再次检查期限，停止/窗口守卫保持。第三层首领和事件战继续使用原结束条件与`settlement_poll_ms`（5000ms）。

精英及第一二层首领的领奖入口直接使用`_fight`返回的已连续三帧确认的Detection，核对身份与场景可点击后立即`_tap`最后一帧锚点，不再对同一个领取按钮执行`_act`重复确认。中间不额外截图或等待；输入仍通过停止锁与完整窗口检查，随后保留`loot_entry_settle_ms`等待、三选一识别及各自领取完成确认。普通战的VICTORY与继续探查是不同目标，不复用为同一按钮；第三层首领和事件战保持原分支。此优化不改变3秒观察窗口：无升级时满窗口后才开始稳定确认结果，未识别到结果或存在不完整/残留升级时继续等候，不能到点直接跳转。

所有原先检测升级的战斗在后续页面稳定前都继续检查升级，统一通过`_wait_battle_transition`实现。普通战从VICTORY交接到“继续探查”按钮确认、点击及原楼层地图连续三帧确认；精英和第一二层首领从领奖点击到三张战利品连续三帧确认。每张新帧优先检测升级标题和关闭文字，任一命中都打断后方页面/按钮的稳定证据，两者共同三帧稳定才关闭。`_battle_levelup_closed`在每次`_fight`开始时清零，贯穿结算及后续页面，关闭提交后及网络恢复时保持，已关闭过的残留弹窗不重复点击。晚到升级关闭后保留300ms缓冲，先确认目标地图/三卡；只有本阶段刚提交过升级关闭、目标页面尚未出现且同一“继续探查”/领奖按钮新三帧稳定，才用最新位置补点一次，沿用各自action_settle_ms/loot_entry_settle_ms。按钮仍无响应不再补点，网络恢复本身不授权重发。普通战保留原按钮等待、点击缓冲及地图等待的分段期限；各个等待内部的检测、关闭、补点及缓冲共用原page_timeout_ms（15000ms），不因升级或补点另起15秒，识别或日志跨期限后禁止输入。地图必须是原楼层，确认后保留原一次性交接；部分地图证据被升级或重连打断后重新累计。补给、事件战、第三层首领及原3秒结算观察不变；基准和非基准共用流程，不改模板、ROI、阈值或匹配后端。

`chaos_battle_settlement`仅在signal、popup_visible、popup_complete、popup_stable、close_submitted和result_confirmed阶段记录日志，提供自本场等待起点及上一阶段起点的有效耗时、墙钟耗时；close_submitted在原生输入返回后、300ms缓冲前记录。`chaos_battle_settlement_summary`每场结束（含中断/超时/战败）一次，记录截图次数/耗时、升级和VICTORY各自的匹配次数/总耗时与结果。后续页面等待中的晚到升级关闭和同页动作补点分别记录`chaos_battle_transition_levelup_closed`与`chaos_battle_transition_action_resumed`，携带target/action_key，并沿用原有`input`及整段`chaos_loot_recognition`汇总，不增加逐帧日志。首次可识别时刻不等于游戏实际开始显示时刻，日志不声称测得不可见于采样间隔内的出现时间。

首领战在结算预兆出现前同时检测story_skip_prompt和story_skip，确认弹窗优先；自动战斗确认期间也接收这两类信号。跳过单帧可靠命中后直接通过受守卫的_tap点击；确认须提示文字及story_skip_confirm连续三帧稳定，后者复用unclaimed_confirm资源。提交确认后连续三帧确认提示消失，才允许下一段跳过；跳过缺失不代表剧情完成，必须出现升级、VICTORY、当前楼层奖励或星之旅途信号。进入结算观察窗口后不再检测剧情。story_timeout_ms=120000限制整段剧情有效时间，story_retry_ms=2000及story_max_attempts=3仅允许对仍稳定可见的同一控件有限重试；所有动作仍受原战斗期限、网络恢复、手动停止及窗口守卫。剧情信号已确认胜利，此后停止FAILED检测。普通、精英、事件战不检测剧情。

FAILED检测仅在_fight已提交战斗、尚未确认成功时启用，覆盖普通/精英/首领/事件战斗。首次命中后按poll_ms复核，三个连续新帧的位置签名一致才抛出BattleDefeated；未命中或网络恢复清空证据，复核期间不运行自动按钮/结果操作。首领剧情单帧信号或稳定战后结果关闭检测，_fight的finally也清除战斗标志。execute在当前探索轮捕获该信号，点击最后一帧FAILED匹配中心、确认journey_result，再共用_close_journey关闭并确认大厅；FAILED和结算关闭均接入本页有限重试，只有原页面、原目标重新稳定且未出现可靠响应时才允许间隔至少3秒补点一次。continue重建RoundState，绕过成功轮数递增。failed场景仅开放中心点击锚点，原PNG/ROI/阈值不变。回收任一步超时、手动停止或窗口异常都停止，不重启本轮回收或重置补点次数，不把失败计入成功；不存在自动失败次数上限，仍以成功轮数目标或既有停止条件结束。

事件入口使用`event_entry_settle_ms: 4000`，替换该次进入按钮原有的300ms等待；其他节点及文本点击继续使用action_settle_ms。入口与文本收尾都先识别一次三角形，未命中才分别检查选项或原楼层地图，两者都未确认则继续新截图轮询。不要把单次三角未命中当作永久切换阶段的依据。

事件选项统一使用`EventContext.choose(choice, next_page=...)`，文本路线使用`finish_text_and_map(choice=event)`，经验路线仍由自身处理器显式调用`finish_with_optional_levelup(..., choice=event)`。基准和非基准共用`events/choices.py`的有限重试；处理器声明当前阶段的预期后续页面，不重新调用全局分派。新帧上后续页面证据优先于原选项，即使还未满三帧也先阻止补点；只有同一原选项的位置连续三帧稳定、距上次点击至少`event_choice_retry_ms: 1500`毫秒才补点，总计最多`event_choice_max_attempts: 3`次，包含首次。每次使用最新Detection的客户区锚点，不重复转换坐标。阶段从首次选项点击前开始计原`page_timeout_ms: 15000`期限，重试和重连不重新计时或补回次数；网络恢复只清空稳定证据，未命中、未知画面、位置不稳定或不完整升级弹窗均不授权补点。后续页面确认沿用原三帧规则，文本三角仍为可靠单帧。选项重试不扩展到招募/离队确认、升阶提交、战斗开始或领奖按钮；卡片关闭继续使用自身能力。新增事件及后续选项必须声明其后续页面并接入该入口，不能直接`tap`选项后放弃确认。

事件选项索引由`features/chaos/event_recognition.py`从正式模板生成，运行配置在chaos.yaml的event_recognition。新增/替换事件时必须同步EVENT_IDENTITIES、REQUIRED_SCENES、HANDLERS和对应索引条目：layout为2或3；origin是模板在基准画面中的参考对齐位置；titles是各选项标题在模板内的矩形；weights是标题排序权重。元数据只辅助排序/局部起始搜索，实际点击仍取完整模板匹配的动态位置；局部首轮未命中可在原场景ROI重新定位。不得将历史来源记录作为运行输入，不改PNG来迁就索引。配置检查要求每个入口事件恰好一份索引，标题必须落在模板及其布局槽位内；构建索引时拒绝正权重标题的空前景；禁用标题允许权重0且不参与排序，每份索引必须至少有一个正权重标题。完整模板匹配和三帧复核仍覆盖禁用状态。首次选项的不同状态分别登记全局入口与索引，可以复用同一处理器；后续阶段选项继续只在处理器内识别。

二/三选项各使用一个可复用线程，索引排序后优先局部验证前3个候选，后续候选直接粗定位再精匹配，避免未命中画面重复做两套搜索。首个完整匹配命中后停止其余候选，等待正在执行的工作收尾；后两帧只在候选位置周围复核，失败清空候选并打断稳定帧。此调度由基准和非基准共用，不合并两组排名或改为两候选分批；只在像素验证处按完整客户区变换分流。基准发现帧在主线程只生成一次只读半尺寸粗定位图，供两组共用，缓存复核帧不生成且不跨帧保存。入口begin/end限定缓存生命周期，文本、输入、恢复均使候选失效。运行只在既有chaos_event_dispatched日志附加帧数、候选验证数、缓存复核数及识别总耗时，不增加逐模板或逐帧日志。新增模板继续走既有图审与登记流程，同时制作/更新索引元数据并验证该事件在索引中的排序和完整识别结果。

FrameView的region/crop共用严格默认契约：要求完整区域在当前视图内，越界报错；只有动态局部搜索、合法配置ROI与子视图求交和候选透明边距处理显式传clip=True。固定区域、数字、滚动与卡片内容保持完整读取。求交为空时匹配拒绝，子视图保持完整客户区变换与全局原点。阶段金框共用“搜索去重→包含目标文字→唯一结果”顺序，不把包含判断提前到候选搜索中。

当前及后续chaos事件的领奖、生命变化卡片统一调用`EventContext.close_reward_cards()`，双卡片使用`count=2`。事件升阶结果的`rank_close`由`ChaosFlow._rank_up`接入同一能力；营地升阶与战斗升阶的“继续探索”保持各自原流程。禁止直接发送关闭点击后将`reward_stage`标为完成。

公共实现在`features/chaos/events/rewards.py`：关闭提示及卡片下部静态信息至少保持配置的稳定帧数和600毫秒稳定窗口后才点击。第二页必须与上一页卡片不同，并重新通过稳定窗口。点击后继续检查下一张卡片，或弹窗消失且出现文本/原楼层地图；只在同一张卡片和关闭提示仍稳定可见时，间隔至少1500毫秒重试，每页最多3次，每页总等待15秒。未知额外卡片不授权点击，超限停止，页码、尝试次数、阶段及失败详情统一进入`run_stopped.detail`。配置值位于chaos.yaml的timing及geometry，像素稳定阈值为平均差不超过3，换卡阈值仍为20；两种判断都使用event_card_change=[1000,920,330,130]的下部静态信息区，排除动态立绘。该范围覆盖生命卡名称/属性栏，生命卡切到奖励卡时仍须达到换卡差异阈值。参数仍需后续实测反馈校准。实际点击沿用`input`日志，仅额外保留`chaos_event_close_retry`及整段关闭完成时的一条`chaos_event_close_confirmed`摘要（页数、总尝试次数、返回目标）；不逐次记录attempt、逐页记录confirmed或单独记录failed。网络恢复重建稳定证据，不自动重放事件选择、领奖或升阶提交；选项的有限重试由独立的当前阶段新帧证据授权。

## 校准工具输入

模板提取命令必须提供原始图片。文件来源、尺寸及裁剪依据见 `docs/calibration/<主题>/*manifest.yaml` 和 `*manifest.json`；历史临时路径只是来源记录，不能假定在其他机器上存在。准备相同来源的文件，再传入实际位置。

| 模块（前缀 `scripts.calibration.`） | 必需参数 |
| --- | --- |
| `crop_calibration_templates` | `--source-dir` |
| `extract_main_shop_icon_template` | `--source`（完整主界面截图）或 `--source-dir`（旧版局部素材） |
| `extract_shop_refresh_button_template` | `--source-dir` |
| `extract_shop_exit_icon_template` | `--source-dir` |
| `extract_insufficient_funds_template` | `--source-dir` |
| `extract_refresh_confirm_templates` | `--source` |
| `extract_sky_stone_zero_wide_template` | `--source` |
| `extract_sky_stone_templates` | `--source`、`--context-source`、`--balance-3924-source`、`--balance-3900-source`、`--combined-top-bar-source` |
| `calibrate_client_frames` | `--main-source`、`--shop-top-source`、`--shop-bottom-source`、`--refresh-confirm-source`、`--purchase-confirm-source` |
| `crop_network_templates` | `--source` |
| `make_network_text_templates` | `--template-dir`；读取两张网络裁剪图，并通过可选 `--output-dir` 导出新候选 |

`--source-dir` 工具仍按历史文件名中的时间标记选择来源；保留文件名标记及对应内容。全窗口校准仍要求五个角色对应原有尺寸、裁剪与校验条件，不适用于任意截图。

查看参数不会启动校准：

```powershell
.\.venv\Scripts\python.exe -m scripts.calibration.extract_sky_stone_templates --help
.\.venv\Scripts\python.exe -m scripts.calibration.calibrate_client_frames --help
```

模板提取默认生成独立的 `artifacts/template-candidates/<时间>-<编号>` 目录，PNG按功能层级输出并附带校验文件；来源记录跟随候选目录。可以通过 `--output-dir`、`--manifest-dir` 自定义候选位置，禁止直接写正式资源。候选验收后通过统一的 `scripts.templates.register prepare/apply` 登记；用法及失败恢复见 [模板登记流程](TEMPLATE_WORKFLOW.md)。`calibrate_client_frames` 使用 `--output` 指定清单输出文件。导入工具模块不会执行网络模板裁剪或转换。运行时捕获图片仍仅存于内存。

实机验证步骤与授权条件见 [校准指南](CALIBRATION.md) 和 [后台验证](../validation/platform/BACKGROUND_VALIDATION.md)。

涉及模板、识别配置和功能加载的开发，先遵循项目根目录 [AGENTS.md](../../AGENTS.md)。新增模板与修改已有模板均需候选验证及 `prepare → apply` 登记。更新完成后运行 `.\.venv\Scripts\python.exe -m scripts.templates.check_changes`；获准提交后，将正式PNG、运行清单和登记来源记录一起暂存，再运行同命令的 `--staged` 模式检查实际暂存内容。保留 `.gitattributes` 中登记来源的字节及CRLF识别规则，避免换行转换破坏哈希或把合法CRLF误报为行尾空白。本地检查不自动提交，也不配置CI或安装hook。

当前主界面入口模板由深色背景完整截图通过 `--source` 重建；步骤包含客户区定位、仅问号/圆点的原始像素提取、浅蓝笔画完整保留及一像素暗边，不保留标题文字或重复羽化。历史 `--source-dir` 模式仅重建旧版局部素材。九张商品模板的遮罩参数记录在 `docs/calibration/shop/manifest.yaml`；已购买按钮和小区域回归素材来源记录在 `purchased_button_manifest.json`。模板自身匹配成功不能替代跨背景和实机验证，当前证据见 [商店识别改动验证记录](../validation/shop/SHOP_RECOGNITION_VALIDATION.md)。

## 测试

按变更选择直接相关模块。例如修改悬浮窗后运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ui/test_overlay.py -q
```

自动化回归位于 `tests/automation/`，脚本契约位于 `tests/scripts/`。仅在变更涉及相应模块时运行；不要用大范围测试替代明确的影响分析。

审核通过后的模板与事件接入遵循[模板工作流的验证复用规则](TEMPLATE_WORKFLOW.md)：先核对已有证据，未变的图片/识别配置直接沿用结果；新增事件只检查新增绑定、点击方向和分支返回。新增分派条目不构成重跑全部旧事件的理由。换对话时先查记录，不把上下文缺失当作重新全量验证的理由；仅文档改动不运行图像或业务测试。

UI用例由 `tests/ui/conftest.py` 保持一个QApplication，并在每个用例后关闭、停止计时器及延迟销毁本用例创建的窗口，再进行Python回收。`close()`只表示关闭/隐藏，不作为对象已释放的证据；新UI测试应遵循该生命周期约定。Qt专用辅助代码放在 `tests/helpers/qt.py`，不从通用helpers入口导出，以免非UI测试提前加载Qt。

窗口拥有的控件不得通过Python强引用反向拥有主窗口；需要回访时使用弱引用，并检查Python包装对象对应的C++对象仍有效。生命周期回归同时检查正常窗口操作、销毁后的事件和实际对象释放。原生压力用例在带超时的独立进程执行，检查退出码；默认循环垃圾回收保持开启，不以禁用GC或拆分普通用例绕过失败。

Qt与WGC测试现在可以在同一Python进程运行。WGC入口先通过 `platform/native_runtime.py` 选择兼容的MSVCP140运行库，再导入PyWinRT，避免其旧私有DLL先被加载。不要绕过该入口提前导入WinRT；若进程已加载不兼容旧DLL，明确报错并重启，不尝试卸载正在使用的运行库。生产COM初始化、截图资源及释放仍由工作线程管理。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ui tests/platform/test_wgc_capture.py tests/platform/test_native_runtime.py -q
```

`scripts/test-source.ps1` 已恢复单次pytest调用，不再排除WGC后另起进程；仍只有用户明确要求全量测试时才运行。两种导入顺序的原生回归在受控子进程检查正常退出，不能用拆分普通测试替代同进程验收。共存测试使用真实WinRT内存位图和Qt工作线程，不捕获游戏或桌面、不发送游戏输入。

## 构建与交付

准备发布的一批修改按下面顺序执行；普通开发可以随时提交，不要求每次commit都打包：

1. 完成修改及直接相关测试，集中同步版本号、CHANGELOG、README源码版本和随包使用说明。
2. 执行构建与新产物验收。打包输入此时定稿；验收后把实际结论及限制一次写入版本记录。
3. 用户授权后集中commit/push。只修改未打包的验证记录不需要重建；源码、配置、资源、随包说明或构建工具变化需要重建并重新验收。
4. 通过最终提交一致性检查，确认该提交已推送；获发布授权后创建标签、上传已验证安装EXE。
5. 核对远端标签和附件，结果保存到本地产物。发布成功后不再为了更新“已发布”状态修改仓库文档。

```powershell
.\.venv\Scripts\python.exe -B -m scripts.release.workflow preflight
powershell -ExecutionPolicy Bypass -File scripts\release\build-standalone.ps1
# 替换为构建脚本打印的实际目录：
$releaseRun = '<构建脚本打印的Release record directory>'
# 管理员PowerShell中执行编译版验收：
.\.venv\Scripts\python.exe -B -m scripts.release.verify_installer --run-dir $releaseRun
# 补齐版本记录并完成获授权的commit/push后：
.\.venv\Scripts\python.exe -B -m scripts.release.workflow check --run-dir $releaseRun --revision HEAD
```

构建入口自动检查版本/文档与环境，编译前capture记录输入，prepare冻结Nuitka目录和安装文件清单，ISCC编译后seal检查输入/payload未变并封存安装EXE摘要，待验收包留在本次staging/setup；安装验收逐文件核对实际安装内容，全部通过并卸载测试副本后再提升到dist。dist只保留最新成功验收的标准安装EXE，原子替换同名包并清理直属旧版标准安装EXE；构建或验收失败保留原可用包。最后的check还要求最终Git提交包含同一组输入，并存在绑定该安装EXE的成功编译版及安装/升级/卸载验收记录。可以先构建未提交修改，再集中提交；Git行尾转换按实际过滤规则核对，图片字节保持精确匹配。新增或删除输入文件也会使旧包失效。

输入范围为src/e7auto、assets/templates、assets/ui、scripts/release、scripts/common，以及launcher.py、pyproject.toml、requirements.lock、config/internal.yaml、config/chaos.yaml、随包使用说明和存在时的.gitattributes；不包含未打包的验证记录或tools中的安装、缓存及私有状态。后续构建入口增加新的输入目录时，须同步workflow的输入范围并执行实际受影响的验证；新增依赖不强制专门新增边界测试。检查不自动提交、推送或创建Release，也不替代编译版自检与实机验证；直接绕过工具不在保证范围内。已有v1.3.3包和标签保持原样，缺少新快照的历史构建不补造记录。

流程边界及当前验收范围见[发布流程验证](../validation/common/RELEASE_WORKFLOW_VALIDATION.md)。

构建继续使用根目录 `launcher.py`、现有配置和资源布局。源码测试与构建脚本契约通过，不等于已重新构建或验证新的独立程序。修改源码后需要另行执行构建和 [发布检查](RELEASE_CHECKLIST.md)，才能对新产物作出结论。

构建成功后、发布前，`scripts.release.native_runtime` 将build/nuitka内的根目录和嵌套MSVC运行库副本统一为锁定Qt/Shiboken附带的兼容版本；不修改.venv、系统DLL或既有dist。发布验证检查缺失及旧版本副本。更新Qt或WinRT依赖时，必须复核MINIMUM_MSVC_VERSION、随包DLL及两种导入顺序，不能只更新pip版本号。

运行库选择的最低版本当前为 `14.44.35211.0`。若进程已加载达到该要求的MSVCP140，则直接复用；否则源码从Shiboken安装目录、编译程序从可执行文件目录选择随包运行库。构建工具校验7个MSVC DLL，先检查输出路径，再统一副本，成功后才替换旧发行目录。项目内写入仅允许 `scripts.common.paths.BUILD_NUITKA_DIR` 下的构建产物；明确指定的项目外临时导出目录也受支持。工具拒绝链接目标越界，不能用于修补现有dist。

## 更新日志与发布记录

卡厄思首次正式发布前的修复、优化、尺寸适配和流程完善均属于首版开发，CHANGELOG统一归入一条“新增自动卡厄思”的最终功能介绍，不单列该功能的修复/改进。首次正式发布后的后续修复再逐项记录；本机构建或实机验证不等于正式发布。混合条目拆出已有商店、企鹅或公共能力的实际变化，技术过程和验证历史保留在对应开发/验证文档，不重写已发布版本的历史内容。

`CHANGELOG.md` 保留版本号和日期，按实际内容使用新增、修复、改进、移除等分类；不添加发布准备、本机构建、发布等过程栏目。README保留项目介绍、源码版本及固定下载入口；随包使用说明只写对应版本的使用方式，不跟踪构建/上传状态。RELEASE_CHECKLIST只保存通用流程，不逐版本追加执行日志。

`docs/releases/<版本>/` 在最终提交前一次记录版本范围、已完成的验证结论和未覆盖场景，不预写发布成功，也不在上传后补提交状态。编译日志、阶段进度、包摘要、上传结果和远端核对保存在 `artifacts/releases/<版本>/<构建ID>/`；实际公开发布状态以GitHub Release为准。历史记录保留原事实，不为符合新流程删除证据。

整理历史条目时，删除重复的版本和打包说明；栏目中包含的真实行为变化应迁回该版本的对应分类，已有验证证据保留在原记录中。GitHub Release页面的下载文件栏目与CHANGELOG职责不同，继续遵循[发布检查单](RELEASE_CHECKLIST.md)。

## 新增功能与模板

所有后续功能必须遵循[模板资源组织与新增功能约定](TEMPLATES.md)：功能专用模板放入 `assets/templates/<feature>/`，实际复用的模板放入 `common/<用途>/`，启动按功能加载，历史来源记录放入 `docs/calibration/`。新增或迁移模板时同步配置、提取工具、离线回归及发布验证。

## 新文件及生成位置

新增功能、bug修复、文档、测试及实验输出必须遵循[目录与产物约定](LAYOUT.md)。任务通过 `scripts.project.artifacts` 创建，复用已有任务的inputs/previews/results/scratch；可复用工具进入scripts。提交前目录检查读取暂存区，不以工作区替代。

实机验证器的默认结果输出为新建管理任务的results；将控制台输出的目录保存为本次证据索引。`promote_insufficient_funds_live_result` 需要显式传入 `--source`，不再默认读取logs中的固定文件。模板候选仍由登记工具使用专用目录。

## 自动卡厄思新增事件

每个事件使用独立模块，不按流程复杂度合并事件文件。先导出并审核候选（图片预览＋白色遮罩），获批后逐项正式登记；随后在运行YAML及配置场景契约加入映射，在 `events/dispatcher.py` 显式登记处理器。不得动态扫描文件自动加载。

`entry.py` 只负责选项出现前的通用文本；`dispatcher.py` 只识别唯一事件和调用处理器，不点击选项或决定奖励处理。处理器自身选择选项并组织完整流程，通过 `EventContext` 调用截图、稳定等待、点击、战斗等待和文本推进能力，最终确认原楼层地图。不要导入或持有整个ChaosFlow实现，不复制手动停止、网络或坐标缩放逻辑。完成新事件记录时必须明确列出从选项到返回地图的完整步骤。

多阶段事件只将首次身份加入全局HANDLERS。二次及后续选项使用同一事件文件的内部阶段等待，明确要求相应条件证据；不得重新调用全局分派器或在未提供的分支上猜测选法。


火把、祈祷室、神秘脚印、破碎烛台处理器在选择经验选项后，显式调用events/experience.py的finish_with_optional_levelup。模块统一维护升级/文本/地图分支，每次调用独立保存关闭状态，复用EventContext.wait/tap；升级标题与关闭提示稳定出现才关闭一次，任一仍可见都阻止后方三角/地图操作。已发送标记跨网络恢复保留；持续不消失按原等待期限停止，不重发关闭，原事件等待日志名称不变。公共finish_text_and_map仍只处理文本和原楼层地图，不包含升级参数或状态。用户已验证事件战斗不增加经验，因此事件战斗不接入此模块；后续事件仅在当前所选路线会获得经验时显式接入。金色纹样中选项确认结果文字后对state.ranks现有全部条目更新一次，min(5, rank+1)，每个实际变化沿用chaos_rank_changed日志；不创建缺失英雄、不调用普通rank_up。


多阶段事件的奖励关闭可传入close_reward_cards(next_page=页面检测回调)。公共关闭方法只负责关闭提示消失、稳定后续页面及既有限次重试；具体页面识别由事件处理器提供。设置next_page后不以默认文本/地图替代指定结果。武器库第一张奖励卡用该接口确认第二组选项，第二场战斗后的奖励沿用默认文本/原楼层地图确认；两场战斗各调用一次单卡关闭，不使用count=2。神秘脚印由自身处理器显式调用经验收尾模块，公共文本收尾不新增事件专属开关。


升阶结果通过ChaosVision.rank_result读取同帧左右阶数，每侧必须恰好一个匹配且右侧大于左侧；模板rank_value_1至rank_value_5均包含阶数图标和数字，5阶按2026-10-13用户授权移除右侧(Max)，不识别箭头。5阶分部布局为primary=[0,0,61,60]、secondary=[61,0,39,60]，保留原0.96/0.8门槛；基准与非基准按已登记布局共用分部匹配（2026-10-17用户明确授权）。左右范围在YAML的rank_result_before/after，必须互不重叠、按左右排列并位于原rank_result范围内。公共_rank_up的steps默认1；魔导书传入2，按用户确认规则封顶5，连续稳定确认结果与原记录及预期一致后才更新一次。战斗奖励特殊卡片继续使用event_battle_rank_done，只允许steps=1；金色纹样直接全体加阶机制不变。旧rank_12/23/34/45已移出运行配置和必需资源，并按用户要求通过retire事务删除正式PNG及活动清单条目；历史来源和退役记录仅供追溯。


离队事件通过阵容显式departure_hero选择目标；EventContext.depart封装离队页面、选中身份与按钮复核和一次提交，Missing稳定确认后才更新RoundState.ranks/departed_heroes。卡片关闭继续使用close_reward_cards(count=2)。second_floor_voucher_done记录本轮第二层招募机会是否已处理；平民队restore_shadow_rose策略仅补招已离队且尚未回队的暗影萝季，完成后阶数重置为1。暗双子队仅在雅洛薇仍在队时安排律荷替换。新增状态随每轮RoundState重建，不从资源加载与否推导业务策略。


## 安装版交付

1.4.0起使用Nuitka 4.1.3加Inno Setup 6.7.3，最终只分发Setup EXE。独立目录保留在build，日志/状态仍在用户选择的安装根目录；升级保留，卸载清理。目录选择、快捷方式、通知身份、文件清单和实际安装验收的完整规则见[安装包开发说明](INSTALLER.md)。
