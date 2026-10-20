import json
from threading import Event

import pytest
from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QScrollArea

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.ui import MainWindow
from e7auto.ui.notifications import RunEndNotifier, _REASONS, completion_message
from e7auto.ui.preferences import NotificationPreferences
from e7auto.ui.widgets import _ToggleSwitch
from tests.helpers import make_config
from tests.helpers.qt import wait_for


@pytest.mark.parametrize("reason", list(StopReason))
def test_all_terminal_reasons_are_explained_except_manual_stop(reason):
    snapshot = RuntimeSnapshot.chaos_run("run", 5).finalized(reason)
    result = completion_message(snapshot)
    if reason == StopReason.MANUAL_STOP:
        assert result is None
    else:
        assert reason in _REASONS
        assert _REASONS[reason] in result[1]
        assert "0/5" in result[1]


@pytest.mark.parametrize("reason,outcome", [
    (StopReason.BUDGET_COMPLETE, "已完成"),
    (StopReason.PENGUIN_LIMIT_COMPLETE, "已完成"),
    (StopReason.CHAOS_COMPLETE, "已完成"),
    (StopReason.REFRESH_STRATEGY_EXHAUSTED, "已停止"),
    (StopReason.PURCHASE_FUNDS_INSUFFICIENT, "已停止"),
    (StopReason.PENGUIN_FUNDS_COMPLETE, "已停止"),
    (StopReason.CAPTURE_FAILURE, "异常停止"),
    (StopReason.INTERNAL_ERROR, "异常停止"),
])
def test_notification_outcome_and_feature_progress(reason, outcome):
    snapshot = RuntimeSnapshot.penguins("run", 10).with_penguin_purchase().finalized(reason)
    title, message = completion_message(snapshot)
    assert title == f"E7auto · 红叶换企鹅{outcome}"
    assert "1/10" in message


def test_disabled_manual_nonterminal_and_duplicate_do_not_send(tmp_path, monkeypatch):
    parent = QObject()
    notifier = RunEndNotifier(tmp_path / "app.ico", parent)
    calls = []
    monkeypatch.setattr(notifier, "_send", lambda *args: calls.append(args))
    live = RuntimeSnapshot.initial("run", (), 3)
    notifier.notify(live, enabled=True)
    notifier.notify(live.finalized(StopReason.MANUAL_STOP), enabled=True)
    disabled = RuntimeSnapshot.initial("disabled", (), 3).finalized(StopReason.BUDGET_COMPLETE)
    notifier.notify(disabled, enabled=False)
    notifier.notify(disabled, enabled=True)  # Enabling cannot replay an old completion.
    assert calls == []
    final = RuntimeSnapshot.initial("next", (), 3).finalized(StopReason.BUDGET_COMPLETE)
    notifier.notify(final, enabled=True)
    notifier.notify(final, enabled=True)
    assert len(calls) == 1


@pytest.mark.parametrize("raw", [None, "broken", "[]", '{"run_end_notifications": "false"}',
                                 '{"run_end_notifications": 0}'])
def test_preferences_missing_or_invalid_default_to_enabled(tmp_path, raw):
    path = tmp_path / "preferences.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    assert NotificationPreferences(path).load() is True


def test_settings_switch_persists_and_matches_feature_switch(tmp_path, qt_application):
    window = MainWindow(tmp_path)
    window._show_section("settings")
    window.resize(window.minimumSize())
    window.show()
    page = window._settings_page
    toggle = page.notifications_toggle
    assert isinstance(toggle, _ToggleSwitch)
    assert toggle.size() == window._shop_feature_page.friendship_toggle.size()
    assert toggle.isChecked() and toggle.toolTip() == ""
    assert toggle.accessibleName() == "运行结束通知"
    assert page.findChild(QLabel, "currentVersion") is not None
    scroll = page.findChild(QScrollArea)
    scroll.ensureWidgetVisible(toggle)
    qt_application.processEvents()
    assert scroll.horizontalScrollBar().maximum() == 0
    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert not toggle.isChecked()
    saved = tmp_path / "state/ui_preferences.json"
    assert json.loads(saved.read_text(encoding="utf-8"))["run_end_notifications"] is False
    assert not MainWindow(tmp_path)._settings_page.notifications_enabled
    QTest.keyClick(toggle, Qt.Key.Key_Space)
    assert toggle.isChecked()
    assert MainWindow(tmp_path)._settings_page.notifications_enabled


