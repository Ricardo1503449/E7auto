from dataclasses import replace

import pytest

from e7auto.bootstrap import AutomationSession
from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.ports import CaptureError
from e7auto.core.types import Rect, Point, Size, ClientPoint
from e7auto.vision.frames import CoordinateTransform
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


def stage_flow(game):
    control=StopController();snapshots=[]
    flow=ChaosFlow(game.config,game.dependencies(),control,SnapshotPublisher(RuntimeSnapshot.chaos_run('stage',1),snapshots.append))
    flow.runtime.prepare();game.state='hall'
    return flow,control,snapshots


def test_already_stage_one_has_no_selection_inputs():
    game=ChaosGame(chaos_config());flow,_,_=stage_flow(game)
    flow.stages.ensure()
    assert game.actions==[]


@pytest.mark.parametrize('scrolled',[False,True])
def test_stage_three_selects_one_before_exploration_and_second_round_skips_switch(scrolled):
    game=ChaosGame(chaos_config(),stage_one=False);game.stage_scrolled=scrolled
    final=AutomationSession(game.config,game.dependencies(),StopController(),lambda _:None).run_chaos(2)
    assert final.stop_reason==StopReason.CHAOS_COMPLETE,game.actions
    assert final.rounds_completed==2 and game.stage_one
    assert game.actions.count('stage_confirm')==game.actions.count('open_stage')==1
    assert game.actions.index('stage_confirm')<game.actions.index('start_exploration')
    assert game.actions.count('pointer_down')==(1 if scrolled else 0)
    assert game.actions.count('pointer_up')==(1 if scrolled else 0)


def test_other_stage_without_a_number_template_can_open_known_lobby_selector(monkeypatch):
    game=ChaosGame(chaos_config(),stage_one=False);original=game._visible
    monkeypatch.setattr(game,'_visible',lambda:[k for k in original() if k!='stage_three'])
    flow,_,_=stage_flow(game);flow.stages.ensure()
    assert game.stage_one and game.actions.count('stage_confirm')==1


def test_each_round_rechecks_stage_if_game_advances_it_after_settlement():
    game=ChaosGame(chaos_config(),stage_one=False)
    def advance(g,key):
        if key=='journey_close':g.stage_one=False;g.stage_scrolled=True;g.stage_chosen_one=False
    game.on_click=advance
    final=AutomationSession(game.config,game.dependencies(),StopController(),lambda _:None).run_chaos(2)
    assert final.stop_reason==StopReason.CHAOS_COMPLETE and final.rounds_completed==2
    assert game.actions.count('stage_confirm')==2
    assert game.actions.count('start_exploration')==2 and game.state=='home'


def test_01_requires_its_own_highlight(monkeypatch):
    game=ChaosGame(chaos_config(),stage_one=False);game.stage_scrolled=False;original=game.control
    def control(frame,key,region=None):
        hit=original(frame,key,region)
        if hit and key=='stage_selected':return replace(hit,bounds=Rect(995,381,1199,146))
        return hit
    monkeypatch.setattr(game,'control',control)
    flow,_,_=stage_flow(game)
    with pytest.raises(StopExecution) as error:flow.stages.ensure()
    assert error.value.reason==StopReason.CHAOS_STAGE_SWITCH_FAILED
    assert 'stage_confirm' not in game.actions and 'start_exploration' not in game.actions


def test_no_scroll_progress_stops_at_configured_bound_without_confirm():
    config=chaos_config()
    assert config.chaos.stage_selection['max_drags']==2
    game=ChaosGame(config,stage_one=False)
    game.on_pointer=lambda g,action,_:setattr(g,'stage_scrolled',True) if action=='pointer_up' else None
    flow,_,_=stage_flow(game)
    with pytest.raises(StopExecution) as error:flow.stages.ensure()
    assert error.value.reason==StopReason.CHAOS_STAGE_SWITCH_FAILED
    assert game.actions.count('pointer_down')==game.actions.count('pointer_up')==2
    assert 'stage_confirm' not in game.actions


