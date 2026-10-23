from __future__ import annotations

import ctypes
from collections import deque
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest

import e7auto.platform.wgc_capture as wgc_capture
from e7auto.platform.input import BackgroundCaptureError
from e7auto.core.types import Rect
from e7auto.core.ports import WindowRef
from e7auto.platform.wgc_capture import WindowsGraphicsCaptureService, _copy_software_bitmap


class FakeReference(bytearray):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeBuffer:
    def __init__(self, raw: bytes, plane: object) -> None:
        self.reference = FakeReference(raw)
        self.plane = plane

    def __enter__(self) -> FakeBuffer:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def get_plane_count(self) -> int:
        return 1

    def get_plane_description(self, index: int) -> object:
        assert index == 0
        return self.plane

    def create_reference(self) -> FakeReference:
        return self.reference


class FakeFrame:
    def __init__(self, index: int, width: int = 6, height: int = 4) -> None:
        self.system_relative_time = timedelta(milliseconds=index)
        self.content_size = SimpleNamespace(width=width, height=height)
        self.surface = object()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeCaptureSession:
    def __init__(self, frame_pool: FakeFramePool) -> None:
        self._frame_pool = frame_pool
        self.is_cursor_capture_enabled = True
        self.closed = False

    def start_capture(self) -> None:
        assert self._frame_pool.handler is not None
        self._frame_pool.handler(self._frame_pool, object())

    def close(self) -> None:
        self.closed = True


class FakeFramePool:
    def __init__(self, frames: list[FakeFrame]) -> None:
        self.frames = deque(frames)
        self.handler = None
        self.session = FakeCaptureSession(self)
        self.closed = False
        self.removed = False

    def create_capture_session(self, _item: object) -> FakeCaptureSession:
        return self.session

    def add_frame_arrived(self, handler: object) -> int:
        self.handler = handler
        return 123

    def remove_frame_arrived(self, token: int) -> None:
        assert token == 123
        self.removed = True

    def try_get_next_frame(self) -> FakeFrame | None:
        return self.frames.popleft() if self.frames else None

    def close(self) -> None:
        self.closed = True


class FakeBitmap:
    def __enter__(self) -> FakeBitmap:
        return self

    def __exit__(self, *_args: object) -> None:
        pass


def install_fake_wgc(
    monkeypatch: pytest.MonkeyPatch,
    frames: list[FakeFrame],
    *,
    item_size: tuple[int, int] = (6, 4),
    pixels: np.ndarray | None = None,
) -> tuple[FakeFramePool, list[str]]:
    frame_pool = FakeFramePool(frames)
    apartment_events: list[str] = []
    item = SimpleNamespace(size=SimpleNamespace(width=item_size[0], height=item_size[1]))
    monkeypatch.setattr(wgc_capture, "_make_qpc_clock", lambda: lambda: 1_000_000)
    monkeypatch.setattr(wgc_capture.GraphicsCaptureSession, "is_supported", lambda: True)
    monkeypatch.setattr(
        wgc_capture,
        "_validate_window_identity",
        lambda window: window.hwnd,
    )
    monkeypatch.setattr(wgc_capture.win32gui, "IsIconic", lambda hwnd: False)
    monkeypatch.setattr(
        wgc_capture,
        "init_apartment",
        lambda apartment: apartment_events.append(f"init:{int(apartment)}"),
    )
    monkeypatch.setattr(
        wgc_capture,
        "uninit_apartment",
        lambda: apartment_events.append("uninit"),
    )
    monkeypatch.setattr(
        wgc_capture,
        "_create_direct3d_device",
        lambda: (object(), ctypes.c_void_p(), ctypes.c_void_p()),
    )
    monkeypatch.setattr(wgc_capture, "create_for_window", lambda hwnd: item)
    monkeypatch.setattr(
        wgc_capture.Direct3D11CaptureFramePool,
        "create_free_threaded",
        lambda *_args: frame_pool,
    )
    monkeypatch.setattr(
        wgc_capture.SoftwareBitmap,
        "create_copy_from_surface_async",
        lambda _surface: SimpleNamespace(get=lambda: FakeBitmap()),
    )
    monkeypatch.setattr(
        wgc_capture,
        "_software_bitmap_pixels",
        lambda _bitmap: nullcontext(pixels if pixels is not None else np.ones((4, 6, 4), dtype=np.uint8)),
    )
    return frame_pool, apartment_events


def test_bitmap_copy_honors_plane_offset_and_row_stride() -> None:
    raw = bytes([9, 9, 1, 2, 3, 4, 5, 6, 7, 8, 99, 99])
    plane = SimpleNamespace(start_index=2, width=1, height=2, stride=6)
    buffer = FakeBuffer(raw, plane)
    bitmap = SimpleNamespace(
        bitmap_pixel_format=wgc_capture.BitmapPixelFormat.BGRA8,
        lock_buffer=lambda _mode: buffer,
    )

    copied = _copy_software_bitmap(bitmap)

    assert copied.tolist() == [[[1, 2, 3, 4]], [[7, 8, 99, 99]]]
    assert buffer.reference.closed


def test_wgc_service_consumes_latest_frame_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    older = FakeFrame(1)
    latest = FakeFrame(2)
    frame_pool, apartment_events = install_fake_wgc(monkeypatch, [older, latest])
    service = WindowsGraphicsCaptureService(frame_timeout_seconds=0.01)
    window = WindowRef(101, "game", "game.exe", r"D:\game.exe", 77)

    frame = service.capture_client(window, Rect(10, 20, 6, 4))
    service.close()

    assert frame.shape == (4, 6, 4)
    assert older.closed and latest.closed
    assert frame_pool.removed and frame_pool.closed and frame_pool.session.closed
    assert apartment_events == [
        f"init:{int(wgc_capture.ApartmentType.MULTI_THREADED)}",
        "uninit",
    ]


def test_wgc_service_rejects_reused_frame_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame_pool, _ = install_fake_wgc(monkeypatch, [FakeFrame(1)])
    service = WindowsGraphicsCaptureService(frame_timeout_seconds=0.001)
    window = WindowRef(101, "game", "game.exe", r"D:\game.exe", 77)
    bounds = Rect(10, 20, 6, 4)
    service.capture_client(window, bounds)
    repeated = FakeFrame(1)
    frame_pool.frames.append(repeated)
    assert frame_pool.handler is not None
    frame_pool.handler(frame_pool, object())

    with pytest.raises(BackgroundCaptureError, match="fresh WGC frame"):
        service.capture_client(window, bounds)

    assert repeated.closed
    service.close()


def test_wgc_support_diagnostic_preserves_windows_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> bool:
        raise OSError(22, "service unavailable", None, -2147023836)

    monkeypatch.setattr(wgc_capture.GraphicsCaptureSession, "is_supported", fail)

    supported, detail = WindowsGraphicsCaptureService.support_diagnostic()

    assert not supported
    assert "OSError" in detail
    assert "service unavailable" in detail

class DiagnosticLogger:
    def __init__(self):
        self.events = []

    def event(self, name, **fields):
        self.events.append((name, fields))


