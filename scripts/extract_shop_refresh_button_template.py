from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import cv2
import numpy as np
import yaml


SOURCE_TOKEN = "200332"
EXPECTED_SOURCE_SHA256 = "a52a98445550d0fa936f414339038b7f0424048088cc6e6919bca61d4360c412"
HSV_LOWER = np.array([45, 50, 20], dtype=np.uint8)
HSV_UPPER = np.array([85, 255, 255], dtype=np.uint8)
MIN_CONTOUR_AREA = 60_000.0


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
        raise RuntimeError(f"Unexpected source fingerprint for {source}: {actual_hash}")
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


def button_contour(image: np.ndarray) -> tuple[np.ndarray, float]:
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, HSV_LOWER, HSV_UPPER)
    contours, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise RuntimeError("Refresh-button extraction found no green contour")
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    if area < MIN_CONTOUR_AREA:
        raise RuntimeError(f"Refresh-button contour is unexpectedly small: {area}")
    return contour, area


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract the complete rounded Epic Seven refresh-button template"
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
    contour, area = button_contour(image)
    x, y, width, height = cv2.boundingRect(contour)
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, thickness=cv2.FILLED)

    output_image = np.ascontiguousarray(image[y : y + height, x : x + width].copy())
    output_image[:, :, 3] = mask[y : y + height, x : x + width]
    output = output_dir / "shop_refresh_button.png"
    write_png(output, output_image)

    manifest = {
        "schema_version": 1,
        "method": "exact source RGB plus largest rounded-green-button contour as binary alpha",
        "source_path": str(source),
        "source_size": {"width": int(image.shape[1]), "height": int(image.shape[0])},
        "source_sha256": sha256(source),
        "crop": {"x": x, "y": y, "width": width, "height": height},
        "mask": {
            "color_space": "OpenCV HSV",
            "lower": HSV_LOWER.tolist(),
            "upper": HSV_UPPER.tolist(),
            "contour_area": area,
            "foreground_pixels": int(np.count_nonzero(output_image[:, :, 3])),
            "transparent_pixels": int(np.count_nonzero(output_image[:, :, 3] == 0)),
        },
        "output_path": output.name,
        "output_size": {"width": width, "height": height},
        "output_sha256": sha256(output),
    }
    (output_dir / "shop_refresh_button_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
