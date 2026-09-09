# 后台捕获与输入实机验证

本验证用于确认“游戏保持展开但被其他窗口完全覆盖”时，E7auto 是否能通过生产使用的 WGC 后端取得新画面，并发送不占用真实鼠标的窗口消息。不要用正常启动按钮替代这些管理员验证。

## 共同前提

- 使用当前源码和项目虚拟环境，不使用旧的独立版。
- 游戏窗口保持展开，禁止最小化。
- 游戏客户区必须为当前参考尺寸 `2322 x 1306`。
- 执行验证前，用 PowerShell 或其他普通窗口完全覆盖游戏并保持该窗口在前台。
- 三项验证都不会保存截图；只在 `logs` 中写入一份 JSON 结果。
- `capture` 不发送输入；`scroll` 只发送六次 `-120` 滚轮消息；`navigation` 只点击主界面的商店入口和商店内的退出按钮。
- 验证器没有刷新、刷新确认、购买或购买确认调用路径。

## 1. 后台捕获

先让游戏停在秘密商店库存顶部，再用另一个窗口完全覆盖游戏。在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-admin-background-validation.ps1 -Mode capture
```

结果写入 `logs\background-capture-wgc-validation.json`。只有 `status` 为 `ok` 且所有功能、安全与现有识别时限条件满足才算通过。WGC 在正式自动化工作线程中延迟加载，正常 UI 启动阶段不会导入 PyWinRT。

若要区分 WGC 动态画面是短暂延迟还是在完全遮挡期间持续冻结，先把库存放回顶部并完全覆盖游戏，然后运行一次最长 10 秒、仍然只有六次滚轮输入的观测：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-admin-background-validation.ps1 -Mode scroll -CaptureBackend wgc -EffectObservationMs 10000
```

结果写入 `logs\background-scroll-wgc-post-10000ms-validation.json`。这个扩展观测模式会在最后一次滚轮消息发送后立即轮询 WGC 新帧，以便观察滚动效果出现的完整时序；生产路径则从 `100 ms` 开始自适应稳定检测，并以 `800 ms` 为最长时限。`effect_trace` 会记录每次新 WGC 帧相对滚动前画面的位移和变化比例以及距最后一次输入的时间。观测期间不会追加任何输入。

## 2. 后台滚动

仅在同一捕获后端通过第 1 项后执行。再次人工确认库存位于顶部、游戏已被覆盖，并在短测试期间不要主动移动鼠标：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-admin-background-validation.ps1 -Mode scroll
```

结果写入 `logs\background-scroll-wgc-post-validation.json`。通过条件包括准确发送六次窗口消息、真实鼠标位置和前台窗口不变、列表向上位移超过 `300 px`、变化像素比例超过 `30%`，以及顶部/底部识别稳定。

## 3. 安全导航点击

仅在第 1、2 项通过后执行。先让游戏回到主界面，再用另一个窗口完全覆盖游戏：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-admin-background-validation.ps1 -Mode navigation
```

结果写入 `logs\background-navigation-wgc-post-validation.json`。验证器只允许“进入商店”和“退出商店”两个已识别坐标；任一视觉确认超时都会停止。

## 结果处理

按顺序提供三份 JSON。任一项失败都应停止实机验证并排查；三项全部通过后再进行完整生产流程试跑。
