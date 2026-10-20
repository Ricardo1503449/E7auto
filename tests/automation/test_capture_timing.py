from types import SimpleNamespace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.ports import CaptureError, CaptureTiming, WindowRef
from e7auto.core.types import Rect, Size
from e7auto.runtime.context import RuntimeContext
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from e7auto.vision.frames import CoordinateTransform
from tests.helpers import FakeClock, make_config
from tests.helpers.services import FakeCapture


def context(capture):
    clock = FakeClock()
    clock.now = 10
    ctx = RuntimeContext(make_config(), SimpleNamespace(capture=capture, clock=clock),
                         StopController(), SnapshotPublisher(RuntimeSnapshot.chaos_run("capture", 1), lambda _: None))
    ctx.window = WindowRef(101, "game", "game.exe")
    ctx.baseline_bounds = Rect(0, 0, 64, 64)
    ctx.transform = CoordinateTransform(Size(64, 64), Size(64, 64))
    ctx.ensure_window = lambda **kwargs: None
    return ctx


def test_cache_preserves_timing_of_its_own_successful_frame():
    capture = FakeCapture()
    timing = CaptureTiming(100, 10_100, 2.5)
    capture.last_capture_timing = timing
    ctx = context(capture)
    pixels = ctx.capture_raw()
    saved = ctx.cached_game_frame()
    assert saved.frame is pixels and saved.timing is timing
    capture.last_capture_timing = CaptureTiming(200, 20_200, 4)
    assert saved.timing is timing
    assert saved.captured_monotonic == 10


def test_capture_without_metadata_does_not_claim_zero_source_age():
    ctx = context(FakeCapture())
    ctx.capture_raw()
    assert ctx.cached_game_frame().timing is None


def test_failed_capture_keeps_last_successful_evidence_and_timing():
    capture = FakeCapture()
    capture.last_capture_timing = CaptureTiming(100, 10_100, 2.5)
    ctx = context(capture)
    ctx.capture_raw()
    saved = ctx.cached_game_frame()
    capture.last_capture_timing = None
    capture.capture_client = lambda *args: (_ for _ in ()).throw(CaptureError("no fresh frame"))
    with pytest.raises(StopExecution) as error:
        ctx.capture_raw()
    assert error.value.reason == StopReason.CAPTURE_FAILURE
    assert ctx.cached_game_frame() is saved


@pytest.mark.parametrize('reason', [StopReason.MANUAL_STOP, StopReason.WINDOW_ABNORMAL])
def test_runtime_supplies_and_releases_capture_wait_guard(reason):
    capture = FakeCapture()
    ctx = context(capture)
    callbacks = []
    capture.set_wait_checkpoint = callbacks.append
    def interrupted(*args):
        if reason is StopReason.MANUAL_STOP:
            ctx.control.request(reason)
        callbacks[-1]()
    def guard():
        ctx.control.checkpoint()
        if callbacks:
            raise StopExecution(StopReason.WINDOW_ABNORMAL)
    ctx.ensure_window = guard
    capture.capture_client = interrupted
    with pytest.raises(StopExecution) as error:
        ctx.capture_raw()
    assert error.value.reason is reason
    assert callbacks[0] == guard and callbacks[-1] is None
    assert ctx.cached_game_frame() is None
