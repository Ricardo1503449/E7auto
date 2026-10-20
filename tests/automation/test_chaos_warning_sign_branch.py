"""Disabled-middle first-choice variant uses the warning-sign right exit."""
import struct

import pytest

from e7auto.core.domain import RuntimeSnapshot
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController
from tests.helpers.chaos_game import ChaosGame, chaos_config


KEY = 'event_warning_sign_low_health'


@pytest.fixture(scope='module')
def configs():
    return {key: chaos_config(key) for key in ('dark_twins', 'budget')}


def setup(config, **kwargs):
    game = ChaosGame(config, event_type=KEY, **kwargs)
    flow = ChaosFlow(config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('warning-sign-branch', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState.for_lineup(config.chaos.lineup)
    flow.state.floor = game.floor = 3
    flow.state.node_kind = game.state = game.kind = 'event'
    return game, flow


@pytest.mark.parametrize('lineup', ['dark_twins', 'budget'])
def test_first_choice_variant_uses_right_exit_and_same_floor(configs, lineup):
    config = configs[lineup]
    game, flow = setup(config)
    spec = config.chaos.event_recognition['templates'][KEY]
    scene = config.chaos.scenes[KEY]
    size = struct.unpack('>II', config.template_paths[scene.template_key].read_bytes()[16:24])
    assert tuple(round(o + n * a) for o, n, a in zip(spec['origin'], size, scene.action)) == (1850, 1090)
    assert spec['layout'] == 3 and spec['weights'] == [1., 0., 1.]
    assert HANDLERS[KEY] is HANDLERS['event_warning_sign']
    ranks = dict(flow.state.ranks)
    flow._event()
    assert game.actions == [KEY, 'text_continue']
    assert game.state == 'map' and game.floor == flow.state.floor == 3
    assert flow.state.ranks == ranks and not flow.state.counted


def test_recovery_does_not_repeat_event_choice(configs):
    game, flow = setup(configs['dark_twins'], network_after=KEY)
    flow._event()
    assert [key for key in game.actions if key != 'network_retry'] == [KEY, 'text_continue']
    assert 'network_retry' in game.actions and game.state == 'map'
