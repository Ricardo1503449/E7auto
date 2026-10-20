import pytest
from dataclasses import replace

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.features.chaos.lineups import RecruitmentPolicy
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController
from e7auto.bootstrap import AutomationSession
from tests.helpers.chaos_game import ChaosGame, chaos_config


def setup(lineup_id):
    game = ChaosGame(chaos_config(lineup_id))
    flow = ChaosFlow(game.config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('lineups', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState.for_lineup(game.config.chaos.lineup)
    return game, flow


@pytest.mark.parametrize('lineup_id,expected', [
    ('dark_twins', [('thief', 'twins', {'dark'}), ('soul', 'ivanna', {'fire'}),
                    ('warrior', 'xiaochun', {'water'}), ('knight', 'arowell', {'light'})]),
    ('budget', [('thief', 'teacher', {'dark'}), ('soul', 'ivanna', {'fire'}),
                ('warrior', 'xiaochun', {'water'}), ('knight', 'shadow_rose', {'dark'})]),
])
def test_lineup_initial_recruitment_and_rank_state(lineup_id, expected):
    game, flow = setup(lineup_id)
    game.state = 'hall'
    submitted = []
    game.on_click = lambda g, key: submitted.append((g.ticket, g.hero, set(g.attributes))) if key == 'recruit_confirm' else None
    flow._start_round()
    assert submitted == expected
    assert game.state == 'map'
    assert flow.state.ranks == dict.fromkeys((h[1] for h in expected), 1)


@pytest.mark.parametrize('lineup_id,priority', [
    ('dark_twins', ('xiaochun', 'twins', 'ivanna')),
    ('budget', ('xiaochun', 'shadow_rose', 'teacher')),
])
def test_camp_uses_each_priority_then_skips_without_selecting_fourth_hero(lineup_id, priority):
    game, flow = setup(lineup_id)
    for hero in priority:
        flow.state.ranks[hero] = game.ranks[hero] = 4
    for hero in priority:
        assert flow.state.next_rank_hero() == hero
        game.state, game.kind, flow.state.node_kind = 'camp', 'rest', 'rest'
        flow._camp()
        assert flow.state.ranks[hero] == game.ranks[hero] == 5
    assert flow.state.next_rank_hero() is None
    before = len(game.actions)
    game.state, game.kind = 'camp', 'rest'
    flow._camp()
    assert game.actions[before:] == ['camp_leave']
    assert sum(rank == 1 for rank in flow.state.ranks.values()) == 1


@pytest.mark.parametrize('lineup_id,last', [('dark_twins', 'ivanna'), ('budget', 'teacher')])
def test_event_uses_same_third_priority_as_camp(lineup_id, last):
    game, flow = setup(lineup_id)
    for hero in flow.state.rank_priority[:2]:
        flow.state.ranks[hero] = game.ranks[hero] = 5
    game.state, game.kind, flow.state.node_kind = 'event', 'event', 'event'
    game.event_type = 'event_mushroom'
    flow._event()
    assert 'hero_' + last in game.actions
    assert flow.state.ranks[last] == 2


def test_budget_skips_special_voucher_without_recruitment_or_swap():
    game, flow = setup('budget')
    game.state, game.kind, game.floor = 'claimed', 'boss', 2
    game.recruited = set(flow.state.ranks)
    flow.state.floor = flow.state.battle_floor = 2
    flow._voucher()
    assert not game.actions and not flow.state.pending_teacher
    flow._continue(3, allow_unclaimed=True)
    assert game.actions == ['continue_exploration', 'unclaimed_confirm']
    assert game.state == 'map' and game.floor == 3


def test_dark_twins_keeps_special_recruitment_without_initial_teacher_name_guard():
    game, flow = setup('dark_twins')
    game.state, game.kind, game.floor = 'claimed', 'boss', 2
    game.recruited = set(flow.state.ranks)
    flow.state.floor = flow.state.battle_floor = 2
    assert 'selected_name_teacher' not in flow.settings.scenes
    flow._voucher()
    assert game.actions.count('recruit_confirm') == 1
    assert game.state == 'teacher_done' and flow.state.pending_teacher
    assert flow.state.ranks['teacher'] == 1


def test_budget_two_rounds_never_recruit_midway_or_replace():
    game = ChaosGame(chaos_config('budget'))
    session = AutomationSession(game.config, game.dependencies(), StopController(), lambda _: None)
    final = session.run_chaos(2)
    assert final.stop_reason == StopReason.CHAOS_COMPLETE, game.actions
    assert final.rounds_completed == game.completed == 2
    assert game.actions.count('recruit_confirm') == 8
    assert game.actions.count('hero_teacher') == 2
    assert not set(game.actions) & {'ticket_all', 'bench_teacher', 'replace_button'}
    assert set(game.ranks) == {'teacher', 'ivanna', 'xiaochun', 'shadow_rose'}


def test_loaded_recruitment_resources_do_not_enable_disabled_policy(monkeypatch):
    game, flow = setup('dark_twins')
    flow.settings = replace(flow.settings, lineup=replace(
        flow.settings.lineup, recruitment_policy=RecruitmentPolicy.NONE))
    assert 'ticket_all' in flow.settings.scenes and 'replace_button' in flow.settings.scenes
    game.state, game.kind, game.floor = 'claimed', 'boss', 2
    game.recruited = set(flow.state.ranks)
    flow.state.floor = flow.state.battle_floor = 2
    flow._voucher()
    assert not game.actions and not flow.state.pending_teacher
    flow._continue(3, allow_unclaimed=True)
    assert game.actions == ['continue_exploration', 'unclaimed_confirm']
    game.state, game.kind = 'prepare', 'normal'
    flow.state.pending_teacher = True
    def forbidden_swap():
        raise AssertionError('Disabled policy must not replace heroes even with a pending flag')
    monkeypatch.setattr(flow, '_swap_teacher', forbidden_swap)
    flow._fight('prepare_normal', ('victory',))
    assert 'start_battle' in game.actions
    assert not set(game.actions) & {'ticket_all', 'hero_teacher', 'bench_teacher', 'replace_button'}
