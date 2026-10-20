from __future__ import annotations
from dataclasses import dataclass
from math import floor, ceil
from e7auto.core.types import Point, Rect, ClientPoint, ClientRect


@dataclass(frozen=True, slots=True)
class MatchGeometry:
    x: float
    y: float
    width: float
    height: float
    scale_x: float
    scale_y: float

    @property
    def bounds(self):
        x,y=floor(self.x),floor(self.y)
        return ClientRect(x,y,ceil(self.x+self.width)-x,ceil(self.y+self.height)-y)

    def point(self, fractions=(.5,.5), offset=(0.,0.)):
        return ClientPoint(round(self.x+self.width*fractions[0]+offset[0]*self.scale_x),
                           round(self.y+self.height*fractions[1]+offset[1]*self.scale_y))

@dataclass(frozen=True, slots=True)
class Observation:
    object_id: str
    confidence: float
    bounds: Rect
    anchor: Point
    search_region: Rect | None = None
    structure_confidence: float | None = None
    candidate_id: tuple[int, ...] | None = None
    geometry: MatchGeometry | None = None
