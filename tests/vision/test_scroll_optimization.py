import cv2
import numpy as np
import pytest

from e7auto.core.types import Rect
from e7auto.features.shop import scroll_vision
from tests.helpers import make_config


@pytest.mark.parametrize("shape", [(50, 50), (100, 100), (450, 500)])
@pytest.mark.parametrize("extra", [-1, 0, 1])
@pytest.mark.parametrize("strided", [False, True])
def test_texture_gate_preserves_float32_rounding_at_eight(shape, extra, strided):
    values = np.full(shape[0] * shape[1], 100, np.float32)
    values[:values.size // 2 + extra] = 116
    values = values.reshape(shape)
    if strided:
        storage = np.zeros((shape[0], shape[1] * 2), np.float32)
        storage[:, ::2] = values
        values = storage[:, ::2]
    assert scroll_vision._has_content_texture(values, 8) == (float(values.std()) >= 8)


@pytest.mark.parametrize("seed", range(12))
def test_texture_gate_matches_exact_float32_decisions_around_actual_std(seed):
    rng = np.random.default_rng(seed)
    raw = rng.integers(0, 256, (87 + seed, 131 + seed), dtype=np.uint8)
    values = raw.astype(np.float32)
    sigma = values.std()
    for threshold in (8.0, .5, 127.5, float(sigma),
                      float(np.nextafter(sigma, np.float32(-np.inf))),
                      float(np.nextafter(sigma, np.float32(np.inf)))):
        assert scroll_vision._has_content_texture(values, threshold) == (float(sigma) >= threshold)


@pytest.mark.parametrize("value", [0, 100, 255])
def test_texture_gate_rejects_flat_content(value):
    assert not scroll_vision._has_content_texture(np.full((450, 500), value, np.float32), 8)


def test_confirmed_ambiguity_ends_without_ignoring_the_zero_motion_competitor(monkeypatch):
    monkeypatch.setattr(scroll_vision, "_phase_candidates", lambda *_: [(0, -350, .5), (0, 0, .4), (0, -351, .3), (0, 20, .2)])
    calls = []
    def score(*args):
        calls.append(args[2:4])
        return (.9,) * 4
    monkeypatch.setattr(scroll_vision, "_native_content_scores", score)
    frame = np.zeros((80, 100, 3), np.uint8)
    result = scroll_vision.measure_inventory_scroll(frame, frame, Rect(0, 0, 100, 80), 8, config=make_config().scroll, item_rois=())
    assert calls == [(0, -350), (0, 0)]
    assert result.content_reason == "ambiguous_content"
    assert not result.content_verified
    assert result.phase_shift_y == -350


def test_one_passing_candidate_does_not_skip_remaining_competing_candidates(monkeypatch):
    candidates = [(0, -350, .5), (0, -351, .4), (0, 20, .3), (0, -20, .2)]
    monkeypatch.setattr(scroll_vision, "_phase_candidates", lambda *_: candidates)
    calls = []
    def score(*args):
        dx, dy = args[2:4]
        calls.append((dx, dy))
        return (.9,) * 4 if dy < -300 else (.1,) * 2
    monkeypatch.setattr(scroll_vision, "_native_content_scores", score)
    frame = np.zeros((80, 100, 3), np.uint8)
    result = scroll_vision.measure_inventory_scroll(frame, frame, Rect(0, 0, 100, 80), 8, config=make_config().scroll, item_rois=())
    assert calls == [(dx, dy) for dx, dy, _ in candidates] + [(0, 0)]
    assert result.content_verified
    assert result.phase_shift_y == -350
