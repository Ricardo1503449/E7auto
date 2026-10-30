"""Small, local UI preferences, independent of automation configuration."""
from __future__ import annotations

import json
from pathlib import Path


def _read_preferences(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_preferences(path: Path, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class NotificationPreferences:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict:
        return _read_preferences(self._path)

    def load(self) -> bool:
        value = self._read().get("run_end_notifications", True)
        return value if isinstance(value, bool) else True

    def save(self, enabled: bool) -> None:
        values = self._read()
        values["run_end_notifications"] = enabled
        _write_preferences(self._path, values)


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
        _write_preferences(self._path, values)

    def generation(self) -> int:
        value = self._read().get("shop_statistics_generation", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class AnnouncementPreferences:
    """Remember the acknowledged content independently of the application version."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> str | None:
        value = _read_preferences(self._path).get("announcement_seen_id")
        return value if isinstance(value, str) else None

    def save(self, announcement_id: str) -> None:
        values = _read_preferences(self._path)
        values["announcement_seen_id"] = announcement_id
        _write_preferences(self._path, values)
