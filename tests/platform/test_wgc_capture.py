from __future__ import annotations

import ctypes
from collections import deque
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from e7auto import wgc_capture
from e7auto.background_windows import BackgroundCaptureError
from e7auto.config import Rect
from e7auto.ports import WindowRef
from e7auto.wgc_capture import WindowsGraphicsCaptureService, _copy_software_bitmap


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
        "_copy_software_bitmap",
        lambda _bitmap: pixels if pixels is not None else np.ones((4, 6, 4), dtype=np.uint8),
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
