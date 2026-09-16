# v1.3.2 本机构建验证记录

本文件分别记录两次同版本构建；下方2026-09-15记录为历史数据，当前本机产物以2026-09-16重建结果为准。原始日志和构建包保留在本机，不随本文提交。

## 2026-09-16 同版本重建（当前产物）

用户要求保持版本号，基于提交 `1d0a198209741b04074964a356f5faf0c90e28f2` 重新构建本机dist和ZIP。版本仍为 `1.3.2`，Windows文件及产品版本仍为 `1.3.2.0`。该源码提交随后已推送到 `origin/main`；本次构建没有创建标签或GitHub Release。

### 天空石修复与流程约束

- 天空石图标保持62×75尺寸及全部原始RGB，只通过二值alpha遮罩保留宝石和加号；1,742个像素不透明，2,908个背景像素透明。
- 用户反馈截图中的图标匹配由0.921176135提升为0.971766915，超过保持不变的0.93阈值，余额正确读取为23,711。该结论来自离线截图验证，不扩写为新的游戏实测结果。
- 新增项目级资源/登记约束、本地工作区/暂存区/指定提交检查和来源字节保留规则；不配置CI或自动安装hook。
- 来源及补登记细节见[模板工作流验证](../../validation/templates/TEMPLATE_WORKFLOW_VALIDATION.md)和[正式来源记录](../../calibration/registrations/shop-sky_stone_icon-c78eaf04b03d-424d239c56ae.json)。

### 验证范围

- 环境检查通过，project/source/installed均为1.3.2，依赖无缺失或版本不匹配；构建前后源码和资源输入哈希一致。
- 沿用天空石相关14项、新检查及CLI入口14项、登记恢复26项的已有通过结果；提交准备中因CRLF规则调整，另复验直接相关暂存/提交用例1项通过。不累计重复用例，不重跑全套测试。
- 本轮实际执行环境、Nuitka构建、发布资源、ZIP完整性及管理员编译版自检。未进行游戏操作或纯净Windows验证。

### 构建结果

- `scripts/release/build-standalone.ps1` 退出0；Nuitka 4.1.3、Python 3.12、MSVC cl 14.3。
- 发布目录：`dist/launcher.dist`；EXE：`dist/launcher.dist/E7auto.exe`。
- ZIP：`dist/E7auto_v1.3.2_x64.zip`，83,424,277字节（约79.56 MiB）。
- ZIP内154个文件全部读取并通过CRC校验，逐文件SHA-256及文件集合与dist一致。
- 48个模板目录文件、11个UI资源与源码逐字节一致；配置和使用说明一致，透明天空石模板已包含在包中。
- 模板来源/完整性、PNG解码、必需与禁用文件检查通过；无未恢复登记事务，无运行日志、停止截图、虚拟环境或测试数据混入。
- AMD64、requireAdministrator及Windows版本1.3.2.0验证通过。
- 本地ZIP SHA-256：`39924fe09445d39c9bf8b52c71badab3cd01a1a2aef7a1df016521bb83eaac7f`。

### 编译版自检与原始输出

管理员模式发布校验退出0，`problems=[]`。新EXE报告 `compiled=true`、`version=1.3.2`、`machine=AMD64`、`wgc_importable=true`、`wgc_import_error=""`、`venv_bundled=false`，配置、模板和UI资源均存在。

本机证据目录：`artifacts/release-v1.3.2-1d0a198-20260916/`，包含 `source-snapshot.json`、`build.log`、`build.exit`、`archive-verification.json`、`release-verification.txt`、`release-verification.exit` 及验证脚本。此目录不是仓库或运行时依赖，其他机器应以本文记录和重新执行验证为准。

## 2026-09-15 首次构建（历史记录）

日期：2026-09-15。

用户反馈现有测试后改动基本无误，并授权同步 v1.3.2、重建本机 dist 与 ZIP。本轮不重复完整测试套件，不进行游戏操作、Git 提交、推送、标签或 GitHub 发布；用户反馈不扩写为未记录的逐项实机验证。

