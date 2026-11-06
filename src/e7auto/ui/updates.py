"""Asynchronous release checks and shared, persistent update reminders."""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from e7auto.core.update_feed import (FEED_URLS, MAX_FEED_BYTES, PROJECT_URL, ReleaseAsset,
                                   decode_feed, version_parts)
from e7auto.platform.update_signing import verify_signature
from e7auto.configuration.models import LoggingConfig
from e7auto.logging.run import RunLogManager

from e7auto import __version__

def release_asset(payload: bytes, version: str) -> ReleaseAsset | None:
    """Only the named, uploaded Windows installer with a GitHub digest is eligible."""
    data = json.loads(payload)
    name = f"E7auto_{version}_Setup_x64.exe"
    assets = data.get("assets", [])
    if not isinstance(assets, list):
        return None
    matches = [item for item in assets if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        return None
    item = matches[0]
    size, digest = item.get("size"), item.get("digest")
    url = f"{PROJECT_URL}/releases/download/{data['tag_name']}/{name}"
    if (item.get("state") != "uploaded" or item.get("browser_download_url") != url
            or type(size) is not int or not 0 < size <= 512 * 1024 * 1024
            or not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)):
        return None
    return ReleaseAsset(version, name, url, size, digest[7:].lower())


def _version_parts(tag: str) -> tuple[int, int, int]:
    return version_parts(tag)


def release_details(payload: bytes) -> tuple[str, bool, str]:
    data = json.loads(payload)
    if not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False:
        raise ValueError("不是正式发布版本")
    tag = data.get("tag_name", "")
    remote = _version_parts(tag)
    current = _version_parts(__version__)
    notes = data.get("body")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        raise ValueError("无法识别改动日志")
    return f"v{'.'.join(map(str, remote))}", remote > current, notes.strip()


