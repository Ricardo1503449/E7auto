import pytest

from e7auto.core.domain import RuntimeSnapshot
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


def setup(event, *, floor=1, intro=0):
    game = ChaosGame(chaos_config(), event_type=event, event_intro_pages=intro)
    flow = ChaosFlow(game.config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run("five-events", 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState(floor=floor, node_kind="event")
    game.floor = floor
    game.kind = "event"
    game.state = "event_intro" if intro else "event"
    return game, flow


@pytest.mark.parametrize("event,floor,intro,closes,pages,crop,target", [
    ("event_erased_records", 2, 1, 1, 1, (359, 1069, 1663, 168), (1850, 1090)),
    ("event_fairy_dance", 3, 0, 0, 1, (542, 1069, 1254, 158), (1510, 1090)),
    ("event_blue_flame_altar", 1, 0, 2, 2, (520, 1069, 1276, 158), (1510, 1090)),
    ("event_broken_mask", 3, 0, 1, 2, (271, 1069, 1827, 158), (1161, 1090)),
    ("event_ancient_corridor", 3, 0, 0, 1, (589, 1069, 1089, 157), (810, 1090)),
])
def test_new_bindings_clicks_and_complete_event_routes(
    monkeypatch, event, floor, intro, closes, pages, crop, target,
):
    game, flow = setup(event, floor=floor, intro=intro)
    scene = game.config.chaos.scenes[event]
    assert event in HANDLERS and scene.template_key == "chaos:" + event
    x, y, width, height = crop
    assert (round(x + width * scene.action[0]), round(y + height * scene.action[1])) == target
    remaining = [pages]
    advance_map = game._map_next

    def next_text_or_map():
        if game.state == "text" and remaining[0] > 1:
            remaining[0] -= 1
            return
        advance_map()

    monkeypatch.setattr(game, "_map_next", next_text_or_map)
    ranks_before = dict(flow.state.ranks)
    flow._event()
    assert game.actions == ["text_continue"] * intro + [event] + ["event_reward_close"] * closes + ["text_continue"] * pages
    assert game.state == "map" and game.floor == flow.state.floor == floor
    assert game.node_index == 1
    assert flow.state.ranks == ranks_before and not flow.state.counted


def test_blue_flame_cannot_close_the_same_card_twice(monkeypatch):
    game, flow = setup("event_blue_flame_altar")
    monkeypatch.setattr(game, "event_card_changed", lambda frame, previous: False)
    with pytest.raises(StopExecution):
        flow._event()
    assert game.actions == ["event_blue_flame_altar", "event_reward_close"]
