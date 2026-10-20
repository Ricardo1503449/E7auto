"""Mutable shop state shared explicitly by inventory and refresh components."""
from dataclasses import dataclass
from .contracts import InventoryMatch


@dataclass(slots=True)
class ShopState:
    enabled_target_ids: frozenset[str]
    mandatory_target_ids: frozenset[str]
    continuous_refresh: bool = False
    refresh_strategy_stage: int = 0
    refreshes_without_mandatory_target: int = 0
    consecutive_no_target_refreshes: int = 0
    trusted_sky_stone_balance: int | None = None
    pending_top_scan: tuple[InventoryMatch, ...] | None = None
