from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import cv2
import numpy as np
import yaml


DEFAULT_SOURCE = Path(
    r"C:\Users\lxy\AppData\Local\Temp\codex-clipboard-d7eb2cbf-dc22-47d9-8648-9b5d861554dc.png"
)
EXPECTED_SOURCE_SHA256 = "3545657776c62d08ffc6cbf9f34e88e4b599f90451aa3dbaa970b11b92673e86"

PROMPT_SEARCH = (850, 180, 1650, 360)
PROMPT_MAX_SATURATION = 20
PROMPT_MIN_VALUE = 120
PROMPT_MIN_COMPONENT_AREA = 20
PROMPT_PADDING = 4

BUTTON_SEARCH = (1100, 400, 1750, 650)
CANNY_LOW = 30
CANNY_HIGH = 90
BUTTON_MIN_CONTOUR_AREA = 30_000.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def prompt_mask(image: np.ndarray) -> tuple[np.ndarray, list[int]]:
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    seed = (
        (hsv[:, :, 1] <= PROMPT_MAX_SATURATION)
        & (hsv[:, :, 2] >= PROMPT_MIN_VALUE)
    ).astype(np.uint8)
    x0, y0, x1, y1 = PROMPT_SEARCH
    search = np.zeros(seed.shape, dtype=np.uint8)
    search[y0:y1, x0:x1] = seed[y0:y1, x0:x1]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(search, connectivity=8)
    selected = [
        component
        for component in range(1, count)
        if int(stats[component, cv2.CC_STAT_AREA]) >= PROMPT_MIN_COMPONENT_AREA
    ]
    if len(selected) < 10:
        raise RuntimeError(f"Refresh-confirm prompt has too few components: {len(selected)}")
    mask = np.zeros(seed.shape, dtype=np.uint8)
    for component in selected:
        mask[labels == component] = 255
    return mask, [int(stats[component, cv2.CC_STAT_AREA]) for component in selected]


def button_mask(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, CANNY_LOW, CANNY_HIGH)
    x0, y0, x1, y1 = BUTTON_SEARCH
    search = np.zeros(edges.shape, dtype=np.uint8)
    search[y0:y1, x0:x1] = edges[y0:y1, x0:x1]
    contours, _ = cv2.findContours(search, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise RuntimeError("Refresh-confirm button contour was not found")
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    if area < BUTTON_MIN_CONTOUR_AREA:
        raise RuntimeError(f"Refresh-confirm button contour is unexpectedly small: {area}")
    mask = np.zeros(edges.shape, dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, thickness=cv2.FILLED)
    return mask, contour, area


def crop_with_mask(
    image: np.ndarray,
    mask: np.ndarray,
    *,
    padding: int = 0,
    contour: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, int]]:
    if contour is None:
        ys, xs = np.nonzero(mask)
        x0 = max(0, int(xs.min()) - padding)
        y0 = max(0, int(ys.min()) - padding)
        x1 = min(image.shape[1], int(xs.max()) + padding + 1)
        y1 = min(image.shape[0], int(ys.max()) + padding + 1)
    else:
        x0, y0, width, height = cv2.boundingRect(contour)
        x1 = x0 + width
        y1 = y0 + height
    output = np.ascontiguousarray(image[y0:y1, x0:x1].copy())
    output[:, :, 3] = mask[y0:y1, x0:x1]
    return output, {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract refresh-confirm prompt and button templates"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "assets" / "templates",
    )
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        raise RuntimeError(f"Missing supplied refresh-confirm source: {source}")
    actual_hash = sha256(source)
    if actual_hash != EXPECTED_SOURCE_SHA256:
        raise RuntimeError(f"Unexpected source fingerprint for {source}: {actual_hash}")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    image = read_png(source)
    prompt_alpha, component_areas = prompt_mask(image)
    prompt, prompt_crop = crop_with_mask(
        image,
        prompt_alpha,
        padding=PROMPT_PADDING,
    )
    button_alpha, button_contour, contour_area = button_mask(image)
    button, button_crop = crop_with_mask(
        image,
        button_alpha,
        contour=button_contour,
    )

    outputs = (
        (
            "refresh_confirm_prompt.png",
            "prompt_identity",
            prompt,
            prompt_crop,
            {
                "color_space": "OpenCV HSV",
                "max_saturation": PROMPT_MAX_SATURATION,
                "min_value": PROMPT_MIN_VALUE,
                "min_component_area": PROMPT_MIN_COMPONENT_AREA,
                "component_areas": component_areas,
            },
        ),
        (
            "refresh_confirm_button.png",
            "confirm_button",
            button,
            button_crop,
            {
                "edge_method": "OpenCV Canny",
                "canny_low": CANNY_LOW,
                "canny_high": CANNY_HIGH,
                "contour_area": contour_area,
            },
        ),
    )
    entries: list[dict[str, object]] = []
    for filename, role, output_image, crop, mask_details in outputs:
        output = output_dir / filename
        write_png(output, output_image)
        entries.append(
            {
                "output_path": filename,
                "role": role,
                "crop": crop,
                "output_size": {"width": crop["width"], "height": crop["height"]},
                "mask": {
                    **mask_details,
                    "foreground_pixels": int(np.count_nonzero(output_image[:, :, 3])),
                    "transparent_pixels": int(np.count_nonzero(output_image[:, :, 3] == 0)),
                },
                "output_sha256": sha256(output),
            }
        )

    manifest = {
        "schema_version": 1,
        "method": "exact source RGB with deterministic binary alpha masks",
        "source_path": str(source),
        "source_size": {"width": int(image.shape[1]), "height": int(image.shape[0])},
        "source_sha256": actual_hash,
        "templates": entries,
    }
    (output_dir / "refresh_confirm_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
