"""Qt-only test lifecycle helpers; never import these from the common helper facade."""
from __future__ import annotations

import gc
from pathlib import Path
import weakref

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


def dispose_widgets(application: QApplication, widgets: list[QWidget]) -> None:
    """Destroy test-owned native widgets before Python collects their wrappers."""
    for widget in widgets:
        if not isValid(widget):
            continue
        for timer in widget.findChildren(QTimer):
            timer.stop()
        assert widget.close(), "A test left a widget refusing to close (possibly a running worker)"
    # Finish queued UI callbacks while their receivers still exist.
    application.processEvents()
    for widget in widgets:
        if isValid(widget):
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()
    assert all(not isValid(widget) for widget in widgets), "Qt widget survived deferred deletion"


def lifecycle_probe(root: Path, rounds: int, cleanup: str) -> dict:
    """Exercise actual Qt ownership in a child process, with normal cyclic GC enabled."""
    import sys
    from e7auto.core.domain import RuntimeSnapshot
    from e7auto.ui import MainWindow
    from tests.helpers.config import make_config

    assert gc.isenabled(), "Lifecycle regression must not disable cyclic garbage collection"
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    config = make_config()

    def one_round(index):
        window = MainWindow(root)
        overlay = window._overlay
        refs = [weakref.ref(window), weakref.ref(overlay)]
        window.show()
        window._show_shop_page()
        overlay.configure(config)
        overlay.update_snapshot(RuntimeSnapshot.initial(str(index), (), 0))
        overlay.show()
        application.processEvents()
        overlay._collapse_overlay()
        application.processEvents()
        window._show_function_center()
        window._show_module_page("penguin_exchange")
        overlay.configure_penguins(config)
        overlay.update_snapshot(RuntimeSnapshot.penguins(str(index), 0))
        overlay.start_elapsed_timer()
        overlay.show()
        application.processEvents()
        overlay.stop_elapsed_timer()
        window.close()
        application.processEvents()
        if cleanup == "explicit":
            dispose_widgets(application, [overlay, window])
        return refs

    for index in range(rounds):
        references = one_round(index)
        application.processEvents()
        if cleanup == "explicit":
            gc.collect()
        assert all(reference() is None for reference in references), f"Window retained after round {index}"
        assert not application.topLevelWidgets(), f"Native windows retained after round {index}"
    wgc_loaded = any(name == "winrt" or name.startswith("winrt.")
                     or name == "e7auto.platform.wgc_capture" for name in sys.modules)
    assert not wgc_loaded
    return {"rounds": rounds, "cleanup": cleanup, "gc_enabled": gc.isenabled(),
            "remaining_widgets": len(application.topLevelWidgets()), "wgc_loaded": wgc_loaded}


if __name__ == "__main__":
    import argparse
    import ctypes
    import json
    import os

    if os.name == "nt":
        ctypes.windll.kernel32.SetErrorMode(3)  # Do not leave a modal crash dialog in a test process.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--cleanup", choices=["close", "explicit"], required=True)
    args = parser.parse_args()
    print(json.dumps(lifecycle_probe(args.root, args.rounds, args.cleanup)))
