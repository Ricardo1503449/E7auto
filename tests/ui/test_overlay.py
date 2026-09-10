from __future__ import annotations

from pathlib import Path
import threading

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget
import pytest

from e7auto.config import Rect, load_config
from e7auto.domain import OverlayActivityStatus, RunState, RuntimeSnapshot, StopReason, TargetTally
from e7auto.overlay_position import OverlayPositionStore, SavedOverlayPosition
from e7auto.ui import OverlayCommand, StatsOverlay
from tests.helpers import make_config
from tests.helpers.paths import ROOT
import e7auto.ui.overlay as overlay_module


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
        assert overlay._hint.text() == "F5结束"
        assert all(label.sizeHint().width() <= label.width() for label in overlay.findChildren(QLabel))
    finally:
        overlay.close()
        application.processEvents()


def test_stats_overlay_close_button_is_available_only_after_stop() -> None:
    application = QApplication.instance() or QApplication([])
    overlay = StatsOverlay()
    config = make_config()
    targets = tuple(
        (target.target_id, target.display_name) for target in config.targets
    )
    try:
        overlay.configure(config)
        overlay.show()
        initial = RuntimeSnapshot.initial("dismiss-overlay", targets, 0)
        overlay.update_snapshot(initial)
        application.processEvents()

        button = overlay.findChild(QPushButton, "overlayCloseButton")
        panel = overlay.findChild(QWidget, "panel")
        assert button is not None
        assert panel is not None
        assert not button.isVisible()
        assert not overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        overlay.update_snapshot(initial.finalized(StopReason.BUDGET_COMPLETE))
        application.processEvents()

        assert button.isVisible()
        assert button.accessibleName() == "关闭悬浮窗"
        assert not overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert button.geometry().top() == 4
        assert button.geometry().right() == panel.rect().right() - 4
        assert overlay.isVisible()

        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        application.processEvents()
        assert not overlay.isVisible()

        overlay.configure(config)
        overlay.show()
        overlay.update_snapshot(RuntimeSnapshot.initial("next-run", targets, 0))
        application.processEvents()
        assert not button.isVisible()
        assert not overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    finally:
        overlay.close()
        application.processEvents()


