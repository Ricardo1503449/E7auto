from threading import Barrier, Event, get_ident
import weakref

import cv2
import numpy as np
import pytest

from e7auto.core.types import Point, Rect, Size
from e7auto.features.chaos.configuration import EVENT_IDENTITIES
from e7auto.features.chaos.contracts import Detection
from e7auto.features.chaos.event_recognition import EventRecognizer
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import adapt_frame, CoordinateTransform, frame_view
from tests.helpers.chaos_game import chaos_config


@pytest.fixture
def recognizer():
    config=chaos_config()
    r=EventRecognizer(config.chaos,TemplateRepository(config))
    r.begin()
    yield r
    r.close()


def hit(key='event_observatory'):
    return Detection(key,1.,Rect(519,1069,1159,157),Point(1510,1110))


def bypass_readiness(recognizer, monkeypatch):
    """Isolate the original scheduler/cache tests from the new entry gate."""
    monkeypatch.setattr(recognizer.readiness, 'observe', lambda *args, **kwargs: True)


def prime_readiness(recognizer, frame):
    for _ in range(recognizer.settings.timing['stable_frames']-1):
        assert recognizer.recognize(frame) is None


def test_index_covers_registry_with_read_only_preprocessing(recognizer):
    r=recognizer
    assert set(r.identities)==EVENT_IDENTITIES
    for layout in (2, 3):
        expected = {key for key, spec in r.p['templates'].items() if spec['layout'] == layout}
        assert set(r.keys[layout]) == expected
        assert len(r.keys[layout]) == len(expected)
    for layout in (2,3):
        assert not r.features[layout].flags.writeable
    for item in r.identities.values():
        assert not item.image.flags.writeable and not item.mask.flags.writeable


def test_disabled_title_does_not_affect_shortlist_distance(recognizer):
    r = recognizer
    key = 'event_clear_pond_low_health'
    i = r.keys[2].index(key)
    query = r.features[2][i].copy()
    assert r.weights[2][i].tolist() == [1., 0.]
    assert not np.any(query[1])
    _, first = r.rank(2, query)
    query[1] = 1.
    _, second = r.rank(2, query)
    assert first[i] == second[i] == 0.


def test_empty_title_with_positive_weight_is_still_rejected():
    from copy import deepcopy
    from dataclasses import replace

    config = chaos_config()
    index = deepcopy(config.chaos.event_recognition)
    index['templates']['event_clear_pond_low_health']['weights'] = [1., 1.]
    with pytest.raises(ValueError, match='Empty event title index: event_clear_pond_low_health'):
        EventRecognizer(replace(config.chaos, event_recognition=index), TemplateRepository(config))


@pytest.mark.parametrize('size',[(2322,1306),(1536,864)])
@pytest.mark.parametrize('winner_layout',[2,3])
def test_layout_groups_search_in_rank_order_and_cancel_after_first_hit(recognizer,monkeypatch,size,winner_layout):
    r=recognizer;calls={2:[],3:[]};loser_started=Event();loser_finished=Event()
    query={layout:np.zeros_like(r.features[layout][0]) for layout in (2,3)}
    ordered={layout:[r.keys[layout][int(i)] for i in r.rank(layout,query[layout])[0]] for layout in (2,3)}
    target=ordered[winner_layout][r.p['top_k']]
    layouts={key:layout for layout in (2,3) for key in r.keys[layout]}
    def verify(source,key,*,cancelled,try_reference,**kwargs):
        layout=layouts[key]
        calls[layout].append((key,try_reference))
        if layout!=winner_layout:
            loser_started.set()
            assert cancelled.wait(2)
            loser_finished.set()
            return None
        assert loser_started.wait(2)
        return hit(key) if key==target else None
    monkeypatch.setattr(r,'verify',verify)
    raw=np.zeros((size[1],size[0],3),np.uint8)
    source=frame_view(adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(*size)))).crop(r.region)
    assert r._discover(source,query).key==target
    assert loser_finished.is_set()
    assert calls[winner_layout]==[(key,i<r.p['top_k']) for i,key in enumerate(ordered[winner_layout][:r.p['top_k']+1])]
    assert calls[5-winner_layout]==[(ordered[5-winner_layout][0],True)]
    assert r.stats['event_index_queries']==2
    assert r.stats['event_candidates_checked']==r.p['top_k']+2
    assert r.stats['event_fallbacks']==1


