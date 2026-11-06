import hashlib
import json
import pytest
from PySide6.QtCore import QIODevice, QUrl
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

from e7auto.core.update_feed import FEED_URLS, MIRROR_PREFIX, ReleaseAsset, PROJECT_URL
from e7auto.ui import updates, update_install
from tests.helpers.updates import FeedReply, feed, verify_test_signature


@pytest.fixture
def checker(monkeypatch):
    item = updates.UpdateChecker()
    monkeypatch.setattr(updates, 'verify_signature', verify_test_signature)
    yield item
    item.cancel()


@pytest.mark.parametrize('kind', ['mirror_old', 'origin_old', 'origin_error', 'mirror_invalid', 'mirror_oversize', 'conflict', 'both_error', 'notes_differ'])
def test_parallel_sources_choose_verified_version_and_emit_once(checker, monkeypatch, kind):
    first, second = FeedReply(feed()), FeedReply(feed())
    if kind == 'mirror_old': first = FeedReply(feed('v8.0.0'))
    if kind == 'origin_old': second = FeedReply(feed('v8.0.0'))
    if kind == 'origin_error': second = FeedReply(status=503)
    if kind == 'mirror_invalid': first = FeedReply(b'<html>not a feed</html>')
    if kind == 'mirror_oversize': first = FeedReply(b'x' * (updates.MAX_FEED_BYTES + 1))
    if kind == 'conflict': first = FeedReply(feed(sha256='b' * 64))
    if kind == 'both_error': first, second = FeedReply(status=503), FeedReply(status=429)
    if kind == 'notes_differ': first, second = FeedReply(feed(notes='cached notes')), FeedReply(feed(notes='official notes'))
    replies, requests, results, errors = iter((first, second)), [], [], []
    monkeypatch.setattr(checker._manager, 'get', lambda request: requests.append(request.url().toString()) or next(replies))
    checker.completed.connect(lambda *args: results.append(args))
    checker.failed.connect(errors.append)
    checker.check()
    checker.check()
    assert requests == list(FEED_URLS) and all('api.github.com' not in url for url in requests)
    first.complete()
    assert not results and not errors
    second.complete()
    assert len(results) + len(errors) == 1
    if kind in ('conflict', 'both_error'):
        assert errors and checker.asset is None
    else:
        assert results[0][0] == 'v9.0.0' and checker.asset.version == 'v9.0.0'
        if kind == 'notes_differ': assert results[0][2] == 'official notes'


def test_one_source_cooldown_does_not_block_the_other_and_timeout_uses_valid_result(checker, monkeypatch):
    checker.source_cooldowns[FEED_URLS[0]] = updates.time.time() + 3600
    reply, requests, results = FeedReply(feed()), [], []
    monkeypatch.setattr(checker._manager, 'get', lambda request: requests.append(request.url().toString()) or reply)
    checker.completed.connect(lambda *args: results.append(args))
    checker.check()
    reply.complete()
    assert requests == [FEED_URLS[1]] and results and checker.retry_not_before == 0
    checker.source_cooldowns.clear()
    first, stalled = FeedReply(feed()), FeedReply()
    replies = iter((first, stalled))
    monkeypatch.setattr(checker._manager, 'get', lambda request: next(replies))
    checker.check()
    first.complete()
    checker._timeout.timeout.emit()
    assert len(results) == 2 and stalled.aborted and not checker._replies


def test_partial_limit_is_persisted_even_when_other_source_is_cancelled(tmp_path, monkeypatch):
    controller = updates.UpdateController(tmp_path / 'state/update_status.json')
    limited, pending = FeedReply(status=429, headers={b'Retry-After': b'120'}), FeedReply()
    replies = iter((limited, pending))
    monkeypatch.setattr(controller.checker._manager, 'get', lambda request: next(replies))
    controller.check()
    limited.complete()
    controller.cancel()
    restored = updates.UpdateController(controller._cache_path)
    try:
        assert restored.checker.source_cooldowns[FEED_URLS[0]] > updates.time.time()
        assert restored.retry_not_before == 0
    finally:
        restored.cancel()


@pytest.mark.parametrize('target,allowed', [(FEED_URLS[0].replace('gh-proxy.org', 'gh-proxy.com'), True),
    ('https://evil.test/feed.json', False)])
