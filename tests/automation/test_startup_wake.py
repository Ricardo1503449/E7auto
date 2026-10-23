from collections import deque
from dataclasses import replace

import pytest

from e7auto.features.shop.flow import ShopFlow as AutomationEngine
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController
from e7auto.features.penguin.flow import PenguinFlow as PenguinEngine
from e7auto.runtime.stop_control import StopExecution
from e7auto.core.types import Point, Rect, Size
from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.ports import DisplayGeometry
from tests.helpers import FakeClock, FakeInput, FakeWindowService, ScriptedVision, make_config, make_dependencies

from tests.automation.support import run_session
from tests.automation.test_penguin import PenguinGame, run as run_penguin


def make_engine(feature, *, windows=None):
    config = make_config(stable_frames=3)
    config = replace(config, timing=replace(config.timing, poll_interval_ms=100))
    vision = ScriptedVision(main_visible=False)
    clock = FakeClock()
    deps, *_ = make_dependencies(vision, clock=clock, windows=windows)
    control = StopController()
    initial = (RuntimeSnapshot.penguins("wake-test", 1) if feature == "penguin" else
               RuntimeSnapshot.initial("wake-test", (), 0))
    cls = PenguinEngine if feature == "penguin" else AutomationEngine
    engine = cls(config, deps, control, SnapshotPublisher(initial, lambda _: None), frozenset())
    engine.runtime.prepare()
    return engine, vision, clock


@pytest.fixture(params=["shop", "penguin"])
def engine_state(request):
    return make_engine(request.param)


def wait_icon(engine, detector=None):
    penguin = isinstance(engine, PenguinEngine)
    return engine.runtime.wait_startup_icon(
        "sanctuary_entry" if penguin else "main_shop_icon",
        detector or engine.runtime.deps.vision.main_shop_icon,
        minimum_stable_frames=2 if penguin else 1,
    )


def wake_events(engine):
    return [fields for name, fields in engine.runtime.deps.logger.events
            if name == "input" and fields.get("action") == "wake_main_screen_startup"]


def test_visible_icon_never_sends_wake(engine_state):
    engine, vision, _ = engine_state
    vision.main_visible = True
    assert wait_icon(engine) is not None
    assert engine.runtime.deps.inputs.actions == []
    assert engine.runtime.deps.capture.calls == 3
    assert wake_events(engine) == []


def test_first_miss_wakes_immediately_then_requires_three_fresh_frames(engine_state):
    engine, vision, clock = engine_state
    engine.runtime.deps.inputs.trigger_once = lambda: setattr(vision, "main_visible", True)
    assert wait_icon(engine) is not None
    assert engine.runtime.deps.inputs.actions == [("click", Point(50, 40), None)]
    assert engine.runtime.deps.capture.calls == 4
    assert clock.monotonic() == pytest.approx(.3)
    assert len(wake_events(engine)) == 1


def test_intermittent_misses_do_not_repeat_wake_or_preserve_old_stability(engine_state):
    engine, vision, _ = engine_state
    samples = iter([False, True, True, False, True, True, True])
    seen = []

    def detect(frame):
        visible = next(samples)
        seen.append(visible)
        return vision._observation("entry") if visible else None

    assert wait_icon(engine, detect) is not None
    assert len(seen) == 7 and len(wake_events(engine)) == 1


def test_two_failed_wakes_time_out_and_exhaust_the_run_allowance(engine_state):
    engine, _, clock = engine_state
    with pytest.raises(StopExecution) as stopped:
        wait_icon(engine)
    assert stopped.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert 13 <= clock.monotonic() < 13.3
    assert engine.runtime.deps.inputs.actions == [("click", Point(50, 40), None)] * 2
    # Even an accidental second startup wait in this engine cannot resend it.
    with pytest.raises(StopExecution):
        wait_icon(engine)
    assert [event['attempt'] for event in wake_events(engine)] == [1, 2]


