from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from e7auto.config import Rect
from e7auto.vision import (
    Observation,
    OpenCvGameVision,
    PurchaseOutcome,
    TemplateData,
    measure_inventory_scroll,
    measure_inventory_scroll_stability,
)
from e7auto.config import Point

from .helpers import make_config


class ArrayTemplates:
    def __init__(self, templates: dict[str, np.ndarray]):
        self.templates = templates

    def get(self, key: str) -> TemplateData:
        return TemplateData(self.templates[key])


class DataTemplates:
    def __init__(self, templates: dict[str, TemplateData]):
        self.templates = templates

    def get(self, key: str) -> TemplateData:
        return self.templates[key]


class ThresholdSpyVision(OpenCvGameVision):
    def __init__(self) -> None:
        super().__init__(make_config(), ArrayTemplates({}))
        self.threshold: float | None = None
        self.template_key: str | None = None
        self.roi: Rect | None = None

    def match(
        self,
        frame: object,
        template_key: str,
        roi: Rect,
        threshold: float,
    ) -> Observation | None:
        self.threshold = threshold
        self.template_key = template_key
        self.roi = roi
        return None


def test_main_shop_entry_uses_strict_anchor_threshold() -> None:
    vision = ThresholdSpyVision()
    assert vision.main_shop_icon(np.zeros((1, 1, 3), dtype=np.uint8)) is None
    assert vision.threshold == make_config().anchor_confidence


def test_shop_exit_uses_calibrated_roi_and_strict_anchor_threshold() -> None:
    vision = ThresholdSpyVision()
    assert vision.shop_exit_icon(np.zeros((1, 1, 3), dtype=np.uint8)) is None
    config = make_config()
    assert vision.template_key == "shop_exit_icon"
    assert vision.roi == config.rois["shop_exit_icon"]
    assert vision.threshold == config.anchor_confidence


def test_synthetic_template_match_uses_roi_and_confidence() -> None:
    config = make_config()
    template = np.zeros((5, 5, 3), dtype=np.uint8)
    template[1:4, 2] = 255
    template[2, 1:4] = 255
    frame = np.full((80, 100, 3), 13, dtype=np.uint8)
    frame[22:27, 33:38] = template
    vision = OpenCvGameVision(config, ArrayTemplates({"needle": template}))
    found = vision.match(frame, "needle", Rect(20, 10, 40, 30), 0.95)
    assert found is not None
    assert found.anchor.x == 35
    assert found.anchor.y == 24
    assert found.confidence >= 0.95


def test_synthetic_template_outside_roi_is_not_seen() -> None:
    config = make_config()
    template = np.arange(75, dtype=np.uint8).reshape(5, 5, 3)
    frame = np.zeros((80, 100, 3), dtype=np.uint8)
    frame[50:55, 50:55] = template
    vision = OpenCvGameVision(config, ArrayTemplates({"needle": template}))
    assert vision.match(frame, "needle", Rect(0, 0, 20, 20), 0.99) is None


def test_alpha_mask_ignores_wallpaper_but_requires_foreground() -> None:
    config = make_config()
    template = np.zeros((7, 9, 3), dtype=np.uint8)
    mask = np.zeros((7, 9), dtype=np.uint8)
    foreground = {
        (1, 1): (245, 245, 245),
        (1, 2): (210, 210, 210),
        (2, 2): (120, 120, 120),
        (4, 5): (230, 230, 230),
        (5, 6): (90, 90, 90),
        (5, 7): (250, 250, 250),
    }
    for (y, x), color in foreground.items():
        template[y, x] = color
        mask[y, x] = 255
    vision = OpenCvGameVision(
        config,
        DataTemplates({"masked": TemplateData(template, mask)}),
    )
    roi = Rect(0, 0, 30, 25)
    anchors = []
    for background in ((180, 80, 30), (35, 140, 210)):
        frame = np.full((25, 30, 3), background, dtype=np.uint8)
        for (y, x), color in foreground.items():
            frame[8 + y, 10 + x] = color
        found = vision.match(frame, "masked", roi, 0.999)
        assert found is not None
        anchors.append(found.anchor)
    assert anchors[0] == anchors[1] == Point(14, 11)

    absent = np.full((25, 30, 3), (180, 80, 30), dtype=np.uint8)
    assert vision.match(absent, "masked", roi, 0.95) is None