def test_feed_redirects_are_restricted_to_the_same_feed(checker, monkeypatch, target, allowed):
    class RedirectReply(FeedReply):
        def attribute(self, attribute):
            if attribute == QNetworkRequest.Attribute.RedirectionTargetAttribute:
                return QUrl(target)
            return super().attribute(attribute)
    first, other, redirected = RedirectReply(status=302), FeedReply(status=503), FeedReply(feed())
    first.setUrl(QUrl(FEED_URLS[0]))
    replies, results, errors, requests = iter((first, other, redirected)), [], [], []
    monkeypatch.setattr(checker._manager, 'get', lambda request: requests.append(request.url().toString()) or next(replies))
    checker.completed.connect(lambda *args: results.append(args))
    checker.failed.connect(errors.append)
    checker.check()
    first.complete()
    other.complete()
    if allowed: redirected.complete()
    assert bool(results) is allowed and bool(errors) is not allowed
    assert len(requests) == (3 if allowed else 2)


def test_real_loopback_feed_responses_use_the_bounded_qt_reader(checker, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from PySide6.QtNetwork import QNetworkProxy
    from tests.helpers.qt import wait_for
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            data = feed('v8.0.0' if self.path == '/mirror' else 'v9.0.0')
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *_args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(checker, 'SOURCE_URLS', (root + '/mirror', root + '/official'))
    checker._manager.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
    results = []
    checker.completed.connect(lambda *args: results.append(args))
    try:
        checker.check()
        wait_for(lambda: bool(results))
        assert results == [('v9.0.0', True, '## Changes')]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_old_api_wait_migrates_and_recent_result_joins_without_network(tmp_path, monkeypatch):
    path = tmp_path / 'state/update_status.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'schema_version': 1, 'latest_version': 'v8.0.0', 'notes': 'old',
        'checked_at': 999, 'next_check_at': 9999999, 'retry_not_before': 9999999, 'failure_count': 2}))
    controller = updates.UpdateController(path, clock=lambda: 1000)
    calls, results = [], []
    monkeypatch.setattr(controller.checker, 'check', lambda: calls.append(True))
    controller.completed.connect(lambda *args: results.append(args))
    try:
        assert controller.retry_not_before == controller.next_check_at == 0 and controller.available
        controller.check()
        controller.checker.completed.emit('v9.0.0', True, 'new')
        controller.check(manual=True)
        assert calls == [True] and results == [('v9.0.0', True, 'new')]
        assert json.loads(path.read_text())['schema_version'] == 2
    finally:
        controller.cancel()


DATA = b'MZ' + b'network fixture' * 5000


class DownloadReply(QNetworkReply):
    def __init__(self, data=DATA, status=200, error=None):
        super().__init__()
        self.data, self.offset, self.aborted = data, 0, False
        self.setAttribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute, status)
        if error: self.setError(error, 'fixture error')
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def bytesAvailable(self): return len(self.data) - self.offset + super().bytesAvailable()
    def readData(self, size):
        block = self.data[self.offset:self.offset + size]
        self.offset += len(block)
        return block
    def complete(self):
        self.setFinished(True)
        self.readyRead.emit()
        if not self.aborted: self.finished.emit()
    def abort(self):
        self.aborted = True
        self.setFinished(True)
        self.finished.emit()


