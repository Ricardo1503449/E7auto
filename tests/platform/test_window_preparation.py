from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from e7auto.configuration.models import DisplayConfig
from e7auto.core.domain import StopReason
from e7auto.core.ports import DisplayGeometry, WindowGeometryChanged, WindowState
from e7auto.core.types import Rect, Size
from e7auto.runtime.stop_control import StopController, StopExecution
from e7auto.runtime.windows import prepare
from tests.helpers import FakeClock, FakeWindowService, ScriptedVision, make_config, make_dependencies


def preparation_context(*, windows=None, clock=None, mode=Size(3840, 2160), origin=(0, 0), config=None):
    windows = windows or FakeWindowService()
    clock = clock or FakeClock()
    windows.display_geometry = DisplayGeometry(
        1, r"\\.\DISPLAY1", Rect(*origin, mode.width, mode.height), mode, 96,
    )
    windows.state = WindowState(
        True, False, False,
        Rect(origin[0] + 322, origin[1] + 178, 2283, 1284),
        Rect(origin[0] + 304, origin[1] + 108, 2320, 1373),
    )
    config = config or replace(
        make_config(), baseline_client_size=Size(2322, 1306),
        display=DisplayConfig(Size(3120, 2080), Size(1920, 1080), 0.60),
    )
    deps, _, _, _, _ = make_dependencies(ScriptedVision(), windows=windows, clock=clock)
    return SimpleNamespace(
        config=config, deps=deps, control=StopController(), transition=lambda _state: None,
        window=None, baseline_bounds=None, display_geometry=None, transform=None,
    )


def assert_not_started(ctx):
    assert ctx.window is None
    assert ctx.deps.capture.calls == 0
    assert ctx.deps.inputs.actions == []
    assert ctx.deps.overlay.calls == []


@pytest.mark.parametrize(("mode", "client", "outer"), [
    (Size(3120, 2080), Size(2322, 1306), Size(2359, 1395)),
    (Size(3840, 2160), Size(2304, 1296), Size(2341, 1385)),
    (Size(1920, 1080), Size(1152, 648), Size(1189, 737)),
])
def test_measured_frame_reaches_baseline_and_scaled_targets_without_wait(mode, client, outer):
    ctx = preparation_context(mode=mode)
    prepare(ctx)

    windows = ctx.deps.windows
    assert windows.resize_calls == [client]
    assert Size(windows.outer_rect_calls[0].width, windows.outer_rect_calls[0].height) == outer
    assert ctx.deps.clock.sleeps == []
    assert ctx.transform.is_identity == (client == Size(2322, 1306))
    assert windows.state.foreground is False
    requested = next(fields for event, fields in ctx.deps.logger.events if event == "window_resize_requested")
    assert requested["frame_margins"] == (18, 70, 19, 19)
    assert requested["measured"]["window_dpi"] == 96
    assert requested["requested_outer"] == windows.outer_rect_calls[0]


def test_complete_frame_is_clamped_inside_negative_monitor_coordinates():
    ctx = preparation_context(origin=(-3840, -2160))
    ctx.deps.windows.state = WindowState(
        True, False, False, Rect(-2302, -1303, 2283, 1284), Rect(-2320, -1373, 2320, 1373),
    )
    prepare(ctx)
    assert ctx.deps.windows.outer_rect_calls == [Rect(-2341, -1385, 2341, 1385)]
    assert ctx.baseline_bounds == Rect(-2323, -1315, 2304, 1296)


def test_height_fit_and_first_request_use_the_same_measured_frame():
    ctx = preparation_context(mode=Size(1920, 1080))
    ctx.deps.windows.state = WindowState(
        True, False, False, Rect(10, 500, 580, 490), Rect(0, 0, 600, 1000),
    )
    prepare(ctx)
    assert ctx.deps.windows.outer_rect_calls == [Rect(0, 0, 1034, 1080)]
    assert ctx.baseline_bounds == Rect(10, 500, 1014, 570)
    assert ctx.deps.clock.sleeps == []


@pytest.mark.parametrize("mode", [Size(3120, 2080), Size(3840, 2160)])
def test_stable_frame_change_is_remeasured_before_correcting(mode):
    clock = FakeClock()

    class FrameChangesOnce(FakeWindowService):
        def set_outer_rect(self, window, rect, monitor):
            super().set_outer_rect(window, rect, monitor)
            if len(self.outer_rect_calls) == 1:
                client = self.state.client_bounds
                self.state = replace(self.state, client_bounds=replace(
                    client, width=client.width - 21, height=client.height - 12,
                ))
            else:
                self.corrected_at = clock.now

    ctx = preparation_context(windows=FrameChangesOnce(), clock=clock, mode=mode)
    prepare(ctx)
    first, second = ctx.deps.windows.outer_rect_calls
    assert second.width - first.width == 21
    assert second.height - first.height == 12
    assert ctx.deps.windows.corrected_at >= 0.1
    observations = [fields for event, fields in ctx.deps.logger.events if event == "window_resize_observed"]
    assert (observations[0]["width_difference"], observations[0]["height_difference"]) == (21, 12)
    assert (observations[1]["width_difference"], observations[1]["height_difference"]) == (0, 0)


