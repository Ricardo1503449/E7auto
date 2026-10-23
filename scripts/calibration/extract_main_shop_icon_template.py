from __future__ import annotations

from scripts.common.candidates import write_candidate_png as write_png
from scripts.common.paths import ensure_candidate_path

from pathlib import Path
from scripts.common.paths import calibration_output_dirs, template_relative_path
import argparse

import cv2
import numpy as np
import yaml

from scripts.common.image_io import read_rgba_png as read_png
from scripts.common.paths import PROJECT_ROOT
from scripts.calibration.calibrate_client_frames import locate_client_crop


SOURCE_TOKEN = "202223"
MAX_SATURATION = 20
MIN_VALUE = 80
MIN_COMPONENT_AREA = 30
BORDER_EXCLUSION = 5
CROP_PADDING = 3
MAIN_SCREEN_ROI = (25, 525, 165, 175)
QUESTION_MARK_ROI = (35, 40, 90, 75)
ICON_MAX_SATURATION = 64
ICON_MIN_VALUE = 120
ICON_MIN_COMPONENT_AREA = 20
QUESTION_BORDER_REPAIRS = {
    "left_question_top": (49, 47, 12, 14),
    "right_question_top": (82, 46, 26, 12),
    "right_dot_left": (70, 93, 12, 17),
}


def _question_components(raw: np.ndarray) -> np.ndarray:
    rx, ry, rw, rh = QUESTION_MARK_ROI
    count, labels, stats, _ = cv2.connectedComponentsWithStats(raw, connectivity=8)
    seed = np.zeros(raw.shape, dtype=np.uint8)
    retained = 0
    for index in range(1, count):
        x, y, width, height, area = stats[index]
        if (area >= ICON_MIN_COMPONENT_AREA and rx < x and ry < y
                and x+width < rx+rw and y+height < ry+rh):
            seed[labels == index] = 255
            retained += 1
    if retained != 4:
        raise ValueError('Expected two complete question marks and their two dots')
    return seed


def main_screen_foreground_mask(image: np.ndarray) -> np.ndarray:
    # Extract the question marks and dots from source RGB; their pale-blue edge
    # pixels must not be discarded by the old caption-oriented white-only seed.
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    b, g, r = (image[:, :, channel].astype(np.int16) for channel in range(3))
    region = np.zeros(image.shape[:2], dtype=np.uint8)
    rx, ry, rw, rh = QUESTION_MARK_ROI
    region[ry:ry+rh, rx:rx+rw] = 1
    raw = ((hsv[:, :, 1] <= ICON_MAX_SATURATION)
           & (hsv[:, :, 2] >= ICON_MIN_VALUE) & (g >= r - 3)
           & (region > 0)).astype(np.uint8)
    seed = _question_components(raw)
    outline = ((b >= r - 8) & (g >= r - 3) & (np.abs(b - g) <= 25)
               & (np.maximum(np.maximum(b, g), r) < 170))
    mask = np.where((seed > 0) | ((cv2.dilate(seed, np.ones((3, 3), np.uint8)) > 0)
                                 & outline & (region > 0)), 255, 0).astype(np.uint8)
    # The reviewed source has tinted antialiasing in these three border sections.
    # Re-extract those strokes from source RGB, then retain their one-pixel border.
    # Preserve the original colors instead of painting a uniform black outline.
    raw = ((hsv[:, :, 1] <= ICON_MAX_SATURATION)
           & (hsv[:, :, 2] >= ICON_MIN_VALUE) & (region > 0)).astype(np.uint8)
    source_border = cv2.dilate(_question_components(raw), np.ones((3, 3), np.uint8))
    for x, y, width, height in QUESTION_BORDER_REPAIRS.values():
        selected = source_border[y:y+height, x:x+width] > 0
        mask[y:y+height, x:x+width][selected] = 255
    # Source RGB already includes antialiasing. Preserve it without re-feathering.
    return mask


def find_source(source_dir: Path) -> Path:
    matches = tuple(path for path in source_dir.glob("*.png") if SOURCE_TOKEN in path.stem)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one PNG containing {SOURCE_TOKEN!r}, found {len(matches)}"
        )
    return matches[0]


