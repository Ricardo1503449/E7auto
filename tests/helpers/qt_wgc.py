"""Bounded Qt/real-WinRT coexistence probe; never captures a game or desktop."""
from __future__ import annotations

import argparse
import ctypes
import gc
import json
from pathlib import Path
import queue
import sys
import time


def probe(order: str, root: Path, rounds: int) -> dict:
    assert gc.isenabled()
    if order == "wgc-first":
        import e7auto.platform.wgc_capture as wgc
        assert "PySide6" not in sys.modules  # Loading the CRT must not preload the GUI.
    from PySide6.QtCore import QCoreApplication, QEvent, QThread
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    if order == "qt-first":
        import e7auto.platform.wgc_capture as wgc
    from e7auto.platform.native_runtime import ensure_msvc_runtime
    from e7auto.ui import MainWindow
    from tests.helpers.config import make_config
    from tests.helpers.qt import dispose_widgets

    requests = queue.Queue()
    responses = queue.Queue()

    class NativeWorker(QThread):
        def run(self):
            initialized = False
            bitmap = None
            try:
                wgc.init_apartment(wgc.ApartmentType.MULTI_THREADED)
                initialized = True
                while True:
                    item = requests.get(timeout=10)
                    if item is None:
                        break
                    # Allocate/read/dispose a genuine WinRT object on the worker.
                    # There is no HWND, capture session, real input or screenshot.
                    with wgc.SoftwareBitmap(wgc.BitmapPixelFormat.BGRA8, 4, 3) as bitmap:
                        pixels = wgc._copy_software_bitmap(bitmap)
                        assert pixels.shape == (3, 4, 4) and pixels.flags.c_contiguous
                    bitmap = None
                    responses.put((item, None))
            except Exception as exc:
                responses.put((-1, repr(exc)))
            finally:
                bitmap = None  # Release the projection before uninitializing its apartment.
                if initialized:
                    wgc.uninit_apartment()

    worker = NativeWorker()
    worker.start()
    config = make_config()
    try:
        for index in range(rounds):
            window = MainWindow(root)
            overlay = window._overlay
            try:
                window.show()
                overlay.configure(config)
                overlay.show()
                requests.put(index)
                deadline = time.monotonic() + 10
                while True:
                    application.processEvents()
                    try:
                        completed, error = responses.get(timeout=0.002)
                    except queue.Empty:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("WinRT worker did not finish")
                    else:
                        assert error is None, error
                        assert completed == index
                        break
                overlay.configure_penguins(config)
                application.processEvents()
            finally:
                dispose_widgets(application, [window, overlay])
            del window, overlay
            gc.collect()
    finally:
        requests.put(None)
        assert worker.wait(15000), "WinRT worker did not uninitialize and exit"
        worker.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()
        assert not isValid(worker)
    assert not application.topLevelWidgets()
    return {"order": order, "rounds": rounds, "runtime": ensure_msvc_runtime(),
            "worker_exited": True, "gc_enabled": gc.isenabled(), "remaining_windows": 0}


if __name__ == "__main__":
    ctypes.windll.kernel32.SetErrorMode(3)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--order", choices=["wgc-first", "qt-first"], required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(probe(args.order, args.root, args.rounds)))
