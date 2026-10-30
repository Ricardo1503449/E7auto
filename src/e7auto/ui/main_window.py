from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QThread, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QIcon, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from e7auto.core.domain import RunState, RuntimeSnapshot
from e7auto.ui.position import OverlayPositionStore
from e7auto.ui.overlay import StatsOverlay
from e7auto.ui.pages.function_center import _FunctionCenterPage
from e7auto.ui.pages.shop import _ShopFeaturePage
from e7auto.ui.pages.penguin import _PenguinFeaturePage
from e7auto.ui.pages.chaos import _ChaosFeaturePage
from e7auto.ui.pages.application import _AboutPage, _SettingsPage
from e7auto.ui.navigation import _Sidebar
from e7auto.ui.window_chrome import _TitleBar, _ResizeHandle
from e7auto.ui.styles import MAIN_WINDOW_STYLE
from e7auto.ui.preparation import PreparationThread, RunRequest
from e7auto.ui.worker import AutomationWorker
from e7auto.ui.notifications import RunEndNotifier
from e7auto.ui.updates import UpdateController
from e7auto.ui.update_install import UpdateInstaller
from e7auto.ui.shop_reporting import ShopReportingController
from e7auto.ui.announcements import AnnouncementController
from e7auto.ui.widgets import AppDialogHost


