# Calibrated templates

The inventory templates are exact-pixel crops extracted from nine user-provided reference PNGs at the confirmed `2322 x 1306` physical-client scale. The three target resources each have unpurchased, confirmation-dialog, and purchased-state templates. `confirm_button.png` is an additional common button anchor.

`main_shop_icon.png` is extracted from the separately supplied main-screen reference. Its RGB pixels remain exact source pixels, while a deterministic binary alpha mask keeps only `??` and `秘密商店`. The matcher uses that alpha channel so user-selected wallpaper contributes no confidence.

`shop_refresh_button.png` retains the complete rounded refresh control from the supplied in-shop reference: Sky Stone icon, fixed cost `3`, and `立即更新`. Only pixels outside the rounded button silhouette are transparent.

`shop_exit_icon.png` retains both the left return arrow and `秘密商店` title from the supplied shop header. The dark header background is fully transparent and contributes no confidence.

`refresh_confirm_prompt.png` keeps only `要消耗天空石立即更新吗？`; `refresh_confirm_button.png` keeps the complete rounded blue `确认` control. Desktop margins, underlying inventory, and modal background outside those two evidence regions are transparent.

`insufficient_funds.png` keeps the `购买金币` title and its explanatory text from the third supplied insufficient-gold screenshot. All three sequence screenshots are independently cropped in memory to the exact `2322 x 1306` game client first; the first two document the trigger path and are not negative samples. The runtime searches the client-relative `(975,210,400,300)` region and terminates as `purchase_funds_insufficient` without clicking the prompt's `确认` action.

`network_connection_abnormal.png` and `network_retry.png` are alpha-masked text-only templates extracted from the second supplied network-error screenshot. The first keeps only `网络连接异常，请重新连接。`; the second keeps only `点击重试` and excludes the arrow and shop background. Their transparent pixels are ignored by the OpenCV matcher.

`insufficient_funds_live_validation_manifest.yaml` records the foreground administrator capture-only validation: five consecutive detections at confidence `0.9999991492`, unchanged client geometry, zero input, no terminal-confirm click, and no screenshot persistence.

`sky_stone_icon.png` is an exact opaque `62 x 75` source-pixel crop containing the complete top-bar Sky Stone gem and adjacent `+` marker. No HSV masking or transparency is applied. Separate deterministic alpha-masked glyph templates cover all digits `0-9`; the original narrow `0` comes from the repeated `3900` glyphs, `4` from the full-scale gold count in the combined top-bar source and is checked against `3924`, and `6` comes from `3867`. `sky_stone_manifest.yaml` records those sources and validations. `sky_stone_digit_0_wide.png` is a second source-derived `0` variant extracted from the first zero in the supplied gold balance `11,120,980`; it matches the adjacent Sky Stone `4501` zero at `0.993266`. `sky_stone_zero_wide_manifest.yaml` records the desktop source, automatic `(44,124,2322,1306)` client crop, and component geometry. Runtime retains both zero variants and uses their best score.

`client_calibration_manifest.yaml` records five full-window sources. Paired edge gradients first remove the title bar and desktop background, yielding exact `2322 x 1306` client crops at `(49,108)`, `(42,101)`, `(49,111)`, `(31,90)`, and `(32,125)`. The full client images are processed only in memory. The manifest stores verified entry/refresh/dialog/Sky Stone positions, OCR evidence, purchase-row geometry, and the partial config values without persisting a screenshot copy.

`overlay_position_calibration_manifest.yaml` records the historical operator-confirmed `18 px` geometry and the client-relative offset `(-252,-145)`, which is retained as the first-launch/fallback default. Current runtime F6 movement persists an absolute screen position separately; the historical fixed rectangle is not a current runtime placement constraint.

`overlay_capture_validation_manifest.yaml` records that completed later stage: foreground/fixed-geometry checks, exact display-affinity read-back `17`, operator-observed visible positive control, no game input or persisted screenshots, and source-level post-Phase-32/Phase-37 reassessments proving the current 17 configured ROI/slot rectangles remain outside the overlay.

`manifest.yaml` records every source path, crop rectangle, output size, and channel count. Reproduce the files with:

```powershell
.\.venv\Scripts\python.exe scripts\crop_calibration_templates.py
.\.venv\Scripts\python.exe scripts\extract_main_shop_icon_template.py
.\.venv\Scripts\python.exe scripts\extract_shop_refresh_button_template.py
.\.venv\Scripts\python.exe scripts\extract_shop_exit_icon_template.py
.\.venv\Scripts\python.exe scripts\extract_refresh_confirm_templates.py
.\.venv\Scripts\python.exe scripts\extract_insufficient_funds_template.py
.\.venv\Scripts\python.exe scripts\extract_sky_stone_templates.py
.\.venv\Scripts\python.exe scripts\extract_sky_stone_zero_wide_template.py
.\.venv\Scripts\python.exe scripts\calibrate_client_frames.py
```

The scripts perform no generative editing. RGB values remain exact source pixels; deterministic alpha masks exclude irrelevant background, and digit shapes are normalized only in memory during recognition. These are offline calibration utilities for explicitly supplied images. Captured runtime frames are never written here or anywhere else.