def test_stage_selection_allows_one_retry_and_succeeds_on_second_drag():
    game=ChaosGame(chaos_config(),stage_one=False)
    def pointer(g,action,_):
        if action=='pointer_up' and g.actions.count('pointer_up')==1:g.stage_scrolled=True
    game.on_pointer=pointer
    flow,_,_=stage_flow(game);flow.stages.ensure()
    assert game.stage_one and game.actions.count('stage_confirm')==1
    assert game.actions.count('pointer_down')==game.actions.count('pointer_up')==2


def test_stage_selection_default_fifteen_second_budget_stops_missing_panel(monkeypatch):
    game=ChaosGame(chaos_config(),stage_one=False)
    assert game.config.chaos.stage_selection['timeout_ms']==15000
    original=game.control
    monkeypatch.setattr(game,'control',lambda frame,key,region=None:None if key=='stage_confirm' else original(frame,key,region))
    flow,_,_=stage_flow(game);started=flow.runtime.active_monotonic()
    with pytest.raises(StopExecution) as error:flow.stages.ensure()
    assert error.value.reason==StopReason.CHAOS_STAGE_SWITCH_FAILED
    assert 15<=flow.runtime.active_monotonic()-started<=15.2
    assert 'stage_confirm' not in game.actions and 'pointer_down' not in game.actions


@pytest.mark.parametrize('at',['pointer_down','pointer_move'])
def test_manual_stop_during_drag_sends_release_only_after_stop(at):
    game=ChaosGame(chaos_config(),stage_one=False);flow,stop,_=stage_flow(game);cutoff=[]
    def pointer(g,action,_):
        if action==at and not cutoff:cutoff.append(len(g.actions));stop.request(StopReason.MANUAL_STOP)
    game.on_pointer=pointer
    with pytest.raises(StopExecution) as error:flow.stages.ensure()
    assert error.value.reason==StopReason.MANUAL_STOP
    assert game.actions[cutoff[0]:]==['pointer_up']
    assert not game.pointer_held and 'stage_confirm' not in game.actions


def test_network_mid_drag_releases_before_retry_and_reacquires_list():
    config=chaos_config();config=replace(config,chaos=replace(config.chaos,stage_selection=dict(config.chaos.stage_selection,timeout_ms=6000)))
    game=ChaosGame(config,stage_one=False);flow,_,snapshots=stage_flow(game);injected=[]
    def pointer(g,action,_):
        if action=='pointer_move' and not injected:injected.append(True);g.network_remaining=4
    game.on_pointer=pointer
    flow.stages.ensure()
    assert game.actions.index('pointer_up')<game.actions.index('network_retry')
    assert game.actions.count('network_retry')==4 and game.actions.count('stage_confirm')==1
    assert game.stage_one and not game.pointer_held
    assert game.clock.now>40 and flow.runtime.active_monotonic()<6


def test_reconnect_after_confirm_does_not_resubmit():
    game=ChaosGame(chaos_config(),stage_one=False,network_after='stage_confirm');flow,_,_=stage_flow(game)
    flow.stages.ensure()
    assert game.actions.count('stage_confirm')==1 and game.actions.count('network_retry')==4
    assert game.stage_one


def test_selector_disappearing_during_short_burst_is_rechecked_after_release():
    game=ChaosGame(chaos_config(),stage_one=False);flow,_,_=stage_flow(game)
    def pointer(g,action,_):
        if action=='pointer_move':g.state='hall'
    game.on_pointer=pointer
    with pytest.raises(StopExecution) as error:flow.stages.ensure()
    assert error.value.reason==StopReason.CHAOS_STAGE_SWITCH_FAILED
    assert game.actions.count('pointer_move')==6 and game.actions[-1]=='pointer_up'
    assert not game.pointer_held


def test_capture_failure_during_hold_still_releases():
    game=ChaosGame(chaos_config(),stage_one=False);flow,_,_=stage_flow(game)
    # Force a frame-check deadline to occur while the pointer is held.
    game.on_pointer=lambda g,action,_:g.clock.sleep(.1) if action=='pointer_down' else None
    def fail(g):
        if g.pointer_held:raise CaptureError('synthetic capture failure')
    game.on_capture=fail
    with pytest.raises(StopExecution) as error:flow.stages.ensure()
    assert error.value.reason==StopReason.CAPTURE_FAILURE
    assert game.actions[-1]=='pointer_up' and not game.pointer_held


