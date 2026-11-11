"""Shared network waits preserve state and never authorize business input."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from e7auto.core.domain import OverlayActivityStatus, RuntimeSnapshot, StopReason
from e7auto.core.observations import NetworkObservation, Observation
from e7auto.core.ports import WindowRef
from e7auto.core.types import Point, Rect, Size
from e7auto.runtime.context import RuntimeContext
from e7auto.runtime.network import NetworkService
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from e7auto.vision.frames import CoordinateTransform
from tests.helpers import FakeClock, FakeInput, FakeLogger, make_config


def signal(*, loading=False, error=False, retry=False):
    def hit(name, enabled):
        return Observation(name, .99, Rect(1, 1, 4, 4), Point(3, 3)) if enabled else None
    return NetworkObservation(hit("network_loading", loading), hit("network_connection_abnormal", error),
                              hit("network_retry", retry))


class Detector:
    def observe(self, frame):
        return frame if isinstance(frame, NetworkObservation) else NetworkObservation()


def context(*, clock=None, control=None, service=None, interval_ms=100, snapshot=None):
    clock = clock or FakeClock()
    snapshots, invalidations = [], []
    deps = SimpleNamespace(clock=clock, inputs=FakeInput(), logger=FakeLogger(),
                           network=service or NetworkService(Detector(), clear_frames=3, clear_ms=500))
    config = make_config()
    config = replace(config, timing=replace(config.timing, poll_interval_ms=interval_ms))
    original = (snapshot or RuntimeSnapshot.penguins("network", 2)).with_overlay_status(
        OverlayActivityStatus.BUYING_PENGUINS)
    runtime = RuntimeContext(config, deps, control or StopController(),
                             SnapshotPublisher(original, snapshots.append), on_recovery=invalidations.append)
    runtime.window = WindowRef(123, "test", "test.exe")
    runtime.transform = CoordinateTransform(Size(100, 80), Size(100, 80))
    runtime.ensure_window = lambda **_: None
    return runtime, snapshots, invalidations


@pytest.mark.parametrize("interval_ms,expected_captures,elapsed", [(100, 6, .5), (600, 3, 1.2)])
def test_loading_only_restores_previous_status_without_retry(interval_ms, expected_captures, elapsed):
    runtime, snapshots, invalidations = context(interval_ms=interval_ms)
    captured = []
    def capture():
        captured.append(len(captured))
        return signal()
    runtime.capture_raw = capture
    runtime.recover_network(signal(loading=True))
    assert [snapshot.overlay_status for snapshot in snapshots] == [
        OverlayActivityStatus.RECONNECTING, OverlayActivityStatus.BUYING_PENGUINS]
    assert runtime.deps.inputs.actions == []
    assert len(captured) == expected_captures
    assert runtime.network_paused_seconds == pytest.approx(elapsed)
    assert runtime.active_monotonic() == pytest.approx(0)
    assert runtime.network_recovery_generation == 1 and invalidations == ["network_recovery"]
    assert not runtime.handling_network and runtime.network_status_before_reconnect is None


def test_icon_gap_text_retry_and_retry_residue_remain_one_recovery():
    runtime, snapshots, invalidations = context()
    frames = iter([signal(loading=True, error=True, retry=True), signal(), signal(),
                   signal(error=True, retry=True), signal(loading=True), signal(retry=True)]
                  + [signal() for _ in range(6)])
    runtime.capture_raw = lambda: next(frames)
    runtime.recover_network(signal(loading=True))
    assert runtime.deps.inputs.actions == [("click", Point(50, 40), None)]
    assert runtime.network_paused_seconds == pytest.approx(1.1)
    assert runtime.network_recovery_generation == 1 and len(invalidations) == 1
    assert [snapshot.overlay_status for snapshot in snapshots] == [
        OverlayActivityStatus.RECONNECTING, OverlayActivityStatus.BUYING_PENGUINS]


def test_continuously_visible_icon_has_no_automatic_wait_limit():
    runtime, snapshots, _ = context(interval_ms=1000)
    runtime.capture_raw = lambda: signal(loading=runtime.deps.clock.monotonic() < 121)
    runtime.recover_network(signal(loading=True))
    assert runtime.network_paused_seconds == pytest.approx(123)
    assert runtime.deps.inputs.actions == []
    assert snapshots[-1].overlay_status is OverlayActivityStatus.BUYING_PENGUINS


def test_manual_stop_interrupts_icon_wait_without_retry_or_status_restore():
    control = StopController()
    class StopClock(FakeClock):
        def sleep(self, seconds):
            super().sleep(seconds)
            if self.monotonic() >= .3:
                control.request(StopReason.MANUAL_STOP)
    runtime, snapshots, _ = context(clock=StopClock(), control=control)
    runtime.capture_raw = lambda: signal(loading=True)
    with pytest.raises(StopExecution) as stopped:
        runtime.recover_network(signal(loading=True))
    assert stopped.value.reason is StopReason.MANUAL_STOP
    assert runtime.deps.inputs.actions == []
    assert [snapshot.overlay_status for snapshot in snapshots] == [OverlayActivityStatus.RECONNECTING]
    assert runtime.network_paused_seconds == pytest.approx(.3)
    assert not runtime.handling_network


def test_probe_is_read_only_and_drag_releases_before_retry():
    runtime, snapshots, _ = context()
    detected = runtime.probe_network(signal(loading=True))
    assert detected.blocked and not snapshots and runtime.network_recovery_generation == 0
    assert runtime.deps.inputs.actions == []
    frames = iter([signal(), signal(loading=True), signal(error=True, retry=True)]
                  + [signal() for _ in range(6)])
    runtime.capture_raw = lambda: next(frames)
    assert not runtime.drag("network_drag", Point(10, 10), Point(30, 30), duration_ms=200, step_ms=100)
    assert [action[0] for action in runtime.deps.inputs.actions] == ["pointer_down", "pointer_up", "click"]


@pytest.mark.parametrize("kind", ["shop", "penguin", "chaos"])
def test_all_runtime_capture_paths_discard_the_interrupted_frame(kind):
    snapshot = {"shop": RuntimeSnapshot.initial("shop", (), 10),
                "penguin": RuntimeSnapshot.penguins("penguin", 2),
                "chaos": RuntimeSnapshot.chaos_run("chaos", 1)}[kind]
    runtime, _, _ = context(snapshot=snapshot)
    frames = iter([signal(loading=True)] + [signal() for _ in range(6)] + ["fresh_business_frame"])
    runtime.capture_raw = lambda: next(frames)
    assert runtime.capture() == "fresh_business_frame"
    assert runtime.network_recovery_generation == 1


def test_service_reuse_does_not_share_recovery_state_between_runs():
    shared = NetworkService(Detector(), clear_frames=3, clear_ms=500)
    first, _, _ = context(service=shared)
    second, _, _ = context(service=shared)
    for runtime in (first, second):
        runtime.capture_raw = lambda: signal()
        runtime.recover_network(signal(loading=True))
    assert first.network_recovery_generation == second.network_recovery_generation == 1
    assert first.network_paused_seconds == second.network_paused_seconds == pytest.approx(.5)
