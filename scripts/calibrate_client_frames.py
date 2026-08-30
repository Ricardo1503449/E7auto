from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import cv2
import numpy as np
import yaml


ROOT = Path(__file__).resolve().parents[1]
BASELINE_WIDTH = 2322
BASELINE_HEIGHT = 1306
INSUFFICIENT_FUNDS_MANIFEST_PATH = (
    ROOT / "assets" / "templates" / "insufficient_funds_manifest.yaml"
)
INSUFFICIENT_FUNDS_LIVE_MANIFEST_PATH = (
    ROOT / "assets" / "templates" / "insufficient_funds_live_validation_manifest.yaml"
)
MAIN_SHOP_LAYOUT_MANIFEST_PATH = (
    ROOT / "assets" / "templates" / "main_shop_layout_manifest.yaml"
)

SOURCE_SPECS = {
    "main": {
        "path": Path(
            r"C:\Users\lxy\AppData\Local\Temp\codex-clipboard-e4b375c4-6d8c-4b2e-9288-e512f7b7f8e2.png"
        ),
        "sha256": "b926e9ff4ecdc6c8a5b13fc2e4c9959dd28b4111ff55641d7b8f4a14a64db970",
        "size": (2425, 1474),
        "client_crop": (49, 108, BASELINE_WIDTH, BASELINE_HEIGHT),
    },
    "shop_top": {
        "path": Path(
            r"C:\Users\lxy\Pictures\Screenshots\屏幕截图 2026-08-24 014250.png"
        ),
        "sha256": "184f655559b82a4e9e67b24ef52092dd4c616cf3512d2a22d53b514902625234",
        "size": (2425, 1463),
        "client_crop": (42, 101, BASELINE_WIDTH, BASELINE_HEIGHT),
    },
    "shop_bottom": {
        "path": Path(
            r"C:\Users\lxy\Pictures\Screenshots\屏幕截图 2026-08-24 014258.png"
        ),
        "sha256": "24e0a1be8a49f244663d3b681270cba4d191b7c81c364f8be560aa7e3a0be3db",
        "size": (2422, 1494),
        "client_crop": (49, 111, BASELINE_WIDTH, BASELINE_HEIGHT),
    },
    "refresh_confirm": {
        "path": Path(
            r"C:\Users\lxy\AppData\Local\Temp\codex-clipboard-da266c82-d2de-4842-b15a-ee480e4374ab.png"
        ),
        "sha256": "d8689ed8e94139e6ccba66e275e52bab0917bbe4c23f1543774893510a2f808d",
        "size": (2401, 1439),
        "client_crop": (31, 90, BASELINE_WIDTH, BASELINE_HEIGHT),
    },
    "purchase_confirm": {
        "path": Path(
            r"C:\Users\lxy\AppData\Local\Temp\codex-clipboard-f67601a5-5bef-49bc-b798-a10036523fe7.png"
        ),
        "sha256": "e72ec85c1c6220dc6b019e5703adf3971d1ebd9e8ee630a6a6bd99c4bd9fe856",
        "size": (2390, 1487),
        "client_crop": (32, 125, BASELINE_WIDTH, BASELINE_HEIGHT),
    },
}

