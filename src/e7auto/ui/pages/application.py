"""Application-level about and settings pages."""
from __future__ import annotations

from pathlib import Path

from ..styles import COLORS, HEADING_SIZE, PAGE_MARGINS

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QTextCursor, QTextFormat
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QTextBrowser, QVBoxLayout, QWidget

from e7auto import __version__
from ..updates import PROJECT_URL, UpdateController
from ..desktop_actions import DesktopActionThread
from ..preferences import NotificationPreferences
from ..widgets import AppDialog, _ToggleSwitch, _use_smooth_text
from ..shop_reporting import ShopReportingController


class _UpdateLabel(QLabel):
    """Keep the reminder next to the title, independent of row width."""

    def __init__(self, title: str) -> None:
        super().__init__(title)
        self.update_available = False

    def sizeHint(self) -> QSize:
        return super().sizeHint() + QSize(12, 0)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.update_available:
            bounds = self.fontMetrics().boundingRect(self.contentsRect(), int(self.alignment()), self.text())
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["update_dot"]))
            painter.drawEllipse(QRectF(bounds.right() + 2, bounds.top(), 7, 7))


class _SettingsRow(QPushButton):
    action_requested = Signal()

    def __init__(self, title: str, object_name: str) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setProperty("settingsRow", True)
        self.setAccessibleName(title)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(76)
        self._busy = False
        self._update_available = False
        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(40)
        self._spinner_timer.timeout.connect(self._advance_spinner)
        self._action_timer = QTimer(self)
        self._action_timer.setSingleShot(True)
        # Give Qt a frame to paint the busy state before handing off to the OS.
        self._action_timer.setInterval(80)
        self._action_timer.timeout.connect(self.action_requested.emit)
        self.clicked.connect(self._queue_action)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 16, 52, 16)
        layout.setSpacing(20)
        label = _UpdateLabel(title) if object_name == "checkUpdatesButton" else QLabel(title)
        self._title_label = label
        label.setObjectName("settingLabel")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(label)
        layout.addStretch(1)

    def set_busy(self, busy: bool) -> None:
        if self._busy == busy:
            return
        self._busy = busy
        self.setEnabled(not busy)
        self._update_accessible_description()
        if busy:
            self._spinner_angle = 0
            self._spinner_timer.start()
        else:
            self._spinner_timer.stop()
            self._action_timer.stop()
        self.update()

    def set_update_available(self, available: bool) -> None:
        self._update_available = available
        if isinstance(self._title_label, _UpdateLabel):
            self._title_label.update_available = available
            self._title_label.update()
        self._update_accessible_description()

    def _update_accessible_description(self) -> None:
        descriptions = []
        if self._update_available:
            descriptions.append("有新版本可用")
        if self._busy:
            descriptions.append("正在处理，请稍候")
        self.setAccessibleDescription("；".join(descriptions))

    def _queue_action(self) -> None:
        self.set_busy(True)
        self._action_timer.start()

    def _advance_spinner(self) -> None:
        self._spinner_angle = (self._spinner_angle + 18) % 360
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#565f68" if self.isEnabled() else "#a2a7ad"), 1.8,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.translate(self.width() - 28, self.height() / 2)
        if self._busy:
            painter.drawArc(QRectF(-9, -9, 18, 18), -self._spinner_angle * 16, 260 * 16)
        else:
            painter.drawPolyline(QPolygonF([QPointF(-3, -5), QPointF(2, 0), QPointF(-3, 5)]))


class _UploadRetryButton(QPushButton):
    def __init__(self) -> None:
        super().__init__("重新上传")
        _use_smooth_text(self)
        self.setObjectName("shopStatisticsRetry")
        self.setAccessibleName("重新上传待传统计记录")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._busy = False
        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(40)
        self._spinner_timer.timeout.connect(self._advance_spinner)

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        metrics = self.fontMetrics()
        text_width = max(metrics.horizontalAdvance("重新上传"), metrics.horizontalAdvance("上传中…"))
        return QSize(size.width() + text_width - metrics.horizontalAdvance(self.text()) + 24, size.height())

    def set_busy(self, busy: bool) -> None:
        if self._busy == busy:
            return
        self._busy = busy
        self.setText("上传中…" if busy else "重新上传")
        self.setAccessibleDescription("正在上传，请稍候" if busy else "")
        self.setEnabled(not busy)
        if busy and self.isVisible():
            self._spinner_timer.start()
        else:
            self._spinner_timer.stop()
        self.update()

    def _advance_spinner(self) -> None:
        self._spinner_angle = (self._spinner_angle + 18) % 360
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._busy:
            self._spinner_timer.start()

    def hideEvent(self, event) -> None:
        self._spinner_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._busy:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(COLORS["primary"]), 1.8,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(self.width() - 21, self.height() / 2 - 7, 14, 14),
                            -self._spinner_angle * 16, 260 * 16)


