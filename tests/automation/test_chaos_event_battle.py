from dataclasses import replace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import BattleDefeated, ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


def event_flow(*, timeout=None, pending_teacher=False, event_type='event_trap', **kwargs):
    config=chaos_config()
    if timeout is not None:
        config=replace(config,chaos=replace(config.chaos,timing=dict(config.chaos.timing,battle_timeout_ms=timeout)))
    game=ChaosGame(config,event_type=event_type,**kwargs)
    stop=StopController();publisher=SnapshotPublisher(RuntimeSnapshot.chaos_run('event',1),lambda _:None)
    flow=ChaosFlow(config,game.dependencies(),stop,publisher)
    flow.runtime.prepare();flow.state=RoundState(node_kind='event',pending_teacher=pending_teacher)
    game.state='event';game.kind='event'
    if pending_teacher:
        game.recruited.add('teacher');flow.state.ranks['teacher']=1
    return game,flow,stop


def assert_no_normal_settlement(game):
    forbidden={'continue_exploration','levelup_close','loot_confirm','elite_choose_reward',
               'boss_choose_reward','recruit_confirm','ticket_all','journey_close'}
    assert not forbidden.intersection(game.actions)


@pytest.mark.parametrize('reward',['loot_summon_mark','loot_old_sword',None])
def test_event_direct_reward_closes_then_advances_text_without_selecting_or_changing_floor(reward,monkeypatch):
    game,flow,_=event_flow(event_reward_key=reward)
    original=game.control
    def control(frame,key,region=None):
        assert not key.startswith('loot_'), 'Directly granted rewards must not identify loot items'
        return original(frame,key,region)
    monkeypatch.setattr(game,'control',control)
    flow._event()
    assert game.actions==['event_trap','start_battle','enable_auto','event_reward_close','text_continue']
    assert game.state=='map' and game.floor==flow.state.floor==1
    assert flow.state.reward_stage=='event_closed' and not flow.state.counted
    assert flow.state.ranks==dict.fromkeys(('twins','ivanna','xiaochun','arowell'),1)
    assert flow.runtime.publisher.snapshot.rounds_completed==0
    assert_no_normal_settlement(game)


@pytest.mark.parametrize('after',[None,'text_continue','event_giant_eye','event_reward_close'])
def test_giant_eye_intro_right_battle_close_and_return_without_loot_identity(after,monkeypatch):
    game,flow,_=event_flow(event_type='event_giant_eye',event_intro_pages=1,network_after=after,
                           event_reward_key=None)
    game.state='event_intro'
    original=game.control
    def control(frame,key,region=None):
        assert not key.startswith('loot_')
        return original(frame,key,region)
    monkeypatch.setattr(game,'control',control)
    flow._event()
    assert [k for k in game.actions if k!='network_retry']==[
        'text_continue','event_giant_eye','start_battle','enable_auto','event_reward_close','text_continue']
    assert game.state=='map' and flow.state.reward_stage=='event_closed'
    assert not flow.state.counted and flow.state.floor==1
    assert_no_normal_settlement(game)


@pytest.mark.parametrize('after',[None,'event_prison','start_battle','event_reward_close'])
@pytest.mark.parametrize('event',['event_prison','event_cage'])
def test_prison_left_battle_direct_reward_and_return(after,monkeypatch,event):
    after=event if after=='event_prison' else after
    game,flow,_=event_flow(event_type=event,network_after=after,event_reward_key=None)
    original=game.control
    def control(frame,key,region=None):
        assert not key.startswith('loot_')
        return original(frame,key,region)
    monkeypatch.setattr(game,'control',control)
    flow._event()
    expected=[event,'start_battle']
    if after!='start_battle':expected.append('enable_auto')
    # A battle that finishes during recovery must not receive a stale auto click.
    expected+=['event_reward_close','text_continue']
    assert [k for k in game.actions if k!='network_retry']==expected
    assert game.state=='map' and flow.state.reward_stage=='event_closed'
    assert not flow.state.counted and flow.state.floor==1
    assert_no_normal_settlement(game)


@pytest.mark.parametrize('after',['event_trap','start_battle','event_reward_close'])
def test_event_recovery_does_not_repeat_choice_start_or_reward_close(after):
    game,flow,_=event_flow(network_after=after)
    flow._event()
    for key in ('event_trap','start_battle','event_reward_close','text_continue'):
        assert game.actions.count(key)==1
    assert game.actions.count('network_retry')==4 and game.state=='map'
    assert_no_normal_settlement(game)


def test_event_requires_its_own_preparation_title_before_start(monkeypatch):
    game,flow,_=event_flow();original=game._visible
    monkeypatch.setattr(game,'_visible',lambda:['prepare_normal','start_battle'] if game.state=='prepare' else original())
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.RECOGNITION_TIMEOUT
    assert game.actions==['event_trap']


def test_failed_event_battle_unwinds_to_round_owner_without_reward():
    game,flow,_=event_flow(fail_battle=True)
    with pytest.raises(BattleDefeated) as error:flow._event()
    assert error.value.detection.key=="failed"
    assert 'event_reward_close' not in game.actions and 'text_continue' not in game.actions


def test_event_battle_timeout_cannot_enter_normal_settlement():
    game,flow,_=event_flow(timeout=3000,endless_battle=True)
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.CHAOS_BATTLE_TIMEOUT
    assert_no_normal_settlement(game)
    assert 'event_reward_close' not in game.actions


def test_loot_identity_without_close_prompt_does_not_authorize_click(monkeypatch):
    game,flow,_=event_flow(timeout=6000);original=game._visible
    monkeypatch.setattr(game,'_visible',lambda:[k for k in original() if k!='event_reward_close'])
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.CHAOS_BATTLE_TIMEOUT
    assert 'event_reward_close' not in game.actions and 'loot_summon_mark' not in game.actions


def test_manual_stop_in_event_battle_sends_no_reward_or_text_click():
    game,flow,stop=event_flow()
    game.on_capture=lambda g:stop.request(StopReason.MANUAL_STOP) if g.state=='battle' else None
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.MANUAL_STOP
    assert game.actions==['event_trap','start_battle']


def test_pending_teacher_can_be_deployed_in_event_preparation():
    game,flow,_=event_flow(pending_teacher=True)
    flow._event()
    assert game.swapped and not flow.state.pending_teacher
    assert game.actions.index('replace_button')<game.actions.index('start_battle')
    assert game.state=='map'


def test_return_requires_the_original_floor(monkeypatch):
    game,flow,_=event_flow();original=game.map_view
    def wrong_floor(frame):
        view=original(frame)
        return replace(view,floor=2) if view else None
    monkeypatch.setattr(game,'map_view',wrong_floor)
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.CHAOS_UNKNOWN_CONTENT
    assert flow.state.floor==1 and not flow.state.counted
