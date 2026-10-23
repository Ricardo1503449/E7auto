from dataclasses import replace

import cv2
import numpy as np
import pytest

from e7auto.configuration.loader import load_config
from e7auto.core.types import ClientPoint, ClientRect, Rect, Size
from e7auto.features.shop import scroll_vision
from e7auto.features.shop.vision import ShopVision
from e7auto.vision.frames import CoordinateTransform, FrameView
from tests.helpers.paths import ROOT, FIXTURES_DIR


@pytest.fixture(scope="module")
def config():
    return load_config(ROOT / "config/internal.yaml")


def recent_frames(config, key="after_152707"):
    transform = CoordinateTransform(config.baseline_client_size, config.baseline_client_size)
    with np.load(FIXTURES_DIR / "shop/scroll_rows_recent.npz") as images:
        return tuple(FrameView(images[label].copy(), transform, ClientPoint(950, 110))
                     for label in ("before", key))


def measure(config, before, after):
    return ShopVision(config, None).inventory_scroll_movement(before, after)


@pytest.mark.parametrize("key", ["after_152637", "after_152707"])
@pytest.mark.parametrize("size", [Size(2322, 1306), Size(2321, 1305), Size(2323, 1307),
                                 Size(1920, 1080), Size(1536, 864)])
def test_fixed_background_and_dim_short_name_no_longer_veto_real_scroll(config, key, size):
    frames = recent_frames(config, key)
    if size != config.baseline_client_size:
        scaled = []
        for frame in frames:
            full = np.zeros((1306, 2322, 3), np.uint8)
            full[110:1290, 950:1850] = frame.raw
            scaled.append(FrameView(cv2.resize(full, (size.width, size.height), interpolation=cv2.INTER_AREA),
                                    CoordinateTransform(config.baseline_client_size, size)))
        frames = scaled
    result = measure(config, *frames)
    assert result.content_verified
    assert result.phase_shift_y == pytest.approx(-394, abs=2)
    assert result.content_scores[:2] == (None, None)  # Upper icons are clipped after scrolling.
    assert all(score >= config.scroll.minimum_content_score for score in result.content_scores[2:])
    assert result.changed_fraction > config.scroll.minimum_changed_fraction
    assert measure(config, *frames) == result  # Repeated calls do not retain pixel evidence.


@pytest.mark.parametrize("row_index", [2, 3])
def test_one_matching_row_cannot_authorize_scroll_and_failure_keeps_scores(config, monkeypatch, row_index):
    before, after = recent_frames(config)
    rows = [s.item_roi for s in config.slots if s.screen == "top"]
    row = rows[row_index]
    after.raw[row.y - 394 - 110 - 4:row.bottom - 394 - 110 + 4,
              row.x - 950 - 4:row.right - 950 + 4] = 0
    monkeypatch.setattr(scroll_vision, "_phase_candidates", lambda *_: [(0, -394, .4)])
    result = measure(config, before, after)
    assert not result.content_verified
    assert result.content_reason == "insufficient_matching_rows"
    assert len(result.content_scores) == 4
    assert sum(s is not None and s >= .75 for s in result.content_scores) == 1


def test_clipped_row_and_alignment_window_are_not_partial_evidence(config):
    image = np.random.default_rng(7).integers(0, 256, (200, 120), dtype=np.uint8).astype(np.float32)
    after = np.roll(image, -40, axis=0)
    rows = (ClientRect(10, 20, 50, 30), ClientRect(10, 42, 50, 30),
            ClientRect(10, 60, 50, 30), ClientRect(10, 110, 50, 30))
    scores = scroll_vision._native_content_scores(image, after, 0, -40, config.scroll, 1, 1, rows)
    assert scores[:2] == (None, None)
    assert scores[2:] == pytest.approx((1, 1), abs=1e-6)


def test_all_rows_are_scored_even_if_only_two_pass(config, monkeypatch):
    calls = []
    monkeypatch.setattr(scroll_vision, "_has_content_texture", lambda *_: True)
    def match(search, reference, method):
        assert search.dtype == reference.dtype == np.float32
        scores = [.75, .4, .75, .2]
        surface = np.zeros((9, 9), np.float32)
        surface[4, 4] = scores[len(calls)]
        calls.append(1)
        return surface
    monkeypatch.setattr(cv2, "matchTemplate", match)
    image = np.zeros((200, 120), np.float32)
    rows = tuple(ClientRect(10, y, 50, 20) for y in (45, 80, 115, 150))
    scores = scroll_vision._native_content_scores(image, image, 0, -40, config.scroll, 1, 1, rows)
    assert len(calls) == 4
    assert scores == pytest.approx((.75, .4, .75, .2))


@pytest.mark.parametrize("dx,dy", [(5, -40), (0, -101)])
def test_horizontal_and_overlap_limits_still_reject(config, dx, dy):
    image = np.zeros((200, 120), np.float32)
    rows = (ClientRect(10, 60, 50, 30), ClientRect(10, 110, 50, 30))
    assert scroll_vision._native_content_scores(image, image, dx, dy, config.scroll, 1, 1, rows) == ()


def test_duplicate_or_overlapping_rows_cannot_count_twice(config):
    before, after = recent_frames(config)
    row = config.slots[2].item_roi
    with pytest.raises(ValueError, match="overlap"):
        scroll_vision.measure_inventory_scroll(before, after, config.rois["inventory_content"], 8,
                                              config=config.scroll, item_rois=(row, row))


def test_each_row_must_be_contained_in_the_actual_crop(config):
    before, after = recent_frames(config)
    with pytest.raises(ValueError, match="outside frame"):
        scroll_vision.measure_inventory_scroll(before, after, config.rois["inventory_content"], 8,
                                              config=config.scroll, item_rois=(Rect(940, 200, 240, 220),))


def test_new_evidence_still_fails_when_three_complete_rows_are_required(config):
    before, after = recent_frames(config)
    result = measure(replace(config, scroll=replace(config.scroll, minimum_content_rows=3)), before, after)
    assert not result.content_verified


@pytest.mark.parametrize("variant", ["stationary", "reverse", "unrelated"])
def test_recent_scene_does_not_authorize_wrong_motion(config, variant):
    before, after = recent_frames(config)
    if variant == "stationary":
        after = before
    elif variant == "reverse":
        before, after = after, before
    else:
        after.raw[:] = np.random.default_rng(11).integers(0, 256, after.raw.shape, dtype=np.uint8)
    result = measure(config, before, after)
    authorized = (result.content_verified and result.phase_shift_y < -config.scroll.minimum_upward_shift_px
                  and result.changed_fraction > config.scroll.minimum_changed_fraction)
    assert not authorized