def test_selection_submission_requires_hall_stage_one_afterward():
    game=ChaosGame(chaos_config(),stage_one=False);flow,_,_=stage_flow(game)
    def clicked(g,key):
        if key=='stage_confirm':g.stage_one=False
    game.on_click=clicked
    with pytest.raises(StopExecution) as error:flow.stages.ensure()
    assert error.value.reason==StopReason.CHAOS_STAGE_SWITCH_FAILED
    assert game.actions.count('stage_confirm')==1 and 'start_exploration' not in game.actions


def test_active_stage_deadline_aborts_a_partial_drag_and_releases():
    config=chaos_config();config=replace(config,chaos=replace(config.chaos,stage_selection=dict(config.chaos.stage_selection,timeout_ms=1000)))
    game=ChaosGame(config,stage_one=False);flow,_,_=stage_flow(game)
    with pytest.raises(StopExecution) as error:flow.stages.ensure()
    assert error.value.reason==StopReason.CHAOS_STAGE_SWITCH_FAILED
    assert 'stage_confirm' not in game.actions and not game.pointer_held
    assert game.actions[-1]=='pointer_up'


def test_manual_stop_during_network_after_drag_does_not_resume_gesture():
    game=ChaosGame(chaos_config(),stage_one=False);flow,stop,_=stage_flow(game);cutoff=[]
    def pointer(g,action,_):
        if action=='pointer_move':g.network_remaining=4
    def capture(g):
        if g.network_remaining and not g.pointer_held and not cutoff:
            cutoff.append(len(g.actions));stop.request(StopReason.MANUAL_STOP)
    game.on_pointer=pointer;game.on_capture=capture
    with pytest.raises(StopExecution) as error:flow.stages.ensure()
    assert error.value.reason==StopReason.MANUAL_STOP
    assert len(game.actions)==cutoff[0] and not game.pointer_held
    assert game.actions.count('pointer_up')==1


def test_fast_long_drag_uses_six_500px_steps_before_settled_01_selection():
    game=ChaosGame(chaos_config(),stage_one=False);flow,_,_=stage_flow(game);points=[];selected=[]
    game.on_pointer=lambda g,action,point:points.append((action,point,g.clock.now))
    game.on_click=lambda g,key:selected.append((key,g.clock.now))
    flow.stages.ensure()
    down,*moves,up=points
    assert down[0]=='pointer_down' and up[0]=='pointer_up'
    assert [p.y for _,p,_ in moves]==[838,1338,1838,2338,2838,3338]
    assert all(action=='pointer_move' and point.x==1675 for action,point,_ in moves)
    assert up[1]==moves[-1][1]
    assert [t-down[2] for _,_,t in moves]==pytest.approx([.01,.02,.03,.04,.05,.06])
    assert next(t for key,t in selected if key=='stage_01')-up[2]>=.5
    assert game.stage_one and game.actions.count('stage_confirm')==1


def synthetic_stage_config(target):
    """Exercise all flow branches with fake lobby observations, not image evidence."""
    from e7auto.features.chaos.configuration import with_chaos_config
    return with_chaos_config(chaos_config(), target_stage=target)


@pytest.mark.parametrize('target',range(1,6))
def test_target_stage_already_in_lobby_skips_selector(target):
    game=ChaosGame(synthetic_stage_config(target));game.current_stage=target
    flow,_,_=stage_flow(game);flow.stages.ensure()
    assert game.actions==[]


@pytest.mark.parametrize('target',range(1,6))
@pytest.mark.parametrize('scrolled',[False,True])
def test_target_stage_selects_correct_row_and_only_its_highlight(target,scrolled):
    game=ChaosGame(synthetic_stage_config(target));game.current_stage=2 if target==1 else 1
    game.chosen_stage=game.current_stage;game.stage_scrolled=scrolled
    flow,_,_=stage_flow(game);flow.stages.ensure()
    assert game.current_stage==target
    assert game.actions.count(f'stage_{target:02d}')==1
    assert game.actions.count('stage_confirm')==1
    assert game.actions.count('pointer_down')==int(scrolled)
    assert 'difficulty_one' not in game.actions


