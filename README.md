# E7auto

E7auto 是一款面向 Windows x64 的安全停止型商店自动化应用，使用 Python 3.12、Qt Widgets、OpenCV、Windows Graphics Capture 和标准 Win32 窗口消息输入构建。

当前检入源码版本为 `v1.2.1`，配置已针对经过验证的目标主机完成全部校准，并设置了 `calibration_complete: true`。当该校准门控、必要模板或安全关键几何信息缺失时，配置加载仍会安全失败，并且不会发送任何输入。本地 Windows x64 构建包名称为 `E7auto_v1.2.1_x64.zip`；GitHub 推送与发布属于独立操作，不由本地构建自动执行。

详细界面行为、刷新策略、校准和窗口适配规则见 [用户行为与运行策略](docs/BEHAVIOR.md)。

## 窗口消息输入风险与免责声明

从 `v1.2.0` 开始，程序不再移动物理鼠标，而是通过目标游戏窗口句柄发送 Win32 窗口消息来执行滚轮和点击命令。这种后台消息输入与普通玩家的前台物理鼠标操作不同，会增大账号被游戏客户端、反作弊系统或运营方判定为异常自动化，并受到警告、限制、暂停或永久封禁的风险。项目不提供规避检测、绕过反作弊或保证账号安全的能力，也不承诺该输入方式现在或未来符合游戏服务条款。

使用者应自行阅读并遵守游戏服务条款、运营规则和所在地适用法律，并自行决定是否运行本程序。下载、测试或使用本程序即表示使用者知悉并自愿承担全部风险；因使用本程序导致的账号处罚、虚拟物品或货币损失、数据损坏、服务中断以及其他直接或间接损失，项目作者和贡献者不承担责任。如果不能接受上述风险，请勿使用本程序。

## 安全边界

- 不包含反作弊绕过、注入、进程内存读取或支付行为。
- 仅使用标准 Windows 窗口管理、Windows Graphics Capture 客户区捕获、窗口消息滚轮/点击和 `RegisterHotKey` API；捕获在自动化工作线程中延迟加载，避免 PyWinRT 与 Qt 启动/退出生命周期混用。
- 运行时截图仅存在于内存中，不提供截图写入器、截图目录或调试截图选项。
- 测试使用替代实现和合成数组，不会实例化真实的输入、截图或窗口服务。
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

## 校准与发布

- [开发指南与目录约定](docs/DEVELOPMENT.md)
- [用户行为与运行策略](docs/BEHAVIOR.md)
- [内部校准指南](docs/CALIBRATION.md)
- [架构与状态机说明](docs/ARCHITECTURE.md)
- [纯净 Windows 发布检查清单](docs/RELEASE_CHECKLIST.md)

使用以下命令构建独立目录：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\release\build-standalone.ps1
.\.venv\Scripts\python.exe -m scripts.release.verify_release
```

构建脚本会嵌入 `requireAdministrator` UAC 清单并生成与 `pyproject.toml` 版本一致的 `E7auto_v<版本>_x64.zip`。只有新版独立目录成功构建且新版 ZIP 成功压缩后，脚本才会删除 `dist` 中其他版本的 ZIP；构建或压缩失败时会保留旧 ZIP。`dist` 仅保存本机构建结果，不纳入 Git 跟踪；通过发布检查的版本化 ZIP 应作为 GitHub Release 附件上传。

在独立目录通过真实主机校准和纯净 Windows 验证之前，暂不采用单文件打包。
