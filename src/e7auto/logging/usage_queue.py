"""Durable daily activity outbox; acknowledgments never discard newer changes."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import sqlite3
from threading import RLock
import uuid

from e7auto.core.usage import FEATURES, UsageDayReport, activity_day, version_key
from e7auto.logging.report_queue import RETRY_DELAYS


@dataclass(frozen=True, slots=True)
class PendingUsage:
    report: UsageDayReport
    revision: int


class UsageQueue:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()
        self._paused = False

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=0.05)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), value TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS days (
                day TEXT PRIMARY KEY, version TEXT NOT NULL, features INTEGER NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1, acknowledged INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                rejected INTEGER NOT NULL DEFAULT 0)''')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def record(self, now: float, version: str, feature: str | None = None) -> None:
        version_key(version)
        if feature is not None and feature not in FEATURES:
            raise ValueError('unknown activity feature')
        day = activity_day(now)
        mask = 0 if feature is None else 1 << FEATURES.index(feature)
        with self._lock, self._connection() as db:
            db.execute('INSERT OR IGNORE INTO identity VALUES (1,?)', (uuid.uuid4().hex,))
            row = db.execute('SELECT * FROM days WHERE day=?', (day,)).fetchone()
            if row is None:
                db.execute('INSERT INTO days(day,version,features) VALUES (?,?,?)', (day, version, mask))
            else:
                newest = max((row['version'], version), key=version_key)
                combined = row['features'] | mask
                if newest != row['version'] or combined != row['features']:
                    db.execute('''UPDATE days SET version=?,features=?,revision=revision+1,
                        attempts=0,retry_at=0,rejected=0 WHERE day=?''', (newest, combined, day))
            cutoff = (date.fromisoformat(day)-timedelta(days=89)).isoformat()
            db.execute('DELETE FROM days WHERE day<?', (cutoff,))

    def pause_uploads(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused

    def next_due(self, now: float) -> PendingUsage | None:
        with self._lock:
            if self._paused:
                return None
            with self._connection() as db:
                row = db.execute('''SELECT * FROM days WHERE revision>acknowledged
                    AND rejected=0 AND retry_at<=? ORDER BY day LIMIT 1''', (now,)).fetchone()
                if row is None:
                    return None
                identity = db.execute('SELECT value FROM identity WHERE singleton=1').fetchone()[0]
                features = tuple(value for index, value in enumerate(FEATURES) if row['features'] & (1 << index))
                return PendingUsage(UsageDayReport(identity, row['day'], row['version'], features), row['revision'])

    def acknowledge(self, pending: PendingUsage) -> None:
        with self._lock, self._connection() as db:
            db.execute('UPDATE days SET acknowledged=MAX(acknowledged,?) WHERE day=?',
                       (pending.revision, pending.report.date))

    def retry_later(self, pending: PendingUsage, now: float, *, minimum_delay: float = 0) -> float:
        with self._lock, self._connection() as db:
            row = db.execute('SELECT attempts FROM days WHERE day=?', (pending.report.date,)).fetchone()
            delay = max(RETRY_DELAYS[min(row[0], len(RETRY_DELAYS)-1)] if row else 60, minimum_delay)
            db.execute('UPDATE days SET attempts=attempts+1,retry_at=? WHERE day=? AND revision=?',
                       (now+delay, pending.report.date, pending.revision))
            return now+delay

    def reject(self, pending: PendingUsage) -> None:
        with self._lock, self._connection() as db:
            db.execute('UPDATE days SET rejected=1 WHERE day=? AND revision=?',
                       (pending.report.date, pending.revision))
