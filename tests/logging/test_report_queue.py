from dataclasses import replace
import sqlite3

import pytest

from e7auto.core.reporting import ShopRunReport
from e7auto.logging.report_queue import MAX_REPORTS, ReportQueue


def report(index=1, **values):
    return ShopRunReport(f'{index:032x}', 'transfer', **values)


def finish(queue, value):
    assert queue.begin(value, queue.generation)
    assert queue.save(value, queue.generation, final=True)


def test_active_journal_survives_restart_but_never_uploads_mid_run(tmp_path):
    path = tmp_path/'state/shop_reports.sqlite3'
    queue = ReportQueue(path)
    assert queue.begin(report(), 0)
    assert queue.save(report(skystone_spent=6, covenant_purchases=2), 0)
    assert queue.next_due(0) is None
    recovered = ReportQueue(path)
    assert recovered.recover_interrupted() == 1
    assert recovered.recover_interrupted() == 0
    assert recovered.next_due(0).report.payload() == {
        'run_id': f'{1:032x}', 'strategy': 'transfer', 'skystone_spent': 6,
        'covenant_purchases': 2, 'mystic_purchases': 0}
    recovered.acknowledge(f'{1:032x}')
    assert recovered.counts() == (0, 0)


def test_pause_preserves_pending_and_cancels_only_unfinished_run(tmp_path):
    queue = ReportQueue(tmp_path/'q.sqlite3')
    finish(queue, report())
    assert queue.begin(report(2), 0)
    queue.set_enabled(False)
    assert queue.next_due(0) is None
    assert queue.counts() == (1, 0)
    queue.set_enabled(True)
    assert not queue.save(report(2, skystone_spent=3), 0, final=True)
    assert queue.next_due(0).report.run_id == report().run_id


def test_persistent_cancellation_epoch_prevents_recovering_stale_active_row(tmp_path):
    path = tmp_path/'q.sqlite3'
    queue = ReportQueue(path)
    queue.begin(report(), 0)
    # A disable saved to preferences while the journal was unwritable; later enabled again.
    restarted = ReportQueue(path, generation=1)
    assert restarted.recover_interrupted() == 0
    assert restarted.next_due(0) is None


def test_exact_default_capacity_does_not_overwrite_or_expire(tmp_path):
    queue = ReportQueue(tmp_path/'q.sqlite3')
    assert queue.capacity == MAX_REPORTS == 10_000
    with queue._connection() as db:
        db.executemany("""INSERT INTO reports
            (run_id,strategy,skystone_spent,covenant_purchases,mystic_purchases,state)
            VALUES (?,'transfer',3,1,0,'pending')""", ((f'{i:032x}',) for i in range(1,10_001)))
    assert not queue.begin(report(10_001), 0)
    assert queue.counts() == (10_000, 0)
    assert queue.next_due(1e12).report.run_id == report().run_id
    queue.acknowledge(report().run_id)
    assert queue.begin(report(10_001), 0)


def test_existing_active_can_finish_at_capacity(tmp_path):
    queue = ReportQueue(tmp_path/'q.sqlite3', capacity=1)
    queue.begin(report(), 0)
    assert queue.save(report(skystone_spent=3), 0, final=True)
    assert not queue.begin(report(2), 0)
    assert queue.next_due(0).report.skystone_spent == 3


def test_retry_schedule_and_retry_after_do_not_delete_rows(tmp_path):
    queue = ReportQueue(tmp_path/'q.sqlite3')
    finish(queue, report())
    now = 1000
    for delay in (60,300,1800,3600,3600):
        due = queue.retry_later(report().run_id, now)
        assert due == now+delay
        assert queue.next_due(due-1) is None
        assert queue.next_due(due) is not None
        now = due
    assert queue.retry_later(report().run_id, now, minimum_delay=7200) == now+7200
    queue.retry_pending()
    assert queue.next_due(0).attempts == 0


