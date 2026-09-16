from __future__ import annotations

from types import SimpleNamespace

import pytest
import pywintypes

from e7auto import platform_windows
from e7auto.platform_windows import (
    Win32F5HotkeyService,
    Win32RuntimeEnvironment,
    Win32WindowService,
    WindowLookupError,
    WindowOperationError,
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
    assert found.process_id == 25428


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


def test_restored_background_window_is_left_unactivated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[tuple[int, int]] = []
    monkeypatch.setattr(platform_windows.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(platform_windows.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(
        platform_windows.win32gui,
        "ShowWindow",
        lambda hwnd, command: shown.append((hwnd, command)),
    )

    Win32WindowService().restore_without_activation(
        WindowRef(100, "第七史诗", "EpicSeven.exe")
    )

    assert shown == []


def test_minimized_window_is_restored_without_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iconic = iter((True, False))
    shown: list[tuple[int, int]] = []
    monkeypatch.setattr(platform_windows.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(
        platform_windows.win32gui,
        "IsIconic",
        lambda _hwnd: next(iconic),
    )
    monkeypatch.setattr(
        platform_windows.win32gui,
        "ShowWindow",
        lambda hwnd, command: shown.append((hwnd, command)),
    )

    Win32WindowService().restore_without_activation(
        WindowRef(100, "第七史诗", "EpicSeven.exe")
    )

    assert shown == [(100, platform_windows.win32con.SW_SHOWNOACTIVATE)]


def test_minimized_window_restore_timeout_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter((10.0, 12.0))
    monkeypatch.setattr(platform_windows.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(platform_windows.win32gui, "IsIconic", lambda _hwnd: True)
    monkeypatch.setattr(platform_windows.win32gui, "ShowWindow", lambda *_args: None)
    monkeypatch.setattr(platform_windows.time, "monotonic", lambda: next(times))

    with pytest.raises(WindowOperationError, match="restored without activation"):
        Win32WindowService().restore_without_activation(
            WindowRef(100, "第七史诗", "EpicSeven.exe")
        )


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
    assert not hasattr(service, "_F6_HOTKEY_ID")


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


def test_runtime_environment_reads_the_windows_administrator_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        platform_windows.ctypes,
        "windll",
        SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1)),
    )

    assert Win32RuntimeEnvironment().is_elevated() is True
