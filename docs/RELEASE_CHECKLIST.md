# Windows x64 release checklist

## Build timing policy

Do not run Nuitka or rebuild `dist\launcher.dist` during incremental development. Use the project `.venv` for source tests, calibration, and separately authorized real-machine validation. Begin the release build only after all script functionality is complete and that live validation has succeeded. Any existing executable is a historical development artifact, not the final deliverable.

## Local standalone gate

- [ ] Run `powershell -ExecutionPolicy Bypass -File scripts\test-source.ps1`; it must run the Qt/non-WGC suite and the PyWinRT/WGC suite in separate Python processes, with both invocations passing.
- [ ] Run `.venv\Scripts\python.exe -m scripts.verify_environment`.
- [ ] Build with `scripts\release\build-standalone.ps1` and project-local Nuitka cache.
- [ ] Confirm the build uses Nuitka `--windows-uac-admin` and the resulting PE manifest requests administrator elevation.
- [ ] Confirm Windows file/product versions are the four-part numeric form of the `pyproject.toml` version, and `E7auto.exe --self-check` reports the same package version.
- [ ] Run `.venv\Scripts\python.exe -m scripts.release.verify_release`.
- [ ] Confirm `dist\launcher.dist` contains `E7auto.exe`, `使用说明.txt`, `config\internal.yaml`, `assets\templates`, and `assets\ui`.
- [ ] Confirm the executable uses `assets\ui\e7auto.ico`; all required 16-1024 PNG sizes, the multi-size ICO, and `shop-card-background.png` are present and pass `verify_ui_assets`.
- [ ] Confirm every required manifest and all 28 described calibrated PNGs are present, decodable, structurally valid, and covered by the focused asset tests. This includes the separately manifested wide Sky Stone `0` variant, plus `client_calibration_manifest.yaml` for automatic initial cropping of five full-window sources and references to the separate insufficient-gold and overlay-position evidence.
- [ ] Confirm no `.venv`, `.pip-cache`, tests, logs, or runtime screenshots are included.
- [ ] Confirm source and packaged logging use the unified defaults: 7 days, 20 runs, 10 MB per file, 3 backups, 500 MB total target; no logging mode selector.
- [ ] Confirm the PE machine is AMD64. Do not label the build ARM64-compatible.

## Clean Windows x64 gate

Use a Windows x64 machine or VM with no Python installed.