@pytest.mark.parametrize('target',range(2,6))
@pytest.mark.parametrize('scrolled',[False,True])
def test_locked_target_stops_once_stable_top_is_found(target,scrolled):
    game=ChaosGame(synthetic_stage_config(target));game.unlocked_stages={1}
    game.stage_scrolled=scrolled;game.chosen_stage=1
    flow,_,_=stage_flow(game)
    with pytest.raises(StopExecution) as error:flow.stages.ensure()
    assert error.value.reason==StopReason.CHAOS_STAGE_SWITCH_FAILED
    assert game.actions.count('pointer_down')==int(scrolled)
    assert 'stage_confirm' not in game.actions and 'start_exploration' not in game.actions
    assert game.current_stage==1


def test_left_difficulty_is_never_queried_and_selected_row_skips_extra_click(monkeypatch):
    game=ChaosGame(synthetic_stage_config(3));game.stage_scrolled=False;game.chosen_stage=3
    original=game.control
    def control(frame,key,region=None):
        assert key!='difficulty_one'
        return original(frame,key,region)
    monkeypatch.setattr(game,'control',control)
    flow,_,_=stage_flow(game);flow.stages.ensure()
    assert 'stage_03' not in game.actions and game.actions.count('stage_confirm')==1


@pytest.mark.parametrize('target', range(1, 6))
def test_lobby_stage_only_queries_the_selected_digit(monkeypatch, target):
    game = ChaosGame(synthetic_stage_config(target));game.current_stage = target
    selected = game.config.chaos.stage_selection['lobby_scenes'][target]
    stage_keys = set(game.config.chaos.stage_selection['lobby_scenes'].values())
    original = game.control;queries = []
    def control(frame, key, region=None):
        if key in stage_keys:
            assert key == selected
            queries.append(key)
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    flow, _, _ = stage_flow(game);flow.stages.ensure()
    assert queries == [selected] * 3
    assert flow.stages.take_start() is not None and game.actions == []


def test_changed_lobby_after_selecting_stage_five_is_rejected():
    game=ChaosGame(synthetic_stage_config(5));game.stage_scrolled=False
    game.on_click=lambda g,key:setattr(g,'current_stage',4) if key=='stage_confirm' else None
    flow,_,_=stage_flow(game)
    with pytest.raises(StopExecution) as error:flow.stages.ensure()
    assert error.value.reason==StopReason.CHAOS_STAGE_SWITCH_FAILED
    assert game.actions.count('stage_confirm')==1 and 'start_exploration' not in game.actions


def test_two_rounds_keep_selected_stage_even_when_settlement_advances_it():
    game=ChaosGame(synthetic_stage_config(3));game.stage_scrolled=False
    def advance(g,key):
        if key=='journey_close':g.current_stage=4;g.chosen_stage=4;g.stage_scrolled=True
    game.on_click=advance
    final=AutomationSession(game.config,game.dependencies(),StopController(),lambda _:None).run_chaos(2)
    assert final.stop_reason==StopReason.CHAOS_COMPLETE and final.rounds_completed==2
    assert game.actions.count('stage_confirm')==2


@pytest.mark.parametrize('target', range(1, 6))
def test_missing_target_digit_requires_selection_without_querying_other_stages(monkeypatch, target):
    game = ChaosGame(synthetic_stage_config(target));game.current_stage = 2 if target == 1 else 1
    selected = game.config.chaos.stage_selection['lobby_scenes'][target]
    stage_keys = set(game.config.chaos.stage_selection['lobby_scenes'].values())
    original = game.control;queries = []
    def control(frame, key, region=None):
        if key in stage_keys:
            assert key == selected
            queries.append(key)
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    flow, _, _ = stage_flow(game)
    result = flow.stages.lobby_state(flow._frame())
    assert result is not None and result[1] is False
    assert queries == [selected] and flow.stages.take_start() is None and game.actions == []


