from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
import threading

from PySide6.QtCore import (
    QElapsedTimer,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
import win32con
import win32gui

from ..config import AppConfig, Point, Rect, TargetConfig
from ..domain import OverlayActivityStatus, RuntimeSnapshot
from ..overlay_position import OverlayPositionStore, SavedOverlayPosition


@dataclass(slots=True)
class OverlayCommand:
    client_bounds: Rect
    completed: threading.Event
    offset: Point | None = None
    result: bool = False


class _OverlayCloseButton(QPushButton):
    _ICON_STROKE_WIDTH = 1.6

    def __init__(self, parent: QWidget) -> None:
        super().__init__("", parent)
        self.setObjectName("overlayCloseButton")
        self.setAccessibleName("关闭悬浮窗")
        self.setToolTip("关闭悬浮窗")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(32, 32)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(QPointF(self.rect().center()))
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(self._ICON_STROKE_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(-7.0, -7.0), QPointF(7.0, 7.0))
        painter.drawLine(QPointF(7.0, -7.0), QPointF(-7.0, 7.0))


class _CollapsedOverlayIcon(QWidget):
    def __init__(self, logo_path: Path | None, diameter: int, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("collapsedOverlayIcon")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(diameter, diameter)
        self._logo = QPixmap(str(logo_path)) if logo_path is not None else QPixmap()
        self._scaled_logo = QPixmap()
        self._scaled_logo_key: tuple[int, float] | None = None

    def _logo_for_dpr(self, logical_side: int, dpr: float) -> QPixmap:
        physical_side = max(1, round(logical_side * dpr))
        key = (physical_side, dpr)
        if key != self._scaled_logo_key:
            self._scaled_logo = self._logo.scaled(
                physical_side,
                physical_side,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled_logo.setDevicePixelRatio(dpr)
            self._scaled_logo_key = key
        return self._scaled_logo

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        target = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        circle = QPainterPath()
        circle.addEllipse(target)

        painter.save()
        painter.setClipPath(circle)
        painter.fillPath(circle, QColor("#12161e"))
        if not self._logo.isNull():
            logical_side = round(max(target.width(), target.height()))
            scaled = self._logo_for_dpr(logical_side, self.devicePixelRatioF())
            painter.drawPixmap(target.topLeft(), scaled)
        painter.restore()

        border = QPen(QColor(255, 255, 255, 150))
        border.setWidthF(1.5)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(target)


class StatsOverlay(QWidget):
    command_requested = Signal(object)
    _MAX_DISPLAY_DIGITS = 7
    _FONT_SIZE_PX = 18
    _SECTION_GAP_PX = _FONT_SIZE_PX
    _COLLAPSED_DIAMETER_PX = 68
    _ACCENT_GREEN = "#26985a"
    _TARGET_TEXT_GREEN = "#04d86a"
    _GREEN_TARGET_IDS = frozenset({"covenant_bookmark", "mystic_medal"})

    def __init__(
        self,
        position_store: OverlayPositionStore | None = None,
        logo_path: Path | None = None,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setWindowOpacity(0.82)
        self._offset = QPoint(0, 0)
        self._position_store = position_store
        self._position_calibration_origin: QPoint | None = None
        self._position_calibration_drag_delta: QPoint | None = None
        self._drag_press_global: QPoint | None = None
        self._drag_moved = False
        self._collapsed = False
        self._expanded_size = QSize()
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
        self._hint = QLabel("F5结束")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for label in (
            self._elapsed,
            self._currency,
            self._no_target,
            self._status,
            self._hint,
        ):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._panel = QWidget(self)
        self._panel.setObjectName("panel")
        self._panel.setCursor(Qt.CursorShape.SizeAllCursor)
        self._collapse_button = QPushButton("收起", self._panel)
        self._collapse_button.setObjectName("overlayCollapseButton")
        self._collapse_button.setAccessibleName("收起悬浮窗")
        self._collapse_button.setToolTip("收起悬浮窗")
        self._collapse_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._collapse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        collapse_font = self._collapse_button.font()
        collapse_font.setPixelSize(self._FONT_SIZE_PX)
        self._collapse_button.setFont(collapse_font)
        self._collapse_button.setFixedSize(54, 32)
        self._collapse_button.clicked.connect(self._collapse_overlay)
        self._close_button = _OverlayCloseButton(self._panel)
        self._close_button.hide()
        self._close_button.clicked.connect(self._dismiss_stopped_overlay)
        self._dismissible = False
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
        self._collapsed_icon = _CollapsedOverlayIcon(
            logo_path,
            self._COLLAPSED_DIAMETER_PX,
            self,
        )
        self._collapsed_icon.hide()
        outer.addWidget(
            self._collapsed_icon,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        self.setStyleSheet(
            "#panel { background: rgba(18, 22, 30, 210); border: 1px solid rgba(255,255,255,70); "
            f"border-radius: 8px; }} QLabel {{ color: white; font-size: {self._FONT_SIZE_PX}px; }} "
            "QPushButton#overlayCloseButton { background: transparent; border: none; border-radius: 4px; } "
            "QPushButton#overlayCloseButton:hover { background: rgba(255,255,255,28); } "
            "QPushButton#overlayCloseButton:pressed { background: rgba(255,255,255,45); } "
            f"QPushButton#overlayCollapseButton {{ color: white; font-size: {self._FONT_SIZE_PX}px; "
            f"background: {self._ACCENT_GREEN}; border: none; border-radius: 4px; padding: 0; }} "
            "QPushButton#overlayCollapseButton:hover { background: #228a50; } "
            "QPushButton#overlayCollapseButton:pressed { background: #1d7846; }"
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
        self._set_dismissible(False)
        self._set_collapsed(False)
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
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            if target.target_id in self._GREEN_TARGET_IDS:
                label.setStyleSheet(f"color: {self._TARGET_TEXT_GREEN};")
            self._target_labels[target.target_id] = label
            self._target_layout.addWidget(label)
            label.show()

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
        self._panel.show()
        self._collapsed_icon.hide()

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
        self._expanded_size = self.size()
        self.setFixedSize(self._expanded_size)
        self._position_corner_buttons()

        for target in targets:
            self._target_labels[target.target_id].setText(
                f"{target.display_name}：0"
            )
        self._elapsed.setText("已耗时：0时0分0秒")
        self._currency.setText("已消耗天空石：0 / 0")
        self._no_target.setText("已经0次未出货")
        self._status.setText("当前状态：已启动")
        self._hint.setText("F5结束")

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
        self._hint.setText("F5结束")
        self._set_dismissible(snapshot.is_final)

    def _position_corner_buttons(self) -> None:
        self._collapse_button.move(4, 4)
        self._close_button.move(
            max(0, self._panel.width() - self._close_button.width() - 4),
            4,
        )
        self._collapse_button.raise_()
        self._close_button.raise_()

    def _set_dismissible(self, enabled: bool) -> None:
        self._dismissible = enabled
        self._close_button.setVisible(enabled and self.isVisible())
        if enabled:
            self._position_corner_buttons()

    @Slot()
    def _collapse_overlay(self) -> None:
        self._set_collapsed(True)
        self._save_position()

    def _set_collapsed(self, collapsed: bool) -> None:
        if collapsed and self._position_calibration_origin is not None:
            return
        self._collapsed = collapsed
        if collapsed:
            self._panel.hide()
            self._collapsed_icon.show()
            self.setFixedSize(
                self._COLLAPSED_DIAMETER_PX,
                self._COLLAPSED_DIAMETER_PX,
            )
            self.setWindowOpacity(1.0)
            self.setAccessibleName("E7auto 悬浮图标，单击展开")
            self.setToolTip("单击展开悬浮窗")
        else:
            self._collapsed_icon.hide()
            self._panel.show()
            if self._expanded_size.isValid():
                self.setFixedSize(self._expanded_size)
            self.setWindowOpacity(0.82)
            self.setAccessibleName("E7auto 悬浮窗")
            self.setToolTip("")
            self._collapse_button.show()
            self._close_button.setVisible(self._dismissible and self.isVisible())
            self._position_corner_buttons()

    @Slot()
    def _dismiss_stopped_overlay(self) -> None:
        if self._dismissible:
            self.hide()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._position_corner_buttons()

    def begin_position_calibration(
        self,
        targets: Sequence[TargetConfig],
        client_bounds: Rect,
    ) -> None:
        """Show the production overlay in an explicitly draggable calibration mode."""

        self._configure_display(targets, QPoint(0, 0))
        self._position_calibration_origin = QPoint(client_bounds.x, client_bounds.y)
        self._position_calibration_drag_delta = None
        self.move(client_bounds.x, client_bounds.y)
        self._set_collapsed(False)
        self._collapse_button.hide()
        self.show()
        self.raise_()

    def position_calibration_offset(self) -> QPoint:
        if self._position_calibration_origin is None:
            raise RuntimeError("Position calibration is not active")
        return self.frameGeometry().topLeft() - self._position_calibration_origin

    def finish_position_calibration(self) -> None:
        self._position_calibration_origin = None
        self._position_calibration_drag_delta = None
        self._collapse_button.show()

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

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._position_calibration_drag_delta = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self._drag_press_global = event.globalPosition().toPoint()
            self._drag_moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._position_calibration_drag_delta is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            if self._drag_press_global is not None:
                distance = (
                    event.globalPosition().toPoint() - self._drag_press_global
                ).manhattanLength()
                self._drag_moved = self._drag_moved or (
                    distance >= QApplication.startDragDistance()
                )
            if not self._drag_moved:
                event.accept()
                return
            self.move(
                event.globalPosition().toPoint()
                - self._position_calibration_drag_delta
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            self._position_calibration_drag_delta is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            was_dragged = self._drag_moved
            was_collapsed = self._collapsed
            self._position_calibration_drag_delta = None
            self._drag_press_global = None
            self._drag_moved = False
            if self._position_calibration_origin is None:
                if was_collapsed and not was_dragged:
                    self._set_collapsed(False)
                elif was_dragged:
                    self._save_position()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _save_position(self) -> None:
        if self._position_store is not None:
            top_left = self.frameGeometry().topLeft()
            try:
                self._position_store.save(
                    SavedOverlayPosition(top_left.x(), top_left.y())
                )
            except OSError:
                pass

    def position(
        self,
        client_bounds: Rect,
        offset: Point | None = None,
    ) -> bool:
        command = OverlayCommand(client_bounds, threading.Event(), offset)
        self.command_requested.emit(command)
        if not command.completed.wait(timeout=3.0):
            return False
        return command.result

    @Slot(object)
    def _apply_command(self, command: OverlayCommand) -> None:
        try:
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
                (ex_style & ~win32con.WS_EX_TRANSPARENT)
                | win32con.WS_EX_NOACTIVATE
                | win32con.WS_EX_LAYERED,
            )
            command.result = True
        finally:
            command.completed.set()