def test_truncated_content_reports_initial_current_pool_and_client_sizes(monkeypatch):
    frame = FakeFrame(1, width=7, height=5)
    install_fake_wgc(monkeypatch, [frame])
    logger = DiagnosticLogger()
    monkeypatch.setattr(wgc_capture.win32gui, 'GetClientRect', lambda hwnd: (0, 0, 6, 4))
    monkeypatch.setattr(wgc_capture.win32gui, 'ClientToScreen', lambda hwnd, point: (10, 20))
    monkeypatch.setattr(wgc_capture.win32gui, 'GetWindowRect', lambda hwnd: (9, 19, 17, 25))
    monkeypatch.setattr(wgc_capture, '_extended_frame_bounds', lambda hwnd: Rect(9, 19, 8, 6))
    service = WindowsGraphicsCaptureService(logger=logger)
    window = WindowRef(101, 'game', 'game.exe', r'D:\game.exe', 77)
    try:
        with pytest.raises(BackgroundCaptureError) as error:
            service.capture_client(window, Rect(10, 20, 6, 4))
        detail = str(error.value)
        assert 'WGC content size exceeds capture surface' in detail
        assert 'initial_item_size=(6, 4)' in detail
        assert 'item_size=(6, 4)' in detail
        assert 'frame_content_size=(7, 5)' in detail
        assert 'frame_pool_size=(6, 4)' in detail
        assert 'actual_client=Rect(x=10, y=20, width=6, height=4)' in detail
        assert 'successful_frames=0' in detail
        assert [name for name, _ in logger.events] == ['wgc_initialized', 'wgc_capture_failed']
        assert frame.closed
    finally:
        service.close()


def test_diagnostic_query_failure_preserves_capture_failure(monkeypatch):
    install_fake_wgc(monkeypatch, [FakeFrame(1, width=7)])

    def fail(hwnd):
        raise OSError('window vanished')

    monkeypatch.setattr(wgc_capture.win32gui, 'GetClientRect', fail)
    service = WindowsGraphicsCaptureService()
    try:
        with pytest.raises(BackgroundCaptureError) as error:
            service.capture_client(WindowRef(101, 'game', 'game.exe', r'D:\game.exe', 77), Rect(10, 20, 6, 4))
        assert 'WGC content size exceeds capture surface' in str(error.value)
        assert 'query_failed:OSError:window vanished' in str(error.value)
    finally:
        service.close()


def test_successful_capture_logs_initialization_only(monkeypatch):
    pool, _ = install_fake_wgc(monkeypatch, [FakeFrame(1)])
    logger = DiagnosticLogger()
    service = WindowsGraphicsCaptureService(logger=logger)
    window = WindowRef(101, 'game', 'game.exe', r'D:\game.exe', 77)
    bounds = Rect(10, 20, 6, 4)
    try:
        service.capture_client(window, bounds)
        pool.frames.append(FakeFrame(2))
        service.capture_client(window, bounds)
        assert [name for name, _ in logger.events] == ['wgc_initialized']
        assert service._successful_frames == 2
    finally:
        service.close()


def test_size_diagnostics_keep_item_size_observed_with_failed_frame(monkeypatch):
    install_fake_wgc(monkeypatch, [FakeFrame(1, width=7)])

    class ChangingItem:
        def __init__(self):
            self.reads = 0

        @property
        def size(self):
            self.reads += 1
            # Initial pool 6x4, failed frame observation 8x4, diagnostic query 9x4.
            return SimpleNamespace(width={1: 6, 2: 8}.get(self.reads, 9), height=4)

    monkeypatch.setattr(wgc_capture, 'create_for_window', lambda hwnd: ChangingItem())
    service = WindowsGraphicsCaptureService()
    try:
        with pytest.raises(BackgroundCaptureError) as error:
            service.capture_client(WindowRef(101, 'game', 'game.exe', r'D:\game.exe', 77), Rect(10, 20, 6, 4))
        assert 'initial_item_size=(6, 4)' in str(error.value)
        assert ' item_size=(8, 4)' in str(error.value)
        assert 'current_item_size=(9, 4)' in str(error.value)
    finally:
        service.close()


def test_start_failure_retains_diagnostics_before_resource_release(monkeypatch):
    pool, _ = install_fake_wgc(monkeypatch, [])

    def fail():
        raise OSError('start failed')

    monkeypatch.setattr(pool.session, 'start_capture', fail)
    service = WindowsGraphicsCaptureService()
    with pytest.raises(BackgroundCaptureError) as error:
        service.capture_client(WindowRef(101, 'game', 'game.exe', r'D:\game.exe', 77), Rect(10, 20, 6, 4))
    assert 'start failed' in str(error.value)
    assert 'item_size=(6, 4)' in str(error.value)
    assert pool.closed and pool.session.closed


@pytest.mark.parametrize(
    "pool_size,content_size,dwm_bounds,outer_bounds,client_bounds,offset",
    [
        pytest.param(
            (2328, 1360), (2306, 1349), Rect(48, 37, 2306, 1349),
            (37, 37, 2365, 1397), Rect(49, 89, 2304, 1296), (1, 52),
            id="reported-win10-175-percent",
        ),
        pytest.param(
            (2326, 1366), (2326, 1366), Rect(545, 443, 2326, 1366),
            (534, 443, 2882, 1820), Rect(547, 501, 2322, 1306), (2, 58),
            id="existing-win11-200-percent",
        ),
        pytest.param(
            (10, 8), (10, 8), Rect(11, 20, 8, 7),
            (10, 20, 20, 28), Rect(12, 23, 6, 4), (2, 3),
            id="outer-window-content",
        ),
        pytest.param(
            (10, 8), (6, 4), None,
            (10, 20, 20, 28), Rect(12, 23, 6, 4), (0, 0),
            id="client-only-content-with-padding",
        ),
    ],
)
def test_capture_maps_valid_content_to_exact_client_pixels(
    monkeypatch, pool_size, content_size, dwm_bounds, outer_bounds, client_bounds, offset,
):
    # Four channels encode each source pixel's x/y coordinates, including high bytes.
    pixels = np.full((pool_size[1], pool_size[0], 4), 255, dtype=np.uint8)
    width, height = content_size
    x = np.arange(width, dtype=np.uint16)[None, :]
    y = np.arange(height, dtype=np.uint16)[:, None]
    pixels[:height, :width, 0] = x % 256
    pixels[:height, :width, 1] = x // 256
    pixels[:height, :width, 2] = y % 256
    pixels[:height, :width, 3] = y // 256
    frames = [FakeFrame(1, width, height), FakeFrame(2, width, height)]
    pool, _ = install_fake_wgc(
        monkeypatch, [frames[0]], item_size=pool_size, pixels=pixels,
    )
    monkeypatch.setattr(wgc_capture, "_extended_frame_bounds", lambda hwnd: dwm_bounds)
    monkeypatch.setattr(wgc_capture.win32gui, "GetWindowRect", lambda hwnd: outer_bounds)
    service = WindowsGraphicsCaptureService()
    window = WindowRef(101, "game", "game.exe", r"D:\game.exe", 77)
    x_offset, y_offset = offset
    expected = pixels[
        y_offset : y_offset + client_bounds.height,
        x_offset : x_offset + client_bounds.width,
    ].copy()
    try:
        first = service.capture_client(window, client_bounds)
        pool.frames.append(frames[1])
        second = service.capture_client(window, client_bounds)
        assert first.shape == (client_bounds.height, client_bounds.width, 4)
        np.testing.assert_array_equal(first, expected)
        np.testing.assert_array_equal(second, expected)
        assert service._successful_frames == 2
        assert all(frame.closed for frame in frames)
    finally:
        service.close()


