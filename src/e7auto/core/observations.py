from __future__ import annotations
from dataclasses import dataclass
from e7auto.core.types import Point, Rect

@dataclass(frozen=True, slots=True)
class Observation:
    object_id: str
    confidence: float
    roi: Rect
    anchor: Point