def test_stage_confirmation_matches_button_only_once_per_frame(monkeypatch):
    game=ChaosGame(chaos_config(),stage_one=False);game.stage_scrolled=False
    original=game.control;calls={};frames=[]
    def control(frame,key,region=None):
        if key=='stage_confirm':
            frames.append(frame)
            calls[id(frame)]=calls.get(id(frame),0)+1
        return original(frame,key,region)
    monkeypatch.setattr(game,'control',control)
    flow,_,_=stage_flow(game);flow.stages.ensure()
    assert game.actions.count('stage_confirm')==1 and game.stage_one
    assert calls and max(calls.values())==1
    selected=[f for f in frames if f['chosen_stage']==1]
    assert len(selected)==3 and len({id(f) for f in selected})==3


def test_stage_wait_returns_last_fresh_button_after_broken_stability(monkeypatch):
    from e7auto.core.types import Point
    game=ChaosGame(chaos_config());flow,_,_=stage_flow(game)
    game.state='stage_panel';game.stage_scrolled=False
    sequence=iter([1,1,3,1,1,1]);frames=[];buttons=[]
    game.on_capture=lambda g:setattr(g,'chosen_stage',next(sequence))
    original=game.control
    def control(frame,key,region=None):
        hit=original(frame,key,region)
        if key=='stage_confirm' and hit:
            frames.append(frame)
            hit=replace(hit,bounds=Rect(1000,1100,200,50),anchor=Point(1100+len(frames),1125))
            buttons.append(hit)
        return hit
    monkeypatch.setattr(game,'control',control)
    button=flow._wait('target_stage_selected',flow.stages.selected)
    assert len(frames)==6 and len({id(f) for f in frames})==6
    assert button is buttons[-1] and button.anchor==Point(1106,1125)
    assert game.actions==[]


def test_multiple_containing_stage_highlights_still_reject_selection(monkeypatch):
    game=ChaosGame(chaos_config());flow,_,_=stage_flow(game)
    game.state='stage_panel';game.stage_scrolled=False;game.chosen_stage=1
    original=game.stage_highlights
    def highlights(frame,entry):
        hit=original(frame,entry)[0]
        return hit,replace(hit,bounds=replace(hit.bounds,y=hit.bounds.y+1))
    monkeypatch.setattr(game,'stage_highlights',highlights)
    assert flow.stages.selected(flow._frame()) is None


def stop_at_lobby_start(flow, game):
    game.on_click = lambda _, key: flow.runtime.control.request(StopReason.MANUAL_STOP) if key == 'start_exploration' else None
    with pytest.raises(StopExecution) as stopped:
        flow._start_round()
    assert stopped.value.reason is StopReason.MANUAL_STOP
    assert game.actions.count('start_exploration') == 1


def adapt_lobby_game(flow, game, monkeypatch, size):
    """Scale fake observations and the fake window; no images or real inputs."""
    # ChaosGame captures dictionaries. Keep the real capture/network guards,
    # while replacing only the pixel adapter that requires a NumPy frame.
    monkeypatch.setattr('e7auto.runtime.capture.adapt_frame', lambda frame, transform: frame)
    transform = CoordinateTransform(Size(2322, 1306), Size(*size))
    flow.runtime.transform = transform
    windows = flow.runtime.deps.windows
    bounds = replace(windows.state.client_bounds, width=size[0], height=size[1])
    windows.state = replace(windows.state, client_bounds=bounds)
    flow.runtime.baseline_bounds = bounds
    original = game.control
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if hit is None:return None
        hit = replace(hit, bounds=transform.rect(hit.bounds), anchor=transform.point(hit.anchor))
        game.points[hit.anchor] = key
        return hit
    monkeypatch.setattr(game, 'control', control)
    anchor = transform.point(Point(1968, 1201))
    game.points[ClientPoint(round(anchor.x+250*transform.scale_x), round(anchor.y-125*transform.scale_y))] = 'open_stage'


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
@pytest.mark.parametrize('target', range(1, 6))
def test_lobby_start_uses_three_fresh_joint_frames_and_last_anchor(monkeypatch, target, size):
    game = ChaosGame(synthetic_stage_config(target));game.current_stage = target
    flow, _, _ = stage_flow(game)
    adapt_lobby_game(flow, game, monkeypatch, size)
    original = game.control;calls = {};frames = [];buttons = []
    def control(frame, key, region=None):
        frames.append(frame)  # Keep frame identities alive for per-frame counts.
        calls[id(frame), key] = calls.get((id(frame), key), 0) + 1
        hit = original(frame, key, region)
        if key == 'start_exploration' and hit is not None:
            hit = replace(hit, anchor=ClientPoint(hit.anchor.x+game.captures, hit.anchor.y))
            game.points[hit.anchor] = key;buttons.append(hit)
        return hit
    monkeypatch.setattr(game, 'control', control)
    stop_at_lobby_start(flow, game)
    assert game.captures == 3 and len(buttons) == 3
    assert len({id(frame) for frame in frames}) == 3
    selected = flow.settings.stage_selection['lobby_scenes'][target]
    for key in ('date_change_title', 'lobby_research', 'start_exploration', selected):
        assert [count for (frame_id, name), count in calls.items() if name == key] == [1, 1, 1]
    other_stages = set(flow.settings.stage_selection['lobby_scenes'].values()) - {selected}
    assert not any(name in other_stages for frame_id, name in calls)
    event = next(fields for name, fields in flow.runtime.deps.logger.events
                 if name == 'input' and fields['action'] == 'chaos_start_exploration')
    assert event['client_x'] == buttons[-1].anchor.x


