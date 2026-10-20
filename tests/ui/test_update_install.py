from dataclasses import replace
import hashlib
import json

import pytest
from PySide6.QtCore import QIODevice, QUrl
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from e7auto.ui import update_install
from e7auto.ui.main_window import MainWindow
from e7auto.ui.updates import PROJECT_URL, ReleaseAsset, release_asset
from tests.helpers.qt import wait_for


DATA = b'MZ' + b'installer test payload' * 10_000


def asset_for(data=DATA):
    name = 'E7auto_v9.0.0_Setup_x64.exe'
    return ReleaseAsset('v9.0.0', name, f'{PROJECT_URL}/releases/download/v9.0.0/{name}',
                        len(data), hashlib.sha256(data).hexdigest())


class DownloadReply(QNetworkReply):
    def __init__(self, data=DATA, *, status=200, error=None, redirect=None, url=None):
        super().__init__()
        self.data = data
        self.offset = 0
        self.aborted = False
        self.setUrl(QUrl(url or asset_for().url))
        self.setAttribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute, status)
        if redirect:
            self.setAttribute(QNetworkRequest.Attribute.RedirectionTargetAttribute, QUrl(redirect))
        if error is not None:
            self.setError(error, 'test network error')
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def bytesAvailable(self):
        return len(self.data) - self.offset + super().bytesAvailable()

    def readData(self, size):
        block = self.data[self.offset:self.offset + size]
        self.offset += len(block)
        return block

    def complete(self):
        self.setFinished(True)
        self.readyRead.emit()
        if not self.aborted:
            self.finished.emit()

    def abort(self):
        self.aborted = True
        self.setError(QNetworkReply.NetworkError.OperationCanceledError, 'cancelled')
        self.setFinished(True)
        self.finished.emit()


@pytest.fixture
def downloader(tmp_path, monkeypatch):
    monkeypatch.setattr(update_install, 'validate_installation', lambda root: None)
    controller = update_install.UpdateInstaller(tmp_path)
    calls, failures, started = [], [], []
    monkeypatch.setattr(update_install, 'launch_update', lambda *args: calls.append(args) or True)
    controller.failed.connect(failures.append)
    controller.install_started.connect(lambda: started.append(True))
    yield controller, calls, failures, started
    controller.cancel()
    wait_for(lambda: not controller.launching)


def install_reply(controller, monkeypatch, reply):
    requests = []
    monkeypatch.setattr(controller._manager, 'get', lambda request: requests.append(request) or reply)
    return requests


def test_verified_native_reply_download_hands_off_exact_file_once(downloader, monkeypatch):
    controller, calls, failures, started = downloader
    asset = asset_for()
    reply = DownloadReply()
    requests = install_reply(controller, monkeypatch, reply)
    controller.start(asset)
    controller.start(asset)
    assert len(requests) == 1 and controller.busy
    assert requests[0].attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
    reply.complete()
    wait_for(lambda: started)
    assert len(calls) == 1 and not failures and not controller.busy
    assert calls[0][1].read_bytes() == DATA
    assert calls[0][2:5] == (asset.version, asset.size, asset.sha256)
    assert not list(controller.root.rglob('*.part'))
    request = json.loads((calls[0][1].parent / 'request.json').read_text(encoding='utf-8'))
    assert request['sha256'] == asset.sha256


@pytest.mark.parametrize('kind', ['truncated', 'oversize', 'digest', 'http', 'network', 'timeout', 'cancel'])
def test_failed_or_cancelled_download_never_installs_and_discards_partial(downloader, monkeypatch, kind):
    controller, calls, failures, started = downloader
    data = DATA[:-1] if kind == 'truncated' else DATA + b'x' if kind == 'oversize' else DATA
    reply = DownloadReply(data, status=503 if kind == 'http' else 200,
                          error=QNetworkReply.NetworkError.SslHandshakeFailedError if kind == 'network' else None)
    install_reply(controller, monkeypatch, reply)
    asset = replace(asset_for(), sha256='0' * 64) if kind == 'digest' else asset_for()
    controller.start(asset)
    if kind == 'timeout':
        controller._deadline.timeout.emit()
    elif kind == 'cancel':
        controller.cancel()
        reply.finished.emit()  # Late completion must not resurrect the cancelled download.
    else:
        reply.complete()
    assert not controller.busy and not calls and not started
    assert bool(failures) is (kind != 'cancel')
    assert not list(controller.root.rglob('*.part'))
    assert not list(controller.root.rglob('*.exe'))


def test_disk_failure_is_reported_without_installation(downloader, monkeypatch):
    controller, calls, failures, started = downloader
    reply = DownloadReply()
    install_reply(controller, monkeypatch, reply)
    controller.start(asset_for())
    original = controller._stream
    class FailedWriter:
        def write(self, block): raise OSError('disk full')
        def close(self): original.close()
    controller._stream = FailedWriter()
    reply.complete()
    assert failures == ['disk full'] and not calls and not started and not controller.busy
    assert not list(controller.root.rglob('*.part'))


