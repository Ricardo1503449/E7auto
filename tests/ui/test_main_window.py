from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QWidget,
)
import pytest

from e7auto.ui import MainWindow


@pytest.mark.parametrize("maximized", [False, True])
def test_windows_native_minimize_restore_with_frameless_chrome(tmp_path, qt_application, maximized):
    if sys.platform != "win32" or qt_application.platformName() != "windows":
        pytest.skip("requires the native Windows Qt platform")
    import win32con
    import win32gui

    window = MainWindow(tmp_path)
    try:
        initial_size = window.size()
        window.show()
        qt_application.processEvents()
        hwnd = int(window.winId())
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        assert style & win32con.WS_MINIMIZEBOX
        assert style & win32con.WS_MAXIMIZEBOX
        assert style & win32con.WS_SYSMENU
        assert style & win32con.WS_CAPTION
        assert style & win32con.WS_THICKFRAME
        assert not win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_LAYERED
        assert window.size() == initial_size
        if maximized:
            window.showMaximized()
            qt_application.processEvents()
        win32gui.SendMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_MINIMIZE, 0)
        qt_application.processEvents()
        assert win32gui.IsIconic(hwnd) and window.isMinimized()
        win32gui.SendMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_RESTORE, 0)
        qt_application.processEvents()
        assert not win32gui.IsIconic(hwnd) and not window.isMinimized()
        assert window.isMaximized() is maximized
        assert window._title_bar.height() == 50
        assert win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & win32con.WS_THICKFRAME
    finally:
        window.close()


