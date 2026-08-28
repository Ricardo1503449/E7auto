from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import cv2
import numpy as np
import yaml


BASELINE_WIDTH = 2322
BASELINE_HEIGHT = 1306
SOURCE_SPECS = {
    "shop": {
        "timestamp_token": "164129",
        "sequence_role": "shop_before_purchase",
        "sha256": "501affd4cce5b51ca97a18f00a7b4b0fb18ef64c84ccffa97ea868a9654130dc",
        "source_size": {"width": 2422, "height": 1467},
        "expected_client_origin": {"x": 46, "y": 103},
    },
    "purchase_confirmation": {
        "timestamp_token": "164137",
        "sequence_role": "purchase_confirmation_before_insufficient_gold",
        "sha256": "a9ff3969b37b8acbbe2c590a5827c18f8adacbd0b7d4cec1117ae32ba4b77b2e",
        "source_size": {"width": 2433, "height": 1473},
        "expected_client_origin": {"x": 57, "y": 117},
    },
    "insufficient_gold": {
        "timestamp_token": "164145",
        "sequence_role": "terminal_insufficient_gold_prompt",
        "sha256": "d96c3cd5345b9f8ddf335bad47b7ce2af232b4f19454fe140930a3de83747689",
        "source_size": {"width": 2433, "height": 1497},
        "expected_client_origin": {"x": 67, "y": 137},
    },
}

