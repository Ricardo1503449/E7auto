"""Approved penguin controls and price-only recognition (no balance reads)."""
from __future__ import annotations

from e7auto.features.penguin.contracts import PenguinDialog

import cv2
import numpy as np

from e7auto.configuration.models import AppConfig, ConfigError
from e7auto.configuration.schema import PENGUIN_CONTROLS
from e7auto.core.types import Rect
from e7auto.resources.templates import TemplateData, TemplateRepository
from e7auto.vision.matching import TemplateMatcher
from e7auto.vision.digits import DigitMatcher
from e7auto.vision.network import NetworkVision
from e7auto.core.observations import Observation


CONTROLS = PENGUIN_CONTROLS


class _PurchaseBodyRepository:
    def __init__(self, data: TemplateData):
        self.data = data

    def get(self, key: str) -> TemplateData:
        return self.data


class PenguinVision:
    def __init__(self, config: AppConfig, templates: TemplateRepository):
        self._config = config
        self._templates = templates
        self._matcher = TemplateMatcher(templates)
        self._digits = DigitMatcher(templates, config.template_paths)
        self._network = NetworkVision(config, self._matcher)
        approved = templates.get("penguin_penguin_buy_5100")
        mask = approved.mask.copy()
        r = config.penguin_price_rect
        mask[r.y:r.bottom, r.x:r.right] = 0
        if not np.any(mask):
            raise ConfigError(["vision.penguin_price_rect removes the entire purchase-button mask"])
        # Only the amount is excluded for locating a dialog whose price changed.
        # Full 5100 recognition still uses the unmodified approved template.
        self._purchase_body = TemplateData(approved.image, mask)
        self._body_matcher = TemplateMatcher(_PurchaseBodyRepository(self._purchase_body))

    def match(self, frame, template_key: str, roi: Rect, threshold: float) -> Observation | None:
        return self._matcher.match(frame, template_key, roi, threshold)

    def match_entry(self, frame, template_key: str, roi: Rect, threshold: float,
                    *, structure_threshold: float) -> Observation | None:
        return self._matcher.match_entry(frame, template_key, roi, threshold,
                                         structure_threshold=structure_threshold)

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
        source = self._matcher.crop(self._matcher.prepare_bgr(frame), roi)
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
        variants, _ = self._digits.template_variants()
        digits = []
        for _, glyph in sorted(glyphs, key=lambda item: item[0]):
            match = self._digits.best_match(glyph, variants)
            if (match.confidence < self._config.penguin_price_digit_confidence
                    or match.margin < self._config.penguin_price_digit_margin):
                return None
            digits.append(match.digit)
        value = int("".join(digits))
        return value if value > 0 else None

    def network_connection_error(self, frame):
        return self._network.network_connection_error(frame)

    def network_retry(self, frame):
        return self._network.network_retry(frame)
