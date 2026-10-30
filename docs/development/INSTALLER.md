# Windows 安装包

## 工具和目录

保留项目锁定的 Nuitka 4.1.3，由.venv中的Python开发依赖管理；Inno Setup仍为6.7.3。两者只在开发机器上使用，用户不需要安装Python、Nuitka或Inno Setup。安装编译器使用官方签名发行包，以 `/PORTABLE=1 /CURRENTUSER` 准备到登记的tools/innosetup/6.7.3；构建通过公共路径入口解析ISCC.exe，不修改系统PATH。官方安装器副本保留在tools/innosetup/innosetup-6.7.3.exe。重建.venv后重装Python开发依赖，Inno Setup安装不受影响；tools不会进入安装payload。

C后端编译器由Nuitka检测和选择。当前WinLibs GCC 15.2.0（MinGW-w64 13.0.0，MSVCRT，x86_64 POSIX SEH，r6）登记为`nuitka_gcc`，位于`tools/cache/nuitka/downloads/gcc/x86_64/15.2.0posix-13.0.0-msvcrt-r6/mingw64`，公共工具入口解析`bin/gcc.exe`。Nuitka缓存登记为`nuitka`，构建脚本通过`development_tool_path`设置`NUITKA_CACHE_DIR=tools/cache/nuitka`，不依赖任务临时路径或根目录旧缓存。工具和缓存仅开发使用，不修改系统PATH，不随程序打包；Nuitka本身继续由.venv管理。编译器版本变化仍按规则重新判断构建及安装验收范围。

安装脚本为 [e7auto.iss](../../scripts/release/e7auto.iss)。简体中文语言文件取自 Inno Setup 官方仓库 `is-6_7_3` 标签的 `Files/Languages/Unofficial/ChineseSimplified.isl`，保留贡献者注释；它属于构建输入，不是程序运行依赖。

- Nuitka 文件：`build/nuitka/<构建ID>/launcher.dist`，成功构建后连同本次编译目录自动清理。
- 待验收安装包：`build/staging/<构建ID>/setup`，封存成功后保留至验收和dist提升完成。
- 最终分发目录：`dist/`只保留最新成功验收的`E7auto_v<版本>_Setup_x64.exe`。不生成ZIP、解压目录或安装包备份。
- 证据：`artifacts/releases/<版本>/<构建ID>/`；安装生命周期验证使用同一 ID 下的 `build/staging` 专用测试目录。

自动清理只匹配dist直属的`E7auto_v<三段数字版本>_Setup_x64.exe`；其他文件、目录、artifacts原始证据、编译缓存及GitHub历史Release不在清理范围内。路径含符号链接或联接时拒绝提升和清理。

## 安装与用户数据

默认目录为 Program Files 下的 E7auto。安装向导始终显示目录选择页，用户可以改为其他本地磁盘的专用目录，包括中文和空格路径。首次安装拒绝磁盘根目录、网络目录、符号链接/联接，以及未登记本产品的非空目录。旧免安装版不自动迁移，不覆盖源码目录。

开始菜单入口随安装创建；桌面快捷方式首次默认不勾选，可由用户选择。程序、DLL、assets、config、使用说明.md、logs和state都留在同一安装根目录。资源按可执行文件所在目录读取，不依赖启动工作目录，不另建 AppData 业务数据目录。

程序仍请求管理员权限。安装、升级和卸载不强制终止程序；安装器与程序使用命名互斥对象防止运行中替换文件。自动化停止并且工作线程退出后，用户才可关闭应用进行安装维护。

## 升级与卸载

固定 AppId 为 `{438C8C44-EB0D-48EA-BA98-6B4589FE35B2}`，升级沿用原目录和同一控制面板记录，拒绝降级。更换目录需要先卸载；卸载会清除日志和状态。

覆盖安装更新程序、配置、模板和使用说明，保留logs/state；`install-files.txt`记录本版本安装器拥有的文件。升级只删除旧清单中已经不再分发的文件，不通配清空安装根目录。清单中的绝对路径、上级目录、运行数据和不安全路径会被拒绝。

同版本重新构建的安装包也允许覆盖原目录，程序和模板按新包替换；用户先停止脚本并关闭E7auto后运行新安装包即可，无需卸载。版本号和文件名相同不能用于区分构建，交付核对使用本地发布记录中的安装EXE摘要及构建ID。

## 程序内更新

