"""Saved explorations are cleared before stage selection, without counting a run."""
from dataclasses import replace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.bootstrap import AutomationSession
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


@pytest.fixture(scope='module')
def config():
    return chaos_config()


class SavedGame(ChaosGame):
    def __init__(self, config, *, saved=True, stage=3, stuck=None, network_after=None):
        super().__init__(config, network_after=network_after, event_type='event_trap')
        self.saved = saved
        self.current_stage = stage
        self.chosen_stage = stage
        self.stage_scrolled = False
        self.stuck = stuck
        self.reads = []

    def _visible(self):
        if self.state == 'abandon_prompt':
            return [key for key in ('abandon_prompt', 'abandon_confirm') if key != self.stuck]
        if self.state == 'date':
            return ['date_change_title', 'date_change_close', 'lobby_research', 'lobby_abandon']
        if self.state == 'signin':
            return ['daily_signin_confirm']
        visible = super()._visible()
        if self.state == 'hall' and self.saved:
            visible.remove('start_exploration')
            visible.append('lobby_abandon')
        return visible

    def control(self, frame, key, region=None):
        self.reads.append((frame['state'], key, self.captures))
        return super().control(frame, key, region)

    def click(self, window, point):
        if self.network_remaining:
            return super().click(window, point)
        key = self.points.get(point)
        transitions = {'lobby_abandon': ('hall', 'abandon_prompt'),
                       'abandon_confirm': ('abandon_prompt', 'journey'),
                       'date_change_close': ('date', 'signin'),
                       'daily_signin_confirm': ('signin', 'home')}
        if key in transitions:
            before, after = transitions[key]
            assert self.state == before
            self.actions.append(key)
            if self.stuck != key+'_click':
                self.state = after
            if key == self.network_after and not self.network_injected:
                self.network_remaining, self.network_injected = 4, True
            if self.on_click:
                self.on_click(self, key)
            return
        if key == 'journey_close' and self.saved:
            self.actions.append(key)
            if self.stuck != 'journey_close_click':
                self.state = 'hall'
                self.saved = self.stuck == 'saved_after_close'
            if key == self.network_after and not self.network_injected:
                self.network_remaining, self.network_injected = 4, True
            if self.on_click:
                self.on_click(self, key)
            return
        if key == 'start_exploration' or self.state == 'hall' and key == 'open_stage':
            assert not self.saved, 'saved exploration must be cleared first'
        return super().click(window, point)


def prepared(game):
    stop = StopController()
    publisher = SnapshotPublisher(RuntimeSnapshot.chaos_run('saved-exploration', 1), lambda _: None)
    flow = ChaosFlow(game.config, game.dependencies(), stop, publisher)
    flow.runtime.prepare()
    game.state = 'hall'
    return flow, stop


def test_empty_lobby_has_no_cleanup_inputs_or_stage_checks(config):
    game = SavedGame(config, saved=False)
    flow, _ = prepared(game)
    flow._prepare_entry_lobby()
    assert game.actions == [] and game.captures == 3
    stage_keys = set(config.chaos.stage_selection['lobby_scenes'].values())
    assert not any(key in stage_keys for _, key, _ in game.reads)
    assert flow.stages.take_start() is None


@pytest.mark.parametrize('stage', [1, 3])
def test_cleanup_precedes_stage_selection_and_new_round(config, stage):
    game = SavedGame(config, stage=stage)
    flow, _ = prepared(game)
    flow._prepare_entry_lobby()
    assert game.actions == ['lobby_abandon', 'abandon_confirm', 'journey_close']
    assert flow.state is None
    assert flow.runtime.publisher.snapshot.rounds_completed == flow.runtime.publisher.snapshot.defeats == 0
    assert flow.stages.take_start() is None
    flow.state = RoundState.for_lineup(config.chaos.lineup)
    flow._start_round()
    assert game.state == 'map' and game.floor == 1
    assert game.actions.count('open_stage') == (stage != 1)
    assert game.actions.index('journey_close') < game.actions.index('start_exploration')
    if stage != 1:
        assert game.actions.index('journey_close') < game.actions.index('open_stage')


@pytest.mark.parametrize('stage', [1, 3])
def test_startup_clears_saved_exploration_once_and_completes_requested_rounds(config, stage):
    game = SavedGame(config, stage=stage)
    snapshots = []
    final = AutomationSession(config, game.dependencies(), StopController(), snapshots.append).run_chaos(2)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    assert final.rounds_completed == 2 and final.defeats == 0 and game.state == 'home'
    assert game.actions[:6] == ['home_battle', 'exploration_card', 'intro_enter',
                               'lobby_abandon', 'abandon_confirm', 'journey_close']
    assert game.actions.count('lobby_abandon') == game.actions.count('abandon_confirm') == 1
    assert game.actions.count('journey_close') == 3
    assert game.actions.count('start_exploration') == 2
    assert game.actions.count('open_stage') == (stage != 1)
    assert game.completed == 2


