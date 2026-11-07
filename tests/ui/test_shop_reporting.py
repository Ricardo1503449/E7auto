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
def server():
    state = {'paths':[],'requests':[],'code':201,'ack':True,'retry_after':'','hold':threading.Event()}
    state['hold'].set()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append((body,dict(self.headers)))
            state['paths'].append(self.path)
            state['hold'].wait(state.get('hold_timeout',3))
            self.send_response(state['code'])
            self.send_header('Content-Type','application/json')
            if state['retry_after']:
                self.send_header('Retry-After',state['retry_after'])
            self.end_headers()
            try:
                response = ({'accepted':True,'run_id':body['run_id'] if state['ack'] else 'wrong'}
                    if 'run_id' in body else {'accepted':True,'installation_id':body['installation_id'] if state['ack'] else 'wrong','date':body['date']})
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


def test_retired_endpoint_404_preserves_record_and_resumes_after_restart(
        tmp_path,qt_application,local_proxy_only,server,monkeypatch):
    from types import SimpleNamespace
    from e7auto.ui import shop_reporting

    endpoint,state = server
    state['code'] = 404
    path = tmp_path/'q.sqlite3'
    queue = ReportQueue(path)
    report = enqueue(queue)
    old = ShopReportUploader(queue,endpoint)
    old.start()
    try:
        wait_for(lambda: len(state['requests']) == 1 and old._reply is None)
        assert queue.counts() == (1,0)
        assert queue.next_due(time.time()) is None
    finally:
        old.stop(); old.deleteLater(); qt_application.processEvents()

    restored = ReportQueue(path)
    due = time.time()+3600
    assert restored.next_due(due).report == report
    state['code'] = 201
    new = ShopReportUploader(restored,endpoint.replace('127.0.0.1','localhost'))
    new.start()
    try:
        assert len(state['requests']) == 1  # The persisted backoff still applies.
        monkeypatch.setattr(shop_reporting,'time',SimpleNamespace(time=lambda: due))
        new.pump()
        wait_for(lambda: restored.counts() == (0,0))
        assert [body for body,_ in state['requests']] == [report.payload(),report.payload()]
        assert state['requests'][0][1]['Host'] != state['requests'][1][1]['Host']
    finally:
        new.stop(); new.deleteLater(); qt_application.processEvents()


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
        process.stdin.write('pools\n'); process.stdin.flush()
        rows = json.loads(process.stdout.readline())
        stats = {'pools':{row['strategy']:row for row in rows}}
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




def test_usage_and_shop_share_uploader_with_distinct_ack_and_busy_pause(tmp_path,qt_application,local_proxy_only,server):
    endpoint,state=server
    controller=ShopReportingController(tmp_path,report_usage=True)
    controller.endpoint=endpoint
    controller.application_shown()
    usage=controller.usage_queue.next_due(time.time())
    enqueue(controller.queue)
    state['ack']=False
    controller.start()
    try:
        wait_for(lambda:len(state['requests'])==1 and not controller.uploading)
        assert state['paths']==['/v1/usage-days']
        assert controller.usage_queue.next_due(time.time()+10000) is not None
        state['ack']=True
        controller.set_running(True)
        controller._uploader._retry_not_before=0
        controller.usage_queue.retry_later(usage,0)
        controller.set_running(False)
        wait_for(lambda:controller.queue.counts()==(0,0) and controller.usage_queue.next_due(time.time()+10000) is None)
        assert '/v1/shop-runs' in state['paths']
        assert all(set(body)=={'installation_id','date','version','features'} if path=='/v1/usage-days'
                   else set(body)=={'run_id','strategy','skystone_spent','covenant_purchases','mystic_purchases'}
                   for path,(body,_) in zip(state['paths'],state['requests']))
    finally:
        controller.cancel(); wait_for(lambda:controller._thread is None)


def test_usage_inflight_shutdown_retains_daily_record(tmp_path,qt_application,local_proxy_only,server):
    endpoint,state=server
    controller=ShopReportingController(tmp_path,report_usage=True)
    controller.endpoint=endpoint
    controller.application_shown()
    state['hold'].clear()
    controller.start()
    try:
        wait_for(lambda:len(state['requests'])==1)
        controller.cancel()
        wait_for(lambda:controller._thread is None)
        assert controller.usage_queue.next_due(time.time()) is not None
    finally:
        state['hold'].set()
        controller.cancel()
