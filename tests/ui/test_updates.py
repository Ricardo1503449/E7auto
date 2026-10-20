import json

import pytest
from PySide6.QtNetwork import QNetworkReply

from e7auto import __version__
from e7auto.ui.updates import LATEST_RELEASE_API, UpdateChecker, UpdateController, release_details


@pytest.mark.parametrize("tag, newer", [("v1.3.4", False), ("v1.4.0", False), ("v1.4.1", True), ("v1.10.0", True)])
def test_release_version_compares_numeric_components(tag, newer):
    version, available, notes = release_details(json.dumps({"tag_name": tag, "draft": False, "prerelease": False}).encode())
    assert version == f"v{tag.removeprefix('v')}" and available is newer
    assert notes == ""


@pytest.mark.parametrize("data", [[], {}, {"tag_name": 7}, {"tag_name": "v2.0.0-beta", "draft": False, "prerelease": False},
                                  {"tag_name": "v2.0.0", "draft": True, "prerelease": False},
                                  {"tag_name": "v2.0.0", "draft": False, "prerelease": True}])
def test_release_version_rejects_unknown_or_nonfinal_releases(data):
    with pytest.raises(ValueError):
        release_details(json.dumps(data).encode())


@pytest.mark.parametrize("body, expected", [(None, ""), ("", ""), ("## 改进\n- 增加设置页\n", "## 改进\n- 增加设置页")])
def test_release_details_preserves_notes_and_accepts_absent_notes(body, expected):
    data = {"tag_name": "v9.0.0", "draft": False, "prerelease": False, "body": body}
    assert release_details(json.dumps(data).encode()) == ("v9.0.0", True, expected)


def test_release_details_rejects_invalid_notes_type():
    data = {"tag_name": "v9.0.0", "draft": False, "prerelease": False, "body": ["unrecognized"]}
    with pytest.raises(ValueError, match="改动日志"):
        release_details(json.dumps(data).encode())


class FakeReply(QNetworkReply):
    """Stub transport while retaining Qt's native response-header API."""

    def __init__(self, payload=b"", status=200, headers=None):
        super().__init__()
        self.payload, self.status = payload, status
        self.aborted = False
        for name, value in (headers or {}).items():
            self.setRawHeader(name, value)

    def attribute(self, _attribute):
        return self.status

    def error(self):
        return QNetworkReply.NetworkError.OperationCanceledError if self.aborted or self.status != 200 else QNetworkReply.NetworkError.NoError

    def readAll(self):
        return self.payload

    def abort(self):
        self.aborted = True
        self.finished.emit()


@pytest.mark.parametrize("status, payload, succeeds", [
    (200, b'{"tag_name":"v9.0.0","draft":false,"prerelease":false,"body":"## Changes"}', True),
    (200, b"invalid JSON", False), (404, b"", False), (403, b"", False),
])
def test_async_checker_single_request_completion_and_retry(monkeypatch, status, payload, succeeds):
    checker = UpdateChecker()
    requests, results, failures = [], [], []
    reply = FakeReply(payload, status)
    monkeypatch.setattr(checker._manager, "get", lambda request: requests.append(request.url().toString()) or reply)
    checker.completed.connect(lambda *args: results.append(args))
    checker.failed.connect(failures.append)
    checker.check()
    checker.check()
    assert requests == [LATEST_RELEASE_API]
    assert checker._timeout.isActive()
    reply.finished.emit()
    assert bool(results) is succeeds
    assert bool(failures) is not succeeds
    if succeeds:
        assert results == [("v9.0.0", True, "## Changes")]
    assert checker._reply is None and not checker._timeout.isActive()
    next_reply = FakeReply()
    monkeypatch.setattr(checker._manager, "get", lambda request: next_reply)
    checker.check()
    assert checker._reply is next_reply
    checker.cancel()


@pytest.mark.parametrize("cancel", [False, True])
def test_check_timeout_and_window_close_release_reply(monkeypatch, cancel):
    checker = UpdateChecker()
    reply = FakeReply()
    failures = []
    monkeypatch.setattr(checker._manager, "get", lambda request: reply)
    checker.failed.connect(failures.append)
    checker.check()
    if cancel:
        checker.cancel()
    else:
        checker._timeout.timeout.emit()
    assert reply.aborted and checker._reply is None
    assert not checker._timeout.isActive()
    assert bool(failures) is not cancel


def _controller(tmp_path, monkeypatch):
    now, requests = [100_000.0], []
    controller = UpdateController(tmp_path / "state" / "update_status.json", clock=lambda: now[0])
    monkeypatch.setattr(controller.checker, "check", lambda: requests.append(now[0]))
    return controller, now, requests