class UpdateChecker(QObject):
    completed = Signal(str, bool, str)
    failed = Signal(str)
    cooldowns_changed = Signal()
    SOURCE_URLS = FEED_URLS

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._replies = {}
        self._buffers = {}
        self._slots = {}
        self._redirects = {}
        self._valid = []
        self._active = False
        self.source_cooldowns = {}
        self.retry_not_before = 0.0
        self.asset = None
        self.diagnostic = lambda event, **fields: None
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(10_000)
        self._timeout.timeout.connect(self._abort)

    def check(self):
        if self._active:
            return
        self._active = True
        self._valid = []
        self.asset = None
        now = time.time()
        self.retry_not_before = 0.0
        for source in self.SOURCE_URLS:
            if self.source_cooldowns.get(source, 0) > now:
                self.diagnostic('update_source_wait', source=source, until=self.source_cooldowns[source])
                continue
            self._redirects[source] = 0
            self._request(source, source)
        if self._replies:
            self._timeout.start()
        else:
            self._finish_check()

    def _request(self, source, url):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'Accept', b'application/json')
        request.setRawHeader(b'User-Agent', f'E7auto/{__version__}'.encode('ascii'))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(10_000)
        reply = self._manager.get(request)
        reply.setReadBufferSize(MAX_FEED_BYTES + 1)
        self._replies[source] = reply
        self._buffers[source] = bytearray()
        read_slot = lambda: self._read(source) if self._replies.get(source) is reply else None
        finished_slot = lambda: self._finished(source) if self._replies.get(source) is reply else None
        self._slots[source] = (read_slot, finished_slot)
        reply.readyRead.connect(read_slot)
        reply.finished.connect(finished_slot)

    def _read(self, source):
        reply = self._replies.get(source)
        if reply is None:
            return
        data = bytes(reply.read(MAX_FEED_BYTES + 1 - len(self._buffers[source])))
        self._buffers[source].extend(data)
        if len(self._buffers[source]) > MAX_FEED_BYTES:
            self.diagnostic('update_source_rejected', source=source, reason='response_too_large')
            self._drop(source)
            if not self._replies:
                self._finish_check()

    def _drop(self, source):
        reply = self._replies.pop(source, None)
        self._buffers.pop(source, None)
        slots = self._slots.pop(source, None)
        if reply is not None:
            try:
                reply.readyRead.disconnect(slots[0])
                reply.finished.disconnect(slots[1])
            except (RuntimeError, TypeError):
                pass
            if not reply.isFinished():
                reply.abort()
            reply.deleteLater()

    def _finished(self, source):
        reply = self._replies.get(source)
        if reply is None:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        deadline = self._rate_limit_deadline(reply, status)
        headers = {name: bytes(reply.rawHeader(name)).decode('ascii', errors='replace')[:128]
                   for name in ('Retry-After', 'X-RateLimit-Remaining', 'X-RateLimit-Reset')}
        self.diagnostic('update_source_response', source=source, http=status, wait_until=deadline, **headers)
        if deadline:
            self.source_cooldowns[source] = deadline
            self.cooldowns_changed.emit()
        if status in (301, 302, 303, 307, 308):
            target = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
            url = reply.url().resolved(target).toString() if isinstance(target, QUrl) else ''
            allowed = (*FEED_URLS, FEED_URLS[0].replace('://gh-proxy.org/', '://gh-proxy.com/', 1))
            if url in allowed and self._redirects[source] < 5:
                self._redirects[source] += 1
                self._drop(source)
                self._request(source, url)
                return
        try:
            self._read(source)
            if source not in self._replies:
                return
            if status != 200 or reply.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError(f'HTTP {status}')
            release = decode_feed(bytes(self._buffers[source]), verify_signature)
            self._valid.append((source, release))
            self.source_cooldowns.pop(source, None)
            self.cooldowns_changed.emit()
        except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
            self.diagnostic('update_source_rejected', source=source, reason=str(exc))
        finally:
            self._drop(source)
        if not self._replies:
            self._finish_check()

    def _finish_check(self):
        if not self._active:
            return
        self._active = False
        self._timeout.stop()
        now = time.time()
        waits = [self.source_cooldowns.get(source, 0) for source in self.SOURCE_URLS]
        self.retry_not_before = min(waits) if waits and all(value > now for value in waits) else 0.0
        if not self._valid:
            self.failed.emit('检查失败，请检查网络后重试，或打开 GitHub 项目查看发布页面。')
            return
        candidates = [release for source, release in self._valid]
        if any(a.version == b.version and a.asset != b.asset for a in candidates for b in candidates):
            self.failed.emit('更新来源提供的同版本安装包信息不一致，已停止更新，请稍后重试。')
            return
        _, release = max(self._valid, key=lambda item: (version_parts(item[1].version),
                                                        item[0] == self.SOURCE_URLS[-1]))
        available = version_parts(release.version) > version_parts(__version__)
        self.asset = release.asset if available else None
        self.completed.emit(release.version, available, release.notes)

    def _abort(self):
        for source in list(self._replies):
            self.diagnostic('update_source_timeout', source=source)
            self._drop(source)
        self._finish_check()

    def cancel(self):
        self._active = False
        self._timeout.stop()
        for source in list(self._replies):
            self._drop(source)

    @staticmethod
    def _rate_limit_deadline(reply, status):
        if status not in (403, 429):
            return 0.0
        now = time.time()
        retry = bytes(reply.rawHeader('Retry-After')).decode('ascii', errors='ignore').strip()
        remaining = bytes(reply.rawHeader('X-RateLimit-Remaining')).strip()
        deadlines = []
        if retry:
            try:
                seconds = float(retry)
                if math.isfinite(seconds) and seconds >= 0:
                    deadlines.append(now + seconds)
            except ValueError:
                try:
                    date = parsedate_to_datetime(retry)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    deadlines.append(date.timestamp())
                except (ValueError, TypeError, OverflowError):
                    pass
        if remaining == b'0':
            try:
                reset = float(bytes(reply.rawHeader('X-RateLimit-Reset')))
                if math.isfinite(reset):
                    deadlines.append(reset)
            except ValueError:
                pass
        return max([now + 60, *deadlines]) if deadlines or status == 429 or remaining == b'0' or retry else 0.0


