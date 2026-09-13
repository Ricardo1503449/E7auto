from __future__ import annotations

import ctypes
import threading
import time
from typing import Callable
from ctypes import wintypes

import numpy as np
import win32gui
from winrt.runtime import ApartmentType, init_apartment, uninit_apartment
from winrt.windows.graphics.capture import (
    Direct3D11CaptureFrame,
    Direct3D11CaptureFramePool,
    GraphicsCaptureSession,
)
from winrt.windows.graphics.capture.interop import create_for_window
from winrt.windows.graphics.directx import DirectXPixelFormat
from winrt.windows.graphics.directx.direct3d11.interop import (
    create_direct3d11_device_from_dxgi_device,
)
from winrt.windows.graphics.imaging import (
    BitmapBufferAccessMode,
    BitmapPixelFormat,
    SoftwareBitmap,
)

from .background_windows import (
    BackgroundCaptureError,
    BackgroundInputError,
    _validate_window_identity,
)
from .config import Rect
from .ports import Frame, TextRunLogger, WindowRef


_D3D_DRIVER_TYPE_HARDWARE = 1
_D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
_D3D11_SDK_VERSION = 7
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


def _release_com_pointer(pointer: ctypes.c_void_p) -> None:
    if not pointer.value:
        return
    vtable = ctypes.cast(
        pointer,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ).contents
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    release(pointer)
    pointer.value = None


