from __future__ import annotations
from typing import Protocol
from dataclasses import dataclass
from e7auto.core.domain import StopReason
from e7auto.core.ports import CachedGameFrame

class FeatureFlow(Protocol):
    def execute(self) -> None: ...
    def finish_normal_run(self, reason: StopReason) -> None: ...
    def cached_game_frame(self) -> CachedGameFrame | None: ...
    def release_cached_game_frame(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RunPolicy:
    """The application declares which outcomes need return navigation or diagnostics."""
    normal_completion: frozenset[StopReason]
    expected_stops: frozenset[StopReason]