class UpdateController(QObject):
    completed = Signal(str, bool, str)
    failed = Signal(str)
    availability_changed = Signal(bool)
    _CHECK_INTERVAL = 6 * 60 * 60
    _RETRY_DELAYS = (1800, 3600, 7200, 14400, 21600)

    def __init__(self, cache_path: Path, parent=None, *, clock=time.time):
        super().__init__(parent)
        self._cache_path, self._clock = cache_path, clock
        self.latest_version, self.notes, self.asset = None, '', None
        self.checked_at = self.next_check_at = self.retry_not_before = 0.0
        self._failure_count = 0
        self._checking = self._manual = self._started = self._closed = False
        self._fresh_result = False
        self._logger = None
        self.checker = UpdateChecker(self)
        self.checker.diagnostic = self.log_event
        self._load_cache()
        self.checker.cooldowns_changed.connect(self._save_cache)
        self.checker.completed.connect(self._completed)
        self.checker.failed.connect(self._failed)
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._check_automatically)

    def log_event(self, event, **fields):
        if self._closed:
            return
        try:
            if self._logger is None:
                self._logger = RunLogManager(self._cache_path.parent.parent / 'logs', LoggingConfig()).start('updates-' + uuid.uuid4().hex)
            self._logger.event(event, **fields)
        except Exception:
            pass  # Diagnostics never block update checks, downloads, or window teardown.

    @property
    def available(self):
        return self.latest_version is not None and version_parts(self.latest_version) > version_parts(__version__)

    @property
    def checking(self):
        return self._checking

    def start(self):
        if not self._started and not self._closed:
            self._started = True
            self._auto_timer.start(1000)

    def check(self, *, manual=False):
        if self._closed:
            return
        self._manual = self._manual or manual
        if self._checking:
            return
        now = self._clock()
        if now < self.retry_not_before:
            self._manual = False
            self._schedule()
            self.log_event('update_cached_wait', manual=manual, until=self.retry_not_before)
            if manual:
                self.failed.emit(self._rate_limit_message(now))
            return
        if manual and self._fresh_result and 0 <= now - self.checked_at < 60:
            self._manual = False
            self.log_event('update_recent_result', checked_at=self.checked_at)
            self.completed.emit(self.latest_version, self.available, self.notes)
            return
        self._auto_timer.stop()
        self._checking = True
        self.log_event('update_check_started', manual=manual)
        self.checker.check()

    def _check_automatically(self):
        if not self._closed:
            if self._clock() < max(self.next_check_at, self.retry_not_before):
                self._schedule()
            else:
                self.check()

    def _schedule(self):
        if self._started and not self._closed:
            delay = max(self.next_check_at, self.retry_not_before) - self._clock()
            self._auto_timer.start(math.ceil(min(max(delay, .001), 2_147_483.647) * 1000))

    def _completed(self, version, available, notes):
        if self._closed or not self._checking:
            return
        if self.latest_version and version_parts(version) < version_parts(self.latest_version):
            self._failed('更新来源尚未同步，保留上次已确认的版本，请稍后重试。')
            return
        manual = self._manual
        self._checking = self._manual = False
        self.latest_version, self.notes = version, notes
        self.asset = self.checker.asset if self.checker.asset is not None and self.checker.asset.version == version else None
        self.checked_at = self._clock()
        self.next_check_at = self.checked_at + self._CHECK_INTERVAL
        self.retry_not_before = 0.0
        self._failure_count = 0
        self._fresh_result = True
        self._save_cache()
        self._schedule()
        self.availability_changed.emit(self.available)
        if manual:
            self.completed.emit(version, self.available, notes)

    def _failed(self, message):
        if self._closed or not self._checking:
            return
        now, manual = self._clock(), self._manual
        self._checking = self._manual = self._fresh_result = False
        self.asset = None
        delay = self._RETRY_DELAYS[min(self._failure_count, len(self._RETRY_DELAYS) - 1)]
        self._failure_count = min(self._failure_count + 1, len(self._RETRY_DELAYS))
        self.retry_not_before = self.checker.retry_not_before
        self.next_check_at = max(now + delay, self.retry_not_before)
        self._save_cache()
        self._schedule()
        if manual:
            self.failed.emit(self._rate_limit_message(now, cached=False) if self.retry_not_before > now else message)

    def _rate_limit_message(self, now, *, cached=True):
        minutes = max(1, math.ceil((self.retry_not_before - now) / 60))
        origin = '更新来源仍在此前的等待期内' if cached else '本次请求收到更新来源的限流响应'
        return f'{origin}，请在约 {minutes} 分钟后重试，或打开 GitHub 项目发布页面。'

    def _load_cache(self):
        try:
            raw = json.loads(self._cache_path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict) or raw.get('schema_version') not in (1, 2):
                return
            version, notes = raw.get('latest_version'), raw['notes']
            if version is not None:
                version_parts(version)
            if not isinstance(notes, str):
                return
            dates = [raw[key] for key in ('checked_at', 'next_check_at', 'retry_not_before')]
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in dates):
                return
            failures = raw['failure_count']
            if type(failures) is not int or not 0 <= failures <= len(self._RETRY_DELAYS):
                return
            cooldowns = raw.get('source_cooldowns', {})
            if not isinstance(cooldowns, dict) or any(key not in self.checker.SOURCE_URLS or type(value) not in (int, float) or not math.isfinite(value) or value < 0 for key, value in cooldowns.items()):
                return
            self.latest_version, self.notes, self.checked_at = version, notes, dates[0]
            if raw['schema_version'] == 2:
                self.next_check_at, self.retry_not_before, self._failure_count = dates[1], dates[2], failures
                self.checker.source_cooldowns = cooldowns
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            pass

    def _save_cache(self):
        values = {'schema_version': 2, 'latest_version': self.latest_version, 'notes': self.notes,
                  'checked_at': self.checked_at, 'next_check_at': self.next_check_at,
                  'retry_not_before': self.retry_not_before, 'failure_count': self._failure_count,
                  'source_cooldowns': self.checker.source_cooldowns}
        temporary = None
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self._cache_path.parent,
                                    prefix='update_status-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(values, stream, ensure_ascii=False, indent=2)
            temporary.replace(self._cache_path)
        except (OSError, UnicodeError):
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def cancel(self):
        if not self._closed:
            self._closed = True
            self._auto_timer.stop()
            self._checking = self._manual = False
            self.checker.cancel()
            if self._logger is not None:
                try:
                    self._logger.close()
                except Exception:
                    pass
                self._logger = None
