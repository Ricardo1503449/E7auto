from __future__ import annotations

from pathlib import Path

import pytest
import win32con

from e7auto import background_windows
from e7auto.background_windows import (
    BackgroundInputError,
    Win32WindowMessageInputService,
    _pack_signed_point,
    _pack_wheel_wparam,
)
from e7auto.config import Point
from e7auto.ports import WindowRef


ROOT = Path(__file__).resolve().parents[1]


def install_valid_window(monkeypatch: pytest.MonkeyPatch) -> WindowRef:
    window = WindowRef(101, "第七史诗", "EpicSeven.exe", r"D:\Games\EpicSeven.exe", 77)
    monkeypatch.setattr(background_windows.win32gui, "IsWindow", lambda hwnd: hwnd == 101)
    monkeypatch.setattr(background_windows.win32gui, "IsIconic", lambda hwnd: False)
    monkeypatch.setattr(
        background_windows.win32gui,
        "GetWindowText",
        lambda hwnd: "第七史诗",
    )
    monkeypatch.setattr(
        background_windows.win32gui,
        "GetClientRect",
        lambda hwnd: (0, 0, 200, 100),
    )
    monkeypatch.setattr(
        background_windows.win32process,
        "GetWindowThreadProcessId",
        lambda hwnd: (12, 77),
    )
    monkeypatch.setattr(
        background_windows,
        "_process_path",
        lambda pid: r"D:\Games\EpicSeven.exe",
    )
    return window


def test_signed_message_values_preserve_negative_multi_monitor_coordinates() -> None:
    packed = _pack_signed_point(-120, 450)
    assert packed & 0xFFFF == (-120 & 0xFFFF)
    assert (packed >> 16) & 0xFFFF == 450
    assert _pack_wheel_wparam(-120) == (-120 & 0xFFFF) << 16


def test_message_values_reject_unrepresentable_coordinates_and_delta() -> None:
    with pytest.raises(BackgroundInputError, match="signed 16-bit"):
        _pack_signed_point(40000, 1)
    with pytest.raises(BackgroundInputError, match="wheel delta"):
        _pack_wheel_wparam(0)


def test_background_click_posts_hover_down_up_without_cursor_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = install_valid_window(monkeypatch)
    events: list[tuple[object, ...]] = []
    service = Win32WindowMessageInputService(
        post_message=lambda *args: events.append(("post", *args)),
        sleep=lambda seconds: events.append(("sleep", seconds)),
    )

    service.click(window, Point(12, 34))

    point = _pack_signed_point(12, 34)
    assert events == [
        ("post", 101, win32con.WM_MOUSEMOVE, 0, point),
        ("sleep", 0.10),
        ("post", 101, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, point),
        ("sleep", 0.05),
        ("post", 101, win32con.WM_LBUTTONUP, 0, point),
    ]


def test_background_click_releases_button_when_hold_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = install_valid_window(monkeypatch)
    messages: list[int] = []

    def sleep(seconds: float) -> None:
        if seconds == 0.05:
            raise RuntimeError("synthetic hold failure")

    service = Win32WindowMessageInputService(
        post_message=lambda _hwnd, message, _wparam, _lparam: messages.append(message),
        sleep=sleep,
    )

    with pytest.raises(RuntimeError, match="hold failure"):
        service.click(window, Point(12, 34))

    assert messages[-2:] == [win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP]


def test_background_wheel_uses_client_hover_and_signed_screen_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = install_valid_window(monkeypatch)
    monkeypatch.setattr(
        background_windows.win32gui,
        "ClientToScreen",
        lambda hwnd, point: (-100, 250),
    )
    messages: list[tuple[int, int, int]] = []
    service = Win32WindowMessageInputService(
        post_message=lambda _hwnd, message, wparam, lparam: messages.append(
            (message, wparam, lparam)
        )
    )

    service.scroll(window, Point(12, 34), -120)

    assert messages == [
        (win32con.WM_MOUSEMOVE, 0, _pack_signed_point(12, 34)),
        (
            win32con.WM_MOUSEWHEEL,
            _pack_wheel_wparam(-120),
            _pack_signed_point(-100, 250),
        ),
    ]


def test_background_input_rejects_minimized_or_reused_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = install_valid_window(monkeypatch)
    service = Win32WindowMessageInputService(post_message=lambda *args: None)
    monkeypatch.setattr(background_windows.win32gui, "IsIconic", lambda hwnd: True)
    with pytest.raises(BackgroundInputError, match="minimized"):
        service.click(window, Point(12, 34))

    monkeypatch.setattr(background_windows.win32gui, "IsIconic", lambda hwnd: False)
    monkeypatch.setattr(
        background_windows.win32process,
        "GetWindowThreadProcessId",
        lambda hwnd: (12, 99),
    )
    with pytest.raises(BackgroundInputError, match="process identity"):
        service.scroll(window, Point(12, 34), -120)


def test_runtime_source_contains_no_printwindow_or_real_cursor_backend() -> None:
    forbidden = (
        "PrintWindow",
        "PrintWindowCaptureService",
        "PW_RENDERFULLCONTENT",
        "WM_PRINT",
        "Win32InputService",
        "SetCursorPos",
        "GetCursorPos",
        "mouse_event",
        "SendInput",
        "pyautogui",
    )
    violations: list[str] = []
    for path in sorted((ROOT / "src" / "e7auto").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        violations.extend(
            f"{path.name}: {symbol}"
            for symbol in forbidden
            if symbol in source
        )

    assert violations == []