@pytest.mark.parametrize("content_size", [(0, 4), (6, 0), (-1, 4), (6, -1), (7, 4), (6, 5)])
def test_capture_rejects_invalid_or_truncated_content(monkeypatch, content_size):
    frame = FakeFrame(1, *content_size)
    install_fake_wgc(monkeypatch, [frame])
    service = WindowsGraphicsCaptureService()
    error = "invalid WGC content size" if min(content_size) <= 0 else "exceeds capture surface"
    try:
        with pytest.raises(BackgroundCaptureError, match=error):
            service.capture_client(
                WindowRef(101, "game", "game.exe", r"D:\game.exe", 77), Rect(10, 20, 6, 4),
            )
        assert service._successful_frames == 0
        assert frame.closed
    finally:
        service.close()


@pytest.mark.parametrize(
    "dwm_bounds,client_bounds",
    [
        pytest.param(Rect(10, 20, 7, 5), Rect(11, 21, 4, 2), id="unmapped-content"),
        pytest.param(Rect(10, 20, 6, 4), Rect(13, 21, 4, 2), id="client-outside-right"),
        pytest.param(Rect(10, 20, 6, 4), Rect(9, 21, 4, 2), id="client-outside-left"),
        pytest.param(Rect(10, 20, 6, 4), Rect(11, 23, 4, 2), id="client-outside-bottom"),
        pytest.param(Rect(10, 20, 6, 4), Rect(11, 19, 4, 2), id="client-outside-top"),
    ],
)
def test_capture_rejects_unmapped_or_out_of_bounds_client(monkeypatch, dwm_bounds, client_bounds):
    frame = FakeFrame(1)
    install_fake_wgc(monkeypatch, [frame])
    monkeypatch.setattr(wgc_capture, "_extended_frame_bounds", lambda hwnd: dwm_bounds)
    monkeypatch.setattr(wgc_capture.win32gui, "GetWindowRect", lambda hwnd: (9, 20, 17, 26))
    service = WindowsGraphicsCaptureService()
    try:
        with pytest.raises(BackgroundCaptureError, match="cannot be mapped"):
            service.capture_client(
                WindowRef(101, "game", "game.exe", r"D:\game.exe", 77), client_bounds,
            )
        assert service._successful_frames == 0
        assert frame.closed
    finally:
        service.close()


class CaptureClock:
    def __init__(self, milliseconds=1000):
        self.ms = milliseconds

    def qpc(self):
        return round(self.ms * 10_000)

    def monotonic(self):
        return self.ms / 1000


def freshness_capture(monkeypatch, frames, *, now_ms=1000, logger=None, startup_timeout_seconds=.25,
                      frame_timeout_seconds=1., pixels=None, item_size=(6, 4)):
    pool, _ = install_fake_wgc(monkeypatch, frames, pixels=pixels, item_size=item_size)
    clock = CaptureClock(now_ms)
    monkeypatch.setattr(wgc_capture.time, "monotonic", clock.monotonic)
    service = WindowsGraphicsCaptureService(qpc_clock=clock.qpc, logger=logger,
                                           startup_timeout_seconds=startup_timeout_seconds,
                                           frame_timeout_seconds=frame_timeout_seconds)
    waits = []
    arrivals = []

    def wait(seconds):
        waits.append(seconds)
        if arrivals:
            delay_ms, frame = arrivals.pop(0)
            if delay_ms > seconds * 1000 + 1e-6:
                clock.ms += seconds * 1000
                arrivals.insert(0, (delay_ms - seconds * 1000, frame))
                return False
            clock.ms += delay_ms
            service._frame_pool.frames.append(frame)
            return True
        clock.ms += seconds * 1000
        return False

    service._frame_available = SimpleNamespace(set=lambda: None, clear=lambda: None, wait=wait)
    monkeypatch.setattr(wgc_capture.threading, 'Event',
                        lambda: SimpleNamespace(set=lambda: None, clear=lambda: None, wait=wait))
    created = []
    def create_pool(*_args):
        new_pool = FakeFramePool([]) if created else pool
        created.append(new_pool)
        return new_pool
    monkeypatch.setattr(wgc_capture.Direct3D11CaptureFramePool, 'create_free_threaded', create_pool)
    return service, pool, clock, waits, arrivals


def take_frame(service):
    return service.capture_client(WindowRef(101, "game", "game.exe", r"D:\game.exe", 77), Rect(10, 20, 6, 4))


def test_first_frame_can_arrive_after_250ms_then_running_budget_is_restored(monkeypatch):
    service, _, clock, waits, arrivals = freshness_capture(monkeypatch, [], startup_timeout_seconds=2.)
    arrivals.append((600, FakeFrame(1600)))
    try:
        take_frame(service)
        assert clock.ms == 1600 and service.last_capture_timing.source_age_ms == 0
        assert service.last_capture_timing.wait_ms == pytest.approx(600)
        assert waits == [2.]
        with pytest.raises(BackgroundCaptureError, match="fresh WGC frame"):
            take_frame(service)
        assert clock.ms == pytest.approx(5600) and waits[-2:] == pytest.approx([1., 3.])
        assert service._stats['rebuild_attempts'] == 1
    finally:
        service.close()


def test_startup_has_one_bounded_budget_and_rejects_old_frames(monkeypatch):
    service, _, clock, waits, arrivals = freshness_capture(monkeypatch, [], startup_timeout_seconds=2.)
    old = FakeFrame(1000)
    arrivals.append((500, old))
    try:
        with pytest.raises(BackgroundCaptureError, match="during startup"):
            take_frame(service)
        assert clock.ms == 3000 and waits == [2., 1.5]
        assert old.closed and service._stats['expired_frames'] == 1
        assert service.last_capture_timing is None and service._successful_frames == 0
    finally:
        service.close()


@pytest.mark.parametrize('reason', ['manual_stop', 'window_abnormal'])
@pytest.mark.parametrize('running', [False, True])
def test_short_waits_propagate_runtime_guards_without_capture_failure(monkeypatch, reason, running):
    from e7auto.core.domain import StopReason
    from e7auto.runtime.stop_control import StopExecution
    logger = DiagnosticLogger()
    service, _, clock, waits, _ = freshness_capture(
        monkeypatch, [FakeFrame(1000)] if running else [], logger=logger, startup_timeout_seconds=2.)
    if running:
        take_frame(service)
    def checkpoint():
        if clock.ms >= 1150:
            raise StopExecution(StopReason(reason))
    service.set_wait_checkpoint(checkpoint)
    try:
        with pytest.raises(StopExecution) as error:
            take_frame(service)
        assert error.value.reason is StopReason(reason)
        assert clock.ms == 1150 and max(waits) <= .05
        assert not service._stats['capture_failures']
        assert not any(name == 'wgc_capture_failed' for name, _ in logger.events)
    finally:
        service.close()


def test_guarded_startup_continues_after_empty_slices_and_does_not_sleep_full_budget(monkeypatch):
    service, _, clock, waits, arrivals = freshness_capture(monkeypatch, [], startup_timeout_seconds=2.)
    arrivals.append((320, FakeFrame(1320)))
    service.set_wait_checkpoint(lambda: None)
    try:
        take_frame(service)
        assert clock.ms == pytest.approx(1320) and max(waits) <= .05
        assert service.last_capture_timing.source_age_ms == 0
    finally:
        service.close()


