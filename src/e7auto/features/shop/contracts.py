from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from e7auto.core.types import Point, Rect, ClientRect
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
class RowPixelChange:
    row_id: str
    bounds: ClientRect
    changed_fraction: float


@dataclass(frozen=True, slots=True)
class ScrollContentCandidate:
    candidate_id: int
    phase_shift_x: float
    phase_shift_y: float
    phase_response: float
    content_scores: tuple[float | None, ...]
    matching_rows: int
    quality: float | None


@dataclass(frozen=True, slots=True)
class ScrollMovementObservation:
    mean_absolute_difference: float
    changed_fraction: float
    maximum_difference: int
    phase_shift_x: float
    phase_shift_y: float
    phase_response: float
    content_verified: bool = False
    content_reason: str = "not_checked"
    content_scores: tuple[float | None, ...] = ()
    maximum_row_changed_fraction: float = 0.0
    row_pixel_changes: tuple[RowPixelChange, ...] = ()
    content_candidates: tuple[ScrollContentCandidate, ...] = ()
    content_selected_candidate: int | None = None
    content_runner_up_candidate: int | None = None
    content_margin: float | None = None


class PurchaseOutcome(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    SUCCESS_BUTTON = "success_button"
    INSUFFICIENT_FUNDS = "insufficient_funds"


class ShopVisionPort(Protocol):
    def navigation_menu(self, frame: object) -> Observation | None: ...
    def navigation_tavern(self, frame: object) -> Observation | None: ...
    def inventory_scene_state(self, frame: object, screen: str, *,
                              slot_ids: frozenset[str] | None = None) -> str: ...

    def inventory_scene_stability(self, before: object, after: object, screen: str, *,
                                  slot_ids: frozenset[str] | None = None) -> ScrollMovementObservation: ...

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

    def sky_stone_balance(self, frame: object) -> SkyStoneBalanceObservation | None: ...

    def network_connection_error(self, frame: object) -> Observation | None: ...

    def network_retry(self, frame: object) -> Observation | None: ...
