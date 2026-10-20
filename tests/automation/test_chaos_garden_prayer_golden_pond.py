"""New event bindings and the optional experience/all-hero rank branches."""
import struct

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


@pytest.fixture(scope='module')
def config():
    return chaos_config()


class PrayerGame(ChaosGame):
    """The text remains detectable behind the popup, including during its fade."""
    close_delay = .6
    closed_at = None
    destination = 'text'
    popup_controls = ('levelup', 'levelup_close')

    def _visible(self):
        if self.state in ('prayer_popup', 'prayer_closing'):
            return [*self.popup_controls, 'text_continue']
        return super()._visible()

    def capture_client(self, window, bounds):
        if self.state == 'prayer_closing' and self.clock.now - self.closed_at >= self.close_delay:
            self.state = self.destination
        return super().capture_client(window, bounds)

    def click(self, window, point):
        key = self.points.get(point)
        if not self.network_remaining and key == 'levelup_close':
            assert self.state == 'prayer_popup'
            self.actions.append(key)
            self.state, self.closed_at = 'prayer_closing', self.clock.now
            if self.on_click:
                self.on_click(self, key)
            return
        if not self.network_remaining and key == 'text_continue':
            assert self.state not in ('prayer_popup', 'prayer_closing')
        super().click(window, point)


