"""Durable local journal/outbox. Scheduling metadata never leaves this file."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import threading

from e7auto.core.reporting import ShopRunReport

MAX_REPORTS = 10_000
RETRY_DELAYS = (60, 300, 1800, 3600)


@dataclass(frozen=True, slots=True)
class PendingReport:
    report: ShopRunReport
    attempts: int


class ReportQueue:
    def __init__(self, path: Path, *, enabled: bool = True, capacity: int = MAX_REPORTS,
                 generation: int = 0) -> None:
        self.path = path
        self.capacity = capacity
        self._lock = threading.RLock()
        self._enabled = enabled
        self._generation = generation
        self._paused = False
        self._schema_identity: tuple[int, int] | None = None

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def upload_allowed(self) -> bool:
        with self._lock:
            return self._enabled and not self._paused

    def pause_uploads(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused

    def _prepare_schema(self, connection: sqlite3.Connection) -> None:
        with self._lock:
            stat = self.path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if self._schema_identity == identity:
                return
            columns = {row[1] for row in connection.execute('PRAGMA table_info(reports)')}
            indexes = {row[1] for row in connection.execute('PRAGMA index_list(reports)')}
            if 'generation' in columns and 'report_due' in indexes:
                self._schema_identity = identity
                return
            # Serialize creation/migration across queue instances. Recheck columns
            # after admission, since another connection may have migrated first.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE IF NOT EXISTS reports (
                position INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE, strategy TEXT NOT NULL,
                skystone_spent INTEGER NOT NULL, covenant_purchases INTEGER NOT NULL,
                mystic_purchases INTEGER NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('active','pending','rejected')),
                attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                rejection TEXT NOT NULL DEFAULT '', generation INTEGER NOT NULL DEFAULT 0)""")
            if 'generation' not in {row[1] for row in connection.execute('PRAGMA table_info(reports)')}:
                connection.execute('ALTER TABLE reports ADD COLUMN generation INTEGER NOT NULL DEFAULT 0')
            connection.execute("CREATE INDEX IF NOT EXISTS report_due ON reports(state,retry_at,position)")
            connection.commit()
            self._schema_identity = identity

    @contextmanager
    def _connection(self, *, write: bool = True):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0.05)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA synchronous=FULL")
            self._prepare_schema(connection)
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_enabled(self, enabled: bool, *, generation: int | None = None) -> None:
        with self._lock:
            if not enabled:
                # Update the gate even if disk I/O fails; no later callback may resume this run.
                self._generation = self._generation+1 if generation is None else generation
                self._enabled = False
                with self._connection() as db:
                    db.execute("DELETE FROM reports WHERE state='active'")
            else:
                if generation is not None:
                    self._generation = generation
                self._enabled = True

    def recover_interrupted(self) -> int:
        with self._lock, self._connection() as db:
            if not self._enabled:
                db.execute("DELETE FROM reports WHERE state='active'")
                return 0
            db.execute("DELETE FROM reports WHERE state='active' AND generation<>?", (self._generation,))
            return db.execute("UPDATE reports SET state='pending' WHERE state='active'").rowcount

    def begin(self, report: ShopRunReport, generation: int) -> bool:
        with self._lock:
            if not self._enabled or generation != self._generation:
                return False
            with self._connection() as db:
                if db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] >= self.capacity:
                    return False
                db.execute("""INSERT INTO reports
                    (run_id,strategy,skystone_spent,covenant_purchases,mystic_purchases,state,generation)
                    VALUES (?,?,?,?,?,'active',?)""", (*report.payload().values(), generation))
                return True

    def save(self, report: ShopRunReport, generation: int, *, final: bool = False) -> bool:
        with self._lock:
            if not self._enabled or generation != self._generation:
                return False
            with self._connection() as db:
                row = db.execute("SELECT * FROM reports WHERE run_id=? AND state='active'", (report.run_id,)).fetchone()
                if row is None:
                    return False
                if report.strategy != row['strategy'] or any(new < row[name] for new, name in zip(
                        report.counters, ('skystone_spent', 'covenant_purchases', 'mystic_purchases'))):
                    raise ValueError("confirmed report counters cannot decrease")
                db.execute("""UPDATE reports SET skystone_spent=?,covenant_purchases=?,
                    mystic_purchases=?,state=? WHERE run_id=?""",
                    (*report.counters, 'pending' if final else 'active', report.run_id))
                return True

    @staticmethod
    def _report(row: sqlite3.Row) -> ShopRunReport:
        return ShopRunReport(*(row[name] for name in (
            'run_id','strategy','skystone_spent','covenant_purchases','mystic_purchases')))

    def next_due(self, now: float) -> PendingReport | None:
        with self._lock:
            if not self._enabled or self._paused:
                return None
            with self._connection(write=False) as db:
                row = db.execute("""SELECT * FROM reports WHERE state='pending' AND retry_at<=?
                    ORDER BY position LIMIT 1""", (now,)).fetchone()
                return PendingReport(self._report(row), row['attempts']) if row else None

    def acknowledge(self, run_id: str) -> None:
        with self._lock, self._connection() as db:
            db.execute("DELETE FROM reports WHERE run_id=? AND state='pending'", (run_id,))

    def retry_later(self, run_id: str, now: float, *, minimum_delay: float = 0) -> float:
        with self._lock, self._connection() as db:
            row = db.execute("SELECT attempts FROM reports WHERE run_id=? AND state='pending'", (run_id,)).fetchone()
            if row:
                delay = max(RETRY_DELAYS[min(row[0], len(RETRY_DELAYS)-1)], minimum_delay)
                db.execute("UPDATE reports SET attempts=attempts+1,retry_at=? WHERE run_id=?", (now+delay, run_id))
                return now+delay
            return now+60

    def reject(self, run_id: str, reason: str) -> None:
        with self._lock, self._connection() as db:
            db.execute("UPDATE reports SET state='rejected',rejection=? WHERE run_id=? AND state='pending'",
                       (reason, run_id))

    def retry_pending(self) -> None:
        with self._lock, self._connection() as db:
            db.execute("UPDATE reports SET attempts=0,retry_at=0 WHERE state='pending'")

    def counts(self) -> tuple[int, int]:
        with self._lock, self._connection(write=False) as db:
            rows = dict(db.execute("SELECT state,COUNT(*) FROM reports GROUP BY state"))
            return rows.get('pending', 0)+rows.get('rejected', 0), rows.get('rejected', 0)