def test_running_wait_recovers_after_250ms_with_fresh_post_input_frame(monkeypatch):
    service, _, clock, waits, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)])
    try:
        take_frame(service)
        service.mark_input_complete()
        service.set_wait_checkpoint(lambda: None)
        arrivals.extend([(150, FakeFrame(1000)), (450, FakeFrame(1600))])
        take_frame(service)
        assert clock.ms == pytest.approx(1600)
        assert max(waits) <= .05
        assert service.last_capture_timing.source_age_ms == 0
        assert service.last_capture_timing.source_ticks > service._input_after_ticks
        assert service._stats['capture_failures'] == 0
    finally:
        service.close()


def test_running_copy_expiry_recovers_and_diagnostics_reset_each_call(monkeypatch):
    service, _, clock, _, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)])
    original = wgc_capture._copy_client_bitmap
    try:
        take_frame(service)
        copies = []
        def slow_first_copy(bitmap, *args):
            copies.append(1)
            if len(copies) == 1:
                clock.ms += 219
            return original(bitmap, *args)
        monkeypatch.setattr(wgc_capture, '_copy_client_bitmap', slow_first_copy)
        expired, fresh = FakeFrame(1250), FakeFrame(1489)
        arrivals.extend([(250, expired), (20, fresh)])
        take_frame(service)
        assert expired.closed and fresh.closed
        assert service.last_capture_timing.source_ticks == 14_890_000
        assert service._attempt_expired_after_copy == 1
        assert service._attempt_copy_ms == pytest.approx(219)
        arrivals.append((10, FakeFrame(1499)))
        take_frame(service)
        assert service._attempt_expired_after_copy == 0
        assert service._attempt_copy_ms == 0
    finally:
        service.close()


def test_running_rejected_frames_do_not_extend_total_budget(monkeypatch):
    logger = DiagnosticLogger()
    service, _, clock, waits, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)], logger=logger)
    try:
        take_frame(service)
        service.set_wait_checkpoint(lambda: None)
        expired = [FakeFrame(1050) for _ in range(4)]
        arrivals.extend((200, frame) for frame in expired)
        with pytest.raises(BackgroundCaptureError, match='fresh WGC frame'):
            take_frame(service)
        assert clock.ms == pytest.approx(5000)
        assert max(waits) <= .05 and all(frame.closed for frame in expired)
        assert service.last_capture_timing is None
        failure = next(fields for name, fields in logger.events if name == 'wgc_capture_failed')
        assert failure['expired_before_copy'] == 4
        assert failure['expired_after_copy'] == 0
        assert failure['capture_copy_ms'] == 0
        assert failure['capture_elapsed_ms'] == pytest.approx(4000)
    finally:
        service.close()


def test_slow_copy_failure_reports_after_copy_expiry(monkeypatch):
    logger = DiagnosticLogger()
    service, pool, clock, _, _ = freshness_capture(monkeypatch, [FakeFrame(1000)], logger=logger)
    original = wgc_capture._copy_client_bitmap
    try:
        take_frame(service)
        clock.ms += 10
        pool.frames.append(FakeFrame(1010))
        def stalled_copy(bitmap, *args):
            clock.ms += 1100
            return original(bitmap, *args)
        monkeypatch.setattr(wgc_capture, '_copy_client_bitmap', stalled_copy)
        with pytest.raises(BackgroundCaptureError):
            take_frame(service)
        failure = next(fields for name, fields in logger.events if name == 'wgc_capture_failed')
        assert failure['expired_after_copy'] == 1 and failure['expired_before_copy'] == 0
        assert failure['capture_copy_ms'] == pytest.approx(1100)
        assert failure['observed_frame_age_ms'] == pytest.approx(1100)
        assert service.last_capture_timing is None
    finally:
        service.close()


@pytest.mark.parametrize('phase', ['queue', 'copy'])
def test_running_guard_releases_inflight_frames(monkeypatch, phase):
    from e7auto.core.domain import StopReason
    from e7auto.runtime.stop_control import StopExecution
    logger = DiagnosticLogger()
    service, pool, clock, _, _ = freshness_capture(monkeypatch, [FakeFrame(1000)], logger=logger)
    frames = []
    try:
        take_frame(service)
        def checkpoint():
            if clock.ms >= 1100:
                raise StopExecution(StopReason.MANUAL_STOP)
        service.set_wait_checkpoint(checkpoint)
        if phase == 'queue':
            def next_frame():
                clock.ms += 25
                frame = FakeFrame(clock.ms)
                frames.append(frame)
                return frame
            pool.try_get_next_frame = next_frame
        else:
            clock.ms += 1
            frames.append(FakeFrame(clock.ms))
            pool.frames.extend(frames)
            original = wgc_capture._copy_client_bitmap
            def copy_then_stop(bitmap, *args):
                clock.ms = 1100
                return original(bitmap, *args)
            monkeypatch.setattr(wgc_capture, '_copy_client_bitmap', copy_then_stop)
        with pytest.raises(StopExecution) as error:
            take_frame(service)
        assert error.value.reason is StopReason.MANUAL_STOP
        assert clock.ms == 1100 and all(frame.closed for frame in frames)
        assert service.last_capture_timing is None
        assert service._stats['capture_failures'] == 0
        assert not any(name == 'wgc_capture_failed' for name, _ in logger.events)
    finally:
        service.close()


@pytest.mark.parametrize("age_ms", [0, 10, 99, 100])
def test_fresh_queued_frame_is_used_without_waiting_for_another_frame(monkeypatch, age_ms):
    frame = FakeFrame(1000 - age_ms)
    service, _, _, waits, _ = freshness_capture(monkeypatch, [frame])
    try:
        take_frame(service)
        assert waits == []
        assert service.last_capture_timing.source_age_ms == age_ms
        assert service.last_capture_timing.wait_ms == 0
        assert frame.closed
    finally:
        service.close()


def test_old_but_increasing_queued_frames_are_rejected_before_bitmap_copy(monkeypatch):
    old, newer_old, fresh = FakeFrame(1), FakeFrame(2), FakeFrame(20_010)
    service, _, _, waits, arrivals = freshness_capture(monkeypatch, [old, newer_old], now_ms=20_000)
    original = wgc_capture._copy_client_bitmap
    copies = []
    monkeypatch.setattr(wgc_capture, "_copy_client_bitmap", lambda bitmap, *args: (copies.append(1), original(bitmap, *args))[1])
    arrivals.append((10, fresh))
    try:
        take_frame(service)
        assert copies == [1]  # Expired images never incur the native bitmap copy.
        assert waits == [pytest.approx(.25)]
        assert all(frame.closed for frame in (old, newer_old, fresh))
        assert service.last_capture_timing.source_ticks == 200_100_000
        assert service.last_capture_timing.source_age_ms == 0
        assert service.last_capture_timing.wait_ms == pytest.approx(10)
        assert service._stats["expired_frames"] == 1
        assert service._stats["superseded_frames"] == 1
    finally:
        service.close()