CALIBRATED_ROIS = {
    "main_shop_icon": (25, 525, 165, 175),
    "shop_refresh_button": (105, 1125, 580, 150),
    "shop_exit_icon": (39, 25, 267, 70),
    "refresh_confirm_prompt": (925, 540, 475, 90),
    "refresh_confirm_button": (1150, 755, 415, 160),
    "inventory_list": (950, 110, 1320, 1180),
    "confirm_item": (880, 535, 220, 225),
    "confirm_button": (1385, 860, 200, 130),
    "purchase_result": (975, 210, 400, 300),
    "sky_stone_icon": (1450, 12, 195, 95),
    "sky_stone_digits": (1625, 38, 110, 45),
}
CALIBRATED_POINTS = {
    "shop_icon": (102, 594),
    "shop_exit_button": (172, 60),
    "main_screen_wake": (1161, 653),
    "refresh_button": (394, 1201),
    "refresh_confirm_button": (1356, 834),
    "confirm_button": (1485, 925),
}
CALIBRATED_SLOTS = (
    ("top_1", "top", 0, (970, 145, 240, 220), (2091, 299)),
    ("top_2", "top", 1, (970, 408, 240, 220), (2091, 562)),
    ("top_3", "top", 2, (970, 671, 240, 220), (2091, 825)),
    ("top_4", "top", 3, (970, 934, 240, 220), (2091, 1087)),
    ("bottom_5", "bottom", 4, (970, 803, 240, 220), (2091, 957)),
    ("bottom_6", "bottom", 5, (970, 1066, 240, 220), (2091, 1220)),
)
SCROLL_CURSOR_POINT = (1500, 650)
SKY_STONE_DIGITS_OFFSET = (57, 16)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_png(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        raise RuntimeError(f"Expected an RGBA PNG: {path}")
    return image


def rect_dict(rect: tuple[int, int, int, int]) -> dict[str, int]:
    x, y, width, height = rect
    return {"x": x, "y": y, "width": width, "height": height}


def point_dict(point: tuple[int, int]) -> dict[str, int]:
    return {"x": point[0], "y": point[1]}


def locate_client_crop(image: np.ndarray) -> tuple[int, int, int, int]:
    if image.shape[1] <= BASELINE_WIDTH or image.shape[0] <= BASELINE_HEIGHT:
        raise RuntimeError(f"Source is too small for the baseline client: {image.shape}")
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY).astype(np.float32)
    vertical = np.abs(np.diff(gray, axis=1)).mean(axis=0)
    horizontal = np.abs(np.diff(gray, axis=0)).mean(axis=1)
    x = max(
        range(1, image.shape[1] - BASELINE_WIDTH),
        key=lambda candidate: float(
            vertical[candidate - 1]
            + vertical[candidate + BASELINE_WIDTH - 1]
        ),
    )
    y = max(
        range(1, image.shape[0] - BASELINE_HEIGHT),
        key=lambda candidate: float(
            horizontal[candidate - 1]
            + horizontal[candidate + BASELINE_HEIGHT - 1]
        ),
    )
    return (x, y, BASELINE_WIDTH, BASELINE_HEIGHT)


def verified_client(
    role: str, spec: dict[str, object]
) -> tuple[np.ndarray, dict[str, object]]:
    path = Path(spec["path"])
    if not path.is_file():
        raise RuntimeError(f"Missing supplied {role} source: {path}")
    actual_hash = sha256(path)
    if actual_hash != spec["sha256"]:
        raise RuntimeError(f"Unexpected {role} source fingerprint: {actual_hash}")
    image = read_png(path)
    expected_width, expected_height = spec["size"]
    if image.shape[:2] != (expected_height, expected_width):
        raise RuntimeError(f"Unexpected {role} source shape: {image.shape}")
    detected_crop = locate_client_crop(image)
    if detected_crop != spec["client_crop"]:
        raise RuntimeError(
            f"Unexpected {role} client crop: {detected_crop}; "
            f"expected {spec['client_crop']}"
        )
    x, y, width, height = detected_crop
    client = np.ascontiguousarray(image[y : y + height, x : x + width].copy())
    if client.shape != (BASELINE_HEIGHT, BASELINE_WIDTH, 4):
        raise RuntimeError(f"Unexpected {role} client shape: {client.shape}")
    if not np.all(client[:, :, 3] == 255):
        raise RuntimeError(f"{role} client crop must be fully opaque")

    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY).astype(np.float32)
    vertical = np.abs(np.diff(gray, axis=1)).mean(axis=0)
    horizontal = np.abs(np.diff(gray, axis=0)).mean(axis=1)
    boundary_strengths = {
        "left": float(vertical[x - 1]),
        "right": float(vertical[x + width - 1]),
        "top": float(horizontal[y - 1]),
        "bottom": float(horizontal[y + height - 1]),
    }
    if min(boundary_strengths.values()) < 50.0:
        raise RuntimeError(f"Weak {role} client boundary evidence: {boundary_strengths}")
    return client[:, :, :3], {
        "path": str(path),
        "size": {"width": expected_width, "height": expected_height},
        "sha256": actual_hash,
        "client_crop": rect_dict(detected_crop),
        "boundary_gradient_strength": boundary_strengths,
    }


