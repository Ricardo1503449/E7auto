from dataclasses import replace

import pytest

from e7auto.bootstrap import AutomationSession
from e7auto.core.domain import OverlayActivityStatus, StopReason
from e7auto.features.chaos.contracts import Detection, Node
from e7auto.features.chaos.state import choose_node
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController
from e7auto.core.domain import RuntimeSnapshot
from e7auto.core.types import Point, Rect
from tests.helpers.chaos_game import ChaosGame, chaos_config


def run_game(game, rounds=1):
    snapshots=[]
    session=AutomationSession(game.config,game.dependencies(),StopController(),snapshots.append)
    final=session.run_chaos(rounds)
    return final,snapshots


@pytest.mark.parametrize('failure', ['reset', 'events', 'loot'])
def test_cleanup_failure_still_attempts_other_pools_and_frame_release(failure):
    from types import SimpleNamespace
    calls = []
    def step(name):
        calls.append(name)
        if name == failure:
            raise RuntimeError(name)
    flow = ChaosFlow.__new__(ChaosFlow)
    flow.stages = SimpleNamespace(invalidate_start=lambda: None)
    flow.vision = SimpleNamespace(
        reset_temporal=lambda: step('reset'),
        close_event_recognition=lambda: step('events'),
        close_loot_recognition=lambda: step('loot'))
    flow.runtime = SimpleNamespace(release_cached_game_frame=lambda: step('frame'))
    with pytest.raises(RuntimeError, match=failure):
        flow.release_cached_game_frame()
    assert calls == ['reset', 'events', 'loot', 'frame']
    assert flow.state is None and flow._map_handoff is None


def test_two_rounds_complete_only_after_final_home_with_all_nodes_and_swap():
    game=ChaosGame(chaos_config())
    final,snapshots=run_game(game,2)
    assert final.stop_reason==StopReason.CHAOS_COMPLETE,game.actions
    assert final.rounds_completed==game.completed==2 and game.state=="home"
    assert final.overlay_status==OverlayActivityStatus.COMPLETED
    assert game.actions.count("purchase_confirm")==2
    assert game.actions.count("rank_confirm")==2
    assert game.actions.count("replace_button")==2
    assert game.actions.count("enable_auto")==2
    assert game.actions.count("recruit_confirm")==10
    assert all(s.overlay_status in (OverlayActivityStatus.RUNNING,OverlayActivityStatus.RECONNECTING,OverlayActivityStatus.COMPLETED,OverlayActivityStatus.STOPPED) for s in snapshots)


def test_network_after_purchase_pauses_deadline_and_never_replays_purchase():
    game=ChaosGame(chaos_config(),network_after="purchase_confirm")
    final,snapshots=run_game(game)
    assert final.stop_reason==StopReason.CHAOS_COMPLETE,game.actions
    assert game.actions.count("purchase_confirm")==1
    assert game.actions.count("network_retry")==4
    assert any(s.overlay_status==OverlayActivityStatus.RECONNECTING for s in snapshots)


@pytest.mark.parametrize('action,expected_count', [('recruit_confirm',5),('rank_confirm',1),('loot_confirm',4)])
def test_reconnect_after_irreversible_action_confirms_result_once(action,expected_count):
    game=ChaosGame(chaos_config(),network_after=action)
    final,_=run_game(game)
    assert final.stop_reason==StopReason.CHAOS_COMPLETE,game.actions
    assert game.actions.count(action)==expected_count
    assert final.rounds_completed==1


def test_battle_finishing_during_reconnect_resumes_settlement_not_stale_auto_click():
    game=ChaosGame(chaos_config(),network_after='start_battle')
    final,_=run_game(game)
    assert final.stop_reason==StopReason.CHAOS_COMPLETE,game.actions
    assert game.actions.count('enable_auto')==1
    assert game.actions.index('continue_exploration') < game.actions.index('enable_auto')


@pytest.mark.parametrize('option,reason,forbidden',[
    ('wrong_selection',StopReason.RECOGNITION_TIMEOUT,'loot_confirm'),
    ('unknown_loot',StopReason.CHAOS_UNKNOWN_CONTENT,'loot_confirm'),
    ('multi_knights',StopReason.CHAOS_UNKNOWN_CONTENT,'replace_button'),
])
def test_unsafe_states_stop_without_submit_or_completed_round(option,reason,forbidden):
    game=ChaosGame(chaos_config(),**{option:True})
    final,_=run_game(game)
    assert final.stop_reason==reason,game.actions
    assert final.rounds_completed==0
    assert forbidden not in game.actions


def test_non_home_start_sends_only_one_wake_then_stops(monkeypatch):
    game=ChaosGame(chaos_config());game.state='hall'
    def wake_only(_window, point):
        assert point == game.config.points['main_screen_wake']
        game.actions.append('wake')
    monkeypatch.setattr(game, 'click', wake_only)
    final,_=run_game(game)
    assert final.stop_reason==StopReason.RECOGNITION_TIMEOUT
    assert game.actions == ['wake']