class _ReleaseNotesView(QTextBrowser):
    def __init__(self, notes: str) -> None:
        super().__init__()
        _use_smooth_text(self)
        self.setObjectName("updateReleaseNotes")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setCursorWidth(0)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(180)
        self.setMarkdown(notes or "此版本未提供改动日志。")
        block = self.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() == 1:
                cursor = QTextCursor(block)
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                heading = cursor.charFormat()
                heading.clearProperty(QTextFormat.Property.FontSizeAdjustment)
                heading.setFontPointSize(HEADING_SIZE * 72 / self.logicalDpiY())
                cursor.setCharFormat(heading)
                spacing = block.blockFormat()
                spacing.setBottomMargin(15)
                cursor.setBlockFormat(spacing)
            block = block.next()

    def loadResource(self, resource_type, name):
        # Release text is display-only: never load linked files or images.
        return None


class _UpdateResultDialog(AppDialog):
    update_requested = Signal()

    def __init__(self, title: str, message: str, action: str | None, parent: QWidget,
                 *, notes: str | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self._install_launching = False
        self.setObjectName("updateResultDialog")
        self.setWindowTitle("检查更新")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(min(500, parent.width() - 48))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("updateResultCard")
        layout = QGridLayout(card)
        layout.setContentsMargins(28, 28, 4, 24)
        layout.setHorizontalSpacing(0)
        layout.setVerticalSpacing(24)
        layout.setColumnStretch(0, 1)
        layout.setColumnMinimumWidth(1, 24)
        self.heading = QLabel(title)
        self.heading.setObjectName("updateResultHeading")
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading, 0, 0)
        if notes is not None:
            self.notes_view = _ReleaseNotesView(notes)
            layout.addWidget(self.notes_view, 1, 0, 1, 2)
            layout.setRowStretch(1, 1)
        else:
            self.message = QLabel(message)
            _use_smooth_text(self.message)
            self.message.setObjectName("updateResultMessage")
            self.message.setTextFormat(Qt.TextFormat.PlainText)
            self.message.setWordWrap(True)
            layout.addWidget(self.message, 1, 0)
        self.install_status = QLabel()
        _use_smooth_text(self.install_status)
        self.install_status.setObjectName("updateInstallStatus")
        self.install_status.setTextFormat(Qt.TextFormat.PlainText)
        self.install_status.setWordWrap(True)
        self.install_status.hide()
        layout.addWidget(self.install_status, 2, 0)
        if action == "更新":
            self.set_install_status("下载后自动安装并重启，保留用户设置和记录。")
        self.download_progress = QProgressBar()
        self.download_progress.setObjectName("updateDownloadProgress")
        self.download_progress.setAccessibleName("安装包下载进度")
        self.download_progress.setRange(0, 100)
        self.download_progress.hide()
        layout.addWidget(self.download_progress, 3, 0)
        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        buttons.addStretch(1)
        self.close_button = QPushButton("关闭")
        _use_smooth_text(self.close_button)
        self.close_button.setObjectName("updateDialogCloseButton")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setMinimumSize(88, 40)
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        if action is not None:
            self.action_button = QPushButton(action)
            _use_smooth_text(self.action_button)
            self.action_button.setObjectName("updateDialogActionButton")
            self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_button.setMinimumSize(self.close_button.minimumSize())
            self.action_button.clicked.connect(self.update_requested if action == "更新" else self.accept)
            buttons.addWidget(self.action_button)
        layout.addLayout(buttons, 4, 0)
        outer.addWidget(card)
        if notes is None:
            self.adjustSize()
        else:
            self.resize(min(680, parent.width() - 48), min(500, parent.height() - 64))
        self.move(parent.mapToGlobal(parent.rect().center()) - self.rect().center())

    def reject(self) -> None:
        if not self._install_launching:
            super().reject()

    def set_install_busy(self, busy: bool) -> None:
        self.action_button.setEnabled(not busy)
        self.action_button.setText("下载中…" if busy else "更新")
        self.close_button.setText("取消" if busy else "关闭")
        if not busy:
            self.download_progress.hide()

    def set_install_status(self, message: str) -> None:
        self.install_status.setText(message)
        self.install_status.show()
        if hasattr(self, 'notes_view'):
            self.notes_view.setMinimumHeight(100)

    def set_download_progress(self, received: int, total: int) -> None:
        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(received * 100 // total if total else 0)
        self.download_progress.show()
        self.set_install_status(f"正在下载安装包：{received / 1048576:.1f} / {total / 1048576:.1f} MB")

    def set_install_launching(self, launching: bool) -> None:
        self._install_launching = launching
        self.close_button.setEnabled(not launching)
        if launching:
            self.action_button.setText("启动安装中…")
            self.download_progress.setRange(0, 0)


class _ApplicationPage(QWidget):
    actions_settled = Signal()

    def __init__(self, title: str, object_name: str) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self._open_tasks: dict[DesktopActionThread, _SettingsRow] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*PAGE_MARGINS)
        outer.setSpacing(24)
        self.heading = QLabel(title)
        self.heading.setObjectName("applicationHeading")
        scroll = QScrollArea()
        scroll.setObjectName("applicationScrollArea")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("applicationContent")
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(16)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)


    def _add_status_label(self) -> None:
        self.status_label = QLabel()
        self.status_label.setObjectName("settingsStatus")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        self.content_layout.addWidget(self.status_label)

    def _show_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def _open_target(self, button: _SettingsRow, target: str | Path, *, create_directory: bool = True) -> None:
        self._show_status("")
        task = DesktopActionThread(target, self, create_directory=create_directory)
        self._open_tasks[task] = button
        task.finished.connect(self._finish_open_tasks)
        task.start()

    def has_pending_actions(self) -> bool:
        return bool(self._open_tasks)

    def _finish_open_tasks(self) -> None:
        awaiting_cleanup = False
        for task, button in list(self._open_tasks.items()):
            if not task.wait(0):
                awaiting_cleanup = awaiting_cleanup or task.isFinished()
                continue
            del self._open_tasks[task]
            button.set_busy(False)
            if not task.cancelled.is_set():
                self._show_status(task.error)
            task.deleteLater()
        if awaiting_cleanup:
            QTimer.singleShot(10, self._finish_open_tasks)
        if not self._open_tasks:
            self.actions_settled.emit()

    def cancel_pending_actions(self) -> None:
        for task in self._open_tasks:
            task.cancel()
        for button in self.findChildren(_SettingsRow):
            if button not in self._open_tasks.values():
                button.set_busy(False)


