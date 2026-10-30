"""A local startup notice, acknowledged once per announcement revision."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from e7auto.ui.preferences import AnnouncementPreferences
from e7auto.ui.widgets import AppDialog, _use_smooth_text


# Change this only when the announcement needs to be shown to existing users again.
ANNOUNCEMENT_ID = "usage-guide-v1"
QQ_GROUP_TEXT = "反馈交流QQ群：966214752"
ANNOUNCEMENT_MESSAGE = (
    "首次使用前，建议先阅读使用说明。\n"
    "查看位置：左侧「关于」→「使用说明」。\n"
    f"{QQ_GROUP_TEXT}"
)


class _AnnouncementDialog(AppDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("announcementDialog")
        self.setWindowTitle("公告")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(min(520, parent.width() - 48))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("announcementCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(24)
        self.heading = QLabel("公告")
        self.heading.setObjectName("announcementHeading")
        layout.addWidget(self.heading)
        self.message = QLabel(ANNOUNCEMENT_MESSAGE)
        self.message.setObjectName("announcementMessage")
        _use_smooth_text(self.message)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.confirm_button = QPushButton("我知道了")
        self.confirm_button.setObjectName("announcementConfirmButton")
        _use_smooth_text(self.confirm_button)
        self.confirm_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_button.setMinimumSize(104, 40)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)
        outer.addWidget(card)
        self.adjustSize()
        self.move(parent.mapToGlobal(parent.rect().center()) - self.rect().center())


class AnnouncementController(QObject):
    def __init__(self, preferences_path: Path, parent: QWidget) -> None:
        super().__init__(parent)
        self._preferences = AnnouncementPreferences(preferences_path)
        self._announcement_id = ANNOUNCEMENT_ID
        self._started = False
        self._cancelled = False
        self._dialog: _AnnouncementDialog | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show_if_unread)

    def start(self) -> None:
        if not self._started and not self._cancelled:
            self._started = True
            self._timer.start(0)

    def cancel(self) -> None:
        self._cancelled = True
        self._timer.stop()
        if self._dialog is not None:
            self._dialog.reject()

    def _show_if_unread(self) -> None:
        window = self.parent()
        if self._cancelled or not window.isVisible():
            return
        if self._preferences.load() == self._announcement_id:
            return
        self._dialog = _AnnouncementDialog(window)
        self._dialog.finished.connect(self._dismissed)
        self._dialog.open()

    def _dismissed(self, _result: int) -> None:
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog.deleteLater()
        if not self._cancelled:
            try:
                self._preferences.save(self._announcement_id)
            except OSError:
                # Closing remains available; a later launch can show the unread notice.
                pass