def test_delayed_size_settles_without_another_resize():
    clock = FakeClock()

    class DelayedWindow(FakeWindowService):
        def set_outer_rect(self, window, rect, monitor):
            super().set_outer_rect(window, rect, monitor)
            self.intended = self.state
            self.state = replace(self.state, client_bounds=replace(
                self.state.client_bounds, width=self.state.client_bounds.width - 21,
            ))

        def measure_geometry(self, window):
            if self.outer_rect_calls and clock.now >= 0.06:
                self.state = self.intended
            return super().measure_geometry(window)

    ctx = preparation_context(windows=DelayedWindow(), clock=clock)
    prepare(ctx)
    assert len(ctx.deps.windows.outer_rect_calls) == 1
    assert clock.now == pytest.approx(0.06)
    assert any(event == "window_resize_settled" for event, _ in ctx.deps.logger.events)


def test_changed_snapshot_is_discarded_before_first_resize():
    class ChangingSnapshot(FakeWindowService):
        unstable_calls = 0

        def measure_geometry(self, window):
            if self.unstable_calls < 2:
                self.unstable_calls += 1
                raise WindowGeometryChanged("geometry changed between reads")
            return super().measure_geometry(window)

    ctx = preparation_context(windows=ChangingSnapshot())
    prepare(ctx)
    assert ctx.deps.clock.now == pytest.approx(0.04)
    assert ctx.deps.windows.outer_rect_calls == [Rect(304, 108, 2341, 1385)]


def test_unstable_snapshot_resets_the_correction_stability_window():
    clock = FakeClock()

    class UnstableWindow(FakeWindowService):
        def set_outer_rect(self, window, rect, monitor):
            super().set_outer_rect(window, rect, monitor)
            if len(self.outer_rect_calls) == 1:
                self.state = replace(self.state, client_bounds=replace(
                    self.state.client_bounds, width=self.state.client_bounds.width - 21,
                ))
            else:
                self.corrected_at = clock.now

        def measure_geometry(self, window):
            if len(self.outer_rect_calls) == 1 and 0.04 <= clock.now < 0.1:
                raise WindowGeometryChanged("window is still changing")
            return super().measure_geometry(window)

    ctx = preparation_context(windows=UnstableWindow(), clock=clock)
    prepare(ctx)
    assert ctx.deps.windows.corrected_at >= 0.2
    assert len(ctx.deps.windows.outer_rect_calls) == 2


def test_unreachable_size_stops_after_three_writes_and_one_total_deadline():
    ctx = preparation_context(windows=FakeWindowService(resize_succeeds=False))
    with pytest.raises(StopExecution) as stopped:
        prepare(ctx)
    assert stopped.value.reason is StopReason.WINDOW_ABNORMAL
    assert len(ctx.deps.windows.outer_rect_calls) == 3
    assert ctx.deps.clock.now == pytest.approx(2.0)
    assert_not_started(ctx)
    failure = next(fields for event, fields in ctx.deps.logger.events if event == "window_preparation_failed")
    assert failure["stage"] == "settle" and failure["attempts"] == 3
    assert failure["target_client"] == Size(2304, 1296)
    assert failure["measured"] is not None


@pytest.mark.parametrize("phase", ["measure", "set", "request_log"])
def test_slow_operations_consume_the_original_preparation_budget(phase):
    clock = FakeClock()

    class SlowWindow(FakeWindowService):
        def measure_geometry(self, window):
            result = super().measure_geometry(window)
            if phase == "measure":
                clock.now += 2.1
            return result

        def set_outer_rect(self, window, rect, monitor):
            super().set_outer_rect(window, rect, monitor)
            if phase == "set":
                clock.now += 2.1

    ctx = preparation_context(windows=SlowWindow(), clock=clock)
    original_event = ctx.deps.logger.event

    def event(name, **fields):
        original_event(name, **fields)
        if phase == "request_log" and name == "window_resize_requested":
            clock.now += 2.1

    ctx.deps.logger.event = event
    with pytest.raises(StopExecution) as stopped:
        prepare(ctx)
    assert stopped.value.reason is StopReason.WINDOW_ABNORMAL
    assert len(ctx.deps.windows.outer_rect_calls) == (1 if phase == "set" else 0)
    assert_not_started(ctx)


