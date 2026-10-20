"""Two initial torn-map wordings use the same right-side exit route."""
import struct

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


KEY = 'event_torn_map_path'


@pytest.fixture(scope='module')
def configs():
    return {lineup: chaos_config(lineup) for lineup in ('dark_twins', 'budget')}


def setup(config, event=KEY, **kwargs):
    game = ChaosGame(config, event_type=event, **kwargs)
    stop = StopController()
    flow = ChaosFlow(config, game.dependencies(), stop,
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('torn-map-branch', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState.for_lineup(config.chaos.lineup)
    flow.state.floor = game.floor = 2
    flow.state.node_kind = game.state = game.kind = 'event'
    return game, flow, stop


@pytest.mark.parametrize('lineup', ['dark_twins', 'budget'])
@pytest.mark.parametrize('event', ['event_torn_map', KEY])
def test_initial_wordings_share_right_exit_and_original_floor(configs, lineup, event):
    config = configs[lineup]
    game, flow, _ = setup(config, event)
    spec = config.chaos.event_recognition['templates'][event]
    scene = config.chaos.scenes[event]
    size = struct.unpack('>II', config.template_paths[scene.template_key].read_bytes()[16:24])
    assert tuple(round(o+n*a) for o, n, a in zip(spec['origin'], size, scene.action)) == (1510, 1110)
    assert HANDLERS[KEY] is HANDLERS['event_torn_map']
    assert scene.threshold == (.92 if event == KEY else .96)
    assert scene.structure == .8
    ranks = dict(flow.state.ranks)
    flow._event()
    assert game.actions == [event, 'text_continue']
    assert game.state == 'map' and game.floor == flow.state.floor == 2
    assert flow.state.ranks == ranks and not flow.state.counted


@pytest.mark.parametrize('network_after', [KEY, 'text_continue'])
def test_recovery_does_not_replay_the_choice(configs, network_after):
    game, flow, _ = setup(configs['dark_twins'], network_after=network_after)
    flow._event()
    assert [k for k in game.actions if k != 'network_retry'] == [KEY, 'text_continue']
    assert 'network_retry' in game.actions and game.state == 'map'


def test_stop_after_choice_does_not_advance_text(configs):
    game, flow, stop = setup(configs['dark_twins'])
    game.on_click = lambda _, key: stop.request(StopReason.MANUAL_STOP) if key == KEY else None
    with pytest.raises(StopExecution):
        flow._event()
    assert game.actions == [KEY]