def test_automatic_check_is_silent_and_manual_request_joins_it(tmp_path, monkeypatch):
    controller, now, requests = _controller(tmp_path, monkeypatch)
    results, badges, failures = [], [], []
    controller.completed.connect(lambda *args: results.append(args))
    controller.failed.connect(failures.append)
    controller.availability_changed.connect(badges.append)
    controller.check()
    controller.checker.completed.emit("v9.0.0", True, "日志")
    assert badges == [True] and not results and not failures
    assert controller.available and controller.notes == "日志"
    now[0] += 1
    controller.check()
    controller.check(manual=True)
    controller.check(manual=True)
    assert len(requests) == 2
    controller.checker.completed.emit("v9.0.0", True, "新日志")
    assert results == [("v9.0.0", True, "新日志")]
    assert controller.next_check_at == now[0] + 6 * 60 * 60
    controller.cancel()


@pytest.mark.parametrize("has_update", [False, True])
def test_failure_preserves_confirmed_result_and_only_manual_check_reports_it(tmp_path, monkeypatch, has_update):
    controller, now, requests = _controller(tmp_path, monkeypatch)
    failures = []
    controller.failed.connect(failures.append)
    if has_update:
        controller.check()
        controller.checker.completed.emit("v9.0.0", True, "保留日志")
    controller.check()
    controller.checker.failed.emit("网络失败")
    assert controller.available is has_update
    assert controller.latest_version == ("v9.0.0" if has_update else None)
    assert controller.notes == ("保留日志" if has_update else "")
    assert not failures
    assert controller.next_check_at == now[0] + 30 * 60
    controller.check(manual=True)
    controller.checker.failed.emit("手动失败")
    assert failures == ["手动失败"]
    assert controller.next_check_at == now[0] + 60 * 60
    controller.cancel()


def test_cache_restores_result_and_recomputes_reminder_after_upgrade(tmp_path, monkeypatch):
    controller, now, requests = _controller(tmp_path, monkeypatch)
    controller.check()
    controller.checker.completed.emit("v9.0.0", True, "日志")
    path = controller._cache_path
    cached = json.loads(path.read_text(encoding="utf-8"))
    assert "available" not in cached
    restored = UpdateController(path, clock=lambda: now[0])
    assert restored.available and restored.latest_version == "v9.0.0" and restored.notes == "日志"
    assert restored.next_check_at == now[0] + 6 * 60 * 60
    monkeypatch.setattr("e7auto.ui.updates.__version__", "9.0.0")
    upgraded = UpdateController(path, clock=lambda: now[0])
    assert not upgraded.available
    for item in (controller, restored, upgraded):
        item.cancel()


def test_fresh_cache_is_reused_then_checked_at_six_hours(tmp_path, monkeypatch, offline_update_startup):
    controller, now, requests = _controller(tmp_path, monkeypatch)
    controller.check()
    controller.checker.completed.emit(f"v{__version__}", False, "")
    restored = UpdateController(controller._cache_path, clock=lambda: now[0])
    monkeypatch.setattr(restored.checker, "check", lambda: requests.append(now[0]))
    offline_update_startup(restored)
    assert restored._auto_timer.interval() == 1000
    restored._auto_timer.stop()
    restored._check_automatically()
    assert len(requests) == 1
    assert restored._auto_timer.interval() == 6 * 60 * 60 * 1000
    now[0] += 6 * 60 * 60
    restored._check_automatically()
    assert len(requests) == 2
    controller.cancel()
    restored.cancel()


def test_backoff_survives_restart_caps_at_six_hours_and_resets_after_success(tmp_path, monkeypatch):
    controller, now, requests = _controller(tmp_path, monkeypatch)
    for delay in (1800, 3600, 7200, 14400, 21600, 21600):
        controller.check()
        controller.checker.failed.emit("离线")
        assert controller.next_check_at == now[0] + delay
        restored = UpdateController(controller._cache_path, clock=lambda: now[0])
        calls = []
        monkeypatch.setattr(restored.checker, "check", lambda: calls.append(True))
        restored._check_automatically()
        assert not calls
        assert restored._failure_count == controller._failure_count
        restored.cancel()
        now[0] += delay
    controller.check()
    controller.checker.completed.emit("v9.0.0", True, "")
    controller.check()
    controller.checker.failed.emit("离线")
    assert controller.next_check_at == now[0] + 1800
    controller.cancel()


@pytest.mark.parametrize("raw", ["invalid JSON", "[]", "{}", '{"schema_version": 2}'])
def test_invalid_or_absent_cache_does_not_claim_latest_version(tmp_path, raw):
    path = tmp_path / "update_status.json"
    path.write_text(raw, encoding="utf-8")
    controller = UpdateController(path)
    assert controller.latest_version is None and not controller.available
    assert controller.next_check_at == 0
    controller.cancel()


