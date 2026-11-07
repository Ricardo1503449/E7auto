"""Isolated statistics diagnostics for the compiled executable's self-check."""
from pathlib import Path
import json
import sqlite3
import tempfile
import time
import uuid

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtNetwork import (QNetworkAccessManager, QNetworkProxy, QNetworkProxyFactory,
                              QNetworkProxyQuery, QNetworkReply, QNetworkRequest, QSslSocket)
from PySide6.QtWidgets import QApplication

from e7auto.configuration.reporting import load_reporting_endpoint
from e7auto.core.reporting import ShopRunReport
from e7auto.logging.report_queue import ReportQueue
from e7auto.logging.usage_queue import UsageQueue
from e7auto import __version__


def _read_https_summary(endpoint: str) -> dict:
    if not endpoint:
        return {'ok': False, 'error': 'statistics_endpoint_missing'}
    manager = QNetworkAccessManager()
    # HEAD probes the retained ingest route without reading aggregates or inserting data.
    request = QNetworkRequest(QUrl(endpoint+'/v1/shop-runs'))
    request.setRawHeader(b'User-Agent', b'E7auto')
    request.setRawHeader(b'Accept', b'application/json')
    request.setTransferTimeout(10_000)
    request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
    proxies = QNetworkProxyFactory.systemProxyForQuery(QNetworkProxyQuery(request.url()))
    manager.setProxy(proxies[0] if proxies else QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
    reply = manager.head(request)
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(reply.abort)
    reply.finished.connect(loop.quit)
    timer.start(10_000)
    loop.exec()
    timer.stop()
    code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    error = reply.error()
    ok = code == 405 and bytes(reply.rawHeader('Allow')) == b'POST'
    result = {'ok': ok,
              'http_status': code, 'network_error': error.name,
              'error': reply.errorString() if not ok and error != QNetworkReply.NetworkError.NoError else ''}
    reply.deleteLater()
    QApplication.processEvents()
    return result


def check_reporting_runtime(root: Path, *, include_https: bool = False,
                            temp_root: Path | None = None) -> dict:
    # Keep an application alive for Qt's TLS plugins; no windows or game input.
    application = QApplication.instance() or QApplication([])
    result = {'sqlite_version': sqlite3.sqlite_version, 'statistics_queue_ready': False, 'usage_queue_ready': False,
              'statistics_tls_ready': bool(QSslSocket.supportsSsl()), 'statistics_https': None}
    try:
        # Only synthetic, local records in a fresh temporary directory. No uploader
        # is instantiated and no actual user journal is touched.
        with tempfile.TemporaryDirectory(prefix='e7auto-reporting-check-', dir=temp_root) as folder:
            path = Path(folder)/'reports.sqlite3'
            report = ShopRunReport(uuid.uuid4().hex, 'transfer', 3, 1, 0)
            queue = ReportQueue(path)
            if not queue.begin(report, 0) or not queue.save(report, 0):
                raise RuntimeError('journal_write_failed')
            restored = ReportQueue(path)
            if restored.recover_interrupted() != 1:
                raise RuntimeError('journal_recovery_failed')
            pending = restored.next_due(time.time())
            if pending is None or pending.report != report:
                raise RuntimeError('journal_contents_changed')
            restored.acknowledge(report.run_id)
            result['statistics_queue_ready'] = restored.counts() == (0, 0)
            usage = UsageQueue(Path(folder)/'usage.sqlite3')
            now = time.time()
            usage.record(now, __version__)
            old = usage.next_due(now)
            usage.record(now, __version__, 'shop')
            usage.acknowledge(old)
            pending_usage = UsageQueue(usage.path).next_due(now)
            result['usage_queue_ready'] = (pending_usage is not None
                and pending_usage.report.installation_id == old.report.installation_id
                and pending_usage.report.features == ('shop',))
        if include_https:
            result['statistics_https'] = _read_https_summary(
                load_reporting_endpoint(root/'config/internal.yaml'))
    except Exception as error:
        result['statistics_check_error'] = f'{type(error).__name__}: {error}'
    result['statistics_runtime_ready'] = (result['statistics_queue_ready'] and result['usage_queue_ready']
        and result['statistics_tls_ready']
        and (not include_https or bool(result['statistics_https'] and result['statistics_https']['ok'])))
    application.processEvents()
    return result
