from datetime import datetime, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import socket
import os
from pathlib import Path
import subprocess
from urllib.request import urlopen

from PySide6.QtCore import QThread, Qt, qInstallMessageHandler
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtNetwork import QNetworkProxy, QNetworkProxyFactory
from PySide6.QtWidgets import QLabel, QPushButton, QWidget
import pytest

from e7auto.core.reporting import ShopRunReport
from e7auto.logging.report_queue import ReportQueue
from e7auto.ui.main_window import MainWindow
from e7auto.ui.preferences import NotificationPreferences, ShopStatisticsPreferences
from e7auto.ui.shop_reporting import ShopReportUploader, ShopReportingController, retry_after_seconds
from tests.helpers.qt import wait_for


@pytest.fixture
def local_proxy_only(monkeypatch):
    monkeypatch.setattr(QNetworkProxyFactory,'systemProxyForQuery',
                        lambda query: [QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)])


@pytest.fixture
def qt_messages():
    messages = []
    previous = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    try:
        yield messages
    finally:
        qInstallMessageHandler(previous)


@pytest.fixture
def settings_window(tmp_path):
    window = MainWindow(tmp_path)
    window._reporting_controller.endpoint = 'http://127.0.0.1'
    window._settings_page._show_statistics_status('')
    try:
        yield window
    finally:
        window.close()
        wait_for(lambda: window._reporting_controller._thread is None)
        window.close()


@pytest.fixture
def server():
    state = {'requests':[],'code':201,'ack':True,'retry_after':'','hold':threading.Event()}
    state['hold'].set()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append((body,dict(self.headers)))
            state['hold'].wait(state.get('hold_timeout',3))
            self.send_response(state['code'])
            self.send_header('Content-Type','application/json')
            if state['retry_after']:
                self.send_header('Retry-After',state['retry_after'])
            self.end_headers()
            try:
                response = {'accepted':True,'run_id':body['run_id'] if state['ack'] else 'wrong'}
                if state.get('padding'): response['padding'] = state['padding']
                self.wfile.write(json.dumps(response).encode())
            except OSError:
                pass
    httpd = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=httpd.serve_forever,daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{httpd.server_port}',state
    state['hold'].set()
    httpd.shutdown(); httpd.server_close(); thread.join(3)


def enqueue(queue,index=1):
    value = ShopRunReport(f'{index:032x}','transfer',3,1,0)
    assert queue.begin(value,queue.generation)
    assert queue.save(value,queue.generation,final=True)
    return value


def test_statistics_runtime_self_check_preserves_user_journal(tmp_path, qt_application, monkeypatch):
    from e7auto.ui import reporting_check

    queue = ReportQueue(tmp_path/'state/shop_reports.sqlite3')
    original = enqueue(queue)
    monkeypatch.setattr(reporting_check, '_read_https_summary',
                        lambda endpoint: pytest.fail('offline self-check must not access the network'))
    result = reporting_check.check_reporting_runtime(tmp_path, temp_root=tmp_path)
    assert result['statistics_runtime_ready'] is True
    assert result['statistics_queue_ready'] is True
    assert result['statistics_tls_ready'] is True
    assert result['statistics_https'] is None
    assert queue.next_due(time.time()).report == original
    assert not list(tmp_path.glob('e7auto-reporting-check-*'))


def test_statistics_runtime_self_check_reports_missing_tls_backend(tmp_path, qt_application, monkeypatch):
    from e7auto.ui import reporting_check

    monkeypatch.setattr(reporting_check.QSslSocket, 'supportsSsl', lambda: False)
    result = reporting_check.check_reporting_runtime(tmp_path, temp_root=tmp_path)
    assert result['statistics_queue_ready'] is True
    assert result['statistics_tls_ready'] is False
    assert result['statistics_runtime_ready'] is False


def test_ack_is_required_and_upload_has_only_five_fields(tmp_path,qt_application,local_proxy_only,server):
    endpoint,state = server
    queue = ReportQueue(tmp_path/'q.sqlite3')
    value = enqueue(queue)
    uploader = ShopReportUploader(queue,endpoint)
    uploader.start()
    try:
        wait_for(lambda: queue.counts()[0] == 0)
        body,headers = state['requests'][0]
        assert body == value.payload()
        assert len(body) == 5
        assert headers['User-Agent'] == 'E7auto'
    finally:
        uploader.stop(); uploader.deleteLater(); qt_application.processEvents()


