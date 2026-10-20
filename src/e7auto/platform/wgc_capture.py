from __future__ import annotations

import ctypes
import threading
import time
from collections import deque
from math import isfinite
from typing import Callable
from ctypes import wintypes

import numpy as np
import win32gui

from e7auto.platform.native_runtime import ensure_msvc_runtime

# WinRT's private MSVCP140 14.29 must not be loaded before Qt's newer runtime.
# This selects a native DLL only; it does not import Qt or initialize COM.
ensure_msvc_runtime()

from winrt.runtime import ApartmentType, init_apartment, uninit_apartment
from winrt.windows.graphics.capture import Direct3D11CaptureFrame, Direct3D11CaptureFramePool, GraphicsCaptureSession
from winrt.windows.graphics.capture.interop import create_for_window
from winrt.windows.graphics.directx import DirectXPixelFormat
from winrt.windows.graphics.directx.direct3d11.interop import create_direct3d11_device_from_dxgi_device
from winrt.windows.graphics.imaging import BitmapBufferAccessMode, BitmapPixelFormat, SoftwareBitmap

from e7auto.platform.input import BackgroundCaptureError, BackgroundInputError, _validate_window_identity
from e7auto.core.types import Rect
from e7auto.core.ports import CaptureTiming, Frame, TextRunLogger, WindowRef


_D3D_DRIVER_TYPE_HARDWARE = 1
_D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
_D3D11_SDK_VERSION = 7
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


class _FrameWaitTimeout(BackgroundCaptureError):
    """Only expiration of a fresh-frame deadline authorizes session recovery."""