@pytest.mark.parametrize('stuck,actions', [
    ('lobby_abandon_click', ['lobby_abandon', 'lobby_abandon']),
    ('abandon_prompt', ['lobby_abandon']),
    ('abandon_confirm', ['lobby_abandon']),
    ('abandon_confirm_click', ['lobby_abandon', 'abandon_confirm']),
    ('journey_close_click', ['lobby_abandon', 'abandon_confirm', 'journey_close', 'journey_close']),
    ('saved_after_close', ['lobby_abandon', 'abandon_confirm', 'journey_close']),
])
def test_missing_or_unresponsive_pages_stop_without_replay(config, stuck, actions):
    game = SavedGame(config, stuck=stuck)
    flow, _ = prepared(game)
    with pytest.raises(StopExecution) as error:
        flow._prepare_entry_lobby()
    assert error.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert game.actions == actions
    assert flow.runtime.publisher.snapshot.rounds_completed == flow.runtime.publisher.snapshot.defeats == 0


@pytest.mark.parametrize('network_after', ['lobby_abandon', 'abandon_confirm', 'journey_close'])
def test_recovery_preserves_submitted_actions(config, network_after):
    game = SavedGame(config, network_after=network_after)
    flow, _ = prepared(game)
    flow._prepare_entry_lobby()
    assert game.actions.count('network_retry') == 4
    assert [key for key in game.actions if key != 'network_retry'] == ['lobby_abandon', 'abandon_confirm', 'journey_close']
    assert not game.saved


@pytest.mark.parametrize('mode', ['unknown', 'conflicting', 'prompt_over_lobby'])
def test_uncertain_lobby_never_authorizes_input(config, monkeypatch, mode):
    game = SavedGame(config)
    flow, _ = prepared(game)
    visible = game._visible
    def uncertain():
        return [] if mode == 'unknown' else visible()+['start_exploration' if mode == 'conflicting' else 'abandon_prompt']
    monkeypatch.setattr(game, '_visible', uncertain)
    with pytest.raises(StopExecution) as error:
        flow._prepare_entry_lobby()
    assert error.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert game.actions == []


@pytest.mark.parametrize('phase', ['hall', 'abandon_prompt', 'journey'])
@pytest.mark.parametrize('failure', ['manual', 'window'])
def test_stop_and_window_guards_block_next_input(config, phase, failure):
    game = SavedGame(config)
    flow, stop = prepared(game)
    def interrupt(g):
        if g.state != phase:
            return
        if failure == 'manual':
            stop.request(StopReason.MANUAL_STOP)
        else:
            windows = flow.runtime.deps.windows
            bounds = windows.state.client_bounds
            windows.state = replace(windows.state, client_bounds=replace(bounds, x=bounds.x+1))
    game.on_capture = interrupt
    with pytest.raises(StopExecution) as error:
        flow._prepare_entry_lobby()
    assert error.value.reason is (StopReason.MANUAL_STOP if failure == 'manual' else StopReason.WINDOW_ABNORMAL)
    assert game.actions == {'hall': [], 'abandon_prompt': ['lobby_abandon'],
                            'journey': ['lobby_abandon', 'abandon_confirm']}[phase]


@pytest.mark.parametrize('phase', ['hall', 'abandon_prompt'])
def test_date_popup_preempts_underlying_abandonment(config, phase):
    game = SavedGame(config)
    flow, _ = prepared(game)
    game.on_capture = lambda g: setattr(g, 'state', 'date') if g.state == phase else None
    flow._prepare_entry_lobby()
    assert flow._at_home_after_reset and game.state == 'home'
    expected = [] if phase == 'hall' else ['lobby_abandon']
    assert game.actions == expected+['date_change_close', 'daily_signin_confirm']


def test_confirmation_needs_three_consecutive_complete_new_frames(config, monkeypatch):
    game = SavedGame(config)
    flow, _ = prepared(game)
    control = game.control
    prompt_frames = []
    def flicker(frame, key, region=None):
        if frame['state'] == 'abandon_prompt' and key == 'abandon_prompt':
            prompt_frames.append(game.captures)
            if len(prompt_frames) == 3:
                return None
        return control(frame, key, region)
    monkeypatch.setattr(game, 'control', flicker)
    flow._prepare_entry_lobby()
    # One prompt frame is consumed by the standard post-click settling delay.
    assert len(set(prompt_frames)) >= 6
    assert game.actions == ['lobby_abandon', 'abandon_confirm', 'journey_close']


def test_slow_confirmation_cannot_click_after_timeout(config, monkeypatch):
    game = SavedGame(config)
    flow, _ = prepared(game)
    log = flow.runtime.deps.logger.event
    def slow_log(name, **fields):
        log(name, **fields)
        if name == 'chaos_confirmed' and fields.get('stage') == 'entry_abandon_confirmation':
            game.clock.sleep(16.)
    monkeypatch.setattr(flow.runtime.deps.logger, 'event', slow_log)
    with pytest.raises(StopExecution) as error:
        flow._prepare_entry_lobby()
    assert error.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert game.actions == ['lobby_abandon']