安装版的更新弹窗提供“更新”按钮，选择同一次GitHub最新正式Release响应中的唯一
E7auto_v<版本>_Setup_x64.exe，并要求state=uploaded、完整大小及digest=sha256:<摘要>。
下载保存到安装根目录state/updates/<版本>-<32位任务ID>/，使用.part文件逐块写入；
只在大小和摘要吻合后改为EXE。源码、未登记安装目录、错误路径或缺少摘要时拒绝自动安装。
失败或取消清理部分下载；本地清理失败显示剩余路径。完整包在安装启动失败时保留供重试，
启动前再次核验，并核对安装EXE内嵌文件版本与Release版本一致；内容变化时重新下载，不复用损坏包。

通过ShellExecuteEx以runas启动安装器，使用/SILENT /SP- /NORESTART以及显式禁止关闭其他
程序的选项；保留安装进度和错误提示，不自动重启Windows。/E7AUTOUPDATE=1与绑定目标版本的
/E7AUTOUPDATEID启用更新模式。安装器沿用原登记目录，最多等待60秒让旧应用正常退出，
仍拒绝未退出的实例，不强杀进程。之后执行原有目录、清单、版本和保留规则；config与模板
作为程序内容更新，用户偏好、位置、队列等state数据及logs保留。

只有成功安装才由[Run]以nowait启动新E7auto.exe --after-update=<任务ID>。该专用启动先等待
安装锁释放，再进入正常运行互斥保护，避免新程序与尚未退出的安装器互相阻塞。主窗口显示后
后台核对本次下载包与当前版本、文件名、大小及摘要，删除匹配的EXE；短暂文件占用有界重试，
不影响新程序启动。request.json和install.log保留；不扫描清理其他任务或用户文件。
安装失败没有成功重启/清理动作，保留安装包及日志。原手动安装不自动启动程序。

后续发布的安装器必须同时包含上述更新模式及新版启动处理。现有旧包不会因源码变化
获得此能力；程序内更新仍只识别更高版本号，同版本重建需手动覆盖。

