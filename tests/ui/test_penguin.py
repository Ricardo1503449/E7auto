from dataclasses import replace
from pathlib import Path
import threading

import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractButton, QLabel, QLineEdit

from e7auto.domain import OverlayActivityStatus, RuntimeSnapshot, StopReason
from e7auto.config import Rect
from e7auto.ui import MainWindow, StatsOverlay, OverlayCommand
from tests.helpers import make_config


@pytest.mark.parametrize("from_penguins", [False, True])
@pytest.mark.parametrize("collapsed", [False, True])
def test_back_hides_stopped_overlay_until_next_run_is_positioned(tmp_path, from_penguins, collapsed):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    overlay = window._overlay
    config = make_config()
    try:
        old_id = "penguin_exchange" if from_penguins else "shop_refresh"
        new_id = "shop_refresh" if from_penguins else "penguin_exchange"
        window._show_module_page(old_id)
        if from_penguins:
            overlay.configure_penguins(config)
            previous = replace(RuntimeSnapshot.penguins("old", 10), purchases_completed=3)
        else:
            overlay.configure(config)
            previous = RuntimeSnapshot.initial("old", (), 100).with_refresh_spent(9)
        overlay.update_snapshot(previous.finalized(StopReason.MANUAL_F5))
        overlay.show()
        if collapsed:
            overlay._collapse_overlay()
        app.processEvents()
        assert overlay.isVisible()
        old_count_text = overlay._currency.text()

        window._module_pages[old_id].back_button.click()
        app.processEvents()
        assert window._pages.currentWidget() is window._function_center_page
        assert not overlay.isVisible()
        assert overlay._currency.text() == old_count_text  # hidden, not destroyed/reset
        window._function_center_page._cards_by_id[new_id].click()
        assert window._pages.currentWidget() is window._module_pages[new_id]
        assert not overlay.isVisible()

        # Startup configures a fresh display; the worker's positioning command
        # then reveals it. No game window, capture or input is instantiated.
        if from_penguins:
            overlay.configure(config)
            initial = RuntimeSnapshot.initial("new", (), 25)
            expected = "已消耗天空石：0 / 25"
        else:
            overlay.configure_penguins(config)
            initial = RuntimeSnapshot.penguins("new", 25)
            expected = "已购买次数：0 / 25"
        overlay.update_snapshot(initial)
        assert not overlay.isVisible()
        command = OverlayCommand(Rect(100, 100, 1000, 800), threading.Event())
        overlay._apply_command(command)
        app.processEvents()
        assert command.result and overlay.isVisible()
        assert not overlay._collapsed
        assert overlay._currency.text() == expected
        assert overlay._status.text() == "当前状态：已启动"
    finally:
        window.close()
        app.processEvents()


def test_back_request_during_run_does_not_hide_overlay(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    try:
        window._show_shop_page()
        window._overlay.configure(make_config())
        window._overlay.show()
        window._thread = object()
        window._show_function_center()
        assert window._pages.currentWidget() is window._shop_feature_page
        assert window._overlay.isVisible()
    finally:
        window._thread = None
        window.close()
        app.processEvents()


def test_right_hand_card_opens_minimal_penguin_settings(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    try:
        center = window._function_center_page
        assert center.cards[1] is center.penguin_card
        assert center.penguin_card.isEnabled()
        assert center.penguin_card.accessibleName() == "红叶换企鹅"
        center.penguin_card.click()
        assert window._pages.currentWidget() is window._penguin_feature_page
        page = window._penguin_feature_page
        assert page.findChildren(QLineEdit) == [page.limit_input]
        assert isinstance(page.leaf_cost_display, QLabel)
        assert page.leaf_cost_display.text() == "0"
        assert page.leaf_cost_display.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction
        assert len(page.findChildren(QAbstractButton)) == 2  # back + start
        assert "购买次数上限" in [label.text() for label in page.findChildren(QLabel)]
        for text, expected_cost in (("0", "0"), ("1", "5,100"), ("2", "10,200"),
                                    ("2147483647", "10,952,166,599,700")):
            page.limit_input.setText(text)
            assert page.purchase_limit() == int(text)
            assert page.leaf_cost_display.text() == expected_cost
            assert page.start_button.isEnabled()
        for text in ("", "-1", "1.5", "2147483648", "abc"):
            page.limit_input.setText(text)
            assert page.purchase_limit() is None
            assert page.leaf_cost_display.text() == "—"
            assert not page.start_button.isEnabled()
        page.limit_input.setText("3")
        assert page.leaf_cost_display.text() == "15,300"
        assert page.start_button.isEnabled()
        window._thread = object()
        window._show_shop_page()
        assert window._pages.currentWidget() is page
        window._thread = None
        page.back_button.click()
        assert window._pages.currentWidget() is center
    finally:
        window._thread = None
        window.close()
        app.processEvents()


def test_penguin_overlay_has_four_lines_and_preserves_final_count():
    app = QApplication.instance() or QApplication([])
    overlay = StatsOverlay()
    config = make_config()
    try:
        overlay.configure(config)
        overlay.configure_penguins(config)
        overlay.show()
        initial = RuntimeSnapshot.penguins("penguins", 20)
        overlay.update_snapshot(initial)
        overlay.start_elapsed_timer()
        app.processEvents()
        assert overlay._target_labels == {}
        assert overlay._no_target.isHidden()
        visible = [label.text() for label in overlay.findChildren(QLabel) if label.isVisible()]
        assert visible == ["已耗时：0时0分0秒", "已购买次数：0 / 20", "当前状态：已启动", "F5结束"]
        overlay.update_snapshot(initial.with_overlay_status(OverlayActivityStatus.RECONNECTING))
        assert overlay._status.text() == "当前状态：重连中"
        assert overlay._currency.text() == "已购买次数：0 / 20"
        assert overlay._elapsed_timer.isActive()
        overlay.update_snapshot(initial.with_overlay_status(OverlayActivityStatus.BUYING_PENGUINS))
        assert overlay._status.text() == "当前状态：购买企鹅中"
        final = replace(initial, purchases_completed=12).finalized(StopReason.PENGUIN_FUNDS_COMPLETE)
        overlay.update_snapshot(final)
        overlay.stop_elapsed_timer()
        app.processEvents()
        assert overlay.isVisible()
        assert overlay._currency.text() == "已购买次数：12 / 20"
        assert overlay._status.text() == "当前状态：已停止"
        assert not overlay._elapsed_timer.isActive()
        assert all(label.sizeHint().width() <= label.width()
                   for label in overlay.findChildren(QLabel) if label.isVisible())
        largest = replace(initial, purchase_limit=2_147_483_647, purchases_completed=2_147_483_647)
        overlay.update_snapshot(largest)
        app.processEvents()
        assert overlay._currency.sizeHint().width() <= overlay._currency.width()
        overlay._collapse_overlay()
        overlay.configure(config)
        assert not overlay._collapsed and not overlay._no_target.isHidden()
        assert overlay._target_labels
        assert overlay._currency.text() == "已消耗天空石：0 / 0"
    finally:
        overlay.close()
        app.processEvents()
