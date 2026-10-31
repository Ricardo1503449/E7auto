"""Joint selection/action boundaries with fake services and client coordinates."""
from collections import Counter
from dataclasses import replace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.types import ClientPoint, Point, Size
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from e7auto.vision.frames import CoordinateTransform
from tests.helpers.chaos_game import ChaosGame, chaos_config


@pytest.fixture(scope='module')
def configs():
    return {name: chaos_config(name) for name in ('dark_twins', 'budget')}


@pytest.fixture
def make_flow(monkeypatch, configs):
    # The game model captures dictionaries; only its pixel adapter is mocked.
    monkeypatch.setattr('e7auto.runtime.capture.adapt_frame', lambda frame, transform: frame)
    def create(*, size=(2322, 1306), lineup='dark_twins', page_ms=600, target=1, **options):
        base = configs[lineup]
        config = replace(base, chaos=replace(base.chaos, target_stage=target,
            timing=dict(base.chaos.timing, page_timeout_ms=page_ms)))
        game = ChaosGame(config, **options);stop = StopController()
        flow = ChaosFlow(config, game.dependencies(), stop,
                         SnapshotPublisher(RuntimeSnapshot.chaos_run('confirmation', 1), lambda _: None))
        flow.runtime.prepare();flow.state = RoundState.for_lineup(config.chaos.lineup)
        transform = CoordinateTransform(Size(2322, 1306), Size(*size))
        flow.runtime.transform = transform
        windows = flow.runtime.deps.windows
        bounds = replace(windows.state.client_bounds, width=size[0], height=size[1])
        windows.state = replace(windows.state, client_bounds=bounds)
        flow.runtime.baseline_bounds = bounds
        original = game.control
        def control(frame, key, region=None):
            hit = original(frame, key)
            if hit is None:return None
            hit = replace(hit, bounds=transform.rect(hit.bounds), anchor=transform.point(hit.anchor))
            if region is not None:
                area = transform.rect(region)
                if not (area.x <= hit.bounds.x and hit.bounds.right <= area.right
                        and area.y <= hit.bounds.y and hit.bounds.bottom <= area.bottom):return None
            game.points[hit.anchor] = key
            return hit
        monkeypatch.setattr(game, 'control', control)
        anchor = transform.point(Point(1968, 1201))
        entry = ClientPoint(round(anchor.x+250*transform.scale_x), round(anchor.y-125*transform.scale_y))
        game.points[entry] = 'open_stage'
        return game, flow, stop
    return create


def events(flow, name):
    return [fields for event, fields in flow.runtime.deps.logger.events if event == name]


def record_input(game, monkeypatch):
    original = game.click;submitted = []
    def click(window, point):
        submitted.append(point);original(window, point)
    monkeypatch.setattr(game, 'click', click)
    return submitted


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
@pytest.mark.parametrize('profession', [False, True])
def test_ready_filter_closes_from_one_joint_window(make_flow, monkeypatch, size, profession):
    game, flow, _ = make_flow(size=size)
    game.state, game.ticket, game.hero = 'recruit', 'all', 'teacher'
    game.attributes = {'dark', 'fire'};game.profession = bool(profession)
    original = game.control;frames = [];calls = Counter();last_buttons = []
    def control(frame, key, region=None):
        if 'filter_light' in frame['visible']:
            frames.append(frame);calls[id(frame), key] += 1
            if not profession:assert 'thief' not in key and 'knight' not in key
        hit = original(frame, key, region)
        if key == 'filter_light' and hit is not None:
            hit = replace(hit, anchor=ClientPoint(hit.anchor.x+len(last_buttons)+1, hit.anchor.y))
            game.points[hit.anchor] = key;last_buttons.append(hit)
        return hit
    monkeypatch.setattr(game, 'control', control);submitted = record_input(game, monkeypatch)
    flow.recruitment.filter('dark', profession=profession)
    assert game.actions == ['filter_dark', 'filter_light']
    assert game.attributes == {'dark', 'fire'}
    assert len({id(frame) for frame in frames}) == 3 and max(calls.values()) == 1
    assert submitted[-1] == last_buttons[-1].anchor
    assert flow.runtime.transform.point(submitted[-1]) is submitted[-1]


