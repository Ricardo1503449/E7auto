from dataclasses import replace
from datetime import datetime, timezone
import json
import pytest
from types import SimpleNamespace

from PySide6.QtWidgets import QWidget

from e7auto.core.domain import RunState, RuntimeSnapshot
from e7auto.core.reporting import ShopRunReport
from e7auto.logging.report_queue import ReportQueue
from e7auto.ui.main_window import MainWindow
from e7auto.ui.preferences import ShopStatisticsPreferences
from e7auto.ui.shop_reporting import ShopReportingController


def test_legacy_disabled_migration_preserves_pending_and_drops_cancelled_active(tmp_path, qt_application):
    state = tmp_path/'state'
    queue = ReportQueue(state/'shop_reports.sqlite3')
    pending = ShopRunReport('a'*32, 'transfer', 3, 1, 0)
    active = ShopRunReport('b'*32, 'transfer', 3, 0, 0)
    queue.begin(pending,0); queue.save(pending,0,final=True); queue.begin(active,0)
    preferences = ShopStatisticsPreferences(state/'ui_preferences.json')
    preferences.save(False)
    values = json.loads((state/'ui_preferences.json').read_text())
    values.update(run_end_notifications=False, announcement_seen_id='seen', shop_continuous_refresh=True)
    (state/'ui_preferences.json').write_text(json.dumps(values))
    controller = ShopReportingController(tmp_path)
    try:
        assert controller.enabled and controller.queue.generation == 2
        assert controller.queue.counts() == (1,0)
        assert controller.queue.next_due(0).report == pending
        values = json.loads((state/'ui_preferences.json').read_text())
        assert 'shop_statistics' not in values
        assert values['shop_statistics_generation'] == 2
        assert values['run_end_notifications'] is False and values['shop_continuous_refresh'] is True
        assert values['announcement_seen_id'] == 'seen'
    finally:
        controller.cancel()


@pytest.mark.parametrize('preference_write_fails',[False,True])
def test_legacy_false_without_epoch_does_not_recover_cancelled_active(tmp_path, qt_application, monkeypatch, preference_write_fails):
    from e7auto.ui import preferences
    state=tmp_path/'state'
    queue=ReportQueue(state/'shop_reports.sqlite3')
    pending=ShopRunReport('c'*32,'transfer',3,1,0)
    cancelled=ShopRunReport('d'*32,'transfer',3,0,0)
    queue.begin(pending,0);queue.save(pending,0,final=True);queue.begin(cancelled,0)
    (state/'ui_preferences.json').write_text('{"shop_statistics":false}',encoding='utf-8')
    if preference_write_fails:
        monkeypatch.setattr(preferences,'_write_preferences',lambda *args: (_ for _ in ()).throw(OSError('busy')))
    controller=ShopReportingController(tmp_path)
    try:
        assert controller.enabled and controller.queue.generation==1
        assert controller.queue.counts()==(1,0)
        assert controller.queue.next_due(0).report==pending
        controller.cancel()
        resumed=ShopReportingController(tmp_path)
        assert resumed.queue.counts()==(1,0)
        resumed.cancel()
    finally:
        controller.cancel()


def test_statistics_controls_removed_while_personal_overlay_and_notifications_remain(tmp_path, qt_application):
    window = MainWindow(tmp_path)
    try:
        settings = window._settings_page
        assert settings.notifications_toggle is not None and settings.version_label is not None
        assert not any(widget.objectName().startswith('shopStatistics') for widget in settings.findChildren(QWidget))
        assert not hasattr(settings,'statistics_retry') and not hasattr(settings,'statistics_toggle')
        assert window._overlay is not None
        assert not (tmp_path/'state/usage_reports.sqlite3').exists()
        window._reporting_controller.application_shown()
        assert window._reporting_controller.usage_queue is None
    finally:
        window.close()


def test_only_actual_feature_snapshots_count_and_preparation_does_not(tmp_path, qt_application):
    window = MainWindow(tmp_path, report_usage=True)
    controller = window._reporting_controller
    try:
        assert controller.usage_queue.next_due(10**12) is None
        controller.application_shown()
        initial = RuntimeSnapshot.initial('test',(),3)
        window._on_snapshot(initial)
        pending = controller.usage_queue.next_due(10**12)
        assert pending.report.features == ()
        window._on_snapshot(replace(initial,state=RunState.ENTERING_STORE))
        penguin = RuntimeSnapshot.penguins('test',1)
        window._on_snapshot(replace(penguin,state=RunState.ENTERING_SANCTUARY))
        chaos = RuntimeSnapshot.chaos_run('test',1)
        window._on_snapshot(replace(chaos,state=RunState.EXPLORING_CHAOS))
        assert controller.usage_queue.next_due(10**12).report.features == ('shop','penguin','chaos')
    finally:
        window.close()


def test_running_across_midnight_keeps_feature_on_original_and_next_day(tmp_path, qt_application, monkeypatch):
    from e7auto.ui import shop_reporting
    now = [datetime(2026,11,7,15,59,59,tzinfo=timezone.utc).timestamp()]
    monkeypatch.setattr(shop_reporting,'time',SimpleNamespace(time=lambda:now[0]))
    controller = ShopReportingController(tmp_path,report_usage=True)
    try:
        controller.application_shown()
        controller.feature_running('chaos')
        old = controller.usage_queue.next_due(now[0])
        assert old.report.date == '2026-11-07'
        controller.usage_queue.acknowledge(old)
        now[0] += 2
        controller.feature_running(None)
        new = controller.usage_queue.next_due(now[0])
        assert new.report.date == '2026-11-08' and new.report.features == ('chaos',)
        assert old.report.installation_id == new.report.installation_id
    finally:
        controller.cancel()


def test_activity_storage_failure_is_nonblocking_and_retried(tmp_path, qt_application, monkeypatch):
    controller = ShopReportingController(tmp_path,report_usage=True)
    original = controller.usage_queue.record
    monkeypatch.setattr(controller.usage_queue,'record',lambda *args: (_ for _ in ()).throw(OSError('busy')))
    controller.application_shown()
    assert controller._last_activity is None
    monkeypatch.setattr(controller.usage_queue,'record',original)
    controller.record_activity()
    assert controller.usage_queue.next_due(10**12) is not None
    controller.cancel()
