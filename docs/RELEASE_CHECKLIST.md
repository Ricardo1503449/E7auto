# Windows x64 release checklist

## Build timing policy

Do not run Nuitka or rebuild `dist\launcher.dist` during incremental development. Use the project `.venv` for source tests, calibration, and separately authorized real-machine validation. Begin the release build only after all script functionality is complete and that live validation has succeeded. Any existing executable is a historical development artifact, not the final deliverable.

## Local standalone gate

- [ ] Run `.venv\Scripts\python.exe -m pytest`.
- [ ] Run `.venv\Scripts\python.exe scripts\verify_environment.py`.
- [ ] Build with `scripts\build-standalone.ps1` and project-local Nuitka cache.
- [ ] Confirm the build uses Nuitka `--windows-uac-admin` and the resulting PE manifest requests administrator elevation.
- [ ] Run `.venv\Scripts\python.exe scripts\verify_release.py`.
- [ ] Confirm `dist\launcher.dist` contains `E7auto.exe`, `使用说明.txt`, `config\internal.yaml`, and `assets\templates`.
- [ ] Confirm every manifest and all 28 described calibrated PNGs are present, decodable, structurally valid, and covered by the focused asset tests. This includes the separately manifested wide Sky Stone `0` variant, plus `client_calibration_manifest.yaml` for automatic initial cropping of five full-window sources and references to the separate insufficient-gold, overlay-position, and stage-two overlay-capture evidence.
- [ ] Confirm no `.venv`, `.pip-cache`, tests, logs, or runtime screenshots are included.
- [ ] Confirm packaged `config\internal.yaml` uses `logging.profile: compact`; source `config\internal.yaml` remains `detailed` for diagnostics.
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
- [x] Verify the production overlay supports click-through, no activation, and exact capture-exclusion read-back. Runtime startup and every F6 lock now require exclusion; historical fixed-size/fallback geometry remains evidence only.
- [ ] Verify bare F6 pauses automation, enables dragging, then saves position and restores click-through/capture exclusion before resuming; verify the saved position restores after restart and off-screen state falls back.
- [ ] Verify F5 can register only while running and is available again immediately after termination.
- [ ] Verify game movement, resize, minimize, disappearance, and focus loss terminate safely.
- [ ] Verify consecutive-identical top/bottom viewport scans, exactly one calibrated downward navigation per inventory, same-viewport rescans after purchase, completed-slot suppression, all targets/slots, target-specific purchase confirmation/success, insufficient currency, refresh secondary confirmation, automatic return to top after refresh, stable exact Sky Stone `-3` deduction, unchanged/mismatched/unreadable balance refusal, and exact-budget final scan using non-payment test conditions.
- [ ] Verify the adaptive no-target schedule with non-payment test conditions: `13 -> exit/5 s/re-enter -> 13 -> exit/180 s/re-enter -> 13 -> exit/5 s/re-enter -> 10 -> refresh_strategy_exhausted`, plus mandatory-target reset, Friendship Points non-reset, F5 during waits, and budget preemption before recovery.
- [x] With the `购买金币` prompt already visible and the game foreground, run the capture-only administrator validator. The completed run produced 5/5 detections at `0.9999991492`, `purchase_funds_insufficient`, zero input, no click on the prompt's `确认`, no persisted screenshot, and enabled `calibration_complete`.
- [ ] Confirm downward navigation uses exactly six `-120` wheel events at the calibrated product-area cursor with `100 ms` spacing; verify adaptive settling begins at `100 ms`, requires two stable pairwise observations, never waits beyond the `800 ms` ceiling before its final sample, and preserves the calibrated full-resolution total-movement gates. Confirm the last three settle frames either complete bottom recognition with `cache_outcome=hit` or retain the logged stable suffix and capture only missing frames; no fallback may restart from zero. Verify one complete `scroll_settle_trace` and one cache-annotated bottom performance stage per transition.
- [ ] Confirm no-click commissioning reproduces three-frame stable refresh control, Sky Stone OCR, and top/bottom inventory scans within the calibrated 3000 ms scan timeout. If no target is present, record that only stable-empty behavior was exercised.
- [ ] Verify logs are UTF-8 text, retention is bounded, and no screenshot files/directories appear.

## GitHub Release publishing

- [ ] Keep `dist\launcher.dist` and every versioned ZIP as local ignored build output; do not add either path to the Git repository.
- [ ] Create the versioned ZIP from the exact locally verified standalone directory and stream every entry to confirm successful decompression and CRC validation.
- [ ] Create the matching version tag and GitHub Release from the reviewed release commit.
- [ ] Upload only the verified versioned ZIP as the Release asset, then confirm its displayed size and downloadable filename.
- [ ] Confirm the repository tree contains source, tests, templates, build scripts, and documentation, but no tracked `dist` artifact.

Onefile evaluation begins only after every standalone gate passes. Startup extraction behavior, antivirus reputation, data-file lookup, and signed-build behavior then require a separate test matrix.
