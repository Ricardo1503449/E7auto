from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import cv2
import numpy as np
import yaml


SOURCE_TOKEN = "202223"
EXPECTED_SOURCE_SHA256 = "00578e2b0fc0d3d7f29495471f6d54ce299d7dfac72694644af2a8b63be19ae8"
MAX_SATURATION = 20
MIN_VALUE = 80
MIN_COMPONENT_AREA = 30
BORDER_EXCLUSION = 5
CROP_PADDING = 3


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_source(source_dir: Path) -> Path:
    matches = tuple(path for path in source_dir.glob("*.png") if SOURCE_TOKEN in path.stem)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one PNG containing {SOURCE_TOKEN!r}, found {len(matches)}"
        )
    source = matches[0]
    actual_hash = sha256(source)
    if actual_hash != EXPECTED_SOURCE_SHA256:
        raise RuntimeError(
            f"Unexpected source fingerprint for {source}: {actual_hash}"
        )
    return source


def read_png(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        raise RuntimeError(f"Expected an RGBA PNG: {path}")
    return image


def write_png(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError(f"Cannot encode PNG: {path}")
    encoded.tofile(path)


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
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path.home() / "Pictures" / "Screenshots",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "assets" / "templates",
    )
    args = parser.parse_args()
    source = find_source(args.source_dir.resolve())
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    image = read_png(source)
    mask = foreground_mask(image)
    ys, xs = np.nonzero(mask)
    x0 = max(0, int(xs.min()) - CROP_PADDING)
    y0 = max(0, int(ys.min()) - CROP_PADDING)
    x1 = min(image.shape[1], int(xs.max()) + CROP_PADDING + 1)
    y1 = min(image.shape[0], int(ys.max()) + CROP_PADDING + 1)

    output_image = np.ascontiguousarray(image[y0:y1, x0:x1].copy())
    output_image[:, :, 3] = mask[y0:y1, x0:x1]
    output = output_dir / "main_shop_icon.png"
    write_png(output, output_image)

    manifest = {
        "schema_version": 1,
        "method": "exact source RGB plus deterministic binary alpha foreground mask",
        "source_path": str(source),
        "source_size": {"width": int(image.shape[1]), "height": int(image.shape[0])},
        "source_sha256": sha256(source),
        "crop": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
        "mask": {
            "color_space": "OpenCV HSV",
            "max_saturation": MAX_SATURATION,
            "min_value": MIN_VALUE,
            "min_component_area": MIN_COMPONENT_AREA,
            "border_exclusion": BORDER_EXCLUSION,
            "foreground_pixels": int(np.count_nonzero(output_image[:, :, 3])),
            "transparent_pixels": int(np.count_nonzero(output_image[:, :, 3] == 0)),
        },
        "output_path": output.name,
        "output_size": {"width": x1 - x0, "height": y1 - y0},
        "output_sha256": sha256(output),
    }
    (output_dir / "main_shop_icon_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
