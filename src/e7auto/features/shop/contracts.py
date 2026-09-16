from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from e7auto.core.types import Point, Rect
from e7auto.core.observations import Observation

@dataclass(frozen=True, slots=True)
class InventoryMatch:
    target_id: str
    display_name: str
    slot_id: str
    slot_order: int
    buy_point: Point
    confidence: float
    roi: Rect
    is_purchased: bool = False


@dataclass(frozen=True, slots=True)
class SkyStoneBalanceObservation:
    value: int
    confidence: float
    roi: Rect


@dataclass(frozen=True, slots=True)
class ScrollMovementObservation:
    mean_absolute_difference: float
    changed_fraction: float
    maximum_difference: int
    phase_shift_x: float
    phase_shift_y: float
    phase_response: float


SCROLL_OVERLAP_MINIMUM_HEIGHT_FRACTION = 0.50


SCROLL_OVERLAP_MAXIMUM_HORIZONTAL_SHIFT_PX = 4.0


@dataclass(frozen=True, slots=True)
class ScrollOverlapObservation:
    accepted: bool
    reason: str
    overlap_height_fraction: float
    block_scores: tuple[float | None, ...] = ()
    passed_blocks: int = 0


class PurchaseOutcome(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    SUCCESS_BUTTON = "success_button"
    INSUFFICIENT_FUNDS = "insufficient_funds"


class ShopVisionPort(Protocol):
    def main_shop_icon(self, frame: object) -> Observation | None: ...

    def shop_ready(self, frame: object) -> Observation | None: ...

    def shop_exit_icon(self, frame: object) -> Observation | None: ...

    def refresh_confirm_dialog(self, frame: object) -> Observation | None: ...

    def confirm_dialog(self, frame: object, target_id: str) -> Observation | None: ...

    def purchase_outcome(self, frame: object, target_id: str, item_roi: Rect) -> PurchaseOutcome: ...

    def scan_inventory(
        self,
        frame: object,
        screen: str,
        enabled_target_ids: frozenset[str] | None = None,
        excluded_slot_ids: frozenset[str] = frozenset(),
    ) -> tuple[InventoryMatch, ...]: ...

    def inventory_scroll_movement(
        self,
        before: object,
        after: object,
    ) -> ScrollMovementObservation: ...

    def inventory_scroll_stability(
        self,
        before: object,
        after: object,
    ) -> ScrollMovementObservation: ...

    def prepare_scroll_overlap_reference(self, frame: object) -> object: ...

    def verify_scroll_overlap(
        self, reference: object, current: object, shift_x: float, shift_y: float,
    ) -> ScrollOverlapObservation: ...

    def sky_stone_balance(self, frame: object) -> SkyStoneBalanceObservation | None: ...

    def network_connection_error(self, frame: object) -> Observation | None: ...

    def network_retry(self, frame: object) -> Observation | None: ...
