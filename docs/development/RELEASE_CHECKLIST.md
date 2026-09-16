# Windows x64 release checklist

## Build timing policy

Do not run Nuitka or rebuild `dist\launcher.dist` during incremental development. Use the project `.venv` for source tests, calibration, and separately authorized real-machine validation. Begin the release build only after all script functionality is complete and that live validation has succeeded. Any existing executable is a historical development artifact, not the final deliverable.

## Local standalone gate

- [ ] When the full suite is explicitly authorized, run `powershell -ExecutionPolicy Bypass -File scripts\test-source.ps1`; Qt and WGC tests must pass in one pytest process, including the controlled native import-order/lifecycle regressions.
- [ ] Run `.venv\Scripts\python.exe -m scripts.verify_environment`.
- [ ] Build with `scripts\release\build-standalone.ps1` and project-local Nuitka cache.
- [ ] Confirm native-runtime normalization completes before publication; root and nested MSVC DLLs must meet the locked Qt/Shiboken minimum. Do not retain the old WinRT-private MSVCP140 or an older compiler runtime at the distribution root.
- [ ] Confirm the build uses Nuitka `--windows-uac-admin` and the resulting PE manifest requests administrator elevation.
- [ ] Confirm Windows file/product versions are the four-part numeric form of the `pyproject.toml` version, and `E7auto.exe --self-check` reports the same package version.
- [ ] Run `.venv\Scripts\python.exe -m scripts.release.verify_release`.
- [ ] Confirm `dist\launcher.dist` contains `E7auto.exe`, `使用说明.txt`, `config\internal.yaml`, `assets\templates`, and `assets\ui`.
- [ ] Confirm the executable uses `assets\ui\e7auto.ico`; all required 16-1024 PNG sizes, the multi-size ICO, and `shop-card-background.png` are present and pass `verify_ui_assets`.
- [ ] Confirm every template referenced by the release configuration and its provenance manifest is present, decodable, structurally valid, and covered by focused asset tests. Include the nine masked item-state templates, the repaired/smoothed main-shop icon, and `purchased_button.png` / `purchased_button_manifest.json`; verify packaged bytes match the reviewed source assets. Source provenance remains in `docs/calibration/` and is used by the release verifier without being bundled. Include the wide-zero variant and all 13 penguin controls with their runtime `penguin/manifest.json`; validate their hashes. Only PNGs and runtime manifests belong in the distributed template hierarchy.
- [ ] Run the focused template repository tests (`tests/resources/test_template_repository.py`): English paths with spaces and Chinese paths must preserve BGR pixels and alpha masks; missing, empty, corrupt, grayscale, and fully transparent templates must be rejected.
- [ ] Confirm no `.venv`, `.pip-cache`, tests, logs, or runtime screenshots are included.
- [ ] Confirm source and packaged logging use the unified defaults: 7 days, 20 runs, 10 MB per file, 3 backups, 500 MB total target; no logging mode selector.
- [ ] Confirm the PE machine is AMD64. Do not label the build ARM64-compatible.

## v1.3.3 local build