### 改动与已有证据

- 入口识别：[入口识别验证](../../validation/common/ENTRY_RECOGNITION_VALIDATION.md)。
- 单次启动唤醒：[启动唤醒验证](../../validation/common/STARTUP_WAKE_VALIDATION.md)。
- 模板组织与登记：[模板工作流验证](../../validation/templates/TEMPLATE_WORKFLOW_VALIDATION.md)。
- 本版还包含滚动重叠校验兜底、异常停止缓存帧诊断及日志数值格式整理，详见更新日志。

### 本轮验证

- 已同步 `pyproject.toml`、`src/e7auto/__init__.py`、README、使用说明与本机 editable 安装元数据至 1.3.2。
- `.venv/Scripts/python.exe -m pytest tests/scripts/release/test_version_sync.py tests/resources/test_template_assets.py tests/resources/test_template_layout.py tests/resources/test_template_repository.py tests/ui/test_assets.py -q`：50 passed in 5.14s。只运行版本、资源布局、模板读取及 UI 打包资源相关检查，未运行全量套件。
- `.venv/Scripts/python.exe -m scripts.verify_environment`：通过，project/source/installed 均为 1.3.2，锁定依赖无缺失或版本不匹配。
- `git diff --check`：通过。
- 原始输出：`artifacts/releases/v1.3.2/20260916-145543-legacy-release-v1-3-2-a560cb74/focused-tests.txt`、`environment.json`。

### 构建结果

- `scripts/release/build-standalone.ps1` 完成，退出码 0；Nuitka 4.1.3、Python 3.12、MSVC cl 14.3。227 个 C 文件中 212 个缓存命中、15 个重新编译。
- 发布目录：`dist/launcher.dist`；可执行文件：`dist/launcher.dist/E7auto.exe`。
- ZIP：`dist/E7auto_v1.3.2_x64.zip`，81,162,075 字节（约 77.40 MiB）。新 ZIP 完成后，构建脚本已清理 dist 中旧版 ZIP。
- Windows 文件版本和产品版本均为 `1.3.2.0`，PE 架构 AMD64，清单要求 `requireAdministrator`。
- 48 个模板目录文件、11 个 UI 资源文件与源码逐字节一致，运行配置和随包使用说明也一致；模板来源/完整性、PNG 解码、必需及禁用文件检查通过。
- ZIP 内 154 个文件与发布目录文件集合一致，全部读取完成 CRC 校验，逐文件 SHA-256 与 dist 一致。
- 发布包无日志、停止截图、测试、虚拟环境等运行/开发数据；正式模板无未恢复的登记事务。
- 本地 ZIP SHA-256：`9b73511e0baf02dbddb971ebc04c1978ece57fc33ff8695001bcd600c7b4fa02`。
- 原始输出：`artifacts/releases/v1.3.2/20260916-145543-legacy-release-v1-3-2-a560cb74/build.log`、`build.exit`、`archive-verification.json`；校验脚本为同目录 `verify_archive.py`。

### 编译版自检

首次沙箱内启动 RunAs 返回 `0xc0000142`，未执行自检。随后在沙箱外通过 Windows UAC 启动同一发布校验脚本，已完成并返回退出码 0。

- `.venv/Scripts/python.exe -s -B -m scripts.release.verify_release`：`problems=[]`。
- 编译版 `E7auto.exe --self-check`：`compiled=true`、`version=1.3.2`、`machine=AMD64`、`wgc_importable=true`、`wgc_import_error=""`。
- 配置、模板及 UI 资源存在，`venv_bundled=false`。
- 输出：`artifacts/releases/v1.3.2/20260916-145543-legacy-release-v1-3-2-a560cb74/release-verification.txt`、`release-verification.exit`；管理员入口为同目录 `verify-release-admin.ps1`。

本轮仅确认本机版本、构建和发布包自检，未补做游戏实机流程或纯净 Windows 验证。本次未创建 Git 提交、标签、推送或 GitHub Release。