@pytest.mark.parametrize('code,ack,rejected',[(201,False,False),(503,True,False),(429,True,False),(409,True,True),(422,True,True)])
def test_errors_or_mismatched_ack_keep_record_and_classify_retry(tmp_path,qt_application,local_proxy_only,server,code,ack,rejected):
    endpoint,state = server
    state.update(code=code,ack=ack,retry_after='7200' if code == 429 else '')
    queue = ReportQueue(tmp_path/'q.sqlite3')
    enqueue(queue)
    uploader = ShopReportUploader(queue,endpoint)
    uploader.start()
    try:
        wait_for(lambda: len(state['requests']) == 1 and uploader._reply is None)
        assert queue.counts() == (1,1 if rejected else 0)
        assert queue.next_due(time.time()) is None
        if code == 429:
            assert uploader._retry_not_before >= time.time()+7100
    finally:
        uploader.stop(); uploader.deleteLater(); qt_application.processEvents()


def test_manual_retry_after_transient_failure_succeeds(tmp_path,qt_application,local_proxy_only,server):
    endpoint,state = server
    state['code'] = 503
    queue = ReportQueue(tmp_path/'q.sqlite3'); enqueue(queue)
    uploader = ShopReportUploader(queue,endpoint); uploader.start()
    try:
        wait_for(lambda: len(state['requests']) == 1 and uploader._reply is None)
        state['code'] = 200
        uploader.retry()
        wait_for(lambda: queue.counts() == (0,0))
        assert len(state['requests']) == 2
    finally:
        uploader.stop(); uploader.deleteLater(); qt_application.processEvents()


def test_network_runs_on_separate_thread_and_shutdown_preserves_inflight(tmp_path,qt_application,local_proxy_only,server):
    endpoint,state = server
    state['hold'].clear()
    controller = ShopReportingController(tmp_path)
    controller.endpoint = endpoint
    enqueue(controller.queue)
    controller.start()
    try:
        wait_for(lambda: bool(state['requests']))
        assert controller._uploader._manager.thread() is controller._thread
        assert controller._thread is not qt_application.thread()
        controller.cancel()
        wait_for(lambda: controller._thread is None)
        assert controller.queue.counts() == (1,0)
    finally:
        state['hold'].set(); controller.cancel()
        wait_for(lambda: controller._thread is None)
        controller.deleteLater(); qt_application.processEvents()


def test_pending_uploads_pause_during_game_run(tmp_path,qt_application,local_proxy_only,server):
    endpoint,state = server
    controller = ShopReportingController(tmp_path); controller.endpoint = endpoint
    enqueue(controller.queue)
    controller.set_running(True); controller.start()
    try:
        wait_for(lambda: controller._uploader._timer is not None)
        assert not state['requests']
        assert controller.queue.next_due(time.time()) is None
        controller.set_running(False)
        wait_for(lambda: controller.queue.counts() == (0,0))
    finally:
        controller.cancel(); wait_for(lambda: controller._thread is None)
        controller.deleteLater(); qt_application.processEvents()


def test_statistics_preferences_preserve_notifications_and_epoch(tmp_path):
    path = tmp_path/'state/preferences.json'
    notifications = NotificationPreferences(path)
    preferences = ShopStatisticsPreferences(path)
    assert preferences.load()
    notifications.save(False); preferences.save(False)
    assert not notifications.load() and not preferences.load()
    assert preferences.generation() == 1
    notifications.save(True); preferences.save(True)
    assert notifications.load() and preferences.load() and preferences.generation() == 1


def test_settings_have_no_clear_or_hover_controls_and_capacity_stays_silent(tmp_path,qt_application):
    window = MainWindow(tmp_path)
    try:
        controller = window._reporting_controller
        controller.queue.capacity = 1
        enqueue(controller.queue)
        recorder = controller.recorder(continuous_refresh=False)
        from e7auto.core.domain import RuntimeSnapshot,StopReason
        recorder.observe(RuntimeSnapshot.initial('limit',(),3).finalized(StopReason.MANUAL_STOP))
        window._settings_page._refresh_statistics()
        assert window._settings_page.statistics_pending.text() == '待上传记录：1'
        assert not any(word in window._settings_page.statistics_status.text() for word in ('容量','已满','上限'))
        assert not any('清空' in button.text() for button in window._settings_page.findChildren(QPushButton))
        assert all(not widget.toolTip() for widget in window.findChildren(QWidget))
        window._settings_page.statistics_toggle.setChecked(False)
        assert controller.queue.counts() == (1,0) and not controller.enabled
        assert not window._settings_page.statistics_retry.isEnabled()
    finally:
        window.close()