@pytest.mark.parametrize("source_ms", [999, 1000, 1001, 1006, 1010, 1028, 1029, 1049, 1050])
def test_frame_not_past_input_tolerance_is_not_accepted(monkeypatch, source_ms):
    old, fresh = FakeFrame(source_ms), FakeFrame(1051)
    service, _, _, waits, arrivals = freshness_capture(monkeypatch, [old])
    service.mark_input_complete()
    arrivals.append((52, fresh))
    try:
        take_frame(service)
        assert len(waits) == 1 and old.closed and fresh.closed
        assert service.last_capture_timing.source_ticks > service._input_after_ticks + service._future_tolerance_ticks
        assert service._stats["pre_input_frames"] == 1
    finally:
        service.close()


def test_repeated_fresh_frame_is_rejected_then_new_timestamp_is_accepted(monkeypatch):
    service, pool, clock, waits, arrivals = freshness_capture(monkeypatch, [FakeFrame(995)])
    try:
        take_frame(service)
        repeated = FakeFrame(995)
        pool.frames.append(repeated)
        arrivals.append((1, FakeFrame(1001)))
        take_frame(service)
        assert len(waits) == 1 and repeated.closed
        assert service._stats["repeated_frames"] == 1
        assert service.last_capture_timing.received_ticks == clock.qpc()
    finally:
        service.close()


def test_future_source_timestamp_is_rejected(monkeypatch):
    future = FakeFrame(2000)
    service, _, _, _, arrivals = freshness_capture(monkeypatch, [future])
    arrivals.append((1, FakeFrame(1001)))
    try:
        take_frame(service)
        assert future.closed and service._stats["invalid_time_frames"] == 1
        assert service.last_capture_timing.source_age_ms == 0
    finally:
        service.close()


@pytest.mark.parametrize("lead_ms", [4.7928, 6.4642, 6.4752, 6.7141, 10, 28.1811, 29.387, 49.9999, 50])
def test_small_future_timestamps_from_live_logs_are_delivered_without_negative_age(monkeypatch, lead_ms):
    frame = FakeFrame(1010)
    service, _, clock, waits, _ = freshness_capture(monkeypatch, [frame], now_ms=1010 - lead_ms)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.ms += seconds * 1000

    monkeypatch.setattr(wgc_capture.time, "sleep", sleep)
    try:
        take_frame(service)
        timing = service.last_capture_timing
        assert frame.closed and waits == []
        assert timing.source_ticks == 10_100_000
        assert timing.received_ticks >= timing.source_ticks
        assert timing.source_age_ms == pytest.approx(0)
        assert sum(sleeps) * 1000 == pytest.approx(lead_ms)
        assert timing.wait_ms == pytest.approx(lead_ms)
        assert service._stats["invalid_time_frames"] == 0
    finally:
        service.close()


def test_copy_time_can_absorb_future_lead_without_an_extra_wait(monkeypatch):
    logger = DiagnosticLogger()
    frame = FakeFrame(1010)
    service, _, clock, waits, _ = freshness_capture(monkeypatch, [frame], now_ms=1003.2859, logger=logger)
    original = wgc_capture._copy_client_bitmap

    def copy(bitmap, *args):
        clock.ms += 10
        return original(bitmap, *args)

    monkeypatch.setattr(wgc_capture, "_copy_client_bitmap", copy)
    monkeypatch.setattr(wgc_capture.time, "sleep", lambda _: pytest.fail("Copy already consumed the lead"))
    try:
        take_frame(service)
        assert frame.closed and waits == []
        assert service.last_capture_timing.source_age_ms == pytest.approx(3.2859)
        assert service.last_capture_timing.wait_ms == 0
        assert [name for name, _ in logger.events] == ["wgc_initialized"]
    finally:
        service.close()
    summary = next(fields for name, fields in logger.events if name == "wgc_capture_summary")
    assert summary["future_frame_tolerance_ms"] == 50
    assert summary["tolerated_future_frames"] == 1
    assert summary["accepted_future_lead_max_ms"] == pytest.approx(6.7141)
    assert summary["future_catchup_waits"] == 0 and summary["future_catchup_wait_ms"] == 0


@pytest.mark.parametrize("lead_ms", [50.0001, 60, 1000])
def test_future_lead_above_tolerance_is_still_rejected_before_copy(monkeypatch, lead_ms):
    frame = FakeFrame(2000)
    service, _, _, _, _ = freshness_capture(monkeypatch, [frame], now_ms=2000 - lead_ms)
    monkeypatch.setattr(wgc_capture, "_copy_client_bitmap", lambda *_: pytest.fail("Excessive future lead"))
    try:
        with pytest.raises(BackgroundCaptureError, match="fresh WGC frame"):
            take_frame(service)
        assert frame.closed and service.last_capture_timing is None
        assert service._stats["invalid_time_frames"] == 1
        assert service._stats["future_catchup_waits"] == 0
    finally:
        service.close()


def test_running_capture_accepts_logged_lead_after_the_50ms_input_boundary(monkeypatch):
    old, fresh = FakeFrame(1029), FakeFrame(1079.387)
    service, pool, clock, waits, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)])
    monkeypatch.setattr(wgc_capture.time, "sleep", lambda seconds: setattr(clock, "ms", clock.ms + seconds * 1000))
    try:
        take_frame(service)
        service.mark_input_complete()
        pool.frames.append(old)
        arrivals.append((50, fresh))
        take_frame(service)
        assert old.closed and fresh.closed
        assert service._stats["pre_input_frames"] == 1
        assert service._stats["invalid_time_frames"] == 0
        assert service._stats["future_catchup_waits"] == 1
        assert clock.ms == pytest.approx(1079.387)
        assert service.last_capture_timing.source_ticks == 10_793_870
        assert service.last_capture_timing.source_ticks > service._input_after_ticks + 500_000
        assert service.last_capture_timing.received_ticks >= service.last_capture_timing.source_ticks
        assert service.last_capture_timing.source_age_ms == pytest.approx(0)
    finally:
        service.close()


@pytest.mark.parametrize("reason", ["manual_stop", "window_abnormal"])
def test_50ms_catchup_preserves_stop_and_window_guards(monkeypatch, reason):
    from e7auto.core.domain import StopReason
    from e7auto.runtime.stop_control import StopExecution
    frame = FakeFrame(1050)
    service, _, clock, _, _ = freshness_capture(monkeypatch, [frame])
    monkeypatch.setattr(wgc_capture.time, "sleep", lambda seconds: setattr(clock, "ms", clock.ms + seconds * 1000))

    def checkpoint():
        if clock.ms >= 1010:
            raise StopExecution(StopReason(reason))

    service.set_wait_checkpoint(checkpoint)
    try:
        with pytest.raises(StopExecution) as error:
            take_frame(service)
        assert error.value.reason is StopReason(reason)
        assert frame.closed and service.last_capture_timing is None
        assert clock.ms == pytest.approx(1050)
        assert service._stats["capture_failures"] == 0
    finally:
        service.close()


def test_future_catchup_uses_remaining_deadline_instead_of_resetting_it(monkeypatch):
    old, future = FakeFrame(1), FakeFrame(1255)
    service, _, clock, waits, arrivals = freshness_capture(monkeypatch, [old])
    arrivals.append((248, future))
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.ms += seconds * 1000

    monkeypatch.setattr(wgc_capture.time, "sleep", sleep)
    try:
        with pytest.raises(BackgroundCaptureError, match="fresh WGC frame"):
            take_frame(service)
        assert clock.ms == pytest.approx(1250)
        assert sum(sleeps) * 1000 == pytest.approx(2)
        assert waits == [pytest.approx(.25)]
        assert old.closed and future.closed and service.last_capture_timing is None
        assert service._attempt_wait_ms == pytest.approx(250)
        assert service._stats["future_catchup_waits"] == 1
        assert service._stats["deadline_frames"] == 1
    finally:
        service.close()


