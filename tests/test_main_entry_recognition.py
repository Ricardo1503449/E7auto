"""Both production entry paths against real captures and adversarial matches."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace

import cv2
import numpy as np
import pytest

from e7auto.config import Point, Rect, Size, load_config
from e7auto.geometry import CoordinateTransform, adapt_frame
from e7auto.penguin_vision import PenguinVision, with_penguin_config
from e7auto.vision import OpenCvGameVision, TemplateData, TemplateRepository

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Size(2322, 1306)


@pytest.fixture(scope="module")
def samples():
    with np.load(ROOT / "tests/fixtures/main_entry_samples.npz", allow_pickle=False) as archive:
        return {name: archive[name] for name in archive.files}


@pytest.fixture(scope="module", params=["shop", "sanctuary"])
def entry(request):
    if request.param == "shop":
        config = load_config(ROOT / "config/internal.yaml")
        vision = OpenCvGameVision(config, TemplateRepository(config))
        return vision, "main_shop_icon", config.anchor_confidence, vision.main_shop_icon
    config = with_penguin_config(load_config(
        ROOT / "config/internal.yaml", template_profile="penguin",
    ))
    vision = PenguinVision(config, TemplateRepository(config))
    return vision, "penguin_sanctuary_entry", 0.96, lambda frame: vision.control(frame, "sanctuary_entry")


def frame_for(sample, width=2322, brightness=1.0):
    frame = np.zeros((1306, 2322, 3), dtype=np.uint8)
    frame[240:1140, 0:210] = np.clip(sample.astype(float) * brightness, 0, 255).astype(np.uint8)
    actual = Size(width, round(width * 1306 / 2322))
    if width != 2322:
        frame = cv2.resize(frame, (actual.width, actual.height), interpolation=cv2.INTER_AREA)
    return adapt_frame(frame, CoordinateTransform(BASELINE, actual))


@pytest.mark.parametrize("sample", ["visible_penguin_calibration", "visible_shop_calibration"])
@pytest.mark.parametrize("width", [2322, 1920, 1536])
@pytest.mark.parametrize("brightness", [0.95, 1.0, 1.05])
def test_real_entries_survive_scale_and_brightness(entry, samples, sample, width, brightness):
    vision, key, threshold, detect = entry
    reference = vision.match(frame_for(samples[sample]), key, vision._config.rois["left_icon_column"], threshold)
    assert reference is not None
    observed = detect(frame_for(samples[sample], width, brightness))
    assert observed is not None
    assert abs(observed.anchor.x - reference.anchor.x) <= 2
    assert abs(observed.anchor.y - reference.anchor.y) <= 2


@pytest.mark.parametrize("sample", ["hidden_192723", "hidden_192753", "hidden_193020"])
@pytest.mark.parametrize("width", [2322, 1920, 1536])
def test_real_hidden_backgrounds_are_rejected_by_both_entries(entry, samples, sample, width):
    assert entry[3](frame_for(samples[sample], width)) is None


def test_recorded_sanctuary_false_positive_requires_structure(samples):
    config = with_penguin_config(load_config(
        ROOT / "config/internal.yaml", template_profile="penguin",
    ))
    vision = PenguinVision(config, TemplateRepository(config))
    key = "penguin_sanctuary_entry"
    frame = frame_for(samples["hidden_192723"])
    old_result = vision.match(frame, key, config.rois["left_icon_column"], 0.96)
    assert old_result is not None and old_result.confidence > 0.968
    assert vision.control(frame, "sanctuary_entry") is None


@pytest.mark.parametrize("kind", ["flat", "gradient", "shuffled"])
def test_color_similar_background_without_icon_structure_is_rejected(entry, kind):
    vision, key, _, detect = entry
    template = vision._templates.get(key)
    mean = np.rint(template.image[template.mask > 0].mean(axis=0)).astype(np.uint8)
    frame = np.full((1306, 2322, 3), mean, dtype=np.uint8)
    if kind == "gradient":
        ramp = np.linspace(-25, 25, frame.shape[1])[None, :, None]
        frame = np.clip(frame.astype(float) + ramp, 0, 255).astype(np.uint8)
    elif kind == "shuffled":
        roi = vision._config.rois["left_icon_column"]
        h, w = template.image.shape[:2]
        pixels = template.image.copy().reshape(-1, 3)
        np.random.default_rng(71).shuffle(pixels)
        frame[roi.y:roi.y+h, roi.x:roi.x+w] = pixels.reshape(h, w, 3)
    assert detect(frame) is None


def test_real_entry_bgra_capture(entry, samples):
    frame = frame_for(samples["visible_shop_calibration"])
    bgra = np.dstack((frame, np.full(frame.shape[:2], 255, dtype=np.uint8)))
    assert entry[3](bgra) is not None


@pytest.mark.parametrize("position", ["top", "middle", "bottom"])
def test_activity_layout_can_move_either_entry_within_the_column(entry, samples, position):
    vision, key, _, detect = entry
    original = frame_for(samples["visible_penguin_calibration"])
    observation = detect(original)
    assert observation is not None
    height, width = vision._templates.get(key).image.shape[:2]
    left, top = observation.anchor.x-width//2, observation.anchor.y-height//2
    icon = original[top:top+height, left:left+width].copy()
    roi = vision._config.rois["left_icon_column"]
    y = {"top": roi.y, "middle": roi.y+400, "bottom": roi.bottom-height}[position]
    x = roi.x + (roi.width-width)//2
    moved = np.zeros_like(original)
    moved[y:y+height, x:x+width] = icon
    result = detect(moved)
    assert result is not None and result.anchor == Point(x+width//2, y+height//2)


def test_sanctuary_search_can_be_calibrated_independently_of_template_origin(samples):
    config = load_config(ROOT / "config/internal.yaml", template_profile="penguin")
    frame = np.zeros((1306, 2322, 3), dtype=np.uint8)
    # Move an unmodified real capture by (200,100), beyond the old search ROI.
    frame[340:1240, 200:410] = samples["visible_penguin_calibration"]
    original = with_penguin_config(config)
    assert PenguinVision(original, TemplateRepository(original)).control(frame, "sanctuary_entry") is None
    requested = Rect(200, 340, 210, 900)
    changed = with_penguin_config(replace(
        config, rois={**config.rois, "left_icon_column": requested},
    ))
    result = PenguinVision(changed, TemplateRepository(changed)).control(frame, "sanctuary_entry")
    assert result is not None and result.anchor == Point(304, 441)
    assert result.roi == requested


@pytest.mark.parametrize("right,bottom", [(False, False), (True, False), (False, True), (True, True)])
def test_sanctuary_search_includes_all_four_template_fit_boundaries(samples, right, bottom):
    config = with_penguin_config(load_config(ROOT / "config/internal.yaml", template_profile="penguin"))
    vision = PenguinVision(config, TemplateRepository(config))
    roi = config.rois["left_icon_column"]
    # Exact original pixels around the visible sanctuary at (64,284), size80x114.
    icon = samples["visible_penguin_calibration"][44:158, 64:144]
    x = roi.right - 80 if right else roi.x
    y = roi.bottom - 114 if bottom else roi.y
    frame = np.zeros((1306, 2322, 3), dtype=np.uint8)
    frame[y:y+114, x:x+80] = icon
    result = vision.control(frame, "sanctuary_entry")
    assert result is not None and result.anchor == Point(x+40, y+57)


class OneTemplate:
    def __init__(self, image, mask):
        self.data = TemplateData(image, mask)

    def get(self, key):
        return self.data


def synthetic_vision():
    template = np.random.default_rng(7).integers(190, 211, (12, 12, 3), dtype=np.uint8)
    mask = np.full((12, 12), 255, dtype=np.uint8)
    mask[0] = 0
    config = load_config(ROOT / "config/internal.yaml")
    return OpenCvGameVision(config, OneTemplate(template, mask)), template


def test_structure_can_select_valid_candidate_after_best_color_candidate_fails():
    vision, template = synthetic_vision()
    frame = np.zeros((20, 40, 3), dtype=np.uint8)
    frame[4:16, 1:13] = 200  # Better color score, but no structure.
    frame[4:16, 25:37] = template - 15  # Real structure, modest brightness change.
    roi = Rect(0, 0, 40, 20)
    old_result = vision.match(frame, "entry", roi, 0.96)
    assert old_result is not None and old_result.anchor.x < 20
    observed = vision.match_entry(frame, "entry", roi, 0.96, structure_threshold=0.8)
    assert observed is not None and observed.anchor == Point(31, 10)


def test_structure_alone_cannot_override_failed_color_gate():
    vision, template = synthetic_vision()
    frame = (template.astype(float) * 0.5).astype(np.uint8)
    assert vision.match_entry(frame, "entry", Rect(0, 0, 12, 12), 0.96, structure_threshold=0.8) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_structure_scores_fail_closed(monkeypatch, value):
    vision, template = synthetic_vision()
    original = cv2.matchTemplate

    def match(source, needle, method, **kwargs):
        result = original(source, needle, method, **kwargs)
        return np.full_like(result, value) if method == cv2.TM_CCOEFF_NORMED else result

    monkeypatch.setattr(cv2, "matchTemplate", match)
    assert vision.match_entry(template, "entry", Rect(0, 0, 12, 12), 0.96, structure_threshold=0.8) is None


@pytest.mark.parametrize("score, accepted", [(0.7999, False), (0.8, True), (0.8001, True)])
def test_structure_threshold_boundary(monkeypatch, score, accepted):
    vision, template = synthetic_vision()
    original = cv2.matchTemplate

    def match(source, needle, method, **kwargs):
        result = original(source, needle, method, **kwargs)
        return np.full_like(result, score) if method == cv2.TM_CCOEFF_NORMED else result

    monkeypatch.setattr(cv2, "matchTemplate", match)
    result = vision.match_entry(template, "entry", Rect(0, 0, 12, 12), 0.96, structure_threshold=0.8)
    assert (result is not None) is accepted


@pytest.mark.parametrize("masked", [True, False])
def test_constant_template_has_no_structure(masked):
    image = np.full((12, 12, 3), 200, dtype=np.uint8)
    mask = np.full((12, 12), 255, dtype=np.uint8) if masked else None
    config = load_config(ROOT / "config/internal.yaml")
    vision = OpenCvGameVision(config, OneTemplate(image, mask))
    assert vision.match_entry(image, "entry", Rect(0, 0, 12, 12), 0.96, structure_threshold=0.8) is None
