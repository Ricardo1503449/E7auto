"""Minimal, immutable shop statistics; no account or installation identity."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Protocol

MAX_COUNTER = 2_147_483_647
STRATEGIES = frozenset({"transfer", "continuous"})


@dataclass(frozen=True, slots=True)
class ShopRunReport:
    run_id: str
    strategy: str
    skystone_spent: int = 0
    covenant_purchases: int = 0
    mystic_purchases: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", self.run_id):
            raise ValueError("report run_id must be a full random UUID hex string")
        if self.strategy not in STRATEGIES:
            raise ValueError("unknown shop reporting strategy")
        for value in self.counters:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNTER:
                raise ValueError("shop report counters must be non-negative 32-bit integers")

    @property
    def counters(self) -> tuple[int, int, int]:
        return self.skystone_spent, self.covenant_purchases, self.mystic_purchases

    def payload(self) -> dict[str, str | int]:
        return asdict(self)


class ReportJournalPort(Protocol):
    @property
    def enabled(self) -> bool: ...
    @property
    def generation(self) -> int: ...
    def begin(self, report: ShopRunReport, generation: int) -> bool: ...
    def save(self, report: ShopRunReport, generation: int, *, final: bool = False) -> bool: ...