class _SettingsPage(_ApplicationPage):
    update_dialog_visibility_changed = Signal(bool)
    update_requested = Signal(object)
    update_cancelled = Signal()
    notifications_changed = Signal(bool)

    def __init__(self, project_root: Path, update_controller: UpdateController,
                 reporting_controller: ShopReportingController | None = None) -> None:
        super().__init__("设置", "applicationSettingsPage")
        self._log_directory = project_root.resolve() / "logs"
        self._preferences = NotificationPreferences(project_root / "state" / "ui_preferences.json")
        self._update_dialog: _UpdateResultDialog | None = None
        self._update_controller = update_controller
        self.update_checker = update_controller.checker
        update_controller.completed.connect(self._update_completed)
        update_controller.failed.connect(self._update_failed)
        card = QFrame()
        card.setObjectName("applicationCard")
        rows = QVBoxLayout(card)
        rows.setContentsMargins(8, 8, 8, 8)
        rows.setSpacing(0)
        self.heading.setContentsMargins(24, 16, 24, 16)
        rows.addWidget(self.heading)
        self.logs_button = _SettingsRow("日志目录", "openLogsButton")
        self.logs_button.action_requested.connect(self._open_logs)
        self.github_button = _SettingsRow("GitHub 项目地址", "openGithubButton")
        self.github_button.action_requested.connect(self._open_project)
        self.update_button = _SettingsRow("检查更新", "checkUpdatesButton")
        self.update_button.action_requested.connect(self._check_updates)
        self.update_button.clicked.connect(self._join_background_check)
        self.update_button.set_update_available(update_controller.available)
        update_controller.availability_changed.connect(self.update_button.set_update_available)
        for button in (self.logs_button, self.github_button, self.update_button):
            rows.addWidget(button)
            line = QFrame()
            line.setObjectName("settingsDivider")
            line.setFixedHeight(1)
            rows.addWidget(line)
        notification_row = QWidget()
        notification_row.setObjectName("runEndNotificationsRow")
        notification_row.setMinimumHeight(76)
        notification_layout = QHBoxLayout(notification_row)
        notification_layout.setContentsMargins(24, 16, 24, 16)
        label = QLabel("运行结束通知")
        notification_layout.addWidget(label)
        notification_layout.addStretch(1)
        self.notifications_toggle = _ToggleSwitch(
            object_name="runEndNotificationsToggle", accessible_name="运行结束通知",
        )
        label.setBuddy(self.notifications_toggle)
        self.notifications_toggle.setChecked(self._preferences.load())
        self.notifications_toggle.toggled.connect(self._save_notifications)
        notification_layout.addWidget(self.notifications_toggle)
        rows.addWidget(notification_row)
        line = QFrame()
        line.setObjectName("settingsDivider")
        line.setFixedHeight(1)
        rows.addWidget(line)
        if reporting_controller is not None:
            self._reporting_controller = reporting_controller
            self._last_statistics_counts: tuple[int, int] | None = None
            self._statistics_success = False
            self._statistics_success_seen = False
            self._statistics_success_timer = QTimer(self)
            self._statistics_success_timer.setSingleShot(True)
            self._statistics_success_timer.setInterval(3000)
            self._statistics_success_timer.timeout.connect(self._clear_statistics_success)
            statistics = QWidget()
            statistics.setObjectName("shopStatisticsSettings")
            statistics_layout = QVBoxLayout(statistics)
            statistics_layout.setContentsMargins(24, 16, 24, 16)
            heading = QHBoxLayout()
            statistics_label = QLabel("参与刷商店匿名统计")
            heading.addWidget(statistics_label)
            heading.addStretch(1)
            self.statistics_toggle = _ToggleSwitch(
                object_name="shopStatisticsToggle", accessible_name="参与刷商店匿名统计")
            statistics_label.setBuddy(self.statistics_toggle)
            self.statistics_toggle.setChecked(reporting_controller.enabled)
            self.statistics_toggle.toggled.connect(self._save_statistics)
            heading.addWidget(self.statistics_toggle)
            statistics_layout.addLayout(heading)
            statistics_layout.addSpacing(12)
            self.statistics_pending = QLabel()
            _use_smooth_text(self.statistics_pending)
            self.statistics_pending.setObjectName("shopStatisticsPending")
            statistics_layout.addWidget(self.statistics_pending)
            self.statistics_status = QLabel()
            _use_smooth_text(self.statistics_status)
            self.statistics_status.setObjectName("shopStatisticsStatus")
            self.statistics_status.setWordWrap(True)
            self.statistics_status.hide()
            statistics_layout.addWidget(self.statistics_status)
            self.statistics_retry = _UploadRetryButton()
            self.statistics_retry.clicked.connect(reporting_controller.retry)
            statistics_layout.addWidget(self.statistics_retry, 0, Qt.AlignmentFlag.AlignLeft)
            reporting_controller.changed.connect(self._refresh_statistics)
            reporting_controller.status.connect(self._show_statistics_status)
            rows.addWidget(statistics)
            line = QFrame()
            line.setObjectName("settingsDivider")
            line.setFixedHeight(1)
            rows.addWidget(line)
            self._refresh_statistics()
        version_row = QWidget()
        version_row.setMinimumHeight(76)
        version_layout = QHBoxLayout(version_row)
        version_layout.setContentsMargins(24, 16, 24, 16)
        version_layout.addWidget(QLabel("当前版本号"))
        version_layout.addStretch(1)
        self.version_label = QLabel(f"v{__version__}")
        self.version_label.setObjectName("currentVersion")
        self.version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        version_layout.addWidget(self.version_label)
        rows.addWidget(version_row)
        self.content_layout.addWidget(card)
        self._add_status_label()
        self.content_layout.addStretch(1)

    @property
    def notifications_enabled(self) -> bool:
        return self.notifications_toggle.isChecked()

    def _save_notifications(self, enabled: bool) -> None:
        try:
            self._preferences.save(enabled)
        except OSError as exc:
            self.notifications_toggle.blockSignals(True)
            self.notifications_toggle.setChecked(not enabled)
            self.notifications_toggle.blockSignals(False)
            self._show_status(f"无法保存运行结束通知设置：{exc}")
            return
        self._show_status("")
        self.notifications_changed.emit(enabled)

    def _save_statistics(self, enabled: bool) -> None:
        try:
            self._reporting_controller.set_enabled(enabled)
        except Exception:
            self.statistics_toggle.blockSignals(True)
            self.statistics_toggle.setChecked(self._reporting_controller.enabled)
            self.statistics_toggle.blockSignals(False)
            self._show_statistics_status("统计设置暂时无法保存")
        self._refresh_statistics()

    def _show_statistics_status(self, message: str, success: bool = False) -> None:
        self._statistics_success_timer.stop()
        self._statistics_success = success and bool(message)
        self._statistics_success_seen = False
        self.statistics_status.setText(message)
        self.statistics_status.setVisible(bool(message))
        if self._statistics_success and self.isVisible():
            self._statistics_success_seen = True
            self._statistics_success_timer.start()

    def _clear_statistics_success(self) -> None:
        if self._statistics_success:
            self._statistics_success_timer.stop()
            self._statistics_success = False
            self._statistics_success_seen = False
            self.statistics_status.clear()
            self.statistics_status.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if getattr(self, "_statistics_success", False):
            self._statistics_success_seen = True
            self._statistics_success_timer.start()

    def hideEvent(self, event) -> None:
        if hasattr(self, "_statistics_success_timer"):
            self._statistics_success_timer.stop()
            if self._statistics_success_seen:
                self._clear_statistics_success()
        super().hideEvent(event)

    def _refresh_statistics(self) -> None:
        counts = self._reporting_controller.counts()
        if counts is None:
            if self._last_statistics_counts is None:
                text = "待上传记录：暂不可读取"
            else:
                pending, rejected = self._last_statistics_counts
                text = (f"上次待上传记录：{pending}"
                        + (f" · 服务端拒绝：{rejected}" if rejected else "")
                        + "（暂时无法刷新）")
            can_retry = True  # retry() re-reads the journal before dispatching an upload.
        else:
            self._last_statistics_counts = counts
            pending, rejected = counts
            text = f"待上传记录：{pending}" + (f" · 服务端拒绝：{rejected}" if rejected else "")
            can_retry = pending > rejected
        self.statistics_pending.setText(text)
        self.statistics_retry.set_busy(self._reporting_controller.uploading)
        self.statistics_retry.setEnabled(self._reporting_controller.enabled and can_retry
                                         and not self._reporting_controller.uploading)
        if not self._reporting_controller.endpoint:
            self._show_statistics_status("尚未配置统计服务地址，记录保存在本地")

    def _open_logs(self) -> None:
        self._open_target(self.logs_button, self._log_directory)

    def _open_project(self) -> None:
        self._open_target(self.github_button, PROJECT_URL)

    def _join_background_check(self) -> None:
        if self._update_controller.checking:
            # Join before the row's paint delay: completion may arrive within 80 ms.
            self.update_button._action_timer.stop()
            self._check_updates()

    def _check_updates(self) -> None:
        self._show_status("")
        self.update_button.set_busy(True)
        self._update_controller.check(manual=True)

    def _update_completed(self, version: str, available: bool, notes: str) -> None:
        self.update_button.set_busy(False)
        title = f"发现新版本 {version}" if available else "无需更新"
        message = f"当前版本：v{__version__}\n最新正式版：{version}"
        self._show_update_result(title, message, "更新" if available else None,
                                 notes=notes if available else None, asset=self._update_controller.asset)

    def _update_failed(self, message: str) -> None:
        self.update_button.set_busy(False)
        self._show_update_result("检查更新失败", message, "重试")

    def _show_update_result(self, title: str, message: str, action: str | None,
                            *, notes: str | None = None, asset=None) -> None:
        if self._update_dialog is not None:
            self._update_dialog.reject()
        dialog = _UpdateResultDialog(title, message, action, self.window(), notes=notes)
        self._update_dialog = dialog
        if action == "重试":
            dialog.accepted.connect(self._check_updates)
        elif action == "更新":
            dialog.release_asset = asset
            dialog.update_requested.connect(self._request_install)
            dialog.rejected.connect(self.update_cancelled)
        dialog.finished.connect(self._dispose_update_dialog)
        self.update_dialog_visibility_changed.emit(True)
        dialog.open()

    def _request_install(self) -> None:
        dialog = self.sender()
        if dialog is self._update_dialog:
            self.update_requested.emit(dialog.release_asset)

    def show_install_error(self, message: str) -> None:
        self.show_install_status(message)
        if self._update_dialog is not None:
            self._update_dialog.action_button.setText("重试更新")
        else:
            self._show_status(message)

    def show_install_status(self, message: str) -> None:
        if self._update_dialog is not None:
            self._update_dialog.set_install_status(message)

    def set_install_busy(self, busy: bool) -> None:
        if self._update_dialog is not None and hasattr(self._update_dialog, 'action_button'):
            self._update_dialog.set_install_busy(busy)

    def set_install_launching(self, launching: bool) -> None:
        if self._update_dialog is not None:
            self._update_dialog.set_install_launching(launching)

    def show_download_progress(self, received: int, total: int) -> None:
        if self._update_dialog is not None:
            self._update_dialog.set_download_progress(received, total)

    def _dispose_update_dialog(self) -> None:
        dialog = self.sender()
        if self._update_dialog is dialog:
            self._update_dialog = None
            self.update_dialog_visibility_changed.emit(False)
        dialog.deleteLater()

    def cancel_pending_actions(self) -> None:
        super().cancel_pending_actions()
        if self._update_dialog is not None:
            self._update_dialog.reject()


