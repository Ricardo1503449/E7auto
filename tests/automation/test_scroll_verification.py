from dataclasses import replace

import pytest

from e7auto.core.domain import StopReason
from e7auto.features.shop.contracts import ScrollMovementObservation
from e7auto.features.shop.scrolling import FrameSample, ScrollProgress, ScrollServices, scroll_to_bottom
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.automation.support import run_session
from tests.helpers import FakeClock, FakeLogger, ScriptedVision, make_config


def movement(y=-350.0, x=0.0, *, verified=True, changed=0.4):
    return ScrollMovementObservation(25, changed, 254, x, y, 0.45, verified, "matched" if verified else "ambiguous_content")


@pytest.mark.parametrize("total", [
    movement(350), movement(0), movement(-300), movement(-299), movement(x=4.01),
    movement(verified=False), movement(changed=.30), movement(float("nan")),
    movement(x=float("inf")), movement(changed=float("nan")),
])
def test_invalid_or_ambiguous_motion_never_scans_bottom(total):
    vision = ScriptedVision(scroll_movement=total)
    final, _, _, inputs, _, _, logger = run_session(vision, limit=3)
    assert final.stop_reason is StopReason.SCROLL_VERIFICATION_FAILED
    assert vision.scan_calls == ["top"]
    assert len([a for a, _, _ in inputs.actions if a == "scroll"]) == 1
    trace = next(f for e, f in logger.events if e == "scroll_settle_trace")
    assert trace["verification_method"] == "none"
    assert not any("fallback" in key or "cumulative" in key for key in trace)


def harness(*, total=None, clock=None, capture=None, stable=None, generation=None, checkpoint=None):
    clock = clock or FakeClock()
    logger, progress = FakeLogger(), ScrollProgress()
    services = ScrollServices(
        capture=capture or (lambda: object()), dispatch_scroll=lambda *_: None,
        checkpoint=checkpoint or (lambda: None), sleep=clock.sleep, active_monotonic=clock.monotonic,
        measure_stability=stable or (lambda *_: movement(0)),
        measure_movement=total or (lambda *_: movement()), logger=logger, scene_state=lambda _: "ready",
        recovery_generation=generation or (lambda: 0),
    )
    # Stability response is independent of movement's lower phase response.
    if stable is None:
        services = replace(services, measure_stability=lambda *_: replace(movement(0), phase_response=.99))
    return clock, logger, progress, services


def test_rejected_content_resets_stability_and_cache_to_a_new_window():
    answers = iter([movement(verified=False), movement()])
    captures = []
    def capture():
        captures.append(object())
        return captures[-1]
    clock, logger, progress, services = harness(total=lambda *_: next(answers), capture=capture)
    samples = scroll_to_bottom(make_config().scroll, 3, services, progress, reference=FrameSample(services.capture(), services.active_monotonic()), readiness=make_config().inventory_readiness)
    assert [s.frame for s in samples] == captures[-3:]
    trace = next(f for e, f in logger.events if e == "scroll_settle_trace")
    assert trace["stable_counts"] == "0,1,0,1,2"
    assert trace["total_gate_checks"] == 2
    assert progress.elapsed_ms == 600


@pytest.mark.parametrize("slow_stage", ["capture", "stability", "content"])
def test_capture_and_recognition_time_cannot_authorize_a_late_success(slow_stage):
    clock = FakeClock()
    calls = 0
    def capture():
        nonlocal calls
        calls += 1
        if slow_stage == "capture" and calls == 4:
            clock.sleep(.7)
        return object()
    def stability(*_):
        if slow_stage == "stability":
            clock.sleep(.7)
        return replace(movement(0), phase_response=.99)
    def total(*_):
        if slow_stage == "content":
            clock.sleep(.7)
        return movement()
    _, logger, progress, services = harness(clock=clock, capture=capture, stable=stability, total=total)
    with pytest.raises(StopExecution) as error:
        scroll_to_bottom(make_config().scroll, 3, services, progress, reference=FrameSample(services.capture(), services.active_monotonic()), readiness=make_config().inventory_readiness)
    assert error.value.reason is StopReason.SCROLL_VERIFICATION_FAILED
    assert progress.elapsed_ms > 800
    assert not any(e == "scroll_verified" for e, _ in logger.events)


def test_failures_do_not_restart_the_original_deadline():
    clock, logger, progress, services = harness(total=lambda *_: movement(verified=False))
    with pytest.raises(StopExecution):
        scroll_to_bottom(make_config().scroll, 3, services, progress, reference=FrameSample(services.capture(), services.active_monotonic()), readiness=make_config().inventory_readiness)
    assert clock.now == pytest.approx(.8)
    assert progress.elapsed_ms == 800


