from threading import Event, get_ident
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QRectF, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.configuration.models import ConfigError
from e7auto.ui import MainWindow
from e7auto.ui.worker import AutomationWorker
from e7auto.ui.widgets import _ModuleCard
from tests.helpers import FakeLogger, make_config
from tests.helpers.qt import wait_for


@pytest.mark.parametrize("failure", ["start", "event", "snapshot", "close"])
def test_worker_log_failures_always_emit_one_terminal_result(tmp_path, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise PermissionError("simulated log failure")
    def noop(*args, **kwargs):
        pass
    logger = SimpleNamespace(event=fail if failure == "event" else noop,
                             save_stop_snapshot=fail if failure == "snapshot" else noop,
                             close=fail if failure == "close" else noop)
    monkeypatch.setattr("e7auto.ui.worker.RunLogManager", lambda *a: SimpleNamespace(
        start=fail if failure == "start" else lambda _: logger))
    monkeypatch.setattr("e7auto.ui.worker.create_production_session", fail)
    worker = AutomationWorker(make_config(), 3, False, tmp_path, None)
    results, errors = [], []
    worker.finished.connect(results.append)
    worker.failed.connect(errors.append)
    worker.run()
    assert len(results) == 1 and results[0].stop_reason == StopReason.INTERNAL_ERROR
    assert errors


def test_final_progress_survives_a_late_logging_failure(tmp_path, monkeypatch):
    final = RuntimeSnapshot.penguins("final", 7).finalized(StopReason.PENGUIN_FUNDS_COMPLETE)
    def factory(config, feature, overlay, logger, callback, control):
        def run(*args):
            callback(final)
            raise OSError("log close failed after final snapshot")
        return SimpleNamespace(run_penguins=run)
    monkeypatch.setattr("e7auto.ui.worker.RunLogManager", lambda *a: SimpleNamespace(start=lambda _: FakeLogger()))
    monkeypatch.setattr("e7auto.ui.worker.create_production_session", factory)
    worker = AutomationWorker(make_config(), 0, False, tmp_path, None, purchase_limit=7)
    results, snapshots = [], []
    worker.finished.connect(results.append)
    worker.snapshot.connect(snapshots.append)
    worker.run()
    assert results == snapshots == [final]


def test_log_creation_failure_restores_main_window(tmp_path, monkeypatch):
    def fail(*args):
        raise PermissionError("logs read-only")
    monkeypatch.setattr("e7auto.ui.preparation.load_config", lambda *a, **k: make_config())
    monkeypatch.setattr("e7auto.ui.worker.RunLogManager", lambda *a: SimpleNamespace(start=fail))
    window = MainWindow(tmp_path)
    window._start_run()
    wait_for(lambda: not window._is_busy())
    assert window._shop_feature_page.start_button.isEnabled() and window._sidebar.buttons["home"].isEnabled()
    assert "logs read-only" in window._shop_feature_page.status_label.text()
    assert window.close()


def test_preparation_is_responsive_freezes_input_and_cancels_before_run(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    calls, ticks, threads = [], [], []
    def load(*args, **kwargs):
        threads.append(get_ident())
        entered.set()
        assert release.wait(3)
        return make_config()
    monkeypatch.setattr("e7auto.ui.preparation.load_config", load)
    monkeypatch.setattr("e7auto.ui.main_window.AutomationWorker", lambda *a, **k: calls.append(True))
    window = MainWindow(tmp_path)
    window.show()
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    try:
        window._shop_feature_page.limit_input.setText("7")
        window._start_run()
        wait_for(entered.is_set)
        window._shop_feature_page.limit_input.setText("99")
        assert window._preparation_thread.request.limit == 7
        assert len(threads) == 1 and threads[0] != get_ident()
        assert window._shop_feature_page.start_button.text() == "正在准备…" and not window._shop_feature_page.start_button.isEnabled()
        previous = window._preparation_thread
        window._start_run()
        assert window._preparation_thread is previous
        QTest.qWait(50)
        assert len(ticks) >= 2
        assert not window.close()
        assert previous.cancelled.is_set()
        release.set()
        wait_for(lambda: not window._is_busy())
        assert calls == [] and not window.isVisible()
    finally:
        release.set()
        wait_for(lambda: not window._is_busy())
        timer.stop()
        window.close()


def test_preparation_failure_is_visible_and_does_not_start_worker(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("broken resource manifest")
    calls = []
    monkeypatch.setattr("e7auto.ui.preparation.load_config", fail)
    monkeypatch.setattr("e7auto.ui.main_window.AutomationWorker", lambda *a, **k: calls.append(True))
    window = MainWindow(tmp_path)
    window._start_run()
    wait_for(lambda: not window._is_busy())
    assert window._shop_feature_page.start_button.isEnabled() and calls == []
    assert "broken resource manifest" in window._shop_feature_page.status_label.text()


def test_config_rejection_remains_visible_if_its_log_cannot_be_created(tmp_path, monkeypatch):
    def fail_config(*args, **kwargs):
        raise ConfigError(["missing template"])
    def fail_log(*args):
        raise PermissionError("logs read-only")
    monkeypatch.setattr("e7auto.ui.preparation.load_config", fail_config)
    monkeypatch.setattr("e7auto.ui.preparation.RunLogManager", lambda *a: SimpleNamespace(start=fail_log))
    window = MainWindow(tmp_path)
    window._start_run()
    wait_for(lambda: not window._is_busy())
    message = window._shop_feature_page.status_label.text()
    assert "missing template" in message and "logs read-only" in message
    assert window._shop_feature_page.start_button.isEnabled()


def test_terminal_signal_does_not_enable_restart_before_thread_exit(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    final = RuntimeSnapshot.initial("finish", (), 3).finalized(StopReason.BUDGET_COMPLETE)
    class DelayedWorker(QObject):
        snapshot = Signal(object)
        finished = Signal(object)
        failed = Signal(str)
        def __init__(self, *args, **kwargs):
            super().__init__()
        @Slot()
        def run(self):
            self.finished.emit(final)
            entered.set()
            assert release.wait(3)
    monkeypatch.setattr("e7auto.ui.preparation.load_config", lambda *a, **k: make_config())
    monkeypatch.setattr("e7auto.ui.main_window.AutomationWorker", DelayedWorker)
    window = MainWindow(tmp_path)
    try:
        window._start_run()
        wait_for(entered.is_set)
        QTest.qWait(20)
        active = window._thread
        assert active.isRunning() and not window._shop_feature_page.start_button.isEnabled()
        window._start_run()
        assert window._thread is active and not window.close()
        release.set()
        wait_for(lambda: not window._is_busy())
        assert window._shop_feature_page.start_button.isEnabled()
    finally:
        release.set()
        wait_for(lambda: not window._is_busy())
        window.close()


@pytest.mark.parametrize("name", ["logs_button", "github_button"])
def test_slow_desktop_open_keeps_gui_responsive_and_close_waits(tmp_path, monkeypatch, name):
    entered, release = Event(), Event()
    threads, ticks = [], []
    def open_target(target):
        threads.append(get_ident())
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr("e7auto.ui.desktop_actions.open_default", open_target)
    window = MainWindow(tmp_path)
    page = window._settings_page
    window.show()
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    try:
        button = getattr(page, name)
        button.click()
        wait_for(entered.is_set)
        assert threads[0] != get_ident()
        before = len(ticks)
        QTest.qWait(50)
        assert len(ticks) > before and not button.isEnabled()
        assert not window.close() and page.has_pending_actions()
        release.set()
        wait_for(lambda: not page.has_pending_actions())
        assert not window.isVisible() and button.isEnabled()
    finally:
        release.set()
        wait_for(lambda: not page.has_pending_actions())
        timer.stop()
        window.close()


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_card_physical_cache_preserves_crop_and_reuses_same_dpr(monkeypatch, dpr):
    # Generated in-memory pattern, not a screenshot or a production asset edit.
    source = QImage(400, 200, QImage.Format.Format_RGB32)
    source.fill(QColor("black"))
    painter = QPainter(source)
    painter.fillRect(100, 0, 50, 200, QColor("red"))
    painter.fillRect(150, 0, 100, 200, QColor("green"))
    painter.fillRect(250, 0, 50, 200, QColor("blue"))
    painter.end()
    card = _ModuleCard(title="test", image_path=None, available=True, object_name="dprTest")
    card._pixmap = QPixmap.fromImage(source)
    monkeypatch.setattr(card, "devicePixelRatioF", lambda: dpr)
    image = QImage(round(100*dpr), round(100*dpr), QImage.Format.Format_RGB32)
    image.setDevicePixelRatio(dpr)
    painter = QPainter(image)
    card._draw_cover_pixmap(painter, QRectF(0, 0, 100, 100))
    key = card._scaled_pixmap.cacheKey()
    card._draw_cover_pixmap(painter, QRectF(0, 0, 100, 100))
    painter.end()
    assert card._scaled_pixmap.cacheKey() == key
    assert card._scaled_pixmap.size().toTuple() == (round(200*dpr), round(100*dpr))
    assert card._scaled_pixmap.devicePixelRatioF() == dpr
    for x, color in ((10, "red"), (50, "green"), (90, "blue")):
        assert image.pixelColor(round(x*dpr), round(50*dpr)) == QColor(color)
    monkeypatch.setattr(card, "devicePixelRatioF", lambda: dpr+0.5)
    painter = QPainter(image)
    card._draw_cover_pixmap(painter, QRectF(0, 0, 100, 100))
    painter.end()
    assert card._scaled_pixmap.cacheKey() != key
