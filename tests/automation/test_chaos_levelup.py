"""Upgrade-first probing, same-frame VICTORY fallback and joint popup evidence."""
from dataclasses import replace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import BattleDefeated, ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


class LevelupGame(ChaosGame):
    def __init__(self, config, *, signal_delay=2., popup_delay=0., result_delay=.6,
                 fade_delay=0., victory_duration=None, **kwargs):
        super().__init__(config, endless_battle=True, **kwargs)
        self.signal_delay, self.popup_delay = signal_delay, popup_delay
        self.result_delay, self.fade_delay = result_delay, fade_delay
        self.victory_duration = victory_duration
        self.closed_at = None
        self.observations = []
        self.popup_controls = ('levelup', 'levelup_close')
        self.show_victory, self.keep_popup = True, False

    @property
    def result_key(self):
        return 'victory' if self.kind == 'normal' else self.kind+'_choose_reward'

    def capture_client(self, window, bounds):
        if self.state == 'battle' and self.clock.now-self.battle_started >= self.signal_delay:
            self.state = 'settlement'
        if (self.state == 'settlement' and self.closed_at is None and self.popup_delay is not None
                and self.clock.now-self.battle_started >= self.signal_delay+self.popup_delay):
            self.state = 'levelup'
        return super().capture_client(window, bounds)

    def _visible(self):
        if self.state == 'levelup':
            # The real popup obscures/dims VICTORY, regardless of battle kind.
            visible = list(self.popup_controls)+['continue_exploration']
            if self.kind != 'normal':visible.append(self.result_key)
        elif self.state == 'post_levelup':
            visible = []
            if self.keep_popup or self.clock.now-self.closed_at < self.fade_delay:
                visible += list(self.popup_controls)
            if self.clock.now-self.closed_at >= self.result_delay:
                visible += [self.result_key, 'continue_exploration']
        else:
            visible = super()._visible()
            if (self.state == 'settlement' and self.victory_duration is not None
                    and self.clock.now-self.battle_started >= self.signal_delay+self.victory_duration):
                visible = [key for key in visible if key != 'victory']
        return visible if self.show_victory else [k for k in visible if k != 'victory']

    def control(self, frame, key, region=None):
        hit = super().control(frame, key, region)
        self.observations.append((key, self.clock.now, hit is not None, self.captures, frame['state']))
        return hit

    def click(self, window, point):
        key = self.points.get(point)
        if self.network_remaining or key != 'levelup_close':
            if not self.network_remaining and key in ('continue_exploration', self.result_key):
                assert self.state != 'levelup', 'Cannot click a covered settlement control'
            return super().click(window, point)
        assert self.state == 'levelup' and self.closed_at is None
        self.actions.append(key)
        self.closed_at, self.state = self.clock.now, 'post_levelup'
        if self.network_after == key and not self.network_injected:
            self.network_remaining, self.network_injected = 4, True
        if self.on_click:self.on_click(self, key)


