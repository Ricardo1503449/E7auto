import ctypes
from ctypes import wintypes

import pytest
import win32con

from e7auto.platform.window_frame import NativeWindowFrame, client_rect_in_work_area, resize_hit_test


@pytest.mark.parametrize("work", [(0, 0, 1920, 1040), (-1920, -120, 0, 960), (80, 0, 2560, 1440)])
def test_maximized_client_clips_to_monitor_work_area(work):
    proposed = (work[0]-16, work[1]-16, work[2]+16, work[3]+16)
    assert client_rect_in_work_area(proposed, work) == work


@pytest.mark.parametrize("scale", [1, 1.5, 2])
@pytest.mark.parametrize("offset,expected", [
    ((1, 1), win32con.HTTOPLEFT), ((799, 1), win32con.HTTOPRIGHT),
    ((1, 599), win32con.HTBOTTOMLEFT), ((799, 599), win32con.HTBOTTOMRIGHT),
    ((400, 1), win32con.HTTOP), ((400, 599), win32con.HTBOTTOM),
    ((1, 300), win32con.HTLEFT), ((799, 300), win32con.HTRIGHT),
    ((740, 25), win32con.HTCLIENT), ((400, 300), win32con.HTCLIENT),
    ((-1, 300), win32con.HTNOWHERE),
])
def test_resize_edges_scale_and_leave_caption_controls_clickable(scale, offset, expected):
    # Negative screen coordinates cover monitors to the left/above the primary.
    left, top = -2000, -200
    rect = (left, top, left+round(800*scale), top+round(600*scale))
    x, y = left+round(offset[0]*scale), top+round(offset[1]*scale)
    assert resize_hit_test(rect, x, y, round(6*scale), round(14*scale)) == expected


def test_unrelated_native_messages_do_not_access_window_geometry():
    adapter = NativeWindowFrame()
    assert adapter.handle_message(0) is None
    message = wintypes.MSG()
    message.message = win32con.WM_TIMER
    assert adapter.handle_message(ctypes.addressof(message)) is None
