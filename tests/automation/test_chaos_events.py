from dataclasses import replace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.types import Point, Rect
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


def event_flow(event='event_tablet',intro_pages=0,**kwargs):
    game=ChaosGame(chaos_config(),event_type=event,event_intro_pages=intro_pages,**kwargs)
    stop=StopController()
    flow=ChaosFlow(game.config,game.dependencies(),stop,SnapshotPublisher(RuntimeSnapshot.chaos_run('events',1),lambda _:None))
    flow.runtime.prepare();flow.state=RoundState(node_kind='event')
    game.kind='event';game.state='event_intro' if intro_pages else 'event'
    return game,flow,stop


@pytest.mark.parametrize('event',['event_torch','event_mimic','event_trap','event_tablet','event_vestment','event_golem','event_giant_eye','event_prison','event_cage','event_altar'])
@pytest.mark.parametrize('intro_pages',[0,2])
def test_entry_text_routes_each_event_once_and_handler_returns_to_same_map(event,intro_pages):
    game,flow,_=event_flow(event,intro_pages);flow._event()
    assert game.actions[:intro_pages]==['text_continue']*intro_pages
    assert game.actions[intro_pages]==event
    assert game.actions.count(event)==1
    assert game.actions.count('text_continue')==intro_pages+1
    assert game.state=='map' and game.floor==flow.state.floor==1
    assert not flow.state.counted and flow.runtime.publisher.snapshot.rounds_completed==0
    if event=='event_tablet':
        assert game.actions[intro_pages:]==['event_tablet','event_tablet_continue','unclaimed_confirm','text_continue']
        assert not game.recruited
    if event not in ('event_trap','event_giant_eye','event_prison','event_cage'):assert 'start_battle' not in game.actions


@pytest.mark.parametrize('after',['text_continue','event_tablet','event_tablet_continue','unclaimed_confirm'])
def test_tablet_network_recovery_does_not_replay_completed_steps(after):
    game,flow,_=event_flow(intro_pages=1,network_after=after);flow._event()
    assert game.actions.count('network_retry')==4
    for key in ['event_tablet','event_tablet_continue','unclaimed_confirm']:assert game.actions.count(key)==1
    assert game.actions.count('text_continue')==2 and game.state=='map'


def test_first_verified_event_is_not_blocked_by_full_catalog_uniqueness_scan(monkeypatch):
    game,flow,_=event_flow();original=game._visible
    monkeypatch.setattr(game,'_visible',lambda:['event_tablet','event_trap'] if game.state=='event' else original())
    flow._event()
    assert game.actions[0]=='event_tablet' and 'event_trap' not in game.actions
    assert game.state=='map'


def test_unknown_event_after_intro_is_not_dispatched(monkeypatch):
    game,flow,_=event_flow(intro_pages=1);original=game._visible
    monkeypatch.setattr(game,'_visible',lambda:[] if game.state=='event' else original())
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.CHAOS_UNKNOWN_CONTENT
    assert game.actions==['text_continue']


def test_selected_handler_keeps_control_when_other_event_template_appears(monkeypatch):
    game,flow,_=event_flow();original=game._visible
    monkeypatch.setattr(game,'_visible',lambda:original()+(['event_trap'] if game.state in ('event_ticket','event_unclaimed','text') else []))
    flow._event()
    assert game.actions==['event_tablet','event_tablet_continue','unclaimed_confirm','text_continue']


def test_tablet_confirmation_requires_unclaimed_reward_prompt(monkeypatch):
    game,flow,_=event_flow();original=game._visible
    monkeypatch.setattr(game,'_visible',lambda:[k for k in original() if k!='unclaimed_prompt'])
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.RECOGNITION_TIMEOUT
    assert game.actions==['event_tablet','event_tablet_continue']


@pytest.mark.parametrize('at',['text_continue','event_tablet_continue'])
def test_manual_stop_at_intro_or_tablet_transition_sends_no_further_input(at):
    game,flow,stop=event_flow(intro_pages=1);cut=[]
    def clicked(g,key):
        if key==at and not cut:cut.append(len(g.actions));stop.request(StopReason.MANUAL_STOP)
    game.on_click=clicked
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.MANUAL_STOP and len(game.actions)==cut[0]


@pytest.mark.parametrize('event',['event_tablet','event_golem','event_cage','event_altar'])
def test_tablet_final_text_requires_original_floor(monkeypatch,event):
    game,flow,_=event_flow(event);original=game.map_view
    monkeypatch.setattr(game,'map_view',lambda frame:replace(view,floor=2) if (view:=original(frame)) else None)
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.CHAOS_UNKNOWN_CONTENT and flow.state.floor==1


@pytest.mark.parametrize('event',['event_golem','event_altar'])
def test_golem_recovery_does_not_repeat_right_option(event):
    game,flow,_=event_flow(event,intro_pages=1,network_after=event)
    flow._event()
    assert game.actions.count(event)==1
    assert game.actions.count('text_continue')==2
    assert game.actions.count('network_retry')==4 and game.state=='map'


def test_bouncing_text_is_clicked_from_one_frame_and_options_still_wait_three(monkeypatch):
    game,flow,_=event_flow('event_golem',intro_pages=2)
    original=game.control;seen=[];option_frames=[]
    def control(frame,key,region=None):
        hit=original(frame,key,region)
        if hit and key=='text_continue':
            y=1260+game.captures%4*5
            hit=replace(hit,bounds=Rect(1148,y,23,16),anchor=Point(1159,y+8))
            game.points[hit.anchor]=key;seen.append((frame['state'],game.captures,hit.anchor))
        if hit and key=='event_golem':option_frames.append(game.captures)
        return hit
    monkeypatch.setattr(game,'control',control)
    flow._event()
    assert [s[0] for s in seen]==['event_intro','event_intro','text']
    assert len(option_frames)==3
    assert game.actions==['text_continue','text_continue','event_golem','text_continue']
    assert game.state=='map'