def foreground_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    seed = (
        (hsv[:, :, 1] <= MAX_SATURATION)
        & (hsv[:, :, 2] >= MIN_VALUE)
    ).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(seed, connectivity=8)
    mask = np.zeros(seed.shape, dtype=np.uint8)
    height, width = seed.shape
    for component in range(1, count):
        x, y, component_width, component_height, area = stats[component]
        away_from_border = (
            x > BORDER_EXCLUSION
            and y > BORDER_EXCLUSION
            and x + component_width < width - BORDER_EXCLUSION
            and y + component_height < height - BORDER_EXCLUSION
        )
        if area >= MIN_COMPONENT_AREA and away_from_border:
            mask[labels == component] = 255
    if not np.any(mask):
        raise RuntimeError("Foreground extraction produced an empty mask")
    return mask


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract a wallpaper-independent Epic Seven main-shop icon template"
    )
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--source", type=Path, help="Full baseline-scale main-screen screenshot")
    sources.add_argument(
        "--source-dir",
        type=Path,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
    )
    parser.add_argument("--manifest-dir", type=Path, help="Candidate provenance directory (default: candidate output directory)")
    args = parser.parse_args()
    source = args.source.resolve() if args.source else find_source(args.source_dir.resolve())
    output_dir, manifest_dir = calibration_output_dirs(args.output_dir, args.manifest_dir)

    original = read_png(source)
    client_crop = None
    offset_x = offset_y = 0
    if args.source:
        client_crop = locate_client_crop(original)
        cx, cy, _, _ = client_crop
        rx, ry, rw, rh = MAIN_SCREEN_ROI
        offset_x, offset_y = cx + rx, cy + ry
        image = original[offset_y:offset_y + rh, offset_x:offset_x + rw]
        mask = main_screen_foreground_mask(image)
    else:
        image = original
        mask = foreground_mask(image)
    ys, xs = np.nonzero(mask)
    padding = 0 if args.source else CROP_PADDING
    x0 = max(0, int(xs.min()) - padding)
    y0 = max(0, int(ys.min()) - padding)
    x1 = min(image.shape[1], int(xs.max()) + padding + 1)
    y1 = min(image.shape[0], int(ys.max()) + padding + 1)

    output_image = np.ascontiguousarray(image[y0:y1, x0:x1].copy())
    output_image[:, :, 3] = mask[y0:y1, x0:x1]
    output = output_dir / template_relative_path("main_shop_icon.png", feature="shop")
    write_png(output, output_image)

    manifest = {
        "schema_version": 1,
        "method": "exact source RGB plus deterministic alpha foreground mask",
        "source_path": str(source),
        "source_size": {"width": int(original.shape[1]), "height": int(original.shape[0])},
        "crop": {"x": offset_x + x0, "y": offset_y + y0, "width": x1 - x0, "height": y1 - y0},
        "mask": {
            "color_space": "OpenCV HSV",
            "max_saturation": MAX_SATURATION,
            "min_value": MIN_VALUE,
            "min_component_area": MIN_COMPONENT_AREA,
            "border_exclusion": BORDER_EXCLUSION,
            "foreground_pixels": int(np.count_nonzero(output_image[:, :, 3])),
            "transparent_pixels": int(np.count_nonzero(output_image[:, :, 3] == 0)),
        },
        "output_path": output.relative_to(output_dir).as_posix(),
        "output_size": {"width": x1 - x0, "height": y1 - y0},
    }
    if client_crop is not None:
        manifest["mask"].update(max_saturation=ICON_MAX_SATURATION,
                                min_value=ICON_MIN_VALUE,
                                min_component_area=ICON_MIN_COMPONENT_AREA)
        manifest["mask"].pop("border_exclusion")
        manifest["client_crop"] = dict(zip(("x", "y", "width", "height"), client_crop))
        manifest["mask"]["glyph_roi"] = dict(zip(("x", "y", "width", "height"), MAIN_SCREEN_ROI))
        manifest["mask"]["outline"] = {"radius": 1, "min_blue_minus_red": -8,
                                      "min_green_minus_red": -3,
                                      "max_blue_green_difference": 25, "max_value_exclusive": 170}
        manifest["mask"]["question_strokes"] = {
            "roi": dict(zip(("x", "y", "width", "height"), QUESTION_MARK_ROI)),
            "source_rgb_reextracted": True, "components": 4,
            "caption_removed": True, "original_antialiasing_preserved": True,
            "extra_feathering": False,
            "source_border_repairs": QUESTION_BORDER_REPAIRS,
        }
    (ensure_candidate_path(manifest_dir / "main_shop_icon_manifest.yaml")).write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