@pytest.mark.parametrize('value,expected',[(b'60',60),(b'-1',0),(b'nan',0),(b'inf',0),(b'bad',0)])
def test_retry_after_numeric_values(value,expected):
    assert retry_after_seconds(value,1000) == expected


def test_retry_after_http_date():
    text = format_datetime(datetime.fromtimestamp(8200,timezone.utc),usegmt=True).encode()
    assert retry_after_seconds(text,1000) == 7200


def test_connection_refused_retains_record_and_schedules_retry(tmp_path,qt_application,local_proxy_only):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        port = probe.getsockname()[1]
    queue = ReportQueue(tmp_path/'q.sqlite3'); enqueue(queue)
    uploader = ShopReportUploader(queue,f'http://127.0.0.1:{port}'); uploader.start()
    try:
        wait_for(lambda: uploader._reply is None,timeout_ms=13_000)
        assert queue.counts() == (1,0)
        assert queue.next_due(time.time()) is None
    finally:
        uploader.stop(); uploader.deleteLater(); qt_application.processEvents()


def test_actual_ten_second_timeout_keeps_pending_record(tmp_path,qt_application,local_proxy_only,server,qt_messages,caplog):
    endpoint,state = server
    state['hold'].clear(); state['hold_timeout'] = 15
    queue = ReportQueue(tmp_path/'q.sqlite3'); enqueue(queue)
    uploader = ShopReportUploader(queue,endpoint); uploader.start()
    try:
        assert uploader._reply.request().transferTimeout() == 10_000
        wait_for(lambda: uploader._reply is None,timeout_ms=13_000)
        assert queue.counts() == (1,0)
        assert queue.next_due(time.time()) is None
        assert not any('device not open' in message for message in qt_messages)
        assert 'TimeoutError' in caplog.text
    finally:
        state['hold'].set(); uploader.stop(); uploader.deleteLater(); qt_application.processEvents()