def test_catchup_oversleep_does_not_allow_an_expired_frame(monkeypatch):
    frame = FakeFrame(1007)
    service, _, clock, _, _ = freshness_capture(monkeypatch, [frame])
    monkeypatch.setattr(wgc_capture.time, "sleep", lambda _: setattr(clock, "ms", clock.ms + 110))
    try:
        with pytest.raises(BackgroundCaptureError, match="fresh WGC frame"):
            take_frame(service)
        assert frame.closed and service.last_capture_timing is None
        assert service._stats["expired_frames"] == 1
    finally:
        service.close()


def test_copy_that_makes_a_frame_expired_is_discarded(monkeypatch):
    old, fresh = FakeFrame(950), FakeFrame(1061)
    service, _, clock, _, arrivals = freshness_capture(monkeypatch, [old])
    original = wgc_capture._copy_client_bitmap
    copies = []

    def slow_first_copy(bitmap, *args):
        copies.append(1)
        if len(copies) == 1:
            clock.ms += 60
        return original(bitmap, *args)

    monkeypatch.setattr(wgc_capture, "_copy_client_bitmap", slow_first_copy)
    arrivals.append((1, fresh))
    try:
        take_frame(service)
        assert copies == [1, 1] and old.closed and fresh.closed
        assert service._stats["expired_frames"] == 1
        assert service.last_capture_timing.source_ticks == 10_610_000
    finally:
        service.close()


def test_rejected_frames_share_one_deadline_and_never_return_old_pixels(monkeypatch):
    frames = [FakeFrame(1) for _ in range(5)]
    service, _, clock, waits, arrivals = freshness_capture(monkeypatch, frames[:1])
    arrivals.extend((50, frame) for frame in frames[1:])
    try:
        with pytest.raises(BackgroundCaptureError, match="fresh WGC frame"):
            take_frame(service)
        assert clock.ms == pytest.approx(1250)
        assert waits == pytest.approx([.25, .20, .15, .10, .05])
        assert all(frame.closed for frame in frames)
        assert service.last_capture_timing is None
        assert service._stats["expired_frames"] == 5
    finally:
        service.close()


def test_continuously_filled_queue_cannot_bypass_capture_deadline(monkeypatch):
    service, pool, clock, waits, _ = freshness_capture(monkeypatch, [])
    frames = []

    def endless():
        clock.ms += 10
        frame = FakeFrame(clock.ms)
        frames.append(frame)
        return frame

    pool.try_get_next_frame = endless
    try:
        with pytest.raises(BackgroundCaptureError, match="fresh WGC frame"):
            take_frame(service)
        assert clock.ms == 1250 and waits == []
        assert len(frames) == 25 and all(frame.closed for frame in frames)
    finally:
        service.close()


def test_input_clock_failure_does_not_interrupt_input_but_blocks_capture(monkeypatch):
    service, _, _, _, _ = freshness_capture(monkeypatch, [FakeFrame(995)])
    service._qpc_clock = lambda: (_ for _ in ()).throw(RuntimeError("clock failed"))
    service.mark_input_complete()  # Must not strand a pressed button.
    try:
        with pytest.raises(BackgroundCaptureError, match="input completion timestamp unavailable"):
            take_frame(service)
        assert service.last_capture_timing is None
    finally:
        service.close()


def test_many_captures_log_only_one_final_freshness_summary(monkeypatch):
    logger = DiagnosticLogger()
    service, pool, clock, waits, _ = freshness_capture(monkeypatch, [], logger=logger)
    for _ in range(20):
        clock.ms += 1
        pool.frames.append(FakeFrame(clock.ms - 1))
        take_frame(service)
    assert waits == []
    assert [name for name, _ in logger.events] == ["wgc_initialized"]
    service.close()
    service.close()
    summaries = [fields for name, fields in logger.events if name == "wgc_capture_summary"]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["capture_calls"] == summary["accepted_frames"] == 20
    assert summary["capture_failures"] == summary["waited_calls"] == 0
    assert summary["wait_total_ms"] == 0 and summary["source_age_max_ms"] == 1
    assert summary["max_frame_age_ms"] == 100 and summary["frame_timeout_ms"] == 1000


def test_start_failure_summary_includes_the_failed_attempt(monkeypatch):
    logger = DiagnosticLogger()
    service, pool, _, _, _ = freshness_capture(monkeypatch, [], logger=logger)
    pool.session.start_capture = lambda: (_ for _ in ()).throw(RuntimeError("start failed"))
    with pytest.raises(BackgroundCaptureError, match="start failed"):
        take_frame(service)
    service.close()
    summaries = [fields for name, fields in logger.events if name == "wgc_capture_summary"]
    assert len(summaries) == 1 and summaries[0]["capture_failures"] == 1
    assert summaries[0]["source_age_mean_ms"] == summaries[0]["source_age_max_ms"] == "unknown"
    assert pool.closed and pool.session.closed


@pytest.mark.parametrize("field,value", [
    ("frame_timeout_seconds", 0), ("frame_timeout_seconds", float("inf")),
    ("startup_timeout_seconds", 0), ("startup_timeout_seconds", float("nan")),
    ("max_frame_age_seconds", 0), ("max_frame_age_seconds", float("nan")),
    ("future_frame_tolerance_seconds", -1), ("future_frame_tolerance_seconds", float("inf")),
])
def test_invalid_freshness_limits_are_rejected(field, value):
    with pytest.raises(ValueError):
        WindowsGraphicsCaptureService(**{field: value})


@pytest.mark.parametrize("feature", ["shop", "penguin", "chaos"])
def test_production_input_marks_the_capture_used_by_each_feature(monkeypatch, feature):
    import e7auto.bootstrap as bootstrap
    from e7auto.features.shop.vision import ShopVision
    from e7auto.features.penguin.vision import PenguinVision
    from e7auto.features.chaos.vision import ChaosVision

    class Capture:
        def __init__(self, **kwargs):
            self.marks = 0
            assert kwargs['frame_timeout_seconds'] == 0.75
            assert kwargs['recovery_timeout_seconds'] == 3.
            assert kwargs['rebuild_cooldown_seconds'] == 60.
            assert kwargs['rebuild_window_seconds'] == 600.
            assert kwargs['rebuild_max_attempts'] == 3

        def mark_input_complete(self):
            self.marks += 1

    for cls in (ShopVision, PenguinVision, ChaosVision):
        monkeypatch.setattr(cls, "__init__", lambda self, *args: None)
    monkeypatch.setattr(bootstrap, "TemplateRepository", lambda config: object())
    monkeypatch.setattr(wgc_capture, "WindowsGraphicsCaptureService", Capture)
    config = SimpleNamespace(timing=SimpleNamespace(
        capture_frame_timeout_ms=750, capture_recovery_timeout_ms=3000,
        capture_rebuild_cooldown_ms=60000, capture_rebuild_window_ms=600000,
        capture_rebuild_max_attempts=3))
    session = bootstrap.create_production_session(config, feature, None, None, lambda _: None)
    deps = session._dependencies
    sent = []
    deps.inputs._post_message = lambda *args: sent.append(args)
    deps.inputs._dispatch(101, 0x202, 0, 0)
    assert len(sent) == 1 and deps.capture.marks == 1



