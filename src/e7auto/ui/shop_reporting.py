"""Optional shop upload on an independent Qt event loop; no game services."""
from __future__ import annotations

from datetime import timezone
from email.utils import parsedate_to_datetime
import json
import logging
import math
from pathlib import Path
import time

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtNetwork import (QNetworkAccessManager, QNetworkProxy, QNetworkProxyFactory,
                              QNetworkProxyQuery, QNetworkReply, QNetworkRequest)

from e7auto.configuration.reporting import load_reporting_endpoint
from e7auto.features.shop.reporting import ShopReportRecorder
from e7auto.logging.report_queue import PendingReport, ReportQueue
from e7auto.ui.preferences import ShopStatisticsPreferences

logger = logging.getLogger(__name__)


def retry_after_seconds(value: bytes, now: float) -> float:
    text = value.decode('ascii', errors='ignore').strip()
    try:
        seconds = float(text)
        return seconds if math.isfinite(seconds) and seconds >= 0 else 0
    except ValueError:
        try:
            date = parsedate_to_datetime(text)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0, date.timestamp()-now)
        except (ValueError, TypeError, OverflowError):
            return 0


class ShopReportUploader(QObject):
    changed = Signal()
    status = Signal(str, bool)
    busy_changed = Signal(bool)
    retry_settled = Signal()
    stopped = Signal()

    def __init__(self, queue: ReportQueue, endpoint: str) -> None:
        super().__init__()
        self.queue, self.endpoint = queue, endpoint
        self._manager = None
        self._timer = None
        self._reply: QNetworkReply | None = None
        self._pending: PendingReport | None = None
        self._stopping = False
        self._retry_not_before = 0.0
        self._response_too_large = False

    @Slot()
    def start(self) -> None:
        # All native networking objects are created in their owning upload thread.
        self._manager = QNetworkAccessManager(self)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.pump)
        self._timer.start()
        self.pump()

    @Slot()
    def pump(self, manual: bool = False) -> None:
        if self._stopping:
            self._idle_status('统计上传正在关闭', manual)
            return
        if self._reply is not None:
            if manual:
                self.busy_changed.emit(True)
                self.status.emit('正在重新上传…', False)
            return
        if not self.queue.upload_allowed:
            self._idle_status('当前任务运行中，结束后自动补传' if self.queue.enabled
                              else '匿名统计已关闭，开启后再重试', manual)
            return
        if not self.endpoint:
            self._idle_status('尚未配置统计服务地址，记录保存在本地', manual)
            return
        if time.time() < self._retry_not_before:
            return
        try:
            pending = self.queue.next_due(time.time())
        except Exception:
            self.busy_changed.emit(False)
            self.status.emit('暂时无法读取本地统计记录', False)
            return
        if pending is None or not self.queue.upload_allowed:
            self._idle_status('没有可重试的待上传记录' if self.queue.upload_allowed
                              else '当前任务运行中，结束后自动补传', manual)
            return
        self._pending = pending
        self._response_too_large = False
        request = QNetworkRequest(QUrl(self.endpoint+'/v1/shop-runs'))
        request.setRawHeader(b'Content-Type', b'application/json')
        request.setRawHeader(b'Accept', b'application/json')
        request.setRawHeader(b'User-Agent', b'E7auto')
        # Do not forward anonymous data to a redirected/different host.
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(10_000)
        try:
            # Copy a system proxy value per request; avoid transferring ownership of
            # a Python virtual QNetworkProxyFactory to a native Qt object.
            proxies = QNetworkProxyFactory.systemProxyForQuery(QNetworkProxyQuery(request.url()))
            self._manager.setProxy(proxies[0] if proxies else QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
            if not self.queue.upload_allowed or self._stopping:
                self._pending = None
                self._idle_status('统计上传已暂停，记录保存在本地', manual)
                return
            self._reply = self._manager.post(request, json.dumps(pending.report.payload()).encode('utf-8'))
        except Exception:
            logger.exception('无法启动统计上传请求')
            self._pending = None
            try:
                self._retry_not_before = self.queue.retry_later(pending.report.run_id, time.time())
            except Exception:
                self._retry_not_before = time.time()+60
            self.busy_changed.emit(False)
            self.status.emit('连接失败，记录已保留，稍后自动补传', False)
            return
        self._reply.finished.connect(self._finished)
        self._reply.downloadProgress.connect(self._check_response_size)
        self.busy_changed.emit(True)
        self.status.emit('正在重新上传…' if manual else '正在上传统计记录…', False)

    def _idle_status(self, message: str, manual: bool) -> None:
        if manual:
            self.busy_changed.emit(False)
            self.status.emit(message, False)

    @Slot('qint64', 'qint64')
    def _check_response_size(self, received: int, total: int) -> None:
        if self._reply is not None and (received > 8192 or total > 8192):
            self._response_too_large = True
            self._reply.abort()

    def _cancel_reply(self) -> None:
        if self._reply is not None:
            reply, self._reply = self._reply, None
            self._pending = None
            reply.finished.disconnect(self._finished)
            reply.abort()
            reply.deleteLater()
        self.busy_changed.emit(False)

    @Slot()
    def policy_changed(self) -> None:
        if not self.queue.upload_allowed:
            was_uploading = self._reply is not None
            if was_uploading:
                self.status.emit('当前任务运行中，结束后自动补传' if self.queue.enabled
                                 else '匿名统计已关闭，已暂停上传', False)
            self._cancel_reply()
        else:
            self.pump()

    @Slot()
    def retry(self) -> None:
        try:
            if self.queue.upload_allowed and not self._stopping:
                self._retry_not_before = 0
                self.queue.retry_pending()
            self.pump(manual=True)
        except Exception:
            self.busy_changed.emit(self._reply is not None)
            self.status.emit('暂时无法读取本地统计记录', False)
        finally:
            self.retry_settled.emit()

    @Slot()
    def stop(self) -> None:
        self._stopping = True
        if self._timer is not None:
            self._timer.stop()
        self._cancel_reply()
        self.stopped.emit()

    def _finished(self) -> None:
        reply, self._reply = self._reply, None
        pending, self._pending = self._pending, None
        if reply is None or pending is None:
            return
        try:
            code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            error = reply.error()
            accepted = False
            if (code in (200, 201) and error == QNetworkReply.NetworkError.NoError
                    and reply.isReadable() and reply.bytesAvailable() <= 8192):
                try:
                    received = bytes(reply.readAll())
                    body = json.loads(received)
                    accepted = (isinstance(body, dict) and body.get('accepted') is True
                                and body.get('run_id') == pending.report.run_id)
                except (ValueError, UnicodeError):
                    pass
            if accepted:
                self.queue.acknowledge(pending.report.run_id)
                self._retry_not_before = 0
                self.status.emit('统计记录已上传', True)
            elif code in (400, 409, 413, 422):
                self.queue.reject(pending.report.run_id, f'http_{code}')
                logger.warning('统计记录被服务端拒绝：HTTP=%s', code)
                self.status.emit('有记录被服务端拒绝，已保留在本地', False)
            else:
                now = time.time()
                self._retry_not_before = self.queue.retry_later(pending.report.run_id, now,
                    minimum_delay=retry_after_seconds(bytes(reply.rawHeader('Retry-After')), now))
                logger.warning('统计上传未获确认：HTTP=%s，网络错误=%s（%s）',
                               code, error.name, reply.errorString())
                if self._response_too_large:
                    message = '服务响应过大，记录已保留，稍后重试'
                elif error == QNetworkReply.NetworkError.TimeoutError:
                    message = '上传超时，记录已保留，稍后自动补传'
                elif error == QNetworkReply.NetworkError.NoError:
                    message = '未收到有效上传确认，记录已保留，稍后重试'
                else:
                    message = '连接失败，记录已保留，稍后自动补传'
                self.status.emit(message, False)
        except Exception:
            logger.exception('无法处理统计上传结果')
            self._retry_not_before = time.time()+60
            self.status.emit('记录仍保留在本地，稍后重试', False)
        finally:
            reply.deleteLater()
            self.busy_changed.emit(False)
            self.changed.emit()


class ShopReportingController(QObject):
    changed = Signal()
    status = Signal(str, bool)
    busy_changed = Signal(bool)
    finished = Signal()
    _policy_changed = Signal()
    _retry_requested = Signal()
    _stop_requested = Signal()

    def __init__(self, root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._preferences = ShopStatisticsPreferences(root/'state'/'ui_preferences.json')
        self.queue = ReportQueue(root/'state'/'shop_reports.sqlite3', enabled=self._preferences.load(),
                                 generation=self._preferences.generation())
        self.endpoint = load_reporting_endpoint(root/'config'/'internal.yaml')
        self._thread: QThread | None = None
        self._uploader: ShopReportUploader | None = None
        self._closing = False
        self._in_flight = False
        self._retry_queued = False
        self._uploading = False
        self._poll = QTimer(self)
        self._poll.setInterval(5000)
        self._poll.timeout.connect(self.changed)
        try:
            self.queue.recover_interrupted()
        except Exception:
            pass  # Broken optional storage must not prevent opening the application.

    @property
    def enabled(self) -> bool:
        return self.queue.enabled

    @property
    def closing(self) -> bool:
        return self._closing and self._thread is not None

    @property
    def uploading(self) -> bool:
        return self._uploading

    def _update_busy(self) -> None:
        busy = self._in_flight or self._retry_queued
        if busy != self._uploading:
            self._uploading = busy
            self.busy_changed.emit(busy)
            self.changed.emit()

    @Slot(bool)
    def _set_in_flight(self, busy: bool) -> None:
        self._in_flight = busy
        self._update_busy()

    @Slot()
    def _retry_settled(self) -> None:
        self._retry_queued = False
        self._update_busy()

    def counts(self) -> tuple[int, int] | None:
        try:
            return self.queue.counts()
        except Exception:
            return None

    def recorder(self, *, continuous_refresh: bool) -> ShopReportRecorder:
        return ShopReportRecorder(self.queue, continuous_refresh=continuous_refresh)

    def start(self) -> None:
        self._poll.start()
        if not self.endpoint or self._thread is not None or self._closing:
            return
        thread = QThread(self)
        thread.setObjectName('shopStatisticsUploader')
        uploader = ShopReportUploader(self.queue, self.endpoint)
        uploader.moveToThread(thread)
        thread.started.connect(uploader.start)
        self._policy_changed.connect(uploader.policy_changed)
        self._retry_requested.connect(uploader.retry)
        self._stop_requested.connect(uploader.stop)
        uploader.changed.connect(self.changed)
        uploader.status.connect(self.status)
        uploader.busy_changed.connect(self._set_in_flight)
        uploader.retry_settled.connect(self._retry_settled)
        uploader.stopped.connect(thread.quit)
        thread.finished.connect(uploader.deleteLater)
        thread.finished.connect(self._thread_stopped)
        self._thread, self._uploader = thread, uploader
        thread.start()

    def set_enabled(self, enabled: bool) -> None:
        self._preferences.save(enabled)
        try:
            self.queue.set_enabled(enabled, generation=self._preferences.generation())
        finally:
            self._policy_changed.emit()
            self.changed.emit()

    def set_running(self, running: bool) -> None:
        self.queue.pause_uploads(running)
        self._policy_changed.emit()

    def retry(self) -> None:
        if self._closing:
            self.status.emit('统计上传正在关闭', False)
            return
        if not self.enabled:
            self.status.emit('匿名统计已关闭，开启后再重试', False)
            return
        if not self.endpoint:
            self.status.emit('尚未配置统计服务地址，记录保存在本地', False)
            return
        if not self.queue.upload_allowed:
            self.status.emit('当前任务运行中，结束后自动补传', False)
            return
        if self.uploading:
            return
        try:
            pending, rejected = self.queue.counts()
        except Exception:
            self.status.emit('暂时无法读取本地统计记录', False)
            return
        if pending <= rejected:
            self.status.emit('没有可重试的待上传记录', False)
            return
        self._retry_queued = True
        self._update_busy()
        self.status.emit('正在重新上传…', False)
        try:
            self.start()
            self._retry_requested.emit()
        except Exception:
            logger.exception('无法启动统计上传线程')
            self._retry_queued = False
            self._update_busy()
            self.status.emit('暂时无法启动上传，记录已保留', False)

    def cancel(self) -> None:
        was_closing = self._closing
        self._closing = True
        self._retry_queued = False
        self._update_busy()
        self._poll.stop()
        if self._thread is not None and not was_closing:
            self._stop_requested.emit()

    @Slot()
    def _thread_stopped(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if not thread.wait(0):
            QTimer.singleShot(10, self._thread_stopped)
            return
        self._thread, self._uploader = None, None
        self._in_flight = False
        self._retry_queued = False
        self._update_busy()
        thread.deleteLater()
        self.finished.emit()
