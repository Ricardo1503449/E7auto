import cv2
import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config
from tests.helpers.paths import ROOT


def setup(event='event_mushroom', rank=2, **kwargs):
    game=ChaosGame(chaos_config(),event_type=event,**kwargs)
    stop=StopController()
    flow=ChaosFlow(game.config,game.dependencies(),stop,
                   SnapshotPublisher(RuntimeSnapshot.chaos_run('rank-events',1),lambda _:None))
    flow.runtime.prepare();flow.state=RoundState(node_kind='event')
    game.state='event';game.kind='event'
    flow.state.ranks['xiaochun']=game.ranks['xiaochun']=rank
    return game,flow,stop


def changes(flow):
    return [fields for name,fields in flow.runtime.deps.logger.events if name=='chaos_rank_changed']


@pytest.mark.parametrize('event,expected',[
    ('event_sword',['event_sword','start_battle','enable_auto','event_reward_close','text_continue']),
    ('event_cairn',['event_cairn','event_reward_close','event_reward_close','text_continue']),
    ('event_mushroom',['event_mushroom','event_rank_enter','hero_xiaochun','rank_confirm','rank_close','text_continue']),
])
def test_new_event_routes_and_return(event,expected):
    game,flow,_=setup(event);flow._event()
    assert game.actions==expected and game.state=='map'
    assert flow.state.floor==1 and not flow.state.counted
    if event=='event_mushroom':
        assert flow.state.ranks['xiaochun']==3 and len(changes(flow))==1
    else:assert flow.state.ranks['xiaochun']==2 and not changes(flow)


def test_event_and_camp_share_rank_priority_and_update_log():
    game,flow,_=setup(rank=4)
    flow.state.ranks['twins']=game.ranks['twins']=4
    flow._event()
    assert flow.state.ranks['xiaochun']==5 and flow.state.next_rank_hero()=='twins'
    game.state='camp';game.kind='rest';flow.state.node_kind='rest';flow._camp()
    assert flow.state.ranks['twins']==5 and flow.state.next_rank_hero() == 'ivanna'
    flow.state.ranks['ivanna']=game.ranks['ivanna']=4
    game.state='camp';flow._camp()
    assert flow.state.ranks['ivanna']==5 and flow.state.next_rank_hero() is None
    assert [(c['hero'],c['rank_before'],c['rank_after'],c['node_kind']) for c in changes(flow)]==[
        ('xiaochun',4,5,'event'),('twins',4,5,'rest'),('ivanna',4,5,'rest')]
    before=list(game.actions);game.state='event';game.kind='event'
    with pytest.raises(StopExecution):flow._event()
    assert game.actions==before


@pytest.mark.parametrize('after',['rank_confirm','rank_close'])
def test_event_rank_recovery_records_change_once(after):
    game,flow,_=setup(network_after=after);flow._event()
    assert flow.state.ranks['xiaochun']==3
    assert game.actions.count('rank_confirm')==game.actions.count('rank_close')==1
    assert game.actions.count('network_retry')==4
    assert changes(flow)==[dict(hero='xiaochun',rank_before=2,rank_after=3,node_kind='event')]


@pytest.mark.parametrize('observed',[(3,4),None])
def test_wrong_or_missing_result_never_updates_rank(monkeypatch,observed):
    game,flow,_=setup()
    monkeypatch.setattr(game,'rank_result',lambda frame:observed)
    with pytest.raises(StopExecution):flow._event()
    assert flow.state.ranks['xiaochun']==2 and not changes(flow)
    assert game.actions.count('rank_confirm')==1 and 'rank_close' not in game.actions


@pytest.mark.parametrize('at,expected_rank',[('rank_confirm',2),('rank_close',3)])
def test_manual_stop_does_not_invent_or_repeat_rank_change(at,expected_rank):
    game,flow,stop=setup()
    game.on_click=lambda g,k:stop.request(StopReason.MANUAL_STOP) if k==at else None
    with pytest.raises(StopExecution):flow._event()
    assert flow.state.ranks['xiaochun']==expected_rank
    assert len(changes(flow))==int(expected_rank==3) and 'text_continue' not in game.actions


def test_cairn_refuses_second_close_until_card_changes(monkeypatch):
    game,flow,_=setup('event_cairn')
    monkeypatch.setattr(game,'event_card_changed',lambda frame,previous:False)
    with pytest.raises(StopExecution):flow._event()
    assert game.actions==['event_cairn','event_reward_close']


def test_cairn_waits_through_stale_first_card_without_double_click():
    game,flow,_=setup('event_cairn');close_times=[];ready=[]
    def clicked(g,key):
        if key=='event_reward_close':
            close_times.append(g.clock.now)
            if len(close_times)==1:
                g.state='cairn_hp';ready.append(g.clock.now+1.2)
    def captured(g):
        if ready and g.clock.now>=ready[0] and g.state=='cairn_hp':g.state='cairn_reward'
    game.on_click=clicked;game.on_capture=captured
    flow._event()
    assert len(close_times)==2 and close_times[1]-close_times[0]>=1.2
    assert game.state=='map'


@pytest.mark.parametrize('close_works', [True, False])
def test_real_animated_hp_card_closes_without_becoming_a_false_second_page(monkeypatch, close_works):
    game, flow, _ = setup('event_cairn')
    vision = ChaosVision(game.config, TemplateRepository(game.config))
    images = {name: cv2.imread(str(ROOT / f'tests/fixtures/chaos/event_cards/{name}.png'))
              for name in ('hp_pose_closed', 'hp_pose_open', 'loot')}
    assert all(image is not None for image in images.values())

    def pixels(frame):
        if '_pixels' not in frame:
            key = 'loot' if frame['state'] == 'cairn_reward' else (
                'hp_pose_closed' if game.captures % 2 else 'hp_pose_open')
            frame['_pixels'] = images[key]
        return frame['_pixels']

    original_control = game.control
    def control(frame, key, region=None):
        if key == 'event_reward_close' and frame['state'] in ('cairn_hp', 'cairn_reward'):
            hit = vision.control(pixels(frame), key, region)
            if hit is not None:
                game.points[hit.anchor] = key
            return hit
        return original_control(frame, key, region)

    monkeypatch.setattr(game, 'control', control)
    monkeypatch.setattr(game, 'event_card_snapshot', lambda frame: vision.event_card_snapshot(pixels(frame)))
    monkeypatch.setattr(game, 'event_card_stable', lambda frame, previous: vision.event_card_stable(pixels(frame), previous))
    monkeypatch.setattr(game, 'event_card_changed', lambda frame, previous: vision.event_card_changed(pixels(frame), previous))
    if not close_works:
        game.on_click = lambda g, key: setattr(g, 'state', 'cairn_hp') if key == 'event_reward_close' else None
    try:
        if close_works:
            flow._event()
            assert game.actions == ['event_cairn', 'event_reward_close', 'event_reward_close', 'text_continue']
            assert game.state == 'map' and flow.state.floor == 1
        else:
            with pytest.raises(StopExecution, match='page=1/2; attempts=3'):
                flow._event()
            assert game.actions == ['event_cairn'] + ['event_reward_close'] * 3
            assert game.state == 'cairn_hp'
    finally:
        vision.close_loot_recognition()
