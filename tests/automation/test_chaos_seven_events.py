import struct
import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


def setup(event='event_blood_shelter', rank=3, **kwargs):
    game=ChaosGame(chaos_config(),event_type=event,**kwargs);stop=StopController()
    flow=ChaosFlow(game.config,game.dependencies(),stop,
                   SnapshotPublisher(RuntimeSnapshot.chaos_run('seven-events',1),lambda _:None))
    flow.runtime.prepare();flow.state=RoundState(node_kind='event');game.state='event';game.kind='event'
    flow.state.ranks['xiaochun']=game.ranks['xiaochun']=rank
    return game,flow,stop


def changes(flow):
    return [fields for name,fields in flow.runtime.deps.logger.events if name=='chaos_rank_changed']


@pytest.mark.parametrize('event,x',[
    ('event_library',820),('event_flame',480),('event_torn_map',1510),
    ('event_mural',1850),('event_observatory',1510),('event_passage',1161),('event_blood_shelter',480)])
def test_new_routes_binding_direction_and_original_floor(event,x):
    game,flow,_=setup(event)
    scene=game.config.chaos.scenes[event]
    origin=game.config.chaos.event_recognition['templates'][event]['origin']
    width,_=struct.unpack('>II',game.config.template_paths[scene.template_key].read_bytes()[16:24])
    assert round(origin[0]+width*scene.action[0])+scene.action_offset[0]==x
    flow._event()
    middle=(['event_tablet_continue','unclaimed_confirm'] if event=='event_passage' else
            ['start_battle','enable_auto','event_rank_enter','hero_xiaochun','rank_confirm','continue_exploration'] if event=='event_blood_shelter' else [])
    assert game.actions==[event,*middle,'text_continue']
    assert game.state=='map' and game.floor==flow.state.floor==1
    assert 'event_recruit_enter' not in game.actions
    assert len(changes(flow))==int(event=='event_blood_shelter')


def test_done_alone_updates_rank_before_continue_is_visible(monkeypatch):
    game,flow,_=setup();visible=game._visible
    def without_continue():
        return ['event_battle_rank_done'] if game.state=='rank_result' else visible()
    monkeypatch.setattr(game,'_visible',without_continue)
    with pytest.raises(StopExecution):flow._event()
    assert flow.state.ranks['xiaochun']==4 and len(changes(flow))==1
    assert game.actions.count('rank_confirm')==1 and 'continue_exploration' not in game.actions


def test_continue_alone_never_confirms_rank(monkeypatch):
    game,flow,_=setup();visible=game._visible
    monkeypatch.setattr(game,'_visible',lambda:['continue_exploration'] if game.state=='rank_result' else visible())
    with pytest.raises(StopExecution):flow._event()
    assert flow.state.ranks['xiaochun']==3 and not changes(flow)
    assert 'continue_exploration' not in game.actions


@pytest.mark.parametrize('after',['rank_confirm','continue_exploration'])
def test_rank_network_recovery_records_and_submits_once(after):
    game,flow,_=setup(network_after=after);flow._event()
    assert flow.state.ranks['xiaochun']==4 and len(changes(flow))==1
    assert game.actions.count('rank_confirm')==game.actions.count('continue_exploration')==1
    assert game.state=='map'


def test_rank_priority_and_all_full_stop():
    game,flow,_=setup(rank=5);flow._event()
    assert flow.state.ranks['twins']==2 and changes(flow)[0]['hero']=='twins'
    game,flow,_=setup(rank=5);flow.state.ranks.update(twins=5, ivanna=5)
    with pytest.raises(StopExecution):flow._event()
    assert not game.actions and not changes(flow)


@pytest.mark.parametrize('key',['event_recruit_enter','unclaimed_prompt'])
def test_passage_requires_recruitment_then_confirmation_prompt(monkeypatch,key):
    game,flow,_=setup('event_passage');visible=game._visible
    monkeypatch.setattr(game,'_visible',lambda:[v for v in visible() if v!=key])
    with pytest.raises(StopExecution):flow._event()
    assert 'unclaimed_confirm' not in game.actions
    if key=='event_recruit_enter':assert 'event_tablet_continue' not in game.actions


@pytest.mark.parametrize('at,rank',[('rank_confirm',3),('continue_exploration',4)])
def test_rank_manual_stop_preserves_confirmed_record(at,rank):
    game,flow,stop=setup();game.on_click=lambda _,key:stop.request(StopReason.MANUAL_STOP) if key==at else None
    with pytest.raises(StopExecution):flow._event()
    assert flow.state.ranks['xiaochun']==rank and len(changes(flow))==int(rank==4)
    assert 'text_continue' not in game.actions
