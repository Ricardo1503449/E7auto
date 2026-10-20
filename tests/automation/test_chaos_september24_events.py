import pytest

from e7auto.core.domain import RuntimeSnapshot
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


def setup(event, floor=1):
    game = ChaosGame(chaos_config(), event_type=event)
    flow = ChaosFlow(game.config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run("september24-events", 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState(floor=floor, node_kind="event")
    game.state = game.kind = "event"
    game.floor = floor
    return game, flow


@pytest.mark.parametrize("event,floor,layout,target,steps", [
    ("event_angry_inscription", 1, 2, (1510, 1090), ["text_continue"]),
    ("event_ancient_stele", 2, 2, (1510, 1090), ["event_reward_close", "text_continue"]),
    ("event_blood_meteorite", 2, 3, (1850, 1090), ["text_continue"]),
    ("event_windy_cliff", 1, 2, (1510, 1090), ["text_continue"]),
    ("event_forked_path", 2, 2, (810, 1090), ["event_reward_close", "event_reward_close", "text_continue"]),
    ("event_starlight_flower", 1, 3, (480, 1090), ["event_rank_enter", "hero_xiaochun", "rank_confirm", "rank_close", "text_continue"]),
    ("event_grieving_woman", 2, 3, (1161, 1090), ["event_reward_close", "event_reward_close", "text_continue"]),
])
def test_index_binding_choice_complete_route_and_confirmed_closure(event, floor, layout, target, steps):
    game, flow = setup(event, floor)
    scene = game.config.chaos.scenes[event]
    spec = game.config.chaos.event_recognition['templates'][event]
    assert event in HANDLERS and scene.template_key == 'chaos:' + event
    assert spec['layout'] == layout and len(spec['titles']) == len(spec['weights']) == layout
    # Read the registered image dimensions without running another image scan.
    import struct
    width, height = struct.unpack('>II', game.config.template_paths[scene.template_key].read_bytes()[16:24])
    assert (round(spec['origin'][0] + width * scene.action[0]),
            round(spec['origin'][1] + height * scene.action[1])) == target
    flow._event()
    assert game.actions == [event, *steps]
    assert game.state == 'map' and game.floor == flow.state.floor == floor
    assert game.node_index == 1 and not flow.state.counted
    changes = [data for name, data in flow.runtime.deps.logger.events if name == 'chaos_rank_changed']
    assert flow.state.ranks['xiaochun'] == (2 if event == 'event_starlight_flower' else 1)
    assert len(changes) == int(event == 'event_starlight_flower')
    closes = [data for name, data in flow.runtime.deps.logger.events if name == 'chaos_event_close_confirmed']
    expected_closes = steps.count('event_reward_close') + steps.count('rank_close')
    assert [data['pages'] for data in closes] == ([expected_closes] if expected_closes else [])


@pytest.mark.parametrize('event', ['event_forked_path', 'event_grieving_woman'])
def test_double_card_binding_does_not_finish_when_first_card_stays_visible(event):
    game, flow = setup(event)

    def keep_first_card(g, key):
        if key == 'event_reward_close':
            g.state = 'cairn_hp'

    game.on_click = keep_first_card
    with pytest.raises(StopExecution):
        flow._event()
    assert game.actions == [event] + ['event_reward_close'] * game.config.chaos.timing['event_close_max_attempts']
    assert flow.state.reward_stage != 'event_closed'


def test_flower_requires_supported_rank_target_before_spending_option():
    game, flow = setup('event_starlight_flower')
    flow.state.ranks.update(xiaochun=5, twins=5, ivanna=5)
    with pytest.raises(StopExecution):
        flow._event()
    assert game.actions == []
