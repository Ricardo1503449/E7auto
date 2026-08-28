from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import cv2
import numpy as np
import yaml

try:
    from scripts.calibrate_client_frames import locate_client_crop
except ModuleNotFoundError:
    from calibrate_client_frames import locate_client_crop


DEFAULT_SOURCE = Path(
    r"C:\Users\lxy\AppData\Local\Temp\codex-clipboard-f737e650-4fcb-4974-9950-5c5c9b4274eb.png"
)
EXPECTED_SOURCE_SHA256 = "e2f44aef29f23ac04563dc532012000c43b91a0e4c444754b80ca3ecf3e6f926"
EXPECTED_SOURCE_SIZE = (2419, 1519)
EXPECTED_CLIENT_CROP = (44, 124, 2322, 1306)
WIDE_GOLD_ZERO_COMPONENT = (1464, 46, 19, 29, 273)
NARROW_GOLD_ZERO_COMPONENT = (1536, 46, 18, 29, 266)
SKY_STONE_ZERO_COMPONENT = (1690, 46, 19, 29, 271)
PADDING = 2


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


def neutral_bright_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    return ((hsv[:, :, 1] <= 40) & (hsv[:, :, 2] >= 160)).astype(np.uint8)


def component_mask(
    client: np.ndarray,
    expected: tuple[int, int, int, int, int],
) -> np.ndarray:
    x, y, width, height, area = expected
    mask = neutral_bright_mask(client)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for component in range(1, count):
        found = tuple(int(value) for value in stats[component])
        if found == expected:
            output = np.zeros(mask.shape, dtype=np.uint8)
            output[labels == component] = 255
            return output
    raise RuntimeError(
        f"Expected component {(x, y, width, height, area)} was not found"
    )


def normalize_glyph(mask: np.ndarray, width: int = 24, height: int = 36) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return np.zeros((height, width), dtype=np.uint8)
    glyph = mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    scale = min((width - 4) / glyph.shape[1], (height - 4) / glyph.shape[0])
    resized_width = max(1, int(round(glyph.shape[1] * scale)))
    resized_height = max(1, int(round(glyph.shape[0] * scale)))
    resized = cv2.resize(
        glyph.astype(np.uint8),
        (resized_width, resized_height),
        interpolation=cv2.INTER_NEAREST,
    )
    output = np.zeros((height, width), dtype=np.uint8)
    x = (width - resized_width) // 2
    y = (height - resized_height) // 2
    output[y : y + resized_height, x : x + resized_width] = resized > 0
    return output


def similarity(left: np.ndarray, right: np.ndarray) -> float:
    left = normalize_glyph(left)
    right = normalize_glyph(right)
    union = np.count_nonzero((left > 0) | (right > 0))
    if union == 0:
        return 0.0
    return float(np.count_nonzero((left > 0) & (right > 0)) / union)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract the evidence-backed wide Sky Stone zero glyph variant"
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
        raise RuntimeError(f"Missing supplied wide-zero source: {source}")
    source_hash = sha256(source)
    if source_hash.casefold() != EXPECTED_SOURCE_SHA256:
        raise RuntimeError(f"Unexpected source fingerprint for {source}: {source_hash}")

    image = read_png(source)
    source_size = (int(image.shape[1]), int(image.shape[0]))
    if source_size != EXPECTED_SOURCE_SIZE:
        raise RuntimeError(f"Unexpected source size: {source_size}")
    client_crop = locate_client_crop(image)
    if client_crop != EXPECTED_CLIENT_CROP:
        raise RuntimeError(f"Unexpected client crop: {client_crop}")
    client_x, client_y, client_width, client_height = client_crop
    client = np.ascontiguousarray(
        image[client_y : client_y + client_height, client_x : client_x + client_width]
    )

    wide_mask = component_mask(client, WIDE_GOLD_ZERO_COMPONENT)
    narrow_mask = component_mask(client, NARROW_GOLD_ZERO_COMPONENT)
    sky_mask = component_mask(client, SKY_STONE_ZERO_COMPONENT)
    x, y, width, height, _ = WIDE_GOLD_ZERO_COMPONENT
    crop_x = x - PADDING
    crop_y = y - PADDING
    output = np.ascontiguousarray(
        client[
            crop_y : crop_y + height + PADDING * 2,
            crop_x : crop_x + width + PADDING * 2,
        ].copy()
    )
    output[:, :, 3] = wide_mask[
        crop_y : crop_y + height + PADDING * 2,
        crop_x : crop_x + width + PADDING * 2,
    ]

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "sky_stone_digit_0_wide.png"
    write_png(output_path, output)

    manifest = {
        "schema_version": 1,
        "method": "exact source RGBA crop with deterministic neutral-bright component alpha mask",
        "source": {
            "path": str(source),
            "size": {"width": source_size[0], "height": source_size[1]},
            "sha256": source_hash,
            "client_crop": {
                "x": client_x,
                "y": client_y,
                "width": client_width,
                "height": client_height,
            },
        },
        "template": {
            "digit": "0",
            "variant": "wide",
            "source_context": "first zero in the gold balance 11,120,980",
            "component": {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "area": WIDE_GOLD_ZERO_COMPONENT[4],
            },
            "crop": {
                "x": crop_x,
                "y": crop_y,
                "width": int(output.shape[1]),
                "height": int(output.shape[0]),
            },
            "foreground_pixels": int(np.count_nonzero(output[:, :, 3])),
            "output_path": output_path.name,
            "output_sha256": sha256(output_path),
        },
        "validation": {
            "wide_gold_to_sky_stone_zero_minimum": 0.99,
            "wide_gold_to_sky_stone_zero_similarity": similarity(wide_mask, sky_mask),
            "narrow_gold_to_wide_gold_zero_similarity": similarity(
                narrow_mask, wide_mask
            ),
        },
    }
    if (
        manifest["validation"]["wide_gold_to_sky_stone_zero_similarity"]
        < manifest["validation"]["wide_gold_to_sky_stone_zero_minimum"]
    ):
        raise RuntimeError(f"Wide zero validation failed: {manifest['validation']}")
    (output_dir / "sky_stone_zero_wide_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