def test_main_window_has_approved_shop_controls_and_resizable_shell(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    try:
        assert window.findChildren(QSpinBox) == []
        limit_labels = [
            label
            for label in window.findChildren(QLabel)
            if label.text() == "天空石消耗上限"
        ]
        assert len(limit_labels) == 1
        limit_inputs = window.findChildren(QLineEdit, "refreshLimitInput")
        assert len(limit_inputs) == 1
        assert limit_inputs[0].text() == "0"
        assert limit_inputs[0].alignment() & Qt.AlignmentFlag.AlignRight
        window._show_module_page("shop_refresh")
        window.show()
        application.processEvents()
        assert limit_labels[0].geometry().right() < limit_inputs[0].geometry().left()
        assert (
            limit_inputs[0].mapTo(window, limit_inputs[0].rect().center()).x()
            > window.width() // 2
        )
        toggle = window.findChild(QAbstractButton, "friendshipPointsToggle")
        assert toggle is not None
        assert toggle.size().width() == 58
        assert toggle.size().height() == 32
        assert not toggle.isChecked()
        start = window.findChild(QPushButton, "startButton")
        assert start is not None
        assert start.text() == "启动脚本"
        assert window.minimumSize() == window._MINIMUM_SIZE
        assert window.size().width() > window.minimumWidth()
        assert window.size().height() > window.minimumHeight()
        assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is (window._native_frame is None)
        assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert window.windowFlags() & Qt.WindowType.WindowMinimizeButtonHint
        assert window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
        assert len(window.findChildren(QWidget, "resizeHandle")) == 8
        title_bar_icon = window.findChild(QLabel, "titleBarIcon")
        title_bar_text = window.findChild(QLabel, "windowTitle")
        assert window._title_bar.height() == 50
        assert title_bar_icon is not None
        assert title_bar_icon.size() == QSize(32, 32)
        assert title_bar_text is not None
        assert title_bar_text.text() == "E7auto"
        assert title_bar_text.font().pixelSize() == 18

        minimize = window.findChild(QPushButton, "minimizeButton")
        maximize = window.findChild(QPushButton, "maximizeButton")
        close = window.findChild(QPushButton, "closeButton")
        assert minimize is not None
        assert maximize is not None
        assert close is not None
        assert all(
            button.size() == QSize(44, 34)
            for button in (minimize, maximize, close)
        )
        assert [
            label.text()
            for label in window._shop_feature_page.findChildren(QLabel, "keycap")
        ] == []
        assert not any(
            label.text() in {"F6", "移动悬浮窗"}
            for label in window.findChildren(QLabel)
        )
        assert [button.text() for button in (minimize, maximize, close)] == ["", "", ""]
        assert minimize._control_type == "minimize"
        assert maximize._control_type == "maximize"
        assert close._control_type == "close"
        maximize.click()
        application.processEvents()
        QTest.qWait(1)
        assert window.isMaximized()
        assert maximize._control_type == "restore"
        assert maximize._restore_font.family() in {
            "Segoe Fluent Icons",
            "Segoe MDL2 Assets",
        }
        assert maximize._restore_font.pixelSize() == 12
        assert window._shell.property("windowMaximized") is True
        assert window._title_bar.property("windowMaximized") is True
        assert window.grab().toImage().pixelColor(0, 0).alpha() == 255
        maximize.click()
        application.processEvents()
        QTest.qWait(1)
        assert not window.isMaximized()
        assert maximize._control_type == "maximize"
        assert window._shell.property("windowMaximized") is False
        assert window._title_bar.property("windowMaximized") is False
    finally:
        window.close()
        application.processEvents()


def test_refresh_limit_plain_input_accepts_only_complete_bounded_integers(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    limit_input = window.findChild(QLineEdit, "refreshLimitInput")
    start_button = window.findChild(QPushButton, "startButton")

    try:
        assert limit_input is not None
        assert start_button is not None

        for text, value in (
            ("0", 0),
            ("3", 3),
            ("2147483647", 2_147_483_647),
        ):
            limit_input.setText(text)
            assert limit_input.hasAcceptableInput()
            assert window._shop_feature_page.limit_value() == value
            assert start_button.isEnabled()

        for text in (
            "",
            "-1",
            "+1",
            "1.5",
            "1,000",
            "abc",
            "１２",
            "2147483648",
        ):
            limit_input.setText(text)
            assert not limit_input.hasAcceptableInput()
            assert window._shop_feature_page.limit_value() is None
            assert not start_button.isEnabled()
    finally:
        window.close()
        application.processEvents()


def test_main_window_switches_between_module_and_shop_pages(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    stack = window.findChild(QStackedWidget, "pageStack")
    shop_card = window.findChild(QAbstractButton, "shopModuleCard")
    back_button = window.findChild(QPushButton, "backToModulesButton")
    try:
        assert stack is not None
        assert shop_card is not None
        assert back_button is not None
        assert stack.currentWidget() is not None
        assert stack.currentWidget().objectName() == "functionCenterPage"
        assert shop_card.accessibleName() == "刷新秘密商店"

        shop_card.click()
        application.processEvents()
        assert stack.currentWidget() is not None
        assert stack.currentWidget().objectName() == "shopFeaturePage"

        back_button.click()
        application.processEvents()
        assert stack.currentWidget() is not None
        assert stack.currentWidget().objectName() == "functionCenterPage"
    finally:
        window.close()
        application.processEvents()


def test_function_center_reflows_and_future_cards_are_deliberate_placeholders(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    try:
        window.show()
        window.resize(1180, 760)
        application.processEvents()
        assert window._function_center_page._column_count == 2
        shop_card = window._function_center_page.shop_card
        assert shop_card._IMAGE_ZOOM == pytest.approx(1.0)
        assert shop_card._focal_point == QPointF(0.5, 0.5)

        window.resize(window.minimumSize())
        application.processEvents()
        assert window._function_center_page._column_count == 1

        future_cards = [
            window.findChild(QAbstractButton, f"futureModuleCard{index}")
            for index in range(3, 4)
        ]
        assert all(card is not None for card in future_cards)
        assert window._function_center_page.chaos_card.isEnabled()
        assert all(not card.isEnabled() for card in future_cards if card is not None)
        assert all(
            card.accessibleName() == "开发中"
            for card in future_cards
            if card is not None
        )
        assert set(window._function_center_page._cards_by_id) == {
            "shop_refresh",
            "penguin_exchange",
            "chaos",
            "future_3",
        }
        assert all(
            card.graphicsEffect() is None
            for card in window._function_center_page.cards
        )
        assert all(
            card._card_rect().bottom() < card.rect().bottom()
            for card in window._function_center_page.cards
        )
        current_page = window._pages.currentWidget()
        window._show_module_page("future_1")
        assert window._pages.currentWidget() is current_page
    finally:
        window.close()
        application.processEvents()


@pytest.mark.parametrize("name, label_text", [
    ("friendshipPointsToggle", "购买友情点数"),
    ("continuousRefreshToggle", "连续刷新"),
])
def test_setting_toggle_changes_only_when_switch_itself_is_clicked(
    tmp_path: Path,
    name: str,
    label_text: str,
) -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    shop_card = window.findChild(QAbstractButton, "shopModuleCard")
    toggle = window.findChild(QAbstractButton, name)
    friendship_label = next(
        label
        for label in window.findChildren(QLabel)
        if label.text() == label_text
    )
    try:
        assert shop_card is not None
        assert toggle is not None
        assert toggle.accessibleName() == label_text
        shop_card.click()
        window.show()
        application.processEvents()

        QTest.mouseClick(friendship_label, Qt.MouseButton.LeftButton)
        application.processEvents()
        assert not toggle.isChecked()

        QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
        application.processEvents()
        assert toggle.isChecked()
        assert toggle.hasFocus()
        other = window._shop_feature_page.friendship_toggle if name == "continuousRefreshToggle" else window._shop_feature_page.continuous_refresh_toggle
        assert not other.isChecked()

        focused_image = toggle.grab().toImage()
        toggle.clearFocus()
        application.processEvents()
        unfocused_image = toggle.grab().toImage()
        assert focused_image == unfocused_image
        toggle.setFocus()
        QTest.keyClick(toggle, Qt.Key.Key_Space)
        assert not toggle.isChecked()
        assert not any(button.isChecked() for button in (
            window._shop_feature_page.friendship_toggle, window._shop_feature_page.continuous_refresh_toggle,
        ))
    finally:
        window.close()
        application.processEvents()


def test_continuous_refresh_layout_selection_and_small_window_scroll(tmp_path):
    application = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    try:
        window._show_module_page("shop_refresh")
        window.resize(window.minimumSize())
        window.show()
        application.processEvents()
        page = window._shop_feature_page
        toggle = page.continuous_refresh_toggle
        assert not toggle.isChecked()
        assert toggle.size() == page.friendship_toggle.size()
        assert toggle.geometry().top() > page.friendship_toggle.geometry().bottom()
        assert toggle.geometry().right() == page.friendship_toggle.geometry().right()
        assert [label.text() for label in page.findChildren(QLabel) if label.text()] == [
            "刷新秘密商店", "天空石消耗上限", "购买友情点数", "连续刷新",
        ]
        toggle.setChecked(True)
        assert not page.friendship_toggle.isChecked()
        window._title_bar.back_button.click()
        window._show_module_page("shop_refresh")
        assert toggle.isChecked()
        scroll = page.findChild(QScrollArea, "featureScrollArea")
        assert scroll is not None
        scroll.ensureWidgetVisible(page.start_button)
        application.processEvents()
        center = page.start_button.mapTo(scroll.viewport(), page.start_button.rect().center())
        assert scroll.viewport().rect().contains(center)
        assert scroll.horizontalScrollBar().maximum() == 0
        fresh_window = MainWindow(tmp_path)
        assert fresh_window._shop_feature_page.continuous_refresh_toggle.isChecked()
        fresh_window.close()
    finally:
        window.close()
        application.processEvents()