@pytest.mark.parametrize('size',[(2322,1306),(1536,864)])
def test_two_workers_only_read_native_pixels_and_reuse_pool(recognizer,monkeypatch,size):
    bypass_readiness(recognizer, monkeypatch)
    r=recognizer;owner=get_ident();seen=[];barrier=Barrier(2)
    def verify(source,key,**kwargs):
        seen.append(get_ident())
        assert not source.raw.flags.writeable
        assert source.transform.actual==Size(*size)
        barrier.wait(2)
        return hit(key)
    monkeypatch.setattr(r,'verify',verify)
    raw=np.full((size[1],size[0],3),r.p['foreground_min']+1,np.uint8)
    frame=adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(*size)))
    assert r.recognize(frame) is not None
    pool=r._pool
    r.invalidate();r.recognize(frame)
    assert r._pool is pool and pool._max_workers==2
    assert len(set(seen))==2 and all(t!=owner for t in seen)


@pytest.mark.parametrize('size',[(2322,1306),(1536,864)])
def test_coarse_frame_is_shared_per_discovery_and_not_created_for_cached_rechecks(recognizer,monkeypatch,size):
    bypass_readiness(recognizer, monkeypatch)
    r=recognizer;owner=get_ident();resizes=[];coarse=[];resize=cv2.resize
    def tracked_resize(source,*args,**kwargs):
        if kwargs.get('fx')==.5 and kwargs.get('fy')==.5:
            resizes.append(get_ident())
        return resize(source,*args,**kwargs)
    def verify(source,key,previous=None,*,small_source=None,**kwargs):
        if previous is not None:
            assert small_source is None
            return previous
        coarse.append(small_source)
        return None
    monkeypatch.setattr(cv2,'resize',tracked_resize)
    monkeypatch.setattr(r,'verify',verify)
    raw=np.full((size[1],size[0],3),r.p['foreground_min']+1,np.uint8)
    frame=adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(*size)))
    assert r.recognize(frame) is None
    assert len(coarse)==len(r.identities)
    if size==(2322,1306):
        assert resizes==[owner]
        assert coarse[0] is not None and not coarse[0].flags.writeable
        assert all(item is coarse[0] for item in coarse)
    else:
        assert not resizes and all(item is None for item in coarse)
    count=len(resizes)
    r.candidate=hit()
    assert r.recognize(frame)==hit()
    assert r.recognize(frame)==hit()
    assert len(resizes)==count
    r.invalidate();coarse.clear()
    assert r.recognize(frame) is None
    assert len(resizes)==count*2


def test_cached_candidate_only_reverifies_and_failure_breaks_evidence(recognizer,monkeypatch):
    bypass_readiness(recognizer, monkeypatch)
    r=recognizer;calls=[];expected=hit()
    monkeypatch.setattr(r,'_discover',lambda *args: expected)
    def verify(source,key,previous=None,**kwargs):
        calls.append((key,previous));return expected if len(calls)==1 else None
    monkeypatch.setattr(r,'verify',verify)
    frame=np.full((1306,2322,3),r.p['foreground_min']+1,np.uint8)
    assert r.recognize(frame)==expected
    assert r.recognize(frame)==expected
    assert r.recognize(frame) is None and r.candidate is None
    assert len(calls)==2 and all(key==expected.key and prev==expected for key,prev in calls)
    assert r.recognize(frame)==expected
    assert r.stats['event_cache_failures']==1
    r.end();assert r.candidate is None


@pytest.mark.parametrize('size',[(2322,1306),(1536,864)])
def test_worker_error_drains_jobs_and_invalidates_cache(recognizer,monkeypatch,size):
    bypass_readiness(recognizer, monkeypatch)
    from threading import Lock
    r=recognizer;started=Event();finished=Event();lock=Lock();calls=[]
    def verify(source,key,*,cancelled,**kwargs):
        with lock:
            calls.append(key);position=len(calls)
        if position==1:
            assert started.wait(2)
            raise RuntimeError('injected worker failure')
        started.set()
        assert cancelled.wait(2)
        finished.set()
        return None
    monkeypatch.setattr(r,'verify',verify)
    with pytest.raises(RuntimeError,match='injected worker failure'):
        raw=np.full((size[1],size[0],3),r.p['foreground_min']+1,np.uint8)
        r.recognize(adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(*size))))
    assert r.candidate is None and finished.is_set()
    pool=r._pool;r.close();assert pool._shutdown and r._pool is None


