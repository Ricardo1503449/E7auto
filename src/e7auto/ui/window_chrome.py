from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QPushButton, QWidget


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
