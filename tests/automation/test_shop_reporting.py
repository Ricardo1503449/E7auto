from dataclasses import replace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.shop.reporting import ShopReportRecorder
from e7auto.logging.report_queue import ReportQueue


def snapshot():
    return RuntimeSnapshot.initial('existing-log-id', (
        ('covenant_bookmark','圣约书签'),('mystic_medal','神秘奖牌'),('friendship_points','友情点数')), 600)


@pytest.mark.parametrize('continuous', [False,True])
@pytest.mark.parametrize('reason', [StopReason.MANUAL_STOP,StopReason.BUDGET_COMPLETE,StopReason.PURCHASE_RESULT_AMBIGUOUS])
def test_confirmed_counts_initial_inventory_and_all_endings(tmp_path, continuous, reason):
    queue = ReportQueue(tmp_path/'q.sqlite3')
    recorder = ShopReportRecorder(queue, continuous_refresh=continuous)
    current = snapshot().with_incremented_target('covenant_bookmark')
    recorder.observe(current)
    assert queue.next_due(0) is None
    current = current.with_refresh_spent(3).with_incremented_target('mystic_medal').with_incremented_target('friendship_points')
    recorder.observe(current)
    final = current.finalized(reason)
    recorder.observe(final)
    recorder.observe(final)
    value = queue.next_due(0).report
    assert value.strategy == ('continuous' if continuous else 'transfer')
    assert value.counters == (3,1,1)
    assert len(value.run_id) == 32 and value.run_id != current.run_id
    assert queue.counts() == (1,0)


def test_off_and_on_during_run_never_resumes_cancelled_run(tmp_path):
    queue = ReportQueue(tmp_path/'q.sqlite3')
    recorder = ShopReportRecorder(queue)
    recorder.observe(snapshot())
    queue.set_enabled(False)
    queue.set_enabled(True)
    recorder.observe(snapshot().with_refresh_spent(3).finalized(StopReason.MANUAL_STOP))
    assert queue.counts() == (0,0)


def test_optional_storage_failure_never_escapes_into_business_flow(tmp_path, monkeypatch):
    queue = ReportQueue(tmp_path/'q.sqlite3')
    recorder = ShopReportRecorder(queue)
    def failed(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(queue,'begin',failed)
    recorder.observe(snapshot())
    recorder.observe(snapshot().finalized(StopReason.MANUAL_STOP))


def test_capacity_is_silent_and_unrelated_features_do_not_create_reports(tmp_path):
    queue = ReportQueue(tmp_path/'q.sqlite3', capacity=0)
    recorder = ShopReportRecorder(queue)
    recorder.observe(snapshot())
    recorder.observe(snapshot().finalized(StopReason.MANUAL_STOP))
    assert queue.counts() == (0,0)
    other = ShopReportRecorder(queue)
    other.observe(RuntimeSnapshot.penguins('penguin',10).finalized(StopReason.MANUAL_STOP))
    assert queue.counts() == (0,0)