def test_stats_overlay_collapses_to_draggable_logo_and_click_restores(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    store = OverlayPositionStore(tmp_path / "overlay.json")
    overlay = StatsOverlay(
        store,
        ROOT / "assets" / "ui" / "e7auto-icon-256.png",
    )
    config = make_config()
    try:
        overlay.configure(config)
        overlay.show()
        application.processEvents()

        panel = overlay.findChild(QWidget, "panel")
        collapse = overlay.findChild(QPushButton, "overlayCollapseButton")
        close = overlay.findChild(QPushButton, "overlayCloseButton")
        icon = overlay.findChild(QWidget, "collapsedOverlayIcon")
        assert panel is not None
        assert collapse is not None
        assert close is not None
        assert icon is not None
        assert collapse.text() == "收起"
        assert collapse.font().pixelSize() == overlay._FONT_SIZE_PX
        assert f"background: {overlay._ACCENT_GREEN}" in overlay.styleSheet()
        assert (collapse.geometry().left(), collapse.geometry().top()) == (4, 4)
        assert close.geometry().top() == collapse.geometry().top()
        assert panel.isVisible()
        assert not icon.isVisible()

        overlay.move(100, 100)
        QTest.mouseClick(collapse, Qt.MouseButton.LeftButton)
        application.processEvents()

        assert overlay._collapsed
        assert not panel.isVisible()
        assert icon.isVisible()
        assert overlay.size() == QSize(
            overlay._COLLAPSED_DIAMETER_PX,
            overlay._COLLAPSED_DIAMETER_PX,
        )
        assert not overlay._collapsed_icon._logo.isNull()

        QTest.mousePress(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=QPoint(34, 34),
        )
        QTest.mouseMove(overlay, QPoint(54, 54), delay=10)
        QTest.mouseRelease(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=QPoint(54, 54),
        )
        application.processEvents()

        assert overlay.pos() == QPoint(120, 120)
        assert overlay._collapsed
        assert store.load() == SavedOverlayPosition(120, 120)

        QTest.mouseClick(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=overlay.rect().center(),
        )
        application.processEvents()

        assert not overlay._collapsed
        assert panel.isVisible()
        assert not icon.isVisible()
    finally:
        overlay.close()
        application.processEvents()


def test_covenant_and_mystic_overlay_rows_are_green() -> None:
    application = QApplication.instance() or QApplication([])
    overlay = StatsOverlay()
    config = load_config(ROOT / "config" / "internal.yaml")
    try:
        overlay.configure(config)
        overlay.show()
        application.processEvents()

        assert overlay._ACCENT_GREEN == "#26985a"
        assert overlay._TARGET_TEXT_GREEN == "#04d86a"
        assert overlay._target_labels["covenant_bookmark"].styleSheet() == (
            f"color: {overlay._TARGET_TEXT_GREEN};"
        )
        assert overlay._target_labels["mystic_medal"].styleSheet() == (
            f"color: {overlay._TARGET_TEXT_GREEN};"
        )
        assert overlay._target_labels["friendship_points"].styleSheet() == ""
    finally:
        overlay.close()
        application.processEvents()


def test_collapsed_logo_uses_physical_pixels_for_200_percent_dpi() -> None:
    application = QApplication.instance() or QApplication([])
    overlay = StatsOverlay(
        logo_path=ROOT / "assets" / "ui" / "e7auto-icon-256.png"
    )
    try:
        scaled = overlay._collapsed_icon._logo_for_dpr(64, 2.0)
        first_cache_key = scaled.cacheKey()

        assert (scaled.width(), scaled.height()) == (128, 128)
        assert scaled.devicePixelRatio() == 2.0
        assert scaled.deviceIndependentSize() == QSize(64, 64)
        assert overlay._collapsed_icon._logo_for_dpr(64, 2.0).cacheKey() == (
            first_cache_key
        )
    finally:
        overlay.close()
        application.processEvents()


def test_each_overlay_configuration_restores_the_full_panel() -> None:
    application = QApplication.instance() or QApplication([])
    overlay = StatsOverlay(
        logo_path=ROOT / "assets" / "ui" / "e7auto-icon-256.png"
    )
    config = make_config()
    try:
        overlay.configure(config)
        overlay.show()
        overlay._collapse_overlay()
        application.processEvents()
        assert overlay._collapsed

        overlay.configure(config)
        application.processEvents()

        assert not overlay._collapsed
        assert overlay._panel.isVisible()
        assert not overlay._collapsed_icon.isVisible()
        assert overlay.size() == overlay._expanded_size
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
        assert overlay._hint.text() == "F5结束"
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


def test_overlay_is_always_draggable_and_saves_position_after_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = QApplication.instance() or QApplication([])
    store = OverlayPositionStore(tmp_path / "overlay.json")
    overlay = StatsOverlay(store)
    overlay.configure(make_config())
    styles = [overlay_module.win32con.WS_EX_TRANSPARENT]
    monkeypatch.setattr(overlay_module.win32gui, "GetWindowLong", lambda *_args: styles[-1])
    monkeypatch.setattr(
        overlay_module.win32gui,
        "SetWindowLong",
        lambda _hwnd, _index, style: styles.append(style),
    )
    try:
        command = OverlayCommand(Rect(100, 200, 100, 80), threading.Event())
        overlay._apply_command(command)
        assert command.result
        assert not overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert overlay._hint.text() == "F5结束"
        assert not styles[-1] & overlay_module.win32con.WS_EX_TRANSPARENT

        overlay.move(123, 234)
        application.processEvents()
        overlay._save_position()
        saved = store.load()
        assert saved is not None
        assert (saved.x, saved.y) == (
            overlay.frameGeometry().x(),
            overlay.frameGeometry().y(),
        )
    finally:
        overlay.close()
        application.processEvents()