def test_manual_stop_after_missing_observation_blocks_wake(engine_state):
    engine, _, _ = engine_state

    def detect(frame):
        engine.runtime.control.request(StopReason.MANUAL_STOP)
        return None

    with pytest.raises(StopExecution) as stopped:
        wait_icon(engine, detect)
    assert stopped.value.reason is StopReason.MANUAL_STOP
    assert engine.runtime.deps.inputs.actions == []


def test_manual_stop_immediately_after_wake_blocks_further_capture_and_entry(engine_state):
    engine, _, _ = engine_state
    engine.runtime.deps.inputs.trigger_once = lambda: engine.runtime.control.request(StopReason.MANUAL_STOP)
    with pytest.raises(StopExecution) as stopped:
        wait_icon(engine)
    assert stopped.value.reason is StopReason.MANUAL_STOP
    assert len(wake_events(engine)) == 1 and engine.runtime.deps.capture.calls == 1


def test_window_change_before_wake_blocks_input(engine_state):
    engine, _, _ = engine_state

    def detect(frame):
        windows = engine.runtime.deps.windows
        rect = windows.state.client_bounds
        windows.state = replace(windows.state, client_bounds=replace(rect, x=rect.x+1))
        return None

    with pytest.raises(StopExecution) as stopped:
        wait_icon(engine, detect)
    assert stopped.value.reason is StopReason.WINDOW_ABNORMAL
    assert engine.runtime.deps.inputs.actions == []


def test_failed_input_is_not_retried(engine_state, monkeypatch):
    engine, _, _ = engine_state
    attempted = []

    def fail(window, point):
        attempted.append(point)
        raise OSError("injected wake failure")

    monkeypatch.setattr(engine.runtime.deps.inputs, "click", fail)
    with pytest.raises(StopExecution) as stopped:
        wait_icon(engine)
    assert stopped.value.reason is StopReason.INPUT_FAILURE
    assert attempted == [Point(50, 40)]


@pytest.mark.parametrize("feature", ["shop", "penguin"])
@pytest.mark.parametrize("wake_on", [1, 2])
def test_wake_uses_scaled_client_center_not_desktop_coordinates(feature, wake_on):
    windows = FakeWindowService(display_geometry=DisplayGeometry(
        1, r"\\.\DISPLAY1", Rect(0, 0, 200, 160), Size(200, 160), 96,
    ))
    engine, vision, _ = make_engine(feature, windows=windows)
    inputs = engine.runtime.deps.inputs
    def clicked():
        if len(inputs.actions) == wake_on:
            vision.main_visible = True
        else:
            inputs.trigger_once = clicked
    inputs.trigger_once = clicked
    assert wait_icon(engine) is not None
    assert windows.state.client_bounds.width == 150 and windows.state.client_bounds.height == 120
    assert inputs.actions == [("click", Point(75, 60), None)] * wake_on


@pytest.mark.parametrize("after_wake, recovered_visible", [(False, True), (True, True), (True, False)])
def test_recovery_uses_fresh_frames_and_preserves_wake_budget(engine_state, after_wake, recovered_visible):
    engine, vision, clock = engine_state
    frames = deque((["hidden"] if after_wake else []) + ["error", "clear"])
    seen = []

    def capture():
        frame = frames.popleft() if frames else ("visible" if recovered_visible else "hidden")
        if frame == "clear":
            clock.sleep(12)  # Recovery exceeds the recognition budget but is excluded.
        return frame

    engine.runtime.capture_raw = capture
    vision.network_connection_error = lambda frame: vision._observation("network") if frame == "error" else None
    vision.network_retry = lambda frame: None

    def detect(frame):
        seen.append(frame)
        return vision._observation("entry") if frame == "visible" else None

    if recovered_visible:
        assert wait_icon(engine, detect) is not None
        assert 12 <= clock.monotonic() < 13
        assert seen.count("visible") == 3
    else:
        with pytest.raises(StopExecution) as stopped:
            wait_icon(engine, detect)
        assert stopped.value.reason is StopReason.RECOGNITION_TIMEOUT
        assert 13 <= engine.runtime.active_monotonic() < 13.3
        assert 25 <= clock.monotonic() < 25.3
    assert "error" not in seen and "clear" not in seen
    assert len(wake_events(engine)) == (int(after_wake) if recovered_visible else 2)


