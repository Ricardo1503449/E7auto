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
) -> tuple[FakeFramePool, list[str]]:
    frame_pool = FakeFramePool(frames)
    apartment_events: list[str] = []
    item = SimpleNamespace(size=SimpleNamespace(width=6, height=4))
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
        lambda _bitmap: np.ones((4, 6, 4), dtype=np.uint8),
    )
    monkeypatch.setattr(
        wgc_capture,
        "_crop_client_frame",
        lambda frame, _hwnd, _bounds: frame,
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
