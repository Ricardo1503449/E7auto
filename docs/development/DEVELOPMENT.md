# 开发指南

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
| `src/e7auto/vision/` | 共用模板匹配、数字字形、帧适配和网络识别 |
| `src/e7auto/resources/` | 模板清单完整性校验、图片读取 |
| `src/e7auto/configuration/` | 配置数据模型、YAML结构规则与加载；文件格式和参数保持原约定 |
| `src/e7auto/platform/`、`src/e7auto/logging/` | Windows窗口/消息输入/WGC，以及运行日志/异常截图 |
| `scripts/calibration/` | 模板提取、校准、校准证据归档；悬浮窗位置校准需要实机 |
| `scripts/validation/` | 实机验证器及管理员启动脚本 |
| `scripts/release/` | Nuitka 构建与发布产物校验 |
| `scripts/common/` | 工具共用的项目路径和离线图片读写 |
| `tests/ui/`、`tests/automation/`、`tests/scripts/` | 对应职责的回归测试 |
| `tests/helpers/`、`tests/fixtures/` | 配置工厂、替代服务和固定测试图片 |
| `config/`、`assets/` | 当前运行配置、模板与 UI 资源 |

UI入口仍在 `ui/__init__.py`；源码调用使用明确模块。旧 `automation/`、根部 `config.py`、`vision.py` 等入口已迁移，项目内脚本和测试已同步，不保留无调用方的转发实现。机器规则中的legacy_modules是禁止新代码引用的旧入口名单。测试替换依赖时，替换实际使用者，例如 `e7auto.ui.main_window.QThread`、`e7auto.bootstrap.TemplateRepository`。

`bootstrap.AutomationSession` 保留商店/企鹅启动请求，`runtime.session.RuntimeSession` 接收流程工厂和停止策略，管理单轮生命周期。`ShopFlow` 与 `PenguinFlow` 平级组合 `RuntimeContext`，互不导入或继承。网络恢复只更新共享恢复代次、计时和状态，通过回调使商店失效自己的缓存；企鹅保留恢复后重新截图策略。`StopController` 仍用同一把锁协调停止与输入。商店 `scrolling.py` 通过窄回调使用受控输入、截图、计时与识别，不持有流程对象。

`ui/worker.py` 负责Qt线程、信号及启动失败上报，在 `run()` 中调用 `bootstrap.create_production_session`。WGC/PyWinRT只在该工厂被工作线程调用后导入，或在独立的 `app.validate_wgc_import` 自检入口导入；UI及包导入不得提前加载。功能流程、识别算法和共享运行层不得直接调用Windows实现。

## 源码依赖与后续扩展

- 新功能先登记layout.json的功能名，在 `features/<feature>/` 放流程、识别及契约；在bootstrap显式接入工厂、配置/资源选择、完成策略，再接入UI和发布校验。登记目录名不会自动启用功能。
- 功能间禁止导入和继承；共享层禁止反向导入功能实现或UI。跨功能复用先提取语义确实一致的公共组件，专用滚动/金额规则继续留在所属功能。
- 新bug修改所属模块；不建立根部helper、misc或fix副本，不把修复脚本塞入运行包。公共fixture仍留在tests/helpers。
- `scripts.project.check_layout` 同时执行源码位置和依赖检查。它解析绝对/相对导入、函数内部导入和字面量动态导入；工作区与staged使用各自的源码、规则视图。动态拼接模块名不在静态检查保证内，不应使用它绕开依赖规则。
- 边界测试位于 `tests/core/test_source_boundaries.py`，包括跨功能导入、底层反向依赖、旧入口引用、Win32越界和WGC提前导入；索引与工作区差异测试仍在tests/scripts/project。不会安装hook或配置CI。
- 配置YAML和RuntimeSnapshot继续维持兼容的聚合格式；此次拆分源码职责，不改变运行配置格式、UI字段、识别阈值或模板字节。

## 环境与运行

在项目根目录执行命令，使用项目 `.venv` 中的 Python 3.12。安装步骤见 [README](../../README.md)。

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

`artifacts/` 和 `pip.ini` 是现有本地文件，整理代码时不自动删除或提交。运行日志仍在 `logs/`、用户状态仍在 `state/`；开发任务产物和构建输出遵循[目录与产物约定](LAYOUT.md)。

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

当前主界面入口模板由深色背景完整截图通过 `--source` 重建；步骤包含客户区定位、前景筛选、问号笔画孔洞恢复和仅向内的透明边缘平滑。历史 `--source-dir` 模式仅重建旧版局部素材。九张商品模板的遮罩参数记录在 `docs/calibration/shop/manifest.yaml`；已购买按钮和小区域回归素材来源记录在 `purchased_button_manifest.json`。模板自身匹配成功不能替代跨背景和实机验证，当前证据见 [商店识别改动验证记录](../validation/shop/SHOP_RECOGNITION_VALIDATION.md)。

