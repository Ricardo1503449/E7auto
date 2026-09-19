from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QAbstractButton, QLineEdit
import pytest

from e7auto.ui import MainWindow, StatsOverlay
from tests.helpers import FakeLogger, make_config
import e7auto.ui.main_window as main_window_module
import e7auto.ui.worker as worker_module
from e7auto.core.domain import RuntimeSnapshot, StopReason


@pytest.mark.parametrize("penguins", [False, True])
@pytest.mark.parametrize("continuous_refresh", [False, True])
def test_selected_limit_is_handed_to_worker_as_an_integer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    penguins: bool,
    continuous_refresh: bool,
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
            *,
            purchase_limit: int | None = None,
            continuous_refresh: bool = False,
        ) -> None:
            captured["refresh_limit"] = refresh_limit
            captured["buy_friendship_points"] = buy_friendship_points
            captured["purchase_limit"] = purchase_limit
            captured["continuous_refresh"] = continuous_refresh
            captured["worker_count"] = captured.get("worker_count", 0) + 1
            self.snapshot = FakeSignal()
            self.finished = FakeSignal()

        def moveToThread(self, thread: object) -> None:
            captured["worker_thread"] = thread

        def run(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

    def fake_load_config(_path, *, template_profile):
        captured["template_profile"] = template_profile
        return make_config()

    monkeypatch.setattr(main_window_module, "load_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "with_penguin_config", lambda config: config)
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
        window._continuous_refresh.setChecked(continuous_refresh)
        if penguins:
            window._penguin_feature_page.limit_input.setText("7")
            window._start_penguin_run()
        else:
            window._start_run()

        assert captured["refresh_limit"] == (0 if penguins else 123)
        assert captured["buy_friendship_points"] is (not penguins)
        assert captured["purchase_limit"] == (7 if penguins else None)
        assert captured["continuous_refresh"] is (continuous_refresh and not penguins)
        assert not window._continuous_refresh.isEnabled()
        assert captured["template_profile"] == ("penguin" if penguins else "shop")
        assert isinstance(captured["refresh_limit"], int)
        assert captured["thread_started"] is True
        assert not window._penguin_feature_page.isEnabled()
        assert not window._start.isEnabled()
        window._start_run()
        window._start_penguin_run()
        assert captured["worker_count"] == 1
        window._on_finished(RuntimeSnapshot.initial("finished", (), 123).finalized(StopReason.MANUAL_F5))
        assert window._continuous_refresh.isEnabled()
        assert window._continuous_refresh.isChecked() is continuous_refresh
    finally:
        window._thread = None
        window._worker = None
        window.close()
        application.processEvents()


@pytest.mark.parametrize("continuous_refresh", [False, True])
@pytest.mark.parametrize("penguins", [False, True])
def test_worker_passes_refresh_mode_only_to_shop(tmp_path, monkeypatch, continuous_refresh, penguins):
    captured = {}
    final = RuntimeSnapshot.initial("worker", (), 6).finalized(StopReason.BUDGET_COMPLETE)

    def run_shop(limit, run_id, *, enabled_optional_target_ids, continuous_refresh=False):
        captured.update(feature="shop", limit=limit, optional=enabled_optional_target_ids,
                        continuous_refresh=continuous_refresh)
        return final

    def run_penguins(limit, run_id):
        captured.update(feature="penguin", limit=limit)
        return final

    session = SimpleNamespace(run=run_shop, run_penguins=run_penguins)
    monkeypatch.setattr(worker_module, "RunLogManager", lambda *args: SimpleNamespace(start=lambda run_id: FakeLogger()))
    monkeypatch.setattr(worker_module, "create_production_session", lambda *args: session)
    worker = worker_module.AutomationWorker(
        make_config(), 6, True, tmp_path, None,
        purchase_limit=2 if penguins else None, continuous_refresh=continuous_refresh,
    )
    finished = []
    worker.finished.connect(finished.append)
    worker.run()
    assert finished == [final]
    assert captured == ({"feature": "penguin", "limit": 2} if penguins else {
        "feature": "shop", "limit": 6, "optional": frozenset({"friendship_points"}),
        "continuous_refresh": continuous_refresh,
    })