def test_desktop_to_actual_local_worker_and_d1(tmp_path,qt_application,local_proxy_only):
    node = os.environ.get('SHOP_STATS_NODE')
    if not node or not os.environ.get('SHOP_STATS_TOOLCHAIN'):
        pytest.skip('install the managed Worker toolchain for local integration')
    helper = Path(__file__).resolve().parents[1]/'scripts/cloudflare/serve_worker.mjs'
    environment = os.environ.copy()
    environment['SHOP_STATS_TEST_OUTPUT'] = str(tmp_path/'worker')
    process = subprocess.Popen([node,str(helper)],env=environment,stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    controller = None
    try:
        line = process.stdout.readline()
        assert line, process.stderr.read()
        endpoint = json.loads(line)['url'].rstrip('/')
        controller = ShopReportingController(tmp_path)
        controller.endpoint = endpoint
        controller.set_running(True)
        recorder = controller.recorder(continuous_refresh=True)
        from e7auto.core.domain import RuntimeSnapshot,StopReason
        value = RuntimeSnapshot.initial('integration',(('covenant_bookmark','书签'),('mystic_medal','奖牌')),3)
        recorder.observe(value.with_incremented_target('covenant_bookmark'))
        controller.start()
        assert controller.queue.next_due(time.time()) is None
        final = value.with_refresh_spent(3).with_incremented_target('covenant_bookmark').with_incremented_target('mystic_medal').finalized(StopReason.MANUAL_STOP)
        recorder.observe(final)
        controller.set_running(False)
        wait_for(lambda: controller.queue.counts() == (0,0),timeout_ms=5000)
        with urlopen(endpoint+'/v1/shop-stats',timeout=3) as response:
            stats = json.load(response)
        assert stats['pools']['continuous']['skystone_spent'] == 3
        assert stats['pools']['continuous']['covenant_purchases'] == 1
        assert stats['pools']['continuous']['mystic_purchases'] == 1
        assert stats['pools']['transfer']['skystone_spent'] == 0
    finally:
        if controller is not None:
            controller.cancel(); wait_for(lambda: controller._thread is None)
            controller.deleteLater(); qt_application.processEvents()
        if process.poll() is None:
            process.communicate('stop\n',timeout=10)
        assert process.returncode == 0


def test_oversized_ack_is_never_used_to_delete_record(tmp_path,qt_application,local_proxy_only,server,qt_messages):
    endpoint,state = server
    state['padding'] = 'x'*20_000
    queue = ReportQueue(tmp_path/'q.sqlite3'); enqueue(queue)
    uploader = ShopReportUploader(queue,endpoint); uploader.start()
    try:
        wait_for(lambda: state['requests'] and uploader._reply is None)
        assert queue.counts() == (1,0)
        assert queue.next_due(time.time()) is None
        assert not any('device not open' in message for message in qt_messages)
    finally:
        uploader.stop(); uploader.deleteLater(); qt_application.processEvents()


def test_system_proxy_is_read_per_request_without_global_changes(tmp_path,qt_application,monkeypatch,server):
    endpoint,state = server
    calls = []
    before = QNetworkProxy.applicationProxy().type()
    def lookup(query):
        calls.append(query.url().toString())
        return [QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)]
    monkeypatch.setattr(QNetworkProxyFactory,'systemProxyForQuery',lookup)
    state['code'] = 503
    queue = ReportQueue(tmp_path/'q.sqlite3'); enqueue(queue)
    uploader = ShopReportUploader(queue,endpoint); uploader.start()
    try:
        wait_for(lambda: state['requests'] and uploader._reply is None)
        state['code'] = 201; uploader.retry()
        wait_for(lambda: queue.counts() == (0,0))
        assert calls == [endpoint+'/v1/shop-runs']*2
        assert QNetworkProxy.applicationProxy().type() == before
    finally:
        uploader.stop(); uploader.deleteLater(); qt_application.processEvents()


def test_retry_feedback_is_immediate_and_recovers_after_failure_and_success(settings_window,qt_application,local_proxy_only,server):
    window = settings_window
    controller, page = window._reporting_controller, window._settings_page
    endpoint, state = server
    controller.endpoint = endpoint
    enqueue(controller.queue)
    state.update(code=503)
    state['hold'].clear()
    window.show()
    window._show_section('settings')
    page._refresh_statistics()
    qt_application.processEvents()
    button = page.statistics_retry
    width = button.width()
    try:
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert controller.uploading and button.text() == '上传中…'
        assert not button.isEnabled() and button._spinner_timer.isActive()
        assert page.statistics_status.text() == '正在重新上传…'
        wait_for(lambda: len(state['requests']) == 1)
        controller.changed.emit()
        controller.retry()
        assert not button.isEnabled() and len(state['requests']) == 1
        assert button.width() == width
        window._show_section('home')
        assert not button._spinner_timer.isActive()
        window._show_section('settings')
        assert button._spinner_timer.isActive() and not button.isEnabled()
        state['hold'].set()
        wait_for(lambda: not controller.uploading)
        assert controller.queue.counts() == (1, 0)
        assert page.statistics_pending.text() == '待上传记录：1'
        assert '连接失败' in page.statistics_status.text()
        assert button.text() == '重新上传' and button.isEnabled()
        assert not button._spinner_timer.isActive()
        assert not page._statistics_success_timer.isActive()

        state['code'] = 200
        state['hold'].clear()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert not button.isEnabled() and button._spinner_timer.isActive()
        wait_for(lambda: len(state['requests']) == 2)
        state['hold'].set()
        wait_for(lambda: controller.queue.counts() == (0, 0) and not controller.uploading)
        assert page.statistics_pending.text() == '待上传记录：0'
        assert page.statistics_status.text() == '统计记录已上传'
        assert page._statistics_success_timer.isActive()
        assert not button.isEnabled() and button.text() == '重新上传'
        assert button.width() == width and len(state['requests']) == 2
    finally:
        state['hold'].set()
        controller.cancel()
        wait_for(lambda: controller._thread is None)


