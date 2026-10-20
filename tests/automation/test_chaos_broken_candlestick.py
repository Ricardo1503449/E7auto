"""Broken candlestick bindings and its optional level-up branch."""
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


class CandlestickGame(ChaosGame):
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
    game = CandlestickGame(config, event_type=event, **kwargs)
    stop = StopController()
    flow = ChaosFlow(config, game.dependencies(), stop,
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('september26', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState(floor=2, node_kind='event')
    game.kind = game.state = 'event'
    game.floor = 2
    return game, flow, stop


def test_registered_choice_binding(config):
    key='event_broken_candlestick'
    scene=config.chaos.scenes[key]
    assert scene.template_key=='chaos:'+key
    assert key in HANDLERS and key in config.chaos.event_recognition['templates']
    width,height=struct.unpack('>II',config.template_paths[scene.template_key].read_bytes()[16:24])
    assert tuple(round(o+size*a) for o,size,a in zip((217,1069),(width,height),scene.action))==(1850,1090)


@pytest.mark.parametrize('event', ['event_broken_candlestick'])
def test_simple_routes_and_candlestick_without_levelup(config, event):
    game, flow, _ = setup(config, event)
    flow._event()
    assert game.actions == [event, 'text_continue']
    assert game.state == 'map' and game.floor == flow.state.floor == 2
    assert not flow.state.counted


@pytest.mark.parametrize('network', [False, True])
def test_candlestick_levelup_blocks_text_and_closes_once(config, network):
    game, flow, _ = setup(config, 'event_broken_candlestick')

    def click(g, key):
        if key == 'event_broken_candlestick':g.state = 'levelup'
        elif key == 'levelup_close' and network:g.network_remaining = 1

    game.on_click = click
    flow._event()
    assert [k for k in game.actions if k != 'network_retry'] == ['event_broken_candlestick', 'levelup_close', 'text_continue']
    assert game.state == 'map'


def test_candlestick_late_levelup_before_stable_map(config):
    game, flow, _ = setup(config, 'event_broken_candlestick')
    injected = False

    def capture(g):
        nonlocal injected
        if g.state == 'map' and not injected:
            injected = True
            g.state, g.destination = 'levelup', 'map'

    game.on_capture = capture
    flow._event()
    assert injected and game.actions == ['event_broken_candlestick', 'text_continue', 'levelup_close']


def test_candlestick_stuck_levelup_is_not_closed_repeatedly(config):
    game, flow, _ = setup(config, 'event_broken_candlestick')
    game.close_delay = 999
    game.on_click = lambda g, k: setattr(g, 'state', 'levelup') if k == 'event_broken_candlestick' else None
    with pytest.raises(StopExecution):flow._event()
    assert game.actions == ['event_broken_candlestick', 'levelup_close']
