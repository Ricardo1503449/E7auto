"""Presentation-only layout shared by independent feature pages."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..styles import CARD_MARGINS, CONTROL_SIZE, FORM_SPACING, PAGE_MARGINS
from ..widgets import _MAX_RUN_LIMIT, _IntegerLimitValidator, _add_card_shadow


class _FeaturePage(QWidget):
    def __init__(self, title: str, object_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*PAGE_MARGINS)
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
        content_layout.setSpacing(FORM_SPACING)
        card = QFrame()
        card.setObjectName("settingsCard")
        self.settings = QVBoxLayout(card)
        self.settings.setContentsMargins(*CARD_MARGINS)
        self.settings.setSpacing(FORM_SPACING)
        heading = QLabel(title)
        heading.setObjectName("applicationHeading")
        self.settings.addWidget(heading)
        _add_card_shadow(card)
        content_layout.addWidget(card)
        self.status_label = QLabel()
        self.status_label.setObjectName("settingsStatus")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        content_layout.addWidget(self.status_label)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

    def add_row(self, text: str, control: QWidget) -> QLabel:
        row = QHBoxLayout()
        label = QLabel(text)
        label.setObjectName("settingLabel")
        label.setBuddy(control)
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(control)
        self.settings.addLayout(row)
        return label

    def add_limit(self, text: str, name: str, *, default: int = 0, minimum: int = 0) -> None:
        self.limit_input = QLineEdit(str(default))
        self.limit_input.setObjectName(name)
        self.limit_input.setValidator(_IntegerLimitValidator(self.limit_input, minimum=minimum))
        self.limit_input.setMaxLength(len(str(_MAX_RUN_LIMIT)))
        self.limit_input.setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
        self.limit_input.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.limit_input.setFixedSize(*CONTROL_SIZE)
        self.add_row(text, self.limit_input)

    def add_start_button(self, name: str) -> None:
        self.start_button = QPushButton("启动脚本")
        self.start_button.setObjectName(name)
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setFixedHeight(68)
        self.settings.addWidget(self.start_button)

    def limit_value(self) -> int | None:
        return int(self.limit_input.text()) if self.limit_input.hasAcceptableInput() else None

    def show_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.setVisible(bool(message))
