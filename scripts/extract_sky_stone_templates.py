from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import cv2
import numpy as np
import yaml


DEFAULT_SOURCE = Path(
    r"C:\Users\lxy\Pictures\Screenshots\屏幕截图 2026-08-24 005313.png"
)
EXPECTED_SOURCE_SHA256 = "b953c674d28cdf8cb552e5fbc9194de072a1e72e97c69af09ff500743fbe8b40"
DEFAULT_CONTEXT_SOURCE = Path(
    r"C:\Users\lxy\AppData\Local\Temp\codex-clipboard-fd6d853a-d4fd-4d17-91fa-c205bd9114fa.png"
)
EXPECTED_CONTEXT_SOURCE_SHA256 = "6ba52c11eace4e9678418d8e4900bb835bf060ed15a8f0ec586b1d36082ecba4"
DEFAULT_BALANCE_3924_SOURCE = Path(
    r"C:\Users\lxy\Pictures\Screenshots\屏幕截图 2026-08-24 012002.png"
)
EXPECTED_BALANCE_3924_SHA256 = "ebbe537dfff4afaf4b166d498d16d1687dc0f46d05e37d61bd4f65b9412dfe7a"
DEFAULT_BALANCE_3900_SOURCE = Path(
    r"C:\Users\lxy\Pictures\Screenshots\屏幕截图 2026-08-24 012032.png"
)
EXPECTED_BALANCE_3900_SHA256 = "2cb7ec7793b773c4616dd2fca0af772f5fd24b1b4e2757951eb803b350f144f6"
DEFAULT_COMBINED_TOP_BAR_SOURCE = Path(
    r"C:\Users\lxy\Pictures\Screenshots\屏幕截图 2026-08-24 012140.png"
)
EXPECTED_COMBINED_TOP_BAR_SHA256 = "5f15e6ae9c53a288c665d250fa2b833dbfa837650c0f7cbf376afa8a42791903"

# Exact opaque source crop containing the complete gem and adjacent plus marker.
ICON_CROP = (10, 5, 72, 80)

DIGIT_SEARCH = (70, 20, 175, 66)
DIGIT_MAX_SATURATION = 40
DIGIT_MIN_VALUE = 160
DIGIT_MIN_HEIGHT = 20
DIGIT_MIN_AREA = 100
DIGIT_PADDING = 2
KNOWN_DIGITS = ("3", "9", "2", "7")
CONTEXT_DIGIT_SEARCH = (1400, 165, 1610, 205)
CONTEXT_DIGITS = tuple("18825878")
BALANCE_3924_SEARCH = (60, 20, 165, 70)
BALANCE_3900_SEARCH = (60, 20, 170, 70)
COMBINED_GOLD_SEARCH = (70, 24, 270, 70)
COMBINED_SKY_STONE_SEARCH = (335, 24, 440, 70)
KNOWN_FONT_MIN_SIMILARITY = 0.80
ZERO_REPEAT_MIN_SIMILARITY = 0.94
FOUR_CROSS_SOURCE_MIN_SIMILARITY = 0.98


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