@pytest.mark.parametrize('target, allowed', [
    ('https://release-assets.githubusercontent.com/test?signature=abc', True),
    ('http://release-assets.githubusercontent.com/test', False),
    ('https://evil.test/setup.exe', False),
    ('https://github.com.evil.test/setup.exe', False),
    ('https://user:password@github.com/setup.exe', False),
    ('file:///C:/Windows/notepad.exe', False),
])
def test_redirects_require_https_github_hosts(downloader, monkeypatch, target, allowed):
    controller, calls, failures, started = downloader
    first = DownloadReply(b'', status=302, redirect=target)
    second = DownloadReply(url=target)
    replies, requests = iter([first, second]), []
    monkeypatch.setattr(controller._manager, 'get', lambda request: requests.append(request.url().toString()) or next(replies))
    controller.start(asset_for())
    first.complete()
    if allowed:
        assert requests[-1] == target
        second.complete()
        wait_for(lambda: started)
        assert len(calls) == 1 and not failures
    else:
        assert len(requests) == 1 and failures and not calls and not controller.busy


def test_cancelled_uac_keeps_verified_package_and_retry_does_not_download_again(downloader, monkeypatch):
    controller, calls, failures, started = downloader
    reply = DownloadReply()
    requests = install_reply(controller, monkeypatch, reply)
    def reject(*args): raise OSError(1223, 'UAC cancelled')
    monkeypatch.setattr(update_install, 'launch_update', reject)
    controller.start(asset_for())
    reply.complete()
    wait_for(lambda: failures)
    assert not started and not controller.busy
    verified = controller._verified[1]
    assert verified.is_file()
    monkeypatch.setattr(update_install, 'launch_update', lambda *args: calls.append(args) or True)
    controller.start(asset_for())
    wait_for(lambda: started)
    assert len(requests) == 1 and calls[0][1] == verified


def test_changed_verified_package_is_not_reused_forever(downloader, monkeypatch):
    controller, calls, failures, started = downloader
    reply = DownloadReply()
    install_reply(controller, monkeypatch, reply)
    def reject(*args): raise ValueError('安装包发生变化')
    monkeypatch.setattr(update_install, 'launch_update', reject)
    controller.start(asset_for())
    reply.complete()
    wait_for(lambda: failures)
    assert controller._verified is None and not started
    second = DownloadReply()
    requests = install_reply(controller, monkeypatch, second)
    monkeypatch.setattr(update_install, 'launch_update', lambda *args: calls.append(args) or True)
    controller.start(asset_for())
    assert len(requests) == 1
    second.complete()
    wait_for(lambda: started)


@pytest.mark.parametrize('asset', [None, replace(asset_for(), url='https://evil.test/setup.exe'),
                                  replace(asset_for(), size=-1), replace(asset_for(), sha256='bad')])
def test_unusable_release_cannot_start_download(downloader, monkeypatch, asset):
    controller, calls, failures, started = downloader
    monkeypatch.setattr(controller._manager, 'get', lambda request: pytest.fail('must not download'))
    controller.start(asset)
    assert failures and not calls and not controller.busy


def test_source_installation_is_refused_before_network_or_file_creation(tmp_path, monkeypatch):
    controller = update_install.UpdateInstaller(tmp_path)
    monkeypatch.setattr(controller._manager, 'get', lambda request: pytest.fail('must not download'))
    errors = []
    controller.failed.connect(errors.append)
    controller.start(asset_for())
    assert '源码' in errors[0] and not list(tmp_path.iterdir())


@pytest.mark.parametrize('invalid', [None, 'missing', 'duplicate', 'digest', 'url', 'size', 'state'])
def test_release_installer_selection_binds_name_version_url_and_digest(invalid):
    asset = asset_for()
    item = {'name': asset.name, 'browser_download_url': asset.url, 'size': asset.size,
            'digest': 'sha256:' + asset.sha256, 'state': 'uploaded'}
    items = [item]
    if invalid == 'missing': items = []
    if invalid == 'duplicate': items.append(item.copy())
    if invalid == 'digest': item['digest'] = None
    if invalid == 'url': item['browser_download_url'] = 'https://evil.test/setup.exe'
    if invalid == 'size': item['size'] = True
    if invalid == 'state': item['state'] = 'starter'
    payload = json.dumps({'tag_name': 'v9.0.0', 'assets': items}).encode()
    assert release_asset(payload, 'v9.0.0') == (asset if invalid is None else None)


def show_update(window):
    controller = window._update_controller
    controller._checking = controller._manual = True
    controller.checker.asset = asset_for()
    controller.checker.completed.emit('v9.0.0', True, '## 改动\n\n' + '更新说明\n' * 100)
    return window._settings_page._update_dialog