def load_template(name: str) -> tuple[np.ndarray, np.ndarray | None]:
    raw = read_png(ROOT / "assets" / "templates" / f"{name}.png")
    mask = None if np.all(raw[:, :, 3] == 255) else raw[:, :, 3]
    return np.ascontiguousarray(raw[:, :, :3]), mask


def template_match(
    source: np.ndarray,
    name: str,
    roi: tuple[int, int, int, int] | None = None,
) -> dict[str, object]:
    template, mask = load_template(name)
    if roi is None:
        x = y = 0
        search = source
    else:
        x, y, width, height = roi
        search = source[y : y + height, x : x + width]
    if template.shape[0] > search.shape[0] or template.shape[1] > search.shape[1]:
        raise RuntimeError(f"Template {name} does not fit its search ROI")
    if mask is None:
        result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, location = cv2.minMaxLoc(result)
    else:
        result = cv2.matchTemplate(
            search, template, cv2.TM_SQDIFF_NORMED, mask=mask
        )
        result = np.nan_to_num(result, nan=np.inf, posinf=np.inf, neginf=np.inf)
        difference, _, location, _ = cv2.minMaxLoc(result)
        confidence = 1.0 - float(difference)
    location = (x + location[0], y + location[1])
    confidence = max(0.0, min(1.0, float(confidence)))
    return {
        "location": point_dict(location),
        "size": {"width": template.shape[1], "height": template.shape[0]},
        "center": point_dict(
            (
                location[0] + template.shape[1] // 2,
                location[1] + template.shape[0] // 2,
            )
        ),
        "confidence": confidence,
    }


def neutral_digit_components(
    image: np.ndarray, rect: tuple[int, int, int, int]
) -> tuple[np.ndarray, list[tuple[int, int, int, int, int, int]]]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    neutral = ((hsv[:, :, 1] <= 40) & (hsv[:, :, 2] >= 160)).astype(np.uint8)
    x, y, width, height = rect
    search = np.zeros(neutral.shape, dtype=np.uint8)
    search[y : y + height, x : x + width] = neutral[
        y : y + height, x : x + width
    ]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(search, 8)
    components: list[tuple[int, int, int, int, int, int]] = []
    for component in range(1, count):
        left, top, component_width, component_height, area = (
            int(value) for value in stats[component]
        )
        if component_height >= 18 and area >= 100:
            components.append(
                (
                    component,
                    left,
                    top,
                    component_width,
                    component_height,
                    area,
                )
            )
    components.sort(key=lambda item: item[1])
    return labels, components


def normalize_glyph(mask: np.ndarray, width: int = 24, height: int = 36) -> np.ndarray:
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
    left = (width - resized_width) // 2
    top = (height - resized_height) // 2
    result[top : top + resized_height, left : left + resized_width] = resized > 0
    return result


def glyph_similarity(left: np.ndarray, right: np.ndarray) -> float:
    union = np.count_nonzero((left > 0) | (right > 0))
    intersection = np.count_nonzero((left > 0) & (right > 0))
    return float(intersection / union)