def crop_with_mask(
    image: np.ndarray,
    mask: np.ndarray,
    *,
    padding: int,
) -> tuple[np.ndarray, dict[str, int]]:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        raise RuntimeError("Template mask is empty")
    x0 = max(0, int(xs.min()) - padding)
    y0 = max(0, int(ys.min()) - padding)
    x1 = min(image.shape[1], int(xs.max()) + padding + 1)
    y1 = min(image.shape[0], int(ys.max()) + padding + 1)
    output = np.ascontiguousarray(image[y0:y1, x0:x1].copy())
    output[:, :, 3] = mask[y0:y1, x0:x1]
    return output, {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def icon_template(image: np.ndarray) -> tuple[np.ndarray, dict[str, int]]:
    x0, y0, x1, y1 = ICON_CROP
    output = np.ascontiguousarray(image[y0:y1, x0:x1].copy())
    if not np.all(output[:, :, 3] == 255):
        raise RuntimeError("Supplied Sky Stone anchor crop must be fully opaque")
    return output, {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def digit_components(
    image: np.ndarray,
    search_rect: tuple[int, int, int, int] = DIGIT_SEARCH,
) -> tuple[np.ndarray, list[tuple[int, int, int, int, int, int]]]:
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    neutral = (
        (hsv[:, :, 1] <= DIGIT_MAX_SATURATION)
        & (hsv[:, :, 2] >= DIGIT_MIN_VALUE)
    ).astype(np.uint8)
    x0, y0, x1, y1 = search_rect
    search = np.zeros(neutral.shape, dtype=np.uint8)
    search[y0:y1, x0:x1] = neutral[y0:y1, x0:x1]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(search, connectivity=8)
    components: list[tuple[int, int, int, int, int, int]] = []
    for component in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[component])
        if height >= DIGIT_MIN_HEIGHT and area >= DIGIT_MIN_AREA:
            components.append((component, x, y, width, height, area))
    components.sort(key=lambda item: item[1])
    return labels, components


def normalized_glyph(mask: np.ndarray, width: int = 24, height: int = 36) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    glyph = mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    scale = min((width - 4) / glyph.shape[1], (height - 4) / glyph.shape[0])
    resized_width = max(1, int(round(glyph.shape[1] * scale)))
    resized_height = max(1, int(round(glyph.shape[0] * scale)))
    resized = cv2.resize(
        glyph.astype(np.uint8),
        (resized_width, resized_height),
        interpolation=cv2.INTER_NEAREST,
    )
    result = np.zeros((height, width), dtype=np.uint8)
    x = (width - resized_width) // 2
    y = (height - resized_height) // 2
    result[y : y + resized_height, x : x + resized_width] = resized > 0
    return result


def glyph_similarity(left: np.ndarray, right: np.ndarray) -> float:
    union = np.count_nonzero((left > 0) | (right > 0))
    intersection = np.count_nonzero((left > 0) & (right > 0))
    return float(intersection / union)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract Sky Stone icon and supplied digit glyph templates"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--context-source", type=Path, default=DEFAULT_CONTEXT_SOURCE)
    parser.add_argument(
        "--balance-3924-source", type=Path, default=DEFAULT_BALANCE_3924_SOURCE
    )
    parser.add_argument(
        "--balance-3900-source", type=Path, default=DEFAULT_BALANCE_3900_SOURCE
    )
    parser.add_argument(
        "--combined-top-bar-source", type=Path, default=DEFAULT_COMBINED_TOP_BAR_SOURCE
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "assets" / "templates",
    )
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        raise RuntimeError(f"Missing supplied Sky Stone source: {source}")
    actual_hash = sha256(source)
    if actual_hash != EXPECTED_SOURCE_SHA256:
        raise RuntimeError(f"Unexpected source fingerprint for {source}: {actual_hash}")
    context_source = args.context_source.resolve()
    if not context_source.is_file():
        raise RuntimeError(f"Missing supplied Sky Stone context source: {context_source}")
    context_hash = sha256(context_source)
    if context_hash != EXPECTED_CONTEXT_SOURCE_SHA256:
        raise RuntimeError(
            f"Unexpected context fingerprint for {context_source}: {context_hash}"
        )

    supplemental_paths = {
        "balance_3924": (
            args.balance_3924_source.resolve(),
            EXPECTED_BALANCE_3924_SHA256,
        ),
        "balance_3900": (
            args.balance_3900_source.resolve(),
            EXPECTED_BALANCE_3900_SHA256,
        ),
        "combined_top_bar": (
            args.combined_top_bar_source.resolve(),
            EXPECTED_COMBINED_TOP_BAR_SHA256,
        ),
    }
    supplemental_hashes: dict[str, str] = {}
    supplemental_images: dict[str, np.ndarray] = {}
    for source_name, (source_path, expected_hash) in supplemental_paths.items():
        if not source_path.is_file():
            raise RuntimeError(f"Missing supplied {source_name} source: {source_path}")
        source_hash = sha256(source_path)
        if source_hash != expected_hash:
            raise RuntimeError(
                f"Unexpected {source_name} fingerprint for {source_path}: {source_hash}"
            )
        supplemental_hashes[source_name] = source_hash
        supplemental_images[source_name] = read_png(source_path)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    image = read_png(source)
    context_image = read_png(context_source)

    icon, icon_crop = icon_template(image)
    icon_path = output_dir / "sky_stone_icon.png"
    write_png(icon_path, icon)

    labels, components = digit_components(image)
    if len(components) != len(KNOWN_DIGITS):
        raise RuntimeError(f"Expected four balance digits, found {len(components)}: {components}")
    digit_entries: list[dict[str, object]] = []
    digit_masks: dict[str, np.ndarray] = {}

    def write_digit(
        digit: str,
        component_data: tuple[int, int, int, int, int, int],
        component_labels: np.ndarray,
        source_image: np.ndarray,
        source_name: str,
    ) -> None:
        component, x, y, width, height, area = component_data
        component_mask = np.zeros(source_image.shape[:2], dtype=np.uint8)
        component_mask[component_labels == component] = 255
        output, crop = crop_with_mask(source_image, component_mask, padding=DIGIT_PADDING)
        output_path = output_dir / f"sky_stone_digit_{digit}.png"
        write_png(output_path, output)
        digit_entries.append(
            {
                "digit": digit,
                "source": source_name,
                "output_path": output_path.name,
                "crop": crop,
                "component_area": area,
                "foreground_pixels": int(np.count_nonzero(output[:, :, 3])),
                "output_sha256": sha256(output_path),
            }
        )
        digit_masks[digit] = component_mask[y : y + height, x : x + width] > 0

    for digit, component_data in zip(KNOWN_DIGITS, components, strict=True):
        write_digit(digit, component_data, labels, image, "balance_crop")

    context_labels, context_components = digit_components(
        context_image, CONTEXT_DIGIT_SEARCH
    )
    if len(context_components) != len(CONTEXT_DIGITS):
        raise RuntimeError(
            f"Expected eight top-bar gold digits, found {len(context_components)}: {context_components}"
        )
    context_by_digit: dict[str, tuple[int, int, int, int, int, int]] = {}
    context_masks: dict[str, np.ndarray] = {}
    for digit, component_data in zip(CONTEXT_DIGITS, context_components, strict=True):
        component, x, y, width, height, _ = component_data
        context_by_digit.setdefault(digit, component_data)
        context_masks.setdefault(
            digit, context_labels[y : y + height, x : x + width] == component
        )
    cross_source = {
        digit: glyph_similarity(
            normalized_glyph(digit_masks[digit]),
            normalized_glyph(context_masks[digit]),
        )
        for digit in ("2", "7")
    }
    if min(cross_source.values()) < 0.95:
        raise RuntimeError(f"Top-bar fonts do not match safely: {cross_source}")
    for digit in ("1", "5", "8"):
        write_digit(
            digit,
            context_by_digit[digit],
            context_labels,
            context_image,
            "full_context",
        )

    def parse_sequence(
        source_name: str,
        source_image: np.ndarray,
        search_rect: tuple[int, int, int, int],
        expected_digits: str,
    ) -> tuple[
        np.ndarray,
        list[tuple[int, int, int, int, int, int]],
        list[np.ndarray],
    ]:
        sequence_labels, sequence_components = digit_components(
            source_image, search_rect
        )
        if len(sequence_components) != len(expected_digits):
            raise RuntimeError(
                f"Expected {expected_digits} in {source_name}, found "
                f"{len(sequence_components)} components: {sequence_components}"
            )
        sequence_masks: list[np.ndarray] = []
        for component, x, y, width, height, _ in sequence_components:
            sequence_masks.append(
                sequence_labels[y : y + height, x : x + width] == component
            )
        return sequence_labels, sequence_components, sequence_masks

    sequence_specs = (
        (
            "balance_3924",
            BALANCE_3924_SEARCH,
            "3924",
        ),
        (
            "balance_3900",
            BALANCE_3900_SEARCH,
            "3900",
        ),
        (
            "combined_top_bar",
            COMBINED_GOLD_SEARCH,
            "18455878",
        ),
        (
            "combined_top_bar",
            COMBINED_SKY_STONE_SEARCH,
            "3867",
        ),
    )
    parsed_sequences: list[
        tuple[
            str,
            tuple[int, int, int, int],
            str,
            np.ndarray,
            list[tuple[int, int, int, int, int, int]],
            list[np.ndarray],
        ]
    ] = []
    known_digit_similarity: list[dict[str, object]] = []
    for source_name, search_rect, expected_digits in sequence_specs:
        source_image = supplemental_images[source_name]
        sequence_labels, sequence_components, sequence_masks = parse_sequence(
            source_name, source_image, search_rect, expected_digits
        )
        parsed_sequences.append(
            (
                source_name,
                search_rect,
                expected_digits,
                sequence_labels,
                sequence_components,
                sequence_masks,
            )
        )
        for index, (digit, sequence_mask) in enumerate(
            zip(expected_digits, sequence_masks, strict=True)
        ):
            if digit not in digit_masks:
                continue
            similarity = glyph_similarity(
                normalized_glyph(digit_masks[digit]),
                normalized_glyph(sequence_mask),
            )
            known_digit_similarity.append(
                {
                    "source": source_name,
                    "sequence": expected_digits,
                    "index": index,
                    "digit": digit,
                    "similarity": similarity,
                }
            )
    if min(
        float(entry["similarity"]) for entry in known_digit_similarity
    ) < KNOWN_FONT_MIN_SIMILARITY:
        raise RuntimeError(
            f"Supplemental top-bar fonts do not match safely: {known_digit_similarity}"
        )

    balance_3924 = parsed_sequences[0]
    balance_3900 = parsed_sequences[1]
    combined_gold = parsed_sequences[2]
    combined_sky_stone = parsed_sequences[3]
    zero_repeat_similarity = glyph_similarity(
        normalized_glyph(balance_3900[5][2]),
        normalized_glyph(balance_3900[5][3]),
    )
    if zero_repeat_similarity < ZERO_REPEAT_MIN_SIMILARITY:
        raise RuntimeError(
            f"Repeated zero glyphs do not match safely: {zero_repeat_similarity}"
        )
    four_cross_source_similarity = glyph_similarity(
        normalized_glyph(balance_3924[5][3]),
        normalized_glyph(combined_gold[5][2]),
    )
    if four_cross_source_similarity < FOUR_CROSS_SOURCE_MIN_SIMILARITY:
        raise RuntimeError(
            f"Cross-source four glyphs do not match safely: {four_cross_source_similarity}"
        )

    write_digit(
        "0",
        balance_3900[4][2],
        balance_3900[3],
        supplemental_images["balance_3900"],
        "balance_3900",
    )
    write_digit(
        "4",
        combined_gold[4][2],
        combined_gold[3],
        supplemental_images["combined_top_bar"],
        "combined_top_bar_gold",
    )
    write_digit(
        "6",
        combined_sky_stone[4][2],
        combined_sky_stone[3],
        supplemental_images["combined_top_bar"],
        "combined_top_bar_sky_stone",
    )
    digit_entries.sort(key=lambda entry: str(entry["digit"]))

    manifest = {
        "schema_version": 1,
        "method": "exact source RGBA crops; digit glyphs use deterministic binary alpha masks",
        "sources": {
            "balance_crop": {
                "path": str(source),
                "size": {"width": int(image.shape[1]), "height": int(image.shape[0])},
                "sha256": actual_hash,
            },
            "full_context": {
                "path": str(context_source),
                "size": {
                    "width": int(context_image.shape[1]),
                    "height": int(context_image.shape[0]),
                },
                "sha256": context_hash,
                "exact_balance_crop_location": {"x": 1610, "y": 143, "width": 202, "height": 87},
            },
            **{
                source_name: {
                    "path": str(source_path),
                    "size": {
                        "width": int(supplemental_images[source_name].shape[1]),
                        "height": int(supplemental_images[source_name].shape[0]),
                    },
                    "sha256": supplemental_hashes[source_name],
                }
                for source_name, (source_path, _) in supplemental_paths.items()
            },
        },
        "icon": {
            "output_path": icon_path.name,
            "method": "exact opaque source pixel crop",
            "crop": icon_crop,
            "opaque_pixels": int(np.count_nonzero(icon[:, :, 3])),
            "output_sha256": sha256(icon_path),
        },
        "digit_segmentation": {
            "search": {"x": DIGIT_SEARCH[0], "y": DIGIT_SEARCH[1], "width": DIGIT_SEARCH[2] - DIGIT_SEARCH[0], "height": DIGIT_SEARCH[3] - DIGIT_SEARCH[1]},
            "max_saturation": DIGIT_MAX_SATURATION,
            "min_value": DIGIT_MIN_VALUE,
            "min_height": DIGIT_MIN_HEIGHT,
            "min_area": DIGIT_MIN_AREA,
        },
        "digits": digit_entries,
        "cross_source_font_similarity": cross_source,
        "supplemental_sequences": [
            {
                "source": source_name,
                "digits": expected_digits,
                "search": {
                    "x": search_rect[0],
                    "y": search_rect[1],
                    "width": search_rect[2] - search_rect[0],
                    "height": search_rect[3] - search_rect[1],
                },
            }
            for source_name, search_rect, expected_digits in sequence_specs
        ],
        "supplemental_validation": {
            "known_digit_minimum": KNOWN_FONT_MIN_SIMILARITY,
            "known_digit_similarity": known_digit_similarity,
            "zero_repeat_minimum": ZERO_REPEAT_MIN_SIMILARITY,
            "zero_repeat_similarity": zero_repeat_similarity,
            "four_cross_source_minimum": FOUR_CROSS_SOURCE_MIN_SIMILARITY,
            "four_cross_source_similarity": four_cross_source_similarity,
        },
        "missing_digits": [],
    }
    (output_dir / "sky_stone_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