def test_dialog_download_progress_escape_cancellation_and_run_guard(tmp_path, monkeypatch, qt_application):
    window = MainWindow(tmp_path)
    window.show()
    monkeypatch.setattr(update_install, 'validate_installation', lambda root: None)
    reply = DownloadReply()
    requests = install_reply(window._update_installer, monkeypatch, reply)
    dialog = show_update(window)
    dialog.action_button.click()
    assert window._is_busy() and dialog.action_button.text() == '下载中…'
    assert not dialog.action_button.isEnabled() and dialog.close_button.text() == '取消'
    assert dialog.download_progress.isVisible()
    window._launch_run('shop')
    assert window._preparation_thread is None
    dialog.update_requested.emit()
    assert len(requests) == 1
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    wait_for(lambda: window._settings_page._update_dialog is None)
    assert reply.aborted and not window._is_busy() and window.isVisible()
    assert not list(tmp_path.rglob('*.part'))
    window.close()


def test_ui_failed_download_keeps_notes_and_reenables_retry(tmp_path, monkeypatch, qt_application):
    window = MainWindow(tmp_path)
    window.show()
    monkeypatch.setattr(update_install, 'validate_installation', lambda root: None)
    reply = DownloadReply(b'', status=503)
    install_reply(window._update_installer, monkeypatch, reply)
    dialog = show_update(window)
    dialog.action_button.click()
    reply.complete()
    assert window.isVisible() and not window._is_busy()
    assert dialog.action_button.isEnabled() and dialog.action_button.text() == '重试更新'
    assert '下载失败' in dialog.install_status.text() and '更新说明' in dialog.notes_view.toPlainText()
    assert dialog.close_button.text() == '关闭'
    window.close()


def test_successful_installer_launch_closes_application_only_after_handoff(tmp_path, monkeypatch, qt_application):
    window = MainWindow(tmp_path)
    window.show()
    monkeypatch.setattr(update_install, 'validate_installation', lambda root: None)
    monkeypatch.setattr(update_install, 'launch_update', lambda *args: True)
    reply = DownloadReply()
    install_reply(window._update_installer, monkeypatch, reply)
    dialog = show_update(window)
    dialog.action_button.click()
    assert window.isVisible()
    reply.complete()
    wait_for(lambda: not window.isVisible())
    assert window._close_requested and not window._update_installer.launching


def test_real_loopback_http_stream_reaches_verified_handoff(downloader, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from PySide6.QtNetwork import QNetworkProxy
    controller, calls, failures, started = downloader
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(DATA)))
            self.end_headers()
            for offset in range(0, len(DATA), 8192):
                self.wfile.write(DATA[offset:offset + 8192])
                self.wfile.flush()
        def log_message(self, *_args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = controller._request
    monkeypatch.setattr(controller, '_request', lambda _url: original(f'http://127.0.0.1:{server.server_port}/installer'))
    controller._manager.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
    progress = []
    controller.progress.connect(lambda received, total: progress.append((received, total)))
    try:
        controller.start(asset_for())
        wait_for(lambda: started or failures)
        assert started and not failures and calls[0][1].read_bytes() == DATA
        assert progress[-1] == (len(DATA), len(DATA))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('launches', [False, True])
def test_window_close_waits_for_installer_thread_and_does_not_kill_it(tmp_path, monkeypatch, launches):
    from threading import Event
    entered, proceed = Event(), Event()
    window = MainWindow(tmp_path)
    window.show()
    monkeypatch.setattr(update_install, 'validate_installation', lambda root: None)
    def launch(*args):
        entered.set()
        proceed.wait(2)
        return launches
    monkeypatch.setattr(update_install, 'launch_update', launch)
    reply = DownloadReply()
    install_reply(window._update_installer, monkeypatch, reply)
    dialog = show_update(window)
    dialog.action_button.click()
    reply.complete()
    wait_for(entered.is_set)
    assert not dialog.close_button.isEnabled()
    try:
        window.close()
        assert window.isVisible() and window._close_requested
    finally:
        proceed.set()
    wait_for(lambda: not window.isVisible())
    assert not window._update_installer.launching


def test_update_ui_objects_are_released_with_window(tmp_path, qt_application):
    import gc
    import weakref
    from tests.helpers.qt import dispose_widgets
    window = MainWindow(tmp_path)
    window.show()
    show_update(window)
    refs = [weakref.ref(window), weakref.ref(window._update_installer)]
    dispose_widgets(qt_application, [window, window._overlay])
    del window
    gc.collect()
    assert all(ref() is None for ref in refs)


@pytest.mark.parametrize('size', [(760, 560), (1180, 760)])
def test_update_progress_and_buttons_fit_small_and_normal_window(tmp_path, qt_application, size):
    window = MainWindow(tmp_path)
    window.resize(*size)
    window.show()
    dialog = show_update(window)
    dialog.set_install_busy(True)
    dialog.set_download_progress(1048576, 85 * 1048576)
    qt_application.processEvents()
    assert dialog.width() < window.width() and dialog.height() < window.height()
    for widget in (dialog.action_button, dialog.close_button, dialog.download_progress):
        top_left = widget.mapTo(dialog, widget.rect().topLeft())
        bottom_right = widget.mapTo(dialog, widget.rect().bottomRight())
        assert dialog.rect().contains(top_left) and dialog.rect().contains(bottom_right)
        assert not widget.toolTip()
    assert dialog.notes_view.verticalScrollBar().maximum() > 0
    window.close()
