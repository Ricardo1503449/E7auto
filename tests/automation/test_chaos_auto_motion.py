"""Use static-reference state evidence inside the guarded auto-button flow."""
import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.auto_motion import AutoStateEvidence
from e7auto.features.chaos.flow import BattleDefeated, ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


class MotionGame(ChaosGame):
    def __init__(self, *, mode='rotating'):
        super().__init__(chaos_config(), endless_battle=True)
        self.mode = mode
        self.auto = mode != 'static'
        self.motion = AutoStateEvidence(self.config.chaos.auto_motion)

    def begin_auto_recognition(self, now):
        self.motion.begin()

    def auto_recognition_summary(self):
        return self.motion.summary()

    def reset_temporal(self):
        super().reset_temporal()
        self.motion.reset()

    def auto_state(self, frame, now):
        angle = round(now*100) % 180 if self.auto else 0
        return self.motion.observe(now, .2 if self.mode == 'unknown' else .9, angle)


def setup_flow(mode='rotating'):
    game = MotionGame(mode=mode)
    flow = ChaosFlow(game.config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('motion', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState.for_lineup(game.config.chaos.lineup)
    game.state, game.kind = 'battle', 'normal'
    return game, flow


def summaries(flow):
    return [fields for event, fields in flow.runtime.deps.logger.events if event == 'chaos_auto_recognition']


def test_rotation_never_clicks_auto_and_logs_one_summary():
    game, flow = setup_flow()
    flow._ensure_auto(('levelup', 'victory'))
    assert 'enable_auto' not in game.actions and flow.state.auto_confirmed
    record, = summaries(flow)
    assert record['outcome'] == record['state'] == 'rotating'
    assert record['elapsed_ms'] == pytest.approx(100)
    assert abs(record['reference_angle_deg']) >= 10


def test_static_three_frames_clicks_once_without_outer_wait_and_reconfirms_rotation():
    game, flow = setup_flow('static')
    clicks = []
    game.on_click = lambda g, key: clicks.append((key, g.clock.now))
    flow._ensure_auto(('levelup', 'victory'))
    assert game.actions.count('enable_auto') == 1
    assert next(t for key, t in clicks if key == 'enable_auto') == pytest.approx(.2)
    assert summaries(flow)[0]['static_samples'] == 3
    assert [s['outcome'] for s in summaries(flow)] == ['static', 'rotating']


def test_unknown_times_out_without_click_and_logs_rejection():
    game, flow = setup_flow('unknown')
    with pytest.raises(StopExecution) as error:
        flow._ensure_auto(('levelup', 'victory'))
    assert error.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert 'enable_auto' not in game.actions
    record, = summaries(flow)
    assert record['outcome'] == 'recognition_timeout'
    assert record['reason'] == 'icon_not_visible' and record['misses'] > 0
    assert 15000 <= record['elapsed_ms'] <= 15200


def test_result_preempts_unknown_auto_without_click():
    game, flow = setup_flow('unknown')
    game.endless_battle = False
    hit = flow._ensure_auto(('levelup', 'victory'))
    assert hit.key in ('levelup', 'victory') and 'enable_auto' not in game.actions
    assert summaries(flow)[0]['outcome'] == hit.key


def test_network_recovery_breaks_evidence_without_replaying_auto():
    game, flow = setup_flow('static')
    def capture(g):
        if g.clock.now >= .1 and not g.network_injected:
            g.network_remaining, g.network_injected = 1, True
            g.auto = True
    game.on_capture = capture
    flow._ensure_auto(('levelup', 'victory'))
    assert 'enable_auto' not in game.actions
    record, = summaries(flow)
    assert record['evidence_resets'] == 1 and record['outcome'] == 'rotating'
    assert record['elapsed_ms'] >= 100


def test_stop_during_static_confirmation_never_clicks():
    game, flow = setup_flow('static')
    def capture(g):
        if g.clock.now >= .2:
            flow.runtime.control.request(StopReason.MANUAL_STOP)
    game.on_capture = capture
    with pytest.raises(StopExecution) as error:
        flow._ensure_auto(('levelup', 'victory'))
    assert error.value.reason is StopReason.MANUAL_STOP and 'enable_auto' not in game.actions
    assert summaries(flow)[0]['outcome'] == 'manual_stop'


def test_failed_guard_still_preempts_auto():
    game, flow = setup_flow()
    game.state = 'failed'
    flow._battle_pending, flow._battle_deadline = True, 20
    with pytest.raises(BattleDefeated):
        flow._ensure_auto(('levelup', 'victory'))
    assert 'enable_auto' not in game.actions
    assert summaries(flow)[0]['observations'] == 0


def test_short_battle_result_starts_one_full_observation_window(monkeypatch):
    game, flow = setup_flow('unknown')
    game.state, game.endless_battle = 'prepare', False
    waits = []
    original = flow._delay
    def delay(seconds):
        waits.append(seconds)
        original(seconds)
    monkeypatch.setattr(flow, '_delay', delay)
    result = flow._fight('prepare_normal', ('levelup', 'victory'))
    assert result.key == 'victory' and 'enable_auto' not in game.actions
    assert 3. not in waits
    phases = [f for e, f in flow.runtime.deps.logger.events if e == 'chaos_battle_settlement']
    assert [f['phase'] for f in phases] == ['signal', 'result_confirmed']
    assert phases[1]['elapsed_ms']-phases[0]['elapsed_ms'] == pytest.approx(3200)
    record, = summaries(flow)
    assert record['outcome'] == 'victory'
    assert record['criteria']['active_min_angle_deg'] == 10


def test_real_ring_evidence_returns_without_extra_three_frame_wait(monkeypatch):
    import numpy as np
    from e7auto.features.chaos.auto_motion import AutoIconState
    from e7auto.resources.templates import TemplateRepository
    from tests.helpers.chaos_images import auto_icon_image

    game, flow = setup_flow()
    glyph = TemplateRepository(game.config).get(game.config.chaos.scenes['auto_static'].template_key).image
    detector = AutoIconState(game.config.chaos.auto_motion, glyph)
    base = np.zeros((110, 110, 3), np.uint8)
    base[5:110, 1:110] = glyph[:105, :109]
    monkeypatch.setattr(game, 'begin_auto_recognition', detector.begin)
    monkeypatch.setattr(game, 'auto_recognition_summary', detector.summary)
    monkeypatch.setattr(game, 'auto_state', lambda frame, now: detector.observe(auto_icon_image(base, now*100), now))
    flow._ensure_auto(('levelup', 'victory'))
    record, = summaries(flow)
    assert record['elapsed_ms'] == record['first_active_ms'] == pytest.approx(100)
    assert record['first_visible_ms'] == 0
    assert 'enable_auto' not in game.actions


def test_static_click_uses_localized_anchor(monkeypatch):
    from e7auto.core.types import Point
    game, flow = setup_flow('static')
    r = game.config.chaos.auto_motion['roi']
    anchor = Point(r[0]+r[2]//2+3, r[1]+r[3]//2-2)
    game.points[anchor] = 'enable_auto'
    monkeypatch.setattr(game, 'auto_anchor', lambda: anchor)
    calls = []
    original = flow.runtime.click
    def click(action, point):
        calls.append((action, point))
        original(action, point)
    monkeypatch.setattr(flow.runtime, 'click', click)
    flow._ensure_auto(('levelup', 'victory'))
    assert calls == [('chaos_enable_auto', anchor)]


def test_static_without_locked_anchor_never_clicks(monkeypatch):
    game, flow = setup_flow('static')
    monkeypatch.setattr(game, 'auto_anchor', lambda: None)
    with pytest.raises(StopExecution):
        flow._ensure_auto(('levelup', 'victory'))
    assert 'enable_auto' not in game.actions
