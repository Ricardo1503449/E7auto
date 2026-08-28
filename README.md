# E7auto

E7auto is a Windows x64, fail-closed shop automation application built with Python 3.12, Qt Widgets, OpenCV, MSS, and standard Win32 window/input APIs.

The checked-in source configuration is fully calibrated for the validated target machine and has `calibration_complete: true`. Configuration loading still fails closed and sends no input whenever that gate, a required template, or safety-critical geometry is missing. The existing standalone executable is intentionally stale and must not be treated as the final build.

The executable name `EpicSeven.exe`, exact title `第七史诗`, DPI-aware physical client baseline `2322 x 1306`, refresh cost, templates, complete Sky Stone digits `0-9`, entry/exit/refresh/dialog/Sky Stone/inventory/insufficient-gold ROIs and click points, the six non-overlapping inventory slots, downward scroll sequence (`-120` repeated 6 times), stable empty-scan timing (`100 ms`, 3 frames, 3000 ms timeout), operator-confirmed `18 px` overlay offset `(-252,-145)`, overlay capture exclusion, and foreground live `购买金币` recognition are now recorded. The running game is located by the exact window title plus executable name, so its installation directory does not need to be configured. The terminal check passed 5/5 frames at confidence `0.9999991492` with zero input and no persisted screenshots. The four existing wait caps were user-reviewed and retained unchanged. Fixed top/bottom navigation anchors are not part of the design. Post-purchase success is recognized dynamically in the matched inventory slot and does not use an absolute full-window result ROI.

The production scroll path is locked to six `-120` events spaced `100 ms` apart, followed by an `800 ms` settle. Before bottom scanning, the inventory must show both more than `300 px` upward phase translation and more than `30%` pixels changing above difference threshold `8`; otherwise the run stops as `scroll_verification_failed`. Production also requires genuine administrator elevation and exact cursor-position read-back before any click or wheel event is considered dispatched.

## Safety boundaries

- No anti-cheat bypass, injection, process-memory reading, or payment behavior.
- Only standard Windows window management, screen capture, mouse wheel/click, and `RegisterHotKey` APIs are used.
- Runtime captures exist in memory only. There is no screenshot writer, screenshot directory, or debug screenshot option.
- Tests use fakes and synthetic arrays; they do not instantiate live input/capture/window services.
- The overlay is click-through and requires exact `WDA_EXCLUDEFROMCAPTURE` read-back. Press F6 to pause automation and enter drag mode; press F6 again to save the absolute screen position, restore click-through/capture exclusion, and resume. Missing, invalid, or off-screen saved state falls back to the calibrated client-relative default position.
- The observed game process runs at High integrity. The final standalone build therefore requests Windows administrator elevation at startup; refusing the UAC prompt prevents the application from running or sending input. Source-level live commissioning must likewise be started through a genuine Windows `RunAs`/administrator process. A Codex sandbox approval by itself is not Windows elevation.

## Development setup

The only supported source interpreter is:

```powershell
D:\E7auto\.venv\Scripts\python.exe
```

Install exactly the locked dependencies without using user site-packages:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --cache-dir .\.pip-cache -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

The first command is the only permitted use of system Python. Run and test with:

```powershell
.\.venv\Scripts\python.exe -m e7auto
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\verify_environment.py
```

## User-visible behavior

The main window contains the refresh-currency limit input, one `购买友情点数` checkbox, and the start button. Sacred Covenant Bookmarks and Mystic Medals are always purchased; Friendship Points are purchased only for runs where the checkbox was selected. A valid start resets all per-run counts, registers bare F5 and F6, starts the elapsed timer, restores the last valid saved overlay position (or the calibrated default on first launch), and minimizes the main window. The overlay centers `已耗时：x时x分x秒`, every target count, `已消耗天空石：spent / limit`, `已经x次未出货`, `当前状态：xxx`, and the permanent `F5结束 / F6移动` hint in one shared content column. Status is `已启动` before confirmed shop entry, `刷新ing...` while operating in the shop, `转运ing...` after a strategy exit reaches the main screen for its recovery wait, `重连中` while the network recovery path is active, and `已停止` after termination. Network recovery time is excluded from recognition timeout budgets; after recovery, the prior activity status is restored and all existing stable-frame and exact-balance gates remain unchanged. The displayed no-target streak increments after each fully scanned refreshed inventory without Covenant Bookmarks or Mystic Medals, continues across the unchanged `13/13/13/10` strategy stage boundaries, and resets only when either mandatory target is found. Its production size is measured once for maximum values and remains fixed for the entire run; elapsed updates repaint only their label. At termination the timer freezes and the last immutable snapshot remains visible.

Detailed stop reasons and recognition/input events are written only to per-run UTF-8 text files under `logs`. Retention is bounded by both age and file count.

A refresh is counted only when the stable top-right Sky Stone balance changes from its pre-refresh value to exactly `before - 3`. An unchanged value is allowed to remain pending until timeout; a stable different delta terminates fail-closed. The exact post-refresh value is reused only while the engine remains in the same certain in-shop state; entry/re-entry, strategy or network recovery, ambiguity, and refresh retry/failure invalidate it and force a fresh stable read. Frames showing the exact expected post-refresh value simultaneously accumulate the next top viewport's unchanged three-frame stability, but no inventory action occurs before the balance gate succeeds; an unstable concurrent result falls back to the original independent top scan. Inventory matching prepares BGR once per captured frame, omits disabled targets and already completed current-inventory slots before template work, and summarizes repeated purchased-state skips. Aggregate performance-stage logs report capture/vision timing without persisting screenshots.

The consecutive no-target policy applies only to inventories produced by successful refreshes. Without an available Covenant Bookmark or Mystic Medal it runs `13 refreshes -> exit, wait 5 seconds on the main screen, re-enter -> 13 refreshes -> exit, wait 3 minutes, re-enter -> 13 refreshes -> exit, wait 5 seconds, re-enter -> 10 refreshes -> stop`. Detecting either mandatory target resets the policy to the first 13-refresh stage; Friendship Points and already-purchased states do not reset it. The currency limit remains an independent hard ceiling and is checked before any recovery wait or exit. Strategy exhaustion stops as `refresh_strategy_exhausted` and retains the final overlay.
Only the 3-minute main-screen recovery can trigger the game's UI-hidden standby state. After that wait, automation sends one fully guarded click at the calibrated client center `(1161,653)` to restore the main-screen UI before locating and clicking the shop icon. The two 5-second recoveries do not send this wake click.

## Calibration and release

- [Internal calibration guide](docs/CALIBRATION.md)
- [Architecture and state-machine notes](docs/ARCHITECTURE.md)
- [Clean Windows release checklist](docs/RELEASE_CHECKLIST.md)

Build a standalone directory with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-standalone.ps1
.\.venv\Scripts\python.exe scripts\verify_release.py
```

The build script embeds a `requireAdministrator` UAC manifest. The existing `dist` directory predates this correction and remains a stale historical artifact until live validation is complete.

Onefile is intentionally deferred until the standalone directory passes real-machine calibration and clean-Windows validation.
