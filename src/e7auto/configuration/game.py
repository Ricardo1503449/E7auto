"""Game executable targets shared by production and window calibration tools."""
from __future__ import annotations

from pathlib import Path

from e7auto.core.ports import GameClient


def load_game_clients(game: dict, errors: list[str]) -> tuple[GameClient, ...]:
    if "clients" in game:
        if "executable_path" in game:
            errors.append("game.clients and game.executable_path cannot both be configured")
        raw = game["clients"]
        if not isinstance(raw, dict) or not raw:
            errors.append("game.clients must be a nonempty mapping")
            return ()
        if set(raw) - {"stove", "steam"}:
            errors.append("game.clients only supports stove and steam")
        entries = ((key, value, f"game.clients.{key}") for key, value in raw.items())
    else:
        value = game.get("executable_path")
        name = Path(value).name.casefold() if isinstance(value, str) else ""
        client_id = {"epicseven.exe": "stove", "epicseven_steam.exe": "steam"}.get(name, "legacy")
        entries = ((client_id, value, "game.executable_path"),)

    clients: list[GameClient] = []
    seen: set[str] = set()
    for client_id, value, location in entries:
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{location} must be a nonempty .exe path or filename")
            continue
        path = Path(value)
        if path.suffix.casefold() != ".exe" or (not path.is_absolute() and path.name != str(path)):
            errors.append(f"{location} must be an absolute .exe path or an .exe filename")
            continue
        normalized = str(path).casefold()
        if normalized in seen:
            errors.append(f"{location} duplicates another game executable")
        seen.add(normalized)
        clients.append(GameClient(client_id, str(path)))
    return tuple(clients)
