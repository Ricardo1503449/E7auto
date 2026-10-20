import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


def setup():
    game = ChaosGame(chaos_config(), event_type='event_golem')
    flow = ChaosFlow(game.config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('entry', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState()
    return game, flow


@pytest.mark.parametrize('kind,expected', [('event', 4.), ('normal', .3)])
def test_only_event_entry_waits_four_seconds_total(kind, expected, monkeypatch):
    game, flow = setup()
    game.state = 'map'; game.routes = (kind,)
    entered, recognized = [], []
    game.on_click = lambda g,k: entered.append(g.clock.now) if k=='node_enter' else None
    def event():
        recognized.append(game.clock.now)
        raise StopExecution(StopReason.MANUAL_STOP)
    def battle(_):
        recognized.append(game.clock.now)
        return True
    monkeypatch.setattr(flow, '_event', event)
    monkeypatch.setattr(flow, '_battle', battle)
    if kind=='event':
        with pytest.raises(StopExecution):flow._explore()
    else:flow._explore()
    assert recognized[0]-entered[0] == pytest.approx(expected)


def test_manual_stop_during_event_entry_wait_prevents_option_recognition(monkeypatch):
    game, flow = setup()
    game.state = 'map'; game.routes = ('event',)
    entered = []
    game.on_click = lambda g,k: entered.append(g.clock.now) if k=='node_enter' else None
    def captured(g):
        if entered and g.clock.now-entered[0]>=1:
            flow.runtime.control.request(StopReason.MANUAL_STOP)
    game.on_capture = captured
    monkeypatch.setattr(flow, '_event', lambda: pytest.fail('Recognition after manual stop'))
    with pytest.raises(StopExecution) as error:flow._explore()
    assert error.value.reason is StopReason.MANUAL_STOP
    assert 'event_golem' not in game.actions


def test_multiple_intro_pages_bypass_index_and_finish_text_bypasses_map(monkeypatch):
    game, flow = setup()
    game.kind = 'event';game.state = 'event_intro';game.event_intro_remaining = 3
    original_choice, original_map = game.event_choice, game.map_view
    choices = []
    def choice(frame):
        assert frame['state'] != 'event_intro'
        choices.append(frame['state'])
        return original_choice(frame)
    def map_view(frame):
        assert frame['state'] != 'text'
        return original_map(frame)
    monkeypatch.setattr(game, 'event_choice', choice)
    monkeypatch.setattr(game, 'map_view', map_view)
    flow._event()
    assert game.actions == ['text_continue']*3+['event_golem','text_continue']
    assert choices == ['event']*3 and game.state=='map'


def test_transient_missing_triangle_returns_to_triangle_check(monkeypatch):
    game, flow = setup()
    game.kind='event';game.state='event_intro';game.event_intro_remaining=1
    original = game.control
    misses=[]
    def control(frame,key,region=None):
        if frame['state']=='event_intro' and key=='text_continue' and not misses:
            misses.append(1);return None
        return original(frame,key,region)
    monkeypatch.setattr(game,'control',control)
    flow._event()
    assert game.actions==['text_continue','event_golem','text_continue']
    assert game.state=='map'


def test_recovery_restarts_candidate_evidence_without_replaying_choice(monkeypatch):
    game,flow=setup();game.kind=game.state='event'
    original=game.event_choice;generations=[]
    def choose(frame):
        result=original(frame)
        if result:
            generations.append(flow.runtime.network_recovery_generation)
            if len(generations)==2:game.network_remaining=4
        return result
    monkeypatch.setattr(game,'event_choice',choose)
    flow._event()
    assert generations==[0,0,1,1,1]
    assert game.actions.count('event_golem')==1 and game.actions.count('network_retry')==4


def test_manual_stop_at_third_event_frame_prevents_choice_and_clears_candidate(monkeypatch):
    game,flow=setup();game.kind=game.state='event'
    original=game.event_choice;seen=[]
    def choose(frame):
        result=original(frame)
        if result:
            seen.append(result)
            if len(seen)==3:flow.runtime.control.request(StopReason.MANUAL_STOP)
        return result
    monkeypatch.setattr(game,'event_choice',choose)
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason is StopReason.MANUAL_STOP
    assert not game.actions and game._event_candidate is None


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_typing_frame_does_not_spend_entry_deadline_before_real_options(monkeypatch, size):
    import cv2
    import numpy as np

    from e7auto.core.types import Size
    from e7auto.features.chaos.event_recognition import EventRecognizer
    from e7auto.features.chaos.events import dispatcher
    from e7auto.resources.templates import TemplateRepository
    from e7auto.vision.frames import adapt_frame, CoordinateTransform
    from tests.helpers.paths import ROOT

    game, flow = setup()
    game.kind = 'event'
    game.state = 'event_intro'
    recognizer = EventRecognizer(game.config.chaos, TemplateRepository(game.config))
    frames = {}
    if size == (2322, 1306):
        frames['intro'] = np.zeros((1306, 2322, 3), np.uint8)
        reference = np.zeros((1306, 2322, 3), np.uint8)
        item = recognizer.identities['event_red_phantom']
        x, y = item.origin
        height, width = item.image.shape[:2]
        reference[y:y+height, x:x+width] = item.image
        frames['options'] = reference  # Assembled baseline layout, original pixels.
    else:
        for stage in ('intro', 'options'):
            path = ROOT/f'tests/fixtures/chaos/resolution/event_red_phantom_{stage}_1536.png'
            raw = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
            frames[stage] = adapt_frame(raw, CoordinateTransform(Size(2322, 1306), Size(*size)))
    started = game.clock.now
    captured = game.captures
    original_control = game.control
    original_discovery = recognizer._discover
    dispatched = []
    slow_scans = []
    def control(frame, key, region=None):
        if key == 'text_continue':
            return None  # Text is typing, then replaced by the option page.
        return original_control(frame, key, region)
    def slow_discovery(source, queries):
        if not any(np.any(query) for query in queries.values()):
            slow_scans.append(True)
            game.clock.sleep(19.)  # Model the recorded gap before timeout.
            return None
        return original_discovery(source, queries)
    def captured_frame(g):
        if g.clock.now-started >= .5:
            g.state = 'event'
    def choice(frame):
        return recognizer.recognize(frames['intro' if frame['state'] == 'event_intro' else 'options'])
    monkeypatch.setattr(game, 'control', control)
    monkeypatch.setattr(game, 'event_choice', choice)
    monkeypatch.setattr(game, 'begin_event_recognition', recognizer.begin)
    monkeypatch.setattr(game, 'end_event_recognition', recognizer.end)
    monkeypatch.setattr(game, 'invalidate_event_recognition', recognizer.invalidate)
    monkeypatch.setattr(recognizer, '_discover', slow_discovery)
    monkeypatch.setattr(dispatcher, 'dispatch',
                        lambda context, found, summary: dispatched.append((found, summary)))
    game.on_capture = captured_frame
    try:
        flow._event()
    finally:
        recognizer.close()
    assert not slow_scans
    assert len(dispatched) == 1 and dispatched[0][0].key == 'event_red_phantom'
    assert dispatched[0][1]['event_cache_checks'] == 2
    assert game.captures-captured >= 8 and game.clock.now-started < 15.
    assert not game.actions


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_partial_altar_returns_for_new_frames_before_entry_deadline(monkeypatch, size):
    import cv2
    import numpy as np

    from e7auto.core.types import Size
    from e7auto.features.chaos.event_recognition import EventRecognizer
    from e7auto.features.chaos.events import dispatcher
    from e7auto.resources.templates import TemplateRepository
    from e7auto.vision.frames import adapt_frame, CoordinateTransform, frame_view
    from tests.helpers.paths import ROOT

    game, flow = setup()
    game.kind = 'event'
    game.state = 'event_intro'
    recognizer = EventRecognizer(game.config.chaos, TemplateRepository(game.config))
    transform = CoordinateTransform(Size(2322, 1306), Size(*size))
    frames = {}
    for stage, name in [('partial', 'event_altar_transition_1536.png'), ('complete', 'event_altar_2304.png')]:
        path = ROOT/'tests/fixtures/chaos/resolution'/name
        raw = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        if raw.shape[:2] != (size[1], size[0]):
            # Derived boundary input; the 1536 partial is the actual user PNG.
            raw = cv2.resize(raw, size, interpolation=cv2.INTER_AREA if size[0] < raw.shape[1] else cv2.INTER_LINEAR)
        frames[stage] = adapt_frame(raw, transform)
    partial = frame_view(frames['partial']).crop(recognizer.region).raw
    discovery = recognizer._discover
    old_scans, dispatched = [], []
    started = game.clock.now

    def discover(source, queries):
        if np.array_equal(source.raw, partial):
            old_scans.append(True)
            game.clock.sleep(19.)  # Recorded single-frame search can exhaust the page deadline.
            return None
        return discovery(source, queries)

    def choice(frame):
        return recognizer.recognize(frames['partial' if frame['state'] == 'event_intro' else 'complete'])

    def captured(g):
        if g.clock.now-started >= .5:
            g.state = 'event'

    control = game.control
    monkeypatch.setattr(game, 'control', lambda frame, key, region=None:
                        None if key == 'text_continue' else control(frame, key, region))
    monkeypatch.setattr(game, 'event_choice', choice)
    monkeypatch.setattr(game, 'begin_event_recognition', recognizer.begin)
    monkeypatch.setattr(game, 'end_event_recognition', recognizer.end)
    monkeypatch.setattr(game, 'invalidate_event_recognition', recognizer.invalidate)
    monkeypatch.setattr(recognizer, '_discover', discover)
    monkeypatch.setattr(dispatcher, 'dispatch', lambda context, found, summary: dispatched.append((found, summary)))
    game.on_capture = captured
    try:
        flow._event()
    finally:
        recognizer.close()
    assert not old_scans and not game.actions
    assert len(dispatched) == 1 and dispatched[0][0].key == 'event_altar'
    assert dispatched[0][1]['event_cache_checks'] == 2
    assert dispatched[0][1]['event_readiness_waits'] >= 3
    assert game.clock.now-started < 15.
