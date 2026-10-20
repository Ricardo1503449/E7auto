from __future__ import annotations

import cv2
import numpy as np
import pytest
from e7auto.configuration.loader import load_config
from e7auto.core.types import Rect, Size, ClientPoint
from e7auto.features.shop.scroll_vision import measure_inventory_scroll, measure_inventory_scroll_stability
from e7auto.features.shop.scrolling import ScrollServices, ScrollProgress, scroll_to_bottom
from e7auto.features.shop.vision import ShopVision
from e7auto.vision.frames import CoordinateTransform, FrameView
from tests.helpers import FakeClock, FakeLogger
from tests.helpers.paths import ROOT, FIXTURES_DIR


@pytest.fixture(scope='module')
def config():
    return load_config(ROOT / 'config/internal.yaml')


def real_frames(config):
    transform = CoordinateTransform(config.baseline_client_size, Size(1536, 864))
    return {label: FrameView(cv2.imread(str(FIXTURES_DIR / 'shop' / ('scroll_content_' + label + '.png'))),
                             transform, ClientPoint(628, 72)) for label in ['before', 'first', 'last-3']}


@pytest.mark.parametrize('label', ['first', 'last-3'])
def test_real_repeated_price_failure_verifies_native_product_content(config, label):
    frames = real_frames(config)
    result = measure_inventory_scroll(frames['before'], frames[label], config.rois['inventory_content'], 8, config=config.scroll)
    assert result.content_verified
    assert result.phase_shift_y == pytest.approx(-394, abs=2)
    assert abs(result.phase_shift_x) < 1
    assert result.changed_fraction > config.scroll.minimum_changed_fraction
    assert sum(s is not None and s >= config.scroll.minimum_content_score for s in result.content_scores) >= 3


@pytest.mark.parametrize('size', [Size(2322, 1306), Size(1920, 1080), Size(1536, 864)])
def test_previous_real_baseline_sample_and_derived_sizes(config, size):
    frames = []
    for label in ['top', 'bottom']:
        raw = np.zeros((1306, 2322, 3), np.uint8)
        raw[110:1290, 950:2270] = cv2.imread(str(FIXTURES_DIR / 'shop' / ('scroll_overlap_' + label + '.png')))
        if size != config.baseline_client_size:
            raw = cv2.resize(raw, (size.width, size.height), interpolation=cv2.INTER_AREA)
        frames.append(FrameView(raw, CoordinateTransform(config.baseline_client_size, size)))
    result = measure_inventory_scroll(*frames, config.rois['inventory_content'], 8, config=config.scroll)
    assert result.content_verified
    assert result.phase_shift_y == pytest.approx(-393, abs=3)
    assert result.changed_fraction > config.scroll.minimum_changed_fraction


def test_first_detection_already_at_bottom_passes_the_single_production_path(config):
    frames = real_frames(config)
    sequence = iter([frames['before'], frames['first'], frames['last-3'], frames['last-3']])
    clock, logger, progress = FakeClock(), FakeLogger(), ScrollProgress()
    roi = config.rois['inventory_content']
    services = ScrollServices(
        capture=lambda: next(sequence), dispatch_scroll=lambda *_: None,
        checkpoint=lambda: None, sleep=clock.sleep, active_monotonic=clock.monotonic,
        measure_stability=lambda a, b: measure_inventory_scroll_stability(a, b, roi, 8, 4),
        measure_movement=lambda a, b: measure_inventory_scroll(a, b, roi, 8, config=config.scroll), logger=logger,
    )
    result = scroll_to_bottom(config.scroll, 3, services, progress)
    assert len(result) == 3
    trace = next(fields for event, fields in logger.events if event == 'scroll_settle_trace')
    assert trace['verification_method'] == 'content'
    assert trace['total_gate_checks'] == 1
    assert trace['sample_count'] == 3
    assert not any('fallback' in key or 'cumulative' in key for key in trace)


