# 后台捕获与输入实机验证

本验证用于确认“游戏保持展开但被其他窗口完全覆盖”时，E7auto 是否能通过生产使用的 WGC 后端取得新画面，并发送不占用真实鼠标的窗口消息。不要用正常启动按钮替代这些管理员验证。

## 共同前提

- 使用当前源码和项目虚拟环境，不使用旧的独立版。
- 游戏窗口保持展开，禁止最小化。
- 游戏客户区必须为当前参考尺寸 `2322 x 1306`。
- 执行验证前，用 PowerShell 或其他普通窗口完全覆盖游戏并保持该窗口在前台。
- 三项验证都不会保存截图；只在管理任务的 `results/` 中写入一份 JSON 结果。
- `capture` 不发送输入；`scroll` 只发送六次 `-120` 滚轮消息，按当前配置以 `10 ms` 间隔发送；`navigation` 只点击主界面的商店入口和商店内的退出按钮。
- 验证器没有刷新、刷新确认、购买或购买确认调用路径。

## 1. 后台捕获

先让游戏停在秘密商店库存顶部，再用另一个窗口完全覆盖游戏。在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validation\run-admin-background-validation.ps1 -Mode capture
```

结果写入 `artifacts/tasks/platform/<本次任务>/results/background-capture-wgc-validation.json`。只有 `status` 为 `ok` 且所有功能、安全与现有识别时限条件满足才算通过。WGC 在正式自动化工作线程中延迟加载，正常 UI 启动阶段不会导入 PyWinRT。

若要区分 WGC 动态画面是短暂延迟还是在完全遮挡期间持续冻结，先把库存放回顶部并完全覆盖游戏，然后在管理员 PowerShell 中切换到项目根目录，运行一次最长 10 秒、仍然只有六次滚轮输入的观测：

```powershell
.\.venv\Scripts\python.exe -m scripts.validation.validate_background_mode scroll --acknowledge-shop-top-covered --capture-backend wgc --interval-ms 10 --effect-observation-ms 10000
```

结果写入 `artifacts/tasks/platform/<本次任务>/results/background-scroll-wgc-post-10000ms-validation.json`。这个扩展观测模式会在最后一次滚轮消息发送后立即轮询 WGC 新帧，以便观察滚动效果出现的完整时序；生产路径则从 `100 ms` 开始自适应稳定检测，并以 `800 ms` 为最长时限。`effect_trace` 会记录每次新 WGC 帧相对滚动前画面的位移和变化比例以及距最后一次输入的时间。观测期间不会追加任何输入。

## 2. 后台滚动

当前必须显式传入 `--interval-ms 10`：验证器的默认值仍为 `100 ms`，与 `config/internal.yaml` 不一致时会拒绝运行；现有 PowerShell 包装脚本没有间隔参数，因此本节直接调用 Python 模块。五次滚轮间等待合计配置为 `50 ms`；滚动后最短等待 `100 ms`、检测间隔 `100 ms` 和校验窗口 `800 ms` 保持不变。

仅在同一捕获后端通过第 1 项后执行。再次人工确认库存位于顶部、游戏已被覆盖，并在短测试期间不要主动移动鼠标。在管理员 PowerShell 中切换到项目根目录后执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.validation.validate_background_mode scroll --acknowledge-shop-top-covered --capture-backend wgc --interval-ms 10
```

结果写入 `artifacts/tasks/platform/<本次任务>/results/background-scroll-wgc-post-validation.json`。通过条件包括准确发送六次窗口消息、真实鼠标位置和前台窗口不变、列表向上位移超过 `300 px`、变化像素比例超过 `30%`，以及顶部/底部识别稳定。

## 3. 安全导航点击

仅在第 1、2 项通过后执行。先让游戏回到主界面，再用另一个窗口完全覆盖游戏：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validation\run-admin-background-validation.ps1 -Mode navigation
```

结果写入 `artifacts/tasks/platform/<本次任务>/results/background-navigation-wgc-post-validation.json`。验证器只允许“进入商店”和“退出商店”两个已识别坐标；任一视觉确认超时都会停止。

## 结果处理

按顺序提供三份 JSON。任一项失败都应停止实机验证并排查；三项全部通过后再进行完整生产流程试跑。

## 2026-09-16 Qt/PyWinRT原生运行库共存

旧测试入口把Qt与WGC分成两个进程，曾用于规避混合加载时的原生崩溃。专项复核在当前Qt窗口生命周期修复之后仍复现：先导入winrt.runtime，再导入QtCore即发生0xC0000005；尚未创建窗口或截图，因此不能归因于窗口循环引用。

诊断发现WinRT 3.2.1附带MSVCP140为14.29.30157.0，锁定Qt/Shiboken附带版本为14.44.35211.0。原生异常处理器记录的空地址读取发生在winrt/MSVCP140.dll+0x13080，调用栈经过shiboken6与QtCore初始化。先显式加载兼容CRT后，同样的导入顺序正常。

Windows会优先复用已加载的同名依赖DLL，MSVC运行库版本需满足使用者的构建要求，参见[微软DLL查找规则](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order)及[MSVC运行库兼容要求](https://learn.microsoft.com/en-us/lifecycle/faq/visual-c-faq)。此处结论依据本机DLL版本、原生栈和受控对照，不将库名相同或单次测试通过作为依据。

修复：WGC入口先验证已加载的MSVCP140，达到最低版本14.44.35211.0则复用；尚未加载时从Qt/Shiboken随包目录选择兼容CRT。不加载Qt、不初始化COM、不修改PATH、.venv或系统文件；已载入旧DLL则报Python异常，不尝试替换在用模块。构建输出在路径校验后、替换旧发行目录前统一根部与嵌套的7种CRT副本，发布检查拒绝缺失/旧版本。测试入口恢复单次pytest调用，旧的Qt/WGC强制分进程约束退役；生产工作线程的COM初始化/资源释放保持不变。

验证：WGC先加载的同进程相关组82项通过；Qt先创建的反向顺序62项通过（与前组重叠，不累计）。新增原生共存测试各运行10轮，在真实QThread中初始化MTA、创建/读取/关闭WinRT内存位图并反初始化，同时反复创建/销毁Qt窗口；默认GC开启，工作线程正常退出，无残留窗口。没有创建WGC捕获会话、采集游戏或桌面画面、发送输入或运行全套测试。

本地原始日志、DLL版本和原生调用栈在 `artifacts/tasks/platform/20260916-181105-verify-qt-wgc-coexistence-aba57b27/results/`。现有发行包仅只读检查，未重新构建或修改；新源码和构建契约验证不代表旧EXE已经更新。

最终去重共有83项直接相关用例通过：上述82项加新CLI入口的1项检查，反向62项不另累计。覆盖运行库版本字的无符号解码、兼容库复用、旧库拒绝、绝对路径加载、随包库缺失/版本过旧、两种原生导入顺序、构建根部/嵌套副本统一及旧dist保护。清理收紧后另复验两种共存顺序，确认WinRT投影在COM反初始化前释放，QThread延迟销毁完成；这些复验不增加去重总数。

Python与PowerShell语法、布局/源码依赖边界及diff检查通过，74个受保护模板、校准、配置和样本摘要保持不变。完整清单及汇总分别见上述results内的 `related-test-nodeids.json` 和 `final-verification.json`。此前38项UI生命周期回归包含在本轮相关组中，不与83项相加。
