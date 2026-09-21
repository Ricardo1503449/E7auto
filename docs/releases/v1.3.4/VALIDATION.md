# v1.3.4 验证记录

## 版本范围

本版本以 `6fc5b5b`（新增连续刷新开关）为功能基准，仅同步版本号、更新日志、使用说明和本版本交付文档。商店设置页新增默认关闭的“连续刷新”开关，开启后跳过转运等待与连续 49 次未出货停止，保留预算限制、未出货统计及原有停止条件。

工作区另有 chaos 校准任务的目录登记、脚本、测试、文档及 `pip.ini`；它们不属于本次提交或打包输入，保持原样。本次未修改正式模板、识别参数或运行配置。

## 源码验证

- 功能提交前，两批直接相关测试分别为 56 项和 46 项通过（包含复验），覆盖界面、参数传递、连续刷新 60 次、原有四阶段策略、预算边界、F5、网络恢复、计数显示和源码边界。
- 用户在提交功能前已反馈完成源码实机验证；本次不重复该功能测试，也未将此反馈视为新版独立程序的实机验收。
- 2026-09-21 运行版本同步、模板仓库、模板资源与目录、UI 资源相关测试：50 项通过（8.93 秒），未运行全套测试。
- 项目、源码和本地 editable 安装版本均为 `1.3.4`，锁定依赖没有缺失或版本不匹配；工作区布局检查通过。

版本与资源测试证据位于本地 `artifacts/releases/v1.3.4/20260921-115354-version-and-publication-f7388353/`，包括 `release-tests.log`、`editable-install.log` 和布局检查日志。

## 本机构建与验收

- 使用现有 Nuitka 独立程序构建入口生成 `dist/launcher.dist/E7auto.exe` 和 `dist/E7auto_v1.3.4_x64.zip`，Windows 文件及产品版本为 `1.3.4.0`。
- ZIP 大小为 83,420,705 字节，包含 156 个文件；构建流程 capture/seal 检查通过，ZIP 与 dist 逐文件一致，随包配置、模板、UI 资源和使用说明与捕获的构建输入一致。
- 根目录和 Shiboken 下共 11 个 MSVC DLL 均为 `14.44.35211.0`；EXE 的 PE 清单要求 `requireAdministrator`。
- 管理员发布验收使用本次构建的 `--run-dir`，退出码为 0、`problems=[]`。编译版自检报告 `compiled=true`、`version=1.3.4`、`machine=AMD64`、`wgc_importable=true`、`wgc_import_error` 为空、`venv_bundled=false`，配置、模板和 UI 资源均存在。
- 编译版验收结果已绑定构建记录和 ZIP；最终提交后使用 `release.workflow check` 核对提交、构建输入及已验收 ZIP，发布与远端核对结果保留在本地产物和 GitHub。

构建证据位于本地 `artifacts/releases/v1.3.4/20260921-115541-standalone-bc8172e5/`：`build.log`、`build-inputs.json`、`build-artifact.json`、`build-result.json`、`archive-verification.json`、`release-verification.json`、`release-verification.txt` 和 `release-verification.exit`。本地摘要保存在这些记录中。

## 未验证范围

本轮没有操作游戏、运行新版独立程序的游戏流程，或在未安装 Python 的纯净 Windows 机器上验证。管理员自检和资源验收不能代替这些场景；此前用户确认的实机验证仅对应功能源码。