def test_prices_and_buttons_cannot_influence_movement_or_stability(config):
    frames = real_frames(config)
    raw = []
    for label in ['before', 'last-3']:
        image = np.zeros((864, 1536, 3), np.uint8)
        image[72:854, 628:1257] = frames[label].raw
        raw.append(image)
    vision = ShopVision(config, None)
    transform = frames['before'].transform
    original = vision.inventory_scroll_movement(*(FrameView(p, transform) for p in raw))
    rng = np.random.default_rng(43)
    for image in raw:
        image[:, 1257:] = rng.integers(0, 256, image[:, 1257:].shape, dtype=np.uint8)
    assert vision.inventory_scroll_movement(*(FrameView(p, transform) for p in raw)) == original
    stable_before = raw[1].copy()
    raw[1][:, 1257:] = 255
    stable = vision.inventory_scroll_stability(FrameView(stable_before, transform), FrameView(raw[1], transform))
    assert abs(stable.phase_shift_x) < 1e-4
    assert abs(stable.phase_shift_y) < 1e-4


@pytest.mark.parametrize('shape', [(782, 874), (781, 629), (195, 218), (196, 219)])
def test_identical_native_pixels_have_zero_shift_on_odd_and_even_grids(shape):
    raw = np.random.default_rng(19).integers(0, 256, (*shape, 3), dtype=np.uint8)
    result = measure_inventory_scroll_stability(raw, raw, Rect(0, 0, shape[1], shape[0]), 8, 4)
    assert result.phase_shift_x == pytest.approx(0, abs=1e-4)
    assert result.phase_shift_y == pytest.approx(0, abs=1e-4)
    assert result.phase_response == pytest.approx(1, abs=1e-4)


def test_reverse_real_content_keeps_its_actual_direction(config):
    frames = real_frames(config)
    result = measure_inventory_scroll(frames['last-3'], frames['before'], config.rois['inventory_content'], 8, config=config.scroll)
    assert result.content_verified
    assert result.phase_shift_y > 390


def test_stationary_and_unrelated_content_cannot_fabricate_upward_motion(config):
    frames = real_frames(config)
    stationary = measure_inventory_scroll(frames['before'], frames['before'], config.rois['inventory_content'], 8, config=config.scroll)
    assert abs(stationary.phase_shift_y) < 1e-4
    assert stationary.changed_fraction == 0
    noise = FrameView(np.random.default_rng(81).integers(0, 256, frames['before'].raw.shape, dtype=np.uint8),
                      frames['before'].transform, frames['before'].origin)
    unrelated = measure_inventory_scroll(frames['before'], noise, config.rois['inventory_content'], 8, config=config.scroll)
    assert not unrelated.content_verified


def test_repeated_product_content_is_rejected_as_ambiguous(config):
    row = np.random.default_rng(74).integers(0, 256, (80, 160, 3), dtype=np.uint8)
    before = np.tile(row, (6, 1, 1))
    after = np.roll(before, -40, axis=0)
    result = measure_inventory_scroll(before, after, Rect(0, 0, 160, 480), 8, config=config.scroll)
    assert not result.content_verified
    assert result.content_reason == 'ambiguous_content'


def test_final_content_verification_does_not_resize_native_pixels(config, monkeypatch):
    frames = real_frames(config)
    def forbidden(*args, **kwargs):
        pytest.fail('Final scroll evidence must use native pixels')
    monkeypatch.setattr(cv2, 'resize', forbidden)
    assert measure_inventory_scroll(frames['before'], frames['last-3'], config.rois['inventory_content'], 8, config=config.scroll).content_verified


def test_flat_content_cannot_verify_a_translation(config):
    blank = np.full((200, 300, 3), 100, np.uint8)
    result = measure_inventory_scroll(blank, blank, Rect(0, 0, 300, 200), 8, config=config.scroll)
    assert not result.content_verified


def test_cropped_frames_must_keep_the_same_full_client_geometry(config):
    frames = real_frames(config)
    wrong = FrameView(frames['last-3'].raw, CoordinateTransform(config.baseline_client_size, Size(1920, 1080)),
                      frames['last-3'].origin)
    with pytest.raises(ValueError, match='client geometry'):
        measure_inventory_scroll(frames['before'], wrong, config.rois['inventory_content'], 8, config=config.scroll)