def setup(config, event='event_prayer_room', *, floor=2, game_type=PrayerGame):
    game = game_type(config, event_type=event)
    stop = StopController()
    flow = ChaosFlow(config, game.dependencies(), stop,
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('four-events', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState(floor=floor, node_kind='event')
    game.state = game.kind = 'event'
    game.floor = floor
    return game, flow, stop


@pytest.mark.parametrize('event,layout,target,steps', [
    ('event_withered_garden', 3, (1850, 1090), ['text_continue']),
    ('event_prayer_room', 2, (1510, 1090), ['text_continue']),
    ('event_golden_patterns', 3, (1161, 1090), ['text_continue']),
    ('event_clear_pond', 2, (1510, 1090), ['event_reward_close', 'text_continue']),
])
def test_registered_binding_and_route(config, event, layout, target, steps):
    game, flow, _ = setup(config, event)
    scene = config.chaos.scenes[event]
    spec = config.chaos.event_recognition['templates'][event]
    assert event in HANDLERS and scene.template_key == 'chaos:' + event
    assert spec['layout'] == layout and len(spec['titles']) == layout
    width, height = struct.unpack('>II', config.template_paths[scene.template_key].read_bytes()[16:24])
    assert tuple(round(o + extent * a) for o, extent, a in zip(spec['origin'], (width, height), scene.action)) == target
    flow._event()
    assert game.actions == [event, *steps]
    assert game.state == 'map' and game.floor == flow.state.floor == 2
    assert not flow.state.counted and game.node_index == 1
    if event == 'event_clear_pond':
        assert flow.state.reward_stage == 'event_closed'
        assert any(n == 'chaos_event_close_confirmed' and d['pages'] == 1
                   for n, d in flow.runtime.deps.logger.events)


@pytest.mark.parametrize('network', [False, True])
def test_prayer_popup_closes_once_and_blocks_underlying_triangle(config, network):
    game, flow, _ = setup(config)

    def click(g, key):
        if key == 'event_prayer_room':
            g.state = 'prayer_popup'
        elif key == 'levelup_close' and network:
            g.network_remaining = 1

    game.on_click = click
    flow._event()
    assert [k for k in game.actions if k != 'network_retry'] == ['event_prayer_room', 'levelup_close', 'text_continue']
    assert game.state == 'map'
    assert game.actions.count('network_retry') == int(network)


def test_late_popup_interrupts_map_confirmation(config):
    game, flow, _ = setup(config)
    map_frames = 0
    injected = False

    def capture(g):
        nonlocal map_frames, injected
        if g.state == 'map' and not injected:
            map_frames += 1
            if map_frames == 2:
                injected = True
                g.destination = 'map'
                g.state = 'prayer_popup'

    game.on_capture = capture
    flow._event()
    assert injected and game.state == 'map'
    assert game.actions == ['event_prayer_room', 'text_continue', 'levelup_close']


@pytest.mark.parametrize('controls', [('levelup',), ('levelup_close',)])
def test_incomplete_popup_evidence_never_clicks_through(config, controls):
    game, flow, _ = setup(config)
    game.popup_controls = controls
    game.on_click = lambda g, k: setattr(g, 'state', 'prayer_popup') if k == 'event_prayer_room' else None
    with pytest.raises(StopExecution):
        flow._event()
    assert game.actions == ['event_prayer_room']


def test_persistent_popup_is_not_closed_twice(config):
    game, flow, _ = setup(config)
    game.close_delay = 999
    game.on_click = lambda g, k: setattr(g, 'state', 'prayer_popup') if k == 'event_prayer_room' else None
    with pytest.raises(StopExecution):
        flow._event()
    assert game.actions == ['event_prayer_room', 'levelup_close']


@pytest.mark.parametrize('guard', ['stop'])
def test_popup_wait_preserves_stop_guard(config, guard):
    game, flow, stop = setup(config)
    game.on_click = lambda g, k: setattr(g, 'state', 'prayer_popup') if k == 'event_prayer_room' else None

    def capture(g):
        if g.state == 'prayer_popup':
            if guard == 'stop':
                stop.request(StopReason.MANUAL_STOP)
            else:
                g.state = 'failed'

    game.on_capture = capture
    with pytest.raises(StopExecution) as error:
        flow._event()
    assert game.actions == ['event_prayer_room']
    if guard == 'failed':
        assert error.value.reason == StopReason.CHAOS_FAILED


@pytest.mark.parametrize('ranks', [
    {'xiaochun': 1, 'twins': 4, 'ivanna': 5, 'arowell': 2, 'teacher': 3},
    {'xiaochun': 5, 'twins': 5, 'ivanna': 5, 'arowell': 5},
])
def test_all_saved_heroes_rank_once_across_extra_text_and_recovery(config, ranks):
    game, flow, _ = setup(config, 'event_golden_patterns')
    flow.state.ranks = dict(ranks)

    def click(g, key):
        if key == 'text_continue' and g.actions.count(key) == 1:
            g.state = 'text'
            g.network_remaining = 1

    game.on_click = click
    flow._event()
    assert flow.state.ranks == {key: min(5, value + 1) for key, value in ranks.items()}
    assert [key for key in game.actions if key != 'network_retry'] == ['event_golden_patterns', 'text_continue', 'text_continue']
    changes = [d for n, d in flow.runtime.deps.logger.events if n == 'chaos_rank_changed']
    assert {d['hero'] for d in changes} == {h for h, r in ranks.items() if r < 5}
    assert len(changes) == sum(r < 5 for r in ranks.values())


def test_all_hero_ranks_wait_for_result_evidence(config, monkeypatch):
    game, flow, _ = setup(config, 'event_golden_patterns')
    original = game._visible
    monkeypatch.setattr(game, '_visible', lambda: [] if game.state == 'text' else original())
    before = dict(flow.state.ranks)
    with pytest.raises(StopExecution):
        flow._event()
    assert flow.state.ranks == before
    assert game.actions == ['event_golden_patterns']


def test_pond_requires_confirmed_reward_close(config):
    game, flow, _ = setup(config, 'event_clear_pond')
    game.on_click = lambda g, k: setattr(g, 'state', 'event_reward') if k == 'event_reward_close' else None
    with pytest.raises(StopExecution):
        flow._event()
    assert game.actions == ['event_clear_pond'] + ['event_reward_close'] * config.chaos.timing['event_close_max_attempts']
    assert flow.state.reward_stage != 'event_closed'
