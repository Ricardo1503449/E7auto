# E7auto

E7auto 是一款面向 Windows x64 的安全停止型商店自动化应用，使用 Python 3.12、Qt Widgets、OpenCV、Windows Graphics Capture 和标准 Win32 窗口消息输入构建。

当前检入源码版本为 `v1.3.3`，配置已针对经过验证的目标主机完成校准，并设置了 `calibration_complete: true`。当校准门控、必要模板或安全关键几何信息缺失时，配置加载会安全失败，不发送输入。可下载版本及附件以 [GitHub Releases](https://github.com/Ricardo1503449/E7auto/releases/latest) 为准；源码版本与已发布版本可能不同。

详细界面行为、刷新策略、校准和窗口适配规则见 [用户行为与运行策略](docs/design/BEHAVIOR.md)。

各版本的功能变化见[更新日志](CHANGELOG.md)，验证结论见[项目文档](docs/README.md)。

功能中心还提供“红叶换企鹅”：从游戏主界面进入成长祭坛，按每批 50 个企鹅自动购买，达到成功批次上限或红叶不足一整批时正常返回主界面。设置页提供购买次数上限、只读的“预计消耗红叶”（上限 × 5,100）、启动按钮和 F5 提示；预计值不是实时余额读取。悬浮窗保留最终耗时与购买次数，返回功能中心时隐藏，下次启动后重新显示。网络异常会自动重连，恢复后继续画面确认，已提交的购买不会重复提交。详见 [红叶换企鹅运行规则](docs/design/BEHAVIOR.md#红叶换企鹅)。红叶换企鹅自 v1.3.0 提供；v1.3.1 的商店识别改进继续保留。

运行中显示 `转运ing...` 表示正在按刷新策略等待，属于正常流程。持续未发现圣约书签或神秘奖牌时，按 `13 / 13 / 13 / 10` 次分阶段刷新，前三阶段结束后分别退出商店，在主界面等待 `5 秒 / 3 分钟 / 5 秒`，随后自动重新进入商店；第四阶段仍未出货则正常停止。等待期间耗时继续累计，无需手动返回商店或重启脚本，F5 仍可结束运行。具体计数、重置及停止规则见 [刷新策略与转运等待](docs/design/BEHAVIOR.md#刷新策略与转运等待)，发布包用户也可查看 [使用说明](docs/user/使用说明.txt)。

## 窗口消息输入风险与免责声明

从 `v1.2.0` 开始，程序不再移动物理鼠标，而是通过目标游戏窗口句柄发送 Win32 窗口消息来执行滚轮和点击命令。这种后台消息输入与普通玩家的前台物理鼠标操作不同，会增大账号被游戏客户端、反作弊系统或运营方判定为异常自动化，并受到警告、限制、暂停或永久封禁的风险。项目不提供规避检测、绕过反作弊或保证账号安全的能力，也不承诺该输入方式现在或未来符合游戏服务条款。

使用者应自行阅读并遵守游戏服务条款、运营规则和所在地适用法律，并自行决定是否运行本程序。下载、测试或使用本程序即表示使用者知悉并自愿承担全部风险；因使用本程序导致的账号处罚、虚拟物品或货币损失、数据损坏、服务中断以及其他直接或间接损失，项目作者和贡献者不承担责任。如果不能接受上述风险，请勿使用本程序。

## 安全边界

- 不包含反作弊绕过、注入、进程内存读取或支付行为。
- 仅使用标准 Windows 窗口管理、Windows Graphics Capture 客户区捕获、窗口消息滚轮/点击和 `RegisterHotKey` API；捕获在自动化工作线程中延迟加载，COM及截图资源由该线程初始化和释放。WGC入口在导入PyWinRT前选择兼容的MSVC运行库，避免其旧私有DLL与Qt冲突。
- 正常运行的识别帧在内存中处理；异常停止时最多将最后一次成功捕获的缓存游戏帧保存为 logs 中的 `run-…-stop.png`，不额外捕获画面。正常完成和 F5 停止不保存；无缓存帧时跳过，写入失败不改变停止原因。
- 离线业务测试使用替代服务和固定样本；UI生命周期回归会创建真实Qt窗口，Qt/WGC共存回归还会在真实工作线程中创建和读取WinRT内存位图。这些回归不创建WGC捕获会话、不采集游戏或桌面画面、不发送游戏输入；实机验证需另行执行。
- 悬浮窗可在脚本运行或停止期间直接拖动，松开鼠标时保存绝对屏幕位置。左上角绿色“收起”可将其变为使用程序 Logo 的圆形悬浮图标；图标仍可拖动，单击即可恢复完整悬浮窗，每次启动脚本也会先恢复完整形态。圣约书签和神秘奖牌两行统计使用绿色文字突出显示。保存状态缺失、无效或位于屏幕外时，程序会回退到经过校准的客户区相对默认位置。悬浮窗不再鼠标穿透，因此其覆盖区域不会把用户的物理鼠标操作传递给下方窗口，但不会影响发往游戏窗口的后台自动化消息。WGC 只捕获指定的游戏窗口，不依赖悬浮窗截图排除。
- 所有识别 ROI、锚点、客户区点击点、滚轮位置和首次悬浮窗偏移都从唯一的 `2322 x 1306` 校准空间映射到实际客户区；输入前重新核对窗口身份、显示器、当前模式和 DPI，运行中不会自动二次缩放或改用新的坐标基准。
- 已观察到的游戏进程运行于 High 完整性级别。因此最终独立构建会在启动时请求 Windows 管理员权限；拒绝 UAC 提示将阻止应用运行或发送输入。源码级实时校准同样必须通过真正的 Windows `RunAs`/管理员进程启动。仅批准 Codex 沙箱权限并不等同于获得 Windows 管理员权限。

## 开发环境配置

源码唯一支持的解释器为：

```powershell
D:\E7auto\.venv\Scripts\python.exe
```

安装锁定的精确依赖，并禁用用户级 site-packages：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --cache-dir .\.pip-cache -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

只有第一条命令允许使用系统 Python。运行和测试命令如下：

```powershell
.\.venv\Scripts\python.exe -m e7auto
powershell -ExecutionPolicy Bypass -File scripts\test-source.ps1
.\.venv\Scripts\python.exe -m scripts.verify_environment
```

`scripts/test-source.ps1` 是全套测试入口，仅在明确要求全量验证时运行；Qt与WGC使用同一次pytest调用。增量修改按[开发指南](docs/development/DEVELOPMENT.md)只运行直接相关测试。

准备发布时，按[开发指南的发布顺序](docs/development/DEVELOPMENT.md#构建与交付)先同步文档、构建验收，再集中提交推送，最后发布Release。

## 校准与发布

- [开发指南与目录约定](docs/development/DEVELOPMENT.md)
- [用户行为与运行策略](docs/design/BEHAVIOR.md)
- [内部校准指南](docs/development/CALIBRATION.md)
- [架构与状态机说明](docs/design/ARCHITECTURE.md)
- [纯净 Windows 发布检查清单](docs/development/RELEASE_CHECKLIST.md)

使用以下命令构建独立目录：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\release\build-standalone.ps1
# 将占位内容替换为构建脚本打印的完整目录，在管理员终端执行验收：
$releaseRun = '<构建脚本打印的Release record directory>'
.\.venv\Scripts\python.exe -B -m scripts.release.verify_release --run-dir $releaseRun
```

构建脚本会嵌入 `requireAdministrator` UAC 清单并生成与 `pyproject.toml` 版本一致的 `E7auto_v<版本>_x64.zip`。只有新版独立目录成功构建且新版 ZIP 成功压缩后，脚本才会删除 `dist` 中其他版本的 ZIP；构建或压缩失败时会保留旧 ZIP。`dist` 仅保存本机构建结果，不纳入 Git 跟踪；通过发布检查的版本化 ZIP 应作为 GitHub Release 附件上传。

在独立目录通过真实主机校准和纯净 Windows 验证之前，暂不采用单文件打包。

新增功能或识别模板时，遵循[模板资源组织与新增功能约定](docs/development/TEMPLATES.md)。

项目文档入口见[docs/README.md](docs/README.md)；新增文件和任务输出遵循[目录与产物约定](docs/development/LAYOUT.md)。
