from pathlib import Path
import json
import subprocess
import sys
import weakref

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow
from shiboken6 import delete, isValid

from e7auto.ui import MainWindow
from e7auto.ui.window_chrome import _ResizeHandle, _TitleBar
from tests.helpers.paths import ROOT
from tests.helpers.qt import dispose_widgets


class MousePress:
    """No native input: provide only the mouse-event methods used by the chrome."""
    accepted = False

    def button(self):
        return Qt.MouseButton.LeftButton

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def test_chrome_does_not_keep_window_alive(tmp_path, qt_application):
    window = MainWindow(tmp_path)
    reference = weakref.ref(window)
    # Retain Python child wrappers deliberately. They must not own the main window.
    chrome = [window._title_bar, *window._resize_handles]
    window.close()
    qt_application.processEvents()
    del window
    # No gc.collect(): reference-counted window teardown must work on its own.
    assert reference() is None
    assert all(not isValid(widget) for widget in chrome)


@pytest.mark.parametrize("native_only", [False, True])
def test_chrome_ignores_events_after_window_destruction(native_only):
    window = QMainWindow()
    bar = _TitleBar(window, Path("missing-test-icon.ico"))
    handle = _ResizeHandle(window, Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor)
    # Cover both an expired weak reference and a live wrapper around deleted C++ storage.
    if native_only:
        delete(window)
    else:
        del window
    event = MousePress()
    bar._toggle_maximized()
    bar.sync_maximize_state()
    bar.mousePressEvent(event)
    bar.mouseDoubleClickEvent(event)
    handle.mousePressEvent(event)
    assert not event.accepted


def test_drag_and_resize_still_dispatch_to_the_live_window(monkeypatch):
    window = QMainWindow()
    bar = _TitleBar(window, Path("missing-test-icon.ico"))
    edge = Qt.Edge.RightEdge | Qt.Edge.BottomEdge
    handle = _ResizeHandle(window, edge, Qt.CursorShape.SizeFDiagCursor)
    calls = []

    class NativeHandle:
        def startSystemMove(self):
            calls.append("move")
            return True

        def startSystemResize(self, edges):
            calls.append(edges)
            return True

    monkeypatch.setattr(window, "windowHandle", lambda: NativeHandle())
    move, resize = MousePress(), MousePress()
    bar.mousePressEvent(move)
    handle.mousePressEvent(resize)
    assert calls == ["move", edge]
    assert move.accepted and resize.accepted


def test_cleanup_destroys_main_window_overlay_and_active_timers(tmp_path, qt_application):
    window = MainWindow(tmp_path)
    overlay = window._overlay
    timer = overlay._elapsed_timer
    overlay.start_elapsed_timer()
    assert timer.isActive()
    dispose_widgets(qt_application, [window, overlay])
    assert not any(isValid(obj) for obj in [window, overlay, timer])


@pytest.mark.parametrize("cleanup", ["close", "explicit"])
def test_repeated_native_window_lifecycles_exit_cleanly(tmp_path, cleanup):
    command = [sys.executable, "-B", "-X", "faulthandler", "-m", "tests.helpers.qt",
               "--root", str(tmp_path), "--rounds", "30", "--cleanup", cleanup]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, f"Native process exited {result.returncode}\n{result.stdout}\n{result.stderr}"
    report = json.loads(result.stdout)
    assert report == {"rounds": 30, "cleanup": cleanup, "gc_enabled": True,
                      "remaining_widgets": 0, "wgc_loaded": False}
