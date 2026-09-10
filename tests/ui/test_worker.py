from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication, QAbstractButton, QLineEdit
import pytest

from e7auto.ui import MainWindow, StatsOverlay
from tests.helpers import make_config
import e7auto.ui.main_window as main_window_module


def test_refresh_limit_is_handed_to_worker_as_an_integer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = QApplication.instance() or QApplication([])
    captured: dict[str, object] = {}

    class FakeSignal:
        def __init__(self) -> None:
            self.slots: list[object] = []

        def connect(self, slot: object) -> None:
            self.slots.append(slot)

    class FakeThread:
        def __init__(self, parent: object) -> None:
            self.parent = parent
            self.started = FakeSignal()
            self.finished = FakeSignal()

        def start(self) -> None:
            captured["thread_started"] = True

        def quit(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

    class FakeWorker:
        def __init__(
            self,
            config: object,
            refresh_limit: int,
            buy_friendship_points: bool,
            project_root: Path,
            overlay: StatsOverlay,
        ) -> None:
            captured["refresh_limit"] = refresh_limit
            captured["buy_friendship_points"] = buy_friendship_points
            self.snapshot = FakeSignal()
            self.finished = FakeSignal()

        def moveToThread(self, thread: object) -> None:
            captured["worker_thread"] = thread

        def run(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

    monkeypatch.setattr(main_window_module, "load_config", lambda _path: make_config())
    monkeypatch.setattr(main_window_module, "QThread", FakeThread)
    monkeypatch.setattr(main_window_module, "AutomationWorker", FakeWorker)

    window = MainWindow(tmp_path)
    limit_input = window.findChild(QLineEdit, "refreshLimitInput")
    toggle = window.findChild(QAbstractButton, "friendshipPointsToggle")
    try:
        assert limit_input is not None
        assert toggle is not None
        limit_input.setText("123")
        toggle.setChecked(True)
        window._start_run()

        assert captured["refresh_limit"] == 123
        assert captured["buy_friendship_points"] is True
        assert isinstance(captured["refresh_limit"], int)
        assert captured["thread_started"] is True
    finally:
        window._thread = None
        window._worker = None
        window.close()
        application.processEvents()