@pytest.mark.parametrize("change", ["stop", "identity", "minimized", "monitor", "mode", "dpi"])
def test_preparation_guards_stop_before_another_resize_or_capture(change):
    class InterruptedWindow(FakeWindowService):
        def set_outer_rect(self, window, rect, monitor):
            super().set_outer_rect(window, rect, monitor)
            if change == "stop":
                ctx.control.request(StopReason.MANUAL_STOP)
            elif change == "identity":
                self.state = replace(self.state, exists=False)
            elif change == "minimized":
                self.state = replace(self.state, minimized=True)
            elif change == "monitor":
                self.display_geometry = replace(self.display_geometry, monitor_id=2)
            elif change == "mode":
                self.display_geometry = replace(self.display_geometry, current_mode=Size(2560, 1440))
            else:
                self.display_geometry = replace(self.display_geometry, dpi=216)

    ctx = preparation_context(windows=InterruptedWindow())
    with pytest.raises(StopExecution) as stopped:
        prepare(ctx)
    expected = (StopReason.MANUAL_STOP if change == "stop" else
                StopReason.WINDOW_ABNORMAL if change in ("identity", "minimized") else StopReason.DISPLAY_CHANGED)
    assert stopped.value.reason is expected
    assert len(ctx.deps.windows.outer_rect_calls) == 1
    assert_not_started(ctx)


def test_manual_stop_during_wait_is_not_wrapped_as_a_window_failure():
    clock = FakeClock()
    ctx = preparation_context(windows=FakeWindowService(resize_succeeds=False), clock=clock)
    original_sleep = clock.sleep

    def sleep(seconds):
        original_sleep(seconds)
        ctx.control.request(StopReason.MANUAL_STOP)

    clock.sleep = sleep
    with pytest.raises(StopExecution) as stopped:
        prepare(ctx)
    assert stopped.value.reason is StopReason.MANUAL_STOP
    assert len(ctx.deps.windows.outer_rect_calls) == 1
    assert clock.now == pytest.approx(0.02)
    assert_not_started(ctx)


def test_stop_during_request_logging_prevents_the_first_resize():
    ctx = preparation_context()
    original_event = ctx.deps.logger.event

    def event(name, **fields):
        original_event(name, **fields)
        if name == "window_resize_requested":
            ctx.control.request(StopReason.MANUAL_STOP)

    ctx.deps.logger.event = event
    with pytest.raises(StopExecution) as stopped:
        prepare(ctx)
    assert stopped.value.reason is StopReason.MANUAL_STOP
    assert ctx.deps.windows.outer_rect_calls == []
    assert_not_started(ctx)


@pytest.mark.parametrize("invalid", ["missing_outer", "empty_client", "outside_client", "dpi", "context"])
def test_invalid_geometry_stops_without_guessing_a_frame(invalid):
    class InvalidWindow(FakeWindowService):
        def measure_geometry(self, window):
            geometry = super().measure_geometry(window)
            if invalid == "dpi":
                return replace(geometry, window_dpi=0)
            if invalid == "context":
                return replace(geometry, thread_dpi_awareness=0)
            state = geometry.state
            if invalid == "missing_outer":
                state = replace(state, outer_bounds=None)
            elif invalid == "empty_client":
                state = replace(state, client_bounds=replace(state.client_bounds, width=0))
            else:
                state = replace(state, client_bounds=replace(state.client_bounds, x=0))
            return replace(geometry, state=state)

    ctx = preparation_context(windows=InvalidWindow())
    with pytest.raises(StopExecution) as stopped:
        prepare(ctx)
    assert stopped.value.reason is StopReason.WINDOW_ABNORMAL
    assert ctx.deps.windows.outer_rect_calls == []
    assert_not_started(ctx)


@pytest.mark.parametrize("succeeds", [True, False])
def test_resize_diagnostics_do_not_replace_success_or_the_original_stop(succeeds):
    ctx = preparation_context(windows=FakeWindowService(resize_succeeds=succeeds))
    original_event = ctx.deps.logger.event

    def event(name, **fields):
        if name.startswith("window_"):
            raise OSError("log unavailable")
        original_event(name, **fields)

    ctx.deps.logger.event = event
    if succeeds:
        prepare(ctx)
        assert ctx.deps.clock.sleeps == []
    else:
        with pytest.raises(StopExecution) as stopped:
            prepare(ctx)
        assert stopped.value.reason is StopReason.WINDOW_ABNORMAL
        assert_not_started(ctx)


def test_reference_target_is_not_shrunk_to_fit_a_large_frame():
    ctx = preparation_context(mode=Size(400, 400), config=make_config())
    ctx.deps.windows.state = WindowState(True, False, False, Rect(0, 325, 64, 64), Rect(0, 0, 64, 389))
    with pytest.raises(StopExecution) as stopped:
        prepare(ctx)
    assert stopped.value.reason is StopReason.WINDOW_ABNORMAL
    assert "stage=set_outer_rect" in stopped.value.detail
    assert ctx.deps.windows.outer_rect_calls == []
    assert_not_started(ctx)
