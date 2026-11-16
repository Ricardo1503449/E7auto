from __future__ import annotations

import ctypes
import os
import time
from contextlib import contextmanager
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path

import win32api
import win32con
import win32gui
import win32process

from e7auto.core.types import Rect, Size
from e7auto.core.ports import (
    DisplayGeometry, GameClient, WindowGeometry, WindowGeometryChanged,
    WindowLookupError, WindowRef, WindowState,
)


class WindowOperationError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _dpi_api():
    api = ctypes.WinDLL("user32", use_last_error=True)
    signatures = {
        "SetThreadDpiAwarenessContext": ([wintypes.HANDLE], wintypes.HANDLE),
        "GetThreadDpiAwarenessContext": ([], wintypes.HANDLE),
        "GetWindowDpiAwarenessContext": ([wintypes.HWND], wintypes.HANDLE),
        "GetAwarenessFromDpiAwarenessContext": ([wintypes.HANDLE], ctypes.c_int),
        "GetDpiForWindow": ([wintypes.HWND], wintypes.UINT),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


@contextmanager
def _physical_dpi_context():
    api = _dpi_api()
    previous = api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if not previous:
        raise WindowOperationError("cannot enter per-monitor physical-pixel DPI context")
    failed = False
    try:
        awareness = api.GetAwarenessFromDpiAwarenessContext(api.GetThreadDpiAwarenessContext())
        if awareness != 2:
            raise WindowOperationError(f"unexpected measurement thread DPI awareness: {awareness}")
        yield api
    except BaseException:
        failed = True
        raise
    finally:
        try:
            if not api.SetThreadDpiAwarenessContext(previous):
                raise WindowOperationError("cannot restore thread DPI context")
        except Exception:
            if not failed:
                raise


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
    _RESTORE_TIMEOUT_SECONDS = 1.0
    _RESTORE_POLL_SECONDS = 0.02

    def locate_unique(self, clients: tuple[GameClient, ...], window_title: str) -> WindowRef:
        matches: dict[int, WindowRef] = {}
        targets = tuple(
            (client, Path(client.executable_path), os.path.normcase(os.path.normpath(client.executable_path)))
            for client in clients
        )

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
            for client, expected_executable, expected_path in targets:
                path_matches = (
                    os.path.normcase(os.path.normpath(found_path)) == expected_path
                    if expected_executable.is_absolute()
                    else Path(found_path).name.casefold() == expected_executable.name.casefold()
                )
                if path_matches:
                    matches.setdefault(hwnd, WindowRef(hwnd, title, Path(found_path).name, found_path, pid, client.client_id))
                    break
            return True

        win32gui.EnumWindows(callback, None)
        if len(matches) != 1:
            raise WindowLookupError(tuple(matches.values()))
        return next(iter(matches.values()))

    def restore_without_activation(self, window: WindowRef) -> None:
        if not win32gui.IsWindow(window.hwnd):
            raise WindowOperationError("Game window no longer exists")
        if not win32gui.IsIconic(window.hwnd):
            return
        win32gui.ShowWindow(window.hwnd, win32con.SW_SHOWNOACTIVATE)
        deadline = time.monotonic() + self._RESTORE_TIMEOUT_SECONDS
        while win32gui.IsIconic(window.hwnd):
            if not win32gui.IsWindow(window.hwnd):
                raise WindowOperationError("Game window no longer exists")
            if time.monotonic() >= deadline:
                raise WindowOperationError(
                    "Game window could not be restored without activation"
                )
            time.sleep(self._RESTORE_POLL_SECONDS)

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

    def _geometry_sample(self, window: WindowRef, api) -> WindowGeometry:
        state = self._inspect_physical(window)
        if not state.exists or state.minimized:
            raise WindowOperationError(f"game window unavailable during measurement: {state}")
        return WindowGeometry(
            state=state,
            style=win32gui.GetWindowLong(window.hwnd, win32con.GWL_STYLE),
            ex_style=win32gui.GetWindowLong(window.hwnd, win32con.GWL_EXSTYLE),
            window_dpi=int(api.GetDpiForWindow(window.hwnd)),
            window_dpi_awareness=int(api.GetAwarenessFromDpiAwarenessContext(
                api.GetWindowDpiAwarenessContext(window.hwnd),
            )),
            thread_dpi_awareness=int(api.GetAwarenessFromDpiAwarenessContext(
                api.GetThreadDpiAwarenessContext(),
            )),
        )

    def measure_geometry(self, window: WindowRef) -> WindowGeometry:
        with _physical_dpi_context() as api:
            first = self._geometry_sample(window, api)
            second = self._geometry_sample(window, api)
            if first.stability_key != second.stability_key:
                raise WindowGeometryChanged(f"window changed during measurement: {first} -> {second}")
            try:
                second.frame_margins
            except ValueError as exc:
                raise WindowOperationError(str(exc)) from exc
            if second.window_dpi <= 0 or second.window_dpi_awareness not in (0, 1, 2):
                raise WindowOperationError(f"invalid window DPI information: {second}")
            return second

    def set_outer_rect(self, window: WindowRef, rect: Rect, monitor_bounds: Rect) -> None:
        if (
            min(rect.width, rect.height, monitor_bounds.width, monitor_bounds.height) <= 0
            or rect.x < monitor_bounds.x or rect.y < monitor_bounds.y
            or rect.right > monitor_bounds.right or rect.bottom > monitor_bounds.bottom
        ):
            raise WindowOperationError(f"complete outer window does not fit monitor: {rect}")
        with _physical_dpi_context():
            state = self._inspect_physical(window)
            if not state.exists or state.minimized:
                raise WindowOperationError(f"game window unavailable before resizing: {state}")
            win32gui.SetWindowPos(
                window.hwnd, None, rect.x, rect.y, rect.width, rect.height,
                win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
            )

    def inspect(self, window: WindowRef) -> WindowState:
        with _physical_dpi_context():
            return self._inspect_physical(window)

    def _inspect_physical(self, window: WindowRef) -> WindowState:
        exists = bool(win32gui.IsWindow(window.hwnd))
        if not exists:
            return WindowState(False, False, False, Rect(0, 0, 0, 0))
        _, pid = win32process.GetWindowThreadProcessId(window.hwnd)
        if (
            (window.process_id and pid != window.process_id)
            or win32gui.GetWindowText(window.hwnd) != window.title
        ):
            return WindowState(False, False, False, Rect(0, 0, 0, 0))
        client = win32gui.GetClientRect(window.hwnd)
        origin = win32gui.ClientToScreen(window.hwnd, (client[0], client[1]))
        end = win32gui.ClientToScreen(window.hwnd, (client[2], client[3]))
        bounds = Rect(origin[0], origin[1], end[0] - origin[0], end[1] - origin[1])
        outer = win32gui.GetWindowRect(window.hwnd)
        return WindowState(
            True,
            bool(win32gui.IsIconic(window.hwnd)),
            win32gui.GetForegroundWindow() == window.hwnd,
            bounds,
            Rect(outer[0], outer[1], outer[2] - outer[0], outer[3] - outer[1]),
        )


class Win32RuntimeEnvironment:
    def is_elevated(self) -> bool:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