@pytest.mark.parametrize('during_network',[False,True])
def test_manual_stop_prevents_inputs_after_boundary_and_never_returns_home(during_network):
    game=ChaosGame(chaos_config(),network_after='purchase_confirm' if during_network else None)
    snapshots=[];session=AutomationSession(game.config,game.dependencies(),StopController(),snapshots.append)
    cutoff=[]
    def capture(g):
        if not cutoff and ((g.network_remaining>0) if during_network else g.state=='battle'):
            cutoff.append(len(g.actions));session.request_stop()
    game.on_capture=capture
    final=session.run_chaos(1)
    assert final.stop_reason==StopReason.MANUAL_STOP
    assert len(game.actions)==cutoff[0] and game.state!='home'
    assert final.rounds_completed==0


def test_battle_has_active_time_limit():
    config=chaos_config();timing=dict(config.chaos.timing,battle_timeout_ms=8000)
    game=ChaosGame(replace(config,chaos=replace(config.chaos,timing=timing)),endless_battle=True)
    final,_=run_game(game)
    assert final.stop_reason==StopReason.CHAOS_BATTLE_TIMEOUT


def test_recovery_discards_old_stability_evidence():
    game=ChaosGame(chaos_config())
    flow=ChaosFlow(game.config,game.dependencies(),StopController(),SnapshotPublisher(RuntimeSnapshot.chaos_run('r',1),lambda _:None))
    flow.runtime.prepare()
    post_recovery=[]
    def inject(g):
        if g.captures==3:g.network_remaining=4
        if g.captures>3 and not g.network_remaining:post_recovery.append(g.captures)
    game.on_capture=inject
    flow._control('home_battle')
    # One raw frame detects recovery completion and is discarded; three fresh
    # observations must then form the new stable window.
    assert len(post_recovery)>=4
    assert flow.runtime.active_monotonic()<2
    assert game.clock.now>=40


def test_manual_stop_during_input_dispatch_allows_only_that_input():
    game=ChaosGame(chaos_config());session=AutomationSession(game.config,game.dependencies(),StopController(),lambda _:None)
    cutoff=[]
    def clicked(g,key):
        if key=='rank_confirm':cutoff.append(len(g.actions));session.request_stop()
    game.on_click=clicked
    final=session.run_chaos(1)
    assert final.stop_reason==StopReason.MANUAL_STOP and len(game.actions)==cutoff[0]
    assert 'rank_close' not in game.actions


@pytest.mark.parametrize('kind',['elite','boss'])
def test_reward_without_claim_confirmation_never_continues_or_resubmits(kind):
    game=ChaosGame(chaos_config());submitted=[]
    def clicked(g,key):
        if key=='loot_confirm' and g.kind==kind and not submitted:
            submitted.append(len(g.actions));g.state='settlement'
    game.on_click=clicked
    final,_=run_game(game)
    assert final.stop_reason==StopReason.RECOGNITION_TIMEOUT
    assert submitted and final.rounds_completed==0
    assert 'continue_exploration' not in game.actions[submitted[0]:]
    assert 'loot_confirm' not in game.actions[submitted[0]:]


def test_unknown_event_is_not_clicked(monkeypatch):
    game=ChaosGame(chaos_config());visible=game._visible
    monkeypatch.setattr(game,'_visible',lambda: [] if game.state=='event' else visible())
    final,_=run_game(game)
    assert final.stop_reason==StopReason.CHAOS_UNKNOWN_CONTENT
    assert 'event_torch' not in game.actions and final.rounds_completed==0


@pytest.mark.parametrize('wrong_row',[False,True])
def test_hero_selection_relocates_identity_and_requires_its_own_gold_frame(monkeypatch,wrong_row):
    game=ChaosGame(chaos_config());original=game._hit
    def hit(key,frame):
        result=original(key,frame)
        if frame['state']=='recruit' and frame['selected'] and key.startswith('hero_'):
            dx,dy=(0,250) if wrong_row and key=='hero_selected_border' else (0,0) if wrong_row else (650,0)
            result=replace(result,bounds=Rect(result.bounds.x+dx,result.bounds.y+dy,result.bounds.width,result.bounds.height),anchor=Point(result.anchor.x+dx,result.anchor.y+dy))
        return result
    monkeypatch.setattr(game,'_hit',hit)
    final,_=run_game(game)
    assert final.stop_reason==(StopReason.RECOGNITION_TIMEOUT if wrong_row else StopReason.CHAOS_COMPLETE)
    if wrong_row:assert 'recruit_confirm' not in game.actions


def test_other_boss_vouchers_skip_without_needing_identity_templates(monkeypatch):
    # The budget roster has no required replacement unless Shadow Rose departed.
    # Dark twins must still require their second-floor Teacher voucher.
    game=ChaosGame(chaos_config('budget'));visible=game._visible
    def without_supported_voucher():
        keys=visible()
        return [k for k in keys if not k.startswith('ticket_')] if game.state=='claimed' and game.kind=='boss' else keys
    monkeypatch.setattr(game,'_visible',without_supported_voucher)
    final,_=run_game(game)
    assert final.stop_reason==StopReason.CHAOS_COMPLETE
    assert game.actions.count('unclaimed_confirm')==2
    assert 'ticket_all' not in game.actions and 'replace_button' not in game.actions


def test_node_priority_and_y_ties_do_not_depend_on_input_order():
    def node(kind,y):return Node(kind,Detection('node_'+kind,1,Rect(100,y,20,20),Point(110,y+10)))
    assert choose_node((node('shop',1),node('event',40),node('loot',20))).kind=='loot'
    assert choose_node((node('elite',1),node('rest',900))).kind=='rest'
    assert choose_node((node('boss',50),)).kind=='boss'
