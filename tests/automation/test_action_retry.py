from dataclasses import replace
from types import SimpleNamespace

import pytest

from e7auto.core.domain import StopReason
from e7auto.core.observations import Observation
from e7auto.core.types import ClientPoint, ClientRect
from e7auto.runtime.action_retry import Transition, click_and_wait, observation_signature
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers import FakeClock, FakeLogger, make_config


def hit(x=20, key="button"):
    return Observation(key, 1., ClientRect(x, 10, 30, 20), ClientPoint(x+15, 20))


@pytest.fixture
def action():
    clock, logger, control = FakeClock(), FakeLogger(), StopController()
    config = make_config(stable_frames=3)
    config = replace(config, timing=replace(config.timing, poll_interval_ms=100))
    runtime = SimpleNamespace(config=config, deps=SimpleNamespace(clock=clock, logger=logger),
                              control=control, active_monotonic=clock.monotonic, network_recovery_generation=0)
    clicks, frames = [], []
    def capture():
        frame = {"n": len(frames), "at": clock.now}
        frames.append(frame)
        return frame
    runtime.capture = capture
    def run(observe, **kwargs):
        options = dict(name="open", initial=hit(), click=lambda target: clicks.append((clock.now, target)),
                       source=lambda _: hit(), observe=observe, signature=observation_signature,
                       target_signature=observation_signature, timeout_ms=3000)
        options.update(kwargs)
        return click_and_wait(runtime, **options)
    return runtime, clicks, frames, run


def test_success_is_immediate_after_original_stability_not_retry_interval(action):
    runtime, clicks, _, run = action
    result = run(lambda _: Transition(hit(key="next")))
    assert result.object_id == "next" and len(clicks) == 1
    assert runtime.deps.clock.now == pytest.approx(.2)


def test_missed_click_retries_once_after_three_seconds_using_new_anchor(action):
    runtime, clicks, _, run = action
    result = run(lambda _: Transition(hit(key="next")) if len(clicks) == 2 else Transition(),
                 source=lambda _: hit(40))
    assert result.object_id == "next"
    assert [at for at, _ in clicks] == pytest.approx([0., 3.])
    assert clicks[1][1].anchor == ClientPoint(55, 20)


def test_second_failure_keeps_one_six_second_deadline(action):
    runtime, clicks, _, run = action
    with pytest.raises(StopExecution, match="attempts=2"):
        run(lambda _: Transition())
    assert len(clicks) == 2 and runtime.deps.clock.now == pytest.approx(6.)


@pytest.mark.parametrize("outer,original,expected", [(4.,3000,4.), (None,15000,15.)])
def test_original_longer_timeout_and_outer_deadline(action, outer, original, expected):
    runtime, clicks, _, run = action
    with pytest.raises(StopExecution):
        run(lambda _: Transition(), timeout_ms=original, outer_deadline=outer)
    assert runtime.deps.clock.now == pytest.approx(expected)
    assert len(clicks) == 2


@pytest.mark.parametrize("partial", [False, True])
def test_one_response_permanently_prevents_retry_even_after_disappearance(action, partial):
    _, clicks, _, run = action
    def observe(frame):
        return Transition(responded=True) if partial and frame["n"] == 1 else (
            Transition(hit(key="next")) if frame["n"] == 1 else Transition())
    with pytest.raises(StopExecution):
        run(observe)
    assert len(clicks) == 1


@pytest.mark.parametrize("blocked", [False, True])
def test_missing_source_or_overlay_cannot_authorize_retry(action, blocked):
    _, clicks, _, run = action
    with pytest.raises(StopExecution):
        run(lambda _: Transition(blocked=blocked), source=lambda _: hit() if blocked else None)
    assert len(clicks) == 1


def test_moving_source_never_forms_three_stable_frames(action):
    _, clicks, _, run = action
    with pytest.raises(StopExecution):
        run(lambda _: Transition(), source=lambda f: hit(20+f["n"]*8))
    assert len(clicks) == 1