def test_nonreference_discovery_shares_preparation_and_releases_it_between_frames(recognizer, monkeypatch):
    bypass_readiness(recognizer, monkeypatch)
    r = recognizer
    scopes = []
    seen = []
    prepare = r.matcher.prepare_search
    def tracked(frame):
        scope = prepare(frame)
        scopes.append(weakref.ref(scope))
        return scope
    def verify(source, key, *, preparation, **kwargs):
        seen.append(weakref.ref(preparation))
        scene = r.settings.scenes[key]
        assert r.matcher.locate_all(source, scene.template_key, source.bounds,
                                    scene.threshold, scene.structure, limit=1,
                                    preparation=preparation) == ()
        return None
    monkeypatch.setattr(r.matcher, 'prepare_search', tracked)
    monkeypatch.setattr(r, 'verify', verify)
    for value in (r.p['foreground_min']+1, 255):
        frame = adapt_frame(np.full((864, 1536, 3), value, np.uint8),
                            CoordinateTransform(Size(2322, 1306), Size(1536, 864)))
        assert r.recognize(frame) is None
        assert all(ref == scopes[-1] for ref in seen)
        assert scopes[-1]() is None
        seen.clear()
    assert len(scopes) == 2


def test_real_altar_with_different_description_wrapping(recognizer):
    from tests.helpers.paths import ROOT

    path = ROOT / 'tests/fixtures/chaos/resolution/event_altar_2304.png'
    raw = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert raw is not None and raw.shape == (1296, 2304, 3)
    transform = CoordinateTransform(Size(2322, 1306), Size(2304, 1296))
    target = transform.point(Point(1850, 1110))
    prime_readiness(recognizer, adapt_frame(raw.copy(), transform))
    # The real capture wraps the last two reward-name characters onto line two.
    # Repeated offline frames check discovery and current-pixel cache rechecks.
    for _ in range(3):
        hit = recognizer.recognize(adapt_frame(raw.copy(), transform))
        assert hit is not None and hit.key == 'event_altar'
        assert abs(hit.anchor.x - target.x) <= 2
        assert abs(hit.anchor.y - target.y) <= 2
        assert transform.point(hit.anchor) is hit.anchor
    assert recognizer.stats['event_cache_checks'] == 2
    assert recognizer.stats['event_cache_failures'] == 0


def test_real_torn_map_initial_title_variant(recognizer):
    from tests.helpers.paths import ROOT

    path = ROOT / 'tests/fixtures/chaos/resolution/event_torn_map_path_1536.png'
    raw = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert raw is not None and raw.shape == (864, 1536, 3)
    transform = CoordinateTransform(Size(2322, 1306), Size(1536, 864))
    target = transform.point(Point(1510, 1110))
    assert recognizer.settings.scenes['event_torn_map_path'].threshold == .92
    assert recognizer.settings.scenes['event_torn_map'].threshold == .96
    prime_readiness(recognizer, adapt_frame(raw.copy(), transform))
    for _ in range(3):
        hit = recognizer.recognize(adapt_frame(raw.copy(), transform))
        assert hit is not None and hit.key == 'event_torn_map_path'
        assert abs(hit.anchor.x-target.x) <= 2 and abs(hit.anchor.y-target.y) <= 2
        assert hit.evidence.structure_confidence >= .8
        assert transform.point(hit.anchor) is hit.anchor
    assert recognizer.stats['event_cache_checks'] == 2
    assert recognizer.stats['event_cache_failures'] == 0


def red_phantom_frame(stage):
    from tests.helpers.paths import ROOT

    path = ROOT/f'tests/fixtures/chaos/resolution/event_red_phantom_{stage}_1536.png'
    raw = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert raw is not None and raw.shape == (864, 1536, 3)
    return adapt_frame(raw, CoordinateTransform(Size(2322, 1306), Size(1536, 864)))


@pytest.mark.parametrize('nested', [False, True])
def test_real_intro_with_empty_titles_does_not_start_discovery(recognizer, monkeypatch, nested):
    frame = red_phantom_frame('intro')
    if nested:
        frame = frame_view(frame).crop(recognizer.region)
    monkeypatch.setattr(recognizer, '_discover',
                        lambda *args: pytest.fail('Full discovery on a pre-option narrative frame'))
    assert recognizer.recognize(frame) is None
    assert recognizer.candidate is None and recognizer._pool is None
    assert recognizer.stats['event_candidates_checked'] == 0


def test_intro_then_real_options_uses_fresh_pixels_and_keeps_cache(recognizer):
    assert recognizer.recognize(red_phantom_frame('intro')) is None
    assert recognizer.stats['event_candidates_checked'] == 0
    prime_readiness(recognizer, red_phantom_frame('options'))
    for nested in (False, True, False):
        frame = red_phantom_frame('options')
        if nested:
            frame = frame_view(frame).crop(recognizer.region)
        found = recognizer.recognize(frame)
        assert found is not None and found.key == 'event_red_phantom'
        assert 310 <= found.anchor.x <= 323 and 714 <= found.anchor.y <= 730
    assert recognizer.stats['event_cache_checks'] == 2
    assert recognizer.stats['event_cache_failures'] == 0