def test_rejected_row_is_retained_without_blocking_following_rows(tmp_path):
    queue = ReportQueue(tmp_path/'q.sqlite3')
    finish(queue, report())
    finish(queue, report(2))
    queue.reject(report().run_id, 'http_409')
    queue.retry_pending()
    assert queue.next_due(0).report.run_id == report(2).run_id
    assert queue.counts() == (2,1)


def test_counters_cannot_regress_and_failed_transaction_preserves_previous_values(tmp_path):
    queue = ReportQueue(tmp_path/'q.sqlite3')
    queue.begin(report(skystone_spent=6), 0)
    with pytest.raises(ValueError):
        queue.save(report(skystone_spent=3), 0)
    queue.recover_interrupted()
    assert queue.next_due(0).report.skystone_spent == 6


def test_two_connections_cannot_overfill_capacity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path/'q.sqlite3'
    first, second = ReportQueue(path, capacity=1), ReportQueue(path, capacity=1)
    first.counts()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda args: args[0].begin(args[1], 0), [(first,report()),(second,report(2))]))
    assert sorted(results) == [False,True]
    assert sqlite3.connect(path).execute('SELECT COUNT(*) FROM reports').fetchone()[0] == 1


@pytest.mark.parametrize('new_instance', [False, True])
def test_readers_see_committed_reports_during_another_connections_write(tmp_path, new_instance):
    path = tmp_path/'q.sqlite3'
    queue = ReportQueue(path)
    finish(queue, report(skystone_spent=3, covenant_purchases=1))
    reader = ReportQueue(path) if new_instance else queue
    writer = sqlite3.connect(path)
    try:
        writer.execute('BEGIN IMMEDIATE')
        writer.execute('UPDATE reports SET skystone_spent=6')
        assert reader.counts() == (1, 0)
        pending = reader.next_due(0)
        assert pending.report.counters == (3, 1, 0)
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            reader.begin(report(2), 0)
    finally:
        writer.rollback()
        writer.close()
    assert reader.counts() == (1, 0)
    assert reader.begin(report(2), 0)


def test_failed_first_schema_preparation_can_retry_after_writer_releases(tmp_path):
    path = tmp_path/'q.sqlite3'
    writer = sqlite3.connect(path)
    queue = ReportQueue(path)
    try:
        writer.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            queue.counts()
    finally:
        writer.rollback()
        writer.close()
    assert queue.counts() == (0, 0)
    finish(queue, report())
    assert queue.next_due(0).report == report()


def test_legacy_schema_upgrade_preserves_interrupted_report(tmp_path):
    path = tmp_path/'q.sqlite3'
    legacy = sqlite3.connect(path)
    try:
        legacy.execute('''CREATE TABLE reports (
            position INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL UNIQUE,
            strategy TEXT NOT NULL, skystone_spent INTEGER NOT NULL,
            covenant_purchases INTEGER NOT NULL, mystic_purchases INTEGER NOT NULL,
            state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0, rejection TEXT NOT NULL DEFAULT '')''')
        legacy.execute("INSERT INTO reports (run_id,strategy,skystone_spent,covenant_purchases,mystic_purchases,state) "
                       "VALUES (?,'transfer',6,2,0,'active')", (report().run_id,))
        legacy.commit()
    finally:
        legacy.close()
    queue = ReportQueue(path)
    assert queue.counts() == (0, 0)
    assert queue.recover_interrupted() == 1
    assert queue.next_due(0).report.counters == (6, 2, 0)
    assert queue.recover_interrupted() == 0


@pytest.mark.parametrize('replacement', [False, True])
def test_schema_preparation_tracks_database_recreation_or_replacement(tmp_path, replacement):
    path = tmp_path/'q.sqlite3'
    queue = ReportQueue(path)
    finish(queue, report())
    if replacement:
        other = ReportQueue(tmp_path/'replacement.sqlite3')
        finish(other, report(2, skystone_spent=6))
        other.path.replace(path)
        assert queue.next_due(0).report == report(2, skystone_spent=6)
    else:
        path.unlink()
        assert queue.counts() == (0, 0)
        finish(queue, report(2))
        assert queue.next_due(0).report == report(2)
