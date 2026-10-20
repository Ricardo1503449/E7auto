from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QLinearGradient,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShowEvent,
    QValidator,
)
from PySide6.QtWidgets import QAbstractButton, QDialog, QGraphicsDropShadowEffect, QPushButton, QSizePolicy, QWidget


_MAX_RUN_LIMIT = 2_147_483_647


def _use_smooth_text(widget: QWidget) -> None:
    """Use symmetric DirectWrite smoothing without changing text size or color."""
    font = widget.font()
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    widget.setFont(font)


class AppDialog(QDialog):
    """Start on the container; activate buttons only after explicit focus or click."""

    def __init__(self, parent: QWidget | None = None, flags: Qt.WindowType = Qt.WindowType.Dialog):
        super().__init__(parent, flags)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def showEvent(self, event: QShowEvent) -> None:
        if not event.spontaneous():
            for button in self.findChildren(QPushButton):
                button.setAutoDefault(False)
                button.setDefault(False)
        super().showEvent(event)
        if not event.spontaneous():
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused = self.focusWidget()
            if isinstance(focused, QPushButton) and focused.hasFocus() and focused.isEnabled():
                focused.click()
            event.accept()
            return
        super().keyPressEvent(event)


class _IntegerLimitValidator(QValidator):
    def __init__(self, parent=None, *, minimum: int = 0):
        super().__init__(parent)
        self._minimum = minimum

    def validate(
        self,
        input_text: str,
        position: int,
    ) -> tuple[QValidator.State, str, int]:
        if input_text == "":
            return QValidator.State.Intermediate, input_text, position
        if not input_text.isascii() or not input_text.isdecimal():
            return QValidator.State.Invalid, input_text, position
        if self._minimum <= int(input_text) <= _MAX_RUN_LIMIT:
            return QValidator.State.Acceptable, input_text, position
        return QValidator.State.Invalid, input_text, position


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
    def __init__(
        self, parent: QWidget | None = None, *,
        object_name: str = "friendshipPointsToggle",
        accessible_name: str = "购买友情点数",
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setAccessibleName(accessible_name)
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
        self._scaled_pixmap_key: tuple[int, int, float] | None = None
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
        dpr = self.devicePixelRatioF()
        scale = max(
            rect.width() / self._pixmap.width(),
            rect.height() / self._pixmap.height(),
        ) * self._IMAGE_ZOOM * dpr
        scaled_size = QSize(
            max(1, ceil(self._pixmap.width() * scale)),
            max(1, ceil(self._pixmap.height() * scale)),
        )
        key = (scaled_size.width(), scaled_size.height(), dpr)
        if key != self._scaled_pixmap_key:
            self._scaled_pixmap = self._pixmap.scaled(
                scaled_size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled_pixmap.setDevicePixelRatio(dpr)
            self._scaled_pixmap_key = key
        scaled = self._scaled_pixmap
        source_width, source_height = rect.width() * dpr, rect.height() * dpr
        source_x = min(
            max(0.0, scaled.width() * self._focal_point.x() - source_width / 2),
            max(0.0, scaled.width() - source_width),
        )
        source_y = min(
            max(0.0, scaled.height() * self._focal_point.y() - source_height / 2),
            max(0.0, scaled.height() - source_height),
        )
        painter.drawPixmap(
            rect,
            scaled,
            QRectF(source_x, source_y, source_width, source_height),
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
