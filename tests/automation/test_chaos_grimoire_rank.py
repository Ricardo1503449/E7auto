"""Single and multiple rank promotion, evidence gating and capped bookkeeping."""
import struct

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


@pytest.fixture(scope='module')
def config():return chaos_config()


def setup(config, rank=1, event='event_grimoire', **kwargs):
    game = ChaosGame(config, event_type=event, **kwargs)
    stop = StopController()
    flow = ChaosFlow(config, game.dependencies(), stop,
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('grimoire', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState(floor=2, node_kind='event')
    flow.state.ranks['xiaochun'] = game.ranks['xiaochun'] = rank
    game.kind = game.state = 'event';game.floor = 2
    return game, flow, stop


def changes(flow):return [d for n,d in flow.runtime.deps.logger.events if n=='chaos_rank_changed']


def test_grimoire_binding_and_original_combination_resources_no_longer_loaded(config):
    key = 'event_grimoire';scene = config.chaos.scenes[key]
    spec = config.chaos.event_recognition['templates'][key]
    assert key in HANDLERS and spec['layout'] == 3
    width,height = struct.unpack('>II',config.template_paths[scene.template_key].read_bytes()[16:24])
    assert tuple(round(o+s*a) for o,s,a in zip(spec['origin'],(width,height),scene.action)) == (1161,1090)
    assert all(k not in config.chaos.scenes and 'chaos:'+k not in config.template_paths
               for k in ('rank_12','rank_23','rank_34','rank_45'))


@pytest.mark.parametrize('rank', [1,2,3,4])
def test_grimoire_two_ranks_capped_at_five_and_closed_once(config, rank):
    game,flow,_ = setup(config,rank)
    flow._event()
    assert game.actions == ['event_grimoire','event_rank_enter','hero_xiaochun','rank_confirm','rank_close','text_continue']
    assert flow.state.ranks['xiaochun'] == min(5,rank+2)
    assert changes(flow)==[dict(hero='xiaochun',rank_before=rank,rank_after=min(5,rank+2),node_kind='event')]
    assert game.state=='map' and game.floor==flow.state.floor==2


@pytest.mark.parametrize('event', ['event_mushroom','event_ghost','event_starlight_flower','camp'])
def test_existing_single_rank_callers_use_new_pair_reading(config, event):
    game,flow,_ = setup(config,2,event)
    if event=='camp':
        game.kind=flow.state.node_kind='rest';game.state='camp';flow._camp()
    else:flow._event()
    assert flow.state.ranks['xiaochun']==3 and len(changes(flow))==1
    assert game.actions.count('rank_confirm')==game.actions.count('rank_close')==1
    assert game.state=='map'


@pytest.mark.parametrize('after', ['rank_confirm','rank_close'])
def test_two_rank_network_recovery_does_not_resubmit_or_double_update(config, after):
    game,flow,_=setup(config,network_after=after);flow._event()
    assert flow.state.ranks['xiaochun']==3 and len(changes(flow))==1
    assert game.actions.count('rank_confirm')==game.actions.count('rank_close')==1


@pytest.mark.parametrize('observed', [None,(1,2),(2,3),(1,4)])
def test_missing_wrong_before_or_wrong_increment_cannot_update_or_close(config, observed, monkeypatch):
    game,flow,_=setup(config)
    monkeypatch.setattr(game,'rank_result',lambda frame:observed)
    with pytest.raises(StopExecution):flow._event()
    assert flow.state.ranks['xiaochun']==1 and not changes(flow)
    assert game.actions.count('rank_confirm')==1 and 'rank_close' not in game.actions


def test_rank_evidence_must_be_consecutive(config, monkeypatch):
    game,flow,_=setup(config);seen=0
    def observe(frame):
        nonlocal seen
        seen+=1
        return (1,3) if seen%2 else None
    monkeypatch.setattr(game,'rank_result',observe)
    with pytest.raises(StopExecution):flow._event()
    assert seen>3 and flow.state.ranks['xiaochun']==1 and 'rank_close' not in game.actions


@pytest.mark.parametrize('at,expected', [('rank_confirm',1),('rank_close',3)])
def test_stop_keeps_only_confirmed_rank_update(config, at, expected):
    game,flow,stop=setup(config)
    game.on_click=lambda g,k:stop.request(StopReason.MANUAL_STOP) if k==at else None
    with pytest.raises(StopExecution):flow._event()
    assert flow.state.ranks['xiaochun']==expected and len(changes(flow))==int(expected==3)
    assert 'text_continue' not in game.actions


def test_full_first_priority_uses_twins_then_ivanna_then_stops_when_all_full(config):
    game,flow,_=setup(config,5)
    flow.state.ranks['twins']=game.ranks['twins']=4
    flow._event()
    assert 'hero_twins' in game.actions and flow.state.ranks['twins']==5
    flow.state.ranks['ivanna']=game.ranks['ivanna']=4
    game.state='event';flow._event()
    assert 'hero_ivanna' in game.actions and flow.state.ranks['ivanna']==5
    game.state='event';before=list(game.actions)
    with pytest.raises(StopExecution):flow._event()
    assert game.actions==before


@pytest.mark.parametrize('steps', [0,-1,True,1.5,5])
def test_invalid_increment_is_rejected_before_input(config, steps):
    game,flow,_=setup(config)
    with pytest.raises(StopExecution):flow._rank_up('xiaochun',steps=steps)
    assert game.actions==[]


def test_multi_rank_battle_reward_is_not_inferred_from_single_done_signal(config):
    game,flow,_=setup(config)
    with pytest.raises(StopExecution):flow._rank_up('xiaochun',steps=2,battle_reward=True)
    assert game.actions==[]