def test_filter_changes_one_control_at_a_time_in_original_order(make_flow):
    game, flow, _ = make_flow()
    game.state, game.ticket, game.hero = 'recruit', 'all', 'teacher'
    game.attributes = {'light', 'water', 'fire'}
    flow.recruitment.filter('dark', profession=True)
    assert game.actions == ['filter_dark', 'filter_water_selected', 'filter_light_selected',
                            'filter_icon_dark', 'filter_icon_thief_filter', 'filter_light']
    assert game.attributes == {'fire', 'dark'} and game.profession


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
def test_knight_filter_uses_only_budget_profession_and_client_coordinates(make_flow, monkeypatch, size):
    game, flow, _ = make_flow(size=size, lineup='budget')
    game.state, game.ticket, game.hero = 'recruit', 'all', 'shadow_rose';game.attributes={'dark', 'water'}
    visible, control, click = game._visible, game.control, game.click;knight=[False]
    def keys():
        values = visible()
        if game.filter_open:
            values = [key for key in values if 'thief' not in key]
            values.append('filter_knight_selected' if knight[0] else 'filter_icon_knight_filter')
        return values
    def observe(frame, key, region=None):
        assert 'thief' not in key
        return control(frame, key, region)
    def choose(window, point):
        if game.points.get(point) == 'filter_icon_knight_filter':
            game.actions.append('filter_icon_knight_filter');knight[0]=True
        else:click(window, point)
    monkeypatch.setattr(game, '_visible', keys);monkeypatch.setattr(game, 'control', observe)
    monkeypatch.setattr(game, 'click', choose);submitted=record_input(game, monkeypatch)
    flow.recruitment.filter('dark', profession='knight')
    assert game.actions == ['filter_dark', 'filter_water_selected', 'filter_icon_knight_filter', 'filter_light']
    assert game.attributes == {'dark'} and knight[0]
    assert 'filter_thief_selected' not in flow.settings.scenes
    assert all(flow.runtime.transform.point(point) is point for point in submitted)


@pytest.mark.parametrize('change', ['filter_icon_dark', 'filter_light_selected'])
def test_delayed_filter_change_is_not_replayed_or_skipped(make_flow, change):
    game, flow, _ = make_flow(page_ms=1500)
    game.state, game.ticket, game.hero = 'recruit', 'all', 'teacher'
    game.attributes = {'light'} if change.endswith('selected') else set()
    due = [];other_inputs = []
    def clicked(g, key):
        if key == change:
            g.attributes.add('light') if key.endswith('selected') else g.attributes.discard('dark')
            due.append(g.clock.now+.7)
        elif due and g.clock.now < due[0]:other_inputs.append(key)
    def capture(g):
        if due and g.clock.now >= due[0]:
            g.attributes.discard('light') if change.endswith('selected') else g.attributes.add('dark')
    game.on_click, game.on_capture = clicked, capture
    flow.recruitment.filter('dark')
    assert game.actions.count(change) == 1 and other_inputs == [] and game.attributes == {'dark'}


@pytest.mark.parametrize('missing', ['off_evidence', 'close_button', 'never_changed'])
def test_filter_unknown_or_unchanged_state_never_authorizes_next_input(make_flow, monkeypatch, missing):
    game, flow, _ = make_flow()
    game.state, game.ticket, game.hero = 'recruit', 'all', 'teacher'
    game.attributes = {'dark'} if missing != 'never_changed' else set()
    original = game.control
    def control(frame, key, region=None):
        if 'filter_light' in frame['visible']:
            if missing == 'off_evidence' and key in ('filter_water_selected', 'filter_icon_water'):return None
            if missing == 'close_button' and key in ('filter_dark', 'filter_light'):return None
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    if missing == 'never_changed':
        game.on_click = lambda g, key: g.attributes.discard('dark') if key == 'filter_icon_dark' else None
    with pytest.raises(StopExecution) as stopped:flow.recruitment.filter('dark')
    assert stopped.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert game.actions == (['filter_dark', 'filter_icon_dark', 'filter_icon_dark'] if missing == 'never_changed' else ['filter_dark'])