def test_sky_stone_digits_roi_follows_detected_icon() -> None:
    config = replace(make_config(), sky_stone_digits_offset=Point(57, 16))
    icon_template = np.zeros((75, 62, 3), dtype=np.uint8)
    vision = OpenCvGameVision(
        config,
        DataTemplates({"sky_stone_icon": TemplateData(icon_template)}),
    )
    frame = np.zeros((1306, 2322, 3), dtype=np.uint8)
    icon = Observation(
        "sky_stone_icon",
        0.99,
        config.rois["sky_stone_icon"],
        Point(1502, 59),
    )

    assert vision._sky_stone_digits_roi(frame, icon) == Rect(1528, 38, 30, 10)


def test_sky_stone_digits_roi_fails_closed_when_derived_region_is_outside_frame() -> None:
    config = replace(make_config(), sky_stone_digits_offset=Point(57, 16))
    vision = OpenCvGameVision(
        config,
        DataTemplates(
            {"sky_stone_icon": TemplateData(np.zeros((75, 62, 3), dtype=np.uint8))}
        ),
    )
    frame = np.zeros((80, 100, 3), dtype=np.uint8)
    icon = Observation(
        "sky_stone_icon",
        0.99,
        config.rois["sky_stone_icon"],
        Point(95, 40),
    )

    assert vision._sky_stone_digits_roi(frame, icon) is None


def test_inventory_matches_sort_by_slot_order_only() -> None:
    config = make_config()
    vision = OpenCvGameVision(config, ArrayTemplates({}))

    def fake_match(frame, template_key, roi, threshold):
        pairs = {
            ("wood", config.slots[1].item_roi),
            ("ore", config.slots[0].item_roi),
        }
        return Observation(template_key, 0.99, roi, Point(roi.x, roi.y)) if (template_key, roi) in pairs else None

    vision._match_bgr = fake_match  # type: ignore[method-assign]
    matches = vision.scan_inventory(np.zeros((80, 100, 3), dtype=np.uint8), "top")
    assert [(item.target_id, item.slot_order) for item in matches] == [("ore", 0), ("wood", 1)]


def test_inventory_scan_classifies_the_highest_confidence_state_per_slot() -> None:
    config = make_config()
    vision = OpenCvGameVision(config, ArrayTemplates({}))
    slot_roi = config.slots[0].item_roi
    scores = {
        ("wood", slot_roi): 0.961507,
        ("wood_purchased", slot_roi): 0.999,
        ("ore", config.slots[1].item_roi): 0.999,
        ("ore_purchased", config.slots[1].item_roi): 0.923,
    }

    def fake_match(frame, template_key, roi, threshold):
        confidence = scores.get((template_key, roi))
        if confidence is None or confidence < threshold:
            return None
        return Observation(template_key, confidence, roi, Point(roi.x, roi.y))

    vision._match_bgr = fake_match  # type: ignore[method-assign]
    matches = vision.scan_inventory(np.zeros((80, 100, 3), dtype=np.uint8), "top")

    assert [(item.target_id, item.is_purchased) for item in matches] == [
        ("wood", True),
        ("ore", False),
    ]


def test_inventory_scan_prepares_bgr_once_and_prunes_targets_and_slots() -> None:
    config = make_config(include_friendship=True)

    class InventorySpyVision(OpenCvGameVision):
        def __init__(self) -> None:
            super().__init__(config, ArrayTemplates({}))
            self.bgr_calls = 0
            self.match_calls: list[tuple[str, Rect]] = []

        def _bgr(self, frame):  # type: ignore[override]
            self.bgr_calls += 1
            return np.ascontiguousarray(frame[:, :, :3])

        def _match_bgr(self, frame, template_key, roi, threshold):
            self.match_calls.append((template_key, roi))
            return None

    vision = InventorySpyVision()
    frame = np.zeros((80, 100, 4), dtype=np.uint8)

    assert vision.scan_inventory(
        frame,
        "top",
        frozenset({"wood"}),
        frozenset({"top-2"}),
    ) == ()
    assert vision.bgr_calls == 1
    assert vision.match_calls == [
        ("wood", config.slots[0].item_roi),
        ("wood_purchased", config.slots[0].item_roi),
    ]


