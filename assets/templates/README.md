# 模板目录

后续新增功能必须遵循[模板资源组织与新增功能约定](../../docs/TEMPLATES.md)。提取工具默认仅输出候选；正式替换通过[统一登记流程](../../docs/TEMPLATE_WORKFLOW.md)同步图片、运行清单和来源记录。

| 目录 | 内容 |
| --- | --- |
| `shop/` | 18张商店专用模板和运行清单 `manifest.json` |
| `common/` | 数字及网络模板的统一运行清单 `manifest.json` |
| `common/digits/` | 11张共用数字模板 |
| `common/network/` | 2张共用网络异常/重试模板 |
| `penguin/` | 13张企鹅专用模板和运行必需的 `manifest.json` |

PNG按功能和实际复用关系存放；根目录不再接收新图片。下文商店图片文件名相对于 `shop/`，数字和网络图片分别在对应的 `common/` 子目录。历史校准清单均位于 [docs/calibration](../../docs/calibration/)，其 `output_path` 相对于本模板根目录。每份运行清单的 `file` 相对于该清单所在目录。运行阈值及最终搜索区域均在 `config/internal.yaml` 中维护，清单只登记文件、来源和完整性。

## Calibrated templates

The inventory templates retain the exact RGB pixels and dimensions cropped from nine user-provided reference PNGs at the confirmed `2322 x 1306` physical-client scale. All nine use binary alpha masks to exclude the outer dark-gray frame and exterior background, retaining the inner frame, enclosed artwork/background, and complete quantity digits. The three target resources each have unpurchased, confirmation-dialog, and purchased-state templates. `confirm_button.png` is an additional unchanged common button anchor. Runtime uses its existing masked template-matching path for these nine assets.

`main_shop_icon.png` is extracted from the supplied dark-background main-screen reference `codex-clipboard-713efead-f496-4726-bc3a-dd851974cdc1.png`. Its RGB pixels remain exact source pixels, while a deterministic alpha mask keeps only `??` and `秘密商店`, including their adjacent neutral outlines. Foreground components are extracted within the calibrated main-shop ROI; exterior wallpaper pixels are transparent. The mask is feathered inward at its edges with a 3x3 Gaussian kernel (sigma 0.65), preserving its original nonzero support and fully transparent openings. Reproduce this reference with `extract_main_shop_icon_template --source <full-screenshot.png>`; the historical `--source-dir` mode remains available for the original cropped source.

The main-screen extractor restores enclosed pale-blue holes inside the question-mark strokes that the strict white seed can miss. This repair is limited to the question-mark region and bright, low-saturation source pixels; natural openings, caption counters, and exterior transparency are preserved.

`shop_refresh_button.png` retains the complete rounded refresh control from the supplied in-shop reference: Sky Stone icon, fixed cost `3`, and `立即更新`. Only pixels outside the rounded button silhouette are transparent.

`shop_exit_icon.png` retains both the left return arrow and `秘密商店` title from the supplied shop header. The dark header background is fully transparent and contributes no confidence.

`refresh_confirm_prompt.png` keeps only `要消耗天空石立即更新吗？`; `refresh_confirm_button.png` keeps the complete rounded blue `确认` control. Desktop margins, underlying inventory, and modal background outside those two evidence regions are transparent.

`insufficient_funds.png` keeps the `购买金币` title and its explanatory text from the third supplied insufficient-gold screenshot. All three sequence screenshots are independently cropped in memory to the exact `2322 x 1306` game client first; the first two document the trigger path and are not negative samples. The runtime searches the client-relative `(975,210,400,300)` region and terminates as `purchase_funds_insufficient` without clicking the prompt's `确认` action.

`purchased_button.png` is the shared disabled `0/1 购买` button for all three targets, approved as preview v4. Its original `331 x 98` RGB pixels are preserved; exterior background is transparent, the uneven top scanline is removed, and the four corners have antialiased alpha. After the target identity and purchase confirmation have been checked, runtime searches only around that slot's buy point, with 18 px horizontal and 16 px vertical padding at baseline scale. If the purchased item icon is missed, button matches at the anchor threshold (`0.93`) must persist for the configured stable-frame count (`3`) to confirm and count the purchase. Insufficient-funds evidence takes precedence. Inventory scanning does not use this button to infer target identity or count pre-existing purchases. `purchased_button_manifest.json` records the source and screenshot checks.