def test_filter_recovery_keeps_submitted_toggle_and_builds_fresh_evidence(make_flow, monkeypatch):
    game, flow, _ = make_flow(network_after='filter_icon_dark')
    game.state, game.ticket, game.hero = 'recruit', 'all', 'teacher'
    original = game.control;generations = []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if key == 'filter_dark_selected' and hit is not None:generations.append(flow.runtime.network_recovery_generation)
        return hit
    monkeypatch.setattr(game, 'control', control)
    flow.recruitment.filter('dark')
    assert game.actions.count('filter_icon_dark') == game.actions.count('filter_light') == 1
    assert game.actions.count('network_retry') == 4 and generations == [1, 1, 1]


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
def test_teacher_swap_joint_window_restarts_and_uses_last_arrow(make_flow, monkeypatch, size):
    game, flow, _ = make_flow(size=size, page_ms=1500)
    game.state, game.kind = 'prepare', 'normal';game.recruited.add('teacher')
    flow.state.pending_teacher = True
    original = game.control;selected_frames = [];arrows = []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if key == 'bench_selected':
            selected_frames.append(frame)
            if len(selected_frames) == 3:return None
        if key == 'replace_button' and hit is not None:
            hit = replace(hit, anchor=ClientPoint(hit.anchor.x+len(arrows)+1, hit.anchor.y))
            game.points[hit.anchor] = key;arrows.append(hit)
        return hit
    monkeypatch.setattr(game, 'control', control);submitted = record_input(game, monkeypatch)
    flow._swap_teacher()
    assert len(selected_frames) == 6 and len({id(frame) for frame in selected_frames}) == 6
    assert submitted[-1] == arrows[-1].anchor and flow.runtime.transform.point(submitted[-1]) is submitted[-1]
    assert game.actions == ['bench_teacher', 'replace_button'] and not flow.state.pending_teacher