def _create_direct3d_device() -> tuple[object, ctypes.c_void_p, ctypes.c_void_p]:
    create_device = ctypes.windll.d3d11.D3D11CreateDevice
    create_device.argtypes = [
        ctypes.c_void_p,
        wintypes.UINT,
        wintypes.HMODULE,
        wintypes.UINT,
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    create_device.restype = ctypes.c_long

    native_device = ctypes.c_void_p()
    native_context = ctypes.c_void_p()
    feature_level = wintypes.UINT()
    result = create_device(
        None,
        _D3D_DRIVER_TYPE_HARDWARE,
        None,
        _D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        None,
        0,
        _D3D11_SDK_VERSION,
        ctypes.byref(native_device),
        ctypes.byref(feature_level),
        ctypes.byref(native_context),
    )
    if result < 0:
        raise ctypes.WinError(result)
    try:
        projected = create_direct3d11_device_from_dxgi_device(native_device.value)
    except Exception:
        _release_com_pointer(native_context)
        _release_com_pointer(native_device)
        raise
    return projected, native_device, native_context


def _copy_software_bitmap(bitmap: SoftwareBitmap) -> np.ndarray:
    if bitmap.bitmap_pixel_format != BitmapPixelFormat.BGRA8:
        raise BackgroundCaptureError(
            f"unexpected WGC bitmap pixel format: {bitmap.bitmap_pixel_format}"
        )
    with bitmap.lock_buffer(BitmapBufferAccessMode.READ) as buffer:
        if buffer.get_plane_count() != 1:
            raise BackgroundCaptureError("WGC bitmap must contain exactly one plane")
        plane = buffer.get_plane_description(0)
        reference = buffer.create_reference()
        view: memoryview | None = None
        try:
            view = memoryview(reference)
            required = (
                plane.start_index
                + max(0, plane.height - 1) * plane.stride
                + plane.width * 4
            )
            if (
                plane.width <= 0
                or plane.height <= 0
                or plane.stride < plane.width * 4
                or required > view.nbytes
            ):
                raise BackgroundCaptureError(
                    "WGC bitmap plane metadata exceeds its memory buffer"
                )
            pixels = np.ndarray(
                (plane.height, plane.width, 4),
                dtype=np.uint8,
                buffer=view,
                offset=plane.start_index,
                strides=(plane.stride, 4, 1),
            ).copy()
        finally:
            if view is not None:
                view.release()
            reference.close()
    return pixels


def _rect_from_tuple(value: tuple[int, int, int, int]) -> Rect:
    left, top, right, bottom = (int(part) for part in value)
    return Rect(left, top, right - left, bottom - top)


def _extended_frame_bounds(hwnd: int) -> Rect | None:
    rect = wintypes.RECT()
    result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd,
        _DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if result != 0:
        return None
    return Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _crop_client_frame(
    frame: np.ndarray,
    hwnd: int,
    expected_client: Rect,
) -> np.ndarray:
    frame_height, frame_width = frame.shape[:2]
    if (frame_width, frame_height) == (expected_client.width, expected_client.height):
        return np.ascontiguousarray(frame)

    candidates: list[Rect] = []
    extended = _extended_frame_bounds(hwnd)
    if extended is not None:
        candidates.append(extended)
    candidates.append(_rect_from_tuple(win32gui.GetWindowRect(hwnd)))

    for capture_bounds in candidates:
        if (capture_bounds.width, capture_bounds.height) != (frame_width, frame_height):
            continue
        x = expected_client.x - capture_bounds.x
        y = expected_client.y - capture_bounds.y
        if (
            x >= 0
            and y >= 0
            and x + expected_client.width <= frame_width
            and y + expected_client.height <= frame_height
        ):
            return frame[
                y : y + expected_client.height,
                x : x + expected_client.width,
            ].copy()
    raise BackgroundCaptureError(
        "WGC frame bounds cannot be mapped to the exact game client rectangle"
    )


class WindowsGraphicsCaptureService:
    """Production WGC backend, loaded lazily inside the automation worker."""

    def __init__(self, *, frame_timeout_seconds: float = 0.25, logger: TextRunLogger | None = None) -> None:
        if frame_timeout_seconds <= 0:
            raise ValueError("frame_timeout_seconds must be positive")
        self._frame_timeout_seconds = frame_timeout_seconds
        self._capture_lock = threading.Lock()
        self._frame_available = threading.Event()
        self._window: WindowRef | None = None
        self._expected_client: Rect | None = None
        self._item: object | None = None
        self._device: object | None = None
        self._native_device = ctypes.c_void_p()
        self._native_context = ctypes.c_void_p()
        self._frame_pool: Direct3D11CaptureFramePool | None = None
        self._session: object | None = None
        self._frame_token: object | None = None
        self._last_frame_time: object | None = None
        self._apartment_initialized = False
        self._closed = False
        self._logger = logger
        self._initial_item_size: tuple[int, int] | None = None
        self._pool_size: tuple[int, int] | None = None
        self._content_size: tuple[int, int] | None = None
        self._observed_item_size: tuple[int, int] | None = None
        self._startup_failure_diagnostics: dict[str, object] | None = None
        self._successful_frames = 0

    def _diagnostics(self, window: WindowRef, bounds: Rect) -> dict[str, object]:
        def query(operation: Callable[[], object]) -> object:
            try:
                return operation()
            except Exception as exc:
                return f"query_failed:{type(exc).__name__}:{exc}"

        def item_size() -> tuple[int, int] | None:
            if self._item is None:
                return None
            size = self._item.size
            return (size.width, size.height)

        def client_bounds() -> Rect:
            left, top, right, bottom = win32gui.GetClientRect(window.hwnd)
            x, y = win32gui.ClientToScreen(window.hwnd, (left, top))
            return Rect(x, y, right - left, bottom - top)

        def dpi_awareness() -> int:
            user32 = ctypes.windll.user32
            get_context = user32.GetThreadDpiAwarenessContext
            get_context.restype = ctypes.c_void_p
            get_awareness = user32.GetAwarenessFromDpiAwarenessContext
            get_awareness.argtypes = [ctypes.c_void_p]
            return int(get_awareness(get_context()))

        return {
            "hwnd": window.hwnd,
            "initial_item_size": self._initial_item_size,
            "item_size": self._observed_item_size or query(item_size),
            "current_item_size": query(item_size),
            "frame_content_size": self._content_size,
            "frame_pool_size": self._pool_size,
            "expected_client": bounds,
            "actual_client": query(client_bounds),
            "window_rect": query(lambda: win32gui.GetWindowRect(window.hwnd)),
            "dwm_bounds": query(lambda: _extended_frame_bounds(window.hwnd)),
            "minimized": query(lambda: bool(win32gui.IsIconic(window.hwnd))),
            "window_dpi": query(lambda: int(ctypes.windll.user32.GetDpiForWindow(window.hwnd))),
            "capture_thread_dpi_awareness": query(dpi_awareness),
            "successful_frames": self._successful_frames,
        }

    @staticmethod
    def support_diagnostic() -> tuple[bool, str]:
        try:
            supported = bool(GraphicsCaptureSession.is_supported())
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
        return supported, "supported" if supported else "Windows reported WGC unsupported"

    def _start(self, window: WindowRef, expected_client: Rect) -> None:
        if self._closed:
            raise BackgroundCaptureError("WGC capture service is closed")
        if win32gui.IsIconic(window.hwnd):
            raise BackgroundCaptureError("game window is minimized")
        try:
            init_apartment(ApartmentType.MULTI_THREADED)
            self._apartment_initialized = True
            supported, diagnostic = self.support_diagnostic()
            if not supported:
                raise BackgroundCaptureError(
                    f"Windows Graphics Capture is not supported: {diagnostic}"
                )
            self._device, self._native_device, self._native_context = (
                _create_direct3d_device()
            )
            self._item = create_for_window(window.hwnd)
            item_size = self._item.size
            self._initial_item_size = (item_size.width, item_size.height)
            if item_size.width <= 0 or item_size.height <= 0:
                raise BackgroundCaptureError("WGC returned an invalid capture-item size")
            self._frame_pool = Direct3D11CaptureFramePool.create_free_threaded(
                self._device,
                DirectXPixelFormat.B8_G8_R8_A8_UINT_NORMALIZED,
                2,
                item_size,
            )
            self._pool_size = self._initial_item_size
            self._session = self._frame_pool.create_capture_session(self._item)
            self._session.is_cursor_capture_enabled = False
            if self._session.is_cursor_capture_enabled:
                raise BackgroundCaptureError("WGC cursor capture could not be disabled")
            self._frame_token = self._frame_pool.add_frame_arrived(
                lambda _pool, _args: self._frame_available.set()
            )
            self._window = window
            self._expected_client = expected_client
            if self._logger is not None:
                self._logger.event("wgc_initialized", **self._diagnostics(window, expected_client))
            self._session.start_capture()
        except Exception as exc:
            self._startup_failure_diagnostics = self._diagnostics(window, expected_client)
            self.close()
            if isinstance(exc, BackgroundCaptureError):
                raise
            raise BackgroundCaptureError(f"unable to start WGC capture: {exc}") from exc

    def _latest_frame(self) -> Direct3D11CaptureFrame | None:
        assert self._frame_pool is not None
        latest: Direct3D11CaptureFrame | None = None
        while True:
            candidate = self._frame_pool.try_get_next_frame()
            if candidate is None:
                return latest
            if latest is not None:
                latest.close()
            latest = candidate

    def capture_client(self, window: WindowRef, bounds: Rect) -> Frame:
        try:
            return self._capture_client(window, bounds)
        except Exception as exc:
            diagnostics = self._startup_failure_diagnostics or self._diagnostics(window, bounds)
            detail = " ".join(f"{key}={value}" for key, value in diagnostics.items())
            if self._logger is not None:
                self._logger.event("wgc_capture_failed", error_type=type(exc).__name__,
                                   error=str(exc), **diagnostics)
            if isinstance(exc, BackgroundCaptureError):
                raise BackgroundCaptureError(f"{exc}; {detail}") from exc
            raise

    def _capture_client(self, window: WindowRef, bounds: Rect) -> Frame:
        with self._capture_lock:
            self._content_size = None
            self._observed_item_size = None
            try:
                _validate_window_identity(window)
            except BackgroundInputError as exc:
                raise BackgroundCaptureError(str(exc)) from exc
            if self._window is None:
                self._start(window, bounds)
            elif self._window != window or self._expected_client != bounds:
                raise BackgroundCaptureError(
                    "game window or client rectangle changed after WGC startup"
                )
            if self._closed:
                raise BackgroundCaptureError("WGC capture service is closed")

            deadline = time.monotonic() + self._frame_timeout_seconds
            while True:
                self._frame_available.clear()
                frame = self._latest_frame()
                if frame is not None:
                    try:
                        frame_time = frame.system_relative_time
                        if self._last_frame_time is None or frame_time > self._last_frame_time:
                            assert self._item is not None
                            item_size = self._item.size
                            self._observed_item_size = (item_size.width, item_size.height)
                            content_size = frame.content_size
                            self._content_size = (content_size.width, content_size.height)
                            if content_size.width <= 0 or content_size.height <= 0:
                                raise BackgroundCaptureError(
                                    f"invalid WGC content size: {self._content_size}"
                                )
                            with SoftwareBitmap.create_copy_from_surface_async(
                                frame.surface
                            ).get() as bitmap:
                                pixels = _copy_software_bitmap(bitmap)
                            surface_height, surface_width = pixels.shape[:2]
                            if (
                                content_size.width > surface_width
                                or content_size.height > surface_height
                            ):
                                raise BackgroundCaptureError(
                                    "WGC content size exceeds capture surface: "
                                    f"content={self._content_size} "
                                    f"surface={(surface_width, surface_height)}"
                                )
                            # The pool/item may include invisible window borders while
                            # ContentSize matches DWM's visible bounds. Discard undefined
                            # surface padding BEFORE choosing the client crop's origin.
                            pixels = pixels[:content_size.height, :content_size.width]
                            client = _crop_client_frame(pixels, window.hwnd, bounds)
                            self._last_frame_time = frame_time
                            if client.shape != (bounds.height, bounds.width, 4):
                                raise BackgroundCaptureError(
                                    f"unexpected WGC client shape: {client.shape}"
                                )
                            self._successful_frames += 1
                            return client
                    finally:
                        frame.close()

                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._frame_available.wait(remaining):
                    raise BackgroundCaptureError(
                        "timed out waiting for a fresh WGC frame; game may be minimized or stalled"
                    )

    def close(self) -> None:
        self._closed = True
        self._frame_available.set()
        if self._frame_pool is not None and self._frame_token is not None:
            try:
                self._frame_pool.remove_frame_arrived(self._frame_token)
            except Exception:
                pass
        self._frame_token = None
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
        self._session = None
        if self._frame_pool is not None:
            try:
                self._frame_pool.close()
            except Exception:
                pass
        self._frame_pool = None
        self._item = None
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
        self._device = None
        _release_com_pointer(self._native_context)
        _release_com_pointer(self._native_device)
        if self._apartment_initialized:
            uninit_apartment()
            self._apartment_initialized = False
