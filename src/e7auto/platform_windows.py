from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

import mss
import mss.windows.gdi as mss_gdi
import numpy as np
import pywintypes
import win32api
import win32con
import win32gui
import win32process

from .config import Point, Rect, Size
from .ports import DisplayGeometry, Frame, WindowRef, WindowState


WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11
_CLICK_HOVER_SECONDS = 0.10
_CLICK_HOLD_SECONDS = 0.05
_MSS_GDI_RASTER_OPERATION_LOCK = threading.Lock()


class WindowLookupError(RuntimeError):
    pass


class WindowOperationError(RuntimeError):
    pass


def enable_per_monitor_dpi_awareness() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def _process_path(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(capacity)):
            return ""
        return str(Path(buffer.value))
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


class Win32WindowService:
    def locate_unique(self, executable_path: str, window_title: str) -> WindowRef:
        matches: list[WindowRef] = []
        expected_executable = Path(executable_path)
        expected_path = os.path.normcase(os.path.normpath(executable_path))
        expected_name = expected_executable.name.casefold()

        def callback(hwnd: int, _: object) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if title != window_title:
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            found_path = _process_path(pid)
            if not found_path:
                return True
            path_matches = (
                os.path.normcase(os.path.normpath(found_path)) == expected_path
                if expected_executable.is_absolute()
                else Path(found_path).name.casefold() == expected_name
            )
            if path_matches:
                matches.append(WindowRef(hwnd, title, Path(found_path).name, found_path))
            return True

        win32gui.EnumWindows(callback, None)
        if len(matches) != 1:
            raise WindowLookupError(f"Expected exactly one game window, found {len(matches)}")
        return matches[0]

    def restore_and_foreground(self, window: WindowRef) -> None:
        if not win32gui.IsWindow(window.hwnd):
            raise WindowOperationError("Game window no longer exists")
        win32gui.ShowWindow(window.hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(window.hwnd)
        if win32gui.GetForegroundWindow() != window.hwnd:
            raise WindowOperationError("Game window could not be focused")

    @staticmethod
    def _adjusted_outer_rect(hwnd: int, size: Size) -> wintypes.RECT:
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        rect = wintypes.RECT(0, 0, size.width, size.height)
        ok = ctypes.windll.user32.AdjustWindowRectExForDpi(
            ctypes.byref(rect), style, False, ex_style, dpi
        )
        if not ok:
            raise WindowOperationError("AdjustWindowRectExForDpi failed")
        return rect

    def inspect_display(self, window: WindowRef, *, validate_mode: bool) -> DisplayGeometry:
        monitor = int(
            win32api.MonitorFromWindow(
                window.hwnd,
                win32con.MONITOR_DEFAULTTONEAREST,
            )
        )
        if not monitor:
            raise WindowOperationError("MonitorFromWindow failed")
        info = win32api.GetMonitorInfo(monitor)
        monitor_rect = info.get("Monitor")
        device = info.get("Device")
        if (
            not isinstance(monitor_rect, tuple)
            or len(monitor_rect) != 4
            or not isinstance(device, str)
            or not device
        ):
            raise WindowOperationError("GetMonitorInfo returned invalid display geometry")
        left, top, right, bottom = (int(value) for value in monitor_rect)
        bounds = Rect(left, top, right - left, bottom - top)
        if bounds.width <= 0 or bounds.height <= 0:
            raise WindowOperationError("monitor dimensions must be positive")
        dpi = int(ctypes.windll.user32.GetDpiForWindow(window.hwnd))
        if dpi <= 0:
            raise WindowOperationError("GetDpiForWindow returned invalid DPI")
        current_mode: Size | None = None
        if validate_mode:
            settings = win32api.EnumDisplaySettings(device, win32con.ENUM_CURRENT_SETTINGS)
            width = int(getattr(settings, "PelsWidth", 0))
            height = int(getattr(settings, "PelsHeight", 0))
            current_mode = Size(width, height)
            if width <= 0 or height <= 0 or current_mode != Size(bounds.width, bounds.height):
                raise WindowOperationError(
                    "current desktop mode disagrees with the full monitor rectangle"
                )
        return DisplayGeometry(monitor, device, bounds, current_mode, dpi)

    def fit_client_size(
        self,
        window: WindowRef,
        desired: Size,
        baseline: Size,
        monitor_bounds: Rect,
    ) -> Size:
        def client_for_width(width: int) -> Size:
            height = max(1, int(width * baseline.height / baseline.width + 0.5))
            return Size(width, height)

        if desired.width <= 0 or desired.height <= 0:
            raise WindowOperationError("desired client dimensions must be positive")
        outer = self._adjusted_outer_rect(window.hwnd, desired)
        if outer.bottom - outer.top <= monitor_bounds.height:
            return desired
        low, high = 1, desired.width
        best: Size | None = None
        while low <= high:
            middle = (low + high) // 2
            candidate = client_for_width(middle)
            adjusted = self._adjusted_outer_rect(window.hwnd, candidate)
            if adjusted.bottom - adjusted.top <= monitor_bounds.height:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        if best is None:
            raise WindowOperationError("no positive fixed-aspect client fits monitor height")
        return best

    def resize_client(self, window: WindowRef, size: Size, monitor_bounds: Rect) -> None:
        rect = self._adjusted_outer_rect(window.hwnd, size)
        current_left, current_top, _, _ = win32gui.GetWindowRect(window.hwnd)
        outer_width = rect.right - rect.left
        outer_height = rect.bottom - rect.top
        if outer_width > monitor_bounds.width or outer_height > monitor_bounds.height:
            raise WindowOperationError("complete outer window does not fit monitor")
        left = min(max(current_left, monitor_bounds.x), monitor_bounds.right - outer_width)
        top = min(max(current_top, monitor_bounds.y), monitor_bounds.bottom - outer_height)
        win32gui.SetWindowPos(
            window.hwnd,
            None,
            left,
            top,
            outer_width,
            outer_height,
            win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
        )

    def inspect(self, window: WindowRef) -> WindowState:
        exists = bool(win32gui.IsWindow(window.hwnd))
        if not exists:
            return WindowState(False, False, False, Rect(0, 0, 0, 0))
        client = win32gui.GetClientRect(window.hwnd)
        origin = win32gui.ClientToScreen(window.hwnd, (client[0], client[1]))
        bounds = Rect(origin[0], origin[1], client[2] - client[0], client[3] - client[1])
        outer = win32gui.GetWindowRect(window.hwnd)
        return WindowState(
            True,
            bool(win32gui.IsIconic(window.hwnd)),
            win32gui.GetForegroundWindow() == window.hwnd,
            bounds,
            Rect(outer[0], outer[1], outer[2] - outer[0], outer[3] - outer[1]),
        )


class MssCaptureService:
    def capture_client(self, window: WindowRef, bounds: Rect) -> Frame:
        del window
        # MSS 10.2.0's Windows GDI backend combines SRCCOPY with CAPTUREBLT.
        # Controlled live A/B evidence showed that CAPTUREBLT causes recurring
        # cursor-display flicker, while pure SRCCOPY captures valid game frames
        # without that repeated flicker.  Keep the existing per-frame context
        # lifecycle and override only this pinned backend's raster flag.
        with _MSS_GDI_RASTER_OPERATION_LOCK:
            original_captureblt = mss_gdi.CAPTUREBLT
            mss_gdi.CAPTUREBLT = 0
            try:
                with mss.mss() as grabber:
                    shot = grabber.grab(
                        {
                            "left": bounds.x,
                            "top": bounds.y,
                            "width": bounds.width,
                            "height": bounds.height,
                        }
                    )
                    return np.asarray(shot, dtype=np.uint8).copy()
            finally:
                mss_gdi.CAPTUREBLT = original_captureblt


class Win32InputService:
    def __init__(self) -> None:
        self._last_cursor: Point | None = None

    def _set_cursor_pos_if_needed(self, point: Point) -> None:
        """Move the system cursor only when it is not already at *point*.

        The automation safety gate positions and reads back the cursor before
        dispatching an input.  ``click``/``scroll`` used to call
        ``SetCursorPos`` again unconditionally, so every logical input caused
        two identical cursor-position notifications.  The user observed that
        suppressing those duplicates reduced, but did not eliminate, flicker.
        """

        if self._last_cursor != point:
            win32api.SetCursorPos((point.x, point.y))
            self._last_cursor = point

    def move(self, point: Point) -> None:
        self._set_cursor_pos_if_needed(point)

    def position(self) -> Point:
        x, y = win32api.GetCursorPos()
        point = Point(int(x), int(y))
        self._last_cursor = point
        return point

    def click(self, point: Point) -> None:
        self._set_cursor_pos_if_needed(point)
        time.sleep(_CLICK_HOVER_SECONDS)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        try:
            time.sleep(_CLICK_HOLD_SECONDS)
        finally:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def scroll(self, point: Point, delta: int) -> None:
        self._set_cursor_pos_if_needed(point)
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, delta, 0)