@pytest.mark.parametrize('invalid', ['missing_selection', 'multiple_knights', 'multiple_arrows'])
def test_invalid_swap_never_submits_or_clears_pending(make_flow, monkeypatch, invalid):
    game, flow, _ = make_flow(multi_knights=invalid == 'multiple_knights')
    game.state, game.kind = 'prepare', 'normal';game.recruited.add('teacher')
    flow.state.pending_teacher = True
    original, controls = game.control, game.controls
    monkeypatch.setattr(game, 'control', lambda f, k, region=None:
                        None if invalid == 'missing_selection' and k == 'bench_selected' else original(f, k, region))
    def many(frame, key, region=None):
        hits = controls(frame, key, region)
        return hits+hits if invalid == 'multiple_arrows' and key == 'replace_button' else hits
    monkeypatch.setattr(game, 'controls', many)
    with pytest.raises(StopExecution):flow._swap_teacher()
    assert game.actions == ['bench_teacher'] * (2 if invalid == 'missing_selection' else 1) and flow.state.pending_teacher


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
@pytest.mark.parametrize('target', range(1, 6))
def test_preselected_stage_submits_from_panel_three_frames_without_date_reads(make_flow, monkeypatch, size, target):
    game, flow, _ = make_flow(size=size, target=target, page_ms=1500)
    game.state = 'hall';game.current_stage = 2 if target == 1 else 1
    game.chosen_stage = target;game.stage_scrolled = False
    original = game.control;frames = [];calls = Counter();buttons = []
    def control(frame, key, region=None):
        if frame['state'] == 'stage_panel':
            assert key != 'date_change_title'
            frames.append(frame);calls[id(frame), key] += 1
        hit = original(frame, key, region)
        if key == 'stage_confirm' and hit is not None:
            hit = replace(hit, anchor=ClientPoint(hit.anchor.x+len(buttons)+1, hit.anchor.y))
            game.points[hit.anchor] = key;buttons.append(hit)
        return hit
    monkeypatch.setattr(game, 'control', control);submitted = record_input(game, monkeypatch)
    flow.stages.ensure()
    assert len({id(frame) for frame in frames}) == len(buttons) == 3
    assert max(calls.values()) == 1 and submitted[-1] == buttons[-1].anchor
    assert game.actions == ['open_stage', 'stage_confirm'] and game.current_stage == target


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
def test_stage_click_requires_three_new_joint_frames_after_highlight_flicker(make_flow, monkeypatch, size):
    game, flow, _ = make_flow(size=size, page_ms=1500)
    game.state='hall';game.current_stage=game.chosen_stage=3;game.stage_scrolled=False
    original = game.stage_highlights;frames = []
    def highlights(frame, entry):
        if frame['chosen_stage'] == 1:
            frames.append(frame)
            if len(frames) == 2:return ()
        return original(frame, entry)
    monkeypatch.setattr(game, 'stage_highlights', highlights)
    flow.stages.ensure()
    assert len(frames) == len({id(frame) for frame in frames}) == 5
    assert game.actions == ['open_stage', 'stage_01', 'stage_confirm']


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
def test_unclaimed_prompt_and_button_share_three_frames_and_last_anchor(make_flow, monkeypatch, size):
    game, flow, _ = make_flow(size=size)
    game.state, game.kind = 'claimed', 'boss'
    original = game.control;prompts = [];buttons = []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if key == 'unclaimed_prompt' and hit is not None:prompts.append(frame)
        if key == 'unclaimed_confirm' and hit is not None:
            assert frame is prompts[-1]
            hit = replace(hit, anchor=ClientPoint(hit.anchor.x+len(buttons)+1, hit.anchor.y))
            game.points[hit.anchor] = key;buttons.append(hit)
        return hit
    monkeypatch.setattr(game, 'control', control);submitted = record_input(game, monkeypatch)
    flow._continue(2, allow_unclaimed=True)
    assert len(prompts) == len({id(frame) for frame in prompts}) == len(buttons) == 3
    assert submitted[-1] == buttons[-1].anchor and flow.runtime.transform.point(submitted[-1]) is submitted[-1]
    assert game.actions == ['continue_exploration', 'unclaimed_confirm'] and game.state == 'map'


def test_missing_unclaimed_button_blocks_background_map(make_flow, monkeypatch):
    game, flow, _ = make_flow();game.state, game.kind = 'claimed', 'boss'
    original, map_view = game.control, game.map_view
    monkeypatch.setattr(game, 'control', lambda f, k, region=None: None if k == 'unclaimed_confirm' else original(f, k, region))
    def background(frame):
        assert frame['state'] != 'unclaimed', 'a visible prompt must block background-map probing'
        return map_view(frame)
    monkeypatch.setattr(game, 'map_view', background)
    with pytest.raises(StopExecution):flow._continue(2, allow_unclaimed=True)
    assert game.actions == ['continue_exploration']


def test_no_forfeiture_permission_does_not_query_popup(make_flow, monkeypatch):
    game, flow, _ = make_flow();game.state, game.kind = 'claimed', 'normal'
    original = game.control
    def control(frame, key, region=None):
        assert key not in ('unclaimed_prompt', 'unclaimed_confirm')
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    flow._continue(1)
    assert game.actions == ['continue_exploration'] and game.state == 'map'


