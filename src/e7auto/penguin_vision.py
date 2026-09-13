"""Approved penguin controls and price-only recognition (no balance reads)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import hashlib
import json

import cv2
import numpy as np

from .config import AppConfig, ConfigError, Rect
from .vision import OpenCvGameVision, TemplateData, TemplateRepository
from .vision_types import Observation


CONTROLS = (
    "sanctuary_entry", "forest_entry", "growth_altar", "penguin_buy_102",
    "penguin_buy_5100", "quantity_max", "penguin_complete", "reward_close",
    "altar_close", "forest_back", "sanctuary_back", "purchase_currency",
    "purchase_cancel",
)
# Relative to the approved 604 x 118 purchase button; covers the full price field.
PRICE_RECT = Rect(115, 25, 210, 62)


def with_penguin_config(config: AppConfig) -> AppConfig:
    directory = config.source_path.parent.parent / "assets" / "templates" / "penguin"
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["schema_version"] != 1 or manifest["baseline"] != [
            config.baseline_client_size.width, config.baseline_client_size.height,
        ]:
            raise ValueError("penguin calibration baseline mismatch")
        paths, rois = dict(config.template_paths), dict(config.rois)
        for name in CONTROLS:
            entry = manifest["templates"][name]
            path = directory / entry["file"]
            if path.resolve().parent != directory.resolve():
                raise ValueError("invalid penguin template path")
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError(f"approved template changed: {name}")
            x, y, width, height = entry["roi"]
            if not (0 <= x < x + width <= config.baseline_client_size.width
                    and 0 <= y < y + height <= config.baseline_client_size.height):
                raise ValueError(f"invalid penguin ROI: {name}")
            left, top = max(0, x - 24), max(0, y - 24)
            right = min(config.baseline_client_size.width, x + width + 24)
            bottom = min(config.baseline_client_size.height, y + height + 24)
            paths[f"penguin_{name}"] = path
            rois[f"penguin_{name}"] = Rect(left, top, right-left, bottom-top)
        return replace(config, template_paths=paths, rois=rois)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError([f"Penguin templates: {exc}"]) from exc


@dataclass(frozen=True, slots=True)
class PenguinDialog:
    price: int | None
    purchase: Observation
    maximum: Observation
    cancel: Observation
    full_price_button: bool


class _PurchaseBodyRepository:
    def __init__(self, data: TemplateData):
        self.data = data

    def get(self, key: str) -> TemplateData:
        return self.data


class PenguinVision(OpenCvGameVision):
    def __init__(self, config: AppConfig, templates: TemplateRepository):
        super().__init__(config, templates)
        approved = templates.get("penguin_penguin_buy_5100")
        mask = approved.mask.copy()
        r = PRICE_RECT
        mask[r.y:r.bottom, r.x:r.right] = 0
        # Only the amount is excluded for locating a dialog whose price changed.
        # Full 5100 recognition still uses the unmodified approved template.
        self._purchase_body = TemplateData(approved.image, mask)
        self._body_matcher = OpenCvGameVision(config, _PurchaseBodyRepository(self._purchase_body))

    def control(self, frame: object, name: str) -> Observation | None:
        key = f"penguin_{name}"
        return self.match(frame, key, self._config.rois[key], 0.96)

    def dialog(self, frame: object) -> PenguinDialog | None:
        maximum = self.control(frame, "quantity_max")
        cancel = self.control(frame, "purchase_cancel")
        if maximum is None or cancel is None:
            return None
        key = "penguin_penguin_buy_5100"
        # A private one-entry repository avoids mutating the approved repository.
        body = self._body_matcher.match(frame, key, self._config.rois[key], 0.96)
        if body is None:
            return None
        height, width = self._purchase_body.image.shape[:2]
        r = PRICE_RECT
        price_roi = Rect(body.anchor.x - width//2 + r.x,
                         body.anchor.y - height//2 + r.y, r.width, r.height)
        price = self._price(frame, price_roi)
        full = self.control(frame, "penguin_buy_5100") is not None
        return PenguinDialog(price, body, maximum, cancel, full)

    def _price(self, frame: object, roi: Rect) -> int | None:
        source = self._crop(self._bgr(frame), roi)
        hsv = cv2.cvtColor(source, cv2.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 40)
                & (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 170)).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        glyphs = []
        for index in range(1, count):
            x, y, width, height, area = (int(v) for v in stats[index])
            if height < 12 and area < 45:  # thousands separator / isolated noise
                continue
            if height < 20 or height > 40 or area < 35 or x == 0 or x+width >= roi.width:
                return None
            glyphs.append((x, labels[y:y+height, x:x+width] == index))
        if not 1 <= len(glyphs) <= 6:
            return None
        variants, _ = self._sky_stone_template_variants()
        digits = []
        for _, glyph in sorted(glyphs, key=lambda item: item[0]):
            match = self._best_digit_match(glyph, variants)
            if match.confidence < 0.84 or match.margin < 0.09:
                return None
            digits.append(match.digit)
        value = int("".join(digits))
        return value if value > 0 else None