def read_balance(frame: np.ndarray) -> dict[str, object]:
    labels, components = neutral_digit_components(
        frame, CALIBRATED_ROIS["sky_stone_digits"]
    )
    if len(components) != 4:
        raise RuntimeError(f"Expected four Sky Stone digits, found: {components}")
    template_masks = {
        digit: normalize_glyph(
            read_png(
                ROOT / "assets" / "templates" / f"sky_stone_digit_{digit}.png"
            )[:, :, 3]
            > 0
        )
        for digit in "0123456789"
    }
    parsed: list[str] = []
    observations: list[dict[str, object]] = []
    for component, x, y, width, height, area in components:
        glyph = normalize_glyph(labels[y : y + height, x : x + width] == component)
        scores = {
            digit: glyph_similarity(glyph, template_mask)
            for digit, template_mask in template_masks.items()
        }
        digit, confidence = max(scores.items(), key=lambda item: item[1])
        parsed.append(digit)
        observations.append(
            {
                "digit": digit,
                "bounds": rect_dict((x, y, width, height)),
                "area": area,
                "confidence": confidence,
                "runner_up": max(score for key, score in scores.items() if key != digit),
            }
        )
    value = int("".join(parsed))
    if value != 3840 or min(item["confidence"] for item in observations) < 0.80:
        raise RuntimeError(f"Unexpected Sky Stone parse: {value} {observations}")
    return {
        "value": value,
        "digit_roi": rect_dict(CALIBRATED_ROIS["sky_stone_digits"]),
        "digits": observations,
        "accepted_confidence": 0.80,
    }


def purchase_buttons(frame: np.ndarray) -> list[dict[str, int]]:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(
        hsv,
        np.array((40, 65, 20), dtype=np.uint8),
        np.array((90, 255, 255), dtype=np.uint8),
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(green, 8)
    result: list[dict[str, int]] = []
    for component in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[component])
        if x >= 1800 and width >= 300 and height >= 90 and area >= 25000:
            result.append(
                {
                    **rect_dict((x, y, width, height)),
                    "area": area,
                    "center_x": x + width // 2,
                    "center_y": y + height // 2,
                }
            )
    result.sort(key=lambda entry: entry["y"])
    return result