@pytest.mark.parametrize('kind', ['http', 'ssl', 'truncated', 'digest', 'oversize'])
def test_download_failover_restarts_bytes_and_keeps_the_same_asset(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(update_install, 'validate_installation', lambda root: None)
    item = update_install.UpdateInstaller(tmp_path)
    name = 'E7auto_v9.0.0_Setup_x64.exe'
    asset = ReleaseAsset('v9.0.0', name, f'{PROJECT_URL}/releases/download/v9.0.0/{name}', len(DATA), hashlib.sha256(DATA).hexdigest())
    bad = DATA[:-1] if kind == 'truncated' else b'x' * len(DATA) if kind == 'digest' else DATA + b'x' if kind == 'oversize' else DATA
    first = DownloadReply(bad, status=503 if kind == 'http' else 200,
        error=QNetworkReply.NetworkError.SslHandshakeFailedError if kind == 'ssl' else None)
    second = DownloadReply()
    replies, requests, files, errors, progress = iter((first, second)), [], [], [], []
    monkeypatch.setattr(item._manager, 'get', lambda request: requests.append(request.url().toString()) or next(replies))
    def capture(path):
        files.append(path.read_bytes())
        item._idle()
    monkeypatch.setattr(item, '_launch', capture)
    item.failed.connect(errors.append)
    item.progress.connect(lambda received, total: progress.append(received))
    try:
        item.start(asset)
        first.complete()
        assert item.busy and not files and not errors and len(requests) == 2
        second.complete()
        assert files == [DATA] and not errors and not item.busy
        assert requests == [MIRROR_PREFIX + asset.url, asset.url]
        assert progress.count(0) == 2 and not list(tmp_path.rglob('*.part'))
    finally:
        item.cancel()


@pytest.mark.parametrize('operation', ['cancel', 'deadline', 'exhaust'])
def test_failover_is_bounded_and_cancel_never_installs(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(update_install, 'validate_installation', lambda root: None)
    item = update_install.UpdateInstaller(tmp_path)
    name = 'E7auto_v9.0.0_Setup_x64.exe'
    asset = ReleaseAsset('v9.0.0', name, f'{PROJECT_URL}/releases/download/v9.0.0/{name}', len(DATA), hashlib.sha256(DATA).hexdigest())
    first, second = DownloadReply(status=503), DownloadReply(status=503)
    replies, requests, errors, installed = iter((first, second)), [], [], []
    monkeypatch.setattr(item._manager, 'get', lambda request: requests.append(True) or next(replies))
    monkeypatch.setattr(item, '_launch', lambda path: installed.append(path))
    item.failed.connect(errors.append)
    item.start(asset)
    if operation == 'deadline': item._deadline.timeout.emit()
    else:
        first.complete()
        if operation == 'cancel': item.cancel()
        else: second.complete()
    assert not item.busy and not installed and not list(tmp_path.rglob('*.part'))
    assert len(requests) == (1 if operation == 'deadline' else 2)
    assert len(errors) == int(operation != 'cancel')


def test_rate_limit_dialog_has_release_link_and_disables_immediate_retry(tmp_path, monkeypatch, qt_application):
    from e7auto.ui.main_window import MainWindow
    window = MainWindow(tmp_path)
    controller = window._update_controller
    controller.retry_not_before = controller._clock() + 120
    page = window._settings_page
    opened = []
    monkeypatch.setattr(page, '_open_target', lambda button, target: opened.append(target))
    try:
        page._update_failed('test wait')
        dialog = page._update_dialog
        assert not dialog.action_button.isEnabled() and dialog._retry_timer.isActive()
        qt_application.processEvents()
        for button in (dialog.release_button, dialog.action_button, dialog.close_button):
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        dialog.release_button.click()
        assert opened == [PROJECT_URL + '/releases/latest']
        assert not dialog.release_button.toolTip()
    finally:
        window.close()


def test_download_retry_skips_a_source_until_retry_after_and_ignores_old_callback(tmp_path, monkeypatch):
    monkeypatch.setattr(update_install, 'validate_installation', lambda root: None)
    item = update_install.UpdateInstaller(tmp_path)
    name = 'E7auto_v9.0.0_Setup_x64.exe'
    asset = ReleaseAsset('v9.0.0', name, f'{PROJECT_URL}/releases/download/v9.0.0/{name}', len(DATA), hashlib.sha256(DATA).hexdigest())
    limited, failed, success = DownloadReply(status=429), DownloadReply(status=503), DownloadReply()
    limited.setRawHeader(b'Retry-After', b'120')
    replies, requests, files = iter((limited, failed, success)), [], []
    monkeypatch.setattr(item._manager, 'get', lambda request: requests.append(request.url().toString()) or next(replies))
    monkeypatch.setattr(item, '_launch', lambda path: (files.append(path.read_bytes()), item._idle()))
    item.start(asset)
    old_finished = item._reply_slots[1]
    limited.complete()
    old_finished()  # A queued callback from the old source must not finish the new reply.
    assert item._reply is failed and item.busy
    failed.complete()
    assert not item.busy
    item.start(asset)
    assert item._reply is success and requests == [MIRROR_PREFIX + asset.url, asset.url, asset.url]
    success.complete()
    assert files == [DATA]
