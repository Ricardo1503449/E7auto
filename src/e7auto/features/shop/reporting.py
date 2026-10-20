"""Observe already-confirmed snapshots; optional reporting never controls a run."""
from __future__ import annotations

import uuid

from e7auto.core.domain import RuntimeSnapshot
from e7auto.core.reporting import ReportJournalPort, ShopRunReport


class ShopReportRecorder:
    def __init__(self, queue: ReportJournalPort, *, continuous_refresh: bool = False) -> None:
        self.queue = queue
        self.run_id = uuid.uuid4().hex
        self.strategy = 'continuous' if continuous_refresh else 'transfer'
        self.generation = queue.generation
        self._started = False
        self._suppressed = not queue.enabled
        self._finished = False
        self._last: tuple[int, int, int] | None = None

    def observe(self, snapshot: RuntimeSnapshot, logger=None) -> None:
        if self._suppressed or self._finished or snapshot.feature_id != 'shop_refresh':
            return
        if not self.queue.enabled or self.generation != self.queue.generation:
            self._suppressed = True
            return
        try:
            tallies = {tally.target_id: tally.acquired for tally in snapshot.targets}
            report = ShopRunReport(self.run_id, self.strategy, snapshot.refresh_spent,
                                   tallies.get('covenant_bookmark', 0), tallies.get('mystic_medal', 0))
            if not self._started:
                self._started = self.queue.begin(report, self.generation)
                if not self._started:
                    self._suppressed = True  # Capacity is intentionally silent for this whole run.
                    return
            if report.counters != self._last or snapshot.is_final:
                if self.queue.save(report, self.generation, final=snapshot.is_final):
                    self._last = report.counters
                    self._finished = snapshot.is_final
        except Exception as error:
            if logger is not None:
                try:
                    logger.event('shop_statistics_storage_failed', error=type(error).__name__)
                except Exception:
                    pass
