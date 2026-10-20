"""Cancellable installer download and a verified handoff to Windows setup."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit
import uuid

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from e7auto import __version__
from e7auto.platform.update_install import launch_update, ordinary_path, validate_installation
from e7auto.ui.updates import PROJECT_URL, ReleaseAsset, _version_parts


def allowed_download_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
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


class UpdateInstaller(QObject):
    progress = Signal(int, int)
    status = Signal(str)
    failed = Signal(str)
    busy_changed = Signal(bool)
    launching_changed = Signal(bool)
    install_started = Signal()
    finished = Signal()

    def __init__(self, root: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.root = root
        self.busy = False
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._thread: _InstallerThread | None = None
        self._asset: ReleaseAsset | None = None
        self._verified: tuple[ReleaseAsset, Path] | None = None
        self._stream = None
        self._partial: Path | None = None
        self._received = 0
        self._redirects = 0
        self._checksum = hashlib.sha256()
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(15 * 60 * 1000)
        self._deadline.timeout.connect(self._download_timed_out)

    @property
    def launching(self) -> bool:
        return self._thread is not None

    def start(self, asset: ReleaseAsset | None) -> None:
        if self.busy:
            return
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
            self._asset = asset
            self.busy = True
            self.busy_changed.emit(True)
            if self._verified is not None and self._verified[0] == asset and self._verified[1].is_file():
                self._launch(self._verified[1])
                return
            directory = self.root / 'state' / 'updates' / f'{asset.version}-{uuid.uuid4().hex}'
            ordinary_path(directory)
            directory.mkdir(parents=True, exist_ok=False)
            (directory / 'request.json').write_text(json.dumps({
                'version': asset.version, 'name': asset.name, 'size': asset.size,
                'sha256': asset.sha256,
            }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            self._partial = directory / (asset.name + '.part')
            self._stream = self._partial.open('xb')
            self._received = self._redirects = 0
            self._checksum = hashlib.sha256()
            self.status.emit('正在下载安装包…')
            self.progress.emit(0, asset.size)
            self._deadline.start()
            self._request(asset.url)
        except (OSError, ValueError, TypeError, IndexError) as exc:
            self._fail(str(exc))

    def _request(self, url: str) -> None:
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setRawHeader(b'Accept', b'application/octet-stream')
        request.setRawHeader(b'User-Agent', f'E7auto/{__version__}'.encode('ascii'))
        request.setTransferTimeout(30_000)
        self._reply = self._manager.get(request)
        self._reply.setReadBufferSize(1024 * 1024)
        self._reply.readyRead.connect(self._read)
        self._reply.finished.connect(self._download_finished)

    def _download_timed_out(self) -> None:
        self._fail('下载超时，请检查网络后重试。')

    def _read(self) -> None:
        reply, asset = self._reply, self._asset
        if reply is None or asset is None or self._stream is None:
            return
        code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if code != 200:
            if code is not None and code >= 400:
                self._fail(f'下载失败，请检查网络后重试。（HTTP {code}）')
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
        except (OSError, ValueError) as exc:
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
                self._fail('下载地址跳转无效，已停止更新。')
                return
            self._redirects += 1
            self._release_reply()
            self._request(url)
            return
        if code != 200 or reply.error() != QNetworkReply.NetworkError.NoError:
            self._fail(f'下载失败，请检查网络后重试。（HTTP {code or "无响应"}）')
            return
        self._read()
        if self._reply is None:
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
            self._launch(path)
        except (OSError, ValueError) as exc:
            self._fail(str(exc))

    def _launch(self, path: Path) -> None:
        self.status.emit('安装包已校验，正在启动安装；完成后将自动重启。')
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
            reply.readyRead.disconnect(self._read)
            reply.finished.disconnect(self._download_finished)
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
        self.busy = False
        self.busy_changed.emit(False)

    def _fail(self, message: str) -> None:
        self._deadline.stop()
        self._release_reply()
        cleanup_issue = self._discard_partial()
        self._idle()
        self.failed.emit(message + ('\n' + cleanup_issue if cleanup_issue else ''))
        self.finished.emit()

    def cancel(self) -> None:
        self._deadline.stop()
        self._release_reply()
        cleanup_issue = self._discard_partial()
        if cleanup_issue:
            self.failed.emit(cleanup_issue)
        if self._thread is not None:
            self._thread.cancelled.set()
        elif self.busy:
            self._idle()
            self.finished.emit()