def _make_qpc_clock() -> Callable[[], int]:
    """Use WGC's QPC clock domain, converted to TimeSpan's 100 ns units."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    frequency = ctypes.c_longlong()
    query_frequency = kernel.QueryPerformanceFrequency
    query_frequency.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
    query_frequency.restype = wintypes.BOOL
    query_counter = kernel.QueryPerformanceCounter
    query_counter.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
    query_counter.restype = wintypes.BOOL
    if not query_frequency(ctypes.byref(frequency)) or frequency.value <= 0:
        raise BackgroundCaptureError("unable to query QPC frequency")
    ticks_per_second = frequency.value

    def now() -> int:
        counter = ctypes.c_longlong()
        if not query_counter(ctypes.byref(counter)):
            raise BackgroundCaptureError("unable to query QPC time")
        return counter.value * 10_000_000 // ticks_per_second

    return now


def _timespan_ticks(value) -> int:
    return ((value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds) * 10


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

    def __init__(self, *, frame_timeout_seconds: float = 1.0,
                 startup_timeout_seconds: float = 2.0,
                 max_frame_age_seconds: float = 0.1, logger: TextRunLogger | None = None,
                 future_frame_tolerance_seconds: float = 0.05,
                 recovery_timeout_seconds: float = 3.0,
                 rebuild_cooldown_seconds: float = 60.0,
                 rebuild_window_seconds: float = 600.0,
                 rebuild_max_attempts: int = 3,
                 qpc_clock: Callable[[], int] | None = None) -> None:
        if not isfinite(frame_timeout_seconds) or frame_timeout_seconds <= 0:
            raise ValueError("frame_timeout_seconds must be positive")
        if not isfinite(startup_timeout_seconds) or startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        if not isfinite(max_frame_age_seconds) or max_frame_age_seconds < 0.000001:
            raise ValueError("max_frame_age_seconds must be finite and at least one microsecond")
        if not isfinite(future_frame_tolerance_seconds) or future_frame_tolerance_seconds < 0:
            raise ValueError("future_frame_tolerance_seconds must be finite and non-negative")
        for name, value in (("recovery_timeout_seconds", recovery_timeout_seconds),
                            ("rebuild_cooldown_seconds", rebuild_cooldown_seconds),
                            ("rebuild_window_seconds", rebuild_window_seconds)):
            if isinstance(value, bool) or not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if type(rebuild_max_attempts) is not int or rebuild_max_attempts <= 0:
            raise ValueError("rebuild_max_attempts must be a positive integer")
        self._frame_timeout_seconds = frame_timeout_seconds
        self._startup_timeout_seconds = startup_timeout_seconds
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._rebuild_cooldown_seconds = rebuild_cooldown_seconds
        self._rebuild_window_seconds = rebuild_window_seconds
        self._rebuild_max_attempts = rebuild_max_attempts
        self._rebuild_times: deque[float] = deque()
        self._last_rebuild_time: float | None = None
        self._recovery_action = "none"
        self._recovery_stage = "normal"
        self._rebuild_elapsed_ms = 0.
        self._pending_recovery_event: dict[str, object] | None = None
        self._wait_checkpoint: Callable[[], None] | None = None
        self._wait_interrupted = False
        self._max_frame_age_ticks = round(max_frame_age_seconds * 10_000_000)
        self._future_tolerance_ticks = round(future_frame_tolerance_seconds * 10_000_000)
        self._qpc_clock = qpc_clock or _make_qpc_clock()
        self._input_after_ticks: int | None = None
        self._input_timing_error: str | None = None
        self.last_capture_timing: CaptureTiming | None = None
        self._stats = dict(capture_calls=0, capture_failures=0, received_frames=0,
                          superseded_frames=0, expired_frames=0, pre_input_frames=0,
                          repeated_frames=0, invalid_time_frames=0, deadline_frames=0,
                          waited_calls=0, tolerated_future_frames=0, future_catchup_waits=0,
                          rebuild_attempts=0, rebuild_successes=0, rebuild_failures=0,
                          rebuild_interruptions=0, rebuild_rate_limit_hits=0,
                          extended_wait_successes=0)
        self._future_catchup_wait_ms = self._accepted_future_lead_max_ms = 0.
        self._capture_total_ms = self._capture_max_ms = 0.
        self._wait_total_ms = self._wait_max_ms = 0.
        self._age_total_ms = self._age_max_ms = 0.
        self._age_buckets = [0] * 5  # <=10, <=25, <=50, <=100, >100 ms
        self._wait_buckets = [0] * 7  # <=1, <=10, <=25, <=50, <=100, <=250, >250 ms
        self._attempt_wait_ms = 0.
        self._attempt_started = 0.
        self._attempt_copy_ms = 0.
        self._attempt_expired_before_copy = self._attempt_expired_after_copy = 0
        self._attempt_waited = False
        self._observed_frame_ticks: int | None = None
        self._observed_frame_age_ms: float | None = None
        self._summary_emitted = False
        self._capture_in_progress = False
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
        self._last_frame_ticks: int | None = None
        self._apartment_initialized = False
        self._closed = False
        self._logger = logger
        self._initial_item_size: tuple[int, int] | None = None
        self._pool_size: tuple[int, int] | None = None
        self._content_size: tuple[int, int] | None = None
        self._observed_item_size: tuple[int, int] | None = None
        self._startup_failure_diagnostics: dict[str, object] | None = None
        self._successful_frames = 0

    def set_wait_checkpoint(self, checkpoint: Callable[[], None] | None) -> None:
        """Runtime supplies stop/window guards without a platform-to-runtime import."""
        self._wait_checkpoint = checkpoint

    def _event(self, event: str, **fields: object) -> None:
        try:
            if self._logger is not None:
                self._logger.event(event, **fields)
        except Exception:
            pass  # Telemetry cannot interrupt capture or native resource cleanup.

    def _flush_recovery_event(self) -> None:
        fields, self._pending_recovery_event = self._pending_recovery_event, None
        if fields is not None:
            self._event("wgc_recovery_completed", **fields)

    def _check_wait(self) -> None:
        if self._wait_checkpoint is not None:
            try:
                self._wait_checkpoint()
            except Exception:
                self._wait_interrupted = True
                raise

    def mark_input_complete(self) -> None:
        # Called after message dispatch on the same production worker as capture.
        try:
            self._input_after_ticks = self._qpc_clock()
            self._input_timing_error = None
        except Exception as exc:
            # Never interrupt a button-down/up sequence because telemetry failed.
            # The next capture must fail rather than use an unknown input boundary.
            self._input_timing_error = str(exc)

    def _reject_reason(self, frame_ticks: int, now: int) -> str | None:
        self._observed_frame_ticks = frame_ticks
        self._observed_frame_age_ms = (now - frame_ticks) / 10_000
        if frame_ticks < 0 or frame_ticks - now > self._future_tolerance_ticks:
            return "invalid_time_frames"
        if self._last_frame_ticks is not None and frame_ticks <= self._last_frame_ticks:
            return "repeated_frames"
        if now - frame_ticks > self._max_frame_age_ticks:
            return "expired_frames"
        # A timestamp leading actual capture by the allowed tolerance must not
        # make a pre-input image look as though it followed that input.
        if (self._input_after_ticks is not None
                and frame_ticks <= self._input_after_ticks + self._future_tolerance_ticks):
            return "pre_input_frames"
        return None

    def _wait_for_frame_time(self, frame_ticks: int, deadline: float) -> int | None:
        now = self._qpc_clock()
        if now >= frame_ticks:
            return now
        self._stats["future_catchup_waits"] += 1
        self._attempt_waited = True
        waiting = time.monotonic()
        try:
            while now < frame_ticks:
                self._check_wait()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                time.sleep(min(remaining, 0.05, (frame_ticks - now) / 10_000_000))
                self._check_wait()
                now = self._qpc_clock()
            return now
        finally:
            elapsed_ms = (time.monotonic() - waiting) * 1000
            self._attempt_wait_ms += elapsed_ms
            self._future_catchup_wait_ms += elapsed_ms

    @staticmethod
    def _count_bucket(buckets: list[int], bounds: tuple[int, ...], value: float) -> None:
        buckets[next((i for i, bound in enumerate(bounds) if value <= bound), len(bounds))] += 1

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
            "max_frame_age_ms": self._max_frame_age_ticks / 10_000,
            "future_frame_tolerance_ms": self._future_tolerance_ticks / 10_000,
            "frame_timeout_ms": self._frame_timeout_seconds * 1000,
            "startup_timeout_ms": self._startup_timeout_seconds * 1000,
            "capture_phase": "startup" if not self._successful_frames else "running",
            "last_source_frame_ticks": self._last_frame_ticks,
            "input_after_ticks": self._input_after_ticks,
            "input_source_lower_bound_ticks": (None if self._input_after_ticks is None
                                               else self._input_after_ticks + self._future_tolerance_ticks),
            "input_timing_error": self._input_timing_error,
            "observed_frame_ticks": self._observed_frame_ticks,
            "observed_frame_age_ms": self._observed_frame_age_ms,
            "capture_wait_ms": round(self._attempt_wait_ms, 3),
            "capture_elapsed_ms": round((time.monotonic() - self._attempt_started) * 1000, 3),
            "capture_copy_ms": round(self._attempt_copy_ms, 3),
            "expired_before_copy": self._attempt_expired_before_copy,
            "expired_after_copy": self._attempt_expired_after_copy,
            "recovery_action": self._recovery_action,
            "recovery_stage": self._recovery_stage,
            "recovery_timeout_ms": self._recovery_timeout_seconds * 1000,
            "rebuild_elapsed_ms": round(self._rebuild_elapsed_ms, 3),
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
            self._create_capture_session(window, expected_client)
        except Exception as exc:
            self._startup_failure_diagnostics = self._diagnostics(window, expected_client)
            self.close()
            if self._wait_interrupted:
                raise
            if isinstance(exc, BackgroundCaptureError):
                raise
            raise BackgroundCaptureError(f"unable to start WGC capture: {exc}") from exc

    def _create_capture_session(self, window: WindowRef, expected_client: Rect) -> None:
        self._check_wait()
        self._item = create_for_window(window.hwnd)
        self._check_wait()
        item_size = self._item.size
        if self._initial_item_size is None:
            self._initial_item_size = (item_size.width, item_size.height)
        if item_size.width <= 0 or item_size.height <= 0:
            raise BackgroundCaptureError("WGC returned an invalid capture-item size")
        self._frame_pool = Direct3D11CaptureFramePool.create_free_threaded(
            self._device,
            DirectXPixelFormat.B8_G8_R8_A8_UINT_NORMALIZED,
            2,
            item_size,
        )
        self._pool_size = (item_size.width, item_size.height)
        self._check_wait()
        self._session = self._frame_pool.create_capture_session(self._item)
        self._check_wait()
        self._session.is_cursor_capture_enabled = False
        if self._session.is_cursor_capture_enabled:
            raise BackgroundCaptureError("WGC cursor capture could not be disabled")
        available = self._frame_available
        self._frame_token = self._frame_pool.add_frame_arrived(
            lambda _pool, _args: available.set()
        )
        self._window = window
        self._expected_client = expected_client
        if self._successful_frames == 0 and self._logger is not None:
            self._event("wgc_initialized", **self._diagnostics(window, expected_client))
        self._check_wait()
        self._session.start_capture()
        self._check_wait()

    def _release_capture_session(self, *, strict: bool = False) -> None:
        errors: list[str] = []
        if self._frame_pool is not None and self._frame_token is not None:
            try:
                self._frame_pool.remove_frame_arrived(self._frame_token)
                self._frame_token = None
            except Exception as exc:
                errors.append(f"remove_frame_arrived:{exc!r}")
        for attribute in ("_session", "_frame_pool"):
            resource = getattr(self, attribute)
            if resource is not None:
                try:
                    resource.close()
                    setattr(self, attribute, None)
                except Exception as exc:
                    errors.append(f"{attribute}.close:{exc!r}")
        if self._frame_pool is None:
            self._frame_token = None
        self._item = None
        if errors and strict:
            raise BackgroundCaptureError("unable to release WGC capture session: " + "; ".join(errors))

    def _check_recovery_target(self, window: WindowRef, bounds: Rect) -> None:
        self._check_wait()
        try:
            _validate_window_identity(window)
        except BackgroundInputError as exc:
            raise BackgroundCaptureError(str(exc)) from exc
        if self._closed or self._window != window or self._expected_client != bounds:
            raise BackgroundCaptureError("game window or capture service changed during WGC recovery")
        if win32gui.IsIconic(window.hwnd):
            raise BackgroundCaptureError("game window is minimized")
        if self._input_timing_error is not None:
            raise BackgroundCaptureError(f"input completion timestamp unavailable: {self._input_timing_error}")

    def _rebuild_limit_reason(self, now: float) -> str | None:
        while self._rebuild_times and now - self._rebuild_times[0] >= self._rebuild_window_seconds:
            self._rebuild_times.popleft()
        if self._last_rebuild_time is not None and now - self._last_rebuild_time < self._rebuild_cooldown_seconds:
            return "cooldown"
        if len(self._rebuild_times) >= self._rebuild_max_attempts:
            return "window_limit"
        return None

    def _recover_capture(self, window: WindowRef, bounds: Rect) -> Frame:
        self._check_recovery_target(window, bounds)
        now = time.monotonic()
        initial_elapsed_ms = (now - self._attempt_started) * 1000
        limit_reason = self._rebuild_limit_reason(now)
        rebuild = limit_reason is None
        self._recovery_action = "rebuild" if rebuild else "extended_wait"
        if rebuild:
            self._last_rebuild_time = now
            self._rebuild_times.append(now)
            self._stats["rebuild_attempts"] += 1
        else:
            self._stats["rebuild_rate_limit_hits"] += 1
        fields = dict(action=self._recovery_action, rate_limit_reason=limit_reason or "none",
                      initial_elapsed_ms=round(initial_elapsed_ms, 3),
                      attempts_in_window=len(self._rebuild_times),
                      recovery_timeout_ms=self._recovery_timeout_seconds * 1000)
        self._event("wgc_recovery_started" if rebuild else "wgc_recovery_skipped", **fields)
        outcome = "failed"
        recovery_started: float | None = None
        try:
            if rebuild:
                self._recovery_stage = "rebuild"
                rebuild_started = time.monotonic()
                try:
                    self._release_capture_session(strict=True)
                    self._check_recovery_target(window, bounds)
                    self._frame_available = threading.Event()
                    self._create_capture_session(window, bounds)
                finally:
                    self._rebuild_elapsed_ms = (time.monotonic() - rebuild_started) * 1000
            self._recovery_stage = "recovery_wait"
            recovery_started = time.monotonic()
            self._check_recovery_target(window, bounds)
            frame = self._wait_for_fresh_frame(recovery_started + self._recovery_timeout_seconds)
            self._stats["rebuild_successes" if rebuild else "extended_wait_successes"] += 1
            outcome = "recovered"
            return frame
        except Exception as exc:
            outcome = "interrupted" if self._wait_interrupted else "failed"
            if rebuild:
                self._stats["rebuild_interruptions" if self._wait_interrupted else "rebuild_failures"] += 1
            if not self._wait_interrupted and not isinstance(exc, BackgroundCaptureError):
                raise BackgroundCaptureError(f"unable to recover WGC capture: {exc}") from exc
            raise
        finally:
            completed = dict(fields, outcome=outcome,
                             rebuild_elapsed_ms=round(self._rebuild_elapsed_ms, 3),
                             recovery_elapsed_ms=(0. if recovery_started is None else
                                                  round((time.monotonic() - recovery_started) * 1000, 3)),
                             total_elapsed_ms=round((time.monotonic() - self._attempt_started) * 1000, 3))
            if outcome == "recovered":
                # Writing after final freshness validation could age the returned image.
                # Flush before the next capture, or at final close; retain measured times.
                self._pending_recovery_event = completed
            else:
                self._event("wgc_recovery_completed", **completed)

    def _latest_frame(self, deadline: float) -> Direct3D11CaptureFrame | None:
        assert self._frame_pool is not None
        latest: Direct3D11CaptureFrame | None = None
        try:
            while time.monotonic() < deadline:
                self._check_wait()
                candidate = self._frame_pool.try_get_next_frame()
                if candidate is None:
                    return latest
                self._stats["received_frames"] += 1
                previous, latest = latest, candidate
                if previous is not None:
                    self._stats["superseded_frames"] += 1
                    previous.close()
        except BaseException:
            if latest is not None:
                latest.close()
            raise
        if latest is not None:
            self._stats["deadline_frames"] += 1
            latest.close()
        return None

    def capture_client(self, window: WindowRef, bounds: Rect) -> Frame:
        self._flush_recovery_event()
        started = time.monotonic()
        self._attempt_started = started
        self._attempt_copy_ms = 0.
        self._attempt_expired_before_copy = self._attempt_expired_after_copy = 0
        self._stats["capture_calls"] += 1
        self.last_capture_timing = None
        self._observed_frame_ticks = self._observed_frame_age_ms = None
        self._attempt_wait_ms = 0.
        self._attempt_waited = False
        self._wait_interrupted = False
        self._recovery_action, self._recovery_stage = "none", "normal"
        self._rebuild_elapsed_ms = 0.
        self._capture_in_progress = True
        try:
            return self._capture_client(window, bounds)
        except Exception as exc:
            if self._wait_interrupted:
                raise  # Preserve the runtime's stop/window reason without capture-failure noise.
            self._stats["capture_failures"] += 1
            diagnostics = self._startup_failure_diagnostics or self._diagnostics(window, bounds)
            detail = " ".join(f"{key}={value}" for key, value in diagnostics.items())
            self._event("wgc_capture_failed", error_type=type(exc).__name__,
                        error=str(exc), **diagnostics)
            if isinstance(exc, BackgroundCaptureError):
                raise BackgroundCaptureError(f"{exc}; {detail}") from exc
            raise
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            self._capture_total_ms += elapsed_ms
            self._capture_max_ms = max(self._capture_max_ms, elapsed_ms)
            self._wait_total_ms += self._attempt_wait_ms
            self._wait_max_ms = max(self._wait_max_ms, self._attempt_wait_ms)
            self._stats["waited_calls"] += int(self._attempt_waited)
            self._count_bucket(self._wait_buckets, (1, 10, 25, 50, 100, 250), self._attempt_wait_ms)
            self._capture_in_progress = False
            if self._closed:
                self._log_capture_summary()

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
            if self._input_timing_error is not None:
                raise BackgroundCaptureError(f"input completion timestamp unavailable: {self._input_timing_error}")

            startup = self._successful_frames == 0
            timeout = self._startup_timeout_seconds if startup else self._frame_timeout_seconds
            try:
                return self._wait_for_fresh_frame(time.monotonic() + timeout, startup=startup)
            except _FrameWaitTimeout:
                if startup:
                    raise
                return self._recover_capture(window, bounds)

    def _wait_for_fresh_frame(self, deadline: float, *, startup: bool = False) -> Frame:
        window, bounds = self._window, self._expected_client
        assert window is not None and bounds is not None
        while time.monotonic() < deadline:
            self._check_wait()
            self._frame_available.clear()
            frame = self._latest_frame(deadline)
            if frame is not None:
                try:
                    frame_ticks = _timespan_ticks(frame.system_relative_time)
                    observed_ticks = self._qpc_clock()
                    initial_lead_ms = max(0., (frame_ticks - observed_ticks) / 10_000)
                    rejection = self._reject_reason(frame_ticks, observed_ticks)
                    if rejection is not None:
                        self._stats[rejection] += 1
                        self._attempt_expired_before_copy += int(rejection == "expired_frames")
                    else:
                        assert self._item is not None
                        item_size = self._item.size
                        self._observed_item_size = (item_size.width, item_size.height)
                        content_size = frame.content_size
                        self._content_size = (content_size.width, content_size.height)
                        if content_size.width <= 0 or content_size.height <= 0:
                            raise BackgroundCaptureError(
                                f"invalid WGC content size: {self._content_size}"
                            )
                        copy_started = time.monotonic()
                        try:
                            with SoftwareBitmap.create_copy_from_surface_async(
                                frame.surface
                            ).get() as bitmap:
                                pixels = _copy_software_bitmap(bitmap)
                        finally:
                            self._attempt_copy_ms += (time.monotonic() - copy_started) * 1000
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
                        if client.shape != (bounds.height, bounds.width, 4):
                            raise BackgroundCaptureError(
                                f"unexpected WGC client shape: {client.shape}"
                            )
                        # Copying may itself be slow: validate again before delivery.
                        self._check_wait()
                        received_ticks = self._qpc_clock()
                        rejection = self._reject_reason(frame_ticks, received_ticks)
                        if rejection is None and received_ticks < frame_ticks:
                            received_ticks = self._wait_for_frame_time(frame_ticks, deadline)
                            rejection = ("deadline_frames" if received_ticks is None else
                                         self._reject_reason(frame_ticks, received_ticks))
                        if rejection is not None:
                            self._stats[rejection] += 1
                            self._attempt_expired_after_copy += int(rejection == "expired_frames")
                        elif time.monotonic() >= deadline:
                            self._stats["deadline_frames"] += 1
                        else:
                            self._last_frame_ticks = frame_ticks
                            if initial_lead_ms > 0:
                                self._stats["tolerated_future_frames"] += 1
                                self._accepted_future_lead_max_ms = max(
                                    self._accepted_future_lead_max_ms, initial_lead_ms)
                            self.last_capture_timing = CaptureTiming(
                                frame_ticks, received_ticks, self._attempt_wait_ms)
                            age_ms = self.last_capture_timing.source_age_ms
                            self._age_total_ms += age_ms
                            self._age_max_ms = max(self._age_max_ms, age_ms)
                            self._count_bucket(self._age_buckets, (10, 25, 50, 100), age_ms)
                            self._successful_frames += 1
                            return client
                finally:
                    frame.close()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._attempt_waited = True
            waiting = time.monotonic()
            try:
                # Guard both startup and running waits without resetting the deadline.
                interval = min(remaining, 0.05) if self._wait_checkpoint is not None else remaining
                arrived = self._frame_available.wait(interval)
            finally:
                self._attempt_wait_ms += (time.monotonic() - waiting) * 1000
            self._check_wait()
            if not arrived and interval >= remaining:
                break
        raise _FrameWaitTimeout(
            "timed out waiting for a fresh WGC frame during startup" if startup else
            "timed out waiting for a fresh WGC frame; game may be minimized or stalled"
        )

    def _log_capture_summary(self) -> None:
        if self._summary_emitted or self._logger is None:
            return
        self._summary_emitted = True
        try:
            self._logger.event(
                "wgc_capture_summary", **self._stats, accepted_frames=self._successful_frames,
                max_frame_age_ms=self._max_frame_age_ticks / 10_000,
                future_frame_tolerance_ms=self._future_tolerance_ticks / 10_000,
                accepted_future_lead_max_ms=round(self._accepted_future_lead_max_ms, 4),
                future_catchup_wait_ms=round(self._future_catchup_wait_ms, 3),
                frame_timeout_ms=self._frame_timeout_seconds * 1000,
                startup_timeout_ms=self._startup_timeout_seconds * 1000,
                recovery_timeout_ms=self._recovery_timeout_seconds * 1000,
                rebuild_cooldown_ms=self._rebuild_cooldown_seconds * 1000,
                rebuild_window_ms=self._rebuild_window_seconds * 1000,
                rebuild_max_attempts=self._rebuild_max_attempts,
                capture_total_ms=round(self._capture_total_ms, 3),
                capture_max_ms=round(self._capture_max_ms, 3),
                wait_total_ms=round(self._wait_total_ms, 3), wait_max_ms=round(self._wait_max_ms, 3),
                source_age_mean_ms=(round(self._age_total_ms / self._successful_frames, 3)
                                    if self._successful_frames else "unknown"),
                source_age_max_ms=round(self._age_max_ms, 3) if self._successful_frames else "unknown",
                age_buckets_le_10_25_50_100_over=",".join(map(str, self._age_buckets)),
                wait_buckets_le_1_10_25_50_100_250_over=",".join(map(str, self._wait_buckets)),
            )
        except Exception:
            pass  # Optional telemetry must not prevent resource release or hide the stop reason.

    def close(self) -> None:
        self._flush_recovery_event()
        if not self._capture_in_progress:
            self._log_capture_summary()
        self._closed = True
        self._frame_available.set()
        self._release_capture_session()
        self._session = self._frame_pool = self._frame_token = None
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
