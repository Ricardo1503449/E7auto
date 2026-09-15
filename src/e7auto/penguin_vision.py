"""Approved penguin controls and price-only recognition (no balance reads)."""
from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np

from .config import AppConfig, COMMON_TEMPLATE_KEYS, ConfigError, PENGUIN_CONTROLS, Rect
from .template_manifest import load_template_manifest
from .vision import OpenCvGameVision, TemplateData, TemplateRepository
from .vision_types import Observation


CONTROLS = PENGUIN_CONTROLS


def with_penguin_config(config: AppConfig) -> AppConfig:
    try:
        registered, sizes = load_template_manifest(
            config.template_manifest_paths["penguin"],
            (config.baseline_client_size.width, config.baseline_client_size.height),
            prefix="penguin_",
        )
        expected = {f"penguin_{name}" for name in CONTROLS}
        if set(registered) != expected:
            raise ValueError("penguin catalog must contain exactly the configured controls")
        price = config.penguin_price_rect
        button_width, button_height = sizes["penguin_penguin_buy_5100"]
        if not (0 <= price.x < price.right <= button_width
                and 0 <= price.y < price.bottom <= button_height):
            raise ValueError("vision.penguin_price_rect must fit inside the purchase-button template")
        for key, (width, height) in sizes.items():
            roi_key = "left_icon_column" if key == "penguin_sanctuary_entry" else key
            search = config.rois.get(roi_key)
            if search is None:
                raise ValueError(f"rois.{roi_key} is required")
            if not (0 <= search.x < search.right <= config.baseline_client_size.width
                    and 0 <= search.y < search.bottom <= config.baseline_client_size.height
                    and search.width >= width and search.height >= height):
                raise ValueError(f"rois.{roi_key} must fit the client and contain the template")
        paths = {key: config.template_paths[key] for key in COMMON_TEMPLATE_KEYS}
        paths.update(registered)
        return replace(config, template_paths=paths)
    except (ValueError, KeyError, TypeError) as exc:
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
        r = config.penguin_price_rect
        mask[r.y:r.bottom, r.x:r.right] = 0
        if not np.any(mask):
            raise ConfigError(["vision.penguin_price_rect removes the entire purchase-button mask"])
        # Only the amount is excluded for locating a dialog whose price changed.
        # Full 5100 recognition still uses the unmodified approved template.
        self._purchase_body = TemplateData(approved.image, mask)
        self._body_matcher = OpenCvGameVision(config, _PurchaseBodyRepository(self._purchase_body))

    def control(self, frame: object, name: str) -> Observation | None:
        key = f"penguin_{name}"
        if name == "sanctuary_entry":
            thresholds = self._config.entry_thresholds["penguin"]
            return self.match_entry(
                frame, key, self._config.rois["left_icon_column"], thresholds.color,
                structure_threshold=thresholds.structure,
            )
        return self.match(frame, key, self._config.rois[key], self._config.penguin_control_confidence)

    def dialog(self, frame: object) -> PenguinDialog | None:
        maximum = self.control(frame, "quantity_max")
        cancel = self.control(frame, "purchase_cancel")
        if maximum is None or cancel is None:
            return None
        key = "penguin_penguin_buy_5100"
        # A private one-entry repository avoids mutating the approved repository.
        body = self._body_matcher.match(frame, key, self._config.rois[key], self._config.penguin_control_confidence)
        if body is None:
            return None
        height, width = self._purchase_body.image.shape[:2]
        r = self._config.penguin_price_rect
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
            if (match.confidence < self._config.penguin_price_digit_confidence
                    or match.margin < self._config.penguin_price_digit_margin):
                return None
            digits.append(match.digit)
        value = int("".join(digits))
        return value if value > 0 else None