class MainWindow(QMainWindow):
    _MINIMUM_SIZE = QSize(760, 560)
    _INITIAL_SIZE = QSize(1180, 760)

    def __init__(self, project_root: Path):
        super().__init__()
        self._native_frame = None
        if QApplication.platformName() == "windows":
            from e7auto.platform.window_frame import NativeWindowFrame
            self._native_frame = NativeWindowFrame()
        self.setProperty("nativeFrame", self._native_frame is not None)
        self._project_root = project_root
        self._config_path = project_root / "config" / "internal.yaml"
        self._overlay = StatsOverlay(
            OverlayPositionStore(project_root / "state" / "overlay_position.json"),
            project_root / "assets" / "ui" / "e7auto-icon-256.png",
        )
        self._thread: QThread | None = None
        self._worker: AutomationWorker | None = None
        self._preparation_thread: PreparationThread | None = None
        self._close_requested = False
        self._active_run_page = None
        self._pending_run_result: RuntimeSnapshot | None = None
        self._overlay.stop_requested.connect(self._request_stop)
        self._entered_inventory = False
        self._resize_handles: list[_ResizeHandle] = []

        icon_path = project_root / "assets" / "ui" / "e7auto.ico"
        self._run_notifier = RunEndNotifier(icon_path.with_name("e7auto-icon-64.png"), self)
        self.setWindowTitle("E7AUTO")
        self.setWindowIcon(QIcon(str(icon_path)))
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            # Keep native taskbar/system-menu actions with the custom title bar.
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        # A layered/alpha window bypasses the normal DWM frame transition path.
        # DWM supplies Windows corner clipping; keep Qt transparency only offscreen.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, self._native_frame is None)
        self.setMinimumSize(self._MINIMUM_SIZE)
        self.resize(self._INITIAL_SIZE)

        self._shell = QFrame()
        self._shell.setObjectName("appShell")
        self._shell.setProperty("windowMaximized", False)
        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self._title_bar = _TitleBar(self, icon_path)
        self._title_bar.back_button.clicked.connect(self._show_function_center)
        shell_layout.addWidget(self._title_bar)

        body = QWidget()
        body.setObjectName("appBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(1, 0, 1, 1)
        body_layout.setSpacing(0)
        self._sidebar = _Sidebar()
        self._selected_section = "home"
        self._sidebar.page_requested.connect(self._show_section)
        body_layout.addWidget(self._sidebar)

        self._pages = QStackedWidget()
        self._pages.setObjectName("pageStack")
        self._function_center_page = _FunctionCenterPage(project_root)
        self._shop_feature_page = _ShopFeaturePage(project_root)
        self._penguin_feature_page = _PenguinFeaturePage()
        self._chaos_feature_page = _ChaosFeaturePage()
        self._about_page = _AboutPage(project_root)
        self._update_controller = UpdateController(project_root / "state" / "update_status.json", self)
        self._update_installer = UpdateInstaller(project_root, self)
        self._reporting_controller = ShopReportingController(project_root, self)
        self._reporting_controller.finished.connect(self._finish_deferred_close)
        settings_navigation = self._sidebar.buttons["settings"]
        settings_navigation.set_update_available(self._update_controller.available)
        self._update_controller.availability_changed.connect(settings_navigation.set_update_available)
        self._settings_page = _SettingsPage(project_root, self._update_controller, self._reporting_controller)
        self._settings_page.update_requested.connect(self._start_update)
        self._settings_page.update_cancelled.connect(self._update_installer.cancel)
        self._update_installer.progress.connect(self._settings_page.show_download_progress)
        self._update_installer.status.connect(self._settings_page.show_install_status)
        self._update_installer.failed.connect(self._settings_page.show_install_error)
        self._update_installer.busy_changed.connect(self._settings_page.set_install_busy)
        self._update_installer.busy_changed.connect(self._on_update_busy_changed)
        self._update_installer.launching_changed.connect(self._settings_page.set_install_launching)
        self._update_installer.install_started.connect(self._close_for_update)
        self._update_installer.finished.connect(self._finish_deferred_close)
        self._reporting_controller.start()
        self._pages.addWidget(self._function_center_page)
        self._pages.addWidget(self._shop_feature_page)
        self._pages.addWidget(self._penguin_feature_page)
        self._pages.addWidget(self._chaos_feature_page)
        self._pages.addWidget(self._about_page)
        self._pages.addWidget(self._settings_page)
        self._pages.setCurrentWidget(self._function_center_page)
        body_layout.addWidget(self._pages, 1)
        shell_layout.addWidget(body, 1)
        self.setCentralWidget(self._shell)

        self._dialog_backdrop = QFrame(body)
        self._dialog_backdrop.setObjectName("dialogBackdrop")
        self._dialog_backdrop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._dialog_backdrop.hide()
        body.installEventFilter(self)
        self._dialog_host = AppDialogHost(self, body)
        self._dialog_host.active_changed.connect(self._set_dialog_visible)
        self._announcement_controller = AnnouncementController(project_root / "state" / "ui_preferences.json", self)
        self._desktop_action_pages = (self._settings_page, self._about_page)
        for page in self._desktop_action_pages:
            page.actions_settled.connect(self._finish_deferred_close)

        self._module_pages = {
            "shop_refresh": self._shop_feature_page,
            "penguin_exchange": self._penguin_feature_page,
            "chaos": self._chaos_feature_page,
        }
        self._function_center_page.module_requested.connect(self._show_module_page)
        self._chaos_feature_page.start_button.clicked.connect(self._start_chaos_run)
        self._penguin_feature_page.start_button.clicked.connect(self._start_penguin_run)
        self._shop_feature_page.start_button.clicked.connect(self._start_run)
        for page in self._module_pages.values():
            page.limit_input.textChanged.connect(self._on_limit_text_changed)
        self._resize_handles = self._create_resize_handles()
        self.setStyleSheet(MAIN_WINDOW_STYLE)

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

    @Slot(str)
    def _show_module_page(self, module_id: str) -> None:
        if self._is_busy() or self._dialog_host.active:
            return
        page = self._module_pages.get(module_id)
        if page is None:
            return
        self._selected_section = "home"
        self._sidebar.set_current("home")
        self._pages.setCurrentWidget(page)
        page.limit_input.setFocus()

    @Slot(str)
    def _show_section(self, section: str) -> None:
        if self._is_busy() or self._dialog_host.active:
            self._sidebar.set_current(self._selected_section)
            return
        page = {"home": self._function_center_page, "about": self._about_page,
                "settings": self._settings_page}.get(section)
        if page is None:
            return
        self._selected_section = section
        self._sidebar.set_current(section)
        self._overlay.hide()
        self._pages.setCurrentWidget(page)

    @Slot()
    def _show_function_center(self) -> None:
        if not self._is_busy():
            self._show_section("home")

    def _is_busy(self) -> bool:
        return self._thread is not None or self._preparation_thread is not None or self._update_installer.busy

    def _start_update(self, asset) -> None:
        if self._is_busy() or self._close_requested:
            self._settings_page.show_install_error("请先结束当前任务，再执行更新。")
            return
        self._update_installer.start(asset)

    def _close_for_update(self) -> None:
        self._close_requested = True
        self.close()

    @Slot(bool)
    def _on_update_busy_changed(self, _busy: bool) -> None:
        self._set_run_controls(self._is_busy())

    @Slot(str)
    def _on_limit_text_changed(self, _text: str) -> None:
        if not self._is_busy():
            for page in self._module_pages.values():
                page.start_button.setEnabled(page.limit_value() is not None)

    def _set_run_controls(self, busy: bool, *, preparing: bool = False) -> None:
        self._reporting_controller.set_running(busy)
        for page in self._module_pages.values():
            page.setEnabled(not busy)
            page.start_button.setText("正在准备…" if preparing and page is self._active_run_page else "启动脚本")
        self._title_bar.back_button.setEnabled(not busy and not self._dialog_host.active)
        self._sidebar.set_navigation_enabled(not busy)
        if not busy:
            self._on_limit_text_changed("")

    @Slot()
    def _start_run(self) -> None:
        self._launch_run("shop")

    @Slot()
    def _start_penguin_run(self) -> None:
        self._launch_run("penguin")

    @Slot()
    def _start_chaos_run(self) -> None:
        self._launch_run("chaos")

    def _launch_run(self, feature: str) -> None:
        if self._is_busy() or self._close_requested or self._dialog_host.active:
            return
        page = {"shop": self._shop_feature_page, "penguin": self._penguin_feature_page,
                "chaos": self._chaos_feature_page}[feature]
        limit = page.limit_value()
        if limit is None:
            page.limit_input.setFocus()
            return
        request = RunRequest(
            feature=feature, limit=limit,
            buy_friendship_points=feature == "shop" and self._shop_feature_page.friendship_toggle.isChecked(),
            continuous_refresh=feature == "shop" and self._shop_feature_page.continuous_refresh_toggle.isChecked(),
            target_stage=self._chaos_feature_page.target_stage() if feature == "chaos" else 1,
            lineup_id=self._chaos_feature_page.lineup_id() if feature == "chaos" else "dark_twins",
        )
        self._active_run_page = page
        self._overlay.hide()
        page.show_status("正在检查配置和资源…关闭窗口可取消。")
        self._preparation_thread = PreparationThread(self._config_path, request, self)
        self._preparation_thread.finished.connect(self._on_prepared)
        self._set_run_controls(True, preparing=True)
        self._preparation_thread.start()

    @Slot()
    def _on_prepared(self) -> None:
        preparation = self._preparation_thread
        if preparation is None:
            return
        if not preparation.wait(0):
            QTimer.singleShot(10, self._on_prepared)
            return
        self._preparation_thread = None
        request, config, error = preparation.request, preparation.config, preparation.error
        cancelled = preparation.cancelled.is_set() or self._close_requested
        preparation.deleteLater()
        if cancelled or config is None:
            self._active_run_page.show_status("" if cancelled else f"启动失败：{error}")
            self._set_run_controls(False)
            self._finish_deferred_close()
            if not cancelled:
                self._notify_startup_failure(request.feature)
            return
        try:
            self._start_prepared_run(request, config)
        except Exception as exc:
            self._active_run_page.show_status(f"启动失败：{type(exc).__name__}: {exc}")
            self._overlay.stop_elapsed_timer()
            self._set_run_controls(False)
            self._notify_startup_failure(request.feature)

    def _start_prepared_run(self, request: RunRequest, config) -> None:
        self._entered_inventory = False
        self._pending_run_result = None
        self._active_run_page.show_status("")
        configure = {"shop": self._overlay.configure, "penguin": self._overlay.configure_penguins,
                     "chaos": self._overlay.configure_chaos}[request.feature]
        configure(config)
        self._overlay.start_elapsed_timer()
        self._set_run_controls(True)
        limits = {"shop": {}, "penguin": {"purchase_limit": request.limit},
                  "chaos": {"round_limit": request.limit}}[request.feature]
        thread = QThread(self)
        worker = AutomationWorker(
            config, request.limit if request.feature == "shop" else 0,
            request.buy_friendship_points, self._project_root, self._overlay,
            continuous_refresh=request.continuous_refresh, **limits,
            report_recorder=(self._reporting_controller.recorder(continuous_refresh=request.continuous_refresh)
                             if request.feature == "shop" else None),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.snapshot.connect(self._on_snapshot)
        worker.failed.connect(self._on_run_error)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_stopped)
        self._thread = thread
        self._worker = worker
        thread.start()
        self.showMinimized()

    @Slot(str)
    def _on_run_error(self, message: str) -> None:
        if self._active_run_page is not None:
            self._active_run_page.show_status(message)

    @Slot()
    def _request_stop(self) -> None:
        if self._preparation_thread is not None:
            self._preparation_thread.cancel()
        elif self._worker is not None:
            self._worker.request_stop()

    @Slot(object)
    def _on_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        self._overlay.update_snapshot(snapshot)
        if snapshot.state in {RunState.SCANNING_TOP, RunState.SCANNING_BOTTOM, RunState.PURCHASING, RunState.REFRESHING,
                              RunState.ENTERING_SANCTUARY, RunState.GROWING_PENGUINS, RunState.RETURNING_MAIN, RunState.EXPLORING_CHAOS}:
            self._entered_inventory = True

    @Slot(object)
    def _on_finished(self, snapshot: RuntimeSnapshot) -> None:
        self._overlay.update_snapshot(snapshot)
        self._overlay.stop_elapsed_timer()
        if self._thread is not None and snapshot.is_final:
            self._pending_run_result = snapshot

    @Slot()
    def _on_thread_stopped(self) -> None:
        thread = self._thread
        if thread is None:
            return
        # finished precedes native/TLS teardown. Poll without blocking the GUI.
        if not thread.wait(0):
            QTimer.singleShot(10, self._on_thread_stopped)
            return
        self._thread = None
        self._worker = None
        thread.deleteLater()
        self._set_run_controls(False)
        if not self._entered_inventory:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        result, self._pending_run_result = self._pending_run_result, None
        if result is not None:
            self._run_notifier.notify(
                result, enabled=self._settings_page.notifications_enabled,
            )

    def _notify_startup_failure(self, feature: str) -> None:
        self._run_notifier.startup_failed(
            feature, enabled=self._settings_page.notifications_enabled,
        )

    @Slot()
    def _finish_deferred_close(self) -> None:
        if self._close_requested and not self._is_busy() and not self._reporting_controller.closing and not any(
            page.has_pending_actions() for page in self._desktop_action_pages
        ):
            self.close()

    @Slot(bool)
    def _set_dialog_visible(self, visible: bool) -> None:
        backdrop = self._dialog_backdrop
        backdrop.setGeometry(backdrop.parentWidget().rect())
        backdrop.setVisible(visible)
        if visible:
            backdrop.raise_()
        self._title_bar.back_button.setEnabled(not visible and not self._is_busy())

    def eventFilter(self, watched, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and watched.objectName() == "appBody":
            self._dialog_backdrop.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def nativeEvent(self, event_type, message):
        frame = getattr(self, "_native_frame", None)
        if frame is not None:
            result = frame.handle_message(int(message))
            if result is not None:
                return result
        return super().nativeEvent(event_type, message)

    def showEvent(self, event: QShowEvent) -> None:
        if self._native_frame is not None:
            self._native_frame.attach(int(self.winId()))
        super().showEvent(event)
        self._update_controller.start()
        self._announcement_controller.start()

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
        self._announcement_controller.cancel()
        self._update_installer.cancel()
        if self._update_installer.launching:
            self._close_requested = True
            event.ignore()
            return
        self._update_controller.cancel()
        self._reporting_controller.cancel()
        if self._reporting_controller.closing:
            self._close_requested = True
            event.ignore()
            return
        for page in self._desktop_action_pages:
            page.cancel_pending_actions()
        if self._preparation_thread is not None:
            self._close_requested = True
            self._preparation_thread.cancel()
            self._active_run_page.show_status("正在取消准备，请稍候…")
            event.ignore()
            return
        if any(page.has_pending_actions() for page in self._desktop_action_pages):
            self._close_requested = True
            event.ignore()
            return
        if not self._dialog_host.close_dialogs():
            event.ignore()
            return
        self._overlay.close()
        super().closeEvent(event)
