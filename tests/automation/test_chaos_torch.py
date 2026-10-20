"""Torch experience may show an upgrade during text or the return to the map."""
import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


class TorchGame(ChaosGame):
    close_delay = .6
    closed_at = None
    destination = 'text'
    popup_controls = ('levelup', 'levelup_close')

    def _visible(self):
        if self.state in ('torch_popup', 'torch_closing'):
            return [*self.popup_controls, 'text_continue']
        return super()._visible()

    def capture_client(self, window, bounds):
        if self.state == 'torch_closing' and self.clock.now - self.closed_at >= self.close_delay:
            self.state = self.destination
        return super().capture_client(window, bounds)

    def click(self, window, point):
        key = self.points.get(point)
        if not self.network_remaining and key == 'levelup_close':
            assert self.state == 'torch_popup'
            self.actions.append(key)
            self.state, self.closed_at = 'torch_closing', self.clock.now
            if self.on_click:
                self.on_click(self, key)
            return
        if not self.network_remaining and key == 'text_continue':
            assert self.state not in ('torch_popup', 'torch_closing')
        super().click(window, point)


@pytest.fixture(scope='module')
def config():
    return chaos_config()


def setup(config):
    game = TorchGame(config, event_type='event_torch')
    stop = StopController()
    flow = ChaosFlow(config, game.dependencies(), stop,
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('torch', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState(floor=2, node_kind='event')
    game.state = game.kind = 'event'
    game.floor = 2
    return game, flow, stop


def test_no_upgrade_keeps_text_and_original_floor_return(config):
    game, flow, _ = setup(config)
    flow._event()
    assert game.actions == ['event_torch', 'text_continue']
    assert game.state == 'map' and game.floor == flow.state.floor == 2


@pytest.mark.parametrize('appearance', ['choice', 'text', 'map_confirmation'])
@pytest.mark.parametrize('network', [False, True])
def test_upgrade_closes_once_and_blocks_underlying_text(config, appearance, network):
    game, flow, _ = setup(config)
    map_frames = 0
    injected = False

    def click(g, key):
        nonlocal injected
        if ((appearance == 'choice' and key == 'event_torch')
                or (appearance == 'text' and key == 'text_continue' and not injected)):
            injected = True
            g.destination = 'text' if appearance == 'choice' else 'map'
            g.state = 'torch_popup'
        if key == 'levelup_close' and network:
            g.network_remaining = 1

    def capture(g):
        nonlocal injected, map_frames
        if appearance == 'map_confirmation' and g.state == 'map' and not injected:
            map_frames += 1
            if map_frames == 2:
                injected = True
                g.destination, g.state = 'map', 'torch_popup'

    game.on_click, game.on_capture = click, capture
    flow._event()
    steps = ['levelup_close', 'text_continue'] if appearance == 'choice' else ['text_continue', 'levelup_close']
    assert injected and [key for key in game.actions if key != 'network_retry'] == ['event_torch', *steps]
    assert game.actions.count('network_retry') == int(network)
    assert game.state == 'map' and game.floor == flow.state.floor == 2
    assert not flow.state.counted


@pytest.mark.parametrize('controls', [('levelup',), ('levelup_close',)])
def test_incomplete_popup_never_authorizes_close_or_text(config, controls):
    game, flow, _ = setup(config)
    game.popup_controls = controls
    game.on_click = lambda g, key: setattr(g, 'state', 'torch_popup') if key == 'event_torch' else None
    with pytest.raises(StopExecution) as error:
        flow._event()
    assert error.value.reason is StopReason.CHAOS_UNKNOWN_CONTENT
    assert game.actions == ['event_torch']


def test_persistent_popup_is_not_closed_twice(config):
    game, flow, _ = setup(config)
    game.close_delay = 999
    game.on_click = lambda g, key: setattr(g, 'state', 'torch_popup') if key == 'event_torch' else None
    with pytest.raises(StopExecution):
        flow._event()
    assert game.actions == ['event_torch', 'levelup_close']


@pytest.mark.parametrize('reason', [StopReason.MANUAL_STOP])
def test_popup_wait_preserves_stop_guards(config, reason):
    game, flow, stop = setup(config)
    game.on_click = lambda g, key: setattr(g, 'state', 'torch_popup') if key == 'event_torch' else None
    def capture(g):
        if g.state == 'torch_popup':
            if reason is StopReason.MANUAL_STOP:
                stop.request(reason)
            else:
                g.state = 'failed'
    game.on_capture = capture
    with pytest.raises(StopExecution) as error:
        flow._event()
    assert error.value.reason is reason
    assert game.actions == ['event_torch']


def test_wrong_floor_cannot_finish_torch_event(config):
    game, flow, _ = setup(config)
    game.on_click = lambda g, key: setattr(g, 'floor', 3) if key == 'text_continue' else None
    with pytest.raises(StopExecution):
        flow._event()
    assert flow.state.floor == 2 and not flow.state.counted
