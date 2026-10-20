import ctypes
from ctypes import wintypes
import sys

import pytest
import win32api
import win32con
import win32gui
from PySide6.QtCore import Qt

from e7auto.ui import MainWindow


def test_dwm_frame_geometry_corners_and_native_hit_testing(tmp_path, qt_application):
    if qt_application.platformName() != "windows":
        pytest.skip("requires the native Windows Qt platform")
    window = MainWindow(tmp_path)
    try:
        initial = window.size()
        window.show()
        qt_application.processEvents()
        hwnd = int(window.winId())
        get_attribute = ctypes.windll.dwmapi.DwmGetWindowAttribute
        get_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        get_attribute.restype = ctypes.c_long
        rendering = wintypes.BOOL()
        assert get_attribute(hwnd, 1, ctypes.byref(rendering), ctypes.sizeof(rendering)) == 0
        assert rendering.value  # DWM's non-client renderer remains enabled.
        if sys.getwindowsversion().build >= 22000:
            corners = ctypes.c_int()
            assert get_attribute(hwnd, 33, ctypes.byref(corners), ctypes.sizeof(corners)) == 0
            assert corners.value == 2  # DWMWCP_ROUND
        assert not window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = win32gui.GetWindowRect(hwnd)
        client = win32gui.GetClientRect(hwnd)
        assert win32gui.ClientToScreen(hwnd, (0, 0)) == outer[:2]
        assert (client[2], client[3]) == (outer[2]-outer[0], outer[3]-outer[1])
        x, y = outer[0]+1, (outer[1]+outer[3])//2
        point = (x & 0xffff) | ((y & 0xffff) << 16)
        assert win32gui.SendMessage(hwnd, win32con.WM_NCHITTEST, 0, point) == win32con.HTLEFT
        # The painted minimize button stays a client control, not a ghost native button.
        button = window._title_bar._minimize
        logical = button.mapTo(window, button.rect().center())
        dpr = window.devicePixelRatioF()
        x, y = outer[0]+round(logical.x()*dpr), outer[1]+round(logical.y()*dpr)
        point = (x & 0xffff) | ((y & 0xffff) << 16)
        assert win32gui.SendMessage(hwnd, win32con.WM_NCHITTEST, 0, point) == win32con.HTCLIENT
        for _ in range(3):
            window.showMaximized()
            qt_application.processEvents()
            work = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, 2))["Work"]
            origin = win32gui.ClientToScreen(hwnd, (0, 0))
            client = win32gui.GetClientRect(hwnd)
            assert (*origin, origin[0]+client[2], origin[1]+client[3]) == work
            window.showNormal()
            qt_application.processEvents()
            assert window.size() == initial
        button.click()
        qt_application.processEvents()
        assert window.isMinimized()
        window.showNormal()
        qt_application.processEvents()
        assert not window.isMinimized()
    finally:
        window.close()