def test_background_success_waits_until_settings_are_shown_before_expiring(settings_window,qt_application):
    window = settings_window
    page, controller = window._settings_page, window._reporting_controller
    window.show()
    qt_application.processEvents()
    controller.status.emit('统计记录已上传', True)
    assert not page.isVisible() and not page._statistics_success_timer.isActive()
    QTest.qWait(3100)
    assert page.statistics_status.text() == '统计记录已上传'
    window._show_section('settings')
    qt_application.processEvents()
    assert page.statistics_status.isVisible()
    assert page._statistics_success_timer.interval() == 3000
    assert page._statistics_success_timer.isActive()
    wait_for(lambda: page.statistics_status.isHidden(), timeout_ms=4000)
    assert page.statistics_status.text() == ''
    window._show_section('home')
    window._show_section('settings')
    assert page.statistics_status.isHidden()


def test_leaving_settings_discards_seen_success_but_preserves_unseen_success(settings_window,qt_application):
    window = settings_window
    page, controller = window._settings_page, window._reporting_controller
    window.show()
    window._show_section('settings')
    qt_application.processEvents()
    controller.status.emit('统计记录已上传', True)
    assert page._statistics_success_timer.isActive()
    window._show_section('home')
    assert page.statistics_status.text() == ''
    assert not page._statistics_success_timer.isActive()
    window._show_section('settings')
    assert page.statistics_status.isHidden()
    window._show_section('home')
    controller.status.emit('统计记录已上传', True)
    window.hide()
    assert page.statistics_status.text() == '统计记录已上传'
    window.show()
    window._show_section('settings')
    assert page.statistics_status.isVisible() and page._statistics_success_timer.isActive()


def test_failure_survives_old_success_timeout_and_navigation_with_matching_style(settings_window,qt_application):
    window = settings_window
    page, controller = window._settings_page, window._reporting_controller
    window.show()
    window._show_section('settings')
    controller.status.emit('统计记录已上传', True)
    message = '连接失败，记录已保留，稍后自动补传'
    controller.status.emit(message, False)
    page._statistics_success_timer.timeout.emit()
    qt_application.processEvents()
    assert page.statistics_status.text() == message
    assert not page._statistics_success_timer.isActive()
    for label in (page.statistics_pending, page.statistics_status):
        assert label.font().pixelSize() == 16
        assert label.palette().color(QPalette.ColorRole.WindowText).name() == '#15181b'
    assert page.statistics_pending.geometry().bottom() < page.statistics_status.geometry().top()
    assert page.statistics_status.geometry().bottom() < page.statistics_retry.geometry().top()
    window._show_section('home')
    window._show_section('settings')
    assert page.statistics_status.text() == message and page.statistics_status.isVisible()
    assert all(not widget.toolTip() for widget in window.findChildren(QWidget))


@pytest.mark.parametrize('condition,expected', [
    ('endpoint', '尚未配置统计服务地址'),
    ('running', '当前任务运行中'),
    ('disabled', '匿名统计已关闭'),
    ('empty', '没有可重试的待上传记录'),
    ('rejected', '没有可重试的待上传记录'),
    ('closing', '统计上传正在关闭'),
    ('storage', '暂时无法读取本地统计记录'),
])
def test_retry_explains_blocked_requests_without_busy_or_network(settings_window,monkeypatch,condition,expected):
    controller = settings_window._reporting_controller
    if condition != 'empty':
        report = enqueue(controller.queue)
    if condition == 'endpoint':
        controller.endpoint = ''
    elif condition == 'running':
        controller.set_running(True)
    elif condition == 'disabled':
        controller.set_enabled(False)
    elif condition == 'rejected':
        controller.queue.reject(report.run_id, 'test')
    elif condition == 'closing':
        controller.cancel()
    elif condition == 'storage':
        def fail_counts():
            raise OSError('test')
        monkeypatch.setattr(controller.queue, 'counts', fail_counts)
    statuses, dispatched = [], []
    controller.status.connect(lambda message, success: statuses.append((message, success)))
    controller._retry_requested.connect(lambda: dispatched.append(True))
    controller.retry()
    assert not controller.uploading and controller._thread is None and not dispatched
    assert expected in statuses[-1][0] and statuses[-1][1] is False