`network_connection_abnormal.png` and `network_retry.png` are alpha-masked text-only templates extracted from the second supplied network-error screenshot. The first keeps only `网络连接异常，请重新连接。`; the second keeps only `点击重试` and excludes the arrow and shop background. Their transparent pixels are ignored by the OpenCV matcher.

`insufficient_funds_live_validation_manifest.yaml` records the foreground administrator capture-only validation: five consecutive detections at confidence `0.9999991492`, unchanged client geometry, zero input, no terminal-confirm click, and no screenshot persistence.

`sky_stone_icon.png` is an exact opaque `62 x 75` source-pixel crop containing the complete top-bar Sky Stone gem and adjacent `+` marker. No HSV masking or transparency is applied. Separate deterministic alpha-masked glyph templates cover all digits `0-9`; the original narrow `0` comes from the repeated `3900` glyphs, `4` from the full-scale gold count in the combined top-bar source and is checked against `3924`, and `6` comes from `3867`. `sky_stone_manifest.yaml` records those sources and validations. `sky_stone_digit_0_wide.png` is a second source-derived `0` variant extracted from the first zero in the supplied gold balance `11,120,980`; it matches the adjacent Sky Stone `4501` zero at `0.993266`. `sky_stone_zero_wide_manifest.yaml` records the desktop source, automatic `(44,124,2322,1306)` client crop, and component geometry. Runtime retains both zero variants and uses their best score. The icon is located in a full-width top-bar-only search band; its right-side balance parser accepts a contiguous digit run of any visible length and stops before distant header controls.

`client_calibration_manifest.yaml` records five full-window sources. Paired edge gradients first remove the title bar and desktop background, yielding exact `2322 x 1306` client crops at `(49,108)`, `(42,101)`, `(49,111)`, `(31,90)`, and `(32,125)`. The full client images are processed only in memory. The manifest stores verified entry/refresh/dialog/Sky Stone positions, OCR evidence, purchase-row geometry, and the partial config values without persisting a screenshot copy.

`overlay_position_calibration_manifest.yaml` records the historical operator-confirmed `18 px` geometry and the client-relative offset `(-252,-145)`, which is retained as the first-launch/fallback default. Current runtime dragging persists an absolute screen position separately; the historical fixed rectangle is not a current runtime placement constraint.

`manifest.yaml` records every source path, crop rectangle, output size, and channel count. The entry points below show their required arguments with `--help`. Supply the original images explicitly before reproducing assets; see the [input table](../../docs/DEVELOPMENT.md#校准工具输入). Historical manifest paths are provenance, not portable default inputs:

```powershell
.\.venv\Scripts\python.exe -m scripts.calibration.crop_calibration_templates --help
.\.venv\Scripts\python.exe -m scripts.calibration.extract_main_shop_icon_template --help
.\.venv\Scripts\python.exe -m scripts.calibration.extract_shop_refresh_button_template --help
.\.venv\Scripts\python.exe -m scripts.calibration.extract_shop_exit_icon_template --help
.\.venv\Scripts\python.exe -m scripts.calibration.extract_refresh_confirm_templates --help
.\.venv\Scripts\python.exe -m scripts.calibration.extract_insufficient_funds_template --help
.\.venv\Scripts\python.exe -m scripts.calibration.extract_sky_stone_templates --help
.\.venv\Scripts\python.exe -m scripts.calibration.extract_sky_stone_zero_wide_template --help
.\.venv\Scripts\python.exe -m scripts.calibration.calibrate_client_frames --help
```

The scripts perform no generative editing. RGB values remain exact source pixels; deterministic alpha masks exclude irrelevant background, and digit shapes are normalized only in memory during recognition. These are offline calibration utilities for explicitly supplied images. Captured runtime frames are never written here or anywhere else.

`penguin/` contains the 13 user-approved v2 controls for penguin exchange. All PNGs retain exact source RGB and binary alpha; button exteriors are transparent and reward-close contains text only. `penguin/manifest.json` records baseline 2322 x 1306 geometry and SHA-256 for runtime integrity validation. The application validates these assets only when starting penguin exchange, so shop-only runs do not depend on them. No generated artwork or live screenshot capture is used.
