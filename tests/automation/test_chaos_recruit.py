"""Recruitment confirms the selected identity and submit button in one window."""
from dataclasses import replace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.types import Point, Rect
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


@pytest.fixture
def recruitment():
    game = ChaosGame(chaos_config())
    flow = ChaosFlow(game.config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('recruit', 1), lambda _: None))
    flow.runtime.prepare()
    game.state, game.hero, game.ticket = 'recruit', 'twins', 'thief'
    return game, flow


def test_initial_soul_ticket_recruits_fire_ivanna_and_records_new_roster(recruitment):
    game, flow = recruitment
    flow.state = RoundState()
    game.state = 'hall'
    submitted = []

    def clicked(g, key):
        if key == 'recruit_confirm':
            submitted.append((g.ticket, g.hero, set(g.attributes)))

    game.on_click = clicked
    flow._start_round()
    assert submitted == [('thief', 'twins', {'dark'}), ('soul', 'ivanna', {'fire'}),
                         ('warrior', 'xiaochun', {'water'}), ('knight', 'arowell', {'light'})]
    assert game.state == 'map'
    assert game.recruited == {'twins', 'ivanna', 'xiaochun', 'arowell'}
    assert flow.state.ranks == dict.fromkeys(('twins', 'ivanna', 'xiaochun', 'arowell'), 1)
    assert not any('peini' in action for action in game.actions)


@pytest.mark.parametrize('missing', ['hero_ivanna', 'selected_name_ivanna', 'hero_selected_border'])
def test_ivanna_selection_accepts_either_evidence_without_rechecking_list(recruitment, monkeypatch, missing):
    game, flow = recruitment
    game.hero, game.ticket = 'ivanna', 'soul'
    original = game.control
    monkeypatch.setattr(game, 'control', lambda frame, key, region=None:
                        None if frame['selected'] and key == missing else original(frame, key, region))
    flow.recruitment.recruit('ivanna')
    assert game.actions == ['hero_ivanna', 'recruit_confirm'] and game.recruited == {'ivanna'}


def test_three_joint_frames_use_the_last_button_anchor(recruitment, monkeypatch):
    game, flow = recruitment
    original, original_click = game.control, game.click
    selected_frames, button_frames, clicked = [], [], []

    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if frame['selected'] and key == 'selected_name_twins':
            selected_frames.append(frame)
        if frame['selected'] and key == 'recruit_confirm' and hit:
            button_frames.append(frame)
            x = 1800 + len(button_frames) - 1
            hit = replace(hit, bounds=Rect(x, 1100, 30, 20), anchor=Point(x + 15, 1110))
            game.points[hit.anchor] = key
        return hit

    def click(window, point):
        clicked.append(point)
        original_click(window, point)

    monkeypatch.setattr(game, 'control', control)
    monkeypatch.setattr(game, 'click', click)
    flow.recruitment.recruit('twins')
    assert len(selected_frames) == len(button_frames) == 3
    assert all(a is b for a, b in zip(selected_frames, button_frames))
    assert len({id(f) for f in selected_frames}) == 3
    assert clicked[-1] == Point(1817, 1110)
    assert game.actions == ['hero_twins', 'recruit_confirm']
    assert game.recruited == {'twins'}


@pytest.mark.parametrize('missing', [('hero_selected_border', 'selected_name_twins'), ('recruit_confirm',)])
def test_missing_selection_or_button_never_submits(recruitment, monkeypatch, missing):
    game, flow = recruitment
    original = game.control
    monkeypatch.setattr(game, 'control', lambda frame, key, region=None:
                        None if frame['selected'] and key in missing else original(frame, key, region))
    with pytest.raises(StopExecution) as error:
        flow.recruitment.recruit('twins')
    assert error.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert game.actions == ['hero_twins'] and not game.recruited


def test_button_and_selection_must_agree_in_three_consecutive_frames(recruitment, monkeypatch):
    game, flow = recruitment
    original = game.control
    samples = []

    def control(frame, key, region=None):
        if frame['selected']:
            if key == 'selected_name_twins':
                samples.append(frame)
            # First two frames have selection only; next two have button only.
            if (key == 'recruit_confirm' and len(samples) <= 2
                    or key in ('hero_selected_border', 'selected_name_twins') and 3 <= len(samples) <= 4):
                return None
        return original(frame, key, region)

    monkeypatch.setattr(game, 'control', control)
    flow.recruitment.recruit('twins')
    assert len(samples) == 7
    assert game.actions == ['hero_twins', 'recruit_confirm']