@pytest.mark.parametrize("key, value", [("latest_version", "v2.0.0-beta"), ("notes", []),
                                     ("next_check_at", float("inf")), ("checked_at", True),
                                     ("retry_not_before", -1), ("failure_count", True)])
def test_corrupt_cache_fields_are_discarded_together(tmp_path, monkeypatch, key, value):
    controller, now, requests = _controller(tmp_path, monkeypatch)
    controller.check()
    controller.checker.completed.emit("v9.0.0", True, "日志")
    raw = json.loads(controller._cache_path.read_text(encoding="utf-8"))
    raw[key] = value
    controller._cache_path.write_text(json.dumps(raw), encoding="utf-8")
    restored = UpdateController(controller._cache_path)
    assert restored.latest_version is None and not restored.available
    assert restored.next_check_at == 0
    controller.cancel()
    restored.cancel()


def test_cache_write_failure_cannot_block_result_or_schedule(tmp_path, monkeypatch):
    from pathlib import Path
    controller, now, requests = _controller(tmp_path, monkeypatch)
    def deny_replace(self, target):
        raise OSError("只读状态目录")
    monkeypatch.setattr(Path, "replace", deny_replace)
    results = []
    controller.completed.connect(lambda *args: results.append(args))
    controller.check(manual=True)
    controller.checker.completed.emit("v9.0.0", True, "日志")
    assert controller.available and results == [("v9.0.0", True, "日志")]
    assert controller.next_check_at == now[0] + 21600
    assert not list(controller._cache_path.parent.glob("*.tmp"))
    controller.cancel()


@pytest.mark.parametrize("status, headers, expected", [
    (429, {}, 1060),
    (429, {b"Retry-After": b"120"}, 1120),
    (403, {b"X-RateLimit-Remaining": b"0", b"X-RateLimit-Reset": b"5000"}, 5000),
    (403, {b"Retry-After": b"Thu, 01 Jan 1970 00:33:20 GMT"}, 2000),
    (403, {b"Retry-After": b"invalid", b"X-RateLimit-Remaining": b"0", b"X-RateLimit-Reset": b"nan"}, 1060),
    (403, {}, 0), (200, {b"Retry-After": b"120"}, 0),
])
def test_checker_reads_rate_limit_wait_times(monkeypatch, status, headers, expected):
    monkeypatch.setattr("e7auto.ui.updates.time.time", lambda: 1000)
    assert UpdateChecker._rate_limit_deadline(FakeReply(status=status, headers=headers), status) == expected


def test_rate_limit_blocks_manual_requests_until_reset_and_persists(tmp_path, monkeypatch):
    controller, now, requests = _controller(tmp_path, monkeypatch)
    failures = []
    controller.failed.connect(failures.append)
    controller.check()
    controller.checker.retry_not_before = now[0] + 8 * 60 * 60
    controller.checker.failed.emit("限流")
    assert controller.next_check_at == now[0] + 8 * 60 * 60
    controller.check(manual=True)
    assert len(requests) == 1 and "GitHub 暂时限制请求" in failures[-1]
    restored = UpdateController(controller._cache_path, clock=lambda: now[0])
    monkeypatch.setattr(restored.checker, "check", lambda: requests.append(now[0]))
    restored.check(manual=True)
    assert len(requests) == 1
    now[0] += 8 * 60 * 60
    restored.check(manual=True)
    assert len(requests) == 2
    controller.cancel()
    restored.cancel()


def test_cancel_stops_timers_and_ignores_late_results(tmp_path, monkeypatch, offline_update_startup):
    controller, now, requests = _controller(tmp_path, monkeypatch)
    offline_update_startup(controller)
    controller.check(manual=True)
    results = []
    controller.completed.connect(lambda *args: results.append(args))
    controller.cancel()
    controller.checker.completed.emit("v9.0.0", True, "")
    controller.checker.failed.emit("迟到的失败")
    offline_update_startup(controller)
    controller.check(manual=True)
    assert not controller._auto_timer.isActive()
    assert not results and not controller.available and len(requests) == 1
    assert not controller._cache_path.exists()


@pytest.mark.parametrize("error_type", [OSError, UnicodeError])
def test_cache_stream_failure_keeps_in_memory_update_and_manual_result(tmp_path, monkeypatch, error_type):
    controller, now, requests = _controller(tmp_path, monkeypatch)
    results = []
    controller.completed.connect(lambda *args: results.append(args))
    def fail_write(*args, **kwargs):
        raise error_type("无法写入缓存")
    monkeypatch.setattr("e7auto.ui.updates.json.dump", fail_write)
    controller.check(manual=True)
    controller.checker.completed.emit("v9.0.0", True, "日志")
    assert controller.available and results == [("v9.0.0", True, "日志")]
    assert controller.next_check_at == now[0] + 21600
    assert not list(controller._cache_path.parent.glob("*.tmp"))
    controller.cancel()


