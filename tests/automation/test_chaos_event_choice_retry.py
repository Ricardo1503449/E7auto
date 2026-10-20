"""Event choices retry only on fresh evidence from their own current stage."""
from dataclasses import replace

import numpy as np
import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.ports import DisplayGeometry
from e7auto.core.types import ClientPoint, ClientRect, Rect, Size
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.events.context import EventContext
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


class ChoiceGame(ChaosGame):
    def __init__(self, config, *, size=(2322, 1306), dropped=None, **kwargs):
        super().__init__(config, **kwargs)
        self.size = size
        self.dropped = dict(dropped or {})
        self.sent = []
        self._pixels = np.zeros((size[1], size[0], 3), np.uint8)

    def capture_client(self, window, bounds):
        self._model_frame = super().capture_client(window, bounds)
        return self._pixels

    def prepare_frame(self, frame):
        return self._model_frame

    def network_connection_error(self, frame):
        return super().network_connection_error(self._model_frame)

    def network_retry(self, frame):
        return super().network_retry(self._model_frame)

    def _hit(self, key, frame):
        hit = super()._hit(key, frame)
        sx, sy = self.size[0] / 2322, self.size[1] / 1306
        bounds = ClientRect(round(hit.bounds.x*sx), round(hit.bounds.y*sy),
                            max(1, round(hit.bounds.width*sx)), max(1, round(hit.bounds.height*sy)))
        anchor = ClientPoint(round(hit.anchor.x*sx), round(hit.anchor.y*sy))
        self.points[anchor] = key
        return replace(hit, bounds=bounds, anchor=anchor)

    def click(self, window, point):
        if point == ClientPoint(round(2044*self.size[0]/2322), round(62*self.size[1]/1306)):
            self.points[point] = 'enable_auto'
        key = self.points.get(point)
        if not self.network_remaining:
            self.sent.append((key, self.clock.now, point, self.state, self.captures))
            if self.dropped.get(key, 0):
                self.dropped[key] -= 1
                self.actions.append(key)
                if self.on_click:
                    self.on_click(self, key)
                return
            if key == 'event_red_phantom':
                self.actions.append(key)
                self.state = 'depart_select'
                return
        super().click(window, point)

    def _visible(self):
        if self.state == 'depart_select':
            return ['depart_title']
        return super()._visible()


@pytest.fixture(scope='module')
def config():
    return chaos_config()


