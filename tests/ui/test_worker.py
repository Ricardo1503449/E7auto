from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication
import pytest

from e7auto.ui import MainWindow, StatsOverlay
from tests.helpers import FakeLogger, make_config
import e7auto.ui.worker as worker_module
from e7auto.core.domain import RuntimeSnapshot, StopReason


@pytest.mark.parametrize("feature", ["shop", "penguin", "chaos"])
@pytest.mark.parametrize("before_setup", [False, True])
def test_overlay_stop_reaches_busy_worker_and_survives_setup(tmp_path, monkeypatch, feature, before_setup):
    import threading
    from PySide6.QtCore import QThread
    from PySide6.QtTest import QTest
    from e7auto.runtime.stop_control import StopExecution

    app = QApplication.instance() or QApplication([])
    entered = threading.Event()
    release = threading.Event()
    controls = []
    finals = []
    initial = {"shop": RuntimeSnapshot.initial("stop", (), 3),
               "penguin": RuntimeSnapshot.penguins("stop", 2),
               "chaos": RuntimeSnapshot.chaos_run("stop", 2)}[feature]

    def factory(config, feature_id, overlay, logger, callback, control):
        controls.append(control)
        def run(*args, **kwargs):
            entered.set()
            assert release.wait(3)
            try:
                control.checkpoint()
            except StopExecution as exc:
                final = initial.finalized(exc.reason)
                callback(final)
                return final
            raise AssertionError("stop request was lost")
        return SimpleNamespace(run=run, run_penguins=run, run_chaos=run)

    monkeypatch.setattr(worker_module, "create_production_session", factory)
    monkeypatch.setattr(worker_module, "RunLogManager", lambda *args: SimpleNamespace(start=lambda _: FakeLogger()))
    window = MainWindow(tmp_path)
    worker = worker_module.AutomationWorker(
        make_config(), 3, False, tmp_path, window._overlay,
        purchase_limit=2 if feature == "penguin" else None,
        round_limit=2 if feature == "chaos" else None,
    )
    thread = QThread()
    window._worker = worker
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(finals.append)
    worker.snapshot.connect(window._on_snapshot)
    thread.finished.connect(worker.deleteLater)
    configure = {"shop": window._overlay.configure, "penguin": window._overlay.configure_penguins,
                 "chaos": window._overlay.configure_chaos}[feature]
    configure(make_config())
    window._overlay.show()
    try:
        if before_setup:
            window._overlay._stop_button.click()
        thread.start()
        assert entered.wait(3)
        if not before_setup:
            window._overlay._stop_button.click()
        # run() is still blocked: a queued worker slot could not have handled this.
        assert controls[0].reason is StopReason.MANUAL_STOP
        release.set()
        for _ in range(100):
            app.processEvents()
            if finals:
                break
            QTest.qWait(10)
        assert len(finals) == 1 and finals[0].stop_reason is StopReason.MANUAL_STOP
    finally:
        release.set()
        thread.quit()
        thread.wait(3000)
        window._worker = None
        window.close()
        app.processEvents()


@pytest.mark.parametrize("penguins", [False, True])
@pytest.mark.parametrize("continuous_refresh", [False, True])
def test_selected_limit_is_handed_to_worker_as_an_integer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    penguins: bool,
    continuous_refresh: bool,
) -> None:
    import threading
    from tests.helpers.qt import wait_for
    captured = {}
    entered, release = threading.Event(), threading.Event()
    config = make_config()
    def fake_load_config(_path, *, template_profile):
        captured["template_profile"] = template_profile
        return config
    def factory(config, feature, overlay, logger, callback, control):
        captured["feature"] = feature
        def run(limit, run_id, **kwargs):
            captured.update(limit=limit, **kwargs)
            entered.set()
            assert release.wait(3)
            return RuntimeSnapshot.initial(run_id, (), limit).finalized(StopReason.MANUAL_STOP)
        return SimpleNamespace(run=run, run_penguins=run)
    monkeypatch.setattr("e7auto.ui.preparation.load_config", fake_load_config)
    monkeypatch.setattr("e7auto.ui.preparation.with_penguin_config", lambda config: config)
    monkeypatch.setattr(worker_module, "RunLogManager", lambda *a: SimpleNamespace(start=lambda _: FakeLogger()))
    monkeypatch.setattr(worker_module, "create_production_session", factory)
    window = MainWindow(tmp_path)
    try:
        window._shop_feature_page.limit_input.setText("123")
        window._shop_feature_page.friendship_toggle.setChecked(True)
        window._shop_feature_page.continuous_refresh_toggle.setChecked(continuous_refresh)
        window._penguin_feature_page.limit_input.setText("7")
        (window._start_penguin_run if penguins else window._start_run)()
        wait_for(entered.is_set)
        assert captured["limit"] == (7 if penguins else 123)
        assert captured["feature"] == captured["template_profile"] == ("penguin" if penguins else "shop")
        assert isinstance(captured["limit"], int)
        if not penguins:
            assert captured["continuous_refresh"] is continuous_refresh
            assert captured["enabled_optional_target_ids"] == frozenset({"friendship_points"})
        assert not window._shop_feature_page.continuous_refresh_toggle.isEnabled()
        assert not window._penguin_feature_page.isEnabled()
        assert not window._shop_feature_page.start_button.isEnabled()
        thread = window._thread
        window._start_run()
        window._start_penguin_run()
        assert window._thread is thread
        release.set()
        wait_for(lambda: not window._is_busy())
        assert window._shop_feature_page.continuous_refresh_toggle.isEnabled()
        assert window._shop_feature_page.continuous_refresh_toggle.isChecked() is continuous_refresh
    finally:
        release.set()
        wait_for(lambda: not window._is_busy())
        window.close()


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
