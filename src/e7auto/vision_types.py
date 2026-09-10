from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import Point, Rect


@dataclass(frozen=True, slots=True)
class Observation:
    object_id: str
    confidence: float
    roi: Rect
    anchor: Point


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


class PurchaseOutcome(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    INSUFFICIENT_FUNDS = "insufficient_funds"
