"""Middle cage option: event battle, one shared reward page, original map."""
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
def configs():return {key:chaos_config(key) for key in ('dark_twins','budget')}


def setup(configs,lineup='dark_twins',**kwargs):
    game=ChaosGame(configs[lineup],event_type='event_broken_cage',event_reward_key=None,**kwargs)
    stop=StopController();flow=ChaosFlow(game.config,game.dependencies(),stop,SnapshotPublisher(RuntimeSnapshot.chaos_run('broken-cage',1),lambda _:None))
    flow.runtime.prepare();flow.state=RoundState.for_lineup(game.config.chaos.lineup)
    flow.state.floor=game.floor=2;flow.state.node_kind=game.state=game.kind='event'
    return game,flow,stop


@pytest.mark.parametrize('lineup',['dark_twins','budget'])
def test_middle_binding_battle_and_one_page_close(configs,lineup,monkeypatch):
    game,flow,_=setup(configs,lineup);key='event_broken_cage';scene=flow.settings.scenes[key]
    assert key in HANDLERS and scene.template_key=='chaos:'+key
    spec=flow.settings.event_recognition['templates'][key];assert spec['layout']==3
    size=struct.unpack('>II',game.config.template_paths[scene.template_key].read_bytes()[16:24])
    assert tuple(round(o+n*a) for o,n,a in zip(spec['origin'],size,scene.action))==(1161,1090)
    original=game.control
    def control(frame,key,region=None):
        assert not key.startswith('loot_') and key not in ('levelup','levelup_close')
        return original(frame,key,region)
    monkeypatch.setattr(game,'control',control)
    flow._event()
    assert [k for k in game.actions if k!='enable_auto']==['event_broken_cage','start_battle','event_reward_close','text_continue']
    assert game.state=='map' and game.floor==flow.state.floor==2 and not flow.state.counted
    assert flow.state.reward_stage=='event_closed'
    closes=[d for k,d in flow.runtime.deps.logger.events if k=='chaos_event_close_confirmed']
    assert len(closes)==1 and closes[0]['pages']==1


@pytest.mark.parametrize('after',['event_broken_cage','start_battle','event_reward_close'])
def test_network_recovery_never_replays_completed_steps(configs,after):
    game,flow,_=setup(configs,network_after=after);flow._event()
    for key in ('event_broken_cage','start_battle','event_reward_close','text_continue'):assert game.actions.count(key)==1
    assert game.actions.count('network_retry')==4 and game.state=='map'


def test_failed_event_battle_never_closes_rewards_or_advances(configs):
    game,flow,_=setup(configs,fail_battle=True)
    with pytest.raises(BattleDefeated):flow._event()
    assert 'event_reward_close' not in game.actions and 'text_continue' not in game.actions
    assert flow.state.reward_stage!='event_closed'


def test_manual_stop_after_battle_start_sends_no_later_input(configs):
    game,flow,stop=setup(configs);cut=[]
    def clicked(g,k):
        if k=='start_battle':cut.append(len(g.actions));stop.request(StopReason.MANUAL_STOP)
    game.on_click=clicked
    with pytest.raises(StopExecution):flow._event()
    assert len(game.actions)==cut[0]


def test_wrong_floor_cannot_complete_event(configs):
    game,flow,_=setup(configs)
    game.on_click=lambda g,k:setattr(g,'floor',3) if k=='text_continue' else None
    with pytest.raises(StopExecution):flow._event()
    assert not flow.state.counted and flow.state.floor==2
