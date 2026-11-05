"""Reachable flow handoffs preserve fresh joint evidence and guarded inputs."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.types import Point
from e7auto.core.types import Size, ClientPoint
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.confirmation import Confirmation
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from e7auto.vision.frames import CoordinateTransform
from tests.helpers.chaos_game import ChaosGame, chaos_config


@pytest.fixture(scope='module')
def config():
    return chaos_config()


def prepared(config, *, state='event', kind='event', event='event_mushroom'):
    game = ChaosGame(config, event_type=event)
    game.state, game.kind = state, kind
    flow = ChaosFlow(config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('reuse', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState.for_lineup(config.chaos.lineup)
    flow.state.node_kind = kind
    return game, flow


@pytest.mark.parametrize('change', ['none', 'capture', 'input', 'recovery', 'geometry', 'window'])
def test_confirmation_is_single_use_and_rejects_intervening_changes(change):
    runtime = SimpleNamespace(control=StopController(), capture_count=3, input_generation=1,
                              network_recovery_generation=0, transform=object(), window=object())
    value = object()
    evidence = Confirmation.record(runtime, value)
    if change == 'capture': runtime.capture_count += 1
    if change == 'input': runtime.input_generation += 1
    if change == 'recovery': runtime.network_recovery_generation += 1
    if change == 'geometry': runtime.transform = object()
    if change == 'window': runtime.window = object()
    assert evidence.take(runtime) is (value if change == 'none' else None)
    assert evidence.take(runtime) is None


@pytest.mark.parametrize('scenario,target,page', [
    ('enter', 'intro_enter', 'intro'), ('return', 'battle_title', 'battle_menu'),
    ('camp', 'camp_rank', 'camp'), ('voucher', 'continue_exploration', 'claimed'),
    ('rank', 'event_rank_enter', 'event_rank_offer'),
    ('tablet', 'event_tablet_continue', 'event_ticket'),
    ('passage', 'unclaimed_confirm', 'event_unclaimed'),
    ('armory', 'event_old_armory_left', 'armory_subchoice'),
    ('supply', 'supply_leave', 'supply_done'),
    ('camp_maxed', 'camp_leave', 'camp'),
    ('journey', 'journey_close', 'journey'),
    ('rank_title', 'rank_title', 'rank'),
    ('battle_rank', 'continue_exploration', 'rank_result'),
])
def test_flow_uses_one_stable_window_and_last_button(config, monkeypatch, scenario, target, page):
    events = {'tablet': 'event_tablet', 'passage': 'event_passage', 'armory': 'event_old_armory',
              'battle_rank': 'event_blood_shelter'}
    game, flow = prepared(config, event=events.get(scenario, 'event_mushroom'))
    original = game.control
    frames, anchors = [], []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if key == target and frame['state'] == page and hit is not None:
            frames.append(frame)
            hit = replace(hit, anchor=Point(hit.anchor.x+len(frames), hit.anchor.y))
            game.points[hit.anchor] = key
            anchors.append(hit.anchor)
        return hit
    monkeypatch.setattr(game, 'control', control)
    clicked = []
    click = game.click
    def submit(window, point):
        if game.points.get(point) == target: clicked.append(point)
        click(window, point)
    monkeypatch.setattr(game, 'click', submit)
    if scenario == 'enter':
        game.state = 'home';flow._enter_lobby()
    elif scenario == 'return':
        game.state = 'hall';flow._return_home()
    elif scenario == 'camp':
        game.state, game.kind, flow.state.node_kind = 'camp', 'rest', 'rest';flow._camp()
    elif scenario == 'voucher':
        game.state, game.kind, flow.state.battle_floor = 'claimed', 'boss', 1
        flow._voucher();flow._continue(2, allow_unclaimed=True)
    elif scenario == 'supply':
        game.state, game.kind, flow.state.node_kind = 'supply', 'loot', 'loot';flow._supply()
    elif scenario == 'camp_maxed':
        game.state, game.kind, flow.state.node_kind = 'camp', 'rest', 'rest'
        flow.state.ranks.update(dict.fromkeys(flow.settings.lineup.rank_priority, 5));flow._camp()
    elif scenario == 'journey':
        game.state, game.kind, game.floor = 'prepare', 'boss', 3
        flow.state.battle_floor = 3;flow._battle('boss')
    else:
        flow._event()
    assert len(frames) == len({id(frame) for frame in frames}) == 3
    assert clicked == ([] if scenario == 'rank_title' else [anchors[-1]])


@pytest.mark.parametrize('interruption', ['none', 'missing_button', 'recovery'])
def test_preparation_and_start_share_frames_or_require_new_joint_evidence(config, monkeypatch, interruption):
    game, flow = prepared(config, state='prepare')
    original = game.control
    titles, buttons = [], []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if frame['state'] == 'prepare' and key == 'prepare_event': titles.append(frame)
        if frame['state'] == 'prepare' and key == 'start_battle':
            buttons.append((frame, flow.runtime.network_recovery_generation))
            if len(buttons) == 2 and interruption == 'missing_button': return None
            if len(buttons) == 2 and interruption == 'recovery': game.network_remaining = 4
        return hit
    monkeypatch.setattr(game, 'control', control)
    flow._fight('prepare_event', ('event_reward_close',))
    assert game.actions.count('start_battle') == 1
    assert len(buttons) == (3 if interruption == 'none' else 6 if interruption == 'missing_button' else 5)
    assert all(frame is title for (frame, _), title in zip(buttons, titles))
    if interruption == 'recovery': assert [generation for _, generation in buttons] == [0, 0, 1, 1, 1]


@pytest.mark.parametrize('lineup', ['dark_twins', 'budget'])
def test_recruit_title_filter_and_specific_hero_use_their_last_joint_frames(monkeypatch, lineup):
    config = chaos_config(lineup)
    game, flow = prepared(config, state='recruit_cards')
    hero = config.chaos.heroes[0]
    original, targets, opening = game.control, [], []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if hit is not None and frame['state'] == 'recruit' and not frame['selected']:
            if key == 'hero_'+hero['key']: targets.append(frame)
            if key == 'filter_dark' and game.actions == ['ticket_'+hero['ticket']]: opening.append(frame)
        return hit
    monkeypatch.setattr(game, 'control', control)
    flow._click_to('ticket_'+hero['ticket'], 'title_'+hero['ticket'])
    flow.recruitment.filter(hero['attribute'], hero=hero['key'])
    flow.recruitment.recruit(hero['key'], result_key='recruited_teacher_initial' if hero['key'] == 'teacher' else None)
    assert len(targets) == len(opening) == 3
    assert game.recruited == {hero['key']}


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864)])
def test_joint_handoff_sends_the_last_client_anchor_without_rescaling(config, monkeypatch, size):
    monkeypatch.setattr('e7auto.runtime.capture.adapt_frame', lambda frame, transform: frame)
    game, flow = prepared(config, state='prepare')
    transform = CoordinateTransform(Size(2322, 1306), Size(*size))
    flow.runtime.transform = transform
    windows = flow.runtime.deps.windows
    windows.state = replace(windows.state, client_bounds=replace(
        windows.state.client_bounds, width=size[0], height=size[1]))
    flow.runtime.baseline_bounds = windows.state.client_bounds
    original, anchors, submitted = game.control, [], []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if hit is None:return None
        hit = replace(hit, bounds=transform.rect(hit.bounds), anchor=transform.point(hit.anchor))
        if key == 'start_battle':
            hit = replace(hit, anchor=ClientPoint(hit.anchor.x+len(anchors), hit.anchor.y))
            anchors.append(hit.anchor)
        game.points[hit.anchor] = key
        return hit
    monkeypatch.setattr(game, 'control', control)
    original_click = game.click
    def click(window, point):
        submitted.append(point);original_click(window, point)
    monkeypatch.setattr(game, 'click', click)
    flow._control('prepare_event', alongside=lambda f, _: game.control(f, 'start_battle'))
    start = flow._take_confirmed('start_battle')
    flow._tap(start)
    assert len(anchors) == 3 and submitted == [anchors[-1]]


def test_new_frame_forces_a_reused_control_to_reconfirm(config, monkeypatch):
    game, flow = prepared(config, state='intro')
    flow._control('intro_enter')
    flow._frame()
    before = game.captures
    flow._act('intro_enter')
    # Three new source frames precede the action; its ordinary buffer is retained.
    assert game.captures >= before+3 and game.actions == ['intro_enter']


def test_reward_card_overlaps_result_confirmation_but_keeps_six_hundred_ms(config, monkeypatch):
    game, flow = prepared(config, event='event_backpack')
    original, times, clicked = game.control, [], []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if key == 'event_reward_close' and hit is not None: times.append(game.clock.now)
        return hit
    monkeypatch.setattr(game, 'control', control)
    game.on_click = lambda g, key: clicked.append(g.clock.now) if key == 'event_reward_close' else None
    flow._event()
    assert len(times) == 7
    assert .6-1e-9 <= clicked[0]-times[0] < .7
    assert game.actions.count('event_reward_close') == 1


def test_closed_event_map_is_handed_to_finishing_and_selection_once(config, monkeypatch):
    game, flow = prepared(config, event='event_backpack')
    game.on_click = lambda g, key: setattr(g, 'state', 'map') if key == 'event_reward_close' else None
    flow._event()
    before = game.captures
    flow._map_for_selection(1)
    assert game.captures == before
    flow._map_for_selection(1)
    assert game.captures == before+3


def test_card_content_change_restarts_the_full_stability_duration(config, monkeypatch):
    game, flow = prepared(config, event='event_backpack')
    original, times, clicked = game.control, [], []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if key == 'event_reward_close' and hit is not None:times.append(game.clock.now)
        return hit
    def snapshot(frame):return frame['state'], int(len(times) >= 4)
    monkeypatch.setattr(game, 'control', control)
    monkeypatch.setattr(game, 'event_card_snapshot', snapshot)
    monkeypatch.setattr(game, 'event_card_stable', lambda frame, before: snapshot(frame) == before)
    game.on_click = lambda g, key: clicked.append(g.clock.now) if key == 'event_reward_close' else None
    flow._event()
    assert clicked[0]-times[0] >= .9-1e-9
    assert game.actions.count('event_reward_close') == 1


def test_visible_text_keeps_priority_over_a_background_map_after_card_close(config, monkeypatch):
    game, flow = prepared(config, event='event_backpack')
    visible, pending = game._visible, [True]
    monkeypatch.setattr(game, '_visible', lambda: ['text_continue'] if game.state == 'map' and pending[0] else visible())
    def clicked(g, key):
        if key == 'event_reward_close':g.state = 'map'
        if key == 'text_continue':pending[0] = False
    game.on_click = clicked
    flow._event()
    assert game.actions.count('text_continue') == 1 and not pending[0]


@pytest.mark.parametrize('boundary', ['stop', 'window'])
def test_reused_control_still_passes_final_input_guards(config, monkeypatch, boundary):
    game, flow = prepared(config, state='intro')
    hit = flow._control('intro_enter')
    flow._remember(hit)
    if boundary == 'stop':
        flow.runtime.control.request(StopReason.MANUAL_STOP)
    else:
        monkeypatch.setattr(flow.runtime, 'ensure_window', lambda **_: (_ for _ in ()).throw(
            StopExecution(StopReason.WINDOW_ABNORMAL)))
    with pytest.raises(StopExecution):
        flow._act('intro_enter')
    assert not game.actions
