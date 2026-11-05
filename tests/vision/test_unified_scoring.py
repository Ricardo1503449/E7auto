"""Ordinary paths use one color equation; structure must pass at the same point."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from e7auto.core.types import Rect, Size
from e7auto.resources.templates import TemplateData
from e7auto.vision.baseline import scores as baseline_scores
from e7auto.vision.candidates import CandidateCache
from e7auto.vision.frames import CoordinateTransform, adapt_frame
from e7auto.vision.matching import TemplateMatcher
from e7auto.configuration.loader import load_config
from e7auto.features.shop.vision import ShopVision
from e7auto.resources.templates import TemplateRepository
from tests.helpers.paths import ROOT


@pytest.mark.parametrize('masked', [False, True])
@pytest.mark.parametrize('padding', [0, 12])
def test_color_score_is_difference_for_both_small_and_opencv_windows(masked, padding):
    image = np.random.default_rng(91).integers(60, 200, (12, 14, 3), dtype=np.uint8)
    mask = None if not masked else np.pad(np.full((8, 10), 255, np.uint8), 2)
    data = TemplateData(image, mask)
    observed = image // 2
    source = np.pad(observed, ((0, padding), (0, padding), (0, 0)))
    expected_color = float(1-cv2.matchTemplate(source, image, cv2.TM_SQDIFF_NORMED, mask=mask)[0, 0])
    for score in (baseline_scores(source, image, mask, .1, .8),
                  TemplateMatcher.scores(source, data, .1, .8)):
        assert score is not None
        assert score[0][0, 0] == pytest.approx(expected_color, abs=2e-6)
        assert score[1][0, 0] > .99
    assert baseline_scores(source, image, mask, .93, .8) is None
    assert TemplateMatcher.scores(source, data, .93, .8) is None


@pytest.mark.parametrize('size', [(120, 90), (80, 60), (180, 135)])
def test_search_and_recheck_share_color_and_configured_structure_gates(size):
    image = np.random.default_rng(92).integers(60, 200, (16, 20, 3), dtype=np.uint8)
    data = TemplateData(image)
    cache = CandidateCache()
    matcher = TemplateMatcher(SimpleNamespace(get=lambda _: data), cache,
                              structure_thresholds={'control': .8})
    sx, sy = size[0]/120, size[1]/90
    candidate = cache.get(data, (sx, sy))
    raw = np.zeros((size[1], size[0], 3), np.uint8)
    h, w = candidate.image.shape[:2]
    raw[10:10+h, 15:15+w] = candidate.image
    transform = CoordinateTransform(Size(120, 90), Size(*size))
    frame = adapt_frame(raw, transform)
    roi = Rect(0, 0, 120, 90)
    hit = matcher.match(frame, 'control', roi, .95)
    assert hit is not None and hit.structure_confidence >= .8
    assert matcher.match_entry(frame, 'control', roi, .95, structure_threshold=.8).bounds == hit.bounds
    assert matcher.recheck(frame, hit, .95, None) is not None
    dark = adapt_frame(raw//2, transform)
    assert matcher.match(dark, 'control', roi, .95) is None
    assert matcher.locate_all(dark, 'control', roi, .95, None) == ()
    assert matcher.recheck(dark, hit, .95, None) is None


def test_two_good_scores_at_different_positions_cannot_combine():
    image = np.random.default_rng(93).integers(195, 206, (12, 14, 3), dtype=np.uint8)
    source = np.full((12, 34, 3), 200, np.uint8)
    source[:, 20:] = image//2
    data = TemplateData(image)
    assert baseline_scores(source, image, None, .95, .8) is None
    assert TemplateMatcher.scores(source, data, .95, .8) is None


def test_candidate_statistics_remain_separate_for_different_unmasked_templates():
    cache = CandidateCache()
    first = TemplateData(np.random.default_rng(1).integers(30, 230, (12, 14, 3), dtype=np.uint8))
    second = TemplateData(np.random.default_rng(2).integers(30, 230, (12, 14, 3), dtype=np.uint8))
    a, b = (cache.get(data, (2/3, 2/3)) for data in (first, second))
    assert a.cache_key != b.cache_key
    assert cache.score_data(a) is not cache.score_data(b)


@pytest.mark.parametrize('sample', range(9))
@pytest.mark.parametrize('width', [2322, 1920, 1536])
def test_calibrated_sold_out_gate_with_real_positive_and_negative_samples(sample, width):
    config = load_config(ROOT / 'config/internal.yaml')
    vision = ShopVision(config, TemplateRepository(config))
    path = ROOT / 'tests/fixtures/shop/purchased_button_samples.png'
    samples = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), 1)
    point = config.slots[0].buy_point
    raw = np.zeros((1306, 2322, 3), np.uint8)
    raw[point.y-65:point.y+65, point.x-183:point.x+184] = samples[sample*130:(sample+1)*130]
    height = round(width*1306/2322)
    frame = adapt_frame(cv2.resize(raw, (width, height), interpolation=cv2.INTER_AREA),
                        CoordinateTransform(Size(2322, 1306), Size(width, height)))
    hit = vision.purchased_button(frame, config.slots[0].item_roi)
    assert (hit is not None) is (sample in (3, 5))
    if hit is not None:
        assert hit.structure_confidence >= config.template_structure_thresholds['purchased_button']
