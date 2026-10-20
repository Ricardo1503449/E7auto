from __future__ import annotations

from pathlib import Path
from tests.helpers.paths import ROOT

import pytest
import win32con

import e7auto.platform.input as background_windows
from e7auto.platform.input import BackgroundInputError, Win32WindowMessageInputService, _pack_signed_point, _pack_wheel_wparam
from e7auto.core.types import Point
from e7auto.core.ports import WindowRef





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


def test_drag_messages_hold_the_button_and_use_client_coordinates(monkeypatch):
    window=install_valid_window(monkeypatch);events=[]
    service=Win32WindowMessageInputService(post_message=lambda *args:events.append(args),sleep=lambda _:None)
    service.pointer_down(window,Point(12,20))
    service.pointer_move(window,Point(40,55))
    service.pointer_move(window,Point(80,90))
    service.pointer_up(window,Point(80,90))
    assert events==[
        (101,win32con.WM_MOUSEMOVE,0,_pack_signed_point(12,20)),
        (101,win32con.WM_LBUTTONDOWN,win32con.MK_LBUTTON,_pack_signed_point(12,20)),
        (101,win32con.WM_MOUSEMOVE,win32con.MK_LBUTTON,_pack_signed_point(40,55)),
        (101,win32con.WM_MOUSEMOVE,win32con.MK_LBUTTON,_pack_signed_point(80,90)),
        (101,win32con.WM_LBUTTONUP,0,_pack_signed_point(80,90)),
    ]


def test_release_only_cleanup_is_allowed_after_resize_or_minimize(monkeypatch):
    window=install_valid_window(monkeypatch);events=[]
    service=Win32WindowMessageInputService(post_message=lambda *args:events.append(args),sleep=lambda _:None)
    service.pointer_down(window,Point(80,90))
    monkeypatch.setattr(background_windows.win32gui,'IsIconic',lambda _:True)
    monkeypatch.setattr(background_windows.win32gui,'GetClientRect',lambda _:(0,0,10,10))
    service.pointer_up(window,Point(80,90))
    assert events[-1]==(101,win32con.WM_LBUTTONUP,0,_pack_signed_point(80,90))
    with pytest.raises(BackgroundInputError):service.pointer_move(window,Point(1,1))


def test_release_never_targets_a_reused_window_handle(monkeypatch):
    window=install_valid_window(monkeypatch);events=[]
    service=Win32WindowMessageInputService(post_message=lambda *args:events.append(args),sleep=lambda _:None)
    monkeypatch.setattr(background_windows.win32process,'GetWindowThreadProcessId',lambda _:(12,99))
    with pytest.raises(BackgroundInputError):service.pointer_up(window,Point(80,90))
    assert events==[]


def test_long_drag_preserves_message_coordinates_only_with_explicit_active_owner(monkeypatch):
    window=install_valid_window(monkeypatch);events=[]
    service=Win32WindowMessageInputService(post_message=lambda *args:events.append(args),sleep=lambda _:None)
    end=Point(12,5320)
    with pytest.raises(BackgroundInputError,match='active drag'):
        service.pointer_move(window,end,allow_outside_client=True)
    with pytest.raises(BackgroundInputError,match='outside'):
        service.pointer_down(window,end)
    assert events==[]
    service.pointer_down(window,Point(12,20))
    with pytest.raises(BackgroundInputError,match='outside'):
        service.pointer_move(window,end)
    for y in (1080,2140,3200,4260,5320):
        service.pointer_move(window,Point(12,y),allow_outside_client=True)
    assert events[2:]==[(101,win32con.WM_MOUSEMOVE,win32con.MK_LBUTTON,_pack_signed_point(12,y))
                        for y in (1080,2140,3200,4260,5320)]
    with pytest.raises(BackgroundInputError,match='signed 16-bit'):
        service.pointer_move(window,Point(12,40000),allow_outside_client=True)
    service.pointer_up(window,end)
    assert events[-1]==(101,win32con.WM_LBUTTONUP,0,_pack_signed_point(12,5320))
    with pytest.raises(BackgroundInputError,match='active drag'):
        service.pointer_move(window,end,allow_outside_client=True)


def test_long_drag_still_rejects_changed_window_identity(monkeypatch):
    window=install_valid_window(monkeypatch);events=[]
    service=Win32WindowMessageInputService(post_message=lambda *args:events.append(args),sleep=lambda _:None)
    service.pointer_down(window,Point(12,20));count=len(events)
    monkeypatch.setattr(background_windows.win32process,'GetWindowThreadProcessId',lambda _:(12,99))
    with pytest.raises(BackgroundInputError,match='process identity'):
        service.pointer_move(window,Point(12,5320),allow_outside_client=True)
    assert len(events)==count


@pytest.mark.parametrize("action", ["click", "scroll", "drag"])
def test_input_timestamp_notification_follows_every_successful_message(monkeypatch, action):
    window = install_valid_window(monkeypatch)
    monkeypatch.setattr(background_windows.win32gui, "ClientToScreen", lambda hwnd, p: p)
    events = []
    service = Win32WindowMessageInputService(
        post_message=lambda hwnd, message, wparam, lparam: events.append(("sent", message)),
        sleep=lambda _: None,
        on_input_complete=lambda: events.append(("marked", events[-1][1])),
    )
    if action == "click":
        service.click(window, Point(12, 34))
    elif action == "scroll":
        service.scroll(window, Point(12, 34), -120)
    else:
        service.pointer_down(window, Point(12, 34))
        service.pointer_move(window, Point(20, 40))
        service.pointer_up(window, Point(20, 40))
    assert events
    for sent, marked in zip(events[::2], events[1::2]):
        assert sent[0] == "sent" and marked == ("marked", sent[1])
    assert events[-1][1] == (win32con.WM_MOUSEWHEEL if action == "scroll" else win32con.WM_LBUTTONUP)


def test_failed_message_is_not_reported_as_completed_input(monkeypatch):
    window = install_valid_window(monkeypatch)
    monkeypatch.setattr(background_windows.win32gui, "ClientToScreen", lambda hwnd, p: p)
    marks = []
    service = Win32WindowMessageInputService(
        post_message=lambda *_: (_ for _ in ()).throw(OSError("not sent")),
        on_input_complete=lambda: marks.append(1),
    )
    with pytest.raises(BackgroundInputError):
        service.scroll(window, Point(12, 34), -120)
    assert marks == []


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
