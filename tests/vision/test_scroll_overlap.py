from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from e7auto.core.types import Rect, Size
from e7auto.features.shop.scrolling import ScrollProgress, ScrollServices, scroll_to_bottom
from e7auto.vision.frames import AdaptedFrame, CoordinateTransform
from e7auto.features.shop.vision import ShopVision as OpenCvGameVision
from e7auto.features.shop.scroll_vision import measure_inventory_scroll, measure_inventory_scroll_stability, prepare_scroll_overlap_reference, verify_scroll_overlap
from tests.helpers import FakeClock, FakeLogger, make_config
from tests.helpers.paths import FIXTURES_DIR


ROI = Rect(0, 0, 1320, 1180)


@pytest.fixture(scope="module")
def screenshots():
    # Inventory ROIs only, from the user's 2026-09-15 top/bottom screenshots.
    directory = FIXTURES_DIR / "shop"
    top = cv2.imread(str(directory / "scroll_overlap_top.png"))
    bottom = cv2.imread(str(directory / "scroll_overlap_bottom.png"))
    assert top is not None and bottom is not None
    return top, bottom


@pytest.mark.parametrize("shift_y", [-393.212, -393.409])
def test_real_inventory_overlap_accepts_correct_subpixel_displacement(screenshots, shift_y):
    top, bottom = screenshots
    ref = prepare_scroll_overlap_reference(top, ROI, 4)
    result = verify_scroll_overlap(ref, bottom, ROI, 0.0, shift_y)
    assert result.accepted
    assert result.passed_blocks == 4
    assert min(result.block_scores) >= 0.75
    assert result.overlap_height_fraction == pytest.approx(1 - abs(shift_y) / ROI.height)


@pytest.mark.parametrize("shift_y", [-300, -350, -500, 0])
def test_real_inventory_overlap_rejects_wrong_displacement(screenshots, shift_y):
    top, bottom = screenshots
    result = verify_scroll_overlap(prepare_scroll_overlap_reference(top, ROI, 4), bottom, ROI, 0, shift_y)
    assert not result.accepted
    assert result.reason == "insufficient_matching_blocks"


def test_stationary_inventory_cannot_validate_fabricated_motion(screenshots):
    top, _ = screenshots
    result = verify_scroll_overlap(prepare_scroll_overlap_reference(top, ROI, 4), top, ROI, 0, -393.409)
    assert not result.accepted


def test_main_algorithm_on_real_screenshots_still_passes(screenshots):
    top, bottom = screenshots
    result = measure_inventory_scroll(top, bottom, ROI, 8)
    assert result.phase_shift_y == pytest.approx(-393.212, abs=0.5)
    assert result.changed_fraction > 0.30


def test_uniform_images_do_not_pass_correlation(screenshots):
    top, _ = screenshots
    blank = np.full_like(top, 100)
    result = verify_scroll_overlap(prepare_scroll_overlap_reference(blank, ROI, 4), blank, ROI, 0, -393)
    assert not result.accepted
    assert result.block_scores == (None, None, None, None)
    assert result.passed_blocks == 0


@pytest.mark.parametrize("shift_x,shift_y,reason", [
    (0, -591, "insufficient_overlap"), (4.01, -393, "horizontal_shift"),
    (float("nan"), -393, "non_finite_shift"), (0, float("inf"), "non_finite_shift"),
])
def test_overlap_rejects_invalid_geometry_before_processing_current_frame(screenshots, shift_x, shift_y, reason):
    top, _ = screenshots
    ref = prepare_scroll_overlap_reference(top, ROI, 4)
    # An inaccessible current frame proves rejection precedes any pixel processing.
    result = verify_scroll_overlap(ref, None, ROI, shift_x, shift_y)
    assert not result.accepted
    assert result.reason == reason


def test_overlap_reuses_existing_adapted_roi(screenshots):
    top, bottom = screenshots
    scaled = cv2.resize(bottom, (660, 590), interpolation=cv2.INTER_AREA)
    current = AdaptedFrame(scaled, CoordinateTransform(Size(1320, 1180), Size(660, 590)))
    current.normalized_roi(ROI)
    count = current.normalization_count
    ref = prepare_scroll_overlap_reference(top, ROI, 4)
    verify_scroll_overlap(ref, current, ROI, 0, -393.212)
    assert current.normalization_count == count


@pytest.mark.parametrize("bad_blocks,accepted", [(0, True), (1, True), (2, False)])
def test_overlap_requires_three_textured_matching_blocks(bad_blocks, accepted):
    # Build in downsampled space, then expand: avoids texture dilution during resize.
    rng = np.random.default_rng(47)
    gray = rng.integers(0, 256, (80, 80), dtype=np.uint8)
    after = gray.copy()
    for row, column in [(slice(2, 40), slice(2, 40)), (slice(2, 40), slice(40, 78))][:bad_blocks]:
        after[row, column] = 100
    current = np.repeat(np.repeat(after, 4, axis=0), 4, axis=1)
    current = np.repeat(current[:, :, None], 3, axis=2)
    result = verify_scroll_overlap(gray.astype(np.float32), current, Rect(0, 0, 320, 320), 0, 0)
    assert result.accepted is accepted
    assert result.passed_blocks == 4 - bad_blocks


def test_game_vision_exposes_preparation_and_verification(screenshots):
    top, bottom = screenshots
    base = make_config()
    config = replace(base, rois={**base.rois, "inventory_list": ROI})
    vision = OpenCvGameVision(config, None)
    reference = vision.prepare_scroll_overlap_reference(top)
    assert reference.shape == (295, 330)
    assert vision.verify_scroll_overlap(reference, bottom, 0, -393.212).accepted


def test_real_primary_path_has_no_additional_image_operations(screenshots, monkeypatch):
    top, bottom = screenshots
    frames = iter([top, top, bottom, bottom, bottom])
    clock, logger, progress = FakeClock(), FakeLogger(), ScrollProgress()
    calls = {name: 0 for name in ("cvtColor", "resize", "phaseCorrelate", "matchTemplate", "warpAffine")}
    captures = 0

    for name in calls:
        original = getattr(cv2, name)

        def counted(*args, _name=name, _original=original, **kwargs):
            calls[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(cv2, name, counted)

    def capture():
        nonlocal captures
        captures += 1
        return next(frames)

    def forbidden_overlap(*args):
        pytest.fail("Primary success must never enter the overlap path")

    services = ScrollServices(
        capture=capture, dispatch_scroll=lambda *_: None, checkpoint=lambda: None,
        sleep=clock.sleep, active_monotonic=clock.monotonic,
        measure_stability=lambda a, b: measure_inventory_scroll_stability(a, b, ROI, 8, 4),
        measure_movement=lambda a, b: measure_inventory_scroll(a, b, ROI, 8),
        verify_overlap=forbidden_overlap, inventory_height=ROI.height, logger=logger,
    )
    returned = scroll_to_bottom(make_config().scroll, 3, services, progress)
    assert captures == 5  # B, F1 (top), F2..F4 (bottom).
    assert len(returned) == 3
    assert calls == {"cvtColor": 8, "resize": 6, "phaseCorrelate": 4, "matchTemplate": 0, "warpAffine": 0}
    logged = next(fields for event, fields in logger.events if event == "scroll_settle_trace")
    assert logged["verification_method"] == "primary"
    assert logged["fallback_checks"] == 0
