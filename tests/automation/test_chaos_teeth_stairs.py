"""Two right-side exits bind to the shared text/original-map finishing path."""
import struct
import pytest
from e7auto.core.domain import RuntimeSnapshot
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController
from tests.helpers.chaos_game import ChaosGame, chaos_config


EVENTS=('event_sharp_teeth','event_collapsed_stairs')


@pytest.fixture(scope='module')
def configs():return {key:chaos_config(key) for key in ('dark_twins','budget')}


def setup(configs,event,lineup='dark_twins',**kwargs):
    game=ChaosGame(configs[lineup],event_type=event,**kwargs)
    flow=ChaosFlow(game.config,game.dependencies(),StopController(),SnapshotPublisher(RuntimeSnapshot.chaos_run('teeth-stairs',1),lambda _:None))
    flow.runtime.prepare();flow.state=RoundState.for_lineup(game.config.chaos.lineup)
    flow.state.floor=game.floor=2 if event=='event_sharp_teeth' else 3
    flow.state.node_kind=game.state=game.kind='event'
    return game,flow


@pytest.mark.parametrize('event',EVENTS)
@pytest.mark.parametrize('lineup',['dark_twins','budget'])
def test_registered_right_exit_returns_to_original_map(configs,event,lineup):
    game,flow=setup(configs,event,lineup);scene=flow.settings.scenes[event];spec=flow.settings.event_recognition['templates'][event]
    assert event in HANDLERS and scene.template_key=='chaos:'+event and spec['layout']==2
    size=struct.unpack('>II',game.config.template_paths[scene.template_key].read_bytes()[16:24])
    assert tuple(round(o+n*a) for o,n,a in zip(spec['origin'],size,scene.action))==(1510,1090)
    ranks=dict(flow.state.ranks);floor=flow.state.floor
    flow._event()
    assert game.actions==[event,'text_continue'] and game.state=='map'
    assert game.floor==flow.state.floor==floor and not flow.state.counted
    assert flow.state.ranks==ranks


@pytest.mark.parametrize('event',EVENTS)
def test_recovery_after_choice_does_not_repeat_selection(configs,event):
    game,flow=setup(configs,event,network_after=event);flow._event()
    assert [k for k in game.actions if k!='network_retry']==[event,'text_continue']
    assert game.actions.count('network_retry')==4 and game.state=='map'