def test_preferences_preserve_other_keys_and_failed_save_reverts_switch(tmp_path, monkeypatch):
    state = tmp_path / "state/ui_preferences.json"
    state.parent.mkdir()
    state.write_text('{"future_setting":42}', encoding="utf-8")
    preferences = NotificationPreferences(state)
    preferences.save(False)
    assert json.loads(state.read_text(encoding="utf-8"))["future_setting"] == 42
    window = MainWindow(tmp_path)
    calls = []
    window._settings_page.notifications_changed.connect(calls.append)
    def fail(enabled):
        raise PermissionError("read only")
    monkeypatch.setattr(window._settings_page._preferences, "save", fail)
    window._settings_page.notifications_toggle.click()
    assert not window._settings_page.notifications_enabled
    assert "无法保存" in window._settings_page.status_label.text()
    assert not calls


@pytest.mark.parametrize("reason,enabled,expected", [
    (StopReason.BUDGET_COMPLETE, True, 1),
    (StopReason.PURCHASE_FUNDS_INSUFFICIENT, True, 1),
    (StopReason.RECOGNITION_TIMEOUT, True, 1),
    (StopReason.MANUAL_STOP, True, 0),
    (StopReason.BUDGET_COMPLETE, False, 0),
])
def test_main_window_notifies_once_after_thread_exit(tmp_path, monkeypatch, reason, enabled, expected):
    entered, release = Event(), Event()
    final = RuntimeSnapshot.initial("run", (), 3).finalized(reason)
    class DelayedWorker(QObject):
        snapshot = Signal(object)
        finished = Signal(object)
        failed = Signal(str)
        def __init__(self, *args, **kwargs):
            super().__init__()
        @Slot()
        def run(self):
            self.finished.emit(final)
            self.finished.emit(final)
            entered.set()
            assert release.wait(3)
    monkeypatch.setattr("e7auto.ui.preparation.load_config", lambda *a, **k: make_config())
    monkeypatch.setattr("e7auto.ui.main_window.AutomationWorker", DelayedWorker)
    window = MainWindow(tmp_path)
    window._settings_page.notifications_toggle.setChecked(enabled)
    calls = []
    def send(*args):
        assert window._thread is None
        assert window._shop_feature_page.start_button.isEnabled()
        calls.append(args)
    monkeypatch.setattr(window._run_notifier, "_send", send)
    try:
        window._start_run()
        wait_for(entered.is_set)
        QTest.qWait(20)
        assert window._is_busy() and not calls
        release.set()
        wait_for(lambda: not window._is_busy())
        window._on_thread_stopped()
        assert len(calls) == expected
        assert window._pending_run_result is None
    finally:
        release.set()
        wait_for(lambda: not window._is_busy())
        window.close()


def test_delivery_failure_never_blocks_run_cleanup(tmp_path, monkeypatch, caplog):
    window = MainWindow(tmp_path)
    def fail(*args):
        raise OSError("shell unavailable")
    monkeypatch.setattr(window._run_notifier, "_send", fail)
    window._run_notifier.notify(RuntimeSnapshot.chaos_run("run", 1).finalized(StopReason.CHAOS_COMPLETE), enabled=True)
    assert "通知发送失败" in caplog.text
    assert window.close()


def test_startup_failure_notifies_but_preparation_cancel_does_not(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("broken configuration")
    monkeypatch.setattr("e7auto.ui.preparation.load_config", fail)
    window = MainWindow(tmp_path)
    calls = []
    monkeypatch.setattr(window._run_notifier, "_send", lambda *a: calls.append(a))
    window._start_run()
    wait_for(lambda: not window._is_busy())
    assert len(calls) == 1 and "启动失败" in calls[0][0]
    window._start_run()
    window._request_stop()
    wait_for(lambda: not window._is_busy())
    assert len(calls) == 1


def test_disabling_and_closing_do_not_remove_sent_toasts(tmp_path, monkeypatch):
    window = MainWindow(tmp_path)
    history = []
    class Backend:
        def show(self, title, message):
            history.append((title, message))
    window._run_notifier._native = Backend()
    monkeypatch.setattr("e7auto.ui.notifications.QApplication.platformName", lambda: "windows")
    final = RuntimeSnapshot.chaos_run("run", 1).finalized(StopReason.CHAOS_COMPLETE)
    window._run_notifier.notify(final, enabled=True)
    window._settings_page.notifications_toggle.click()
    window.close()
    assert len(history) == 1
