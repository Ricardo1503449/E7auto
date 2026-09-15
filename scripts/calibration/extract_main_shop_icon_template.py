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
STROKE_HOLE_MAX_SATURATION = 32
STROKE_HOLE_MIN_VALUE = 180
EDGE_SMOOTHING_SIGMA = 0.65


def restore_question_mark_strokes(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Restore pale-blue stroke pixels rejected by the white foreground seed.

    Only enclosed transparent components within the question marks qualify.
    The natural openings connect to exterior transparency; text counters and
    exterior wallpaper are never filled.
    """
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (mask == 0).astype(np.uint8), connectivity=8
    )
    rx, ry, rw, rh = QUESTION_MARK_ROI
    restored = mask.copy()
    for component in range(1, count):
        x, y, width, height, _ = stats[component]
        if not (rx <= x and ry <= y and x + width <= rx + rw and y + height <= ry + rh):
            continue
        pixels = labels == component
        if np.all(hsv[:, :, 1][pixels] <= STROKE_HOLE_MAX_SATURATION) and np.all(
            hsv[:, :, 2][pixels] >= STROKE_HOLE_MIN_VALUE
        ):
            restored[pixels] = 255
    return restored


def main_screen_foreground_mask(image: np.ndarray) -> np.ndarray:
    seed = foreground_mask(image)
    b, g, r = (image[:, :, channel].astype(np.int16) for channel in range(3))
    outline = (b >= r - 8) & (np.abs(b - g) <= 25) & (np.maximum(np.maximum(b, g), r) < 170)
    mask = np.where((seed > 0) | ((cv2.dilate(seed, np.ones((3, 3), np.uint8)) > 0)
                                 & outline), 255, 0).astype(np.uint8)
    mask = restore_question_mark_strokes(image, mask)
    # Feather inward only: never expose wallpaper or fill natural glyph openings.
    softened = cv2.GaussianBlur(mask, (3, 3), EDGE_SMOOTHING_SIGMA, borderType=cv2.BORDER_CONSTANT)
    return np.where(mask > 0, softened, 0).astype(np.uint8)


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
    x0 = max(0, int(xs.min()) - CROP_PADDING)
    y0 = max(0, int(ys.min()) - CROP_PADDING)
    x1 = min(image.shape[1], int(xs.max()) + CROP_PADDING + 1)
    y1 = min(image.shape[0], int(ys.max()) + CROP_PADDING + 1)

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
        manifest["client_crop"] = dict(zip(("x", "y", "width", "height"), client_crop))
        manifest["mask"]["glyph_roi"] = dict(zip(("x", "y", "width", "height"), MAIN_SCREEN_ROI))
        manifest["mask"]["outline"] = {"radius": 1, "min_blue_minus_red": -8,
                                      "max_blue_green_difference": 25, "max_value_exclusive": 170}
        manifest["mask"]["question_stroke_repair"] = {
            "roi": dict(zip(("x", "y", "width", "height"), QUESTION_MARK_ROI)),
            "enclosed_components_only": True,
            "max_saturation": STROKE_HOLE_MAX_SATURATION,
            "min_value": STROKE_HOLE_MIN_VALUE,
        }
        manifest["mask"]["edge_smoothing"] = {
            "kernel_size": [3, 3], "sigma": EDGE_SMOOTHING_SIGMA,
            "inward_only": True,
            "partially_transparent_pixels": int(np.count_nonzero(
                (output_image[:, :, 3] > 0) & (output_image[:, :, 3] < 255)
            )),
        }
    (ensure_candidate_path(manifest_dir / "main_shop_icon_manifest.yaml")).write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