def levelup_flow(*, kind='normal', floor=1, timeout_ms=15000, auto_confirmed=True, **kwargs):
    config = chaos_config()
    config = replace(config, chaos=replace(config.chaos, timing=dict(
        config.chaos.timing, battle_timeout_ms=timeout_ms)))
    game = LevelupGame(config, **kwargs)
    stop = StopController()
    flow = ChaosFlow(config, game.dependencies(), stop,
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('levelup', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState(floor=floor, battle_floor=floor, node_kind=kind,
                            auto_confirmed=auto_confirmed)
    game.state, game.kind, game.floor, game.auto = 'prepare', kind, floor, True
    return game, flow, stop


def fight(game, flow):
    return flow._fight('prepare_'+game.kind, ('levelup', game.result_key))


def phases(flow):
    return [f for e, f in flow.runtime.deps.logger.events if e == 'chaos_battle_settlement']


@pytest.mark.parametrize('kind,floor', [('normal', 1), ('elite', 2), ('boss', 1), ('boss', 2)])
@pytest.mark.parametrize('popup_delay', [0., .6, 2.8])
def test_popup_closes_early_with_one_joint_three_frame_confirmation(kind, floor, popup_delay):
    game, flow, _ = levelup_flow(kind=kind, floor=floor, popup_delay=popup_delay)
    assert fight(game, flow).key == game.result_key
    assert game.actions == ['start_battle', 'levelup_close']
    hits = [(k, t, frame) for k, t, hit, frame, _ in game.observations
            if hit and t <= game.closed_at and k in ('levelup', 'levelup_close')]
    titles = [(t, frame) for k, t, frame in hits if k == 'levelup']
    buttons = [(t, frame) for k, t, frame in hits if k == 'levelup_close']
    assert titles[-3:] == buttons and len(buttons) == 3
    assert len(titles) == (4 if phases(flow)[0]['signal'] == 'levelup' else 3)
    assert game.closed_at-buttons[0][0] == pytest.approx(.2)
    records = {p['phase']: p for p in phases(flow)}
    assert records['close_submitted']['elapsed_ms']-records['signal']['elapsed_ms'] < 3000
    assert records['popup_stable']['phase_elapsed_ms'] == pytest.approx(200)
    after = [t for k, t, _, _, _ in game.observations if k == 'levelup' and t > game.closed_at]
    assert after[0]-game.closed_at == pytest.approx(.3)
    assert .6-1e-6 <= game.clock.now-game.closed_at <= .9


@pytest.mark.parametrize('kind', ['normal', 'elite', 'boss'])
@pytest.mark.parametrize('auto_confirmed', [True, False])
def test_battle_probes_levelup_then_victory_once_per_second_on_same_guarded_frame(kind, auto_confirmed):
    game, flow, _ = levelup_flow(kind=kind, signal_delay=8.2, popup_delay=None,
                                auto_confirmed=auto_confirmed)
    fight(game, flow)
    signal = next(t for k, t, hit, _, _ in game.observations if k == 'victory' and hit)
    probes = [(t, frame) for k, t, _, frame, _ in game.observations if k == 'victory' and t <= signal]
    assert all(b[0]-a[0] == pytest.approx(1.) for a, b in zip(probes, probes[1:]))
    assert len({frame for _, frame in probes}) == len(probes)
    assert not any(k in ('levelup_close', 'elite_choose_reward', 'boss_choose_reward')
                   for k, t, _, _, _ in game.observations if t < signal)
    for index, (key, _, _, frame, _) in enumerate(game.observations):
        if key == 'victory' and frame <= probes[-1][1]:
            prior = game.observations[index-1]
            assert prior[0] == 'levelup' and not prior[2] and prior[3] == frame
    guarded = {frame for k, _, _, frame, _ in game.observations if k == 'failed'}
    assert all(frame in guarded for _, frame in probes)
    summary, = [f for e, f in flow.runtime.deps.logger.events if e == 'chaos_battle_settlement_summary']
    assert summary['victory_checks'] == len(probes) and summary['victory_match_ms'] >= 0
    assert summary['levelup_checks'] == len(probes) and summary['levelup_match_ms'] >= 0
    if auto_confirmed:
        assert all(b[1]-a[1] == 1 for a, b in zip(probes, probes[1:]))


@pytest.mark.parametrize('kind', ['normal', 'elite', 'boss'])
def test_no_popup_waits_full_window_and_needs_fresh_result_evidence(kind):
    game, flow, _ = levelup_flow(kind=kind, popup_delay=None)
    fight(game, flow)
    signal, final = phases(flow)
    assert signal['phase'] == 'signal' and final['phase'] == 'result_confirmed'
    assert final['elapsed_ms']-signal['elapsed_ms'] == pytest.approx(3200)
    assert game.actions == ['start_battle']


def test_next_battle_starts_a_new_one_second_probe_and_observation_window():
    game, flow, _ = levelup_flow()
    fight(game, flow)
    game.popup_delay, game.state = None, 'prepare'
    game.observations.clear()
    fight(game, flow)
    probes = [t for k, t, _, _, _ in game.observations if k == 'victory']
    assert probes[0]-game.battle_started == pytest.approx(.3)
    assert probes[1]-probes[0] == pytest.approx(1.)
    assert game.actions == ['start_battle', 'levelup_close', 'start_battle']
    assert len([p for p in phases(flow) if p['phase'] == 'signal']) == 2


@pytest.mark.parametrize('controls', [('levelup',), ('levelup_close',)])
def test_partial_popup_blocks_result_past_end_of_window(controls):
    game, flow, _ = levelup_flow(kind='elite', popup_delay=2.8, timeout_ms=7000)
    game.popup_controls = controls
    with pytest.raises(StopExecution) as error:fight(game, flow)
    assert error.value.reason is StopReason.CHAOS_BATTLE_TIMEOUT
    assert game.actions == ['start_battle']


def test_partial_popup_can_finish_after_three_second_window():
    game, flow, _ = levelup_flow(popup_delay=2.8)
    game.popup_controls = ('levelup',)
    def complete(g):
        if g.clock.now-g.battle_started >= 6:g.popup_controls = ('levelup', 'levelup_close')
    game.on_capture = complete
    fight(game, flow)
    assert game.closed_at-game.battle_started >= 6.2-1e-6


def test_joint_evidence_resets_when_only_title_position_changes(monkeypatch):
    game, flow, _ = levelup_flow()
    original, count = game.control, 0
    def detect(frame, key, region=None):
        nonlocal count
        hit = original(frame, key, region)
        if key == 'levelup' and hit and phases(flow):
            count += 1
            if count == 2:return replace(hit, bounds=replace(hit.bounds, x=hit.bounds.x+8))
        return hit
    monkeypatch.setattr(game, 'control', detect)
    fight(game, flow)
    first = next(t for k, t, hit, _, _ in game.observations if k == 'levelup' and hit)
    assert game.closed_at-first == pytest.approx(.4)


@pytest.mark.parametrize('auto_confirmed', [True, False])
@pytest.mark.parametrize('popup_delay', [None, 0.])
def test_recovery_resumes_without_victory_banner(auto_confirmed, popup_delay):
    game, flow, _ = levelup_flow(kind='elite', auto_confirmed=auto_confirmed,
                                popup_delay=popup_delay, network_after='start_battle')
    game.show_victory = False
    assert fight(game, flow).key == game.result_key
    assert 'enable_auto' not in game.actions
    assert game.actions.count('levelup_close') == int(popup_delay is not None)
    assert phases(flow)[0]['signal'] == ('levelup' if popup_delay is not None else 'elite_choose_reward')


def test_recovery_during_observation_resets_joint_evidence_without_restarting_window():
    game, flow, _ = levelup_flow()
    def disconnect(g):
        hits = [t for k, t, hit, _, _ in g.observations if k == 'levelup_close' and hit]
        if len(hits) == 2 and not g.network_injected:
            g.network_remaining, g.network_injected = 4, True
    game.on_capture = disconnect
    fight(game, flow)
    hits = [t for k, t, hit, _, _ in game.observations if k == 'levelup_close' and hit and t <= game.closed_at]
    assert len(hits) == 5 and hits[-1]-hits[-3] == pytest.approx(.2)
    assert game.actions.count('levelup_close') == 1
    assert phases(flow)[-1]['wall_elapsed_ms']-phases(flow)[-1]['elapsed_ms'] >= 40000


def test_recovery_pauses_no_popup_window_without_resetting_its_start():
    game, flow, _ = levelup_flow(popup_delay=None)
    def disconnect(g):
        if phases(flow) and not g.network_injected and g.clock.now-g.battle_started >= 3.3:
            g.network_remaining, g.network_injected = 4, True
    game.on_capture = disconnect
    fight(game, flow)
    signal, final = phases(flow)
    assert final['elapsed_ms']-signal['elapsed_ms'] == pytest.approx(3200)
    assert final['wall_elapsed_ms']-final['elapsed_ms'] >= 40000


@pytest.mark.parametrize('network_after', [None, 'levelup_close'])
def test_popup_fade_blocks_result_and_close_is_not_replayed(network_after):
    game, flow, _ = levelup_flow(kind='elite', result_delay=0., fade_delay=.8, network_after=network_after)
    fight(game, flow)
    assert game.actions.count('levelup_close') == 1
    assert game.clock.now-game.closed_at >= 1.-1e-6


def test_residual_close_hint_alone_blocks_result_without_repeated_close():
    game, flow, _ = levelup_flow(kind='elite', result_delay=0., timeout_ms=6000)
    game.keep_popup = True
    game.on_click = lambda g, key: setattr(g, 'popup_controls', ('levelup_close',)) if key == 'levelup_close' else None
    with pytest.raises(StopExecution) as error:fight(game, flow)
    assert error.value.reason is StopReason.CHAOS_BATTLE_TIMEOUT
    assert game.actions == ['start_battle', 'levelup_close']


@pytest.mark.parametrize('timeout_ms', [1100, 3000, 5000])
def test_original_battle_deadline_bounds_signal_and_observation(timeout_ms):
    game, flow, _ = levelup_flow(timeout_ms=timeout_ms, popup_delay=None)
    with pytest.raises(StopExecution) as error:fight(game, flow)
    assert error.value.reason is StopReason.CHAOS_BATTLE_TIMEOUT
    assert timeout_ms/1000 <= game.clock.now-game.battle_started <= timeout_ms/1000+.4
    assert game.actions == ['start_battle']


def test_missing_result_after_close_keeps_original_deadline():
    game, flow, _ = levelup_flow(timeout_ms=5000, result_delay=999)
    with pytest.raises(StopExecution) as error:fight(game, flow)
    assert error.value.reason is StopReason.CHAOS_BATTLE_TIMEOUT
    assert game.clock.now-game.battle_started == pytest.approx(5.3)
    assert game.actions == ['start_battle', 'levelup_close']


@pytest.mark.parametrize('when', ['battle', 'observation', 'after_close'])
def test_manual_stop_prevents_following_inputs(when):
    game, flow, stop = levelup_flow(popup_delay=2., result_delay=999)
    def interrupt(g):
        if (when == 'battle' and g.state == 'battle'
                or when == 'observation' and bool(phases(flow))
                or when == 'after_close' and g.closed_at is not None):
            stop.request(StopReason.MANUAL_STOP)
    game.on_capture = interrupt
    with pytest.raises(StopExecution) as error:fight(game, flow)
    assert error.value.reason is StopReason.MANUAL_STOP
    assert game.actions == ['start_battle']+(['levelup_close'] if when == 'after_close' else [])


def test_failed_preempts_observation_but_is_not_scanned_after_close(monkeypatch):
    game, flow, _ = levelup_flow(popup_delay=2.)
    game.on_capture = lambda g: setattr(g, 'state', 'failed') if phases(flow) else None
    with pytest.raises(BattleDefeated):fight(game, flow)
    assert game.actions == ['start_battle']
    game, flow, _ = levelup_flow()
    original = game.control
    def control(frame, key, region=None):
        assert not (game.closed_at is not None and key == 'failed')
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    fight(game, flow)


def test_single_trigger_does_not_authorize_missing_final_control(monkeypatch):
    game, flow, _ = levelup_flow(kind='elite', popup_delay=None, timeout_ms=6000)
    original = game.control
    monkeypatch.setattr(game, 'control', lambda f, k, r=None: None if k == 'elite_choose_reward' else original(f, k, r))
    with pytest.raises(StopExecution):fight(game, flow)
    assert game.actions == ['start_battle']


@pytest.mark.parametrize('kind,floor,key', [('event', 1, 'event_reward_close'), ('boss', 3, 'journey_result')])
def test_special_battles_keep_original_five_second_result_poll(kind, floor, key):
    game, flow, _ = levelup_flow(kind=kind, floor=floor, popup_delay=None, signal_delay=999)
    game.endless_battle = False
    assert flow._fight('prepare_'+kind, (key,)).key == key
    first = next(t for k, t, _, _, _ in game.observations if k == key)
    assert first-game.battle_started == pytest.approx(5.3)
    assert not any(k in ('levelup', 'levelup_close', 'victory') for k, _, _, _, _ in game.observations)
    assert not phases(flow)


def test_recovery_with_transition_frames_still_resumes_without_victory():
    game, flow, _ = levelup_flow(kind='elite', popup_delay=None, signal_delay=42.,
                                network_after='start_battle')
    game.show_victory = False
    assert fight(game, flow).key == 'elite_choose_reward'
    assert phases(flow)[0]['signal'] == 'elite_choose_reward'
    assert game.actions.count('network_retry') == 4


def test_old_recovery_does_not_enable_extra_scans_in_next_battle():
    game, flow, _ = levelup_flow(kind='elite', network_after='start_battle')
    fight(game, flow)
    game.popup_delay, game.state, game.closed_at = None, 'prepare', None
    game.observations.clear()
    fight(game, flow)
    signal = next(t for k, t, hit, _, _ in game.observations if k == 'victory' and hit)
    assert not any(k in ('levelup_close', 'elite_choose_reward')
                   for k, t, _, _, _ in game.observations if t < signal)


def test_popup_appearing_during_final_result_confirmation_preempts_result():
    game, flow, _ = levelup_flow(kind='elite', popup_delay=3.4)
    assert fight(game, flow).key == 'elite_choose_reward'
    assert game.actions == ['start_battle', 'levelup_close']


def test_slow_recognition_crossing_deadline_cannot_close(monkeypatch):
    game, flow, _ = levelup_flow(timeout_ms=3000)
    original, count = game.control, 0
    def slow(frame, key, region=None):
        nonlocal count
        hit = original(frame, key, region)
        if key == 'levelup_close' and hit:
            count += 1
            if count == 3:game.clock.sleep(1.)
        return hit
    monkeypatch.setattr(game, 'control', slow)
    with pytest.raises(StopExecution) as error:fight(game, flow)
    assert error.value.reason is StopReason.CHAOS_BATTLE_TIMEOUT
    assert game.actions == ['start_battle']


@pytest.mark.parametrize('kind,floor', [('normal', 1), ('elite', 1), ('boss', 1), ('boss', 2)])
def test_early_close_rejoins_each_existing_settlement_and_map(kind, floor):
    game, flow, _ = levelup_flow(kind=kind, floor=floor, popup_delay=.6, result_delay=0.)
    flow._battle(kind)
    action = 'continue_exploration' if kind == 'normal' else game.result_key
    assert game.actions.count('levelup_close') == game.actions.count(action) == 1
    assert game.actions.index('levelup_close') < game.actions.index(action)
    assert game.state == 'map'


@pytest.mark.parametrize('kind,floor', [('normal', 1), ('elite', 2), ('boss', 1), ('boss', 2)])
@pytest.mark.parametrize('auto_confirmed', [True, False])
def test_brief_victory_missed_without_recovery_still_closes_obscuring_upgrade(kind, floor, auto_confirmed):
    game, flow, _ = levelup_flow(kind=kind, floor=floor, auto_confirmed=auto_confirmed,
                                popup_delay=.05, victory_duration=.05)
    assert fight(game, flow).key == game.result_key
    assert phases(flow)[0]['signal'] == 'levelup'
    assert game.actions == ['start_battle', 'levelup_close']
    assert flow.runtime.network_recovery_generation == 0
    assert not any(key == 'victory' and hit and when <= game.closed_at
                   for key, when, hit, _, _ in game.observations)
    assert game.closed_at-game.battle_started < 3


def test_levelup_hit_short_circuits_victory_on_the_same_frame(monkeypatch):
    game, flow, _ = levelup_flow()
    original = game._visible
    monkeypatch.setattr(game, '_visible', lambda: original()+(['victory'] if game.state == 'levelup' else []))
    fight(game, flow)
    first = next(row for row in game.observations if row[0] == 'levelup' and row[2])
    assert not any(key == 'victory' and frame == first[3] for key, _, _, frame, _ in game.observations)
    assert phases(flow)[0]['signal'] == 'levelup'


def test_single_levelup_trigger_never_authorizes_close_without_fresh_joint_evidence(monkeypatch):
    game, flow, _ = levelup_flow(timeout_ms=4000)
    original, hits = game.control, 0
    def detect(frame, key, region=None):
        nonlocal hits
        hit = original(frame, key, region)
        if key == 'levelup' and hit:
            hits += 1
            if hits > 1:return None
        return hit
    monkeypatch.setattr(game, 'control', detect)
    with pytest.raises(StopExecution) as error:fight(game, flow)
    assert error.value.reason is StopReason.CHAOS_BATTLE_TIMEOUT
    assert phases(flow)[0]['signal'] == 'levelup'
    assert game.actions == ['start_battle']


@pytest.mark.parametrize('kind,floor', [('elite', 1), ('boss', 1), ('boss', 2)])
@pytest.mark.parametrize('popup_delay', [None, .6])
def test_reward_entry_uses_last_of_three_result_frames_without_reconfirming(monkeypatch, kind, floor, popup_delay):
    game, flow, _ = levelup_flow(kind=kind, floor=floor, popup_delay=popup_delay, result_delay=0.)
    original = game.control
    samples, submitted = [], []
    def detect(frame, key, region=None):
        hit = original(frame, key, region)
        if key == game.result_key and hit is not None:
            # Sub-cell motion is stable, but the click must use the last anchor.
            hit = replace(hit, anchor=replace(hit.anchor, x=hit.anchor.x+len(samples)))
            game.points[hit.anchor] = key
            samples.append((game.clock.now, game.captures, hit.anchor))
        return hit
    monkeypatch.setattr(game, 'control', detect)
    click = game.click
    def record(window, point):
        if game.points.get(point) == game.result_key:
            submitted.append((game.clock.now, game.captures, point, len(samples)))
        click(window, point)
    monkeypatch.setattr(game, 'click', record)
    flow._battle(kind)
    assert len(submitted) == 1 and submitted[0][3] == 3
    assert len({frame for _, frame, _ in samples}) == 3
    assert submitted[0][:3] == samples[-1]
    assert game.state == 'map'


@pytest.mark.parametrize('kind', ['elite', 'boss'])
@pytest.mark.parametrize('reason', [StopReason.MANUAL_STOP, StopReason.WINDOW_ABNORMAL])
def test_reward_handoff_preserves_stop_and_window_guards(monkeypatch, kind, reason):
    game, flow, stop = levelup_flow(kind=kind, popup_delay=None)
    original = flow._fight
    def finish(*args):
        hit = original(*args)
        if reason is StopReason.MANUAL_STOP:
            stop.request(reason)
        else:
            def invalid_window(**kwargs):raise StopExecution(reason)
            monkeypatch.setattr(flow.runtime, 'ensure_window', invalid_window)
        return hit
    monkeypatch.setattr(flow, '_fight', finish)
    with pytest.raises(StopExecution) as error:flow._battle(kind)
    assert error.value.reason is reason
    assert game.actions == ['start_battle']


@pytest.mark.parametrize('kind', ['elite', 'boss'])
def test_reward_click_recovery_does_not_resubmit_confirmed_button(kind):
    game, flow, _ = levelup_flow(kind=kind, popup_delay=None, network_after=kind+'_choose_reward')
    flow._battle(kind)
    assert game.actions.count(kind+'_choose_reward') == 1
    assert game.actions.count('network_retry') == 4
    assert game.state == 'map'


@pytest.mark.parametrize('invalid', ['wrong_identity', 'recognition_only'])
def test_reward_handoff_requires_expected_actionable_result(monkeypatch, invalid):
    game, flow, _ = levelup_flow(kind='elite', popup_delay=None)
    original = flow._fight
    def finish(*args):
        hit = original(*args)
        if invalid == 'wrong_identity':return replace(hit, key='victory')
        scenes = dict(flow.settings.scenes)
        scenes['elite_choose_reward'] = replace(scenes['elite_choose_reward'], action=None)
        flow.settings = replace(flow.settings, scenes=scenes)
        return hit
    monkeypatch.setattr(flow, '_fight', finish)
    with pytest.raises(ValueError, match='actionable reward entry'):flow._battle('elite')
    assert game.actions == ['start_battle']


def late_loot_flow(*, after_close='loot', late_after=0., **kwargs):
    game, flow, stop = levelup_flow(popup_delay=None, **kwargs)
    clicked_at, appeared = None, False

    def clicked(g, key):
        nonlocal clicked_at
        if key == g.result_key and clicked_at is None:
            clicked_at = g.clock.now
        elif key == 'levelup_close':
            g.state = after_close

    def captured(g):
        nonlocal appeared
        if clicked_at is not None and not appeared and g.clock.now >= clicked_at+late_after-1e-6:
            appeared, g.state = True, 'levelup'

    game.on_click, game.on_capture = clicked, captured
    return game, flow, stop


@pytest.mark.parametrize('kind,floor', [('elite', 1), ('boss', 1), ('boss', 2)])
@pytest.mark.parametrize('after_close', ['loot', 'settlement'])
def test_late_upgrade_after_reward_click_closes_and_reaches_map(kind, floor, after_close):
    game, flow, _ = late_loot_flow(kind=kind, floor=floor, after_close=after_close)
    flow._battle(kind)
    assert game.actions.count('levelup_close') == 1
    assert game.actions.count(game.result_key) == (2 if after_close == 'settlement' else 1)
    assert game.actions.index(game.result_key) < game.actions.index('levelup_close')
    assert game.actions.index('levelup_close') < game.actions.index('loot_ominous_crystal')
    assert game.actions.count('loot_confirm') == 1 and game.state == 'map'
    if after_close == 'settlement':
        samples = [row for row in game.observations if row[0] == game.result_key
                   and row[1] > game.closed_at and row[2]]
        assert len(samples) == 3 and len({row[3] for row in samples}) == 3


@pytest.mark.parametrize('controls', [('levelup',), ('levelup_close',)])
def test_partial_late_upgrade_blocks_cards_and_reward_replay(controls):
    game, flow, _ = late_loot_flow(kind='elite')
    game.popup_controls = controls
    flow.settings = replace(flow.settings, timing=dict(flow.settings.timing, page_timeout_ms=1000))
    with pytest.raises(StopExecution) as caught:
        flow._battle('elite')
    assert caught.value.reason is StopReason.CHAOS_UNKNOWN_CONTENT
    assert 'three_known_loot_cards' in caught.value.detail
    assert game.actions.count(game.result_key) == 1
    assert 'levelup_close' not in game.actions and 'loot_confirm' not in game.actions


def test_late_upgrade_interrupts_two_card_confirmations_and_requires_three_new_frames(monkeypatch):
    game, flow, _ = late_loot_flow(kind='elite', late_after=1.15)
    original, samples = game.loot_cards, []

    def cards(frame):
        value = original(frame)
        if value is not None:
            samples.append((game.clock.now, game.captures))
        return value

    monkeypatch.setattr(game, 'loot_cards', cards)
    flow._battle('elite')
    assert len(samples) == 5
    assert samples[1][0] < game.closed_at < samples[2][0]
    assert len({frame for _, frame in samples[2:]}) == 3
    assert game.state == 'map'


@pytest.mark.parametrize('network_after', ['elite_choose_reward', 'levelup_close'])
def test_late_upgrade_recovery_keeps_close_and_resume_once(network_after):
    game, flow, _ = late_loot_flow(kind='elite', after_close='settlement', network_after=network_after)
    flow._battle('elite')
    assert game.actions.count('network_retry') == 4
    assert game.actions.count('levelup_close') == 1
    assert game.actions.count(game.result_key) == 2
    assert game.state == 'map'


@pytest.mark.parametrize('reason', [StopReason.MANUAL_STOP, StopReason.WINDOW_ABNORMAL])
def test_late_upgrade_close_preserves_input_guards(monkeypatch, reason):
    game, flow, stop = late_loot_flow(kind='elite')
    original, count = game.control, 0

    def detect(frame, key, region=None):
        nonlocal count
        hit = original(frame, key, region)
        if key == 'levelup_close' and hit is not None:
            count += 1
            if count == 3:
                if reason is StopReason.MANUAL_STOP:
                    stop.request(reason)
                else:
                    def invalid_window(**kwargs):
                        raise StopExecution(reason)
                    monkeypatch.setattr(flow.runtime, 'ensure_window', invalid_window)
        return hit

    monkeypatch.setattr(game, 'control', detect)
    with pytest.raises(StopExecution) as caught:
        flow._battle('elite')
    assert caught.value.reason is reason
    assert game.actions.count(game.result_key) == 1
    assert 'levelup_close' not in game.actions and 'loot_confirm' not in game.actions


def test_late_upgrade_close_does_not_restart_loot_deadline(monkeypatch):
    game, flow, _ = late_loot_flow(kind='elite', after_close='settlement')
    game.unknown_loot = True
    flow.settings = replace(flow.settings, timing=dict(flow.settings.timing, page_timeout_ms=1000))
    original, started = game.begin_loot_recognition, []

    def begin():
        started.append(game.clock.now)
        original()

    monkeypatch.setattr(game, 'begin_loot_recognition', begin)
    with pytest.raises(StopExecution) as caught:
        flow._battle('elite')
    assert caught.value.reason is StopReason.CHAOS_UNKNOWN_CONTENT
    assert game.actions.count('levelup_close') == 1
    assert 1.-1e-6 <= flow.runtime.active_monotonic()-started[0] <= 1.1+1e-6
    assert 'loot_confirm' not in game.actions


def test_late_upgrade_slow_match_crossing_loot_deadline_cannot_close(monkeypatch):
    game, flow, _ = late_loot_flow(kind='elite')
    flow.settings = replace(flow.settings, timing=dict(flow.settings.timing, page_timeout_ms=1000))
    original, count = game.control, 0

    def detect(frame, key, region=None):
        nonlocal count
        hit = original(frame, key, region)
        if key == 'levelup_close' and hit is not None:
            count += 1
            if count == 3:
                game.clock.sleep(1.)
        return hit

    monkeypatch.setattr(game, 'control', detect)
    with pytest.raises(StopExecution) as caught:
        flow._battle('elite')
    assert caught.value.reason is StopReason.CHAOS_UNKNOWN_CONTENT
    assert 'levelup_close' not in game.actions and 'loot_confirm' not in game.actions


def test_upgrade_closed_before_reward_entry_is_not_closed_again(monkeypatch):
    game, flow, _ = levelup_flow(kind='elite', popup_delay=0.)
    flow.settings = replace(flow.settings, timing=dict(flow.settings.timing, page_timeout_ms=1000))
    game.on_click = lambda g, key: setattr(g, 'state', 'levelup') if key == g.result_key else None
    with pytest.raises(StopExecution) as caught:
        flow._battle('elite')
    assert caught.value.reason is StopReason.CHAOS_UNKNOWN_CONTENT
    assert game.actions.count('levelup_close') == 1
    assert game.actions.count(game.result_key) == 1
    assert 'loot_confirm' not in game.actions


def test_confirmed_cards_take_priority_over_background_reward_after_late_close(monkeypatch):
    game, flow, _ = late_loot_flow(kind='elite')
    original = game.control

    def detect(frame, key, region=None):
        if frame['state'] == 'loot' and key == game.result_key:
            return game._hit(key, frame)
        return original(frame, key, region)

    monkeypatch.setattr(game, 'control', detect)
    flow._battle('elite')
    assert game.actions.count('levelup_close') == game.actions.count(game.result_key) == 1
    assert game.state == 'map'


def test_late_upgrade_cannot_replay_unresponsive_reward_more_than_once(monkeypatch):
    game, flow, _ = late_loot_flow(kind='elite', after_close='settlement')
    flow.settings = replace(flow.settings, timing=dict(flow.settings.timing, page_timeout_ms=2000))
    original = game.on_click

    def clicked(g, key):
        original(g, key)
        if key == g.result_key and g.closed_at is not None:
            g.state = 'settlement'

    game.on_click = clicked
    with pytest.raises(StopExecution) as caught:
        flow._battle('elite')
    assert caught.value.reason is StopReason.CHAOS_UNKNOWN_CONTENT
    assert game.actions.count('levelup_close') == 1
    assert game.actions.count(game.result_key) == 2
    assert 'loot_confirm' not in game.actions


def test_late_upgrade_joint_evidence_restarts_after_network_recovery(monkeypatch):
    game, flow, _ = late_loot_flow(kind='elite')
    original, injected = game.on_capture, False

    def captured(g):
        nonlocal injected
        original(g)
        samples = [row for row in g.observations if row[0] == 'levelup_close' and row[2]]
        if not injected and len(samples) == 2:
            g.network_remaining, injected = 4, True

    game.on_capture = captured
    flow._battle('elite')
    samples = [row for row in game.observations if row[0] == 'levelup_close' and row[2]]
    assert len(samples) == 5 and samples[2][1]-samples[1][1] >= 40.
    assert game.closed_at == pytest.approx(samples[-1][1])
    assert game.actions.count('levelup_close') == 1 and game.state == 'map'


def test_next_battle_can_close_its_own_late_upgrade():
    game, flow, _ = levelup_flow(kind='elite', popup_delay=0.)
    flow._battle('elite')
    game.state, game.closed_at, game.popup_delay = 'prepare', None, None

    def clicked(g, key):
        if key == g.result_key:
            g.state = 'levelup'
        elif key == 'levelup_close':
            g.state = 'loot'

    game.on_click = clicked
    flow._battle('elite')
    assert game.actions.count('levelup_close') == 2
    assert game.actions.count(game.result_key) == game.actions.count('loot_confirm') == 2
    assert game.state == 'map'


def late_continue_flow(*, after_close='map', **kwargs):
    game, flow, stop = levelup_flow(kind='normal', popup_delay=None, **kwargs)
    intercepted = False

    def clicked(g, key):
        nonlocal intercepted
        if key == 'continue_exploration' and not intercepted:
            intercepted, g.state = True, 'levelup'
            g.node_index -= 1  # The covered first click never advanced the route.
        elif key == 'levelup_close':
            g.state = after_close
            if after_close == 'map':
                g.node_index += 1

    game.on_click = clicked
    return game, flow, stop


@pytest.mark.parametrize('floor', [1, 2, 3])
@pytest.mark.parametrize('after_close', ['map', 'settlement'])
def test_normal_late_upgrade_after_continue_closes_and_reaches_map(floor, after_close):
    game, flow, _ = late_continue_flow(floor=floor, after_close=after_close)
    flow._battle('normal')
    assert game.actions.count('levelup_close') == 1
    assert game.actions.count('continue_exploration') == (2 if after_close == 'settlement' else 1)
    assert game.actions.index('continue_exploration') < game.actions.index('levelup_close')
    assert game.state == 'map' and game.node_index == 1
    view, state, captures = flow._map_handoff
    assert view.floor == floor and state is flow.state and captures == game.captures
    if after_close == 'settlement':
        samples = [row for row in game.observations if row[0] == 'continue_exploration'
                   and row[1] > game.closed_at and row[2]]
        assert len(samples) == 3 and len({row[3] for row in samples}) == 3


def test_normal_upgrade_after_victory_before_continue_button_is_closed_first(monkeypatch):
    game, flow, _ = levelup_flow(kind='normal', popup_delay=None)
    original = flow._fight

    def finish(*args):
        result = original(*args)
        game.state = 'levelup'
        return result

    monkeypatch.setattr(flow, '_fight', finish)
    flow._battle('normal')
    assert game.actions == ['start_battle', 'levelup_close', 'continue_exploration']
    assert game.state == 'map'


@pytest.mark.parametrize('controls', [('levelup',), ('levelup_close',)])
def test_normal_partial_late_upgrade_blocks_continue_and_map(controls):
    game, flow, _ = late_continue_flow()
    game.popup_controls = controls
    flow.settings = replace(flow.settings, timing=dict(flow.settings.timing, page_timeout_ms=1000))
    with pytest.raises(StopExecution) as caught:
        flow._battle('normal')
    assert caught.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert 'continue_destination' in caught.value.detail
    assert game.actions == ['start_battle', 'continue_exploration']
    assert flow._map_handoff is None


@pytest.mark.parametrize('network_after', ['continue_exploration', 'levelup_close'])
def test_normal_late_upgrade_network_keeps_close_and_continue_once(network_after):
    game, flow, _ = late_continue_flow(after_close='settlement', network_after=network_after)
    flow._battle('normal')
    assert game.actions.count('network_retry') == 4
    assert game.actions.count('levelup_close') == 1
    assert game.actions.count('continue_exploration') == 2 and game.state == 'map'


@pytest.mark.parametrize('reason', [StopReason.MANUAL_STOP, StopReason.WINDOW_ABNORMAL])
def test_normal_late_upgrade_close_preserves_input_guards(monkeypatch, reason):
    game, flow, stop = late_continue_flow()
    original, count = game.control, 0

    def detect(frame, key, region=None):
        nonlocal count
        hit = original(frame, key, region)
        if key == 'levelup_close' and hit is not None:
            count += 1
            if count == 3:
                if reason is StopReason.MANUAL_STOP:
                    stop.request(reason)
                else:
                    def invalid_window(**kwargs):
                        raise StopExecution(reason)
                    monkeypatch.setattr(flow.runtime, 'ensure_window', invalid_window)
        return hit

    monkeypatch.setattr(game, 'control', detect)
    with pytest.raises(StopExecution) as caught:
        flow._battle('normal')
    assert caught.value.reason is reason
    assert game.actions == ['start_battle', 'continue_exploration']


def test_normal_upgrade_after_two_map_frames_requires_three_new_frames(monkeypatch):
    game, flow, _ = levelup_flow(kind='normal', popup_delay=None)
    original, samples, appeared = game.map_view, [], False

    def observe(frame):
        value = original(frame)
        if value is not None:
            samples.append((game.clock.now, game.captures))
        return value

    def captured(g):
        nonlocal appeared
        if len(samples) == 2 and not appeared:
            appeared, g.state = True, 'levelup'

    game.on_capture = captured
    game.on_click = lambda g, key: setattr(g, 'state', 'map') if key == 'levelup_close' else None
    monkeypatch.setattr(game, 'map_view', observe)
    flow._battle('normal')
    assert len(samples) == 5 and samples[1][0] < game.closed_at < samples[2][0]
    assert game.actions.count('continue_exploration') == game.actions.count('levelup_close') == 1
    assert flow._map_handoff[2] == samples[-1][1]


def test_normal_late_upgrade_cannot_accept_wrong_floor_or_restart_deadline(monkeypatch):
    game, flow, _ = late_continue_flow()
    flow.settings = replace(flow.settings, timing=dict(flow.settings.timing, page_timeout_ms=1000))
    original, started = game.map_view, []
    original_click = game.on_click

    def clicked(g, key):
        original_click(g, key)
        if key == 'continue_exploration':
            started.append(flow.runtime.active_monotonic()+flow.settings.timing['action_settle_ms']/1000)

    def wrong_floor(frame):
        view = original(frame)
        return replace(view, floor=2) if view is not None else None

    game.on_click = clicked
    monkeypatch.setattr(game, 'map_view', wrong_floor)
    with pytest.raises(StopExecution) as caught:
        flow._battle('normal')
    assert caught.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert game.actions.count('levelup_close') == game.actions.count('continue_exploration') == 1
    assert 1.-1e-6 <= flow.runtime.active_monotonic()-started[0] <= 1.1+1e-6
    assert flow._map_handoff is None


def test_normal_earlier_upgrade_close_is_not_repeated_after_continue():
    game, flow, _ = levelup_flow(kind='normal', popup_delay=0.)
    flow.settings = replace(flow.settings, timing=dict(flow.settings.timing, page_timeout_ms=1000))
    game.on_click = lambda g, key: setattr(g, 'state', 'levelup') if key == 'continue_exploration' else None
    with pytest.raises(StopExecution) as caught:
        flow._battle('normal')
    assert caught.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert game.actions == ['start_battle', 'levelup_close', 'continue_exploration']


def test_normal_continue_requires_actionable_scene():
    game, flow, _ = levelup_flow(kind='normal', popup_delay=None)
    scenes = dict(flow.settings.scenes)
    scenes['continue_exploration'] = replace(scenes['continue_exploration'], action=None)
    flow.settings = replace(flow.settings, scenes=scenes)
    with pytest.raises(ValueError, match='recognition-only: continue_exploration'):
        flow._battle('normal')
    assert game.actions == ['start_battle']