def test_pausing_active_retry_restores_controls_and_retains_record(settings_window,qt_application,local_proxy_only,server,qt_messages):
    window = settings_window
    controller, page = window._reporting_controller, window._settings_page
    endpoint, state = server
    controller.endpoint = endpoint
    enqueue(controller.queue)
    state['hold'].clear()
    window.show()
    window._show_section('settings')
    page._refresh_statistics()
    try:
        page.statistics_retry.click()
        wait_for(lambda: bool(state['requests']))
        controller.set_running(True)
        wait_for(lambda: not controller.uploading)
        assert controller.queue.counts() == (1, 0)
        assert page.statistics_retry.text() == '重新上传'
        assert not page.statistics_retry._spinner_timer.isActive()
        assert page.statistics_status.text() == '当前任务运行中，结束后自动补传'
        assert not any('device not open' in message for message in qt_messages)
    finally:
        state['hold'].set()
        controller.cancel()
        wait_for(lambda: controller._thread is None)


def test_retry_start_failure_restores_button_and_retains_record(settings_window,monkeypatch):
    window = settings_window
    controller, page = window._reporting_controller, window._settings_page
    enqueue(controller.queue)
    def fail_start():
        raise RuntimeError('test start failure')
    monkeypatch.setattr(controller, 'start', fail_start)
    window.show()
    window._show_section('settings')
    page._refresh_statistics()
    page.statistics_retry.click()
    assert not controller.uploading and controller.queue.counts() == (1, 0)
    assert page.statistics_retry.text() == '重新上传' and page.statistics_retry.isEnabled()
    assert not page.statistics_retry._spinner_timer.isActive()
    assert page.statistics_status.text() == '暂时无法启动上传，记录已保留'
    assert not page._statistics_success_timer.isActive()


def test_statistics_count_read_failure_preserves_known_counts_and_upload_feedback(settings_window, qt_application, monkeypatch):
    controller, page = settings_window._reporting_controller, settings_window._settings_page
    enqueue(controller.queue)
    page._refresh_statistics()
    assert page.statistics_pending.text() == '待上传记录：1'
    settings_window.show()
    settings_window._show_section('settings')
    qt_application.processEvents()
    page._show_statistics_status('上传反馈保留', success=True)
    timer_active = page._statistics_success_timer.isActive()
    assert timer_active
    def fail_counts():
        raise OSError('database unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(controller.queue, 'counts', fail_counts)
        assert controller.counts() is None
        page._refresh_statistics()
        assert page.statistics_pending.text() == '上次待上传记录：1（暂时无法刷新）'
        assert page.statistics_retry.isEnabled()
        assert page.statistics_status.text() == '上传反馈保留'
        assert page._statistics_success_timer.isActive() == timer_active
        dispatched = []
        controller._retry_requested.connect(lambda: dispatched.append(True))
        page.statistics_retry.click()
        assert not dispatched and not controller.uploading
        assert page.statistics_status.text() == '暂时无法读取本地统计记录'
    controller.queue.acknowledge(controller.queue.next_due(0).report.run_id)
    page._refresh_statistics()
    assert page.statistics_pending.text() == '待上传记录：0'
    assert not page.statistics_retry.isEnabled()


def test_statistics_first_count_read_failure_does_not_display_zero(tmp_path, qt_application, monkeypatch):
    def fail_counts(*args):
        raise OSError('database unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(ReportQueue, 'counts', fail_counts)
        window = MainWindow(tmp_path)
        try:
            page = window._settings_page
            assert window._reporting_controller.counts() is None
            assert page.statistics_pending.text() == '待上传记录：暂不可读取'
            assert page._last_statistics_counts is None
        finally:
            window.close()
    assert ReportQueue(tmp_path/'state/shop_reports.sqlite3').counts() == (0, 0)


def test_statistics_rejected_counts_remain_visible_when_reading_fails(settings_window, monkeypatch):
    controller, page = settings_window._reporting_controller, settings_window._settings_page
    value = enqueue(controller.queue)
    controller.queue.reject(value.run_id, 'http_409')
    page._refresh_statistics()
    assert page.statistics_pending.text() == '待上传记录：1 · 服务端拒绝：1'
    def fail_counts():
        raise OSError('database unavailable')
    monkeypatch.setattr(controller.queue, 'counts', fail_counts)
    page._refresh_statistics()
    assert page.statistics_pending.text() == '上次待上传记录：1 · 服务端拒绝：1（暂时无法刷新）'