def test_baseline_empty_titles_do_not_start_discovery(recognizer, monkeypatch):
    monkeypatch.setattr(recognizer, '_discover',
                        lambda *args: pytest.fail('Baseline discovery without title evidence'))
    assert recognizer.recognize(np.zeros((1306, 2322, 3), np.uint8)) is None
    assert recognizer._pool is None and recognizer.stats['event_candidates_checked'] == 0


@pytest.mark.parametrize('size', [(1536, 864), (1920, 1080), (2304, 1296), (2560, 1440)])
def test_nonreference_empty_titles_do_not_start_discovery(recognizer, monkeypatch, size):
    monkeypatch.setattr(recognizer, '_discover',
                        lambda *args: pytest.fail('Discovery without any visible title'))
    frame = adapt_frame(np.zeros((size[1], size[0], 3), np.uint8),
                        CoordinateTransform(Size(2322, 1306), Size(*size)))
    assert recognizer.recognize(frame) is None


@pytest.mark.parametrize('layout,slot', [(2, 0), (2, 1), (3, 0), (3, 2)])
@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_one_visible_title_cannot_authorize_discovery(recognizer, monkeypatch, layout, slot, size):
    calls = []
    monkeypatch.setattr(recognizer, '_discover', lambda *args: calls.append(args))
    raw = np.zeros((1306, 2322, 3), np.uint8)
    x, y, _, _ = recognizer.p['layouts'][layout][slot]
    raw[y+20:y+30, x+30:x+60] = 255
    pixels = raw if size == (2322, 1306) else cv2.resize(raw, size, interpolation=cv2.INTER_AREA)
    frame = adapt_frame(pixels, CoordinateTransform(Size(2322, 1306), Size(*size)))
    for _ in range(recognizer.settings.timing['stable_frames']+1):
        assert recognizer.recognize(frame) is None
    assert not calls


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_cached_identity_still_rechecks_when_titles_are_empty(recognizer, monkeypatch, size):
    calls = []
    recognizer.candidate = hit('event_red_phantom')
    def verify(source, key, previous):
        calls.append((key, previous))
        return None
    monkeypatch.setattr(recognizer, 'verify', verify)
    frame = (np.zeros((1306, 2322, 3), np.uint8) if size == (2322, 1306)
             else red_phantom_frame('intro'))
    assert recognizer.recognize(frame) is None
    assert len(calls) == 1 and recognizer.candidate is None
    assert recognizer.stats['event_cache_checks'] == 1
    assert recognizer.stats['event_cache_failures'] == 1


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864), (2304, 1296), (2560, 1440)],
                         ids=['baseline', 'native-1536', 'native-2304', 'native-2560'])
def test_all_registered_titles_remain_eligible_for_discovery(recognizer, monkeypatch, size):
    calls = []
    monkeypatch.setattr(recognizer, '_discover', lambda *args: calls.append(args))
    transform = CoordinateTransform(Size(2322, 1306), Size(*size))
    for key, item in recognizer.identities.items():
        reference = np.zeros((1306, 2322, 3), np.uint8)
        x, y = item.origin
        height, width = item.image.shape[:2]
        reference[y:y+height, x:x+width] = item.image
        raw = cv2.resize(reference, size,
                         interpolation=cv2.INTER_AREA if size[0] < 2322 else cv2.INTER_LINEAR)
        recognizer.invalidate()
        before = len(calls)
        prime_readiness(recognizer, adapt_frame(raw, transform))
        assert recognizer.recognize(adapt_frame(raw, transform)) is None
        assert len(calls) == before+1, (key, size)


def test_baseline_intro_then_options_keeps_original_matcher_and_anchor(recognizer, monkeypatch):
    r = recognizer
    monkeypatch.setattr(r.matcher, '_search',
                        lambda *args, **kwargs: pytest.fail('Baseline used non-reference matching'))
    assert r.recognize(np.zeros((1306, 2322, 3), np.uint8)) is None
    assert r.stats['event_candidates_checked'] == 0
    reference = np.zeros((1306, 2322, 3), np.uint8)
    item = r.identities['event_red_phantom']
    x, y = item.origin
    height, width = item.image.shape[:2]
    reference[y:y+height, x:x+width] = item.image
    # Assembled baseline layout from original template pixels, not a new capture.
    prime_readiness(r, reference.copy())
    for _ in range(3):
        found = r.recognize(reference.copy())
        assert found is not None and found.key == 'event_red_phantom'
        assert (found.anchor.x, found.anchor.y) == (480, 1090)
    assert r.stats['event_cache_checks'] == 2 and r.stats['event_cache_failures'] == 0