@pytest.mark.parametrize('wake_on', [1, 2])
def test_shop_session_wakes_then_completes_original_flow(wake_on):
    vision = ScriptedVision(main_visible=False, top=[()], bottom=[()])
    inputs = FakeInput()
    def clicked():
        if len(inputs.actions) == wake_on:
            vision.main_visible = True
        else:
            inputs.trigger_once = clicked
    inputs.trigger_once = clicked
    final, _, _, inputs, _, _, log = run_session(vision, inputs=inputs, config=make_config(stable_frames=3))
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert [point for action, point, _ in inputs.actions if action == "click"] == [Point(50, 40)] * wake_on + [Point(5, 5), Point(6, 6)]
    assert sum(name == "input" and fields.get("action") == "wake_main_screen_startup" for name, fields in log.events) == wake_on


@pytest.mark.parametrize('wake_on', [1, 2])
def test_penguin_session_wakes_then_buys_and_returns_normally(wake_on):
    class HiddenGame(PenguinGame):
        hidden = True

        def control(self, frame, name):
            if self.state == "home" and self.hidden:
                return None
            return super().control(frame, name)

        def click(self, point):
            if point == Point(50, 40):
                self.actions.append("wake")
                self.hidden = self.actions.count('wake') < wake_on
                return
            super().click(point)

    final, _, game = run_penguin(HiddenGame(), limit=1, config=make_config(stable_frames=3))
    assert final.stop_reason is StopReason.PENGUIN_LIMIT_COMPLETE and final.purchases_completed == 1
    assert game.actions[:wake_on+1] == ["wake"] * wake_on + ["sanctuary_entry"]
    assert game.actions.count("wake") == wake_on and game.state == "home"


def test_second_wake_waits_three_seconds_after_input_completes(engine_state, monkeypatch):
    engine, vision, clock = engine_state
    inputs = engine.runtime.deps.inputs
    click = inputs.click
    started, finished = [], []
    def delayed_click(window, point):
        started.append(engine.runtime.active_monotonic())
        click(window, point)
        clock.sleep(.15)
        finished.append(engine.runtime.active_monotonic())
        if len(started) == 2:
            vision.main_visible = True
    monkeypatch.setattr(inputs, 'click', delayed_click)
    assert wait_icon(engine) is not None
    assert started[0] == 0
    assert 3 <= started[1]-finished[0] < 3.11
    assert [event['attempt'] for event in wake_events(engine)] == [1, 2]
    confirmed = [fields for name, fields in engine.runtime.deps.logger.events if name == 'startup_icon_confirmed']
    assert confirmed[-1]['wake_sent'] is True and confirmed[-1]['wake_attempts'] == 2


def test_visible_icon_at_retry_time_gets_three_frames_without_reclick(engine_state):
    engine, vision, clock = engine_state
    seen = []
    def detect(frame):
        if clock.now >= 3:
            seen.append(engine.runtime.deps.capture.calls)
            return vision._observation('entry')
        return None
    assert wait_icon(engine, detect) is not None
    assert len(seen) == 3 and len(set(seen)) == 3
    assert len(wake_events(engine)) == 1
    assert 3.2 <= clock.now < 3.4


def test_miss_after_partial_confirmation_can_use_second_wake(engine_state):
    engine, vision, clock = engine_state
    flashed = False
    after_second = []
    def detect(frame):
        nonlocal flashed
        if engine.runtime.startup_wake_attempts == 2:
            after_second.append(engine.runtime.deps.capture.calls)
            return vision._observation('entry')
        if clock.now >= 3 and not flashed:
            flashed = True
            return vision._observation('entry')
        return None
    assert wait_icon(engine, detect) is not None
    assert flashed and len(wake_events(engine)) == 2
    assert len(after_second) == len(set(after_second)) == 3


