from datetime import datetime, timezone
from e7auto.logging.usage_queue import UsageQueue


def timestamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def test_identity_survives_restart_and_daily_changes_merge_without_downgrading(tmp_path):
    path = tmp_path/'state/usage_reports.sqlite3'
    now = timestamp('2026-11-07T05:00:00')
    queue = UsageQueue(path)
    queue.record(now, '1.5.2')
    pending = queue.next_due(now)
    assert pending.report.features == ()
    queue.acknowledge(pending)
    assert queue.next_due(now) is None
    resumed = UsageQueue(path)
    resumed.record(now, '1.5.2', 'shop')
    resumed.record(now, '1.5.1', 'penguin')
    latest = resumed.next_due(now)
    assert latest.report.installation_id == pending.report.installation_id
    assert latest.report.version == '1.5.2'
    assert latest.report.features == ('shop', 'penguin')
    resumed.acknowledge(latest)
    resumed.record(now, '1.5.2', 'shop')
    assert resumed.next_due(now) is None


def test_inflight_ack_and_failure_do_not_discard_newer_daily_features(tmp_path):
    now = timestamp('2026-11-07T05:00:00')
    queue = UsageQueue(tmp_path/'usage.sqlite3')
    queue.record(now, '1.5.2')
    old = queue.next_due(now)
    queue.record(now, '1.5.2', 'chaos')
    queue.acknowledge(old)
    queue.retry_later(old, now, minimum_delay=600)
    assert queue.next_due(now).report.features == ('chaos',)
    queue.reject(old)
    assert queue.next_due(now) is not None


def test_beijing_midnight_and_ninety_day_retention(tmp_path):
    queue = UsageQueue(tmp_path/'usage.sqlite3')
    before = timestamp('2026-11-06T15:59:59')
    after = timestamp('2026-11-06T16:00:01')
    queue.record(before, '1.5.2', 'shop')
    first = queue.next_due(after)
    assert first.report.date == '2026-11-06'
    queue.acknowledge(first)
    queue.record(after, '1.5.2', 'shop')
    second = queue.next_due(after)
    assert second.report.date == '2026-11-07'
    assert second.report.installation_id == first.report.installation_id
    queue.record(after+90*86400, '1.5.2')
    assert queue.next_due(after+90*86400).report.date == '2027-02-05'


def test_pause_retry_after_and_explicit_rejection(tmp_path):
    now = timestamp('2026-11-07T05:00:00')
    queue = UsageQueue(tmp_path/'usage.sqlite3')
    queue.record(now, '1.5.2')
    pending = queue.next_due(now)
    queue.pause_uploads(True)
    assert queue.next_due(now) is None
    queue.pause_uploads(False)
    due = queue.retry_later(pending, now, minimum_delay=120)
    assert due == now+120 and queue.next_due(now+119) is None
    queue.reject(pending)
    assert queue.next_due(now+10000) is None
