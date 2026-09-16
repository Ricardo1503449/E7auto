from __future__ import annotations

from pathlib import Path
from tests.helpers.paths import ROOT
from dataclasses import replace

import cv2
import numpy as np
import pytest

from e7auto.core.types import Point, Rect
from e7auto.configuration.loader import load_config
from e7auto.core.observations import Observation
from e7auto.features.shop.vision import ShopVision as OpenCvGameVision
from e7auto.features.shop.contracts import PurchaseOutcome
from e7auto.resources.templates import TemplateRepository





def setup_vision():
    config = load_config(ROOT / "config/internal.yaml")
    return config, OpenCvGameVision(config, TemplateRepository(config))


def sample_frame(config, sample: int, slot_index: int = 0):
    samples = cv2.imread(str(ROOT / "tests/fixtures/shop/purchased_button_samples.png"))
    frame = np.zeros((1306, 2322, 3), dtype=np.uint8)
    point = config.slots[slot_index].buy_point
    frame[point.y - 65:point.y + 65, point.x - 183:point.x + 184] = samples[
        sample * 130:(sample + 1) * 130
    ]
    return frame


@pytest.mark.parametrize("sample,purchased", [
    (0, False), (1, False), (2, False), (3, True), (4, False),
    (5, True), (6, False), (7, False), (8, False),
])
def test_real_button_samples_distinguish_sold_out_from_available(sample, purchased):
    config, vision = setup_vision()
    frame = sample_frame(config, sample)
    outcome = vision.purchase_outcome(frame, "covenant_bookmark", config.slots[0].item_roi)
    expected = PurchaseOutcome.SUCCESS_BUTTON if purchased else PurchaseOutcome.PENDING
    assert outcome is expected


@pytest.mark.parametrize("target", ["covenant_bookmark", "mystic_medal", "friendship_points"])
def test_common_button_works_for_each_previously_confirmed_target(target):
    config, vision = setup_vision()
    frame = sample_frame(config, 5, slot_index=4)
    assert vision.purchase_outcome(frame, target, config.slots[4].item_roi) is PurchaseOutcome.SUCCESS_BUTTON


def test_other_row_and_unknown_slot_cannot_confirm_a_purchase():
    config, vision = setup_vision()
    frame = sample_frame(config, 3, slot_index=1)
    assert vision.purchase_outcome(frame, "covenant_bookmark", config.slots[0].item_roi) is PurchaseOutcome.PENDING
    assert vision.purchase_outcome(frame, "covenant_bookmark", Rect(0, 0, 240, 220)) is PurchaseOutcome.PENDING


@pytest.mark.parametrize("slot_index", [0, 4])
def test_configured_padding_changes_search_without_losing_row_anchor(monkeypatch, slot_index):
    config, _ = setup_vision()
    config = replace(config, purchased_button_padding=Point(5, 7))
    vision = OpenCvGameVision(config, TemplateRepository(config))
    frame = sample_frame(config, 3, slot_index)
    calls = []
    original = vision.match

    def match(frame, key, roi, threshold):
        calls.append(roi)
        return original(frame, key, roi, threshold)

    monkeypatch.setattr(vision, "match", match)
    result = vision.purchased_button(frame, config.slots[slot_index].item_roi)
    assert result is not None
    template = vision._templates.get("purchased_button").image
    center = config.slots[slot_index].buy_point
    assert calls[0].width == template.shape[1] + 10
    assert calls[0].height == template.shape[0] + 14
    assert calls[0].x + calls[0].width // 2 == center.x
    assert calls[0].y + calls[0].height // 2 == center.y


def test_sold_out_button_alone_does_not_identify_inventory_target():
    config, vision = setup_vision()
    frame = sample_frame(config, 3)
    assert vision.scan_inventory(frame, "top") == ()


def test_insufficient_funds_precedes_button_evidence(monkeypatch):
    config, vision = setup_vision()
    frame = sample_frame(config, 3)
    original_match = vision.match

    def funds_match(frame, template_key, roi, threshold):
        if template_key == "insufficient_funds":
            return Observation(template_key, 1.0, roi, Point(roi.x, roi.y))
        return original_match(frame, template_key, roi, threshold)

    monkeypatch.setattr(vision, "match", funds_match)
    assert vision.purchase_outcome(frame, "friendship_points", config.slots[0].item_roi) is PurchaseOutcome.INSUFFICIENT_FUNDS
