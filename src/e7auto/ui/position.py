from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class SavedOverlayPosition:
    x: int
    y: int
    anchor: Literal["center", "top_left"] = "center"


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
        anchor = raw.get("anchor", "top_left")
        if anchor not in ("center", "top_left"):
            return None
        return SavedOverlayPosition(x, y, anchor)

    def save(self, position: SavedOverlayPosition) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"x": position.x, "y": position.y, "anchor": position.anchor},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(self._path)