def test_forfeiture_recovery_never_resubmits_and_wrong_floor_does_not_finish(make_flow):
    game, flow, _ = make_flow(network_after='unclaimed_confirm')
    game.state, game.kind = 'claimed', 'boss'
    with pytest.raises(StopExecution) as stopped:flow._continue(3, allow_unclaimed=True)
    assert stopped.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert game.actions.count('unclaimed_confirm') == 1 and game.actions.count('network_retry') == 4


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
@pytest.mark.parametrize('flicker', [None, 'result', 'button'])
def test_camp_rank_and_close_use_same_fresh_frames(make_flow, monkeypatch, size, flicker):
    game, flow, _ = make_flow(size=size, page_ms=1500)
    game.state, game.kind, game.hero = 'rank', 'rest', 'xiaochun';flow.state.node_kind='rest'
    original, control = game.rank_result, game.control;frames = [];buttons = []
    def rank_result(frame):
        frames.append(frame)
        return None if flicker == 'result' and len(frames) == 2 else original(frame)
    def observe(frame, key, region=None):
        hit = control(frame, key, region)
        if key == 'rank_close' and hit is not None:
            assert frame is frames[-1]
            if flicker == 'button' and len(frames) == 2:return None
            hit = replace(hit, anchor=ClientPoint(hit.anchor.x+len(buttons)+1, hit.anchor.y))
            game.points[hit.anchor] = key;buttons.append(hit)
        return hit
    monkeypatch.setattr(game, 'rank_result', rank_result);monkeypatch.setattr(game, 'control', observe)
    submitted = record_input(game, monkeypatch)
    flow._rank_up('xiaochun')
    assert len(frames) == len({id(frame) for frame in frames}) == (3 if flicker is None else 5)
    assert submitted[-1] == buttons[-1].anchor and flow.runtime.transform.point(submitted[-1]) is submitted[-1]
    assert flow.state.ranks['xiaochun'] == 2 and len(events(flow, 'chaos_rank_changed')) == 1
    assert game.actions == ['hero_xiaochun', 'rank_confirm', 'rank_close']


@pytest.mark.parametrize('missing', ['result', 'wrong_result', 'close'])
def test_camp_missing_evidence_preserves_only_confirmed_rank(make_flow, monkeypatch, missing):
    game, flow, _ = make_flow()
    game.state, game.kind, game.hero = 'rank', 'rest', 'xiaochun';flow.state.node_kind='rest'
    if missing != 'close':monkeypatch.setattr(game, 'rank_result', lambda frame: None if missing == 'result' else (3, 4))
    else:
        original = game.control
        monkeypatch.setattr(game, 'control', lambda f, k, region=None: None if k == 'rank_close' else original(f, k, region))
    with pytest.raises(StopExecution) as stopped:flow._rank_up('xiaochun')
    assert stopped.value.reason is StopReason.RECOGNITION_TIMEOUT and game.actions.count('rank_confirm') == 1
    assert 'rank_close' not in game.actions
    assert flow.state.ranks['xiaochun'] == (2 if missing == 'close' else 1)
    assert len(events(flow, 'chaos_rank_changed')) == int(missing == 'close')


def test_camp_close_gets_original_budget_after_rank_is_recorded(make_flow, monkeypatch):
    game, flow, _ = make_flow(page_ms=600)
    game.state, game.kind, game.hero = 'rank', 'rest', 'xiaochun';flow.state.node_kind='rest'
    original, control = game.rank_result, game.control;first = [];recorded = []
    def result(frame):
        if not first:first.append(game.clock.now)
        return original(frame)
    def observe(frame, key, region=None):
        if key == 'rank_close' and first and game.clock.now-first[0] < .5-1e-6:return None
        return control(frame, key, region)
    logger = flow.runtime.deps.logger.event
    def event(name, **fields):
        logger(name, **fields)
        if name == 'chaos_rank_changed':recorded.append(game.clock.now)
    monkeypatch.setattr(game, 'rank_result', result);monkeypatch.setattr(game, 'control', observe)
    monkeypatch.setattr(flow.runtime.deps.logger, 'event', event)
    flow._rank_up('xiaochun')
    assert recorded[0]-first[0] == pytest.approx(.2)
    assert game.clock.now-first[0] >= .7 and len(recorded) == 1 and game.actions.count('rank_close') == 1


@pytest.mark.parametrize('when', ['during_result', 'after_close'])
def test_camp_network_discards_old_joint_frames_without_repeating_progress(make_flow, monkeypatch, when):
    game, flow, _ = make_flow(network_after='rank_close' if when == 'after_close' else None)
    game.state, game.kind, game.hero = 'rank', 'rest', 'xiaochun';flow.state.node_kind='rest'
    original = game.rank_result;generations=[]
    def result(frame):
        generations.append(flow.runtime.network_recovery_generation)
        if when == 'during_result' and len(generations) == 2:game.network_remaining=1
        return original(frame)
    monkeypatch.setattr(game, 'rank_result', result)
    flow._rank_up('xiaochun')
    assert generations == ([0, 0, 1, 1, 1] if when == 'during_result' else [0, 0, 0])
    assert game.actions.count('rank_confirm') == game.actions.count('rank_close') == len(events(flow, 'chaos_rank_changed')) == 1


