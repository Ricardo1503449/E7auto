from __future__ import annotations

from types import SimpleNamespace

import pytest

import e7auto.platform.windows as platform_windows
from e7auto.platform.windows import Win32RuntimeEnvironment, Win32WindowService, WindowLookupError, WindowOperationError
from e7auto.core.types import Rect
from e7auto.core.ports import WindowRef


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


def test_runtime_environment_reads_the_windows_administrator_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        platform_windows.ctypes,
        "windll",
        SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1)),
    )

    assert Win32RuntimeEnvironment().is_elevated() is True