def test_rebuild_preserves_device_input_watermark_and_isolates_old_callback(monkeypatch):
    logger = DiagnosticLogger()
    service, old_pool, clock, _, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)], logger=logger)
    apartments = []
    monkeypatch.setattr(wgc_capture, 'init_apartment', lambda _: apartments.append('init'))
    monkeypatch.setattr(wgc_capture, 'uninit_apartment', lambda: apartments.append('uninit'))
    try:
        take_frame(service)
        device, old_callback, old_event = service._device, old_pool.handler, service._frame_available
        service.mark_input_complete()
        input_ticks = service._input_after_ticks
        old, fresh = FakeFrame(1000), FakeFrame(2120)
        arrivals.extend([(1100, old), (20, fresh)])
        take_frame(service)
        assert service._device is device and service._input_after_ticks == input_ticks
        assert service._last_frame_ticks == 21_200_000 and service._stats['repeated_frames'] == 1
        assert old.closed and fresh.closed and old_pool.closed and old_pool.session.closed and old_pool.removed
        assert service._stats['rebuild_attempts'] == service._stats['rebuild_successes'] == 1
        assert service._stats['capture_calls'] == 2 and service._stats['capture_failures'] == 0
        assert apartments == ['init'] and not service._closed
        old_signals, new_signals = [], []
        old_event.set = lambda: old_signals.append(1)
        service._frame_available.set = lambda: new_signals.append(1)
        old_callback(old_pool, None)
        assert old_signals == [1] and new_signals == []
        assert [name for name, _ in logger.events] == ['wgc_initialized', 'wgc_recovery_started']
    finally:
        service.close()
        service.close()
    assert apartments == ['init', 'uninit']
    assert sum(name == 'wgc_capture_summary' for name, _ in logger.events) == 1


@pytest.mark.parametrize('arrival_ms,succeeds', [(3999, True), (4000, False)])
def test_recovery_has_exactly_one_additional_three_second_window(monkeypatch, arrival_ms, succeeds):
    service, _, clock, _, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)])
    try:
        take_frame(service)
        arrivals.append((arrival_ms, FakeFrame(1000 + arrival_ms)))
        if succeeds:
            take_frame(service)
            assert service._stats['rebuild_successes'] == 1
        else:
            with pytest.raises(BackgroundCaptureError, match='fresh WGC frame'):
                take_frame(service)
            assert service.last_capture_timing is None
            assert service._stats['rebuild_failures'] == service._stats['capture_failures'] == 1
        assert clock.ms == pytest.approx(1000 + arrival_ms)
        assert service._stats['rebuild_attempts'] == 1
    finally:
        service.close()


@pytest.mark.parametrize('since_rebuild_ms,expected_attempts', [(59999, 1), (60000, 2)])
def test_cooldown_boundary_and_limited_wait_can_recover(monkeypatch, since_rebuild_ms, expected_attempts):
    service, _, clock, _, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)])
    try:
        take_frame(service)
        arrivals.append((1100, FakeFrame(2100)))
        take_frame(service)
        clock.ms = service._last_rebuild_time * 1000 + since_rebuild_ms - 1000
        arrivals.append((1100, FakeFrame(clock.ms + 1100)))
        previous_pool = service._frame_pool
        take_frame(service)
        assert service._stats['rebuild_attempts'] == expected_attempts
        assert service._stats['capture_failures'] == 0
        if expected_attempts == 1:
            assert service._frame_pool is previous_pool and not previous_pool.closed
            assert service._stats['extended_wait_successes'] == service._stats['rebuild_rate_limit_hits'] == 1
        else:
            assert service._frame_pool is not previous_pool and previous_pool.closed
    finally:
        service.close()


def test_rolling_window_preserves_successful_attempts_and_expires_at_ten_minutes(monkeypatch):
    logger = DiagnosticLogger()
    service, _, clock, _, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)], logger=logger)
    try:
        take_frame(service)
        for attempt in range(3):
            if attempt:
                clock.ms = service._last_rebuild_time * 1000 + 59000
            arrivals.append((1100, FakeFrame(clock.ms + 1100)))
            take_frame(service)
        first_attempt = service._rebuild_times[0]
        clock.ms = service._last_rebuild_time * 1000 + 59000
        arrivals.append((1100, FakeFrame(clock.ms + 1100)))
        take_frame(service)
        assert service._stats['rebuild_attempts'] == 3
        assert [f['rate_limit_reason'] for n, f in logger.events if n == 'wgc_recovery_skipped'] == ['window_limit']
        clock.ms = first_attempt * 1000 + 599000
        arrivals.append((1100, FakeFrame(clock.ms + 1100)))
        take_frame(service)
        assert service._stats['rebuild_attempts'] == service._stats['rebuild_successes'] == 4
        assert len(service._rebuild_times) == 3 and first_attempt not in service._rebuild_times
    finally:
        service.close()


def test_rate_limited_empty_session_stops_after_one_extra_window(monkeypatch):
    logger = DiagnosticLogger()
    service, _, clock, _, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)], logger=logger)
    try:
        take_frame(service)
        arrivals.append((1100, FakeFrame(2100)))
        take_frame(service)
        pool, started = service._frame_pool, clock.ms
        with pytest.raises(BackgroundCaptureError, match='fresh WGC frame'):
            take_frame(service)
        assert clock.ms - started == pytest.approx(4000)
        assert service._frame_pool is pool and not pool.closed
        assert service._stats['rebuild_attempts'] == 1 and service._stats['rebuild_rate_limit_hits'] == 1
        completed = [f for n, f in logger.events if n == 'wgc_recovery_completed'][-1]
        assert completed['action'] == 'extended_wait' and completed['outcome'] == 'failed'
        assert completed['recovery_elapsed_ms'] == pytest.approx(3000)
    finally:
        service.close()


def test_recovery_rejects_bad_frames_without_renewing_deadline(monkeypatch):
    service, _, clock, _, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)])
    try:
        take_frame(service)
        bad = [FakeFrame(1000), FakeFrame(1050), FakeFrame(-1), FakeFrame(100000)]
        arrivals.extend([(1100, bad[0])] + [(200, frame) for frame in bad[1:]])
        with pytest.raises(BackgroundCaptureError, match='fresh WGC frame'):
            take_frame(service)
        assert clock.ms == pytest.approx(5000) and all(frame.closed for frame in bad)
        assert service._stats['repeated_frames'] == 1
        assert service._stats['expired_frames'] == 1 and service._stats['invalid_time_frames'] == 2
        assert service._last_frame_ticks == 10_000_000 and service.last_capture_timing is None
    finally:
        service.close()


