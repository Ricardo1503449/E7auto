from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

import win32con
import win32gui
import win32process

from .config import Point, Rect
from .ports import CaptureError, WindowRef


_CLICK_HOVER_SECONDS = 0.10
_CLICK_HOLD_SECONDS = 0.05


class BackgroundCaptureError(CaptureError):
    pass


class BackgroundInputError(RuntimeError):
    pass


def _process_path(pid: int) -> str:
    process_query_limited_information = 0x1000
    kernel32 = ctypes.windll.kernel32
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    query_path = kernel32.QueryFullProcessImageNameW
    query_path.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    query_path.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return ""
    try:
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        succeeded = query_path(
            handle,
            0,
            buffer,
            ctypes.byref(capacity),
        )
        return str(Path(buffer.value)) if succeeded else ""
    finally:
        close_handle(handle)


def _normalized_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def _validate_window_identity(window: WindowRef) -> int:
    if not win32gui.IsWindow(window.hwnd):
        raise BackgroundInputError("game window no longer exists")
    if win32gui.IsIconic(window.hwnd):
        raise BackgroundInputError("game window is minimized")
    if win32gui.GetWindowText(window.hwnd) != window.title:
        raise BackgroundInputError("game window title changed")
    _, pid = win32process.GetWindowThreadProcessId(window.hwnd)
    expected_pid = int(getattr(window, "process_id", 0))
    if expected_pid and pid != expected_pid:
        raise BackgroundInputError("game window process identity changed")
    actual_path = _process_path(pid)
    if not actual_path or (
        window.executable_path
        and _normalized_path(actual_path) != _normalized_path(window.executable_path)
    ):
        raise BackgroundInputError("game executable identity changed")
    return int(window.hwnd)


def _pack_signed_point(x: int, y: int) -> int:
    if not (-32768 <= x <= 32767 and -32768 <= y <= 32767):
        raise BackgroundInputError(f"message coordinate is outside signed 16-bit range: {(x, y)}")
    return (x & 0xFFFF) | ((y & 0xFFFF) << 16)


def _pack_wheel_wparam(delta: int) -> int:
    if not -32768 <= delta <= 32767 or delta == 0:
        raise BackgroundInputError(f"invalid wheel delta: {delta}")
    return (delta & 0xFFFF) << 16


class Win32WindowMessageInputService:
    """Dispatch mouse messages directly to the game HWND without moving the cursor."""

    def __init__(
        self,
        *,
        post_message: Callable[[int, int, int, int], object] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._post_message = post_message or win32gui.PostMessage
        self._sleep = sleep

    @staticmethod
    def _validate_client_point(hwnd: int, point: Point) -> None:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        if not (left <= point.x < right and top <= point.y < bottom):
            raise BackgroundInputError(
                f"client point is outside the game window: {point} not in {(left, top, right, bottom)}"
            )

    def _dispatch(self, hwnd: int, message: int, wparam: int, lparam: int) -> None:
        try:
            self._post_message(hwnd, message, wparam, lparam)
        except Exception as exc:
            raise BackgroundInputError(
                f"PostMessage failed for message 0x{message:04X}: {exc}"
            ) from exc

    def click(self, window: WindowRef, point: Point) -> None:
        hwnd = _validate_window_identity(window)
        self._validate_client_point(hwnd, point)
        client_lparam = _pack_signed_point(point.x, point.y)
        self._dispatch(hwnd, win32con.WM_MOUSEMOVE, 0, client_lparam)
        self._sleep(_CLICK_HOVER_SECONDS)

        if _validate_window_identity(window) != hwnd:
            raise BackgroundInputError("game window changed during click hover")
        self._validate_client_point(hwnd, point)
        down_sent = False
        try:
            self._dispatch(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, client_lparam)
            down_sent = True
            self._sleep(_CLICK_HOLD_SECONDS)
        finally:
            if down_sent:
                self._dispatch(hwnd, win32con.WM_LBUTTONUP, 0, client_lparam)

    def scroll(self, window: WindowRef, point: Point, delta: int) -> None:
        hwnd = _validate_window_identity(window)
        self._validate_client_point(hwnd, point)
        client_lparam = _pack_signed_point(point.x, point.y)
        screen_x, screen_y = win32gui.ClientToScreen(hwnd, (point.x, point.y))
        screen_lparam = _pack_signed_point(int(screen_x), int(screen_y))
        self._dispatch(hwnd, win32con.WM_MOUSEMOVE, 0, client_lparam)
        self._dispatch(
            hwnd,
            win32con.WM_MOUSEWHEEL,
            _pack_wheel_wparam(delta),
            screen_lparam,
        )