TITLE_SEARCH = (1020, 230, 1325, 320)
MESSAGE_SEARCH = (1000, 385, 1340, 500)
MAX_SATURATION = 55
MIN_VALUE = 165
MIN_COMPONENT_AREA = 8
MIN_COMPONENT_HEIGHT = 4
TEMPLATE_PADDING = 4
PURCHASE_RESULT_ROI = {"x": 975, "y": 210, "width": 400, "height": 300}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pixel_sha256(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def read_png(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3 or image.shape[2] not in (3, 4):
        raise RuntimeError(f"Expected a color PNG: {path}")
    if image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    return np.ascontiguousarray(image)


def write_png(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError(f"Cannot encode PNG: {path}")
    encoded.tofile(path)


def find_source(source_dir: Path, token: str) -> Path:
    matches = [path for path in source_dir.glob("*.png") if token in path.name]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one PNG containing timestamp {token} in {source_dir}, "
            f"found {len(matches)}"
        )
    return matches[0].resolve()


def detect_client_origin(image: np.ndarray) -> tuple[int, int, dict[str, float]]:
    height, width = image.shape[:2]
    if width < BASELINE_WIDTH or height < BASELINE_HEIGHT:
        raise RuntimeError(f"Source is smaller than the baseline client: {width}x{height}")

    bgr = image[:, :, :3]
    y_scores: list[tuple[float, int]] = []
    for y in range(1, height - BASELINE_HEIGHT + 1):
        difference = np.abs(
            bgr[y, 100 : width - 100].astype(np.int16)
            - bgr[y - 1, 100 : width - 100].astype(np.int16)
        )
        y_scores.append((float(np.mean(difference)), y))
    top_strength, client_y = max(y_scores)

    client_rows = bgr[client_y + 50 : client_y + BASELINE_HEIGHT - 50]
    x_scores: list[tuple[float, int, float, float]] = []
    for x in range(1, width - BASELINE_WIDTH):
        left_strength = float(
            np.mean(
                np.abs(
                    client_rows[:, x].astype(np.int16)
                    - client_rows[:, x - 1].astype(np.int16)
                )
            )
        )
        right_strength = float(
            np.mean(
                np.abs(
                    client_rows[:, x + BASELINE_WIDTH - 1].astype(np.int16)
                    - client_rows[:, x + BASELINE_WIDTH].astype(np.int16)
                )
            )
        )
        x_scores.append((left_strength + right_strength, x, left_strength, right_strength))
    paired_strength, client_x, left_strength, right_strength = max(x_scores)

    if top_strength < 200 or min(left_strength, right_strength) < 50:
        raise RuntimeError(
            "Client boundary evidence is too weak: "
            f"top={top_strength}, left={left_strength}, right={right_strength}"
        )
    return client_x, client_y, {
        "top": top_strength,
        "left": left_strength,
        "right": right_strength,
        "paired_horizontal": paired_strength,
    }


def crop_client(image: np.ndarray) -> tuple[np.ndarray, dict[str, object]]:
    x, y, strengths = detect_client_origin(image)
    client = np.ascontiguousarray(
        image[y : y + BASELINE_HEIGHT, x : x + BASELINE_WIDTH]
    )
    if client.shape[:2] != (BASELINE_HEIGHT, BASELINE_WIDTH):
        raise RuntimeError(f"Unexpected client crop shape: {client.shape}")
    return client, {
        "x": x,
        "y": y,
        "width": BASELINE_WIDTH,
        "height": BASELINE_HEIGHT,
        "boundary_gradient_strength": strengths,
        "pixel_sha256": pixel_sha256(client),
    }


def terminal_text_mask(client: np.ndarray) -> tuple[np.ndarray, list[int]]:
    hsv = cv2.cvtColor(client[:, :, :3], cv2.COLOR_BGR2HSV)
    seed = (
        (hsv[:, :, 1] <= MAX_SATURATION)
        & (hsv[:, :, 2] >= MIN_VALUE)
    ).astype(np.uint8)
    search = np.zeros(seed.shape, dtype=np.uint8)
    for x0, y0, x1, y1 in (TITLE_SEARCH, MESSAGE_SEARCH):
        search[y0:y1, x0:x1] = seed[y0:y1, x0:x1]

    count, labels, stats, _ = cv2.connectedComponentsWithStats(search, connectivity=8)
    selected = [
        component
        for component in range(1, count)
        if int(stats[component, cv2.CC_STAT_AREA]) >= MIN_COMPONENT_AREA
        and int(stats[component, cv2.CC_STAT_HEIGHT]) >= MIN_COMPONENT_HEIGHT
    ]
    if len(selected) < 30:
        raise RuntimeError(f"Insufficient-gold text has too few components: {len(selected)}")
    mask = np.zeros(seed.shape, dtype=np.uint8)
    for component in selected:
        mask[labels == component] = 255
    return mask, [int(stats[component, cv2.CC_STAT_AREA]) for component in selected]


def crop_template(
    client: np.ndarray, mask: np.ndarray
) -> tuple[np.ndarray, dict[str, int]]:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        raise RuntimeError("Insufficient-gold text mask is empty")
    x0 = max(0, int(xs.min()) - TEMPLATE_PADDING)
    y0 = max(0, int(ys.min()) - TEMPLATE_PADDING)
    x1 = min(client.shape[1], int(xs.max()) + TEMPLATE_PADDING + 1)
    y1 = min(client.shape[0], int(ys.max()) + TEMPLATE_PADDING + 1)
    output = np.ascontiguousarray(client[y0:y1, x0:x1].copy())
    output[:, :, 3] = mask[y0:y1, x0:x1]
    return output, {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def masked_match(client: np.ndarray, template: np.ndarray) -> dict[str, object]:
    roi = PURCHASE_RESULT_ROI
    source = client[
        roi["y"] : roi["y"] + roi["height"],
        roi["x"] : roi["x"] + roi["width"],
        :3,
    ]
    result = cv2.matchTemplate(
        source,
        template[:, :, :3],
        cv2.TM_SQDIFF_NORMED,
        mask=template[:, :, 3],
    )
    result = np.nan_to_num(result, nan=np.inf, posinf=np.inf, neginf=np.inf)
    difference, _, location, _ = cv2.minMaxLoc(result)
    return {
        "location": {"x": roi["x"] + location[0], "y": roi["y"] + location[1]},
        "confidence": max(0.0, min(1.0, 1.0 - float(difference))),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Crop supplied desktop screenshots in memory and extract the terminal insufficient-gold template"
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
    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    clients: dict[str, np.ndarray] = {}
    source_entries: dict[str, object] = {}
    for key, spec in SOURCE_SPECS.items():
        source = find_source(source_dir, str(spec["timestamp_token"]))
        actual_hash = sha256(source)
        if actual_hash != spec["sha256"]:
            raise RuntimeError(f"Unexpected source fingerprint for {source}: {actual_hash}")
        image = read_png(source)
        actual_size = {"width": int(image.shape[1]), "height": int(image.shape[0])}
        if actual_size != spec["source_size"]:
            raise RuntimeError(f"Unexpected source size for {source}: {actual_size}")
        client, client_crop = crop_client(image)
        origin = {"x": client_crop["x"], "y": client_crop["y"]}
        if origin != spec["expected_client_origin"]:
            raise RuntimeError(f"Unexpected client origin for {source}: {origin}")
        clients[key] = client
        source_entries[key] = {
            "path": str(source),
            "sequence_role": spec["sequence_role"],
            "source_size": actual_size,
            "source_sha256": actual_hash,
            "client_crop": client_crop,
        }

    terminal_client = clients["insufficient_gold"]
    mask, component_areas = terminal_text_mask(terminal_client)
    template, template_crop = crop_template(terminal_client, mask)
    output_path = output_dir / "insufficient_funds.png"
    write_png(output_path, template)
    positive_match = masked_match(terminal_client, template)
    if positive_match["confidence"] < 0.999:
        raise RuntimeError(f"Terminal template did not reproduce its source: {positive_match}")

    manifest = {
        "schema_version": 1,
        "method": (
            "hash-pinned desktop sources; exact 2322x1306 clients cropped in memory; "
            "exact terminal-source RGB with deterministic binary alpha text mask"
        ),
        "baseline_client_size": {"width": BASELINE_WIDTH, "height": BASELINE_HEIGHT},
        "sequence_usage": (
            "the first two screenshots document how the third state is reached and are not negative samples"
        ),
        "sources": source_entries,
        "template": {
            "output_path": output_path.name,
            "source": "insufficient_gold",
            "content": "购买金币 title and explanatory text",
            "crop": template_crop,
            "output_size": {
                "width": int(template.shape[1]),
                "height": int(template.shape[0]),
            },
            "mask": {
                "color_space": "OpenCV HSV",
                "search_regions": {
                    "title": {"x0": TITLE_SEARCH[0], "y0": TITLE_SEARCH[1], "x1": TITLE_SEARCH[2], "y1": TITLE_SEARCH[3]},
                    "message": {"x0": MESSAGE_SEARCH[0], "y0": MESSAGE_SEARCH[1], "x1": MESSAGE_SEARCH[2], "y1": MESSAGE_SEARCH[3]},
                },
                "max_saturation": MAX_SATURATION,
                "min_value": MIN_VALUE,
                "min_component_area": MIN_COMPONENT_AREA,
                "min_component_height": MIN_COMPONENT_HEIGHT,
                "component_areas": component_areas,
                "foreground_pixels": int(np.count_nonzero(template[:, :, 3])),
                "transparent_pixels": int(np.count_nonzero(template[:, :, 3] == 0)),
            },
            "output_sha256": sha256(output_path),
        },
        "calibrated": {
            "purchase_result_roi": PURCHASE_RESULT_ROI,
            "runtime_threshold": 0.93,
        },
        "positive_evidence": {
            "source": "insufficient_gold",
            **positive_match,
        },
        "safety": {
            "terminal_stop_reason": "purchase_funds_insufficient",
            "terminal_confirm_clicked": False,
            "full_client_crops_persisted": False,
        },
    }
    (output_dir / "insufficient_funds_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
