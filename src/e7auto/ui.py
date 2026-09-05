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
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
    QValidator,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
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
    offset: Point | None = None
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

    def position_and_secure(
        self,
        client_bounds: Rect,
        recognition_rois: tuple[Rect, ...],
        offset: Point | None = None,
    ) -> bool:
        command = OverlayCommand(client_bounds, recognition_rois, threading.Event(), offset)
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
            if command.offset is not None:
                self._offset = QPoint(command.offset.x, command.offset.y)
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


def _add_card_shadow(widget: QWidget) -> None:
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(24)
    shadow.setOffset(0, 5)
    shadow.setColor(QColor(22, 28, 36, 28))
    widget.setGraphicsEffect(shadow)


@dataclass(frozen=True, slots=True)
class ModuleCardSpec:
    module_id: str
    title: str | None
    image_filename: str | None
    available: bool


class _ToggleSwitch(QAbstractButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("friendshipPointsToggle")
        self.setAccessibleName("购买友情点数")
        self.setCheckable(True)
        self.setChecked(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(58, 32)

    def sizeHint(self) -> QSize:
        return QSize(58, 32)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if not self.isEnabled():
            track_color = QColor("#c7cbd0")
        elif self.isChecked():
            track_color = QColor("#26985a")
        else:
            track_color = QColor("#aeb3b9")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, 15, 15)

        knob_size = 24.0
        knob_x = track.right() - knob_size - 3 if self.isChecked() else track.left() + 3
        knob = QRectF(knob_x, track.top() + 3, knob_size, knob_size)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(knob)

class _ModuleCard(QAbstractButton):
    _IMAGE_ZOOM = 1.0
    _CARD_RADIUS = 14.0

    def __init__(
        self,
        *,
        title: str | None,
        image_path: Path | None,
        available: bool,
        object_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._pixmap = QPixmap(str(image_path)) if image_path is not None else QPixmap()
        self._scaled_pixmap = QPixmap()
        self._scaled_pixmap_size = QSize()
        self._focal_point = QPointF(0.5, 0.5)
        self.setObjectName(object_name)
        self.setAccessibleName(title or "开发中")
        self.setEnabled(available)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if available
            else Qt.CursorShape.ArrowCursor
        )
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(480, 220)

    def _card_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(4.0, 2.0, -4.0, -12.0)

    def _draw_rounded_shadow(self, painter: QPainter, card_rect: QRectF) -> None:
        for spread, alpha in ((8.0, 2), (6.0, 3), (4.0, 4), (2.0, 6), (0.0, 8)):
            shadow_rect = card_rect.translated(0.0, 5.0).adjusted(
                -spread,
                -spread,
                spread,
                spread,
            )
            shadow = QPainterPath()
            shadow.addRoundedRect(
                shadow_rect,
                self._CARD_RADIUS + spread,
                self._CARD_RADIUS + spread,
            )
            painter.fillPath(shadow, QColor(22, 28, 36, alpha))

    def _draw_cover_pixmap(self, painter: QPainter, rect: QRectF) -> None:
        if self._pixmap.isNull():
            painter.fillRect(rect, QColor("#25282c"))
            return
        scale = max(
            rect.width() / self._pixmap.width(),
            rect.height() / self._pixmap.height(),
        ) * self._IMAGE_ZOOM
        scaled_size = QSize(
            max(1, round(self._pixmap.width() * scale)),
            max(1, round(self._pixmap.height() * scale)),
        )
        if scaled_size != self._scaled_pixmap_size:
            self._scaled_pixmap = self._pixmap.scaled(
                scaled_size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled_pixmap_size = scaled_size
        scaled = self._scaled_pixmap
        source_x = min(
            max(0.0, scaled.width() * self._focal_point.x() - rect.width() / 2),
            max(0.0, scaled.width() - rect.width()),
        )
        source_y = min(
            max(0.0, scaled.height() * self._focal_point.y() - rect.height() / 2),
            max(0.0, scaled.height() - rect.height()),
        )
        painter.drawPixmap(
            rect,
            scaled,
            QRectF(source_x, source_y, rect.width(), rect.height()),
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._card_rect()
        self._draw_rounded_shadow(painter, rect)
        path = QPainterPath()
        path.addRoundedRect(rect, self._CARD_RADIUS, self._CARD_RADIUS)
        painter.save()
        painter.setClipPath(path)

        if self.isEnabled():
            self._draw_cover_pixmap(painter, rect)
            gradient = QLinearGradient(rect.left(), 0, rect.left() + rect.width() * 0.72, 0)
            gradient.setColorAt(0.0, QColor(8, 10, 12, 224))
            gradient.setColorAt(0.58, QColor(8, 10, 12, 142))
            gradient.setColorAt(1.0, QColor(8, 10, 12, 0))
            painter.fillRect(rect, gradient)
            if self.underMouse():
                painter.fillRect(rect, QColor(255, 255, 255, 12))
            if self.isDown():
                painter.fillRect(rect, QColor(0, 0, 0, 26))
            font = QFont("Microsoft YaHei UI")
            font.setPixelSize(max(22, min(30, round(rect.height() * 0.11))))
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(
                QRectF(
                    rect.left() + 32,
                    rect.top(),
                    rect.width() * 0.56,
                    rect.height(),
                ),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._title or "",
            )
        else:
            painter.fillRect(rect, QColor("#ffffff"))
            label_font = QFont("Microsoft YaHei UI")
            label_font.setPixelSize(15)
            painter.setFont(label_font)
            metrics = painter.fontMetrics()
            text = "开发中"
            pill_width = metrics.horizontalAdvance(text) + 34
            pill = QRectF(
                rect.center().x() - pill_width / 2,
                rect.center().y() - 18,
                pill_width,
                36,
            )
            painter.setPen(QPen(QColor("#d2d5d9"), 1))
            painter.setBrush(QColor("#eef0f2"))
            painter.drawRoundedRect(pill, 9, 9)
            painter.setPen(QColor("#22262a"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#d0d3d7"), 1))
        painter.drawPath(path)


class _FunctionCenterPage(QWidget):
    module_requested = Signal(str)

    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("functionCenterPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(52, 36, 52, 42)
        layout.setSpacing(24)

        heading = QLabel("功能中心")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("moduleScrollArea")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setAutoFillBackground(False)
        self._scroll.viewport().setAutoFillBackground(False)

        self._card_host = QWidget()
        self._card_host.setObjectName("moduleCardHost")
        self._grid = QGridLayout(self._card_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(28)
        self._grid.setVerticalSpacing(28)
        self._scroll.setWidget(self._card_host)
        layout.addWidget(self._scroll, 1)

        specs = (
            ModuleCardSpec(
                "shop_refresh",
                "刷新秘密商店",
                "shop-card-background.png",
                True,
            ),
            ModuleCardSpec("future_1", None, None, False),
            ModuleCardSpec("future_2", None, None, False),
            ModuleCardSpec("future_3", None, None, False),
        )
        self._cards_by_id: dict[str, _ModuleCard] = {}
        self._cards: list[_ModuleCard] = []
        for index, spec in enumerate(specs):
            image_path = (
                project_root / "assets" / "ui" / spec.image_filename
                if spec.image_filename is not None
                else None
            )
            object_name = (
                "shopModuleCard"
                if spec.module_id == "shop_refresh"
                else f"futureModuleCard{index}"
            )
            card = _ModuleCard(
                title=spec.title,
                image_path=image_path,
                available=spec.available,
                object_name=object_name,
            )
            if spec.available:
                card.clicked.connect(
                    lambda _checked=False, module_id=spec.module_id: self.module_requested.emit(
                        module_id
                    )
                )
            self._cards_by_id[spec.module_id] = card
            self._cards.append(card)
        self.shop_card = self._cards_by_id["shop_refresh"]
        self._column_count = 0
        self._apply_columns(2)

    @property
    def cards(self) -> tuple[_ModuleCard, ...]:
        return tuple(self._cards)

    def _apply_columns(self, columns: int) -> None:
        if columns == self._column_count:
            return
        while self._grid.count():
            self._grid.takeAt(0)
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)
        for column in range(2):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        self._column_count = columns

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._apply_columns(1 if event.size().width() < 820 else 2)
        super().resizeEvent(event)


class _ShopFeaturePage(QWidget):
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("shopFeaturePage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(52, 28, 52, 42)
        outer.setSpacing(20)

        self.back_button = QPushButton("←  返回功能中心")
        self.back_button.setObjectName("backToModulesButton")
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.clicked.connect(self.back_requested.emit)
        outer.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignLeft)

        heading = QLabel("刷新秘密商店")
        heading.setObjectName("pageHeading")
        outer.addWidget(heading)

        scroll = QScrollArea()
        scroll.setObjectName("featureScrollArea")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setObjectName("featureContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(24)

        settings_card = QFrame()
        settings_card.setObjectName("settingsCard")
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(30, 26, 30, 30)
        settings_layout.setSpacing(24)
        settings_heading = QLabel("运行设置")
        settings_heading.setObjectName("cardHeading")
        settings_layout.addWidget(settings_heading)

        limit_row = QHBoxLayout()
        self.limit_label = QLabel("天空石消耗上限")
        self.limit_label.setObjectName("settingLabel")
        self.limit_input = QLineEdit("0")
        self.limit_input.setObjectName("refreshLimitInput")
        self.limit_input.setValidator(_RefreshLimitValidator(self.limit_input))
        self.limit_input.setMaxLength(len(str(_MAX_REFRESH_LIMIT)))
        self.limit_input.setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
        self.limit_input.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.limit_input.setFixedSize(260, 56)
        self.limit_label.setBuddy(self.limit_input)
        limit_row.addWidget(self.limit_label)
        limit_row.addStretch(1)
        limit_row.addWidget(self.limit_input)
        settings_layout.addLayout(limit_row)

        friendship_row = QHBoxLayout()
        friendship_label = QLabel("购买友情点数")
        friendship_label.setObjectName("settingLabel")
        self.friendship_toggle = _ToggleSwitch()
        friendship_row.addWidget(friendship_label)
        friendship_row.addStretch(1)
        friendship_row.addWidget(self.friendship_toggle)
        settings_layout.addLayout(friendship_row)

        self.start_button = QPushButton("启动脚本")
        self.start_button.setObjectName("startButton")
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setFixedHeight(68)
        settings_layout.addWidget(self.start_button)
        _add_card_shadow(settings_card)
        content_layout.addWidget(settings_card)

        shortcut_card = QFrame()
        shortcut_card.setObjectName("shortcutCard")
        shortcut_layout = QVBoxLayout(shortcut_card)
        shortcut_layout.setContentsMargins(30, 24, 30, 26)
        shortcut_layout.setSpacing(16)
        shortcut_heading = QLabel("运行快捷键")
        shortcut_heading.setObjectName("cardHeading")
        shortcut_layout.addWidget(shortcut_heading)
        shortcut_items = QHBoxLayout()
        shortcut_items.setSpacing(16)
        shortcut_items.addWidget(self._keycap("F5"))
        shortcut_items.addWidget(QLabel("结束脚本"))
        shortcut_items.addSpacing(28)
        shortcut_items.addWidget(self._keycap("F6"))
        shortcut_items.addWidget(QLabel("移动悬浮窗"))
        shortcut_items.addStretch(1)
        shortcut_layout.addLayout(shortcut_items)
        _add_card_shadow(shortcut_card)
        content_layout.addWidget(shortcut_card)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

    @staticmethod
    def _keycap(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("keycap")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(52, 44)
        return label


class _WindowControlButton(QPushButton):
    _VALID_CONTROL_TYPES = {"minimize", "maximize", "restore", "close"}
    _ICON_STROKE_WIDTH = 1.4
    _RESTORE_ICON_SIZE = 12

    def __init__(self, control_type: str, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        if control_type not in self._VALID_CONTROL_TYPES:
            raise ValueError(f"Unsupported window control type: {control_type}")
        self._control_type = control_type
        restore_family = (
            "Segoe Fluent Icons"
            if "Segoe Fluent Icons" in QFontDatabase.families()
            else "Segoe MDL2 Assets"
        )
        self._restore_font = QFont(restore_family)
        self._restore_font.setPixelSize(self._RESTORE_ICON_SIZE)

    def set_control_type(self, control_type: str) -> None:
        if control_type not in self._VALID_CONTROL_TYPES:
            raise ValueError(f"Unsupported window control type: {control_type}")
        if control_type == self._control_type:
            return
        self._control_type = control_type
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(QPointF(self.rect().center()))

        color = QColor("#111417")
        if not self.isEnabled():
            color = QColor("#8d9399")
        elif self._control_type == "close" and self.underMouse():
            color = QColor("#ffffff")

        pen = QPen(color)
        pen.setWidthF(self._ICON_STROKE_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self._control_type == "minimize":
            painter.drawLine(QPointF(-6.0, 3.0), QPointF(6.0, 3.0))
        elif self._control_type == "maximize":
            painter.drawRect(QRectF(-5.0, -5.0, 10.0, 10.0))
        elif self._control_type == "restore":
            painter.setFont(self._restore_font)
            painter.drawText(
                QRectF(-7.0, -7.0, 14.0, 14.0),
                Qt.AlignmentFlag.AlignCenter,
                "\ue923",
            )
        else:
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(-5.0, -5.0), QPointF(5.0, 5.0))
            painter.drawLine(QPointF(5.0, -5.0), QPointF(-5.0, 5.0))


class _HighDpiIconLabel(QLabel):
    def __init__(
        self,
        icon_path: Path,
        size: QSize,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon = QIcon(str(icon_path))
        self.setFixedSize(size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        device_pixel_ratio = self.devicePixelRatioF()
        self.setPixmap(self._icon.pixmap(self.size(), device_pixel_ratio))

    def showEvent(self, event: QEvent) -> None:
        super().showEvent(event)
        self._refresh_pixmap()

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            QTimer.singleShot(0, self._refresh_pixmap)
        return handled


class _TitleBar(QWidget):
    _HEIGHT = 50
    _ICON_SIZE = QSize(32, 32)
    _CONTROL_SIZE = QSize(44, 34)

    def __init__(
        self,
        window: QMainWindow,
        icon_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self.setObjectName("titleBar")
        self.setProperty("windowMaximized", False)
        self.setFixedHeight(self._HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 4, 6, 4)
        layout.setSpacing(8)

        icon_label = _HighDpiIconLabel(icon_path, self._ICON_SIZE)
        icon_label.setObjectName("titleBarIcon")
        icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title = QLabel("E7auto")
        title.setObjectName("windowTitle")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(icon_label)
        layout.addWidget(title)
        layout.addStretch(1)

        self._minimize = self._window_button(
            "minimize", "最小化", "minimizeButton"
        )
        self._maximize = self._window_button(
            "maximize", "最大化", "maximizeButton"
        )
        self._close = self._window_button("close", "关闭", "closeButton")
        self._minimize.clicked.connect(window.showMinimized)
        self._maximize.clicked.connect(self._toggle_maximized)
        self._close.clicked.connect(window.close)
        layout.addWidget(self._minimize)
        layout.addWidget(self._maximize)
        layout.addWidget(self._close)

    @staticmethod
    def _window_button(
        control_type: str,
        accessible_name: str,
        object_name: str,
    ) -> _WindowControlButton:
        button = _WindowControlButton(control_type)
        button.setObjectName(object_name)
        button.setProperty("windowControl", True)
        button.setAccessibleName(accessible_name)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.setFixedSize(_TitleBar._CONTROL_SIZE)
        return button

    def _toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def sync_maximize_state(self) -> None:
        maximized = self._window.isMaximized()
        self._maximize.set_control_type("restore" if maximized else "maximize")
        self._maximize.setAccessibleName("还原" if maximized else "最大化")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _ResizeHandle(QWidget):
    def __init__(
        self,
        window: QMainWindow,
        edges: Qt.Edge,
        cursor: Qt.CursorShape,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._edges = edges
        self.setObjectName("resizeHandle")
        self.setCursor(cursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemResize(self._edges):
                event.accept()
                return
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    _MINIMUM_SIZE = QSize(760, 560)
    _INITIAL_SIZE = QSize(1180, 760)

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