@pytest.mark.parametrize('failure', ['manual_stop', 'window', 'input'])
def test_second_wake_uses_stop_window_and_input_guards(engine_state, monkeypatch, failure):
    engine, _, clock = engine_state
    inputs = engine.runtime.deps.inputs
    click = inputs.click
    def guarded_click(window, point):
        if inputs.actions and failure == 'input':
            raise OSError('second wake input failure')
        click(window, point)
    monkeypatch.setattr(inputs, 'click', guarded_click)
    def detect(frame):
        if clock.now >= 3:
            if failure == 'manual_stop':
                engine.runtime.control.request(StopReason.MANUAL_STOP)
            elif failure == 'window':
                windows = engine.runtime.deps.windows
                bounds = windows.state.client_bounds
                windows.state = replace(windows.state, client_bounds=replace(bounds, x=bounds.x+1))
        return None
    with pytest.raises(StopExecution) as stopped:
        wait_icon(engine, detect)
    assert stopped.value.reason is {'manual_stop': StopReason.MANUAL_STOP, 'window': StopReason.WINDOW_ABNORMAL,
                                    'input': StopReason.INPUT_FAILURE}[failure]
    assert len(inputs.actions) == len(wake_events(engine)) == 1


@pytest.mark.parametrize('slow_stage', ['capture', 'recognition', 'log'])
def test_expired_recognition_budget_blocks_second_wake(engine_state, monkeypatch, slow_stage):
    engine, vision, clock = engine_state
    delayed = False
    def slow():
        nonlocal delayed
        if not delayed and clock.now >= 3:
            delayed = True
            clock.sleep(8)
    if slow_stage == 'capture':
        capture = engine.runtime.capture
        def slow_capture():
            slow()
            return capture()
        monkeypatch.setattr(engine.runtime, 'capture', slow_capture)
    elif slow_stage == 'log':
        log = engine.runtime.deps.logger.event
        def slow_log(name, **fields):
            if name == 'recognition':
                slow()
            log(name, **fields)
        monkeypatch.setattr(engine.runtime.deps.logger, 'event', slow_log)
    def detect(frame):
        if slow_stage == 'recognition':
            slow()
        return None
    with pytest.raises(StopExecution) as stopped:
        wait_icon(engine, detect)
    assert stopped.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert delayed and len(wake_events(engine)) == 1


def test_network_after_second_wake_does_not_reset_attempts_or_deadline(engine_state):
    engine, vision, clock = engine_state
    recovering = False
    recovered = False
    def capture():
        nonlocal recovering, recovered
        if recovering:
            recovering = False
            recovered = True
            clock.sleep(12)
            return 'clear'
        if engine.runtime.startup_wake_attempts == 2 and not recovered:
            recovering = True
            return 'error'
        return 'hidden'
    engine.runtime.capture_raw = capture
    vision.network_connection_error = lambda frame: vision._observation('network') if frame == 'error' else None
    vision.network_retry = lambda frame: None
    seen = []
    def detect(frame):
        seen.append(frame)
        return None
    with pytest.raises(StopExecution) as stopped:
        wait_icon(engine, detect)
    assert stopped.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert recovered and set(seen) == {'hidden'}
    assert len(wake_events(engine)) == 2
    assert 13 <= engine.runtime.active_monotonic() < 13.3
    assert 25 <= clock.now < 25.3


@pytest.mark.parametrize('previous_attempts', [0, 1, 2])
def test_normal_wait_never_uses_startup_wake_allowance(engine_state, previous_attempts):
    engine, vision, _ = engine_state
    engine.runtime.startup_wake_attempts = previous_attempts
    engine.runtime.startup_last_wake_at = 0. if previous_attempts else None
    with pytest.raises(StopExecution) as stopped:
        engine.runtime.wait_stable_observation('normal_reentry', vision.main_shop_icon, 4000)
    assert stopped.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert engine.runtime.startup_wake_attempts == previous_attempts
    assert engine.runtime.deps.inputs.actions == []
