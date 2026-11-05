import pytest

from e7auto.configuration.game import load_game_clients
from e7auto.core.ports import GameClient


@pytest.mark.parametrize("game,expected", [
    ({"clients": {"stove": "EpicSeven.exe", "steam": "EpicSeven_Steam.exe"}},
     (GameClient("stove", "EpicSeven.exe"), GameClient("steam", "EpicSeven_Steam.exe"))),
    ({"clients": {"steam": r"E:\Games\Epic Seven\EpicSeven_Steam.exe"}},
     (GameClient("steam", r"E:\Games\Epic Seven\EpicSeven_Steam.exe"),)),
    ({"executable_path": "EpicSeven.exe"}, (GameClient("stove", "EpicSeven.exe"),)),
    ({"executable_path": "EpicSeven_Steam.exe"}, (GameClient("steam", "EpicSeven_Steam.exe"),)),
])
def test_game_clients_accepts_both_platforms_and_legacy_config(game, expected):
    errors = []
    assert load_game_clients(game, errors) == expected
    assert errors == []


@pytest.mark.parametrize("game,error", [
    ({"clients": {}}, "nonempty mapping"),
    ({"clients": []}, "nonempty mapping"),
    ({"clients": None}, "nonempty mapping"),
    ({"clients": {"other": "Game.exe"}}, "only supports stove and steam"),
    ({"clients": {"stove": "EpicSeven.exe"}, "executable_path": "Game.exe"}, "cannot both"),
    ({"clients": {"stove": "EpicSeven.exe", "steam": "EPICSEVEN.EXE"}}, "duplicates"),
    ({"clients": {"steam": ""}}, "nonempty .exe"),
    ({"clients": {"steam": "Steam.url"}}, "absolute .exe path or an .exe filename"),
    ({"clients": {"steam": r"Games\EpicSeven_Steam.exe"}}, "absolute .exe path or an .exe filename"),
    ({}, "game.executable_path"),
])
def test_game_clients_rejects_invalid_or_ambiguous_config(game, error):
    errors = []
    load_game_clients(game, errors)
    assert any(error in item for item in errors)
