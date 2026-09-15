from __future__ import annotations

from scripts.common.image_io import write_png

from pathlib import Path
from scripts.common.paths import calibration_output_dirs, template_relative_path
import argparse

import cv2
import numpy as np
import yaml

from scripts.common.image_io import read_rgba_png as read_png
from scripts.common.paths import PROJECT_ROOT


SOURCE_TOKEN = "202205"
MAX_SATURATION = 20
MIN_VALUE = 140
MIN_COMPONENT_AREA = 800
EXPECTED_COMPONENTS = 5
CROP_PADDING = 3


def find_source(source_dir: Path) -> Path:
    matches = tuple(path for path in source_dir.glob("*.png") if SOURCE_TOKEN in path.stem)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one PNG containing {SOURCE_TOKEN!r}, found {len(matches)}"
        )
    return matches[0]


def header_foreground_mask(image: np.ndarray) -> tuple[np.ndarray, list[int]]:
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    seed = (
        (hsv[:, :, 1] <= MAX_SATURATION)
        & (hsv[:, :, 2] >= MIN_VALUE)
    ).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(seed, connectivity=8)
    selected = [
        component
        for component in range(1, count)
        if int(stats[component, cv2.CC_STAT_AREA]) >= MIN_COMPONENT_AREA
    ]
    if len(selected) != EXPECTED_COMPONENTS:
        raise RuntimeError(
            f"Expected {EXPECTED_COMPONENTS} arrow/title components, found {len(selected)}"
        )
    mask = np.zeros(seed.shape, dtype=np.uint8)
    for component in selected:
        mask[labels == component] = 255
    return mask, [int(stats[component, cv2.CC_STAT_AREA]) for component in selected]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract the Epic Seven shop-exit arrow and title without header background"
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
    )
    parser.add_argument("--manifest-dir", type=Path, help="Directory for calibration provenance")
    args = parser.parse_args()
    source = find_source(args.source_dir.resolve())
    output_dir, manifest_dir = calibration_output_dirs(args.output_dir, args.manifest_dir)

    image = read_png(source)
    mask, component_areas = header_foreground_mask(image)
    ys, xs = np.nonzero(mask)
    x0 = max(0, int(xs.min()) - CROP_PADDING)
    y0 = max(0, int(ys.min()) - CROP_PADDING)
    x1 = min(image.shape[1], int(xs.max()) + CROP_PADDING + 1)
    y1 = min(image.shape[0], int(ys.max()) + CROP_PADDING + 1)

    output_image = np.ascontiguousarray(image[y0:y1, x0:x1].copy())
    output_image[:, :, 3] = mask[y0:y1, x0:x1]
    output = output_dir / template_relative_path("shop_exit_icon.png", feature="shop")
    write_png(output, output_image)

    manifest = {
        "schema_version": 1,
        "method": "exact source RGB plus bright-neutral return-arrow and four-title components as binary alpha",
        "source_path": str(source),
        "source_size": {"width": int(image.shape[1]), "height": int(image.shape[0])},
        "crop": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
        "mask": {
            "color_space": "OpenCV HSV",
            "max_saturation": MAX_SATURATION,
            "min_value": MIN_VALUE,
            "min_component_area": MIN_COMPONENT_AREA,
            "component_areas": component_areas,
            "foreground_pixels": int(np.count_nonzero(output_image[:, :, 3])),
            "transparent_pixels": int(np.count_nonzero(output_image[:, :, 3] == 0)),
        },
        "output_path": output.relative_to(output_dir).as_posix(),
        "output_size": {"width": x1 - x0, "height": y1 - y0},
    }
    (manifest_dir / "shop_exit_icon_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