@pytest.mark.parametrize("status, headers", [
    (429, {b"Retry-After": b"4000"}),
    (403, {b"X-RateLimit-Remaining": b"0", b"X-RateLimit-Reset": b"104000"}),
])
@pytest.mark.parametrize("manual", [False, True])
def test_actual_checker_rate_limit_reaches_controller_and_resets_after_success(
        tmp_path, monkeypatch, offline_update_startup, status, headers, manual):
    now = [100_000.0]
    monkeypatch.setattr("e7auto.ui.updates.time.time", lambda: now[0])
    controller = UpdateController(tmp_path / "update_status.json", clock=lambda: now[0])
    reply = FakeReply(status=status, headers=headers)
    requests = []
    failures = []
    controller.failed.connect(failures.append)
    monkeypatch.setattr(controller.checker._manager, "get", lambda request: requests.append(True) or reply)
    offline_update_startup(controller)
    try:
        controller.check(manual=manual)
        reply.finished.emit()
        assert not controller.checking and controller.checker._reply is None
        assert not controller.checker._timeout.isActive()
        assert controller.retry_not_before == controller.next_check_at == now[0] + 4000
        assert controller._auto_timer.isActive()
        assert controller._auto_timer.interval() == 4_000_000
        cached = json.loads(controller._cache_path.read_text(encoding="utf-8"))
        assert cached["retry_not_before"] == cached["next_check_at"] == now[0] + 4000
        message = "GitHub 暂时限制请求，请在约 67 分钟后重试。"
        assert failures == ([message] if manual else [])
        controller.check(manual=True)
        assert requests == [True] and failures[-1] == message
        assert not controller.checking
        now[0] += 4000
        success = FakeReply(b'{"tag_name":"v9.0.0","draft":false,"prerelease":false}')
        monkeypatch.setattr(controller.checker._manager, "get", lambda request: requests.append(True) or success)
        controller.check(manual=True)
        assert requests == [True, True] and controller.checking
        success.finished.emit()
        assert controller.available and controller.retry_not_before == 0
        assert not controller.checking and not controller.checker._timeout.isActive()
        assert controller._auto_timer.interval() == 6 * 60 * 60 * 1000
    finally:
        controller.cancel()


@pytest.mark.parametrize("operation", ["cancel", "timeout"])
def test_controller_cancels_real_pending_reply_or_reports_timeout(tmp_path, monkeypatch, operation):
    controller = UpdateController(tmp_path / "update_status.json")
    reply = FakeReply()
    monkeypatch.setattr(controller.checker._manager, "get", lambda request: reply)
    failures = []
    controller.failed.connect(failures.append)
    controller.check(manual=True)
    if operation == "cancel":
        controller.cancel()
    else:
        controller.checker._timeout.timeout.emit()
    assert reply.aborted and controller.checker._reply is None
    assert not controller._checking and not controller.checker._timeout.isActive()
    assert bool(failures) is (operation == "timeout")
    assert controller.latest_version is None
    controller.cancel()


def test_release_asset_is_delivered_with_notes_and_cleared_on_next_check(tmp_path, monkeypatch):
    name = 'E7auto_v9.0.0_Setup_x64.exe'
    item = {'name': name, 'size': 123, 'state': 'uploaded', 'digest': 'sha256:' + 'a' * 64,
            'browser_download_url': f'https://github.com/Ricardo1503449/E7auto/releases/download/v9.0.0/{name}'}
    payload = json.dumps({'tag_name': 'v9.0.0', 'draft': False, 'prerelease': False,
                          'body': '同一次响应的日志', 'assets': [item]}).encode()
    controller = UpdateController(tmp_path / 'update_status.json')
    reply = FakeReply(payload)
    monkeypatch.setattr(controller.checker._manager, 'get', lambda request: reply)
    results = []
    controller.completed.connect(lambda *args: results.append((args, controller.asset)))
    controller.check(manual=True)
    reply.finished.emit()
    assert results[0][0] == ('v9.0.0', True, '同一次响应的日志')
    assert results[0][1].sha256 == 'a' * 64 and results[0][1].name == name
    next_reply = FakeReply(b'{"tag_name":"v1.4.0","draft":false,"prerelease":false}')
    monkeypatch.setattr(controller.checker._manager, 'get', lambda request: next_reply)
    controller.check(manual=True)
    assert controller.checker.asset is None
    next_reply.finished.emit()
    assert controller.asset is None and not controller.available
    controller.cancel()
