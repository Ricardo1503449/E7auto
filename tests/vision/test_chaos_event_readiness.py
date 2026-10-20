"""Real transition/gray captures and derived animation boundary regressions."""
import weakref

import cv2
import numpy as np
import pytest

from e7auto.core.types import Point, Size
from e7auto.features.chaos.event_recognition import EventRecognizer
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import CoordinateTransform, adapt_frame, frame_view
from tests.helpers.chaos_game import chaos_config
from tests.helpers.paths import ROOT


@pytest.fixture(scope='module')
def resources():
    config = chaos_config()
    return config, TemplateRepository(config)


@pytest.fixture
def recognizer(resources):
    config, templates = resources
    value = EventRecognizer(config.chaos, templates)
    value.begin()
    yield value
    value.close()


def fixture(name):
    path = ROOT/'tests/fixtures/chaos/resolution'/name
    return cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)


def framed(raw, size=None):
    if size is not None and size != (raw.shape[1], raw.shape[0]):
        # Derived test input only; production never normalizes screenshots.
        raw = cv2.resize(raw, size, interpolation=cv2.INTER_AREA if size[0] < raw.shape[1] else cv2.INTER_LINEAR)
    return adapt_frame(raw, CoordinateTransform(Size(2322, 1306), Size(raw.shape[1], raw.shape[0])))


def assembled(r, key, *, missing=None, dy=0, factor=1.):
    """Assembled original pixels, with explicitly derived title changes."""
    raw = np.zeros((1306, 2322, 3), np.uint8)
    item, spec = r.identities[key], r.p['templates'][key]
    x, y = item.origin
    h, w = item.image.shape[:2]
    raw[y:y+h, x:x+w] = item.image
    for i, (xx, yy, ww, hh) in enumerate(spec['titles']):
        patch = raw[y+yy:y+yy+hh, x+xx:x+xx+ww].copy()
        raw[y+yy:y+yy+hh, x+xx:x+xx+ww] = 0
        if i != missing:
            raw[y+yy+dy:y+yy+dy+hh, x+xx:x+xx+ww] = np.rint(patch*factor).astype(np.uint8)
    return raw


@pytest.mark.parametrize('nested', [False, True])
def test_actual_partial_altar_never_launches_full_search(recognizer, monkeypatch, nested):
    r = recognizer
    frame = framed(fixture('event_altar_transition_1536.png'))
    if nested:
        frame = frame_view(frame).crop(r.region)
    monkeypatch.setattr(r, '_discover', lambda *args: pytest.fail('Full search on partial altar'))
    for _ in range(6):
        assert r.recognize(frame) is None
    assert r.candidate is None and r._pool is None
    assert r.stats['event_candidates_checked'] == 0


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
@pytest.mark.parametrize('key', ['event_altar', 'event_clear_pond_low_health', 'event_warning_sign_low_health'])
def test_missing_any_title_blocks_search_including_zero_weight_titles(recognizer, monkeypatch, size, key):
    r = recognizer
    monkeypatch.setattr(r, '_discover', lambda *args: pytest.fail('Discovery with a missing title'))
    for missing in range(r.p['templates'][key]['layout']):
        r.invalidate()
        frame = framed(assembled(r, key, missing=missing), size)
        for _ in range(4):
            assert r.recognize(frame) is None


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
@pytest.mark.parametrize('change', ['moving', 'fading'])
def test_moving_or_fading_titles_wait_for_three_stable_frames(recognizer, monkeypatch, size, change):
    r = recognizer
    calls = []
    monkeypatch.setattr(r, '_discover', lambda *args: calls.append(True))
    stages = ([assembled(r, 'event_altar', dy=dy) for dy in (12, 8, 4, 0)] if change == 'moving'
              else [assembled(r, 'event_altar', factor=f) for f in (.4, .6, .8, 1.)])
    for raw in stages:
        assert r.recognize(framed(raw, size)) is None
        assert not calls
    complete = framed(stages[-1], size)
    assert r.recognize(complete) is None and not calls
    assert r.recognize(complete) is None and len(calls) == 1


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
@pytest.mark.parametrize('dy', [12, 16])
def test_one_title_still_below_the_others_blocks_discovery(recognizer, monkeypatch, size, dy):
    r = recognizer
    raw = assembled(r, 'event_altar')
    x, y = r.identities['event_altar'].origin
    xx, yy, w, h = r.p['templates']['event_altar']['titles'][1]
    patch = raw[y+yy:y+yy+h, x+xx:x+xx+w].copy()
    raw[y+yy:y+yy+h, x+xx:x+xx+w] = 0
    raw[y+yy+dy:y+yy+dy+h, x+xx:x+xx+w] = patch
    monkeypatch.setattr(r, '_discover', lambda *args: pytest.fail('A compromise offset authorized search'))
    frame = framed(raw, size)
    for _ in range(4):
        assert r.recognize(frame) is None