## 测试

按变更选择直接相关模块。例如修改悬浮窗后运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ui/test_overlay.py -q
```

自动化回归位于 `tests/automation/`，脚本契约位于 `tests/scripts/`。仅在变更涉及相应模块时运行；不要用大范围测试替代明确的影响分析。

UI用例由 `tests/ui/conftest.py` 保持一个QApplication，并在每个用例后关闭、停止计时器及延迟销毁本用例创建的窗口，再进行Python回收。`close()`只表示关闭/隐藏，不作为对象已释放的证据；新UI测试应遵循该生命周期约定。Qt专用辅助代码放在 `tests/helpers/qt.py`，不从通用helpers入口导出，以免非UI测试提前加载Qt。

窗口拥有的控件不得通过Python强引用反向拥有主窗口；需要回访时使用弱引用，并检查Python包装对象对应的C++对象仍有效。生命周期回归同时检查正常窗口操作、销毁后的事件和实际对象释放。原生压力用例在带超时的独立进程执行，检查退出码；默认循环垃圾回收保持开启，不以禁用GC或拆分普通用例绕过失败。

Qt与WGC测试现在可以在同一Python进程运行。WGC入口先通过 `platform/native_runtime.py` 选择兼容的MSVCP140运行库，再导入PyWinRT，避免其旧私有DLL先被加载。不要绕过该入口提前导入WinRT；若进程已加载不兼容旧DLL，明确报错并重启，不尝试卸载正在使用的运行库。生产COM初始化、截图资源及释放仍由工作线程管理。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ui tests/platform/test_wgc_capture.py tests/platform/test_native_runtime.py -q
```

`scripts/test-source.ps1` 已恢复单次pytest调用，不再排除WGC后另起进程；仍只有用户明确要求全量测试时才运行。两种导入顺序的原生回归在受控子进程检查正常退出，不能用拆分普通测试替代同进程验收。共存测试使用真实WinRT内存位图和Qt工作线程，不捕获游戏或桌面、不发送游戏输入。

## 构建与交付

```powershell
powershell -ExecutionPolicy Bypass -File scripts\release\build-standalone.ps1
.\.venv\Scripts\python.exe -m scripts.release.verify_release
```

构建继续使用根目录 `launcher.py`、现有配置和资源布局。源码测试与构建脚本契约通过，不等于已重新构建或验证新的独立程序。修改源码后需要另行执行构建和 [发布检查](RELEASE_CHECKLIST.md)，才能对新产物作出结论。

构建成功后、发布前，`scripts.release.native_runtime` 将build/nuitka内的根目录和嵌套MSVC运行库副本统一为锁定Qt/Shiboken附带的兼容版本；不修改.venv、系统DLL或既有dist。发布验证检查缺失及旧版本副本。更新Qt或WinRT依赖时，必须复核MINIMUM_MSVC_VERSION、随包DLL及两种导入顺序，不能只更新pip版本号。

运行库选择的最低版本当前为 `14.44.35211.0`。若进程已加载达到该要求的MSVCP140，则直接复用；否则源码从Shiboken安装目录、编译程序从可执行文件目录选择随包运行库。构建工具校验7个MSVC DLL，先检查输出路径，再统一副本，成功后才替换旧发行目录。项目内写入仅允许 `scripts.common.paths.BUILD_NUITKA_DIR` 下的构建产物；明确指定的项目外临时导出目录也受支持。工具拒绝链接目标越界，不能用于修补现有dist。

## 新增功能与模板

所有后续功能必须遵循[模板资源组织与新增功能约定](TEMPLATES.md)：功能专用模板放入 `assets/templates/<feature>/`，实际复用的模板放入 `common/<用途>/`，启动按功能加载，历史来源记录放入 `docs/calibration/`。新增或迁移模板时同步配置、提取工具、离线回归及发布验证。

## 新文件及生成位置

新增功能、bug修复、文档、测试及实验输出必须遵循[目录与产物约定](LAYOUT.md)。任务通过 `scripts.project.artifacts` 创建，复用已有任务的inputs/previews/results/scratch；可复用工具进入scripts。提交前目录检查读取暂存区，不以工作区替代。

实机验证器的默认结果输出为新建管理任务的results；将控制台输出的目录保存为本次证据索引。`promote_insufficient_funds_live_result` 需要显式传入 `--source`，不再默认读取logs中的固定文件。模板候选仍由登记工具使用专用目录。
