from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QWidget,
)

from e7auto.domain import (
    OverlayActivityStatus,
    RunState,
    RuntimeSnapshot,
    StopReason,
    TargetTally,
)
from e7auto.config import Rect, load_config
import e7auto.ui as ui_module
from e7auto.ui import MainWindow, OverlayCommand, StatsOverlay
from e7auto.overlay_position import OverlayPositionStore, SavedOverlayPosition

from tests.helpers import make_config
from scripts.verify_release import (
    verify_forbidden_release_files,
    verify_required_release_files,
    verify_ui_assets,
)


ROOT = Path(__file__).resolve().parents[1]


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
        window._show_shop_page()
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
        assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
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
            for label in window.findChildren(QLabel, "keycap")
        ] == ["F5"]
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
            for index in range(1, 4)
        ]
        assert all(card is not None for card in future_cards)
        assert all(not card.isEnabled() for card in future_cards if card is not None)
        assert all(
            card.accessibleName() == "开发中"
            for card in future_cards
            if card is not None
        )
        assert set(window._function_center_page._cards_by_id) == {
            "shop_refresh",
            "future_1",
            "future_2",
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


def test_friendship_toggle_changes_only_when_switch_itself_is_clicked(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    shop_card = window.findChild(QAbstractButton, "shopModuleCard")
    toggle = window.findChild(QAbstractButton, "friendshipPointsToggle")
    friendship_label = next(
        label
        for label in window.findChildren(QLabel)
        if label.text() == "购买友情点数"
    )
    try:
        assert shop_card is not None
        assert toggle is not None
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

        focused_image = toggle.grab().toImage()
        toggle.clearFocus()
        application.processEvents()
        unfocused_image = toggle.grab().toImage()
        assert focused_image == unfocused_image
    finally:
        window.close()
        application.processEvents()


def test_ui_assets_and_standalone_build_are_wired() -> None:
    assert verify_ui_assets(ROOT / "assets" / "ui") == []
    application = QApplication.instance() or QApplication([])
    window = MainWindow(ROOT)
    try:
        window.show()
        application.processEvents()
        assert not window.windowIcon().isNull()
        assert not window._function_center_page.shop_card._pixmap.isNull()
        title_bar_icon = window.findChild(QLabel, "titleBarIcon")
        assert title_bar_icon is not None
        title_bar_pixmap = title_bar_icon.pixmap()
        assert title_bar_pixmap is not None
        expected_dpr = title_bar_icon.devicePixelRatioF()
        assert title_bar_pixmap.devicePixelRatioF() == pytest.approx(expected_dpr)
        assert title_bar_pixmap.width() == round(title_bar_icon.width() * expected_dpr)
        assert title_bar_pixmap.height() == round(title_bar_icon.height() * expected_dpr)
    finally:
        window.close()
        application.processEvents()
    build_script = (ROOT / "scripts" / "build-standalone.ps1").read_text(
        encoding="utf-8"
    )
    assert "--windows-icon-from-ico=$appIcon" in build_script
    assert "--include-data-dir=assets/ui=assets/ui" in build_script
    assert "--include-package=winrt.windows.foundation" in build_script
    assert "--include-module=winrt._winrt_windows_foundation" in build_script
    assert "--noinclude-dlls=cv2/opencv_videoio_ffmpeg*.dll" in build_script
    assert (
        "--noinclude-dlls=PySide6/qt-plugins/imageformats/qpdf.dll"
        in build_script
    )
    assert "--noinclude-dlls=qt6pdf.dll" in build_script
    assert '"E7auto_v${version}_x64.zip"' in build_script
    assert "Compress-Archive" in build_script
    assert "Failed builds/archives never reach this cleanup" in build_script
    assert "Remove-Item -LiteralPath $resolvedOldReleaseZip -Force" in build_script
    assert build_script.index("Compress-Archive") < build_script.index(
        "Remove-Item -LiteralPath $resolvedOldReleaseZip -Force"
    )


def test_release_verifier_rejects_approved_forbidden_files(tmp_path: Path) -> None:
    forbidden = (
        tmp_path / "cv2" / "opencv_videoio_ffmpeg500_64.dll",
        tmp_path / "PySide6" / "qt-plugins" / "imageformats" / "qpdf.dll",
        tmp_path / "qt6pdf.dll",
    )
    for path in forbidden:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    assert verify_forbidden_release_files(tmp_path) == [
        "forbidden release file was bundled: cv2/opencv_videoio_ffmpeg500_64.dll",
        "forbidden release file was bundled: PySide6/qt-plugins/imageformats/qpdf.dll",
        "forbidden release file was bundled: qt6pdf.dll",
    ]


def test_release_verifier_requires_winrt_foundation_projection(
    tmp_path: Path,
) -> None:
    assert verify_required_release_files(tmp_path) == [
        "missing required release file: winrt/_winrt_windows_foundation.pyd"
    ]

    foundation = tmp_path / "winrt" / "_winrt_windows_foundation.pyd"
    foundation.parent.mkdir(parents=True)
    foundation.touch()

    assert verify_required_release_files(tmp_path) == []


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

    monkeypatch.setattr(ui_module, "load_config", lambda _path: make_config())
    monkeypatch.setattr(ui_module, "QThread", FakeThread)
    monkeypatch.setattr(ui_module, "AutomationWorker", FakeWorker)

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
    styles = [ui_module.win32con.WS_EX_TRANSPARENT]
    monkeypatch.setattr(ui_module.win32gui, "GetWindowLong", lambda *_args: styles[-1])
    monkeypatch.setattr(
        ui_module.win32gui,
        "SetWindowLong",
        lambda _hwnd, _index, style: styles.append(style),
    )
    try:
        command = OverlayCommand(Rect(100, 200, 100, 80), threading.Event())
        overlay._apply_command(command)
        assert command.result
        assert not overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert overlay._hint.text() == "F5结束"
        assert not styles[-1] & ui_module.win32con.WS_EX_TRANSPARENT

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
