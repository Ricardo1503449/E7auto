from __future__ import annotations

import ctypes
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import win32con
import win32gui
from PySide6.QtCore import (
    QElapsedTimer,
    QObject,
    QPoint,
    QRect,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QCloseEvent, QMouseEvent, QValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .automation import AutomationDependencies, AutomationSession, SystemClock
from .config import AppConfig, ConfigError, LoggingConfig, Point, Rect, TargetConfig, load_config
from .domain import OverlayActivityStatus, RunState, RuntimeSnapshot, StopReason
from .platform_windows import (
    MssCaptureService,
    WDA_EXCLUDEFROMCAPTURE,
    Win32F5HotkeyService,
    Win32InputService,
    Win32RuntimeEnvironment,
    Win32WindowService,
    exclude_window_from_capture,
    get_window_display_affinity,
)
from .overlay import OverlaySecurityReport, evaluate_overlay_security
from .overlay_position import OverlayPositionStore, SavedOverlayPosition
from .run_logging import RunLogManager
from .vision import OpenCvGameVision, TemplateRepository


_MIN_REFRESH_LIMIT = 0
_MAX_REFRESH_LIMIT = 2_147_483_647


class _RefreshLimitValidator(QValidator):
    def validate(
        self,
        input_text: str,
        position: int,
    ) -> tuple[QValidator.State, str, int]:
        if input_text == "":
            return QValidator.State.Intermediate, input_text, position
        if not input_text.isascii() or not input_text.isdecimal():
            return QValidator.State.Invalid, input_text, position
        if _MIN_REFRESH_LIMIT <= int(input_text) <= _MAX_REFRESH_LIMIT:
            return QValidator.State.Acceptable, input_text, position
        return QValidator.State.Invalid, input_text, position


@dataclass(slots=True)
class OverlayCommand:
    client_bounds: Rect
    recognition_rois: tuple[Rect, ...]
    completed: threading.Event
    result: bool = False
    security_report: OverlaySecurityReport | None = None


@dataclass(slots=True)
class OverlayMoveCommand:
    begin: bool
    completed: threading.Event
    result: bool = False


class StatsOverlay(QWidget):
    command_requested = Signal(object)
    _MAX_DISPLAY_DIGITS = 7
    _FONT_SIZE_PX = 18
    _SECTION_GAP_PX = _FONT_SIZE_PX

    def __init__(self, position_store: OverlayPositionStore | None = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWindowOpacity(0.82)
        self._offset = QPoint(0, 0)
        self._position_store = position_store
        self._client_bounds: Rect | None = None
        self._recognition_rois: tuple[Rect, ...] = ()
        self._moving = False
        self._position_calibration_origin: QPoint | None = None
        self._position_calibration_drag_delta: QPoint | None = None
        self._target_labels: dict[str, QLabel] = {}
        self._target_layout = QVBoxLayout()
        self._target_layout.setContentsMargins(0, 0, 0, 0)
        self._elapsed = QLabel("已耗时：0时0分0秒")
        self._elapsed.setObjectName("elapsedTime")
        self._elapsed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._elapsed_clock = QElapsedTimer()
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(250)
        self._elapsed_timer.timeout.connect(self._update_elapsed)
        self._currency = QLabel()
        self._currency.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_target = QLabel("已经0次未出货")
        self._no_target.setObjectName("noTargetRefreshes")
        self._no_target.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status = QLabel("当前状态：已启动")
        self._status.setObjectName("activityStatus")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint = QLabel("F5结束 / F6移动")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._panel = QWidget(self)
        self._panel.setObjectName("panel")
        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.addWidget(self._elapsed)
        # Keep the elapsed-time line visually separated from the target rows.
        layout.addSpacing(self._SECTION_GAP_PX)
        layout.addLayout(self._target_layout)
        layout.addWidget(self._currency)
        # Keep the spent-currency line visually separated from the no-target streak.
        layout.addSpacing(self._SECTION_GAP_PX)
        layout.addWidget(self._no_target)
        layout.addWidget(self._status)
        layout.addWidget(self._hint)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._panel)
        self.setStyleSheet(
            "#panel { background: rgba(18, 22, 30, 210); border: 1px solid rgba(255,255,255,70); "
            f"border-radius: 8px; }} QLabel {{ color: white; font-size: {self._FONT_SIZE_PX}px; }}"
        )
        self.command_requested.connect(self._apply_command, Qt.ConnectionType.QueuedConnection)

    def configure(self, config: AppConfig) -> None:
        self._configure_display(
            config.targets,
            QPoint(config.overlay_offset.x, config.overlay_offset.y),
        )

    def _configure_display(
        self,
        targets: Sequence[TargetConfig],
        offset: QPoint,
    ) -> None:
        self._offset = offset
        self._configure_target_labels(targets)
        self._lock_production_size(targets)

    def _configure_target_labels(self, targets: Sequence[TargetConfig]) -> None:
        for label in self._target_labels.values():
            self._target_layout.removeWidget(label)
            label.deleteLater()
        self._target_labels.clear()
        for target in targets:
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._target_labels[target.target_id] = label
            self._target_layout.addWidget(label)

    @classmethod
    def _widest_digit_run(cls, label: QLabel) -> str:
        label.ensurePolished()
        metrics = label.fontMetrics()
        widest_digit = max(
            "0123456789",
            key=lambda digit: metrics.horizontalAdvance(digit),
        )
        return widest_digit * cls._MAX_DISPLAY_DIGITS

    def _lock_production_size(self, targets: Sequence[TargetConfig]) -> None:
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16_777_215, 16_777_215)

        for target in targets:
            label = self._target_labels[target.target_id]
            label.setText(
                f"{target.display_name}：{self._widest_digit_run(label)}"
            )
        maximum_currency = self._widest_digit_run(self._currency)
        maximum_elapsed = self._widest_digit_run(self._elapsed)
        self._elapsed.setText(f"已耗时：{maximum_elapsed}时59分59秒")
        self._currency.setText(
            f"已消耗天空石：{maximum_currency} / {maximum_currency}"
        )
        self._no_target.setText(
            f"已经{self._widest_digit_run(self._no_target)}次未出货"
        )
        self._status.ensurePolished()
        self._status.setText(
            max(
                (
                    f"当前状态：{OverlayActivityStatus.STARTED.value}",
                    f"当前状态：{OverlayActivityStatus.REFRESHING.value}",
                    f"当前状态：{OverlayActivityStatus.TRANSFERRING.value}",
                    f"当前状态：{OverlayActivityStatus.RECONNECTING.value}",
                    f"当前状态：{OverlayActivityStatus.STOPPED.value}",
                ),
                key=self._status.fontMetrics().horizontalAdvance,
            )
        )

        self.ensurePolished()
        # Text and target-row replacements invalidate nested Qt layouts lazily.
        # On a second run, adjustSize() can otherwise reuse the previous run's
        # smaller size hint and permanently lock the overlay to that width.
        self._target_layout.invalidate()
        panel_layout = self._panel.layout()
        if panel_layout is not None:
            panel_layout.invalidate()
            panel_layout.activate()
        outer_layout = self.layout()
        if outer_layout is not None:
            outer_layout.invalidate()
            outer_layout.activate()
        self.adjustSize()
        self.setFixedSize(self.size())

        for target in targets:
            self._target_labels[target.target_id].setText(
                f"{target.display_name}：0"
            )
        self._elapsed.setText("已耗时：0时0分0秒")
        self._currency.setText("已消耗天空石：0 / 0")
        self._no_target.setText("已经0次未出货")
        self._status.setText("当前状态：已启动")
        self._hint.setText("F5结束 / F6移动")

    def start_elapsed_timer(self) -> None:
        self._elapsed_clock.start()
        self._elapsed.setText("已耗时：0时0分0秒")
        self._elapsed_timer.start()

    def stop_elapsed_timer(self) -> None:
        if self._elapsed_clock.isValid():
            self._update_elapsed()
        self._elapsed_timer.stop()

    @Slot()
    def _update_elapsed(self) -> None:
        if not self._elapsed_clock.isValid():
            return
        text = self._format_elapsed(self._elapsed_clock.elapsed() // 1000)
        if text != self._elapsed.text():
            self._elapsed.setText(text)

    @staticmethod
    def _format_elapsed(total_seconds: int) -> str:
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"已耗时：{hours}时{minutes}分{seconds}秒"

    def update_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        for tally in snapshot.targets:
            label = self._target_labels.get(tally.target_id)
            if label is not None:
                label.setText(f"{tally.display_name}：{tally.acquired}")
        self._currency.setText(
            f"已消耗天空石：{snapshot.refresh_spent} / {snapshot.refresh_limit}"
        )
        self._no_target.setText(
            f"已经{snapshot.refreshes_without_mandatory_target}次未出货"
        )
        self._status.setText(f"当前状态：{snapshot.overlay_status.value}")
        self._hint.setText("F5结束 / F6移动")

    def begin_position_calibration(
        self,
        targets: Sequence[TargetConfig],
        client_bounds: Rect,
    ) -> None:
        """Show the production overlay in an explicitly draggable calibration mode."""

        self._configure_display(targets, QPoint(0, 0))
        self._position_calibration_origin = QPoint(client_bounds.x, client_bounds.y)
        self._position_calibration_drag_delta = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.move(client_bounds.x, client_bounds.y)
        self.show()
        self.raise_()

    def position_calibration_offset(self) -> QPoint:
        if self._position_calibration_origin is None:
            raise RuntimeError("Position calibration is not active")
        return self.frameGeometry().topLeft() - self._position_calibration_origin

    def finish_position_calibration(self) -> None:
        self._position_calibration_origin = None
        self._position_calibration_drag_delta = None
        self.unsetCursor()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _saved_position(self) -> QPoint | None:
        saved = self._position_store.load() if self._position_store is not None else None
        if saved is None:
            return None
        candidate = QRect(saved.x, saved.y, self.width(), self.height())
        if not any(
            candidate.intersects(screen.availableGeometry())
            for screen in self.screen().virtualSiblings()
        ):
            return None
        return QPoint(saved.x, saved.y)

    def begin_capture_validation(
        self,
        targets: Sequence[TargetConfig],
        offset: QPoint,
        client_bounds: Rect,
        recognition_rois: tuple[Rect, ...],
    ) -> OverlaySecurityReport:
        """Apply the exact production placement/security path on the GUI thread."""

        self._configure_display(targets, offset)
        command = OverlayCommand(client_bounds, recognition_rois, threading.Event())
        self._apply_command(command)
        if command.security_report is None:
            raise RuntimeError("Overlay capture security did not produce a report")
        return command.security_report

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            (self._position_calibration_origin is not None or self._moving)
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._position_calibration_drag_delta = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            (self._position_calibration_origin is not None or self._moving)
            and self._position_calibration_drag_delta is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(
                event.globalPosition().toPoint()
                - self._position_calibration_drag_delta
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            (self._position_calibration_origin is not None or self._moving)
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._position_calibration_drag_delta = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def position_and_secure(self, client_bounds: Rect, recognition_rois: tuple[Rect, ...]) -> bool:
        command = OverlayCommand(client_bounds, recognition_rois, threading.Event())
        self.command_requested.emit(command)
        if not command.completed.wait(timeout=3.0):
            return False
        return command.result

    def begin_move(self) -> bool:
        command = OverlayMoveCommand(True, threading.Event())
        self.command_requested.emit(command)
        return command.completed.wait(timeout=3.0) and command.result

    def finish_move(self) -> bool:
        command = OverlayMoveCommand(False, threading.Event())
        self.command_requested.emit(command)
        return command.completed.wait(timeout=3.0) and command.result

    @Slot(object)
    def _apply_command(self, command: OverlayCommand | OverlayMoveCommand) -> None:
        if isinstance(command, OverlayMoveCommand):
            self._apply_move_command(command)
            return
        try:
            self._client_bounds = command.client_bounds
            self._recognition_rois = command.recognition_rois
            saved = self._saved_position()
            self.move(
                saved
                if saved is not None
                else QPoint(
                    command.client_bounds.x + self._offset.x(),
                    command.client_bounds.y + self._offset.y(),
                )
            )
            self.show()
            self.raise_()
            hwnd = int(self.winId())
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(
                hwnd,
                win32con.GWL_EXSTYLE,
                ex_style | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_LAYERED,
            )
            excluded = exclude_window_from_capture(hwnd)
            affinity = get_window_display_affinity(hwnd)
            geometry = self.frameGeometry()
            overlay_rect = Rect(
                geometry.x(), geometry.y(), geometry.width(), geometry.height()
            )
            report = evaluate_overlay_security(
                excluded,
                affinity,
                WDA_EXCLUDEFROMCAPTURE,
                overlay_rect,
                command.client_bounds,
                command.recognition_rois,
            )
            command.security_report = report
            command.result = report.capture_excluded
        finally:
            command.completed.set()

    def _apply_move_command(self, command: OverlayMoveCommand) -> None:
        try:
            hwnd = int(self.winId())
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if command.begin:
                win32gui.SetWindowLong(
                    hwnd,
                    win32con.GWL_EXSTYLE,
                    ex_style & ~win32con.WS_EX_TRANSPARENT,
                )
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                self._moving = True
                self.raise_()
                command.result = True
                return

            self._moving = False
            self._position_calibration_drag_delta = None
            self.unsetCursor()
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            win32gui.SetWindowLong(
                hwnd,
                win32con.GWL_EXSTYLE,
                ex_style | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_LAYERED,
            )
            excluded = exclude_window_from_capture(hwnd)
            affinity = get_window_display_affinity(hwnd)
            command.result = excluded and affinity == WDA_EXCLUDEFROMCAPTURE
            if command.result and self._position_store is not None:
                top_left = self.frameGeometry().topLeft()
                try:
                    self._position_store.save(
                        SavedOverlayPosition(top_left.x(), top_left.y())
                    )
                except OSError:
                    pass
        finally:
            command.completed.set()


class AutomationWorker(QObject):
    snapshot = Signal(object)
    finished = Signal(object)

    def __init__(
        self,
        config: AppConfig,
        refresh_limit: int,
        buy_friendship_points: bool,
        project_root: Path,
        overlay: StatsOverlay,
    ):
        super().__init__()
        self._config = config
        self._refresh_limit = refresh_limit
        self._buy_friendship_points = buy_friendship_points
        self._project_root = project_root
        self._overlay = overlay

    @Slot()
    def run(self) -> None:
        run_id = uuid.uuid4().hex[:12]
        logger = RunLogManager(self._project_root / "logs", self._config.logging).start(run_id)
        try:
            templates = TemplateRepository(self._config)
            vision = OpenCvGameVision(self._config, templates)
            dependencies = AutomationDependencies(
                windows=Win32WindowService(),
                capture=MssCaptureService(),
                inputs=Win32InputService(),
                overlay=self._overlay,
                vision=vision,
                clock=SystemClock(),
                logger=logger,
                runtime=Win32RuntimeEnvironment(),
            )
            session = AutomationSession(
                self._config,
                dependencies,
                Win32F5HotkeyService(),
                self.snapshot.emit,
            )
            enabled_optional = (
                frozenset({"friendship_points"})
                if self._buy_friendship_points
                else frozenset()
            )
            final = session.run(
                self._refresh_limit,
                run_id,
                enabled_optional_target_ids=enabled_optional,
            )
        except Exception as exc:
            logger.event("worker_setup_failed", error=repr(exc))
            logger.close()
            initial = RuntimeSnapshot.initial(
                run_id,
                tuple((target.target_id, target.display_name) for target in self._config.targets),
                self._refresh_limit,
            )
            final = initial.finalized(StopReason.INTERNAL_ERROR)
            self.snapshot.emit(final)
        self.finished.emit(final)


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path):
        super().__init__()
        self._project_root = project_root
        self._config_path = project_root / "config" / "internal.yaml"
        self._overlay = StatsOverlay(
            OverlayPositionStore(project_root / "state" / "overlay_position.json")
        )
        self._thread: QThread | None = None
        self._worker: AutomationWorker | None = None
        self._entered_inventory = False

        self.setWindowTitle("E7auto")
        central = QWidget()
        layout = QVBoxLayout(central)
        limit_row = QHBoxLayout()
        self._limit_label = QLabel("刷新货币消耗上限")
        self._limit = QLineEdit("0")
        self._limit.setObjectName("refreshLimitInput")
        self._limit.setValidator(_RefreshLimitValidator(self._limit))
        self._limit.setMaxLength(len(str(_MAX_REFRESH_LIMIT)))
        self._limit.setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
        self._limit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._limit_label.setBuddy(self._limit)
        limit_row.addWidget(self._limit_label)
        limit_row.addWidget(self._limit, 1)
        self._friendship_points = QCheckBox("购买友情点数")
        self._friendship_points.setChecked(False)
        self._start = QPushButton("启动脚本")
        self._start.clicked.connect(self._start_run)
        self._limit.textChanged.connect(self._on_limit_text_changed)
        layout.addLayout(limit_row)
        layout.addWidget(self._friendship_points)
        layout.addWidget(self._start)
        self.setCentralWidget(central)
        self.setFixedSize(360, 140)

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
        self._thread = None
        self._worker = None
        self._start.setEnabled(self._validated_refresh_limit() is not None)
        if not self._entered_inventory:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None:
            event.ignore()
            return
        self._overlay.close()
        super().closeEvent(event)
