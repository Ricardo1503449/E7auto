# 开发指南

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `src/e7auto/app.py` | 启动、环境检查及资源根目录解析 |
| `src/e7auto/ui/` | 主窗口、悬浮窗、功能页面、控件与 Qt 工作线程 |
| `src/e7auto/automation/` | 自动化流程、会话、停止控制、快照发布与滚动校验 |
| `src/e7auto/ports.py` | 可替换服务接口，包括 `GameVision` |
| `src/e7auto/vision_types.py` | 不依赖具体 OpenCV 实现的识别结果类型 |
| `src/e7auto/vision.py` | 模板匹配、天空石数字识别及图像位移测量 |
| `scripts/calibration/` | 模板提取、校准、校准证据归档；悬浮窗位置校准需要实机 |
| `scripts/validation/` | 实机验证器及管理员启动脚本 |
| `scripts/release/` | Nuitka 构建与发布产物校验 |
| `scripts/common/` | 工具共用的项目路径和离线图片读写 |
| `tests/ui/`、`tests/automation/`、`tests/scripts/` | 对应职责的回归测试 |
| `tests/helpers/`、`tests/fixtures/` | 配置工厂、替代服务和固定测试图片 |
| `config/`、`assets/` | 当前运行配置、模板与 UI 资源 |

`ui/__init__.py` 和 `automation/__init__.py` 保留主要公开导入入口。内部实现直接依赖具体模块，避免从包入口反向导入。测试替换依赖时，应替换使用该依赖的模块，例如 `e7auto.ui.main_window.QThread`。

`AutomationSession` 管理单轮生命周期；`AutomationEngine` 编排业务；`StopController` 统一协调停止请求与输入派发。`scrolling.py` 通过 `ScrollServices` 接收捕获、受控滚动、检查点、计时、识别和日志回调，不持有引擎对象。所有输入仍经过引擎的窗口检查和同一把停止锁。

`ui/worker.py` 负责组装生产服务。WGC/PyWinRT 只在工作线程的 `run()` 内导入；不能为方便导出而将它们移到 UI 包初始化阶段。

## 环境与运行

在项目根目录执行命令，使用项目 `.venv` 中的 Python 3.12。安装步骤见 [README](../README.md)。

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

`artifacts/` 和 `pip.ini` 是现有本地文件，整理代码时不自动删除或提交。`logs/`、`state/`、`build/`、`dist/` 以及环境和工具缓存继续采用现有忽略规则。

## 校准工具输入

模板提取命令必须提供原始图片。文件来源、尺寸及裁剪依据见 `assets/templates/*manifest.yaml`；历史临时路径只是来源记录，不能假定在其他机器上存在。准备相同来源的文件，再传入实际位置。

| 模块（前缀 `scripts.calibration.`） | 必需参数 |
| --- | --- |
| `crop_calibration_templates` | `--source-dir` |
| `extract_main_shop_icon_template` | `--source-dir` |
| `extract_shop_refresh_button_template` | `--source-dir` |
| `extract_shop_exit_icon_template` | `--source-dir` |
| `extract_insufficient_funds_template` | `--source-dir` |
| `extract_refresh_confirm_templates` | `--source` |
| `extract_sky_stone_zero_wide_template` | `--source` |
| `extract_sky_stone_templates` | `--source`、`--context-source`、`--balance-3924-source`、`--balance-3900-source`、`--combined-top-bar-source` |
| `calibrate_client_frames` | `--main-source`、`--shop-top-source`、`--shop-bottom-source`、`--refresh-confirm-source`、`--purchase-confirm-source` |
| `crop_network_templates` | `--source` |
| `make_network_text_templates` | `--template-dir`；原地转换该目录中的两张网络提示裁剪图 |

`--source-dir` 工具仍按历史文件名中的时间标记选择来源；保留文件名标记及对应内容。全窗口校准仍要求五个角色对应原有尺寸、裁剪与校验条件，不适用于任意截图。

查看参数不会启动校准：

```powershell
.\.venv\Scripts\python.exe -m scripts.calibration.extract_sky_stone_templates --help
.\.venv\Scripts\python.exe -m scripts.calibration.calibrate_client_frames --help
```

提取工具的 `--output-dir` 默认是 `assets/templates`；需要试验时显式传入另一个目录。`calibrate_client_frames` 使用 `--output` 指定清单输出文件。导入工具模块不会执行网络模板裁剪或转换。运行时捕获图片仍仅存于内存。

实机验证步骤与授权条件见 [校准指南](CALIBRATION.md) 和 [后台验证](BACKGROUND_VALIDATION.md)。

## 测试

按变更选择直接相关模块。例如修改悬浮窗后运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ui/test_overlay.py -q
```

自动化回归位于 `tests/automation/`，脚本契约位于 `tests/scripts/`。仅在变更涉及相应模块时运行；不要用大范围测试替代明确的影响分析。

WGC 测试必须单独启动 Python 进程，不能和 Qt UI 测试放在同一次 pytest 调用中：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_wgc_capture.py -q
```

`scripts/test-source.ps1` 保留原有全套分进程测试入口，只有明确需要全量测试时使用。离线测试使用替代服务和固定输入，不启动游戏或发送真实输入。

## 构建与交付

```powershell
powershell -ExecutionPolicy Bypass -File scripts\release\build-standalone.ps1
.\.venv\Scripts\python.exe -m scripts.release.verify_release
```

构建继续使用根目录 `launcher.py`、现有配置和资源布局。源码测试与构建脚本契约通过，不等于已重新构建或验证新的独立程序。修改源码后需要另行执行构建和 [发布检查](RELEASE_CHECKLIST.md)，才能对新产物作出结论。