@pytest.mark.parametrize('boundary', ['stop_before_result', 'stop_after_record', 'stop_after_close', 'window'])
def test_camp_final_input_guards_and_confirmed_rank_survive_stop(make_flow, monkeypatch, boundary):
    game, flow, stop = make_flow()
    game.state, game.kind, game.hero = 'rank', 'rest', 'xiaochun';flow.state.node_kind='rest'
    if boundary in ('stop_before_result', 'stop_after_close'):
        action = 'rank_confirm' if boundary == 'stop_before_result' else 'rank_close'
        game.on_click = lambda g, key: stop.request(StopReason.MANUAL_STOP) if key == action else None
    logger = flow.runtime.deps.logger.event
    def event(name, **fields):
        logger(name, **fields)
        if name == 'chaos_rank_changed':
            if boundary == 'stop_after_record':stop.request(StopReason.MANUAL_STOP)
            elif boundary == 'window':
                windows=flow.runtime.deps.windows
                windows.state=replace(windows.state, client_bounds=replace(windows.state.client_bounds, x=1))
    monkeypatch.setattr(flow.runtime.deps.logger, 'event', event)
    with pytest.raises(StopExecution) as stopped:flow._rank_up('xiaochun')
    assert stopped.value.reason is (StopReason.WINDOW_ABNORMAL if boundary == 'window' else StopReason.MANUAL_STOP)
    assert flow.state.ranks['xiaochun'] == (1 if boundary == 'stop_before_result' else 2)
    assert game.actions.count('rank_close') == int(boundary == 'stop_after_close')
    assert len(events(flow, 'chaos_rank_changed')) == int(boundary != 'stop_before_result')


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
@pytest.mark.parametrize('feature', ['filter', 'swap', 'stage', 'forfeiture'])
@pytest.mark.parametrize('boundary', ['stop', 'window'])
def test_joint_action_keeps_final_stop_and_window_guards(make_flow, monkeypatch, size, feature, boundary):
    game, flow, stop = make_flow(size=size)
    if feature == 'filter':
        game.state, game.ticket, game.hero = 'recruit', 'all', 'teacher';game.attributes={'dark'}
        stage, forbidden = 'filter_state', 'filter_light'
        execute = lambda: flow.recruitment.filter('dark')
    elif feature == 'swap':
        game.state, game.kind = 'prepare', 'normal';game.recruited.add('teacher');flow.state.pending_teacher=True
        stage, forbidden = 'replace_knight', 'replace_button'
        execute = flow._swap_teacher
    elif feature == 'stage':
        game.state='hall';game.current_stage=3;game.chosen_stage=1;game.stage_scrolled=False
        stage, forbidden = 'target_stage_selected', 'stage_confirm'
        execute = flow.stages.ensure
    else:
        game.state, game.kind = 'claimed', 'boss'
        stage, forbidden = 'continue_destination', 'unclaimed_confirm'
        execute = lambda: flow._continue(2, allow_unclaimed=True)
    logger = flow.runtime.deps.logger.event;triggered=[]
    def event(name, **fields):
        logger(name, **fields)
        if name == 'chaos_confirmed' and fields.get('stage') == stage:
            triggered.append(True)
            if boundary == 'stop':stop.request(StopReason.MANUAL_STOP)
            else:
                windows=flow.runtime.deps.windows
                windows.state=replace(windows.state, client_bounds=replace(windows.state.client_bounds, x=1))
    monkeypatch.setattr(flow.runtime.deps.logger, 'event', event)
    with pytest.raises(StopExecution) as stopped:execute()
    assert triggered and stopped.value.reason is (StopReason.MANUAL_STOP if boundary == 'stop' else StopReason.WINDOW_ABNORMAL)
    assert forbidden not in game.actions