def build_manifest() -> dict[str, object]:
    main_shop_layout = yaml.safe_load(
        MAIN_SHOP_LAYOUT_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    if (
        main_shop_layout.get("status") != "operator_confirmed_passed"
        or main_shop_layout.get("expanded_search", {}).get("roi")
        != rect_dict(CALIBRATED_ROIS["main_shop_icon"])
        or main_shop_layout.get("expanded_search", {}).get("confidence", 0.0) < 0.99
        or max(
            control.get("confidence", 1.0)
            for control in main_shop_layout.get("shop_negative_controls", {}).values()
        )
        >= 0.93
        or main_shop_layout.get("click", {}).get("use_recognized_anchor") is not True
        or main_shop_layout.get("criteria_all_met") is not True
        or main_shop_layout.get("game_input_sent") is not False
        or main_shop_layout.get("screenshots_persisted") is not False
    ):
        raise RuntimeError("Main-shop activity-layout evidence is incomplete")
    insufficient_funds = yaml.safe_load(
        INSUFFICIENT_FUNDS_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    if insufficient_funds.get("baseline_client_size") != {
        "width": BASELINE_WIDTH,
        "height": BASELINE_HEIGHT,
    }:
        raise RuntimeError("Insufficient-funds evidence uses the wrong client baseline")
    if insufficient_funds.get("calibrated", {}).get("purchase_result_roi") != rect_dict(
        CALIBRATED_ROIS["purchase_result"]
    ):
        raise RuntimeError("Insufficient-funds evidence uses a different purchase-result ROI")
    if insufficient_funds.get("positive_evidence", {}).get("confidence", 0.0) < 0.999:
        raise RuntimeError("Insufficient-funds positive evidence is too weak")
    insufficient_funds_live = yaml.safe_load(
        INSUFFICIENT_FUNDS_LIVE_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    if (
        insufficient_funds_live.get("status")
        != "operator_confirmed_passed"
        or insufficient_funds_live.get("criteria_all_met") is not True
        or insufficient_funds_live.get("game_input_sent") is not False
        or insufficient_funds_live.get("screenshots_persisted") is not False
    ):
        raise RuntimeError("Insufficient-funds live recognition evidence is incomplete")

    frames: dict[str, np.ndarray] = {}
    sources: dict[str, object] = {}
    for role, spec in SOURCE_SPECS.items():
        frames[role], sources[role] = verified_client(role, spec)

    matches = {
        "main_shop_icon": {
            "main": template_match(
                frames["main"], "main_shop_icon", CALIBRATED_ROIS["main_shop_icon"]
            ),
            "shop_top_negative": template_match(
                frames["shop_top"],
                "main_shop_icon",
                CALIBRATED_ROIS["main_shop_icon"],
            ),
            "shop_bottom_negative": template_match(
                frames["shop_bottom"],
                "main_shop_icon",
                CALIBRATED_ROIS["main_shop_icon"],
            ),
        },
        "shop_refresh_button": {
            role: template_match(
                frames[role],
                "shop_refresh_button",
                CALIBRATED_ROIS["shop_refresh_button"],
            )
            for role in ("main", "shop_top", "shop_bottom")
        },
        "shop_exit_icon": {
            role: template_match(frames[role], "shop_exit_icon")
            for role in ("shop_top", "shop_bottom")
        },
        "sky_stone_icon": {
            role: template_match(
                frames[role], "sky_stone_icon", CALIBRATED_ROIS["sky_stone_icon"]
            )
            for role in ("shop_top", "shop_bottom")
        },
        "refresh_confirm_dialog": {
            "prompt": template_match(
                frames["refresh_confirm"],
                "refresh_confirm_prompt",
                CALIBRATED_ROIS["refresh_confirm_prompt"],
            ),
            "button": template_match(
                frames["refresh_confirm"],
                "refresh_confirm_button",
                CALIBRATED_ROIS["refresh_confirm_button"],
            ),
            "purchase_prompt_negative": template_match(
                frames["purchase_confirm"],
                "refresh_confirm_prompt",
                CALIBRATED_ROIS["refresh_confirm_prompt"],
            ),
            "purchase_button_negative": template_match(
                frames["purchase_confirm"],
                "refresh_confirm_button",
                CALIBRATED_ROIS["refresh_confirm_button"],
            ),
        },
        "purchase_confirm_dialog": {
            "friendship_points": template_match(
                frames["purchase_confirm"],
                "friendship_points_confirm",
                CALIBRATED_ROIS["confirm_item"],
            ),
            "covenant_bookmark_negative": template_match(
                frames["purchase_confirm"],
                "covenant_bookmark_confirm",
                CALIBRATED_ROIS["confirm_item"],
            ),
            "mystic_medal_negative": template_match(
                frames["purchase_confirm"],
                "mystic_medal_confirm",
                CALIBRATED_ROIS["confirm_item"],
            ),
            "button": template_match(
                frames["purchase_confirm"],
                "confirm_button",
                CALIBRATED_ROIS["confirm_button"],
            ),
            "refresh_identity_negative": template_match(
                frames["refresh_confirm"],
                "friendship_points_confirm",
                CALIBRATED_ROIS["confirm_item"],
            ),
            "refresh_button_negative": template_match(
                frames["refresh_confirm"],
                "confirm_button",
                CALIBRATED_ROIS["confirm_button"],
            ),
        },
    }
    if matches["main_shop_icon"]["main"]["confidence"] < 0.99:
        raise RuntimeError(f"Weak main-shop evidence: {matches['main_shop_icon']}")
    if max(
        matches["main_shop_icon"][role]["confidence"]
        for role in ("shop_top_negative", "shop_bottom_negative")
    ) >= 0.93:
        raise RuntimeError(f"Main-shop negative frame matched: {matches['main_shop_icon']}")
    if min(
        matches["shop_refresh_button"][role]["confidence"]
        for role in ("shop_top", "shop_bottom")
    ) < 0.99:
        raise RuntimeError(f"Weak refresh evidence: {matches['shop_refresh_button']}")
    if min(
        matches["shop_exit_icon"][role]["confidence"]
        for role in ("shop_top", "shop_bottom")
    ) < 0.99:
        raise RuntimeError(f"Shop frames are not aligned: {matches['shop_exit_icon']}")
    refresh_dialog = matches["refresh_confirm_dialog"]
    if min(refresh_dialog[key]["confidence"] for key in ("prompt", "button")) < 0.99:
        raise RuntimeError(f"Weak refresh-confirm evidence: {refresh_dialog}")
    if max(
        refresh_dialog[key]["confidence"]
        for key in ("purchase_prompt_negative", "purchase_button_negative")
    ) >= 0.90:
        raise RuntimeError(f"Refresh dialog matched purchase frame: {refresh_dialog}")
    purchase_dialog = matches["purchase_confirm_dialog"]
    if min(
        purchase_dialog[key]["confidence"]
        for key in ("friendship_points", "button")
    ) < 0.99:
        raise RuntimeError(f"Weak purchase-confirm evidence: {purchase_dialog}")
    if max(
        purchase_dialog[key]["confidence"]
        for key in (
            "covenant_bookmark_negative",
            "mystic_medal_negative",
            "refresh_identity_negative",
            "refresh_button_negative",
        )
    ) >= 0.90:
        raise RuntimeError(f"Purchase dialog identity is ambiguous: {purchase_dialog}")

    top_header = cv2.cvtColor(
        frames["shop_top"][:130], cv2.COLOR_BGR2GRAY
    ).astype(np.float32)
    bottom_header = cv2.cvtColor(
        frames["shop_bottom"][:130], cv2.COLOR_BGR2GRAY
    ).astype(np.float32)
    shift, response = cv2.phaseCorrelate(top_header, bottom_header)
    if abs(shift[0]) >= 0.1 or abs(shift[1]) >= 0.75 or response < 0.90:
        raise RuntimeError(f"Shop crop registration failed: {shift}, {response}")

    top_buttons = purchase_buttons(frames["shop_top"])
    bottom_buttons = purchase_buttons(frames["shop_bottom"])
    if [entry["y"] for entry in top_buttons] != [250, 513, 776, 1039]:
        raise RuntimeError(f"Unexpected top purchase-button rows: {top_buttons}")
    if [entry["y"] for entry in bottom_buttons] != [122, 383, 646, 908, 1171]:
        raise RuntimeError(f"Unexpected bottom purchase-button rows: {bottom_buttons}")

    friendship = template_match(frames["shop_bottom"], "friendship_points")
    if friendship["location"] != {"x": 1003, "y": 824} or friendship["confidence"] < 0.99:
        raise RuntimeError(f"Friendship Points slot evidence changed: {friendship}")

    return {
        "schema_version": 1,
        "method": (
            "hash-pinned source windows; paired edge gradients locate exact fixed-size "
            "client crops, which are processed in memory and never written to the project"
        ),
        "baseline_client_size": {
            "width": BASELINE_WIDTH,
            "height": BASELINE_HEIGHT,
        },
        "sources": sources,
        "alignment": {
            "shop_header_phase_shift": {"x": float(shift[0]), "y": float(shift[1])},
            "shop_header_phase_response": float(response),
        },
        "matches": matches,
        "sky_stone_balance": {
            "shop_top": read_balance(frames["shop_top"]),
            "shop_bottom": read_balance(frames["shop_bottom"]),
        },
        "purchase_button_rows": {
            "shop_top": top_buttons,
            "shop_bottom": bottom_buttons,
        },
        "target_slot_evidence": {"friendship_points_bottom_5": friendship},
        "calibrated": {
            "rois": {
                key: rect_dict(value) for key, value in CALIBRATED_ROIS.items()
            },
            "points": {
                key: point_dict(value) for key, value in CALIBRATED_POINTS.items()
            },
            "slots": [
                {
                    "id": slot_id,
                    "screen": screen,
                    "order": order,
                    "item_roi": rect_dict(item_roi),
                    "buy_point": point_dict(buy_point),
                }
                for slot_id, screen, order, item_roi, buy_point in CALIBRATED_SLOTS
            ],
            "scroll_cursor_point": point_dict(SCROLL_CURSOR_POINT),
            "scroll_delta": -120,
            "scroll_repetitions": 6,
            "scroll_interval_ms": 100,
            "scroll_settle_ms": 800,
            "scroll_minimum_upward_shift_px": 300,
            "scroll_difference_threshold": 8,
            "scroll_minimum_changed_fraction": 0.30,
            "anchor_confidence": 0.93,
            "sky_stone_digit_confidence": 0.80,
            "sky_stone_digit_margin": 0.08,
            "sky_stone_digits_offset": point_dict(SKY_STONE_DIGITS_OFFSET),
        },
        "live_scroll_validation": {
            "date": "2026-08-24",
            "windows_admin": True,
            "logical_cursor": point_dict(SCROLL_CURSOR_POINT),
            "delta_per_event": -120,
            "events": 6,
            "total_delta": -720,
            "interval_ms": 100,
            "settle_ms": 800,
            "inventory_difference": {
                "mean_absolute_difference": 25.91128274268105,
                "changed_pixel_fraction_over_8": 0.3761459938366718,
                "maximum_difference": 254,
                "phase_shift_x": -0.006271194715964157,
                "phase_shift_y": -393.2850628524841,
                "phase_response": 0.44810639960206583,
            },
            "screenshots_persisted": False,
        },
        "live_recognition_validation": {
            "date": "2026-08-24",
            "windows_admin": True,
            "sample_count_per_viewport": 8,
            "configured_timing": {
                "poll_interval_ms": 100,
                "scan_timeout_ms": 3000,
                "stable_frames": 3,
            },
            "input": {
                "clicks": 0,
                "refreshes": 0,
                "delta_per_event": -120,
                "events": 6,
                "interval_ms": 100,
                "settle_ms": 800,
            },
            "top": {
                "time_to_three_frame_stability_ms": 1551.551799999288,
                "refresh_confidence_minimum": 0.9995827909442596,
                "sky_stone_value": 4625,
                "sky_stone_confidence_minimum": 0.8589743589743589,
                "inventory_match_mean_ms": 260.9529750002366,
                "inventory_match_maximum_ms": 291.60570000021835,
                "observed_targets": [],
            },
            "bottom": {
                "time_to_three_frame_stability_ms": 1420.2480000003561,
                "refresh_confidence_minimum": 0.9995920604560524,
                "sky_stone_value": 4625,
                "sky_stone_confidence_minimum": 0.8589743589743589,
                "inventory_match_mean_ms": 221.04763749985068,
                "inventory_match_maximum_ms": 240.17619999904127,
                "observed_targets": [],
            },
            "scroll_phase_shift_y": -393.27936770642566,
            "criteria_all_met": True,
            "target_positive_evidence_observed": False,
            "screenshots_persisted": False,
        },
        "external_calibrations": {
            "main_shop_activity_layout": "main_shop_layout_manifest.yaml",
            "overlay_position": "overlay_position_calibration_manifest.yaml",
            "overlay_capture": "overlay_capture_validation_manifest.yaml",
            "insufficient_funds": "insufficient_funds_manifest.yaml",
            "insufficient_funds_live_recognition": "insufficient_funds_live_validation_manifest.yaml",
        },
        "unresolved": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate full-window screenshots and record exact client calibration"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "assets" / "templates" / "client_calibration_manifest.yaml",
    )
    args = parser.parse_args()
    manifest = build_manifest()
    args.output.resolve().write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
