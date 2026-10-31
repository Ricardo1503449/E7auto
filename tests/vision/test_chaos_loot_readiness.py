"""Real loot transition regression and derived completeness/settling boundaries."""
import weakref

import cv2
import numpy as np
import pytest

from e7auto.core.types import ClientRect, Size
from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import CoordinateTransform, FrameView
from tests.helpers.chaos_game import chaos_config
from tests.helpers.chaos_images import loot_frame
from tests.helpers.paths import ROOT


KEYS = ('loot_ominous_crystal', 'loot_old_sword', 'loot_lightning')
SIZES = ((2322, 1306), (1536, 864))


@pytest.fixture(scope='module')
def resources():
    config = chaos_config()
    return config, TemplateRepository(config)


@pytest.fixture
def vision(resources):
    config, templates = resources
    value = ChaosVision(config, templates)
    value.begin_loot_recognition()
    yield value, config, templates
    value.close_loot_recognition()
    value.close_event_recognition()


def framed(raw, size=None, *, nested=False):
    if size is not None and size != (raw.shape[1], raw.shape[0]):
        raw = cv2.resize(raw, size, interpolation=cv2.INTER_AREA if size[0] < raw.shape[1] else cv2.INTER_LINEAR)
    view = FrameView(raw, CoordinateTransform(Size(2322, 1306), Size(raw.shape[1], raw.shape[0])))
    return view.crop(ClientRect(0, 30, raw.shape[1], raw.shape[0]-30)) if nested else view


def transition():
    data = (ROOT/'tests/fixtures/chaos/resolution/loot_transition_1536.png').read_bytes()
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def changed_part(config, templates, frame, *, slot=1, label='name', dy=0, factor=1., missing=False):
    key = KEYS[slot]
    spec = config.chaos.loot_recognition['template_regions'][key]
    xx, yy, w, h = spec[label]
    x, y = round(548+580.5*slot)+spec['origin'][0]+xx, 987+spec['origin'][1]+yy
    patch = frame[y:y+h, x:x+w].copy()
    frame[y:y+h, x:x+w] = 0
    if not missing:
        frame[y+dy:y+dy+h, x:x+w] = np.rint(patch*factor).astype(np.uint8)
    return frame


@pytest.mark.parametrize('nested', [False, True])
def test_real_unfinished_cards_never_start_discovery_and_complete_cards_recover(vision, monkeypatch, nested):
    detector, config, templates = vision
    original = detector._loot._evaluate_cards
    monkeypatch.setattr(detector._loot, '_evaluate_cards', lambda *_: pytest.fail('Full work on unfolding loot'))
    for _ in range(6):
        assert detector.loot_cards(framed(transition(), nested=nested)) is None
    assert detector._loot._pool is None
    assert detector._loot.stats['candidates_checked'] == detector._loot.stats['card_jobs'] == 0
    monkeypatch.setattr(detector._loot, '_evaluate_cards', original)
    full = framed(loot_frame(config, templates, KEYS), (1536, 864), nested=nested)
    for _ in range(2):
        assert detector.loot_cards(full) is None
    for _ in range(3):
        assert tuple(hit.key for hit in detector.loot_cards(full)) == KEYS
    assert detector._loot.stats['index_queries'] == 3
    assert detector._loot.stats['cache_checks'] == 6


@pytest.mark.parametrize('size', SIZES)
@pytest.mark.parametrize('slot', range(3))
@pytest.mark.parametrize('label', ['icon', 'name'])
def test_missing_any_part_stays_unready_even_when_static(vision, monkeypatch, size, slot, label):
    detector, config, templates = vision
    raw = changed_part(config, templates, loot_frame(config, templates, KEYS), slot=slot, label=label, missing=True)
    monkeypatch.setattr(detector._loot, '_evaluate_cards', lambda *_: pytest.fail('Discovery with a missing part'))
    for _ in range(4):
        assert detector.loot_cards(framed(raw, size)) is None
    assert detector._loot.stats['index_queries'] == 0


@pytest.mark.parametrize('size', SIZES)
def test_icon_and_complete_name_from_different_items_do_not_form_a_ready_card(vision, monkeypatch, size):
    detector, config, templates = vision
    raw = changed_part(config, templates, loot_frame(config, templates, KEYS), missing=True)
    other = config.chaos.loot_recognition['template_regions'][KEYS[0]]
    x, y, w, h = other['name']
    data = templates.get(config.chaos.scenes[KEYS[0]].template_key).image[y:y+h, x:x+w]
    left, top = round(548+580.5)+other['origin'][0]+x, 987+other['origin'][1]+y
    raw[top:top+h, left:left+w] = data
    monkeypatch.setattr(detector._loot, '_evaluate_cards', lambda *_: pytest.fail('Mixed identities passed readiness'))
    for _ in range(4):
        assert detector.loot_cards(framed(raw, size)) is None


