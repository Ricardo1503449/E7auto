"""Current-frame preparation reuse without changing nonreference evidence."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import weakref

import cv2
import numpy as np
import pytest

from e7auto.core.types import ClientRect, Rect, Size
from e7auto.resources.templates import TemplateData
from e7auto.vision.frames import CoordinateTransform, adapt_frame
from e7auto.vision.matching import TemplateMatcher


@pytest.fixture
def search_case():
    rng = np.random.default_rng(41)
    templates = {str(i): TemplateData(rng.integers(40, 240, (50, 70, 3), dtype=np.uint8))
                 for i in range(4)}
    frame = adapt_frame(rng.integers(40, 240, (600, 900, 3), dtype=np.uint8),
                        CoordinateTransform(Size(1350, 900), Size(900, 600)))
    matcher = TemplateMatcher(SimpleNamespace(get=templates.__getitem__))
    return matcher, frame, Rect(0, 0, 1350, 900)


def track_preparation(monkeypatch):
    counts = Counter()
    for owner, name, key in ((cv2, 'meanStdDev', 'variance'), (np, 'pad', 'padding'),
                             (cv2, 'resize', 'resize')):
        original = getattr(owner, name)
        def tracked(*args, _original=original, _key=key, **kwargs):
            counts[_key] += 1
            return _original(*args, **kwargs)
        monkeypatch.setattr(owner, name, tracked)
    return counts


def place_target(matcher, frame):
    candidate = matcher.cache.get(matcher._templates.get('0'), (2/3, 2/3))
    width, height = candidate.size
    frame.raw[20:20+height, 30:30+width] = candidate.image


def test_native_retry_prepares_each_search_region_once(search_case, monkeypatch):
    matcher, frame, roi = search_case
    counts = track_preparation(monkeypatch)
    calls = []
    proposals = matcher._proposals
    def tracked(*args, **kwargs):
        calls.append(kwargs['native'])
        return proposals(*args, **kwargs)
    monkeypatch.setattr(matcher, '_proposals', tracked)
    for key in ('0', '1', '2', '3'):
        assert matcher.locate_all(frame, key, roi, .99, .8, limit=1) == ()
    assert calls == [False, True]*4
    assert counts == {'variance': 4, 'padding': 4, 'resize': 4}


def test_parallel_templates_share_preparation_but_keep_independent_results(search_case, monkeypatch):
    matcher, frame, roi = search_case
    place_target(matcher, frame)
    keys = ['0', '1', '2', '3']
    expected = [matcher.locate_all(frame, key, roi, .99, .8, limit=1) for key in keys]
    assert len(expected[0]) == 1 and all(not hits for hits in expected[1:])
    counts = track_preparation(monkeypatch)
    preparation = matcher.prepare_search(frame)
    barrier = Barrier(2)
    def search(group):
        barrier.wait(2)
        return [matcher.locate_all(frame, key, roi, .99, .8, limit=1, preparation=preparation)
                for key in group]
    with ThreadPoolExecutor(max_workers=2) as pool:
        groups = list(pool.map(search, [keys[:2], keys[2:]]))
    assert groups[0]+groups[1] == expected
    assert counts == {'variance': 1, 'padding': 1, 'resize': 1}
    assert not preparation._full.source.flags.writeable
    assert not preparation._full.half().flags.writeable
    assert frame.raw.flags.writeable  # Sharing did not change the caller's array flags.
    pixels = weakref.ref(preparation._full.source)
    del preparation
    assert pixels() is None  # No matcher/template cache retains the frame preparation.


def test_preparation_rejects_other_frame_and_fresh_scope_rechecks_pixels(search_case):
    matcher, frame, roi = search_case
    place_target(matcher, frame)
    preparation = matcher.prepare_search(frame)
    assert matcher.locate_all(frame, '0', roi, .99, .8, limit=1, preparation=preparation)
    changed = adapt_frame(np.zeros_like(frame.raw), frame.transform)
    with pytest.raises(ValueError, match='another frame'):
        matcher.locate_all(changed, '0', roi, .99, .8, limit=1, preparation=preparation)
    assert matcher.locate_all(changed, '0', roi, .99, .8, limit=1,
                              preparation=matcher.prepare_search(changed)) == ()


def test_local_roi_uses_its_own_pixels_and_original_geometry(search_case):
    matcher, frame, roi = search_case
    place_target(matcher, frame)
    preparation = matcher.prepare_search(frame)
    full = matcher.locate_all(frame, '0', roi, .99, .8, limit=1, preparation=preparation)
    for local in (ClientRect(20, 10, 110, 85), ClientRect(700, 500, 110, 85)):
        expected = matcher.locate_all(frame, '0', local, .99, .8, limit=1)
        actual = matcher.locate_all(frame, '0', local, .99, .8, limit=1, preparation=preparation)
        assert actual == expected
        if local.x == 20:
            assert actual[0].bounds == full[0].bounds and actual[0].anchor == full[0].anchor
        else:
            assert actual == ()


def test_baseline_matching_never_enters_new_preparation(search_case, monkeypatch):
    matcher, _, _ = search_case
    raw = np.zeros((90, 140, 3), np.uint8)
    image = matcher._templates.get('0').image
    raw[20:70, 30:100] = image
    def forbidden(*args, **kwargs):
        raise AssertionError('baseline entered nonreference preparation')
    monkeypatch.setattr(matcher, 'prepare_search', forbidden)
    assert matcher.match_bgr(raw, '0', Rect(0, 0, 140, 90), .99) is not None
    assert matcher.locate_all(raw, '0', Rect(0, 0, 140, 90), .99, .8, limit=1)
