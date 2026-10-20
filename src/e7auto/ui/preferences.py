"""Small, local UI preferences, independent of automation configuration."""
from __future__ import annotations

import json
from pathlib import Path


class NotificationPreferences:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def load(self) -> bool:
        value = self._read().get("run_end_notifications", True)
        return value if isinstance(value, bool) else True

    def save(self, enabled: bool) -> None:
        values = self._read()
        values["run_end_notifications"] = enabled
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._path)


class ShopStatisticsPreferences(NotificationPreferences):
    """Use the shared preference file without changing the notification setting."""
    def load(self) -> bool:
        value = self._read().get("shop_statistics", True)
        return value if isinstance(value, bool) else True

    def save(self, enabled: bool) -> None:
        values = self._read()
        values["shop_statistics"] = enabled
        if not enabled:
            values["shop_statistics_generation"] = self.generation()+1
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._path)

    def generation(self) -> int:
        value = self._read().get("shop_statistics_generation", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
