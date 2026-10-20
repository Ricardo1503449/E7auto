"""Template-local geometry shared by configuration and pixel recognition."""
from dataclasses import dataclass

from e7auto.core.types import Rect


@dataclass(frozen=True, slots=True)
class ComponentLayout:
    primary: Rect
    secondary: Rect
    tolerance: tuple[int, int]
