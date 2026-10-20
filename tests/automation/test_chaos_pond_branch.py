"""Alternative first-choice pond states share one reward/return handler."""
import struct

import pytest

from e7auto.core.domain import RuntimeSnapshot
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController
from tests.helpers.chaos_game import ChaosGame, chaos_config


KEY = 'event_clear_pond_low_health'


@pytest.fixture(scope='module')
def configs():
    return {key: chaos_config(key) for key in ('dark_twins', 'budget')}


def setup(config, event, **kwargs):
    game = ChaosGame(config, event_type=event, **kwargs)
    flow = ChaosFlow(config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('pond-branch', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState.for_lineup(config.chaos.lineup)
    flow.state.floor = game.floor = 3
    flow.state.node_kind = game.state = game.kind = 'event'
    return game, flow


@pytest.mark.parametrize('lineup', ['dark_twins', 'budget'])
@pytest.mark.parametrize('event,target', [(KEY, (810, 1090)), ('event_clear_pond', (1510, 1090))])
def test_first_choice_variant_clicks_its_own_side_and_returns(configs, lineup, event, target):
    config = configs[lineup]
    game, flow = setup(config, event)
    spec = config.chaos.event_recognition['templates'][event]
    scene = config.chaos.scenes[event]
    size = struct.unpack('>II', config.template_paths[scene.template_key].read_bytes()[16:24])
    assert tuple(round(o + n * a) for o, n, a in zip(spec['origin'], size, scene.action)) == target
    assert HANDLERS[KEY] is HANDLERS['event_clear_pond']
    ranks = dict(flow.state.ranks)
    flow._event()
    assert game.actions == [event, 'event_reward_close', 'text_continue']
    assert game.state == 'map' and game.floor == flow.state.floor == 3
    assert flow.state.reward_stage == 'event_closed' and flow.state.ranks == ranks
    assert not flow.state.counted
    assert any(name == 'chaos_event_close_confirmed' and data['pages'] == 1
               for name, data in flow.runtime.deps.logger.events)


@pytest.mark.parametrize('network_after', [KEY, 'event_reward_close'])
def test_branch_recovery_does_not_repeat_choice_or_close(configs, network_after):
    game, flow = setup(configs['dark_twins'], KEY, network_after=network_after)
    flow._event()
    assert [k for k in game.actions if k != 'network_retry'] == [KEY, 'event_reward_close', 'text_continue']
    assert 'network_retry' in game.actions and game.state == 'map'