class _AboutPage(_ApplicationPage):
    def __init__(self, project_root: Path) -> None:
        super().__init__("关于", "aboutPage")
        card = QFrame()
        card.setObjectName("applicationCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        details = QVBoxLayout()
        details.setContentsMargins(24, 16, 24, 20)
        details.setSpacing(16)
        title_row = QHBoxLayout()
        title_row.setSpacing(0)
        title_row.addWidget(self.heading, 0, Qt.AlignmentFlag.AlignVCenter)
        title = QLabel("E7auto")
        title.setObjectName("aboutTitle")
        title_row.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        details.addLayout(title_row)
        version = QLabel(f"v{__version__}")
        version.setObjectName("aboutVersion")
        details.addWidget(version)
        description = QLabel("《第七史诗》自动化工具\n\n刷新秘密商店、红叶换企鹅、自动卡厄思。")
        description.setObjectName("aboutDescription")
        description.setWordWrap(True)
        details.addWidget(description)
        layout.addLayout(details)
        self.content_layout.addWidget(card)
        root = project_root.resolve()
        installed_guide = root / "使用说明.md"
        source_guide = root / "docs" / "user" / "使用说明.md"
        self._guide_path = installed_guide if installed_guide.is_file() or not source_guide.is_file() else source_guide
        line = QFrame()
        line.setObjectName("settingsDivider")
        line.setFixedHeight(1)
        layout.addWidget(line)
        self.guide_button = _SettingsRow("使用说明", "openUsageGuideButton")
        self.guide_button.action_requested.connect(self._open_guide)
        layout.addWidget(self.guide_button)
        self._add_status_label()
        self.content_layout.addStretch(1)

    def _open_guide(self) -> None:
        self._open_target(self.guide_button, self._guide_path, create_directory=False)
