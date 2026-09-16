from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SavedOverlayPosition:
    x: int
    y: int


class OverlayPositionStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> SavedOverlayPosition | None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(raw, dict):
            return None
        x = raw.get("x")
        y = raw.get("y")
        if isinstance(x, bool) or not isinstance(x, int):
            return None
        if isinstance(y, bool) or not isinstance(y, int):
            return None
        return SavedOverlayPosition(x, y)

    def save(self, position: SavedOverlayPosition) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"x": position.x, "y": position.y}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self._path)