def test_lobby_start_restarts_three_frames_after_missing_stage(monkeypatch):
    game = ChaosGame(chaos_config());flow, _, _ = stage_flow(game)
    original = game._visible
    monkeypatch.setattr(game, '_visible', lambda: [k for k in original() if k != 'stage_one'] if game.captures == 3 else original())
    stop_at_lobby_start(flow, game)
    assert game.captures == 6 and game.actions == ['start_exploration']


def test_lobby_start_recovers_network_before_building_new_three_frame_evidence(monkeypatch):
    game = ChaosGame(chaos_config());flow, _, _ = stage_flow(game)
    game.on_capture = lambda g: setattr(g, 'network_remaining', 1) if g.captures == 3 else None
    original = game.control;ready = []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if key == 'start_exploration' and hit is not None:
            ready.append((frame, flow.runtime.network_recovery_generation))
        return hit
    monkeypatch.setattr(game, 'control', control)
    stop_at_lobby_start(flow, game)
    assert game.actions == ['network_retry', 'start_exploration']
    assert [generation for _, generation in ready] == [0, 0, 1, 1, 1]
    assert len({id(frame) for frame, _ in ready[-3:]}) == 3


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
def test_stage_switch_hands_off_three_frame_lobby_result_without_another_wait(monkeypatch, size):
    game = ChaosGame(chaos_config(), stage_one=False);game.stage_scrolled = False
    flow, _, _ = stage_flow(game)
    adapt_lobby_game(flow, game, monkeypatch, size)
    original = game.control;returned = []
    def control(frame, key, region=None):
        if key == 'start_exploration' and frame['state'] == 'hall' and 'stage_one' in frame['visible']:
            returned.append(frame)
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    stop_at_lobby_start(flow, game)
    assert game.actions.count('stage_confirm') == 1
    assert len(returned) == len({id(frame) for frame in returned}) == 3


def test_lobby_ready_remains_separate_and_never_provides_start_authorization(monkeypatch):
    game = ChaosGame(chaos_config());flow, _, _ = stage_flow(game)
    original = game.control;stages = set(flow.settings.stage_selection['lobby_scenes'].values())
    def control(frame, key, region=None):
        assert key not in stages
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    flow._wait_round_lobby(observe_ms=0)
    assert game.captures == 3 and flow.stages.take_start() is None and game.actions == []


@pytest.mark.parametrize('boundary', ['frame', 'raw_capture', 'recovery', 'transform', 'target', 'input'])
def test_confirmed_start_is_discarded_after_boundary(boundary):
    game = ChaosGame(chaos_config());flow, _, _ = stage_flow(game)
    flow.stages.ensure()
    if boundary == 'frame':flow._frame()
    elif boundary == 'raw_capture':flow.runtime.capture()
    elif boundary == 'recovery':flow.runtime.network_recovery_generation += 1
    elif boundary == 'transform':flow.runtime.transform = replace(flow.runtime.transform)
    elif boundary == 'target':flow.stages.target_stage = 2
    elif boundary == 'input':
        flow._tap(flow.stages._confirmed_start.button, settle_ms=0)
    assert flow.stages.take_start() is None


