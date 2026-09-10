from __future__ import annotations

from pathlib import Path
import uuid

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QThread, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QIcon, QResizeEvent
from PySide6.QtWidgets import QFrame, QMainWindow, QStackedWidget, QVBoxLayout

from ..config import ConfigError, LoggingConfig, load_config
from ..domain import RunState, RuntimeSnapshot, StopReason
from ..overlay_position import OverlayPositionStore
from ..run_logging import RunLogManager
from .overlay import StatsOverlay
from .pages.function_center import _FunctionCenterPage
from .pages.shop import _ShopFeaturePage
from .window_chrome import _TitleBar, _ResizeHandle
from .worker import AutomationWorker


class MainWindow(QMainWindow):
    _MINIMUM_SIZE = QSize(760, 560)
    _INITIAL_SIZE = QSize(1180, 760)

    def __init__(self, project_root: Path):
        super().__init__()
        self._project_root = project_root
        self._config_path = project_root / "config" / "internal.yaml"
        self._overlay = StatsOverlay(
            OverlayPositionStore(project_root / "state" / "overlay_position.json"),
            project_root / "assets" / "ui" / "e7auto-icon-256.png",
        )
        self._thread: QThread | None = None
        self._worker: AutomationWorker | None = None
        self._entered_inventory = False
        self._resize_handles: list[_ResizeHandle] = []

        icon_path = project_root / "assets" / "ui" / "e7auto.ico"
        self.setWindowTitle("E7AUTO")
        self.setWindowIcon(QIcon(str(icon_path)))
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(self._MINIMUM_SIZE)
        self.resize(self._INITIAL_SIZE)

        self._shell = QFrame()
        self._shell.setObjectName("appShell")
        self._shell.setProperty("windowMaximized", False)
        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self._title_bar = _TitleBar(self, icon_path)
        shell_layout.addWidget(self._title_bar)

        self._pages = QStackedWidget()
        self._pages.setObjectName("pageStack")
        self._function_center_page = _FunctionCenterPage(project_root)
        self._shop_feature_page = _ShopFeaturePage()
        self._pages.addWidget(self._function_center_page)
        self._pages.addWidget(self._shop_feature_page)
        self._pages.setCurrentWidget(self._function_center_page)
        shell_layout.addWidget(self._pages, 1)
        self.setCentralWidget(self._shell)

        self._module_pages = {"shop_refresh": self._shop_feature_page}
        self._function_center_page.module_requested.connect(self._show_module_page)
        self._shop_feature_page.back_requested.connect(self._show_function_center)
        self._limit_label = self._shop_feature_page.limit_label
        self._limit = self._shop_feature_page.limit_input
        self._friendship_points = self._shop_feature_page.friendship_toggle
        self._start = self._shop_feature_page.start_button
        self._start.clicked.connect(self._start_run)
        self._limit.textChanged.connect(self._on_limit_text_changed)
        self._resize_handles = self._create_resize_handles()
        self._apply_styles()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background: transparent; }
            QFrame#appShell {
                background: #f2f3f5;
                border: 1px solid #cfd3d7;
                border-radius: 12px;
            }
            QFrame#appShell[windowMaximized="true"] {
                border: none;
                border-radius: 0;
            }
            QWidget#titleBar {
                background: #f5f6f7;
                border-bottom: 1px solid #d9dce0;
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }
            QWidget#titleBar[windowMaximized="true"] {
                border-top-left-radius: 0;
                border-top-right-radius: 0;
            }
            QLabel#titleBarIcon { border-radius: 4px; }
            QLabel#windowTitle {
                color: #14171a;
                font-family: "Segoe UI";
                font-size: 18px;
                font-weight: 600;
            }
            QPushButton[windowControl="true"] {
                border: none;
                border-radius: 7px;
                background: transparent;
                color: #111417;
            }
            QPushButton[windowControl="true"]:hover { background: #e3e5e8; }
            QPushButton#closeButton:hover { background: #e5565d; color: white; }
            QStackedWidget#pageStack,
            QWidget#functionCenterPage,
            QWidget#shopFeaturePage,
            QWidget#moduleCardHost,
            QWidget#featureContent,
            QScrollArea#moduleScrollArea,
            QScrollArea#featureScrollArea,
            QScrollArea#moduleScrollArea > QWidget > QWidget,
            QScrollArea#featureScrollArea > QWidget > QWidget {
                background: transparent;
                border: none;
            }
            QLabel {
                color: #15181b;
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 17px;
            }
            QLabel#pageHeading {
                color: #101214;
                font-size: 36px;
                font-weight: 700;
            }
            QLabel#cardHeading {
                color: #121518;
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#settingLabel { font-size: 18px; }
            QFrame#settingsCard, QFrame#shortcutCard {
                background: #ffffff;
                border: 1px solid #d7dade;
                border-radius: 14px;
            }
            QLineEdit#refreshLimitInput {
                background: #fbfbfc;
                color: #111417;
                border: 1px solid #d3d6da;
                border-radius: 9px;
                padding: 0 18px;
                font-family: "Segoe UI";
                font-size: 20px;
            }
            QLineEdit#refreshLimitInput:focus { border: 1px solid #8c9299; }
            QPushButton#startButton {
                background: #26985a;
                color: #ffffff;
                border: none;
                border-radius: 10px;
                font-size: 20px;
                font-weight: 600;
            }
            QPushButton#startButton:hover { background: #228a50; }
            QPushButton#startButton:pressed { background: #1d7846; }
            QPushButton#startButton:disabled { background: #b8bdc1; color: #eef0f1; }
            QPushButton#backToModulesButton {
                background: transparent;
                color: #1d2023;
                border: none;
                padding: 4px 2px;
                font-size: 17px;
                text-align: left;
            }
            QPushButton#backToModulesButton:hover { color: #26985a; }
            QLabel#keycap {
                background: #eef0f2;
                border: 1px solid #d7dade;
                border-radius: 8px;
                color: #1b1e21;
                font-family: "Segoe UI";
                font-size: 17px;
            }
            QScrollBar:vertical {
                width: 10px;
                background: transparent;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                min-height: 34px;
                background: #c6cbd0;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            """
        )

    def _create_resize_handles(self) -> list[_ResizeHandle]:
        definitions = (
            (Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor),
            (Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
            (Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
            (Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
            (
                Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                Qt.CursorShape.SizeFDiagCursor,
            ),
            (
                Qt.Edge.TopEdge | Qt.Edge.RightEdge,
                Qt.CursorShape.SizeBDiagCursor,
            ),
            (
                Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
                Qt.CursorShape.SizeBDiagCursor,
            ),
            (
                Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
                Qt.CursorShape.SizeFDiagCursor,
            ),
        )
        return [_ResizeHandle(self, edges, cursor) for edges, cursor in definitions]

    def _layout_resize_handles(self) -> None:
        if not self._resize_handles:
            return
        width = self.width()
        height = self.height()
        edge = 6
        corner = 14
        geometries = (
            QRect(corner, 0, max(0, width - 2 * corner), edge),
            QRect(corner, height - edge, max(0, width - 2 * corner), edge),
            QRect(0, corner, edge, max(0, height - 2 * corner)),
            QRect(width - edge, corner, edge, max(0, height - 2 * corner)),
            QRect(0, 0, corner, corner),
            QRect(width - corner, 0, corner, corner),
            QRect(0, height - corner, corner, corner),
            QRect(width - corner, height - corner, corner, corner),
        )
        for handle, geometry in zip(self._resize_handles, geometries, strict=True):
            handle.setGeometry(geometry)
            handle.setVisible(not self.isMaximized())
            handle.raise_()

    @Slot()
    def _show_shop_page(self) -> None:
        self._show_module_page("shop_refresh")

    @Slot(str)
    def _show_module_page(self, module_id: str) -> None:
        page = self._module_pages.get(module_id)
        if page is None:
            return
        self._pages.setCurrentWidget(page)
        self._limit.setFocus()

    @Slot()
    def _show_function_center(self) -> None:
        if self._thread is None:
            self._pages.setCurrentWidget(self._function_center_page)

    def _validated_refresh_limit(self) -> int | None:
        if not self._limit.hasAcceptableInput():
            return None
        return int(self._limit.text())

    @Slot(str)
    def _on_limit_text_changed(self, _text: str) -> None:
        if self._thread is None:
            self._start.setEnabled(self._validated_refresh_limit() is not None)

    @Slot()
    def _start_run(self) -> None:
        if self._thread is not None:
            return
        refresh_limit = self._validated_refresh_limit()
        if refresh_limit is None:
            self._limit.setFocus()
            return
        run_id = uuid.uuid4().hex[:12]
        try:
            config = load_config(self._config_path)
        except ConfigError as exc:
            manager = RunLogManager(self._project_root / "logs", LoggingConfig(14, 100))
            logger = manager.start(run_id)
            logger.event("startup_rejected", reason=StopReason.CONFIG_INCOMPLETE.value, errors=" | ".join(exc.errors))
            logger.close()
            self.showNormal()
            self.raise_()
            self.activateWindow()
            return

        self._entered_inventory = False
        self._overlay.configure(config)
        self._overlay.start_elapsed_timer()
        self._start.setEnabled(False)
        self._limit.setEnabled(False)
        self._friendship_points.setEnabled(False)
        self._shop_feature_page.back_button.setEnabled(False)
        thread = QThread(self)
        worker = AutomationWorker(
            config,
            refresh_limit,
            self._friendship_points.isChecked(),
            self._project_root,
            self._overlay,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.snapshot.connect(self._on_snapshot)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()
        self.showMinimized()

    @Slot(object)
    def _on_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        self._overlay.update_snapshot(snapshot)
        if snapshot.state in {RunState.SCANNING_TOP, RunState.SCANNING_BOTTOM, RunState.PURCHASING, RunState.REFRESHING}:
            self._entered_inventory = True

    @Slot(object)
    def _on_finished(self, snapshot: RuntimeSnapshot) -> None:
        self._overlay.stop_elapsed_timer()
        self._limit.setEnabled(True)
        self._friendship_points.setEnabled(True)
        self._shop_feature_page.back_button.setEnabled(True)
        self._thread = None
        self._worker = None
        self._start.setEnabled(self._validated_refresh_limit() is not None)
        if not self._entered_inventory:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._layout_resize_handles()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            QTimer.singleShot(0, self._sync_window_state_appearance)

    def _sync_window_state_appearance(self) -> None:
        maximized = self.isMaximized()
        for widget in (self._shell, self._title_bar):
            widget.setProperty("windowMaximized", maximized)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
        self._title_bar.sync_maximize_state()
        self._layout_resize_handles()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None:
            event.ignore()
            return
        self._overlay.close()
        super().closeEvent(event)