- [ ] Copy only the standalone directory.
- [ ] Double-click `E7auto.exe`; verify Windows shows UAC. Rejecting UAC must prevent startup and all input. Accepting it must produce an elevated/High-integrity process able to interact with the observed High-integrity game.
- [ ] Run `E7auto.exe --self-check` and retain its text output.
- [ ] In a separate test copy, set `calibration_complete: false`; verify `E7auto.exe` logs refusal and sends no input. Restore the verified configuration before functional testing.
- [ ] Verify the resizable function center opens `刷新秘密商店`, the back control returns to the card grid, the numeric field has no spinner, only the compact `购买友情点数` switch is clickable, and the green start button retains the existing launch behavior.
- [ ] With input disabled, verify current-mode/full-monitor cross-check, the exact `3120 x 2080 -> 2322 x 1306` reference path, `2560 x 1440` minimum boundary, 60%-width non-reference sizing, DPI-aware outer-height fitting, and negative-origin secondary-monitor clamping.
- [ ] Verify DPI behavior on every monitor used for the game, plus fail-closed stop before input after monitor migration, desktop-mode change, or DPI change.
- [x] Retain source overlay offset `(-252,-145)` as the first-launch/invalid-state fallback position; runtime placement may be persisted anywhere on the virtual desktop.
- [ ] Verify the production overlay remains no-activate and directly draggable throughout a run; drag release saves its position, and WGC game-window frames remain independent of desktop overlay placement.
- [ ] Verify green-background `收起` is aligned at the top-left opposite the final-only close button, uses the overlay text size, and switches to the circular application-Logo view. Verify Covenant Bookmark and Mystic Medal rows use green text, the circle remains draggable, a click without dragging restores the full panel, and every new run starts expanded.
- [ ] Verify drag release saves position without pausing automation, the saved position restores after restart, off-screen state falls back, and F6 is neither registered nor shown in the UI.
- [ ] Verify F5 can register only while running and is available again immediately after termination.
- [ ] Verify startup succeeds while the game is foreground or covered in the background without requesting activation. Verify a minimized game is restored with `SW_SHOWNOACTIVATE` before startup continues, while movement, resize, a later minimize, disappearance, process-identity change, and display/DPI changes still terminate safely.
- [ ] Verify consecutive-identical top/bottom viewport scans, exactly one calibrated downward navigation per inventory, same-viewport rescans after purchase, completed-slot suppression, all targets/slots, target-specific purchase confirmation/success, insufficient currency, refresh secondary confirmation, automatic return to top after refresh, stable exact Sky Stone `-3` deduction, unchanged/mismatched/unreadable balance refusal, and exact-budget final scan using non-payment test conditions. Exercise the full-width top-bar icon search and contiguous digit-run parser with five-, six-, seven-, and longer visible balances at reference and supported non-reference client sizes, including a distant notification-control interference case.
- [ ] Verify the adaptive no-target schedule with non-payment test conditions: `13 -> exit/5 s/re-enter -> 13 -> exit/180 s/re-enter -> 13 -> exit/5 s/re-enter -> 10 -> refresh_strategy_exhausted`, plus mandatory-target reset, Friendship Points non-reset, F5 during waits, and budget preemption before recovery.
- [x] With the `购买金币` prompt already visible and the game foreground, run the WGC capture-only administrator validator. The completed run produced 5/5 detections at `0.9999991492`, `purchase_funds_insufficient`, zero input, no click on the prompt's `确认`, no persisted screenshot, and enabled `calibration_complete`.
- [ ] Confirm downward navigation uses exactly six `-120` client-area window-message wheel events at `100 ms` spacing; verify adaptive settling begins at `100 ms`, requires two stable pairwise observations, never waits beyond the `800 ms` ceiling before its final sample, rejects stale visually stable frames until the full-resolution movement gate passes, and preserves the calibrated displacement thresholds. Confirm the last three settle frames either complete bottom recognition with `cache_outcome=hit` or retain the logged stable suffix and capture only missing frames; no fallback may restart from zero.
- [ ] Confirm no-click commissioning reproduces three-frame stable refresh control, Sky Stone OCR, and top/bottom inventory scans within the calibrated 3000 ms scan timeout. If no target is present, record that only stable-empty behavior was exercised.
- [ ] Verify logs are UTF-8 text, retention is bounded, and no screenshot files/directories appear.

## GitHub Release publishing

- [ ] Keep `dist\launcher.dist` and every versioned ZIP as local ignored build output; do not add either path to the Git repository.
- [ ] Create the versioned ZIP from the exact locally verified standalone directory and stream every entry to confirm successful decompression and CRC validation.
- [ ] Create the matching version tag and GitHub Release from the reviewed release commit.
- [ ] Upload only the verified versioned ZIP as the Release asset, then confirm its displayed size and downloadable filename.
- [ ] Set the public Release title to `E7auto vX.Y.Z` and begin the Release description with the exact top-level heading `# 更新内容`.
- [ ] Under `更新内容`, choose only subheadings that match the actual changes, such as `## 新增`, `## 改进`, or `## 修复`; omit empty or irrelevant sections.
- [ ] Make `## 发布` the final subheading. Its complete content must be exactly one bullet in the form ``- 文件：`E7auto_vX.Y.Z_x64.zip` ``. Do not use `下载校验`, add explanatory text, links, SHA-256, or other digest values to this final section or elsewhere in the public Release title/description. Hashes may be retained only in local build and verification records.
- [ ] Confirm the repository tree contains source, tests, templates, build scripts, and documentation, but no tracked `dist` artifact.

Required Release-description template:

```markdown
# 更新内容

## <按本次实际改动选择的子标题>

- <具体改动>

## 发布

- 文件：`E7auto_vX.Y.Z_x64.zip`
```

Onefile evaluation begins only after every standalone gate passes. Startup extraction behavior, antivirus reputation, data-file lookup, and signed-build behavior then require a separate test matrix.
