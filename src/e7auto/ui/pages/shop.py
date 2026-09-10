from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..widgets import _MAX_REFRESH_LIMIT, _RefreshLimitValidator, _add_card_shadow, _ToggleSwitch


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