@pytest.mark.parametrize('phase', ['waiting', 'creating_pool'])
@pytest.mark.parametrize('reason', ['manual_stop', 'window_abnormal'])
def test_recovery_preserves_guard_reason_and_cleans_partial_session(monkeypatch, phase, reason):
    from e7auto.core.domain import StopReason
    from e7auto.runtime.stop_control import StopExecution
    logger = DiagnosticLogger()
    service, old_pool, clock, waits, _ = freshness_capture(monkeypatch, [FakeFrame(1000)], logger=logger)
    new_pool = None
    try:
        take_frame(service)
        def checkpoint():
            interrupted = clock.ms >= 2500 if phase == 'waiting' else service._frame_pool not in (None, old_pool)
            if interrupted:
                raise StopExecution(StopReason(reason))
        service.set_wait_checkpoint(checkpoint)
        with pytest.raises(StopExecution) as error:
            take_frame(service)
        assert error.value.reason is StopReason(reason)
        assert service._stats['capture_failures'] == service._stats['rebuild_failures'] == 0
        assert service._stats['rebuild_interruptions'] == 1 and max(waits) <= .05
        assert not any(n == 'wgc_capture_failed' for n, _ in logger.events)
        new_pool = service._frame_pool
    finally:
        service.close()
    assert old_pool.closed and new_pool.closed and not service._apartment_initialized


@pytest.mark.parametrize('operation', ['callback', 'session', 'pool'])
def test_cleanup_failure_never_creates_another_session(monkeypatch, operation):
    service, pool, clock, _, _ = freshness_capture(monkeypatch, [FakeFrame(1000)])
    try:
        take_frame(service)
        def fail(*_args):
            raise RuntimeError('cleanup failed')
        if operation == 'callback':
            pool.remove_frame_arrived = fail
        elif operation == 'session':
            pool.session.close = fail
        else:
            pool.close = fail
        with pytest.raises(BackgroundCaptureError, match='unable to release'):
            take_frame(service)
        assert clock.ms == pytest.approx(2000)
        assert len(service._rebuild_times) == service._stats['rebuild_failures'] == 1
        assert pool.session.closed or operation == 'session'
        assert pool.closed or operation == 'pool'
        assert service._successful_frames == 1
    finally:
        service.close()


def test_rebuild_native_error_is_a_capture_failure_and_retains_partial_resources(monkeypatch):
    service, old_pool, _, _, _ = freshness_capture(monkeypatch, [FakeFrame(1000)])
    try:
        take_frame(service)
        def fail(*_args):
            raise RuntimeError('new pool failed')
        monkeypatch.setattr(wgc_capture.Direct3D11CaptureFramePool, 'create_free_threaded', fail)
        with pytest.raises(BackgroundCaptureError, match='new pool failed'):
            take_frame(service)
        assert old_pool.closed and service._item is not None and service._apartment_initialized
        assert service._stats['rebuild_attempts'] == service._stats['rebuild_failures'] == 1
    finally:
        service.close()
    assert service._item is None and not service._apartment_initialized


def test_telemetry_failure_does_not_block_recovery_or_final_cleanup(monkeypatch):
    class BrokenLogger:
        def event(self, *_args, **_kwargs):
            raise OSError('log unavailable')
    service, pool, _, _, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)], logger=BrokenLogger())
    try:
        take_frame(service)
        arrivals.append((1100, FakeFrame(2100)))
        take_frame(service)
        assert service._stats['rebuild_successes'] == 1 and pool.closed
    finally:
        service.close()
    assert not service._apartment_initialized


@pytest.mark.parametrize('width,height', [(2322, 1306), (1536, 864)])
def test_reference_and_scaled_capture_keep_original_client_pixels(monkeypatch, width, height):
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[0, 0], pixels[-1, -1] = (1, 2, 3, 255), (5, 6, 7, 255)
    service, _, _, _, _ = freshness_capture(
        monkeypatch, [FakeFrame(1000, width, height)], pixels=pixels, item_size=(width, height))
    try:
        frame = service.capture_client(WindowRef(101, 'game', 'game.exe'), Rect(10, 20, width, height))
        assert np.array_equal(frame, pixels) and frame.shape == (height, width, 4)
        assert service._stats['rebuild_attempts'] == 0
    finally:
        service.close()



def test_slow_success_log_cannot_age_the_recovered_frame_before_delivery(monkeypatch):
    clock_holder = []
    class SlowLogger(DiagnosticLogger):
        def event(self, event, **fields):
            super().event(event, **fields)
            if event == 'wgc_recovery_completed':
                clock_holder[0].ms += 500
    logger = SlowLogger()
    service, _, clock, _, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)], logger=logger)
    clock_holder.append(clock)
    try:
        take_frame(service)
        arrivals.append((1100, FakeFrame(2100)))
        take_frame(service)
        assert clock.ms == pytest.approx(2100)
        assert clock.qpc() - service.last_capture_timing.source_ticks == 0
    finally:
        service.close()
    completed = [fields for event, fields in logger.events if event == 'wgc_recovery_completed']
    assert len(completed) == 1 and completed[0]['recovery_elapsed_ms'] == pytest.approx(100)


@pytest.mark.parametrize('field,value', [
    ('recovery_timeout_seconds', 0), ('recovery_timeout_seconds', True),
    ('rebuild_cooldown_seconds', -1), ('rebuild_cooldown_seconds', float('nan')),
    ('rebuild_window_seconds', 0), ('rebuild_window_seconds', float('inf')),
    ('rebuild_max_attempts', 0), ('rebuild_max_attempts', True), ('rebuild_max_attempts', 2.5),
])
def test_invalid_recovery_limits_are_rejected(field, value):
    with pytest.raises(ValueError):
        WindowsGraphicsCaptureService(**{field: value})



@pytest.mark.parametrize('failure', ['content_size', 'identity', 'minimized'])
def test_running_non_timeout_errors_never_authorize_rebuild(monkeypatch, failure):
    from e7auto.platform.input import BackgroundInputError
    service, pool, clock, _, _ = freshness_capture(monkeypatch, [FakeFrame(1000)])
    try:
        take_frame(service)
        if failure == 'content_size':
            clock.ms += 10
            pool.frames.append(FakeFrame(1010, width=7))
        elif failure == 'identity':
            def invalid(_window):
                raise BackgroundInputError('different process')
            monkeypatch.setattr(wgc_capture, '_validate_window_identity', invalid)
        else:
            monkeypatch.setattr(wgc_capture.win32gui, 'IsIconic', lambda _: True)
        with pytest.raises(BackgroundCaptureError):
            take_frame(service)
        assert service._stats['rebuild_attempts'] == 0 and service._frame_pool is pool
    finally:
        service.close()


def test_recovery_wait_budget_starts_after_native_session_initialization(monkeypatch):
    logger = DiagnosticLogger()
    service, _, clock, _, arrivals = freshness_capture(monkeypatch, [FakeFrame(1000)], logger=logger)
    try:
        take_frame(service)
        factory = wgc_capture.Direct3D11CaptureFramePool.create_free_threaded
        def slow_factory(*args):
            clock.ms += 5000
            arrivals.append((2999, FakeFrame(clock.ms + 2999)))
            return factory(*args)
        monkeypatch.setattr(wgc_capture.Direct3D11CaptureFramePool, 'create_free_threaded', slow_factory)
        take_frame(service)
        assert clock.ms == pytest.approx(9999) and service._stats['rebuild_successes'] == 1
    finally:
        service.close()
    completed = next(fields for event, fields in logger.events if event == 'wgc_recovery_completed')
    assert completed['rebuild_elapsed_ms'] == pytest.approx(5000)
    assert completed['recovery_elapsed_ms'] == pytest.approx(2999)