The local `E7auto_v1.3.3_x64.zip` and standalone directory have been rebuilt from commit `99435b8`, with Windows file/product version `1.3.3.0`. Native-runtime normalization, resource/archive consistency and the elevated compiled self-check passed; Qt/WGC DLLs meet the locked runtime minimum. The packaged usage guide was synchronized afterward and the ZIP regenerated without recompiling executable code. Details and final archive measurements are in [v1.3.3 validation](../releases/v1.3.3/VALIDATION.md). The user subsequently authorized [GitHub Release v1.3.3](https://github.com/Ricardo1503449/E7auto/releases/tag/v1.3.3), tagged at `cf1ba67`; the uploaded ZIP name, size and server digest match the verified local archive. No new full-suite run, game-operation validation or clean-Windows validation was performed.

## v1.3.2 local rebuild (historical)

The v1.3.2 source targeted `E7auto_v1.3.2_x64.zip`. The records below describe that version's original scope and do not validate v1.3.3. That local rebuild did not include GitHub publishing.

- [x] Initial 2026-09-15 build: version/environment checks and 50 focused tests passed. The 2026-09-16 same-version rebuild uses commit `1d0a198` with the masked Sky Stone icon; prior relevant tests were reused and environment verification passed again.
- [x] Latest local standalone directory and ZIP rebuilt: 83,424,277-byte ZIP, 154 files match dist by CRC/read and SHA-256, 48 template-directory files and 11 UI assets match source. Full current and historical evidence: [v1.3.2 build validation](../releases/v1.3.2/VALIDATION.md).
- [x] Verified AMD64, Windows version 1.3.2.0 and requireAdministrator. Elevated release verification passed with problems=[]; compiled self-check reports version 1.3.2 and WGC importable.

## v1.3.1 recognition changes (historical)

The v1.3.1 source targeted `E7auto_v1.3.1_x64.zip`. Source tests and supplied-screenshot checks are recorded in [the validation report](../validation/shop/SHOP_RECOGNITION_VALIDATION.md); the local v1.3.1 directory and ZIP have now been built and checked for static/resource/archive integrity. After the user requested another Windows UAC prompt, elevated release verification and the compiled self-check passed (problems=[], version 1.3.1, AMD64, WGC importable); no GitHub Release has been published.

- [x] Verify the new assets are included by `--include-data-dir=assets/templates=assets/templates` and the package uses the current configuration (59 template-directory files match source, 31 configured templates decode, 165 ZIP files match dist).
- [x] Complete the elevated compiled `--self-check`: the user-approved UAC retry passed, problems=[], version 1.3.1, AMD64, WGC importable. Static 1.3.1.0 / requireAdministrator checks passed.
- [ ] During separately authorized shop validation, exercise button-only success for the original purchase row, three consecutive detections, another-row rejection, no counting for pre-existing sold-out items, insufficient-funds precedence, and result timeout without retry/count.
- [ ] Validate the main-shop icon against differing wallpapers and supported client sizes on the target machine.

## Clean Windows x64 gate

Use a Windows x64 machine or VM with no Python installed.

- [ ] Copy only the standalone directory.
- [ ] Double-click `E7auto.exe`; verify Windows shows UAC. Rejecting UAC must prevent startup and all input. Accepting it must produce an elevated/High-integrity process able to interact with the observed High-integrity game.
- [ ] Run `E7auto.exe --self-check` and retain its text output.
- [ ] During separately authorized functional validation, launch a complete standalone copy under a Chinese path with spaces (for example, `E:\E7 商店脚本\E7auto`) and click Start; confirm template loading succeeds without `Cannot load template`. Moving only the EXE is not a valid check, and `--self-check` checks template-directory presence without decoding templates.
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
- [ ] Confirm downward navigation uses exactly six `-120` client-area window-message wheel events at `10 ms` spacing (`scroll.interval_ms`), with five configured sleeps totaling `50 ms`; verify adaptive settling begins at `100 ms`, requires two stable pairwise observations, never waits beyond the `800 ms` ceiling before its final sample, rejects stale visually stable frames until the full-resolution movement gate passes, and preserves the calibrated displacement thresholds. For the dedicated scroll validator, explicitly pass `--interval-ms 10` from an administrator PowerShell as documented in `BACKGROUND_VALIDATION.md`; the wrapper still defaults to `100 ms`. Confirm the last three settle frames either complete bottom recognition with `cache_outcome=hit` or retain the logged stable suffix and capture only missing frames; no fallback may restart from zero.
- [ ] Confirm no-click commissioning reproduces three-frame stable refresh control, Sky Stone OCR, and top/bottom inventory scans within the calibrated 3000 ms scan timeout. If no target is present, record that only stable-empty behavior was exercised.
- [ ] Verify logs are UTF-8 text and retention is bounded. Normal completion and F5 produce no snapshot; an abnormal stop may save at most one cached game frame as run-…-stop.png alongside its log, without another capture. Include this image in run-group retention.

## GitHub Release publishing

- [ ] Keep CHANGELOG focused on actual version changes; store build/test/publication status in `docs/releases/<version>/`. The public Release download section below does not belong in CHANGELOG.
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

- 模板更新须经 `scripts.templates.register prepare/apply` 登记；发布目录的图片列表以运行清单为准，来源记录在源码 `docs/calibration/registrations` 中校验哈希，不要求打包历史记录。发布前不得有未恢复的登记事务。
