from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
import pywintypes

from e7auto import platform_windows
from e7auto.config import Point
from e7auto.platform_windows import (
    MssCaptureService,
    Win32F5HotkeyService,
    Win32InputService,
    Win32RuntimeEnvironment,
    Win32WindowService,
    WindowLookupError,
)
from e7auto.config import Rect
from e7auto.ports import WindowRef


def install_single_window(monkeypatch: pytest.MonkeyPatch, process_path: str) -> None:
    monkeypatch.setattr(platform_windows.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(platform_windows.win32gui, "GetWindowText", lambda hwnd: "第七史诗")
    monkeypatch.setattr(
        platform_windows.win32process,
        "GetWindowThreadProcessId",
        lambda hwnd: (1, 25428),
    )
    monkeypatch.setattr(
        platform_windows.win32gui,
        "EnumWindows",
        lambda callback, context: callback(100, context),
    )
    monkeypatch.setattr(platform_windows, "_process_path", lambda pid: process_path)


def test_window_lookup_compares_the_full_executable_path(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = r"D:\Games\EpicSeven\EpicSeven.exe"
    install_single_window(monkeypatch, expected)
    found = Win32WindowService().locate_unique(expected, "第七史诗")
    assert found.hwnd == 100
    assert found.process_name == "EpicSeven.exe"
    assert found.executable_path == expected


def test_window_lookup_rejects_same_basename_from_another_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_single_window(monkeypatch, r"D:\Other\EpicSeven.exe")
    with pytest.raises(WindowLookupError):
        Win32WindowService().locate_unique(
            r"D:\Games\EpicSeven\EpicSeven.exe",
            "第七史诗",
        )


def test_window_lookup_finds_named_executable_in_any_install_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovered = r"E:\User Games\Epic Seven\EpicSeven.exe"
    install_single_window(monkeypatch, discovered)

    found = Win32WindowService().locate_unique("EpicSeven.exe", "第七史诗")

    assert found.hwnd == 100
    assert found.process_name == "EpicSeven.exe"
    assert found.executable_path == discovered


def test_window_lookup_named_executable_rejects_different_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_single_window(monkeypatch, r"E:\User Games\Epic Seven\Other.exe")

    with pytest.raises(WindowLookupError):
        Win32WindowService().locate_unique("EpicSeven.exe", "第七史诗")


def test_capture_service_uses_srccopy_and_restores_captureblt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_captureblt: list[int] = []
    original = platform_windows.mss_gdi.CAPTUREBLT

    class Grabber:
        def __enter__(self) -> Grabber:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def grab(self, _monitor: object) -> object:
            observed_captureblt.append(platform_windows.mss_gdi.CAPTUREBLT)
            return [[[1, 2, 3, 4]]]

    monkeypatch.setattr(platform_windows.mss, "mss", Grabber)
    frame = MssCaptureService().capture_client(
        WindowRef(1, "game", "game.exe"),
        Rect(10, 20, 1, 1),
    )

    assert observed_captureblt == [0]
    assert platform_windows.mss_gdi.CAPTUREBLT == original
    assert frame.shape == (1, 1, 4)


def test_capture_service_restores_captureblt_after_grab_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = platform_windows.mss_gdi.CAPTUREBLT

    class FailingGrabber:
        def __enter__(self) -> FailingGrabber:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def grab(self, _monitor: object) -> object:
            assert platform_windows.mss_gdi.CAPTUREBLT == 0
            raise RuntimeError("synthetic capture failure")

    monkeypatch.setattr(platform_windows.mss, "mss", FailingGrabber)
    with pytest.raises(RuntimeError, match="synthetic capture failure"):
        MssCaptureService().capture_client(
            WindowRef(1, "game", "game.exe"),
            Rect(10, 20, 1, 1),
        )

    assert platform_windows.mss_gdi.CAPTUREBLT == original


def test_f5_hotkey_treats_pywin32_none_return_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: list[tuple[object, int, int, int]] = []
    unregistered: list[tuple[object, int]] = []

    def register_hotkey(hwnd: object, hotkey_id: int, modifiers: int, key: int) -> None:
        registered.append((hwnd, hotkey_id, modifiers, key))

    monkeypatch.setattr(platform_windows.win32gui, "RegisterHotKey", register_hotkey)
    monkeypatch.setattr(platform_windows.win32gui, "GetMessage", lambda *args: (0, None))
    monkeypatch.setattr(
        platform_windows.win32gui,
        "UnregisterHotKey",
        lambda hwnd, hotkey_id: unregistered.append((hwnd, hotkey_id)),
    )
    monkeypatch.setattr(platform_windows.win32api, "GetCurrentThreadId", lambda: 1234)
    monkeypatch.setattr(platform_windows.win32api, "PostThreadMessage", lambda *args: None)

    service = Win32F5HotkeyService()
    assert service.register_f5(lambda: None) is True
    service.unregister_f5()

    assert registered == [(None, service._HOTKEY_ID, 0, platform_windows.win32con.VK_F5)]
    assert unregistered == [(None, service._HOTKEY_ID)]


def test_hotkey_service_registers_f5_and_f6_for_runtime_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: list[tuple[int, int]] = []
    unregistered: list[int] = []
    monkeypatch.setattr(
        platform_windows.win32gui,
        "RegisterHotKey",
        lambda _hwnd, hotkey_id, _modifiers, key: registered.append((hotkey_id, key)),
    )
    monkeypatch.setattr(platform_windows.win32gui, "GetMessage", lambda *args: (0, None))
    monkeypatch.setattr(
        platform_windows.win32gui,
        "UnregisterHotKey",
        lambda _hwnd, hotkey_id: unregistered.append(hotkey_id),
    )
    monkeypatch.setattr(platform_windows.win32api, "GetCurrentThreadId", lambda: 1234)
    monkeypatch.setattr(platform_windows.win32api, "PostThreadMessage", lambda *args: None)

    service = Win32F5HotkeyService()
    assert service.register_f5(lambda: None, lambda: None)
    service.unregister_f5()

    assert registered == [
        (service._F5_HOTKEY_ID, platform_windows.win32con.VK_F5),
        (service._F6_HOTKEY_ID, platform_windows.win32con.VK_F6),
    ]
    assert unregistered == [service._F5_HOTKEY_ID, service._F6_HOTKEY_ID]


def test_f5_hotkey_keeps_real_registration_failure_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_hotkey(*args: object) -> None:
        raise pywintypes.error(1409, "RegisterHotKey", "Hot key already registered")

    monkeypatch.setattr(platform_windows.win32gui, "RegisterHotKey", reject_hotkey)
    monkeypatch.setattr(platform_windows.win32api, "GetCurrentThreadId", lambda: 1234)

    service = Win32F5HotkeyService()
    assert service.register_f5(lambda: None) is False
    assert service._thread is None
    assert service._thread_id is None


def test_f5_hotkey_uses_the_documented_application_id_range() -> None:
    assert 0x0000 <= Win32F5HotkeyService._HOTKEY_ID <= 0xBFFF


def test_input_service_reads_back_the_current_cursor_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform_windows.win32api, "GetCursorPos", lambda: (12, 34))

    assert Win32InputService().position() == Point(12, 34)


def test_input_service_click_waits_for_hover_and_holds_left_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        platform_windows.win32api,
        "SetCursorPos",
        lambda point: events.append(("move", point)),
    )
    monkeypatch.setattr(
        platform_windows.win32api,
        "mouse_event",
        lambda flag, *args: events.append(("mouse", flag)),
    )
    monkeypatch.setattr(
        time,
        "sleep",
        lambda seconds: events.append(("sleep", seconds)),
    )

    Win32InputService().click(Point(12, 34))

    assert events == [
        ("move", (12, 34)),
        ("sleep", 0.10),
        ("mouse", platform_windows.win32con.MOUSEEVENTF_LEFTDOWN),
        ("sleep", 0.05),
        ("mouse", platform_windows.win32con.MOUSEEVENTF_LEFTUP),
    ]


def test_input_service_does_not_reposition_cursor_for_repeated_same_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moves: list[object] = []
    monkeypatch.setattr(
        platform_windows.win32api,
        "SetCursorPos",
        lambda point: moves.append(point),
    )
    monkeypatch.setattr(
        platform_windows.win32api,
        "GetCursorPos",
        lambda: (12, 34),
    )
    service = Win32InputService()

    service.move(Point(12, 34))
    service.scroll(Point(12, 34), -120)
    service.click(Point(12, 34))

    assert moves == [(12, 34)]


def test_runtime_environment_reads_the_windows_administrator_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        platform_windows.ctypes,
        "windll",
        SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1)),
    )

    assert Win32RuntimeEnvironment().is_elevated() is True