def test_repeated_capture_time_restarts_confirmation_with_fresh_samples():
    clock = FakeClock()
    captures = []
    def capture():
        captures.append(object())
        if len(captures) == 3:
            clock.now = .2
        return captures[-1]
    _, logger, _, services = harness(clock=clock, capture=capture)
    samples = scroll_to_bottom(make_config().scroll, 3, services, ScrollProgress(), reference=FrameSample(services.capture(), services.active_monotonic()), readiness=make_config().inventory_readiness)
    assert [s.frame for s in samples] == captures[-3:]
    trace = next(f for e, f in logger.events if e == 'scroll_settle_trace')
    assert trace['stable_counts'] == '0,0,1,2'


def test_stop_during_content_verification_is_observed_before_success():
    control = StopController()
    def total(*_):
        control.request(StopReason.MANUAL_STOP)
        return movement()
    _, _, _, services = harness(total=total, checkpoint=control.checkpoint)
    with pytest.raises(StopExecution) as error:
        scroll_to_bottom(make_config().scroll, 3, services, ScrollProgress(), reference=FrameSample(services.capture(), services.active_monotonic()), readiness=make_config().inventory_readiness)
    assert error.value.reason is StopReason.MANUAL_STOP


def test_recovery_invalidates_the_pending_stability_window():
    captures = 0
    generation = 0
    def capture():
        nonlocal captures, generation
        captures += 1
        if captures == 4:
            generation = 1
        return object()
    _, logger, _, services = harness(capture=capture, generation=lambda: generation)
    with pytest.raises(StopExecution, match="invalidated the scroll reference"):
        scroll_to_bottom(make_config().scroll, 3, services, ScrollProgress(), reference=FrameSample(services.capture(), services.active_monotonic()), readiness=make_config().inventory_readiness)
    assert not any(e == "scroll_verified" for e, _ in logger.events)


@pytest.mark.parametrize("pair", [movement(0, x=2), replace(movement(0), phase_response=float("nan")), movement(float("inf"))])
def test_horizontal_motion_and_nonfinite_stability_cannot_confirm_a_stop(pair):
    _, logger, _, services = harness(stable=lambda *_: replace(pair, phase_response=.99) if pair.phase_shift_x else pair)
    with pytest.raises(StopExecution):
        scroll_to_bottom(make_config().scroll, 3, services, ScrollProgress(), reference=FrameSample(services.capture(), services.active_monotonic()), readiness=make_config().inventory_readiness)
    assert next(f for e, f in logger.events if e == "scroll_settle_trace")["total_gate_checks"] == 0


@pytest.mark.parametrize("frames,starts", [
    (3, [.1, .24, .34]),
    (4, [.1, .24, .38, .54]),
])
def test_capture_and_comparison_work_preserve_the_sampling_schedule(frames, starts):
    config = make_config(stable_frames=frames)
    scroll = replace(config.scroll, minimum_settle_ms=100, settle_ms=1000)
    readiness = replace(config.inventory_readiness, minimum_frames=frames, minimum_stable_ms=200)
    clock = FakeClock()
    captures = []

    def capture():
        captures.append(clock.now)
        clock.sleep(.04)
        return object()

    def stability(*_):
        clock.sleep(.06)
        return replace(movement(0), phase_response=.99)

    _, _, progress, services = harness(clock=clock, capture=capture, stable=stability)
    samples = scroll_to_bottom(scroll, frames, services, progress,
                               reference=FrameSample(object(), clock.now), readiness=readiness)

    assert captures == pytest.approx(starts)
    assert len(samples) == frames
    assert samples[-1].captured_at - samples[0].captured_at >= .2
    assert progress.elapsed_ms <= scroll.settle_ms


@pytest.mark.parametrize("verified", [True, False])
def test_trace_output_failure_preserves_the_scroll_result(monkeypatch, verified):
    _, logger, progress, services = harness(total=lambda *_: movement(verified=verified))
    original_event = logger.event

    def event(name, **fields):
        if name == "scroll_settle_trace":
            raise OSError("trace output unavailable")
        original_event(name, **fields)

    monkeypatch.setattr(logger, "event", event)
    config = make_config()

    def run():
        return scroll_to_bottom(config.scroll, 3, services, progress,
                                reference=FrameSample(object(), 0), readiness=config.inventory_readiness)

    if verified:
        assert len(run()) == 3
        assert any(name == "scroll_verified" for name, _ in logger.events)
    else:
        with pytest.raises(StopExecution) as error:
            run()
        assert error.value.reason is StopReason.SCROLL_VERIFICATION_FAILED
        assert not any(name == "scroll_verified" for name, _ in logger.events)
