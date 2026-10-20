from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )


@dataclass(frozen=True, slots=True)
class Size:
    width: int
    height: int


@dataclass(frozen=True, slots=True, eq=False)
class ClientPoint(Point):
    """A point already measured in client pixels; never scale it again."""

    def __eq__(self, other):
        return isinstance(other, Point) and (self.x, self.y) == (other.x, other.y)

    __hash__ = Point.__hash__


@dataclass(frozen=True, slots=True, eq=False)
class ClientRect(Rect):
    """Global client-pixel bounds, including those measured in a cropped view."""

    def __eq__(self, other):
        return isinstance(other, Rect) and (self.x,self.y,self.width,self.height) == (other.x,other.y,other.width,other.height)

    __hash__ = Rect.__hash__
