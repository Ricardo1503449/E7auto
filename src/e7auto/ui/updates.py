"""Asynchronous release checks and shared, persistent update reminders."""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from e7auto import __version__

PROJECT_URL = "https://github.com/Ricardo1503449/E7auto"
LATEST_RELEASE_API = "https://api.github.com/repos/Ricardo1503449/E7auto/releases/latest"


@dataclass(frozen=True)
class ReleaseAsset:
    version: str
    name: str
    url: str
    size: int
    sha256: str


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
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag) if isinstance(tag, str) else None
    if match is None:
        raise ValueError("无法识别发布版本号")
    return tuple(map(int, match.groups()))


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

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self.retry_not_before = 0.0
        self.asset: ReleaseAsset | None = None
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(10_000)
        self._timeout.timeout.connect(self._abort)

    def check(self) -> None:
        if self._reply is not None:
            return
        self.retry_not_before = 0.0
        self.asset = None
        request = QNetworkRequest(QUrl(LATEST_RELEASE_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"E7auto/{__version__}".encode("ascii"))
        request.setTransferTimeout(10_000)
        self._reply = self._manager.get(request)
        self._reply.finished.connect(self._finished)
        self._timeout.start()

    def _abort(self) -> None:
        if self._reply is not None:
            self._reply.abort()

    def cancel(self) -> None:
        self._timeout.stop()
        if self._reply is not None:
            reply, self._reply = self._reply, None
            reply.finished.disconnect(self._finished)
            reply.abort()
            reply.deleteLater()

    def _finished(self) -> None:
        reply, self._reply = self._reply, None
        self._timeout.stop()
        if reply is None:
            return
        try:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            self.retry_not_before = self._rate_limit_deadline(reply, status)
            if status == 404:
                self.failed.emit("暂未找到正式发布版本，请稍后重试。")
            elif reply.error() != QNetworkReply.NetworkError.NoError:
                self.failed.emit("检查失败，请检查网络后重试，或打开 GitHub 项目查看发布页面。")
            else:
                try:
                    payload = bytes(reply.readAll())
                    version, available, notes = release_details(payload)
                    self.asset = release_asset(payload, version) if available else None
                except (ValueError, UnicodeError):
                    self.failed.emit("无法读取最新版本信息，请稍后重试。")
                else:
                    self.completed.emit(version, available, notes)
        finally:
            reply.deleteLater()

    @staticmethod
    def _rate_limit_deadline(reply: QNetworkReply, status: int | None) -> float:
        if status not in (403, 429):
            return 0.0
        now = time.time()
        retry = bytes(reply.rawHeader("Retry-After")).decode("ascii", errors="ignore").strip()
        remaining = bytes(reply.rawHeader("X-RateLimit-Remaining")).strip()
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
        if remaining == b"0":
            try:
                reset = float(bytes(reply.rawHeader("X-RateLimit-Reset")))
                if math.isfinite(reset):
                    deadlines.append(reset)
            except ValueError:
                pass
        if deadlines or status == 429 or remaining == b"0" or retry:
            return max([now + 60, *deadlines])
        return 0.0


class UpdateController(QObject):
    """Own one request; only explicitly requested checks produce dialogs."""

    availability_changed = Signal(bool)
    completed = Signal(str, bool, str)
    failed = Signal(str)
    _CHECK_INTERVAL = 6 * 60 * 60
    _RETRY_DELAYS = (30 * 60, 60 * 60, 2 * 60 * 60, 4 * 60 * 60, 6 * 60 * 60)

    def __init__(self, cache_path: Path, parent: QObject | None = None, *,
                 clock: Callable[[], float] = time.time) -> None:
        super().__init__(parent)
        self._cache_path = cache_path
        self._clock = clock
        self.latest_version: str | None = None
        self.asset: ReleaseAsset | None = None
        self.notes = ""
        self.checked_at = 0.0
        self.next_check_at = 0.0
        self.retry_not_before = 0.0
        self._failure_count = 0
        self._checking = False
        self._manual = False
        self._started = False
        self._closed = False
        self._load_cache()
        self.checker = UpdateChecker(self)
        self.checker.completed.connect(self._completed)
        self.checker.failed.connect(self._failed)
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._check_automatically)

    @property
    def available(self) -> bool:
        return self.latest_version is not None and _version_parts(self.latest_version) > _version_parts(__version__)

    @property
    def checking(self) -> bool:
        return self._checking

    def start(self) -> None:
        if not self._started and not self._closed:
            self._started = True
            self._auto_timer.start(1000)

    def check(self, *, manual: bool = False) -> None:
        if self._closed:
            return
        self._manual = self._manual or manual
        if self._checking:
            return
        now = self._clock()
        if now < self.retry_not_before:
            self._manual = False
            self._schedule()
            if manual:
                self.failed.emit(self._rate_limit_message(now))
            return
        self._auto_timer.stop()
        self._checking = True
        self.checker.check()

    def cancel(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._auto_timer.stop()
        self._checking = self._manual = False
        self.checker.cancel()

    def _check_automatically(self) -> None:
        if self._closed:
            return
        if self._clock() < max(self.next_check_at, self.retry_not_before):
            self._schedule()
        else:
            self.check()

    def _schedule(self) -> None:
        if self._started and not self._closed:
            delay = max(self.next_check_at, self.retry_not_before) - self._clock()
            # Qt timer intervals are signed 32-bit milliseconds.
            interval = math.ceil(min(max(delay, 0.001), 2_147_483.647) * 1000)
            self._auto_timer.start(interval)

    def _completed(self, version: str, available: bool, notes: str) -> None:
        if self._closed or not self._checking:
            return
        manual = self._manual
        self._checking = self._manual = False
        self.latest_version, self.notes = version, notes
        self.asset = self.checker.asset if self.checker.asset is not None and self.checker.asset.version == version else None
        self.checked_at = self._clock()
        self.next_check_at = self.checked_at + self._CHECK_INTERVAL
        self.retry_not_before = 0.0
        self._failure_count = 0
        self._save_cache()
        self._schedule()
        self.availability_changed.emit(self.available)
        if manual:
            self.completed.emit(version, self.available, notes)

    def _failed(self, message: str) -> None:
        if self._closed or not self._checking:
            return
        now = self._clock()
        manual = self._manual
        self._checking = self._manual = False
        delay = self._RETRY_DELAYS[min(self._failure_count, len(self._RETRY_DELAYS) - 1)]
        self._failure_count = min(self._failure_count + 1, len(self._RETRY_DELAYS))
        self.retry_not_before = self.checker.retry_not_before
        self.next_check_at = max(now + delay, self.retry_not_before)
        self._save_cache()
        self._schedule()
        if manual:
            self.failed.emit(self._rate_limit_message(now) if self.retry_not_before > now else message)

    def _rate_limit_message(self, now: float) -> str:
        minutes = math.ceil((self.retry_not_before - now) / 60)
        return f"GitHub 暂时限制请求，请在约 {minutes} 分钟后重试。"

    def _load_cache(self) -> None:
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != 1:
                return
            version, notes = raw.get("latest_version"), raw["notes"]
            if version is not None:
                _version_parts(version)
            if not isinstance(notes, str):
                return
            dates = [raw[key] for key in ("checked_at", "next_check_at", "retry_not_before")]
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in dates):
                return
            failures = raw["failure_count"]
            if type(failures) is not int or not 0 <= failures <= len(self._RETRY_DELAYS):
                return
            self.latest_version, self.notes = version, notes
            self.checked_at, self.next_check_at, self.retry_not_before = dates
            self._failure_count = failures
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            pass

    def _save_cache(self) -> None:
        values = {
            "schema_version": 1, "latest_version": self.latest_version, "notes": self.notes,
            "checked_at": self.checked_at, "next_check_at": self.next_check_at,
            "retry_not_before": self.retry_not_before, "failure_count": self._failure_count,
        }
        temporary = None
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._cache_path.parent,
                                    prefix="update_status-", suffix=".tmp", delete=False) as stream:
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