def test_recovery_discards_frame_but_not_response_or_deadline(action):
    runtime, clicks, frames, run = action
    capture = runtime.capture
    def recovered():
        frame = capture()
        if frame["n"] == 4:
            runtime.network_recovery_generation += 1
        return frame
    runtime.capture = recovered
    with pytest.raises(StopExecution):
        run(lambda f: Transition(responded=f["n"] == 2))
    assert len(clicks) == 1 and runtime.deps.clock.now == pytest.approx(6.)


@pytest.mark.parametrize("boundary", ["stop", "slow_source", "slow_log"])
def test_retry_checks_stop_and_deadline_after_detection_and_logging(action, boundary):
    runtime, clicks, _, run = action
    logger = runtime.deps.logger.event
    def event(name, **fields):
        logger(name, **fields)
        if boundary == "slow_log" and name == "click_retry":
            runtime.deps.clock.sleep(10)
    runtime.deps.logger.event = event
    def source(frame):
        if frame["at"] >= 3:
            if boundary == "stop":
                runtime.control.request(StopReason.MANUAL_STOP)
            elif boundary == "slow_source":
                runtime.deps.clock.sleep(10)
        return hit()
    with pytest.raises(StopExecution):
        run(lambda _: Transition(), source=source)
    assert len(clicks) == 1


def test_input_exception_is_never_retried(action):
    _, clicks, _, run = action
    def failed(target):
        clicks.append(target)
        raise StopExecution(StopReason.INPUT_FAILURE)
    with pytest.raises(StopExecution) as exc:
        run(lambda _: Transition(), click=failed)
    assert exc.value.reason is StopReason.INPUT_FAILURE and len(clicks) == 1


def test_interval_starts_after_input_and_buffer_does_not_supply_evidence(action):
    runtime, clicks, frames, run = action
    def click(target):
        runtime.deps.clock.sleep(.15)
        clicks.append((runtime.deps.clock.now, target))
    def settle(seconds):
        runtime.capture()
        runtime.deps.clock.sleep(seconds)
    run(lambda _: Transition(hit(key="next")) if len(clicks)==2 else Transition(),
        click=click, settle=settle, settle_ms=100)
    assert clicks[1][0] - clicks[0][0] >= 3.
    assert frames[0]["at"] == pytest.approx(.15)


@pytest.mark.parametrize("size", [(2322,1306), (1536,864)])
def test_retry_client_coordinates_pass_through_real_input_adapter_once(size):
    from e7auto.core.domain import RuntimeSnapshot
    from e7auto.core.ports import WindowRef
    from e7auto.core.types import Size
    from e7auto.runtime.context import RuntimeContext
    from e7auto.runtime.snapshots import SnapshotPublisher
    from e7auto.vision.frames import CoordinateTransform
    from tests.helpers import FakeInput
    clock, inputs = FakeClock(), FakeInput()
    deps = SimpleNamespace(clock=clock, logger=FakeLogger(), inputs=inputs)
    control = StopController()
    runtime = RuntimeContext(make_config(stable_frames=3), deps, control,
                             SnapshotPublisher(RuntimeSnapshot.initial("coordinates", (), 0), lambda _: None))
    runtime.window = WindowRef(1, "fake", "fake.exe")
    runtime.transform = CoordinateTransform(Size(2322,1306), Size(*size))
    runtime.ensure_window = lambda **_: control.checkpoint()
    click_and_wait(runtime, name="select", initial=hit(30),
                   click=lambda current: runtime.click("select", current.anchor),
                   source=lambda _: hit(50), capture=lambda: object(),
                   observe=lambda _: Transition(hit(key="next")) if len(inputs.actions)==2 else Transition(),
                   signature=observation_signature, target_signature=observation_signature, timeout_ms=3000)
    assert [point for _, point, _ in inputs.actions] == [ClientPoint(45,20), ClientPoint(65,20)]
