"""Native Windows frame capabilities for an opaque, client-drawn main window.

Qt keeps its frameless client geometry. Windows retains a standard frame for DWM
transitions, while WM_NCCALCSIZE removes its visible caption and resize borders.
This adapter never changes global animation settings or another window's styles.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import win32api
import win32con
import win32gui


class _Margins(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int) for name in ("left", "right", "top", "bottom")]


def client_rect_in_work_area(rect: tuple[int, int, int, int],
                             work: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Clip invisible maximized frame margins, using physical screen coordinates."""
    return (max(rect[0], work[0]), max(rect[1], work[1]),
            min(rect[2], work[2]), min(rect[3], work[3]))


def resize_hit_test(rect: tuple[int, int, int, int], x: int, y: int,
                    edge: int, corner: int) -> int:
    """Keep caption buttons/client widgets interactive; expose only resize edges."""
    left, top, right, bottom = rect
    if not (left <= x < right and top <= y < bottom):
        return win32con.HTNOWHERE
    if x < left + edge or y < top + edge or x >= right - edge or y >= bottom - edge:
        if x < left + corner and y < top + corner:
            return win32con.HTTOPLEFT
        if x >= right - corner and y < top + corner:
            return win32con.HTTOPRIGHT
        if x < left + corner and y >= bottom - corner:
            return win32con.HTBOTTOMLEFT
        if x >= right - corner and y >= bottom - corner:
            return win32con.HTBOTTOMRIGHT
        if x < left + edge:
            return win32con.HTLEFT
        if x >= right - edge:
            return win32con.HTRIGHT
        if y < top + edge:
            return win32con.HTTOP
        if y >= bottom - edge:
            return win32con.HTBOTTOM
    return win32con.HTCLIENT


class NativeWindowFrame:
    def __init__(self) -> None:
        self._hwnd: int | None = None
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._dwmapi = ctypes.WinDLL("dwmapi")
        self._user32.IsZoomed.argtypes = [wintypes.HWND]
        self._user32.IsZoomed.restype = wintypes.BOOL
        self._user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        self._user32.GetDpiForWindow.restype = wintypes.UINT
        self._dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ]
        self._dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        self._dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(_Margins)]
        self._dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long

    def attach(self, hwnd: int) -> None:
        if self._hwnd == hwnd:
            return
        self._hwnd = hwnd
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE,
                              style | win32con.WS_CAPTION | win32con.WS_THICKFRAME
                              | win32con.WS_SYSMENU | win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX)
        self._configure_dwm(hwnd)
        win32gui.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
            | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED,
        )

    def _configure_dwm(self, hwnd: int) -> None:
        # NCRENDERING_POLICY=ENABLED; TRANSITIONS_FORCEDISABLED=FALSE;
        # WINDOW_CORNER_PREFERENCE=ROUND (unsupported on pre-Windows-11 systems).
        for attribute, value in ((2, 2), (3, 0), (33, 2)):
            setting = ctypes.c_int(value)
            self._dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(setting), ctypes.sizeof(setting))
        margins = _Margins(1, 1, 1, 1)
        self._dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))

    def handle_message(self, address: int) -> tuple[bool, int] | None:
        if not address:
            return None
        message = wintypes.MSG.from_address(address)
        if message.message not in (win32con.WM_NCCALCSIZE, win32con.WM_NCHITTEST, 0x031E):
            return None
        hwnd = int(message.hWnd or 0)
        if not win32gui.IsWindow(hwnd):
            return None
        if message.message == 0x031E:  # WM_DWMCOMPOSITIONCHANGED
            self._configure_dwm(hwnd)
            return None
        if message.message == win32con.WM_NCCALCSIZE:
            if not message.wParam or not message.lParam:
                return None
            rect = wintypes.RECT.from_address(message.lParam)  # first RECT in NCCALCSIZE_PARAMS
            if self._user32.IsZoomed(hwnd):
                proposed = (rect.left, rect.top, rect.right, rect.bottom)
                monitor = win32api.MonitorFromRect(proposed, win32con.MONITOR_DEFAULTTONEAREST)
                work = win32api.GetMonitorInfo(monitor)["Work"]
                rect.left, rect.top, rect.right, rect.bottom = client_rect_in_work_area(proposed, work)
            return True, 0
        if self._user32.IsZoomed(hwnd):
            return True, win32con.HTCLIENT
        x = ctypes.c_short(message.lParam & 0xFFFF).value
        y = ctypes.c_short((message.lParam >> 16) & 0xFFFF).value
        scale = (self._user32.GetDpiForWindow(hwnd) or 96) / 96
        result = resize_hit_test(win32gui.GetWindowRect(hwnd), x, y,
                                 max(1, round(6 * scale)), max(1, round(14 * scale)))
        return True, result
