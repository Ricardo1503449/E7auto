"""Bone throne opts into existing single-rank promotion and confirmed closing."""
import struct
import pytest
from e7auto.core.domain import RuntimeSnapshot
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


@pytest.fixture(scope='module')
def configs():return {key:chaos_config(key) for key in ('dark_twins','budget')}


def setup(configs,lineup='dark_twins',**kwargs):
    game=ChaosGame(configs[lineup],event_type='event_bone_throne',**kwargs)
    flow=ChaosFlow(game.config,game.dependencies(),StopController(),SnapshotPublisher(RuntimeSnapshot.chaos_run('bone-throne',1),lambda _:None))
    flow.runtime.prepare();flow.state=RoundState.for_lineup(game.config.chaos.lineup)
    flow.state.floor=game.floor=2;flow.state.node_kind=game.state=game.kind='event'
    flow.state.ranks['xiaochun']=game.ranks['xiaochun']=3
    return game,flow


def changes(flow):return [d for k,d in flow.runtime.deps.logger.events if k=='chaos_rank_changed']


@pytest.mark.parametrize('lineup',['dark_twins','budget'])
def test_right_binding_promotes_one_rank_closes_and_returns(configs,lineup):
    game,flow=setup(configs,lineup);key='event_bone_throne';scene=flow.settings.scenes[key];spec=flow.settings.event_recognition['templates'][key]
    assert key in HANDLERS and scene.template_key=='chaos:'+key and spec['layout']==3
    size=struct.unpack('>II',game.config.template_paths[scene.template_key].read_bytes()[16:24])
    assert tuple(round(o+n*a) for o,n,a in zip(spec['origin'],size,scene.action))==(1850,1090)
    flow._event()
    assert game.actions==[key,'event_rank_enter','hero_xiaochun','rank_confirm','rank_close','text_continue']
    assert flow.state.ranks['xiaochun']==4
    assert changes(flow)==[dict(hero='xiaochun',rank_before=3,rank_after=4,node_kind='event')]
    assert game.state=='map' and game.floor==flow.state.floor==2 and not flow.state.counted
    closes=[d for k,d in flow.runtime.deps.logger.events if k=='chaos_event_close_confirmed']
    assert len(closes)==1 and closes[0]['control']=='rank_close' and closes[0]['pages']==1


@pytest.mark.parametrize('lineup',['dark_twins','budget'])
def test_maxed_first_priority_uses_lineups_next_hero(configs,lineup):
    game,flow=setup(configs,lineup);flow.state.ranks['xiaochun']=game.ranks['xiaochun']=5
    hero=flow.state.rank_priority[1];flow._event()
    assert 'hero_'+hero in game.actions and 'hero_xiaochun' not in game.actions
    assert flow.state.ranks[hero]==2 and len(changes(flow))==1


@pytest.mark.parametrize('lineup',['dark_twins','budget'])
def test_no_eligible_hero_stops_before_spending_shards(configs,lineup):
    game,flow=setup(configs,lineup)
    for hero in flow.state.rank_priority:flow.state.ranks[hero]=5
    with pytest.raises(StopExecution):flow._event()
    assert game.actions==[]


@pytest.mark.parametrize('after',['rank_confirm','rank_close'])
def test_network_recovery_does_not_repeat_promotion(configs,after):
    game,flow=setup(configs,network_after=after);flow._event()
    assert game.actions.count('rank_confirm')==game.actions.count('rank_close')==1
    assert flow.state.ranks['xiaochun']==4 and len(changes(flow))==1 and game.state=='map'