def test_recovery_discards_pre_recovery_joint_stability(recruitment, monkeypatch):
    game, flow = recruitment
    original = game.control
    probes = []

    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if frame['selected'] and key == 'recruit_confirm' and hit:
            probes.append(flow.runtime.network_recovery_generation)
            if len(probes) == 2:
                game.network_remaining = 4
        return hit

    monkeypatch.setattr(game, 'control', control)
    flow.recruitment.recruit('twins')
    assert probes == [0, 0, 1, 1, 1]
    assert game.actions.count('recruit_confirm') == 1
    assert game.actions.count('network_retry') == 4


def test_manual_stop_at_joint_confirmation_prevents_submission(recruitment, monkeypatch):
    game, flow = recruitment
    original = game.control
    probes = []

    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if frame['selected'] and key == 'recruit_confirm' and hit:
            probes.append(frame)
            if len(probes) == 3:
                flow.runtime.control.request(StopReason.MANUAL_STOP)
        return hit

    monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution) as error:
        flow.recruitment.recruit('twins')
    assert error.value.reason is StopReason.MANUAL_STOP
    assert game.actions == ['hero_twins'] and not game.recruited


def test_name_priority_never_rechecks_list_or_border_after_click(recruitment, monkeypatch):
    game, flow = recruitment
    original = game.control
    def control(frame, key, region=None):
        if frame['selected'] and key in ('hero_twins', 'hero_selected_border'):
            raise AssertionError('name branch must not rescan list or border')
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    flow.recruitment.recruit('twins')
    assert game.actions == ['hero_twins', 'recruit_confirm']


def test_missing_preclick_identity_never_clicks(recruitment, monkeypatch):
    game, flow = recruitment
    original = game.control
    monkeypatch.setattr(game, 'control', lambda f,k,region=None:
                        None if k == 'hero_twins' else original(f,k,region))
    with pytest.raises(StopExecution):flow.recruitment.recruit('twins')
    assert game.actions == []


@pytest.mark.parametrize('target_name_also_matches', [False, True])
def test_other_known_left_name_vetoes_target_and_border(recruitment, monkeypatch, target_name_also_matches):
    game, flow = recruitment
    original = game.control
    def control(frame, key, region=None):
        if frame['selected']:
            if key == 'selected_name_xiaochun':return game._hit(key, frame)
            if key == 'selected_name_twins' and not target_name_also_matches:return None
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution):flow.recruitment.recruit('twins')
    assert game.actions == ['hero_twins']


def test_alternating_name_and_border_cannot_mix_stable_frames(recruitment, monkeypatch):
    game, flow = recruitment
    original = game.control
    samples = []
    def control(frame, key, region=None):
        if frame['selected'] and key == 'selected_name_twins':
            samples.append(frame)
            if len(samples) in (3, 4):return None
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    flow.recruitment.recruit('twins')
    assert len(samples) == 7 and game.actions.count('recruit_confirm') == 1


def test_border_fallback_cannot_use_a_different_card(recruitment, monkeypatch):
    game, flow = recruitment
    original = game.control
    def control(frame, key, region=None):
        if frame['selected']:
            if key == 'selected_name_twins':return None
            if key == 'hero_selected_border':
                return replace(game._hit(key, frame), bounds=Rect(1250, 180, 560, 173))
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution):flow.recruitment.recruit('twins')
    assert game.actions == ['hero_twins']


@pytest.mark.parametrize('name_available', [True, False])
def test_rank_selection_and_submit_share_one_stable_window(recruitment, monkeypatch, name_available):
    game, flow = recruitment
    flow.state = RoundState()
    flow.state.node_kind = game.kind = 'rest'
    game.state, game.hero = 'rank', 'xiaochun'
    original = game.control
    name_frames, button_frames = [], []
    def control(frame, key, region=None):
        if frame['selected'] and frame['state'] == 'rank':
            assert key != 'hero_xiaochun'
            if key == 'selected_name_xiaochun':
                name_frames.append(frame)
                if not name_available:return None
            if key == 'rank_confirm':button_frames.append(frame)
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    flow._rank_up('xiaochun')
    assert len(name_frames) == len(button_frames) == 3
    assert all(a is b for a,b in zip(name_frames,button_frames))
    assert game.actions.count('rank_confirm') == 1 and flow.state.ranks['xiaochun'] == 2