实现依据：[Inno命令行参数](https://jrsoftware.org/ishelp/topic_setupcmdline.htm)、
[Inno运行条目](https://jrsoftware.org/ishelp/topic_runsection.htm)与
[GitHub发布附件元数据](https://docs.github.com/en/rest/releases/assets)。

## 卸载与安装身份

控制面板和安装目录中的 `unins000.exe` 调用同一卸载流程。卸载移除已登记程序文件、使用说明、快捷方式、logs/state及安装版专用注册表项；无关用户文件保留，空安装根目录删除。卸载前拒绝异常清单和目录联接，避免越界删除。

安装版使用 `E7auto.Installed` 通知身份：安装器在 HKLM 的 `Software\Classes\AppUserModelId` 下创建并负责删除，运行时只读核对图标位置，不再创建跨账户难以清理的 HKCU 身份。源码保留 `E7auto.E7auto` 身份和既有通知行为。开始菜单/桌面快捷方式及主窗口使用安装版的同一身份；COM回调仍仅存在于运行进程，无LocalServer32或开机启动项。不修改环境变量或系统PATH。

Windows自身的通知历史和通知偏好由系统管理，不扫描或删除系统通知数据库，也不删除旧源码/免安装版的独立注册表身份。

## 构建与验证

```powershell
powershell -ExecutionPolicy Bypass -File scripts\release\build-standalone.ps1
$releaseRun = '<构建脚本打印的Release record directory>'
# 在管理员终端执行；要求本机没有已安装的本产品或同名快捷方式：
.\.venv\Scripts\python.exe -B -m scripts.release.verify_installer --run-dir $releaseRun
# 只读查看本次验证范围与复用依据（不需要管理员权限，不安装）：
.\.venv\Scripts\python.exe -B -m scripts.release.verify_installer --run-dir $releaseRun --plan
# 安装相关改动、新失败或用户明确要求时强制完整验收：
.\.venv\Scripts\python.exe -B -m scripts.release.verify_installer --run-dir $releaseRun --full
# 若检查单独安装到某个目录的程序：
.\.venv\Scripts\python.exe -B -m scripts.release.verify_release --run-dir $releaseRun --directory '<实际安装目录>'
# 获准提交后才执行：
.\.venv\Scripts\python.exe -B -m scripts.release.workflow check --run-dir $releaseRun --revision HEAD
```

preflight同步版本/文档；capture保存编译输入；Nuitka与原生DLL归一化后prepare生成拥有文件清单并冻结完整payload；ISCC编译后seal确认源码/payload未变并记录安装EXE摘要。prepare和seal不代替安装验收。

所有构建先在本次staging/setup生成并封存新包。dist中的已验收安装包持续保留，直到新包完成完整或复用模式要求的安装检查、实际编译版自检和测试卸载；随后原子移入dist或替换同名包，再清理其他旧版标准安装EXE。此规则同样适用于同版本重建，构建或验收失败不会提前覆盖可用旧包。较低版本或同版本较早构建不得替换已有的较新已验收安装包。

seal通过后只清理本次`build/nuitka/<构建ID>/`；`build/staging/<构建ID>/`标记为retained_until_acceptance，保留待验收包。build-result.json中的installer指向待验收路径。所有清理先核对目录边界并拒绝符号链接或联接；失败构建保留现场，nuitka清理失败单独记录，不删除待验收包或dist中的可用包。

安装验收全部成功、测试安装完整卸载后，verify_installer先保存绑定本包的验收记录，再调用distribution提升安装包并清理旧包，最后清理本次staging。验收失败或未完成不触发提升；提升或旧包清理失败保留现场、返回失败退出码，installer-verification.json的distribution字段记录错误，不将已完成的安装检查伪写为失败。distribution-result.json单独记录计划、提升及已删除文件；旧包被占用等情况可在解除占用后重新整理，无须重复安装。staging清理失败仍单独记录staging_cleanup和staging_cleanup_error；构建与验收日志保存在artifacts/releases。

verify_release检查实际安装目录的程序版本、资源、运行库、编译版自检、卸载登记和通知身份，再逐文件比对封存payload。编译中间目录不能作为安装验证输入。现有批量Git摘要与按阶段计算哈希优化保留。

对于已有完整验收证据的安装包，可以独立预览或执行dist整理：

```powershell
.\.venv\Scripts\python.exe -B -m scripts.release.distribution --run-dir $releaseRun --plan
.\.venv\Scripts\python.exe -B -m scripts.release.distribution --run-dir $releaseRun
```

该入口不安装程序、不需要管理员权限，依据既有封存摘要、安装验收及编译版自检绑定操作，保留原历史报告。它不代表当前源码已重新构建；最终发布仍须通过workflow check的源码与Git输入一致性检查。验收可以读取本构建staging/setup中的包；发布检查只接受已经提升到dist且摘要一致的包。


verify_installer仅在无既有安装的受控管理员环境运行。默认按构建快照与已完成完整验收的范围比较：只有features/vision业务源码、config运行配置、assets/templates模板内容及随包说明可作为免重跑完整生命周期的改动；安装相关输入、编译器、原生依赖、版本和payload文件列表必须一致。其他或未知改动、历史证据缺失/损坏、前次失败、用户指定--full均要求完整验收；发布check会再次核对，不能仅凭报告标记放行。

完整模式执行14项检查：自定义中文空格路径、不选桌面图标、开始菜单入口、实际安装内容及编译版自检、运行中安装/卸载拒绝、同版本覆盖保留数据、停用文件清理、选择桌面图标、卸载删除数据、删除登记、保留无关用户文件、空根目录删除和环境变量前后等价。复用模式仍对新包执行7项实际检查：中文空格路径安装、不选桌面图标、开始菜单入口、逐文件安装核对及版本/资源/原生运行库/编译版自检、卸载删除登记、空根目录删除和环境变量不变；其余7项明确列入reused_checks，引用原完整验收的构建目录及快照、产物、实际安装自检、完整验收、编译器记录哈希。新报告绑定新安装EXE，不伪造已执行的覆盖测试，不允许复用链。

两种模式失败均保留测试安装与日志供检查，不强制清理；成功后按前述规则清理本次staging。真实游戏功能、干净虚拟机、其他Windows版本和通知声音不是自动验收的覆盖范围。此策略只用于之后的验收，不追改历史快照或把未执行的验收记为通过。

验收报告记录系统名称、版本/构建号和架构，复用要求与原完整验收一致；历史报告未记录环境时不能推测补填，下一次先做完整验收建立可复用基线。实际安装前先核对源码输入与构建快照一致，拒绝用修改过的源码工具验证旧快照。

最终check要求真实安装自检与本次验收报告绑定同一安装EXE；完整模式检查所有项，复用模式另验证原完整证据、范围一致性与本次7项检查。历史schema 1 ZIP记录保持原样；新schema 2安装包不得用旧记录追认。版本验证结论写入docs/releases，构建、安装日志和下载摘要留在本地artifacts。提交、推送与发布授权规则保持不变。