class Win32RuntimeEnvironment:
    def is_elevated(self) -> bool:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())


class Win32F5HotkeyService:
    _F5_HOTKEY_ID = 0x67A0
    _F6_HOTKEY_ID = 0x67A1
    _HOTKEY_ID = _F5_HOTKEY_ID

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._stop = threading.Event()

    def register_f5(
        self,
        callback: Callable[[], None],
        move_callback: Callable[[], None] | None = None,
    ) -> bool:
        if self._thread is not None:
            return False
        ready = threading.Event()
        result: list[bool] = []
        self._stop.clear()

        def loop() -> None:
            self._thread_id = win32api.GetCurrentThreadId()
            registered_ids: list[int] = []
            try:
                win32gui.RegisterHotKey(None, self._F5_HOTKEY_ID, 0, win32con.VK_F5)
                registered_ids.append(self._F5_HOTKEY_ID)
                if move_callback is not None:
                    win32gui.RegisterHotKey(None, self._F6_HOTKEY_ID, 0, win32con.VK_F6)
                    registered_ids.append(self._F6_HOTKEY_ID)
            except pywintypes.error:
                for hotkey_id in registered_ids:
                    win32gui.UnregisterHotKey(None, hotkey_id)
                registered = False
            else:
                registered = True
            result.append(registered)
            ready.set()
            if not registered:
                return
            try:
                while not self._stop.is_set():
                    message = win32gui.GetMessage(None, 0, 0)
                    if not message or message[0] == 0:
                        break
                    _, msg = message
                    if msg[1] == win32con.WM_HOTKEY:
                        if msg[2] == self._F5_HOTKEY_ID:
                            callback()
                        elif msg[2] == self._F6_HOTKEY_ID and move_callback is not None:
                            move_callback()
            finally:
                for hotkey_id in registered_ids:
                    win32gui.UnregisterHotKey(None, hotkey_id)

        self._thread = threading.Thread(target=loop, name="e7auto-f5", daemon=True)
        self._thread.start()
        ready.wait(timeout=2.0)
        if not result or not result[0]:
            self._thread.join(timeout=1.0)
            self._thread = None
            self._thread_id = None
            return False
        return True

    def unregister_f5(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        if self._thread_id is not None:
            win32api.PostThreadMessage(self._thread_id, win32con.WM_QUIT, 0, 0)
        thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = None


def set_window_display_affinity(hwnd: int, affinity: int) -> bool:
    return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, affinity))


def get_window_display_affinity(hwnd: int) -> int | None:
    affinity = wintypes.DWORD()
    succeeded = bool(
        ctypes.windll.user32.GetWindowDisplayAffinity(
            hwnd,
            ctypes.byref(affinity),
        )
    )
    return int(affinity.value) if succeeded else None


def dwm_composition_enabled() -> bool:
    enabled = wintypes.BOOL()
    succeeded = ctypes.windll.dwmapi.DwmIsCompositionEnabled(ctypes.byref(enabled))
    return succeeded == 0 and bool(enabled.value)


def exclude_window_from_capture(hwnd: int) -> bool:
    return set_window_display_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
