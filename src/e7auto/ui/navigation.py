"""Shared navigation controls for the desktop shell."""
from __future__ import annotations

from math import cos, pi, sin

from .styles import COLORS
from .widgets import _use_smooth_text

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QPushButton, QVBoxLayout


class _NavigationButton(QPushButton):
    def __init__(self, symbol: str, label: str, object_name: str) -> None:
        super().__init__()
        _use_smooth_text(self)
        self._symbol = symbol
        self._update_available = False
        self.setObjectName(object_name)
        self.setProperty("navigationButton", True)
        self.setAccessibleName(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(44)

    def set_update_available(self, available: bool) -> None:
        self._update_available = available
        self.setAccessibleDescription("有新版本可用" if available else "")
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = COLORS["disabled"] if not self.isEnabled() else COLORS["icon"]
        painter.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.translate(22, self.height() / 2)
        if self._symbol == "menu":
            for y in (-6, 0, 6):
                painter.drawLine(QPointF(-8, y), QPointF(8, y))
        elif self._symbol == "back":
            painter.drawLine(QPointF(-8, 0), QPointF(8, 0))
            painter.drawPolyline(QPolygonF([QPointF(-2, -6), QPointF(-8, 0), QPointF(-2, 6)]))
        elif self._symbol == "grid":
            for x in (-8, 2):
                for y in (-8, 2):
                    painter.drawRoundedRect(QRectF(x, y, 6, 6), 1.4, 1.4)
        elif self._symbol == "about":
            painter.drawEllipse(QRectF(-9, -9, 18, 18))
            painter.drawPoint(QPointF(0, -4))
            painter.drawLine(QPointF(0, 0), QPointF(0, 5))
        elif self._symbol == "settings":
            points = []
            for index in range(32):
                angle = index * pi / 16
                radius = 9.5 if index % 4 in (1, 2) else 7.5
                points.append(QPointF(cos(angle) * radius, sin(angle) * radius))
            painter.drawPolygon(QPolygonF(points))
            painter.drawEllipse(QRectF(-3, -3, 6, 6))
        if self.isChecked() and self._symbol != "menu":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["accent"]))
            painter.drawRoundedRect(QRectF(-22, -10, 3, 20), 1.5, 1.5)
        if self._update_available:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["update_dot"]))
            painter.drawEllipse(QRectF(8.5, -15.5, 7, 7))


class _Sidebar(QFrame):
    page_requested = Signal(str)
    _COLLAPSED_WIDTH = 64
    _EXPANDED_WIDTH = 154

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 10, 18)
        layout.setSpacing(8)
        self.toggle_button = _NavigationButton("menu", "展开侧边栏", "sidebarToggleButton")
        self.toggle_button.clicked.connect(self._toggle_expanded)
        layout.addWidget(self.toggle_button)
        layout.addSpacing(12)
        self.buttons: dict[str, _NavigationButton] = {}
        for key, symbol, label in (
            ("home", "grid", "功能中心"),
            ("about", "about", "关于"),
            ("settings", "settings", "设置"),
        ):
            if key == "about":
                layout.addStretch(1)
            button = _NavigationButton(symbol, label, f"{key}NavigationButton")
            button.setCheckable(True)
            # Selection follows the accepted page change, including the run guard.
            button.setProperty("navigationPage", key)
            button.clicked.connect(self._request_page)
            self.buttons[key] = button
            layout.addWidget(button)
        self.set_expanded(False)
        self.set_current("home")

    def _request_page(self) -> None:
        self.page_requested.emit(self.sender().property("navigationPage"))

    def _toggle_expanded(self) -> None:
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        label = "收起侧边栏" if expanded else "展开侧边栏"
        self.toggle_button.setAccessibleName(label)
        self.setFixedWidth(self._EXPANDED_WIDTH if expanded else self._COLLAPSED_WIDTH)
        for button in self.buttons.values():
            button.setText(button.accessibleName() if expanded else "")

    def set_current(self, page: str) -> None:
        for key, button in self.buttons.items():
            button.setChecked(key == page)

    def set_navigation_enabled(self, enabled: bool) -> None:
        for button in self.buttons.values():
            button.setEnabled(enabled)
