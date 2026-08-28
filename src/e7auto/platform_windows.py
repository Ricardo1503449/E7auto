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
from .ports import Frame, WindowRef, WindowState


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

    def resize_client(self, window: WindowRef, size: Size) -> None:
        style = win32gui.GetWindowLong(window.hwnd, win32con.GWL_STYLE)
        ex_style = win32gui.GetWindowLong(window.hwnd, win32con.GWL_EXSTYLE)
        dpi = ctypes.windll.user32.GetDpiForWindow(window.hwnd)
        rect = wintypes.RECT(0, 0, size.width, size.height)
        ok = ctypes.windll.user32.AdjustWindowRectExForDpi(
            ctypes.byref(rect), style, False, ex_style, dpi
        )
        if not ok:
            raise WindowOperationError("AdjustWindowRectExForDpi failed")
        left, top, _, _ = win32gui.GetWindowRect(window.hwnd)
        outer_width = rect.right - rect.left
        outer_height = rect.bottom - rect.top
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
        return WindowState(
            True,
            bool(win32gui.IsIconic(window.hwnd)),
            win32gui.GetForegroundWindow() == window.hwnd,
            bounds,
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
