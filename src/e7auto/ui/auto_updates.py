"""Opt-in update preparation; installation always belongs to an explicit UI action."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from e7auto.platform.installation import is_installed_build
from e7auto.core.update_feed import version_parts
from e7auto.ui.preferences import UpdatePreferences
from e7auto.ui.widgets import _use_smooth_text


class BackgroundUpdates(QObject):
    changed = Signal()
    enabled_changed = Signal(bool)
    status_changed = Signal(str)
    _FIRST_DOWNLOAD_DELAY = 60

    def __init__(self, preferences_path, controller, installer, parent=None, *, clock=time.time):
        super().__init__(parent)
        self.preferences = UpdatePreferences(preferences_path)
        self.controller, self.installer, self._clock = controller, installer, clock
        self.supported = is_installed_build()
        self.enabled = self.supported and self.preferences.load()
        self.dismissed_version = self.preferences.dismissed_version()
        self._started = self._closed = False
        self._first_download_at = self._retry_at = 0.0
        self._failure_count = 0
        self._retry_asset = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._prepare)
        controller.offer_changed.connect(self._offer_changed)
        controller.check_failed.connect(self._check_failed)
        installer.ready_changed.connect(self._ready_changed)
        installer.background_failed.connect(self._download_failed)
        installer.failed.connect(self._manual_failed)
        installer.finished.connect(self._settled)

    def start(self):
        if self._started or self._closed:
            return
        self._started = True
        self._first_download_at = self._clock() + self._FIRST_DOWNLOAD_DELAY
        if self.supported:
            self.installer.restore()
        if self.enabled:
            self._schedule()

    @property
    def should_prompt(self):
        ready = self.installer.ready
        return (self.enabled and not self._closed and ready is not None
                and (self.controller.offer is None or self.controller.offer.asset == ready.offer.asset
                     or version_parts(ready.offer.asset.version) > version_parts(self.controller.offer.asset.version))
                and ready.offer.asset.version != self.dismissed_version)

    def set_enabled(self, enabled):
        if self._closed:
            return
        if not self.supported:
            self.status_changed.emit('源码运行不支持自动下载更新，请使用安装版。')
            self.enabled_changed.emit(False)
            return
        try:
            self.preferences.save(enabled)
        except OSError as exc:
            self.status_changed.emit(f'无法保存自动下载设置：{exc}')
            self.enabled_changed.emit(self.enabled)
            return
        self.enabled = enabled
        self.enabled_changed.emit(enabled)
        self.status_changed.emit('')
        if enabled:
            self.start()
            self.controller.ensure_offer()
            self._schedule()
        else:
            self._timer.stop()
            if self.installer.busy and not self.installer.blocking and self.installer.state != 'restoring':
                self.installer.cancel()
        self.changed.emit()

    def dismiss(self):
        ready = self.installer.ready
        if ready is None:
            return
        self.dismissed_version = ready.offer.asset.version
        try:
            self.preferences.dismiss(self.dismissed_version)
        except OSError as exc:
            self.status_changed.emit(f'无法保存稍后选择，重启后可能再次提示：{exc}')
        self.changed.emit()

    def _schedule(self):
        if not self.enabled or self._closed or not self._started:
            return
        delay = max(self._first_download_at, self._retry_at) - self._clock()
        self._timer.start(math.ceil(min(max(delay, .001), 2_147_483.647) * 1000))

    def _prepare(self):
        if self._closed or not self.enabled:
            return
        if self._clock() < max(self._first_download_at, self._retry_at):
            self._schedule()
            return
        offer = self.controller.offer
        if offer is None:
            self.controller.ensure_offer()
            return
        if self.installer.ready is not None and self.installer.ready.offer.asset == offer.asset:
            return
        if (self.installer.busy and (self.installer.blocking or self.installer.state == 'restoring'
                                    or self.installer._asset == offer.asset)):
            return
        self.status_changed.emit('正在后台下载安装包…')
        self.installer.prepare(offer)

    def _offer_changed(self, offer):
        if self._closed:
            return
        self.installer.observe_offer(offer)
        if offer is not None and offer.asset != self._retry_asset:
            self._retry_asset = offer.asset
            self._retry_at = 0
            self._failure_count = 0
        if self.enabled:
            self._schedule()
        self.changed.emit()

    def _check_failed(self, message):
        if self.enabled and not self._closed:
            self.status_changed.emit(message)

    def _download_failed(self, message):
        if self._closed:
            return
        self.status_changed.emit(message)
        self._manual_failed(message)

    def _manual_failed(self, _message):
        if self.enabled:
            delays = self.controller._RETRY_DELAYS
            self._retry_at = self._clock() + delays[min(self._failure_count, len(delays) - 1)]
            self._failure_count += 1
            self._schedule()

    def _ready_changed(self, ready):
        if self._closed:
            return
        if ready is not None and not self.controller.remember_prepared(ready.offer):
            self.installer.discard_ready()
            if self.enabled:
                self._schedule()
            return
        if ready is not None:
            self._retry_at = self._failure_count = 0
            self.status_changed.emit(f'{ready.offer.asset.version} 已下载，等待确认安装。')
        self.changed.emit()

    def _settled(self):
        if (self.enabled and not self._closed and self.installer.ready is None
                and not self.installer._cancel_requested):
            self._schedule()

    def cancel(self):
        self._closed = True
        self._timer.stop()
        self.changed.emit()


class UpdateReadyCard(QFrame):
    install_requested = Signal(object)
    later_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('updateReadyCard')
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(320)
        self._asset = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        self.title = QLabel()
        self.title.setObjectName('updateReadyTitle')
        self.title.setWordWrap(True)
        _use_smooth_text(self.title)
        layout.addWidget(self.title)
        buttons = QHBoxLayout()
        self.later_button = QPushButton('稍后')
        self.install_button = QPushButton('安装并重启')
        for button in (self.later_button, self.install_button):
            _use_smooth_text(button)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(36)
            buttons.addWidget(button)
        self.later_button.clicked.connect(self.later_requested)
        self.install_button.clicked.connect(self._install)
        layout.addLayout(buttons)
        self.hide()

    def set_update(self, ready):
        self._asset = ready.offer.asset
        self.title.setText(f'{self._asset.version} 已下载，是否安装并重启？')
        self.adjustSize()

    def _install(self):
        if self._asset is not None:
            self.install_requested.emit(self._asset)