def setup(config, *, size=(2322, 1306), **kwargs):
    game = ChoiceGame(config, size=size, **kwargs)
    deps = game.dependencies()
    if size != (2322, 1306):
        deps.windows.display_geometry = DisplayGeometry(
            1, r'\\.\DISPLAY1', Rect(0, 0, 2560, 1440), Size(2560, 1440), 120)
    stop = StopController()
    flow = ChaosFlow(config, deps, stop, SnapshotPublisher(
        RuntimeSnapshot.chaos_run('choice-retry', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState.for_lineup(config.chaos.lineup)
    flow.state.node_kind = game.kind = game.state = 'event'
    assert flow.runtime.transform.actual == Size(*size)
    return game, flow, stop


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
@pytest.mark.parametrize('event,choice', [
    ('event_golem', 'event_golem'),
    ('event_vestment', 'event_vestment'),
    ('event_vestment', 'event_vestment_no_loot'),
])
def test_first_dropped_choice_is_retried_and_returns_to_original_map(config, size, event, choice):
    game, flow, _ = setup(config, size=size, event_type=event, dropped={choice: 1})
    flow._event()
    clicks = [record for record in game.sent if record[0] == choice]
    assert len(clicks) == 2
    assert clicks[1][1] - clicks[0][1] >= 1.5
    assert clicks[1][4] > clicks[0][4] + 2
    assert game.state == 'map' and game.floor == flow.state.floor == 1
    assert game.actions.count('text_continue') == 1


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
@pytest.mark.parametrize('event', sorted(HANDLERS))
def test_every_registered_entry_binds_retry_to_its_own_destination(config, monkeypatch, size, event):
    game, flow, _ = setup(config, size=size, event_type=event, dropped={event: 1})
    original = EventContext.choose
    destinations = []

    class DestinationConfirmed(Exception):
        pass

    def observed(context, choice, **kwargs):
        result = original(context, choice, **kwargs)
        destinations.append(result)
        raise DestinationConfirmed  # Do not retest unrelated reward/battle internals here.

    monkeypatch.setattr(EventContext, 'choose', observed)
    with pytest.raises(DestinationConfirmed):
        flow._event()
    assert len(destinations) == 1 and destinations[0] is not None
    assert game.actions == [event, event]
    assert game.sent[1][1] - game.sent[0][1] >= 1.5
    retries = [d for name, d in flow.runtime.deps.logger.events if name == 'chaos_event_choice_retry']
    assert len(retries) == 1 and retries[0]['choice'] == event


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
@pytest.mark.parametrize('has_loot', [False, True])
def test_both_vestment_stages_retry_independently(config, size, has_loot):
    second = 'event_vestment_with_loot' if has_loot else 'event_vestment_no_loot'
    game, flow, _ = setup(config, size=size, event_type='event_vestment',
                          vestment_has_loot=has_loot, dropped={'event_vestment': 1, second: 1})
    flow._event()
    assert game.actions == ['event_vestment']*2 + [second]*2 + ['text_continue']
    assert game.state == 'map'


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_armory_second_option_retries_without_repeating_first_battle_or_reward(config, size):
    game, flow, _ = setup(config, size=size, event_type='event_old_armory',
                          dropped={'event_old_armory_left': 1})
    flow._event()
    assert game.actions.count('event_old_armory') == 1
    assert game.actions.count('event_old_armory_left') == 2
    assert game.actions.count('start_battle') == game.actions.count('event_reward_close') == 2
    assert game.actions.count('text_continue') == 1 and game.state == 'map'


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_three_total_attempts_are_bounded_and_report_the_current_stage(config, size):
    game, flow, _ = setup(config, size=size, event_type='event_golem', dropped={'event_golem': 99})
    with pytest.raises(StopExecution) as error:
        flow._event()
    assert error.value.reason is StopReason.CHAOS_UNKNOWN_CONTENT
    assert 'event_golem' in error.value.detail and 'attempts=3/3' in error.value.detail
    assert 'phase=event_text_or_map' in error.value.detail
    assert game.actions == ['event_golem']*3
    assert all(b[1]-a[1] >= 1.5 for a, b in zip(game.sent, game.sent[1:]))


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_retries_do_not_restart_the_original_stage_deadline(config, size):
    short = replace(config, chaos=replace(config.chaos,
        timing=dict(config.chaos.timing, page_timeout_ms=1700)))
    game, flow, _ = setup(short, size=size, event_type='event_golem', dropped={'event_golem': 99})
    with pytest.raises(StopExecution, match='timed out'):
        flow._event()
    first = game.sent[0][1]
    assert len(game.sent) == 2
    assert all(record[1] < first+1.7 for record in game.sent)
    assert first+1.7 <= game.clock.now < first+2.1


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
@pytest.mark.parametrize('visibility', ['absent', 'intermittent', 'other_event'])
def test_missing_or_unstable_original_option_never_authorizes_retry(config, monkeypatch, size, visibility):
    game, flow, _ = setup(config, size=size, event_type='event_golem', dropped={'event_golem': 99})
    original = game.control
    samples = 0

    def control(frame, key, region=None):
        nonlocal samples
        if game.actions:
            if key == 'event_golem':
                samples += 1
                if visibility != 'intermittent' or samples % 2 == 0:
                    return None
            if visibility == 'other_event' and key == 'event_trap':
                return game._hit(key, frame)
        return original(frame, key, region)

    monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution, match='timed out'):
        flow._event()
    assert samples > 3 and game.actions == ['event_golem']


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_destination_takes_priority_even_when_old_template_also_matches(config, monkeypatch, size):
    game, flow, _ = setup(config, size=size, event_type='event_vestment',
                          dropped={'event_vestment_no_loot': 1})
    original = game.control
    old_checks_on_new_page = []

    def control(frame, key, region=None):
        if key == 'event_vestment' and frame['state'] != 'event':
            old_checks_on_new_page.append(frame['state'])
            return game._hit(key, frame)
        return original(frame, key, region)

    monkeypatch.setattr(game, 'control', control)
    flow._event()
    assert not old_checks_on_new_page
    assert game.actions == ['event_vestment', 'event_vestment_no_loot', 'event_vestment_no_loot', 'text_continue']


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_retry_uses_latest_detection_anchor_without_a_second_coordinate_conversion(config, monkeypatch, size):
    game, flow, _ = setup(config, size=size, event_type='event_golem', dropped={'event_golem': 1})
    original = game.control

    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if hit is not None and key == 'event_golem' and game.actions:
            hit = replace(hit, bounds=replace(hit.bounds, x=hit.bounds.x+8),
                          anchor=ClientPoint(hit.anchor.x+8, hit.anchor.y))
            game.points[hit.anchor] = key
        return hit

    monkeypatch.setattr(game, 'control', control)
    flow._event()
    assert game.sent[1][2] == ClientPoint(game.sent[0][2].x+8, game.sent[0][2].y)
    assert game.state == 'map'


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_recovery_rebuilds_evidence_without_refunding_attempts_or_deadline(config, monkeypatch, size):
    game, flow, _ = setup(config, size=size, event_type='event_golem', dropped={'event_golem': 99})
    original = game.control
    samples = []

    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if key == 'event_golem' and game.actions and hit:
            samples.append((flow.runtime.network_recovery_generation, game.captures))
            if len(samples) == 2:
                game.network_remaining = 4
        return hit

    monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution, match='attempts=3/3'):
        flow._event()
    assert game.actions.count('event_golem') == 3 and game.actions.count('network_retry') == 4
    assert flow.runtime.network_recovery_generation == 1
    assert [generation for generation, _ in samples[:2]] == [0, 0]
    assert len({capture for generation, capture in samples
                if generation == 1 and capture <= game.sent[1][4]}) >= 3
    assert game.clock.now > 40 and flow.runtime.active_monotonic() < 15


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_manual_stop_on_retry_prevents_any_later_action(config, size):
    game, flow, stop = setup(config, size=size, event_type='event_vestment',
                             dropped={'event_vestment_no_loot': 1})

    def click(g, key):
        if key == 'event_vestment_no_loot' and g.actions.count(key) == 2:
            stop.request(StopReason.MANUAL_STOP)

    game.on_click = click
    with pytest.raises(StopExecution) as error:
        flow._event()
    assert error.value.reason is StopReason.MANUAL_STOP
    assert game.actions == ['event_vestment', 'event_vestment_no_loot', 'event_vestment_no_loot']


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
@pytest.mark.parametrize('marker', ['levelup', 'levelup_close'])
def test_incomplete_experience_popup_blocks_choice_retries(config, monkeypatch, size, marker):
    game, flow, _ = setup(config, size=size, event_type='event_torch', dropped={'event_torch': 1})
    original = game._visible
    monkeypatch.setattr(game, '_visible', lambda: ['event_torch', marker] if game.actions else original())
    with pytest.raises(StopExecution, match='timed out'):
        flow._event()
    assert game.actions == ['event_torch']


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline-assembled', 'native-real'])
def test_vestment_retry_verifies_pixels_with_original_size_specific_matchers(config, monkeypatch, size):
    import cv2

    from e7auto.features.chaos.vision import ChaosVision
    from e7auto.resources.templates import TemplateRepository
    from e7auto.vision.frames import CoordinateTransform, adapt_frame
    from tests.helpers.paths import ROOT

    key = 'event_vestment_no_loot'
    repository = TemplateRepository(config)
    vision = ChaosVision(config, repository)
    if size == (1536, 864):
        path = ROOT/'tests/fixtures/chaos/resolution/event_vestment_no_loot_1536.png'
        pixels = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    else:
        # Original template pixels on an assembled reference layout, not a live capture.
        pixels = np.zeros((1306, 2322, 3), np.uint8)
        image = repository.get(config.chaos.scenes[key].template_key).image
        height, width = image.shape[:2]
        pixels[1069:1069+height, 308:308+width] = image
    frame_view = adapt_frame(pixels, CoordinateTransform(Size(2322, 1306), Size(*size)))
    game, flow, _ = setup(config, size=size, event_type='event_vestment', dropped={key: 1})
    original = game.control
    pixel_hits = []

    def control(frame, name, region=None):
        if name == key and frame['state'] == 'vestment_subchoice':
            hit = vision.control(frame_view, name, region)
            assert hit is not None
            game.points[hit.anchor] = name
            pixel_hits.append((game.captures, hit))
            return hit
        return original(frame, name, region)

    monkeypatch.setattr(game, 'control', control)
    try:
        flow._event()
    finally:
        vision.close_event_recognition()
        vision.close_loot_recognition()
    clicks = [item for item in game.sent if item[0] == key]
    assert len(clicks) == 2 and len({capture for capture, _ in pixel_hits}) >= 6
    assert clicks[1][2] == pixel_hits[-1][1].anchor
    if size == (1536, 864):
        assert clicks[1][2] == ClientPoint(318, 735)
    assert game.state == 'map' and game.actions.count('event_vestment') == 1


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
def test_window_loss_before_retry_cannot_send_input(config, monkeypatch, size):
    game, flow, _ = setup(config, size=size, event_type='event_golem', dropped={'event_golem': 1})
    original = game.control

    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if hit is not None and key == 'event_golem' and game.sent and game.clock.now-game.sent[0][1] >= 1.5:
            windows = flow.runtime.deps.windows
            windows.state = replace(windows.state, exists=False)
        return hit

    monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution) as error:
        flow._event()
    assert error.value.reason is not StopReason.CHAOS_UNKNOWN_CONTENT
    assert game.actions == ['event_golem']


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
@pytest.mark.parametrize('event', ['event_trap', 'event_backpack', 'event_mushroom',
                                    'event_tablet', 'event_red_phantom'])
def test_nontext_option_exhaustion_keeps_original_timeout_reason(config, size, event):
    game, flow, _ = setup(config, size=size, event_type=event, dropped={event: 99})
    with pytest.raises(StopExecution) as error:
        flow._event()
    assert error.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert 'attempts=3/3' in error.value.detail
    assert game.actions == [event]*3


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)], ids=['baseline', 'native'])
@pytest.mark.parametrize('slow_part', ['destination', 'current_option'])
def test_recognition_finishing_after_deadline_cannot_authorize_input(config, monkeypatch, size, slow_part):
    game, flow, _ = setup(config, size=size, event_type='event_golem', dropped={'event_golem': 1})
    original = game.control

    def control(frame, key, region=None):
        if game.actions and key == ('text_continue' if slow_part == 'destination' else 'event_golem'):
            game.clock.sleep(16.)
            return game._hit(key, frame)
        return original(frame, key, region)

    monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution, match='timed out'):
        flow._event()
    assert game.actions == ['event_golem']