@pytest.mark.parametrize('size', SIZES)
@pytest.mark.parametrize('change', ['moving', 'fading'])
def test_one_unsettled_name_prevents_all_discovery_until_three_complete_frames(vision, monkeypatch, size, change):
    detector, config, templates = vision
    original = detector._loot._evaluate_cards
    calls = []
    def evaluate(jobs):
        calls.append(True)
        return original(jobs)
    monkeypatch.setattr(detector._loot, '_evaluate_cards', evaluate)
    stages = ([dict(dy=dy) for dy in (12, 8, 4, 0)] if change == 'moving'
              else [dict(factor=factor) for factor in (.4, .6, .8, 1.)])
    for stage in stages:
        raw = changed_part(config, templates, loot_frame(config, templates, KEYS), **stage)
        assert detector.loot_cards(framed(raw, size)) is None
        assert not calls
    full = framed(loot_frame(config, templates, KEYS), size)
    assert detector.loot_cards(full) is None and not calls
    assert tuple(hit.key for hit in detector.loot_cards(full)) == KEYS
    assert len(calls) == 1


@pytest.mark.parametrize('size', SIZES)
@pytest.mark.parametrize('dy', [12, 16, -12, -16])
def test_icon_and_name_need_the_same_physical_offset(vision, monkeypatch, size, dy):
    detector, config, templates = vision
    raw = changed_part(config, templates, loot_frame(config, templates, KEYS), dy=dy)
    monkeypatch.setattr(detector._loot, '_evaluate_cards', lambda *_: pytest.fail('Independent part offsets accepted'))
    for _ in range(4):
        assert detector.loot_cards(framed(raw, size)) is None


@pytest.mark.parametrize('boundary', ['invalidate', 'recovery', 'empty', 'geometry', 'end'])
def test_boundaries_discard_two_ready_samples(vision, boundary):
    detector, config, templates = vision
    raw = loot_frame(config, templates, KEYS)
    frame = framed(raw, (1536, 864))
    for _ in range(2):
        assert detector.loot_cards(frame) is None
    if boundary == 'invalidate':
        detector.invalidate_loot_recognition()
    elif boundary == 'recovery':
        detector.reset_temporal()
    elif boundary == 'empty':
        assert detector.loot_cards(framed(np.zeros_like(raw), (1536, 864))) is None
    elif boundary == 'geometry':
        frame = framed(raw)
        # Preserve the established layout-change frame rejection, followed by
        # three new readiness samples at the new complete-client geometry.
        assert detector.loot_cards(frame) is None
        assert detector._loot.stats['layout_resets'] == 1
    else:
        detector.end_loot_recognition()
        detector.begin_loot_recognition()
    for _ in range(2):
        assert detector.loot_cards(frame) is None
    assert tuple(hit.key for hit in detector.loot_cards(frame)) == KEYS


def test_cached_part_failure_returns_without_discovery_then_rebuilds_readiness(vision):
    detector, config, templates = vision
    full = framed(loot_frame(config, templates, KEYS))
    for _ in range(3):
        result = detector.loot_cards(full)
    assert result
    before = detector._loot.stats['index_queries']
    bad = changed_part(config, templates, loot_frame(config, templates, KEYS), missing=True)
    assert detector.loot_cards(framed(bad)) is None
    assert detector._loot.stats['index_queries'] == before
    assert detector._loot.cache[1] is None
    for _ in range(2):
        assert detector.loot_cards(full) is None
        assert detector._loot.stats['index_queries'] == before
    assert tuple(hit.key for hit in detector.loot_cards(full)) == KEYS
    assert detector._loot.stats['index_queries'] == before+1


def test_readiness_does_not_keep_capture_and_one_shot_checks_completeness(vision):
    detector, config, templates = vision
    raw = transition()
    ref = weakref.ref(raw)
    assert detector.loot_cards(framed(raw)) is None
    del raw
    assert ref() is None
    detector.end_loot_recognition()
    assert detector.loot_cards(framed(transition())) is None
    full = framed(loot_frame(config, templates, KEYS))
    assert tuple(hit.key for hit in detector.loot_cards(full)) == KEYS
    assert not detector._loot.active and detector._loot.cache == [None]*3


@pytest.mark.parametrize('size', [*SIZES, (2321, 1306), (2322, 1305), (1537, 865)])
def test_all_76_identities_pass_real_gate_with_unchanged_clicks(vision, size):
    detector, config, templates = vision
    detector.end_loot_recognition()  # one-shot inspection: completeness without temporal evidence
    assert len(config.chaos.available_loot) == 76
    for key in config.chaos.available_loot:
        raw = loot_frame(config, templates, (key, key, key))
        frame = framed(raw, size)
        found = detector.loot_cards(frame)
        assert found is not None, (key, size)
        assert tuple(hit.key for hit in found) == (key, key, key)
        # Compare the new gate with the unchanged native verifier on this frame.
        detector.begin_loot_recognition()
        detector._loot.cache = [next(i for i, item in enumerate(detector._loot.identities) if item.key == key)]*3
        direct = detector.loot_cards(frame)
        assert direct == found
        detector.end_loot_recognition()
