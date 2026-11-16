"""Cancellable installer download and a verified handoff to Windows setup."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit
import uuid
import time

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from e7auto import __version__
from e7auto.platform.update_install import (PreparedUpdate, forget_prepared_update, launch_update,
                                           load_prepared_update, ordinary_path,
                                           store_prepared_update, validate_installation)
from e7auto.ui.updates import PROJECT_URL, ReleaseAsset, _version_parts, UpdateChecker
from e7auto.core.update_feed import VerifiedUpdateOffer, download_urls


def allowed_download_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.hostname in {'gh-proxy.org', 'gh-proxy.com'}:
            inner = parsed.path.removeprefix('/')
            return (parsed.scheme == 'https' and parsed.port in (None, 443)
                    and not parsed.username and not parsed.password and not parsed.fragment and not parsed.query
                    and inner.startswith(PROJECT_URL + '/releases/download/')
                    and allowed_download_url(inner))
        return (parsed.scheme == 'https' and parsed.port in (None, 443)
                and not parsed.username and not parsed.password and not parsed.fragment
                and parsed.hostname in {'github.com', 'release-assets.githubusercontent.com',
                                        'objects.githubusercontent.com', 'github-releases.githubusercontent.com'})
    except ValueError:
        return False


class _InstallerThread(QThread):
    def __init__(self, root: Path, path: Path, asset: ReleaseAsset, parent: QObject):
        super().__init__(parent)
        self.root, self.path, self.asset = root, path, asset
        self.cancelled = Event()
        self.launched = False
        self.error = ''
        self.invalid_package = False

    def run(self) -> None:
        try:
            self.launched = launch_update(self.root, self.path, self.asset.version,
                                          self.asset.size, self.asset.sha256, self.cancelled)
        except Exception as exc:
            self.invalid_package = isinstance(exc, ValueError)
            self.error = f'无法启动安装，当前程序仍可继续使用。\n{exc}'


class _PackageThread(QThread):
    def __init__(self, root, parent, *, offer=None, path=None):
        super().__init__(parent)
        self.root, self.offer, self.path = root, offer, path
        self.cancelled = Event()
        self.prepared = None
        self.error = ''

    def run(self):
        try:
            self.prepared = (load_prepared_update(self.root, self.cancelled) if self.offer is None
                             else store_prepared_update(self.root, self.offer, self.path, self.cancelled))
        except Exception as exc:
            self.error = f'更新包核验失败：{exc}'


class UpdateInstaller(QObject):
    progress = Signal(int, int)
    status = Signal(str)
    failed = Signal(str)
    busy_changed = Signal(bool)
    launching_changed = Signal(bool)
    install_started = Signal()
    finished = Signal()
    blocking_changed = Signal(bool)
    ready_changed = Signal(object)
    state_changed = Signal(str)
    background_failed = Signal(str)

    def __init__(self, root: Path, parent: QObject | None = None, *, diagnostic=None):
        super().__init__(parent)
        self.root = root
        self.busy = False
        self.state = 'idle'
        self.ready: PreparedUpdate | None = None
        self._offer = None
        self._install_when_ready = False
        self._cancel_requested = False
        self._package_thread = None
        self._queued_request = None
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._thread: _InstallerThread | None = None
        self._asset: ReleaseAsset | None = None
        self._verified: tuple[ReleaseAsset, Path] | None = None
        self._stream = None
        self._partial: Path | None = None
        self._received = 0
        self._redirects = 0
        self._sources = ()
        self._source_index = 0
        self._source_cooldowns = {}
        self._reply_slots = None
        self._download_path = None
        self.diagnostic = diagnostic or (lambda event, **fields: None)
        self._checksum = hashlib.sha256()
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(15 * 60 * 1000)
        self._deadline.timeout.connect(self._download_timed_out)

    @property
    def launching(self) -> bool:
        return self._thread is not None

    @property
    def blocking(self) -> bool:
        return (self.busy and (self._install_when_ready or self.launching)
                or self._queued_request is not None and self._queued_request[2])

    @property
    def settling(self) -> bool:
        return self.launching or self._package_thread is not None

    def _set_state(self, state):
        self.state = state
        self.state_changed.emit(state)

    def _set_busy(self, busy):
        self.busy = busy
        self.busy_changed.emit(busy)
        self.blocking_changed.emit(self.blocking)

    def restore(self):
        if self.busy or not (self.root / 'state' / 'updates' / 'ready.json').exists():
            return
        self._cancel_requested = self._install_when_ready = False
        self._set_busy(True)
        self._set_state('restoring')
        self._verify_package()

    def prepare(self, offer: VerifiedUpdateOffer):
        self._start(offer.asset, offer, install=False)

    def start(self, asset: ReleaseAsset | None, *, offer: VerifiedUpdateOffer | None = None) -> None:
        self._start(asset, offer, install=True)

    def install_ready(self, asset: ReleaseAsset):
        if self.ready is None or self.ready.offer.asset != asset:
            self.failed.emit('待安装版本已经变化，请重新确认更新。')
            return
        self.start(asset, offer=self.ready.offer)

    def observe_offer(self, offer):
        previous = self.ready.offer.asset if self.ready is not None else self._asset
        if previous is not None and offer is not None and previous != offer.asset:
            self.discard_ready()

    def discard_ready(self):
        self.ready = self._verified = None
        self.ready_changed.emit(None)
        try:
            forget_prepared_update(self.root)
        except (OSError, ValueError, TypeError) as exc:
            self.diagnostic('update_cache_invalidation_failed', reason=str(exc))

    def _start(self, asset, offer, *, install):
        if self.busy:
            if asset == self._asset and not self._cancel_requested:
                if install:
                    self._install_when_ready = True
                    self.blocking_changed.emit(self.blocking)
                return
            if self.blocking:
                return
            self.cancel()
            if self.settling:
                self._queued_request = (asset, offer, install)
                self.blocking_changed.emit(self.blocking)
                return
        self._cancel_requested = False
        self._install_when_ready = install
        try:
            validate_installation(self.root)
            if (asset is None or _version_parts(asset.version) <= _version_parts(__version__)
                    or asset.name != f'E7auto_{asset.version}_Setup_x64.exe'
                    or not allowed_download_url(asset.url)
                    or asset.url not in {
                        f'{PROJECT_URL}/releases/download/{asset.version}/{asset.name}',
                        f'{PROJECT_URL}/releases/download/{asset.version.removeprefix("v")}/{asset.name}'}
                    or type(asset.size) is not int or not 0 < asset.size <= 512 * 1024 * 1024
                    or len(asset.sha256) != 64 or any(c not in '0123456789abcdef' for c in asset.sha256)):
                raise ValueError('此版本未提供可校验的 Windows 安装包，请稍后重新检查更新。')
            if offer is not None and offer.asset != asset:
                raise ValueError('更新目标与签名清单不一致。')
            self.observe_offer(offer)
            self._asset = asset
            self._offer = offer
            self._set_busy(True)
            if self._cancel_requested or not self.busy:
                return
            if self._verified is not None and self._verified[0] == asset and self._verified[1].is_file():
                if install:
                    self._launch(self._verified[1])
                else:
                    self._idle()
                return
            self._sources = tuple(url for url in download_urls(asset)
                                  if self._source_cooldowns.get(urlsplit(url).hostname, 0) <= time.time())
            if not self._sources:
                raise ValueError('下载来源仍在等待期内，请稍后重试或打开发布页面。')
            directory = self.root / 'state' / 'updates' / f'{asset.version}-{uuid.uuid4().hex}'
            ordinary_path(directory)
            directory.mkdir(parents=True, exist_ok=False)
            (directory / 'request.json').write_text(json.dumps({
                'version': asset.version, 'name': asset.name, 'size': asset.size,
                'sha256': asset.sha256,
            }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            self._download_path = directory / (asset.name + '.part')
            self._source_index = 0
            self._set_state('downloading')
            self._deadline.start()
            self._begin_source()
        except (OSError, ValueError, TypeError, IndexError) as exc:
            self._fail(str(exc))

    def _begin_source(self) -> None:
        if self._cancel_requested or not self.busy:
            self._deadline.stop()
            return
        issue = self._discard_partial()
        if issue:
            self._fail(issue)
            return
        try:
            self._partial = self._download_path
            self._stream = self._partial.open('xb')
        except OSError as exc:
            self._fail(str(exc))
            return
        self._received = self._redirects = 0
        self._checksum = hashlib.sha256()
        self.status.emit('正在下载安装包…' if self._source_index == 0 else '正在切换下载来源，重新下载安装包…')
        self.progress.emit(0, self._asset.size)
        if self.busy:  # A synchronous cancellation from a status/progress listener wins.
            self.diagnostic('update_download_source', source=self._sources[self._source_index])
            self._request(self._sources[self._source_index])

    def _source_failed(self, message: str) -> None:
        self.diagnostic('update_download_source_failed', source=self._sources[self._source_index], reason=message)
        self._release_reply()
        if self.busy and self._deadline.isActive() and self._source_index + 1 < len(self._sources):
            self._source_index += 1
            self._begin_source()
        else:
            self._fail(message)

    def _request(self, url: str) -> None:
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setRawHeader(b'Accept', b'application/octet-stream')
        request.setRawHeader(b'User-Agent', f'E7auto/{__version__}'.encode('ascii'))
        request.setTransferTimeout(30_000)
        self._reply = self._manager.get(request)
        self._reply.setReadBufferSize(1024 * 1024)
        reply = self._reply
        self._reply_slots = (lambda: self._read() if self._reply is reply else None,
                             lambda: self._download_finished() if self._reply is reply else None)
        reply.readyRead.connect(self._reply_slots[0])
        reply.finished.connect(self._reply_slots[1])

    def _record_wait(self, reply, code):
        deadline = UpdateChecker._rate_limit_deadline(reply, code)
        if deadline:
            source = urlsplit(self._sources[self._source_index]).hostname
            self._source_cooldowns[source] = deadline
            self.diagnostic('update_download_wait', source=source, until=deadline)

    def _download_timed_out(self) -> None:
        self._fail('下载超时，请检查网络后重试。')

    def _read(self) -> None:
        reply, asset = self._reply, self._asset
        if reply is None or asset is None or self._stream is None:
            return
        code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if code != 200:
            if code is not None and code >= 400:
                self._record_wait(reply, code)
                self._source_failed(f'下载失败，请检查网络后重试。（HTTP {code}）')
            elif code in (301, 302, 303, 307, 308):
                reply.readAll()  # Do not let a redirect body fill the bounded read buffer.
            return
        try:
            while reply.bytesAvailable():
                block = bytes(reply.read(64 * 1024))
                if not block:
                    break
                self._received += len(block)
                if self._received > asset.size:
                    raise ValueError('下载大小与发布信息不一致，已停止更新。')
                if self._stream.write(block) != len(block):
                    raise OSError('安装包写入不完整。')
                self._checksum.update(block)
            self.progress.emit(self._received, asset.size)
        except ValueError as exc:
            self._source_failed(str(exc))
        except OSError as exc:
            self._fail(str(exc))

    def _download_finished(self) -> None:
        reply, asset = self._reply, self._asset
        if reply is None or asset is None:
            return
        code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if code in (301, 302, 303, 307, 308):
            target = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
            url = reply.url().resolved(target).toString() if isinstance(target, QUrl) else ''
            if not allowed_download_url(url) or self._redirects >= 5:
                self._source_failed('下载地址跳转无效，已停止更新。')
                return
            self._redirects += 1
            self._release_reply()
            self._request(url)
            return
        if code != 200 or reply.error() != QNetworkReply.NetworkError.NoError:
            self._record_wait(reply, code)
            self._source_failed(f'下载失败，请检查网络后重试。（HTTP {code or "无响应"}）')
            return
        self._read()
        if self._reply is not reply:
            return
        try:
            if self._received != asset.size or self._checksum.hexdigest() != asset.sha256:
                raise ValueError('安装包完整性校验失败，已删除不完整下载，请重试。')
            self._stream.close()
            self._stream = None
            path = self._partial.with_suffix('')
            self._partial.replace(path)
            self._partial = None
            self._verified = (asset, path)
            self._deadline.stop()
            self._release_reply()
            if self._offer is not None:
                self._set_state('verifying')
                self._verify_package(self._offer, path)
            else:
                self._launch(path)
        except ValueError as exc:
            self._source_failed(str(exc))
        except OSError as exc:
            self._fail(str(exc))

    def _launch(self, path: Path) -> None:
        if self._cancel_requested:
            return
        self._set_state('installing')
        self.status.emit('安装包已校验，正在启动安装；完成后将自动重启。')
        if self._cancel_requested:
            return
        self._thread = _InstallerThread(self.root, path, self._asset, self)
        self._thread.finished.connect(self._launch_finished)
        self.launching_changed.emit(True)
        try:
            self._thread.start()
        except Exception as exc:
            self._thread.deleteLater()
            self._thread = None
            self.launching_changed.emit(False)
            self._fail(f'无法启动更新任务：{exc}')

    def _verify_package(self, offer=None, path=None):
        if self._cancel_requested:
            return
        self._package_thread = _PackageThread(self.root, self, offer=offer, path=path)
        self._package_thread.finished.connect(self._package_finished)
        try:
            self._package_thread.start()
        except Exception as exc:
            self._package_thread.deleteLater()
            self._package_thread = None
            self._fail(f'无法启动核验任务：{exc}')

    def _package_finished(self):
        thread = self._package_thread
        if thread is None:
            return
        if not thread.wait(0):
            QTimer.singleShot(10, self._package_finished)
            return
        self._package_thread = None
        prepared, error = thread.prepared, thread.error
        cancelled = thread.cancelled.is_set() or self._cancel_requested
        thread.deleteLater()
        if prepared is not None and not cancelled:
            self.diagnostic('update_package_ready', version=prepared.offer.asset.version,
                            job_id=prepared.path.parent.name, restored=thread.offer is None)
            self.ready = prepared
            self._offer, self._asset = prepared.offer, prepared.offer.asset
            self._verified = (self._asset, prepared.path)
            if self._install_when_ready:
                self.ready_changed.emit(prepared)
                if self.ready != prepared:
                    if not self._cancel_requested:
                        self._fail('更新目标已经变化，请重新检查更新后确认安装。')
                    return
                self._launch(prepared.path)
                return
            self._idle()
            self.ready_changed.emit(prepared)
        elif error and not cancelled:
            self.ready = self._verified = None
            try:
                forget_prepared_update(self.root)
            except (OSError, ValueError, TypeError):
                pass
            self.ready_changed.emit(None)
            self._fail(error)
            return
        else:
            self._idle()
        self.finished.emit()
        queued, self._queued_request = self._queued_request, None
        if queued is not None:
            self._start(queued[0], queued[1], install=queued[2])

    def _launch_finished(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if not thread.wait(0):
            QTimer.singleShot(10, self._launch_finished)
            return
        self._thread = None
        launched, error = thread.launched, thread.error
        cancelled = thread.cancelled.is_set()
        if thread.invalid_package:
            self._verified = None
            self.ready = None
            try:
                forget_prepared_update(self.root)
            except (OSError, ValueError, TypeError):
                pass
            self.ready_changed.emit(None)
        thread.deleteLater()
        self.launching_changed.emit(False)
        self._idle()
        if launched:
            self.install_started.emit()
        elif error and not cancelled:
            self.failed.emit(error)
        self.finished.emit()

    def _release_reply(self) -> None:
        reply, self._reply = self._reply, None
        if reply is not None:
            slots, self._reply_slots = self._reply_slots, None
            reply.readyRead.disconnect(slots[0])
            reply.finished.disconnect(slots[1])
            if not reply.isFinished():
                reply.abort()
            reply.deleteLater()

    def _discard_partial(self) -> str:
        issue = ''
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError as exc:
                issue = f'关闭临时文件失败：{exc}'
            self._stream = None
        if self._partial is not None:
            try:
                self._partial.unlink(missing_ok=True)
            except OSError as exc:
                issue = f'临时下载未能删除，请稍后清理 {self._partial}：{exc}'
            self._partial = None
        return issue

    def _idle(self) -> None:
        self._install_when_ready = False
        self._set_busy(False)
        self._set_state('ready' if self.ready is not None else 'idle')

    def _fail(self, message: str) -> None:
        manual = self._install_when_ready
        self.diagnostic('update_prepare_failed', manual=manual, stage=self.state, reason=message)
        self._deadline.stop()
        self._release_reply()
        cleanup_issue = self._discard_partial()
        self._idle()
        (self.failed if manual else self.background_failed).emit(message + ('\n' + cleanup_issue if cleanup_issue else ''))
        self.finished.emit()

    def cancel(self) -> None:
        self._cancel_requested = True
        self._queued_request = None
        self._install_when_ready = False
        self.blocking_changed.emit(self.blocking)
        self._deadline.stop()
        self._release_reply()
        cleanup_issue = self._discard_partial()
        if cleanup_issue:
            self.failed.emit(cleanup_issue)
        if self._thread is not None:
            self._thread.cancelled.set()
        elif self._package_thread is not None:
            self._package_thread.cancelled.set()
        elif self.busy:
            self._idle()
            self.finished.emit()
