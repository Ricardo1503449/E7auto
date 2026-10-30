import json

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea, QWidget

from e7auto.ui import MainWindow
from e7auto.ui.announcements import ANNOUNCEMENT_ID, ANNOUNCEMENT_MESSAGE, QQ_GROUP_TEXT
from e7auto.ui.preferences import AnnouncementPreferences, NotificationPreferences, ShopStatisticsPreferences
from tests.helpers.qt import wait_for


@pytest.fixture(autouse=True)
def enable_announcements(monkeypatch, no_startup_announcement):
    monkeypatch.setattr("e7auto.ui.announcements.AnnouncementController.start", no_startup_announcement)


def show_announcement(window, application):
    window.show()
    wait_for(lambda: window._announcement_controller._dialog is not None)
    application.processEvents()
    return window._announcement_controller._dialog


@pytest.mark.parametrize("dismiss", ["button", "escape"])
def test_first_show_is_deferred_and_dismissal_persists_across_launches(tmp_path, qt_application, dismiss):
    state = tmp_path / "state/ui_preferences.json"
    window = MainWindow(tmp_path)
    original_page = window._pages.currentWidget()
    window.show()
    assert window._announcement_controller._dialog is None
    wait_for(lambda: window._announcement_controller._dialog is not None)
    dialog = window._announcement_controller._dialog
    assert dialog.isVisible() and not dialog.isModal()
    assert not state.exists()
    assert [button.text() for button in dialog.findChildren(QPushButton)] == ["我知道了"]
    assert dialog.windowTitle() == "公告" and dialog.message.text() == ANNOUNCEMENT_MESSAGE
    assert window._dialog_backdrop.isVisible()
    if dismiss == "button":
        QTest.mouseClick(dialog.confirm_button, Qt.MouseButton.LeftButton)
    else:
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert window._announcement_controller._dialog is None
    assert not window._dialog_backdrop.isVisible()
    assert window._pages.currentWidget() is original_page
    assert AnnouncementPreferences(state).load() == ANNOUNCEMENT_ID
    window.close()

    restored = MainWindow(tmp_path)
    restored.show()
    qt_application.processEvents()
    assert restored._announcement_controller._dialog is None
    assert restored.isVisible() and restored.isEnabled()
    restored.close()


@pytest.mark.parametrize("raw", [None, b"broken", b"\xff", b"[]", b"{}",
                                  b'{"announcement_seen_id": 7}', b'{"announcement_seen_id": null}'])
def test_missing_or_invalid_read_receipt_is_unread(tmp_path, qt_application, raw):
    state = tmp_path / "state/ui_preferences.json"
    if raw is not None:
        state.parent.mkdir()
        state.write_bytes(raw)
    window = MainWindow(tmp_path)
    dialog = show_announcement(window, qt_application)
    assert dialog.isVisible()
    dialog.confirm_button.click()
    assert AnnouncementPreferences(state).load() == ANNOUNCEMENT_ID
    window.close()


def test_only_a_new_announcement_revision_prompts_again(tmp_path, qt_application, monkeypatch):
    state = tmp_path / "state/ui_preferences.json"
    AnnouncementPreferences(state).save(ANNOUNCEMENT_ID)
    monkeypatch.setattr("e7auto.__version__", "99.0.0")
    unchanged = MainWindow(tmp_path)
    unchanged.show()
    qt_application.processEvents()
    assert unchanged._announcement_controller._dialog is None
    unchanged.close()

    monkeypatch.setattr("e7auto.ui.announcements.ANNOUNCEMENT_ID", "usage-guide-v2")
    revised = MainWindow(tmp_path)
    dialog = show_announcement(revised, qt_application)
    dialog.confirm_button.click()
    assert AnnouncementPreferences(state).load() == "usage-guide-v2"
    revised.close()


def test_shared_preferences_preserve_changes_made_before_and_after_dismissal(tmp_path, qt_application):
    state = tmp_path / "state/ui_preferences.json"
    state.parent.mkdir()
    state.write_text('{"future_setting":42}', encoding="utf-8")
    window = MainWindow(tmp_path)
    dialog = show_announcement(window, qt_application)
    NotificationPreferences(state).save(False)
    ShopStatisticsPreferences(state).save(False)
    dialog.confirm_button.click()
    expected = {"future_setting": 42, "run_end_notifications": False, "shop_statistics": False,
                "shop_statistics_generation": 1, "announcement_seen_id": ANNOUNCEMENT_ID}
    assert json.loads(state.read_text(encoding="utf-8")) == expected
    NotificationPreferences(state).save(True)
    ShopStatisticsPreferences(state).save(True)
    expected.update(run_end_notifications=True, shop_statistics=True)
    assert json.loads(state.read_text(encoding="utf-8")) == expected
    window.close()


def test_failed_save_allows_close_and_only_a_later_launch_can_prompt_again(tmp_path, qt_application, monkeypatch):
    state = tmp_path / "state/ui_preferences.json"
    window = MainWindow(tmp_path)
    dialog = show_announcement(window, qt_application)

    def fail(_announcement_id):
        raise PermissionError("read only")

    monkeypatch.setattr(window._announcement_controller._preferences, "save", fail)
    dialog.confirm_button.click()
    assert window._announcement_controller._dialog is None and not state.exists()
    window.hide()
    window.show()
    window._show_section("about")
    qt_application.processEvents()
    assert window._announcement_controller._dialog is None
    assert window._pages.currentWidget() is window._about_page
    window.close()

    restored = MainWindow(tmp_path)
    assert show_announcement(restored, qt_application).isVisible()
    restored.close()