def test_inventory_scan_is_equivalent_for_bgr_and_bgra_frames() -> None:
    config = make_config()
    available = np.arange(75, dtype=np.uint8).reshape(5, 5, 3)
    purchased = np.flip(available, axis=1).copy()
    vision = OpenCvGameVision(
        config,
        ArrayTemplates(
            {
                "wood": available,
                "wood_purchased": purchased,
            }
        ),
    )
    bgr = np.full((80, 100, 3), 17, dtype=np.uint8)
    bgr[15:20, 17:22] = available
    bgra = np.dstack((bgr, np.full((80, 100), 255, dtype=np.uint8)))

    bgr_matches = vision.scan_inventory(
        bgr,
        "top",
        frozenset({"wood"}),
    )
    bgra_matches = vision.scan_inventory(
        bgra,
        "top",
        frozenset({"wood"}),
    )

    assert bgr_matches == bgra_matches
    assert [(item.target_id, item.slot_id) for item in bgr_matches] == [
        ("wood", "top-1")
    ]


def test_confirmation_requires_matching_target_identity_and_button() -> None:
    config = make_config()
    vision = OpenCvGameVision(config, ArrayTemplates({}))
    visible = {"wood_confirm", "confirm_button"}

    def fake_match(frame, template_key, roi, threshold):
        if template_key not in visible:
            return None
        return Observation(template_key, 0.98, roi, Point(roi.x, roi.y))

    vision.match = fake_match  # type: ignore[method-assign]
    frame = np.zeros((80, 100, 3), dtype=np.uint8)
    assert vision.confirm_dialog(frame, "wood") is not None
    assert vision.confirm_dialog(frame, "ore") is None
    visible.remove("confirm_button")
    assert vision.confirm_dialog(frame, "wood") is None


def test_refresh_confirmation_requires_prompt_and_blue_button() -> None:
    config = make_config()
    vision = OpenCvGameVision(config, ArrayTemplates({}))
    visible = {"refresh_confirm_prompt", "refresh_confirm_button"}

    def fake_match(frame, template_key, roi, threshold):
        if template_key not in visible:
            return None
        return Observation(template_key, 0.98, roi, Point(roi.x, roi.y))

    vision.match = fake_match  # type: ignore[method-assign]
    frame = np.zeros((80, 100, 3), dtype=np.uint8)
    assert vision.refresh_confirm_dialog(frame) is not None
    visible.remove("refresh_confirm_prompt")
    assert vision.refresh_confirm_dialog(frame) is None
    visible.add("refresh_confirm_prompt")
    visible.remove("refresh_confirm_button")
    assert vision.refresh_confirm_dialog(frame) is None


def test_purchase_success_is_target_specific_and_scoped_to_original_slot() -> None:
    config = make_config()
    vision = OpenCvGameVision(config, ArrayTemplates({}))
    slot_roi = config.slots[0].item_roi

    def fake_match(frame, template_key, roi, threshold):
        if template_key == "wood_purchased" and roi == slot_roi:
            return Observation(template_key, 0.98, roi, Point(roi.x, roi.y))
        return None

    vision.match = fake_match  # type: ignore[method-assign]
    frame = np.zeros((80, 100, 3), dtype=np.uint8)
    assert vision.purchase_outcome(frame, "wood", slot_roi) is PurchaseOutcome.SUCCESS
    assert vision.purchase_outcome(frame, "ore", slot_roi) is PurchaseOutcome.PENDING


def test_inventory_scroll_measurement_reports_calibrated_upward_content_shift() -> None:
    rng = np.random.default_rng(7)
    before_gray = rng.integers(0, 256, (120, 160), dtype=np.uint8)
    after_gray = np.roll(before_gray, -35, axis=0)
    before = np.repeat(before_gray[:, :, None], 3, axis=2)
    after = np.repeat(after_gray[:, :, None], 3, axis=2)

    measured = measure_inventory_scroll(
        before,
        after,
        Rect(0, 0, 160, 120),
        difference_threshold=8,
    )

    assert measured.phase_shift_y == pytest.approx(-35.0, abs=0.01)
    assert measured.phase_response > 0.99
    assert measured.changed_fraction > 0.90


def test_downsampled_scroll_stability_reports_shift_in_original_pixels() -> None:
    rng = np.random.default_rng(11)
    before_gray = rng.integers(0, 256, (160, 200), dtype=np.uint8)
    after_gray = np.roll(before_gray, -12, axis=0)
    before = np.repeat(before_gray[:, :, None], 3, axis=2)
    after = np.repeat(after_gray[:, :, None], 3, axis=2)

    measured = measure_inventory_scroll_stability(
        before,
        after,
        Rect(0, 0, 200, 160),
        difference_threshold=8,
        downsample_factor=4,
    )

    assert measured.phase_shift_y == pytest.approx(-12.0, abs=0.5)
    assert measured.phase_response > 0.80
