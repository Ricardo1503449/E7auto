from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from e7auto.domain import (
    OverlayActivityStatus,
    RunState,
    RuntimeSnapshot,
    StopReason,
    TargetTally,
)
import e7auto.ui as ui_module
from e7auto.ui import MainWindow, OverlayMoveCommand, StatsOverlay
from e7auto.overlay_position import OverlayPositionStore, SavedOverlayPosition

from tests.helpers import make_config


def test_main_window_has_limit_friendship_checkbox_and_start_button(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    try:
        assert window.findChildren(QSpinBox) == []
        limit_labels = [
            label
            for label in window.findChildren(QLabel)
            if label.text() == "刷新货币消耗上限"
        ]
        assert len(limit_labels) == 1
        limit_inputs = window.findChildren(QLineEdit, "refreshLimitInput")
        assert len(limit_inputs) == 1
        assert limit_inputs[0].text() == "0"
        window.show()
        application.processEvents()
        assert limit_labels[0].geometry().right() < limit_inputs[0].geometry().left()
        checkboxes = window.findChildren(QCheckBox)
        assert len(checkboxes) == 1
        assert checkboxes[0].text() == "购买友情点数"
        assert not checkboxes[0].isChecked()
        buttons = window.findChildren(QPushButton)
        assert len(buttons) == 1
        assert buttons[0].text() == "启动脚本"
    finally:
        window.close()
        application.processEvents()


def test_refresh_limit_plain_input_accepts_only_complete_bounded_integers(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    limit_input = window.findChild(QLineEdit, "refreshLimitInput")
    start_button = window.findChild(QPushButton)

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
            assert window._validated_refresh_limit() == value
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
            assert window._validated_refresh_limit() is None
            assert not start_button.isEnabled()
    finally:
        window.close()
        application.processEvents()


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
            self.snapshot = FakeSignal()
            self.finished = FakeSignal()

        def moveToThread(self, thread: object) -> None:
            captured["worker_thread"] = thread

        def run(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

    monkeypatch.setattr(ui_module, "load_config", lambda _path: make_config())
    monkeypatch.setattr(ui_module, "QThread", FakeThread)
    monkeypatch.setattr(ui_module, "AutomationWorker", FakeWorker)

    window = MainWindow(tmp_path)
    limit_input = window.findChild(QLineEdit, "refreshLimitInput")
    try:
        assert limit_input is not None
        limit_input.setText("123")
        window._start_run()

        assert captured["refresh_limit"] == 123
        assert isinstance(captured["refresh_limit"], int)
        assert captured["thread_started"] is True
    finally:
        window._thread = None
        window._worker = None
        window.close()
        application.processEvents()


def test_stats_overlay_keeps_one_size_for_all_runtime_values() -> None:
    application = QApplication.instance() or QApplication([])
    overlay = StatsOverlay()
    config = make_config(include_friendship=True)
    overlay.configure(config)
    targets = tuple(
        (target.target_id, target.display_name) for target in config.targets
    )
    initial = RuntimeSnapshot.initial("fixed-size", targets, 1)
    maximum = RuntimeSnapshot(
        run_id="fixed-size",
        state=RunState.REFRESHING,
        targets=tuple(
            TargetTally(target_id, display_name, 9_999_999)
            for target_id, display_name in targets
        ),
        refresh_spent=9_999_999,
        refresh_limit=9_999_999,
        refreshes_without_mandatory_target=9_999_999,
        overlay_status=OverlayActivityStatus.REFRESHING,
    )

    try:
        overlay.show()
        overlay.update_snapshot(initial)
        application.processEvents()
        fixed_size = overlay.size()

        overlay.start_elapsed_timer()
        assert overlay._format_elapsed(3_661) == "已耗时：1时1分1秒"

        overlay.update_snapshot(maximum)
        application.processEvents()
        assert overlay.size() == fixed_size
        assert overlay._no_target.text() == "已经9999999次未出货"
        assert overlay._status.text() == "当前状态：刷新ing..."

        reconnecting = maximum.with_overlay_status(OverlayActivityStatus.RECONNECTING)
        overlay.update_snapshot(reconnecting)
        application.processEvents()
        assert overlay._status.text() == "当前状态：重连中"
        assert overlay.size() == fixed_size

        overlay.update_snapshot(maximum.finalized(StopReason.REFRESH_STRATEGY_EXHAUSTED))
        overlay.stop_elapsed_timer()
        application.processEvents()
        assert overlay.size() == fixed_size
        assert not overlay._elapsed_timer.isActive()
        assert overlay.isVisible()
        assert overlay._status.text() == "当前状态：已停止"
        assert overlay._hint.text() == "F5结束 / F6移动"
        assert all(label.sizeHint().width() <= label.width() for label in overlay.findChildren(QLabel))
    finally:
        overlay.close()
        application.processEvents()


def test_stats_overlay_recomputes_fixed_size_when_configured_for_a_second_run() -> None:
    application = QApplication.instance() or QApplication([])
    overlay = StatsOverlay()
    config = make_config(include_friendship=True)
    targets = tuple(
        (target.target_id, target.display_name) for target in config.targets
    )

    try:
        overlay.configure(config)
        overlay.show()
        first_size = overlay.size()
        first_final = RuntimeSnapshot.initial("first-run", targets, 150).finalized(
            StopReason.MANUAL_F5
        )
        overlay.update_snapshot(first_final)
        application.processEvents()

        overlay.configure(config)
        overlay.update_snapshot(RuntimeSnapshot.initial("second-run", targets, 15_000))
        application.processEvents()

        assert overlay.size() == first_size
        assert overlay._currency.text() == "已消耗天空石：0 / 15000"
        assert overlay._currency.sizeHint().width() <= overlay._currency.width()
        assert all(
            label.sizeHint().width() <= label.width()
            for label in overlay.findChildren(QLabel)
        )
    finally:
        overlay.close()
        application.processEvents()


def test_stats_overlay_uses_centered_symmetric_longest_line_layout() -> None:
    application = QApplication.instance() or QApplication([])
    overlay = StatsOverlay()
    config = make_config(include_friendship=True)
    overlay.configure(config)
    maximum = RuntimeSnapshot(
        run_id="centered-layout",
        state=RunState.REFRESHING,
        targets=tuple(
            TargetTally(target.target_id, target.display_name, 9_999_999)
            for target in config.targets
        ),
        refresh_spent=9_999_999,
        refresh_limit=9_999_999,
        refreshes_without_mandatory_target=9_999_999,
        overlay_status=OverlayActivityStatus.TRANSFERRING,
    )

    try:
        overlay.show()
        overlay.update_snapshot(maximum)
        application.processEvents()

        labels = overlay.findChildren(QLabel)
        assert "font-size: 18px" in overlay.styleSheet()
        assert overlay._currency.text() == "已消耗天空石：9999999 / 9999999"
        assert overlay._elapsed.text() == "已耗时：0时0分0秒"
        assert overlay._no_target.text() == "已经9999999次未出货"
        assert overlay._status.text() == "当前状态：转运ing..."
        assert overlay._hint.text() == "F5结束 / F6移动"
        assert all(
            label.alignment() & Qt.AlignmentFlag.AlignHCenter for label in labels
        )

        panel = overlay.findChild(QWidget, "panel")
        assert panel is not None and panel.layout() is not None
        panel_layout = panel.layout()
        assert panel_layout.itemAt(1).spacerItem() is not None
        assert panel_layout.itemAt(1).spacerItem().sizeHint().height() == overlay._SECTION_GAP_PX
        assert panel_layout.itemAt(4).spacerItem() is not None
        assert panel_layout.itemAt(4).spacerItem().sizeHint().height() == overlay._SECTION_GAP_PX
        margins = panel.layout().contentsMargins()
        assert (margins.left(), margins.right()) == (14, 14)
        assert overlay.width() == max(label.sizeHint().width() for label in labels) + 28

        for label in labels:
            text_width = label.fontMetrics().horizontalAdvance(label.text())
            remaining = label.width() - text_width
            assert remaining >= 0
            assert abs((remaining // 2) - (remaining - remaining // 2)) <= 1
    finally:
        overlay.close()
        application.processEvents()


def test_overlay_position_store_round_trips_and_rejects_invalid_data(tmp_path: Path) -> None:
    path = tmp_path / "state" / "overlay_position.json"
    store = OverlayPositionStore(path)

    assert store.load() is None
    store.save(SavedOverlayPosition(321, -45))
    assert store.load() == SavedOverlayPosition(321, -45)

    path.write_text('{"x": true, "y": 2}', encoding="utf-8")
    assert store.load() is None


def test_overlay_uses_saved_position_then_falls_back_for_offscreen_state(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    store = OverlayPositionStore(tmp_path / "overlay.json")
    overlay = StatsOverlay(store)
    config = make_config()
    overlay.configure(config)
    try:
        store.save(SavedOverlayPosition(10, 20))
        assert overlay._saved_position() == QPoint(10, 20)

        store.save(SavedOverlayPosition(100_000, 100_000))
        assert overlay._saved_position() is None
    finally:
        overlay.close()
        application.processEvents()


def test_move_mode_restores_click_through_and_persists_locked_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = QApplication.instance() or QApplication([])
    store = OverlayPositionStore(tmp_path / "overlay.json")
    overlay = StatsOverlay(store)
    overlay.configure(make_config())
    styles = [0]
    monkeypatch.setattr(ui_module.win32gui, "GetWindowLong", lambda *_args: styles[-1])
    monkeypatch.setattr(
        ui_module.win32gui,
        "SetWindowLong",
        lambda _hwnd, _index, style: styles.append(style),
    )
    monkeypatch.setattr(ui_module, "exclude_window_from_capture", lambda _hwnd: True)
    monkeypatch.setattr(
        ui_module,
        "get_window_display_affinity",
        lambda _hwnd: ui_module.WDA_EXCLUDEFROMCAPTURE,
    )
    try:
        overlay.show()
        overlay.move(123, 234)
        application.processEvents()

        begin = OverlayMoveCommand(True, threading.Event())
        overlay._apply_move_command(begin)
        assert begin.result
        assert overlay._moving
        assert not overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert overlay._hint.text() == "F5结束 / F6移动"

        finish = OverlayMoveCommand(False, threading.Event())
        overlay._apply_move_command(finish)
        assert finish.result
        assert not overlay._moving
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert styles[-1] & ui_module.win32con.WS_EX_TRANSPARENT
        saved = store.load()
        assert saved is not None
        assert (saved.x, saved.y) == (
            overlay.frameGeometry().x(),
            overlay.frameGeometry().y(),
        )
    finally:
        overlay.close()
        application.processEvents()