def test_restore_and_repeated_show_do_not_replace_or_reopen_notice(tmp_path, qt_application):
    window = MainWindow(tmp_path)
    dialog = show_announcement(window, qt_application)
    window.show()
    qt_application.processEvents()
    assert window._announcement_controller._dialog is dialog
    dialog.confirm_button.click()
    for show in (window.showMinimized, window.showNormal, window.show, window.hide, window.show):
        show()
        qt_application.processEvents()
        assert window._announcement_controller._dialog is None
    for section in ("about", "settings", "home"):
        window._show_section(section)
        qt_application.processEvents()
        assert window._announcement_controller._dialog is None
    window.close()


@pytest.mark.parametrize("before_queued_show", [True, False])
def test_main_window_close_cancels_notice_without_acknowledging(tmp_path, qt_application, before_queued_show):
    state = tmp_path / "state/ui_preferences.json"
    window = MainWindow(tmp_path)
    window.show()
    if not before_queued_show:
        wait_for(lambda: window._announcement_controller._dialog is not None)
    assert not state.exists()
    assert window.close()
    qt_application.processEvents()
    assert window._announcement_controller._dialog is None
    assert not window._announcement_controller._timer.isActive()
    assert not state.exists()
    assert not window.isVisible()

    restored = MainWindow(tmp_path)
    assert show_announcement(restored, qt_application).isVisible()
    restored.close()


@pytest.mark.parametrize("key,backwards", [(Qt.Key.Key_Return, False), (Qt.Key.Key_Enter, True),
                                           (Qt.Key.Key_Space, False)])
def test_dialog_starts_on_container_and_keyboard_can_reach_confirm(tmp_path, qt_application, key, backwards):
    window = MainWindow(tmp_path)
    dialog = show_announcement(window, qt_application)
    assert QApplication.focusWidget() is dialog
    assert not dialog.confirm_button.isDefault() and not dialog.confirm_button.autoDefault()
    for initial_key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        QTest.keyClick(dialog, initial_key)
        assert dialog.isVisible()
    for _ in range(6):
        if dialog.confirm_button.hasFocus():
            break
        QTest.keyClick(QApplication.focusWidget() or dialog, Qt.Key.Key_Tab,
                       Qt.KeyboardModifier.ShiftModifier if backwards else Qt.KeyboardModifier.NoModifier)
    assert dialog.confirm_button.hasFocus()
    QTest.keyClick(dialog.confirm_button, key)
    assert window._announcement_controller._dialog is None
    assert AnnouncementPreferences(tmp_path / "state/ui_preferences.json").load() == ANNOUNCEMENT_ID
    window.close()


@pytest.mark.parametrize("location", ["announcement", "about"])
def test_notice_and_about_support_selecting_and_copying_text(tmp_path, qt_application, location):
    window = MainWindow(tmp_path)
    dialog = show_announcement(window, qt_application)
    if location == "announcement":
        label = dialog.message
    else:
        dialog.confirm_button.click()
        window._show_section("about")
        qt_application.processEvents()
        label = window._about_page.findChild(QLabel, "aboutQQGroup")
        assert label.text() == QQ_GROUP_TEXT
        description = window._about_page.findChild(QLabel, "aboutDescription")
        assert label.geometry().top() > description.geometry().bottom()
    assert label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    clipboard = qt_application.clipboard()
    previous_text = clipboard.text()
    try:
        label.setFocus()
        label.setSelection(label.text().index("966214752"), len("966214752"))
        QTest.keyClick(label, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert label.selectedText() == clipboard.text() == "966214752"
        if location == "announcement":
            QTest.keyClick(label, Qt.Key.Key_Return)
            assert dialog.isVisible()
    finally:
        clipboard.setText(previous_text)
    window.close()


@pytest.mark.parametrize("size", [(760, 560), (1180, 760)])
def test_notice_and_about_fit_window_and_text_is_not_clipped(tmp_path, qt_application, size):
    window = MainWindow(tmp_path)
    window.resize(*size)
    dialog = show_announcement(window, qt_application)
    window_bounds = QRect(window.mapToGlobal(QPoint()), window.size())
    assert window_bounds.contains(dialog.geometry())
    assert (dialog.geometry().center() - window_bounds.center()).manhattanLength() <= 2
    message_bounds = dialog.message.fontMetrics().boundingRect(
        QRect(0, 0, dialog.message.width(), 10000), Qt.TextFlag.TextWordWrap, dialog.message.text()
    )
    assert dialog.message.height() >= message_bounds.height()
    assert dialog.message.font().hintingPreference() == QFont.HintingPreference.PreferNoHinting
    assert dialog.confirm_button.font().hintingPreference() == QFont.HintingPreference.PreferNoHinting
    assert all(not widget.toolTip() for widget in dialog.findChildren(QWidget))
    dialog.confirm_button.click()
    window._show_section("about")
    scroll = window._about_page.findChild(QScrollArea)
    group = window._about_page.findChild(QLabel, "aboutQQGroup")
    scroll.ensureWidgetVisible(group)
    qt_application.processEvents()
    assert scroll.viewport().rect().contains(group.mapTo(scroll.viewport(), group.rect().center()))
    assert scroll.horizontalScrollBar().maximum() == 0
    bounds = group.fontMetrics().boundingRect(QRect(0, 0, group.width(), 10000),
                                             Qt.TextFlag.TextWordWrap, group.text())
    assert group.height() >= bounds.height()
    window.close()
