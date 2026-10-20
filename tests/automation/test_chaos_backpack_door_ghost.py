import pytest

from e7auto.core.domain import RuntimeSnapshot
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


def setup(event):
    game = ChaosGame(chaos_config(), event_type=event)
    flow = ChaosFlow(game.config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run("new-events", 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState(node_kind="event")
    game.state = game.kind = "event"
    flow.state.ranks["xiaochun"] = game.ranks["xiaochun"] = 2
    return game, flow


@pytest.mark.parametrize("event,side,steps", [
    ("event_backpack", "left", ["event_reward_close", "text_continue"]),
    ("event_spirit_door", "right", ["event_reward_close", "event_reward_close", "text_continue"]),
    ("event_ghost", "right", ["event_rank_enter", "hero_xiaochun", "rank_confirm", "rank_close", "text_continue"]),
])
def test_new_event_binding_direction_complete_route_and_map(event, side, steps):
    game, flow = setup(event)
    assert event in HANDLERS
    scene = game.config.chaos.scenes[event]
    assert scene.template_key == "chaos:" + event
    assert (scene.action[0] < .5) == (side == "left")
    flow._event()
    assert game.actions == [event, *steps]
    assert game.state == "map" and flow.state.floor == 1
    changes = [data for name, data in flow.runtime.deps.logger.events if name == "chaos_rank_changed"]
    assert flow.state.ranks["xiaochun"] == (3 if event == "event_ghost" else 2)
    assert len(changes) == int(event == "event_ghost")
    assert not flow.state.counted


def test_spirit_door_requires_new_card_before_second_close(monkeypatch):
    game, flow = setup("event_spirit_door")
    monkeypatch.setattr(game, "event_card_changed", lambda frame, previous: False)
    with pytest.raises(StopExecution):
        flow._event()
    assert game.actions == ["event_spirit_door", "event_reward_close"]


def test_ghost_requires_supported_rank_target_before_option_click():
    game, flow = setup("event_ghost")
    flow.state.ranks.update(xiaochun=5, twins=5, ivanna=5)
    with pytest.raises(StopExecution):
        flow._event()
    assert game.actions == []
