from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml


@dataclass(frozen=True, slots=True)
class CropSpec:
    output: str
    source_token: str
    state: str
    target_id: str | None
    x: int
    y: int
    width: int
    height: int


# Coordinates are measured in the supplied PNGs. They deliberately retain only
# the target icon or the stable right-hand confirmation-button label.
CROPS = (
    CropSpec("friendship_points.png", "194149", "unpurchased", "friendship_points", 254, 32, 176, 180),
    CropSpec("covenant_bookmark.png", "193052", "unpurchased", "covenant_bookmark", 246, 57, 178, 180),
    CropSpec("mystic_medal.png", "215516", "unpurchased", "mystic_medal", 250, 44, 178, 180),
    CropSpec(
        "friendship_points_confirm.png",
        "201926",
        "confirmation",
        "friendship_points",
        931,
        373,
        160,
        176,
    ),
    CropSpec(
        "covenant_bookmark_confirm.png",
        "201213",
        "confirmation",
        "covenant_bookmark",
        924,
        432,
        161,
        176,
    ),
    CropSpec("mystic_medal_confirm.png", "215530", "confirmation", "mystic_medal", 948, 383, 161, 177),
    CropSpec("confirm_button.png", "201926", "confirmation_button", None, 1440, 704, 140, 70),
    CropSpec(
        "friendship_points_purchased.png",
        "202035",
        "purchased",
        "friendship_points",
        233,
        32,
        174,
        182,
    ),
    CropSpec(
        "covenant_bookmark_purchased.png",
        "201229",
        "purchased",
        "covenant_bookmark",
        242,
        29,
        177,
        178,
    ),
    CropSpec("mystic_medal_purchased.png", "215544", "purchased", "mystic_medal", 254, 40, 156, 165),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_source(source_dir: Path, token: str) -> Path:
    matches = tuple(path for path in source_dir.glob("*.png") if token in path.stem)
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one PNG containing {token!r}, found {len(matches)}")
    return matches[0]


def read_png(path: Path) -> np.ndarray:
    encoded = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    if image is None or image.size == 0:
        raise RuntimeError(f"Cannot decode PNG: {path}")
    return image


def write_png(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError(f"Cannot encode PNG: {path}")
    encoded.tofile(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract deterministic Epic Seven calibration templates")
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
    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_entries: list[dict[str, object]] = []
    for spec in CROPS:
        source = find_source(source_dir, spec.source_token)
        image = read_png(source)
        source_height, source_width = image.shape[:2]
        if spec.x < 0 or spec.y < 0 or spec.x + spec.width > source_width or spec.y + spec.height > source_height:
            raise RuntimeError(f"Crop for {spec.output} is outside {source_width}x{source_height}")
        crop = np.ascontiguousarray(
            image[spec.y : spec.y + spec.height, spec.x : spec.x + spec.width]
        )
        output = output_dir / spec.output
        write_png(output, crop)
        entry = asdict(spec)
        entry.update(
            {
                "source_path": str(source),
                "source_size": {"width": source_width, "height": source_height},
                "source_sha256": sha256(source),
                "output_path": output.name,
                "output_sha256": sha256(output),
                "channels": int(crop.shape[2]) if crop.ndim == 3 else 1,
            }
        )
        manifest_entries.append(entry)

    manifest = {
        "schema_version": 1,
        "method": "exact pixel crop; no scaling, filtering, color conversion, or generation",
        "templates": manifest_entries,
    }
    (output_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