def test_one_frame_cannot_provide_start_and_stable_handoff_is_consumed_once():
    game = ChaosGame(chaos_config());flow, _, _ = stage_flow(game)
    flow.stages._lobby_start_state(flow._frame())
    assert flow.stages.take_start() is None
    flow.stages.ensure()
    assert flow.stages.take_start().key == 'start_exploration'
    assert flow.stages.take_start() is None


def test_intervening_capture_reconfirms_before_start(monkeypatch):
    game = ChaosGame(chaos_config());flow, _, _ = stage_flow(game)
    original = flow.runtime.deps.logger.event;injected = []
    def event(name, **fields):
        original(name, **fields)
        if name == 'chaos_stage_checked' and not injected:
            injected.append(True);flow._frame()
    monkeypatch.setattr(flow.runtime.deps.logger, 'event', event)
    stop_at_lobby_start(flow, game)
    assert game.captures == 7


@pytest.mark.parametrize('boundary', ['stop', 'window'])
def test_start_handoff_keeps_final_stop_and_window_guards(monkeypatch, boundary):
    game = ChaosGame(chaos_config());flow, _, _ = stage_flow(game)
    original = flow.runtime.deps.logger.event
    def event(name, **fields):
        original(name, **fields)
        if name == 'chaos_stage_checked':
            if boundary == 'stop':flow.runtime.control.request(StopReason.MANUAL_STOP)
            else:
                windows = flow.runtime.deps.windows
                windows.state = replace(windows.state, client_bounds=replace(windows.state.client_bounds, x=1))
    monkeypatch.setattr(flow.runtime.deps.logger, 'event', event)
    with pytest.raises(StopExecution) as stopped:flow._start_round()
    assert stopped.value.reason is (StopReason.MANUAL_STOP if boundary == 'stop' else StopReason.WINDOW_ABNORMAL)
    assert game.actions == []


@pytest.mark.parametrize('size', [(2322, 1306), (1920, 1080), (1536, 864)])
def test_selector_entry_uses_client_anchor_and_scales_only_offset(size):
    game = ChaosGame(chaos_config());flow, _, _ = stage_flow(game)
    transform = CoordinateTransform(Size(2322, 1306), Size(*size));flow.runtime.transform = transform
    hit = game._hit('start_exploration', {'state': 'hall'})
    hit = replace(hit, bounds=transform.rect(hit.bounds), anchor=transform.point(hit.anchor))
    point = flow.stages.selector_point(hit)
    assert isinstance(point, ClientPoint) and transform.point(point) is point
    assert point == ClientPoint(round(hit.anchor.x+250*transform.scale_x), round(hit.anchor.y-125*transform.scale_y))
    assert hit.bounds.x <= point.x < hit.bounds.right and point.y < hit.bounds.y


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
@pytest.mark.parametrize('boundary', ['right', 'too_high', 'below', 'bad_anchor', 'bad_button', 'outside_client'])
def test_selector_entry_rejects_invalid_button_relative_geometry(size, boundary):
    game = ChaosGame(chaos_config());flow, _, _ = stage_flow(game)
    transform = CoordinateTransform(Size(2322, 1306), Size(*size));flow.runtime.transform = transform
    hit = game._hit('start_exploration', {'state': 'hall'})
    hit = replace(hit, bounds=transform.rect(hit.bounds), anchor=transform.point(hit.anchor))
    if boundary in ('right', 'too_high', 'below'):
        offset = {'right': [700, -125], 'too_high': [250, -400], 'below': [250, 0]}[boundary]
        flow.stages.settings = replace(flow.settings, stage_selection=dict(flow.settings.stage_selection, open_offset=offset))
    elif boundary == 'bad_anchor':hit = replace(hit, anchor=ClientPoint(hit.bounds.right, hit.anchor.y))
    elif boundary == 'bad_button':hit = replace(hit, bounds=replace(hit.bounds, x=0))
    else:hit = replace(hit, bounds=replace(hit.bounds, x=size[0], width=100))
    with pytest.raises(StopExecution) as stopped:flow.stages.selector_point(hit)
    assert stopped.value.reason is StopReason.CONFIG_INCOMPLETE and game.actions == []
