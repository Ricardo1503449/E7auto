# v1.3.3 源码与本机构建验证

日期：2026-09-16。本机v1.3.3 EXE与ZIP已重建，资源、运行库、ZIP一致性及管理员编译版自检通过；未创建标签或发布GitHub Release。下方版本准备记录保留当时范围，本机产物以末节构建结果为准。

## 改动与已有验证

- 源码职责拆分及文档、测试、开发产物分类已经完成，规则与检查入口见[开发指南](../../development/DEVELOPMENT.md)和[目录约定](../../development/LAYOUT.md)。分层阶段的验证及限制见[源码分层记录](../../validation/common/SOURCE_LAYERING_VALIDATION.md)。
- Qt窗口生命周期修复：标题栏/缩放控件改用主窗口弱引用，访问前检查原生对象有效性；原28项UI用例仅应用生产修复即连续通过，最终38项UI测试通过，两种关闭路径各30轮压力回归通过。证据见[UI专项记录](../../validation/common/SOURCE_LAYERING_VALIDATION.md#2026-09-16-后续ui生命周期修复)。
- Qt/PyWinRT运行库修复：WGC导入前选择兼容MSVC运行库，构建归一化及发布检查保护随包DLL；普通测试恢复同进程。去重83项相关用例通过，两种导入顺序各10轮真实Qt/WinRT生命周期回归通过；38项UI用例包含在83项内，不相加。证据见[平台专项记录](../../validation/platform/BACKGROUND_VALIDATION.md#2026-09-16-qtpywinrt原生运行库共存)。

以上记录沿用修复阶段结果，本轮版本同步不重新运行这些已通过且实现未变的崩溃回归。未运行全套测试、创建实际WGC捕获会话、采集游戏/桌面画面或发送游戏输入。

## 版本同步

`pyproject.toml`、`src/e7auto/__init__.py`、README、CHANGELOG、使用说明及发布检查的当前版本统一为1.3.3；既有版本历史记录保留。项目editable安装通过本地无网络、无依赖更新的重新安装同步。目标包名为 `E7auto_v1.3.3_x64.zip`，Windows文件/产品版本由构建脚本读取项目版本生成 `1.3.3.0`；版本同步阶段尚未生成实际二进制，后续构建结果见末节。

本轮运行 `tests/scripts/release/test_version_sync.py`：6项通过，覆盖源码、项目、安装元数据和当前文档版本一致性、源码自检版本、锁定依赖环境及Windows版本生成契约。该组包含此前已通过的用例，是版本修改后的必要复验，不增加上述83项去重总数。

证据目录为 `artifacts/releases/v1.3.3/20260916-185100-source-version-and-docs-064f597e/`，安装日志为 `editable-install.log`，测试日志为 `version-tests.log`。布局检查曾发现本轮日志误放release任务的results子目录，已按既有规则移回任务根目录，未修改检查规则。

纠正后布局/源码依赖边界检查与 `git diff --check` 通过，31个相关文件均已核对并分配至提交方案；无关pip.ini保留。完整结果见 `layout-final.log`、`diff-check.log`、`final-verification.json`，提交范围与message见 `commit-proposal.md`。上述准备阶段之后，用户授权按序提交为 `b1bd46e`、`1f8e53a`、`99435b8`，三项已推送并核对远端。

## 文档职责整理

版本准备后的文档整理：按用户确认删除CHANGELOG中10个“发布准备”“本机构建”“发布”栏目，保留全部版本/日期标题，将v1.1.3日志模式说明移至该版“改进”。现有历史验收文档保持原样；后续CHANGELOG与发布记录的职责已写入项目规则、开发指南和发布检查单。这属于同一版本准备提交，不改变程序实现。

## 本机重建与验收

用户明确要求重新构建本机dist与ZIP。编译源码基准为 `99435b88db68c224989b200d262b8e1d5d1628b9`；沿用已有相关测试，仅执行环境、构建及新产物验收。未重复全套或无关测试，未进行游戏截图、输入或购买。

- 环境project/source/installed均为1.3.3，锁定依赖无缺失或不匹配。
- Nuitka 4.1.3与Python 3.12、MSVC cl 14.3完成构建；259个C编译单元均未命中缓存。构建过程已完成MSVC副本归一化。
- 产物为 `dist/launcher.dist/E7auto.exe` 与 `dist/E7auto_v1.3.3_x64.zip`；最终ZIP为83,419,972字节（约79.56 MiB）。旧v1.3.2 ZIP在新包成功生成后按构建脚本清理。
- ZIP内156个文件全部通过解压CRC与逐文件SHA-256检查，文件集合与dist一致。48个模板目录文件、11个UI资源及配置与源码字节一致；商店31项、企鹅13项配置模板均可加载。
- 根部7种MSVC DLL及Shiboken嵌套副本共11个文件均为14.44.35211.0；AMD64、Windows文件/产品版本1.3.3.0及requireAdministrator清单检查通过，无运行日志、截图、测试或虚拟环境混入。
- 管理员发布验证退出0、problems=[]；编译版自检报告compiled=true、version=1.3.3、machine=AMD64、wgc_importable=true、wgc_import_error为空、venv_bundled=false。
- 编译后首次验证确认134项构建输入摘要不变。随后仅更新随包使用说明中的“待构建”表述并重新压缩ZIP，可执行文件不变；最终包按更新后的使用说明重新核对。

证据目录：`artifacts/releases/v1.3.3/20260916-195520-standalone-8d787353/`。构建日志与结果为 `build.log`、`build-result.json`，最终ZIP度量为 `archive-verification.json`，管理员验证为 `release-verification.txt` 与 `release-verification.exit`。原始摘要保存在本地证据中。

本次本机验证不能替代真实游戏或纯净Windows验收。未创建标签或发布GitHub Release。