@pytest.mark.parametrize('name,key,anchor', [
    ('event_clear_pond_low_health_2322.png', 'event_clear_pond_low_health', (810, 1090)),
    ('event_warning_sign_low_health_2322.png', 'event_warning_sign_low_health', (1850, 1090)),
])
@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
def test_real_gray_titles_pass_and_keep_original_anchor(recognizer, name, key, anchor, size):
    r = recognizer
    frame = framed(fixture(name), size)
    for _ in range(2):
        assert r.recognize(frame) is None
    transform = frame_view(frame).transform
    expected = transform.point(Point(*anchor))
    for _ in range(3):
        found = r.recognize(frame)
        assert found is not None and found.key == key
        assert abs(found.anchor.x-expected.x) <= 2 and abs(found.anchor.y-expected.y) <= 2
    assert r.stats['event_cache_checks'] == 2


@pytest.mark.parametrize('boundary', ['invalidate', 'empty', 'geometry', 'end', 'cache_failure'])
def test_boundaries_discard_incomplete_readiness_counts(recognizer, monkeypatch, boundary):
    r = recognizer
    frame = framed(fixture('event_red_phantom_options_1536.png'))
    calls = []
    monkeypatch.setattr(r, '_discover', lambda *args: calls.append(True))
    assert r.recognize(frame) is None
    assert r.recognize(frame) is None and not calls
    if boundary == 'invalidate':
        r.invalidate()  # Same callback is used by text/input/network recovery.
    elif boundary == 'empty':
        assert r.recognize(framed(np.zeros((864, 1536, 3), np.uint8))) is None
    elif boundary == 'geometry':
        other = framed(assembled(r, 'event_red_phantom'))
        assert r.recognize(other) is None
    elif boundary == 'end':
        r.end()
        r.begin()
    else:
        from e7auto.features.chaos.contracts import Detection
        from e7auto.core.types import ClientRect, ClientPoint
        r.candidate = Detection('event_red_phantom', 1., ClientRect(0, 0, 1, 1), ClientPoint(0, 0))
        monkeypatch.setattr(r, 'verify', lambda *args, **kwargs: None)
        assert r.recognize(frame) is None
    for _ in range(2):
        assert r.recognize(frame) is None and not calls
    assert r.recognize(frame) is None and len(calls) == 1


def test_readiness_keeps_descriptors_without_retaining_capture(recognizer):
    raw = fixture('event_altar_transition_1536.png')
    ref = weakref.ref(raw)
    assert recognizer.recognize(framed(raw)) is None
    del raw
    assert ref() is None


def test_one_shot_inspection_checks_completeness_without_dispatching(recognizer):
    r = recognizer
    r.end()
    assert r.recognize(framed(fixture('event_altar_transition_1536.png'))) is None
    found = r.recognize(framed(fixture('event_altar_2304.png')))
    assert found is not None and found.key == 'event_altar'
    assert not r.active and r.candidate is None
