"""New bindings, event-local level-up and the armory's two distinct battles."""
import struct

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import BattleDefeated, ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


@pytest.fixture(scope='module')
def config():
    return chaos_config()


class FootprintsGame(ChaosGame):
    close_delay = .6
    closed_at = None
    destination = 'text'

    def _visible(self):
        if self.state in ('levelup', 'levelup_closing'):
            return ['levelup', 'levelup_close', 'text_continue']
        return super()._visible()

    def capture_client(self, window, bounds):
        if self.state == 'levelup_closing' and self.clock.now - self.closed_at >= self.close_delay:
            self.state = self.destination
        return super().capture_client(window, bounds)

    def click(self, window, point):
        key = self.points.get(point)
        if not self.network_remaining and key == 'levelup_close':
            assert self.state == 'levelup'
            self.actions.append(key)
            self.state, self.closed_at = 'levelup_closing', self.clock.now
            if self.on_click:self.on_click(self, key)
            return
        if not self.network_remaining and key == 'text_continue':
            assert self.state not in ('levelup', 'levelup_closing')
        super().click(window, point)


def setup(config, event, **kwargs):
    game = FootprintsGame(config, event_type=event, **kwargs)
    stop = StopController()
    flow = ChaosFlow(config, game.dependencies(), stop,
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('september26', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState(floor=2, node_kind='event')
    game.kind = game.state = 'event'
    game.floor = 2
    return game, flow, stop


@pytest.mark.parametrize('key,origin,target,entry', [
    ('event_warning_sign', (174, 1069), (1850, 1090), True),
    ('event_mysterious_footprints', (302, 1069), (1850, 1090), True),
    ('event_old_armory', (287, 1069), (1161, 1090), True),
    ('event_old_armory_left', (632, 1069), (810, 1090), False),
    ('event_blood_circle', (285, 1069), (1161, 1090), True),
])
def test_registered_choice_binding(config, key, origin, target, entry):
    scene = config.chaos.scenes[key]
    assert scene.template_key == 'chaos:' + key
    assert (key in HANDLERS) == entry
    assert (key in config.chaos.event_recognition['templates']) == entry
    width, height = struct.unpack('>II', config.template_paths[scene.template_key].read_bytes()[16:24])
    assert tuple(round(o + size * a) for o, size, a in zip(origin, (width, height), scene.action)) == target


@pytest.mark.parametrize('event', ['event_warning_sign', 'event_blood_circle', 'event_mysterious_footprints'])
def test_simple_routes_and_footprints_without_levelup(config, event):
    game, flow, _ = setup(config, event)
    flow._event()
    assert game.actions == [event, 'text_continue']
    assert game.state == 'map' and game.floor == flow.state.floor == 2
    assert not flow.state.counted


@pytest.mark.parametrize('network', [False, True])
def test_footprints_levelup_blocks_text_and_closes_once(config, network):
    game, flow, _ = setup(config, 'event_mysterious_footprints')

    def click(g, key):
        if key == 'event_mysterious_footprints':g.state = 'levelup'
        elif key == 'levelup_close' and network:g.network_remaining = 1

    game.on_click = click
    flow._event()
    assert [k for k in game.actions if k != 'network_retry'] == ['event_mysterious_footprints', 'levelup_close', 'text_continue']
    assert game.state == 'map'


def test_footprints_late_levelup_before_stable_map(config):
    game, flow, _ = setup(config, 'event_mysterious_footprints')
    injected = False

    def capture(g):
        nonlocal injected
        if g.state == 'map' and not injected:
            injected = True
            g.state, g.destination = 'levelup', 'map'

    game.on_capture = capture
    flow._event()
    assert injected and game.actions == ['event_mysterious_footprints', 'text_continue', 'levelup_close']


def test_footprints_stuck_levelup_is_not_closed_repeatedly(config):
    game, flow, _ = setup(config, 'event_mysterious_footprints')
    game.close_delay = 999
    game.on_click = lambda g, k: setattr(g, 'state', 'levelup') if k == 'event_mysterious_footprints' else None
    with pytest.raises(StopExecution):flow._event()
    assert game.actions == ['event_mysterious_footprints', 'levelup_close']


@pytest.mark.parametrize('after', [None, 'event_old_armory', 'event_reward_close', 'event_old_armory_left'])
def test_armory_two_battles_then_original_map_without_replaying_steps(config, after, monkeypatch):
    game, flow, _ = setup(config, 'event_old_armory', network_after=after)
    original = game.control

    def control(frame, key, region=None):
        assert not key.startswith('loot_'), 'direct event rewards are not loot-selection screens'
        return original(frame, key, region)

    monkeypatch.setattr(game, 'control', control)
    flow._event()
    assert [k for k in game.actions if k not in ('network_retry', 'enable_auto')] == [
        'event_old_armory', 'start_battle', 'event_reward_close', 'event_old_armory_left',
        'start_battle', 'event_reward_close', 'text_continue']
    assert game.state == 'map' and game.floor == flow.state.floor == 2
    assert game.node_index == 1 and flow.state.reward_stage == 'event_closed'
    closes = [d for n, d in flow.runtime.deps.logger.events if n == 'chaos_event_close_confirmed']
    assert [(d['pages'], d['outcome']) for d in closes] == [(1, 'next_page'), (1, 'text')]


def test_armory_second_template_is_only_queried_after_first_reward_close(config, monkeypatch):
    game, flow, _ = setup(config, 'event_old_armory')
    original = game.control
    queries = []

    def control(frame, key, region=None):
        if key == 'event_old_armory_left':
            queries.append(frame['state'])
            assert frame['state'] == 'armory_subchoice'
            assert game.actions.count('start_battle') == 1
            assert game.actions.count('event_reward_close') == 1
            assert 'event_old_armory_left' not in game.actions
        return original(frame, key, region)

    monkeypatch.setattr(game, 'control', control)
    flow._event()
    assert len(queries) >= config.chaos.timing['stable_frames']
    assert game.actions.count('event_old_armory_left') == 1
    assert game.state == 'map'


@pytest.mark.parametrize('destination', ['map', 'text', 'event'])
def test_armory_first_reward_requires_its_second_options(config, destination):
    game, flow, _ = setup(config, 'event_old_armory')
    game.on_click = lambda g, k: setattr(g, 'state', destination) if k == 'event_reward_close' else None
    with pytest.raises(StopExecution):flow._event()
    assert game.actions.count('start_battle') == 1
    assert 'event_old_armory_left' not in game.actions and 'text_continue' not in game.actions
    assert flow.state.reward_stage != 'event_closed'


def test_armory_visible_card_blocks_second_options_and_only_retries_close(config, monkeypatch):
    game, flow, _ = setup(config, 'event_old_armory')
    original = game._visible
    monkeypatch.setattr(game, '_visible', lambda: ['event_reward_close', 'event_old_armory_left'] if game.state == 'event_reward' else original())
    game.on_click = lambda g, k: setattr(g, 'state', 'event_reward') if k == 'event_reward_close' else None
    with pytest.raises(StopExecution):flow._event()
    assert game.actions.count('event_reward_close') == config.chaos.timing['event_close_max_attempts']
    assert 'event_old_armory_left' not in game.actions and game.actions.count('start_battle') == 1


def test_armory_second_page_requires_consecutive_evidence(config, monkeypatch):
    game, flow, _ = setup(config, 'event_old_armory')
    original = game.control
    seen = 0

    def control(frame, key, region=None):
        nonlocal seen
        if key == 'event_old_armory_left' and frame['state'] == 'armory_subchoice':
            seen += 1
            if seen % 2 == 0:return None
        return original(frame, key, region)

    monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution):flow._event()
    assert seen > 3 and 'event_old_armory_left' not in game.actions
    assert flow.state.reward_stage != 'event_closed'


@pytest.mark.parametrize('at', ['event_old_armory', 'event_reward_close', 'event_old_armory_left'])
def test_armory_stop_preserves_current_stage(config, at):
    game, flow, stop = setup(config, 'event_old_armory')
    cut = []

    def click(g, key):
        if key == at:
            cut.append(len(g.actions))
            stop.request(StopReason.MANUAL_STOP)

    game.on_click = click
    with pytest.raises(StopExecution):flow._event()
    assert len(game.actions) == cut[0]


@pytest.mark.parametrize('second', [False, True])
def test_armory_battle_failure_cannot_continue_event(config, second):
    game, flow, _ = setup(config, 'event_old_armory', fail_battle=not second)
    if second:game.on_click = lambda g, k: setattr(g, 'fail_battle', True) if k == 'event_old_armory_left' else None
    with pytest.raises(BattleDefeated):flow._event()
    assert game.actions.count('start_battle') == (2 if second else 1)
    assert game.actions.count('event_reward_close') == int(second)
    assert 'text_continue' not in game.actions


def test_second_armory_options_are_not_a_global_entry(config):
    game, flow, _ = setup(config, 'event_old_armory')
    game.state, game.armory_phase = 'armory_subchoice', 1
    with pytest.raises(StopExecution):flow._event()
    assert game.actions == []
