"""Reuse only the immediately preceding stable map for the next node choice."""
from dataclasses import replace

import pytest

from e7auto.bootstrap import AutomationSession
from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.types import Point, Rect
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


def map_flow():
    config = chaos_config()
    config = replace(config, chaos=replace(config.chaos, timing=dict(
        config.chaos.timing, entry_timeout_ms=1000)))
    game = ChaosGame(config)
    stop = StopController()
    publisher = SnapshotPublisher(RuntimeSnapshot.chaos_run("map-handoff", 1), lambda _: None)
    flow = ChaosFlow(config, game.dependencies(), stop, publisher)
    flow.runtime.prepare()
    flow.state = RoundState()
    game.state = "map"
    return game, flow, stop


def test_each_node_uses_one_three_frame_map_confirmation_across_two_rounds(monkeypatch):
    game = ChaosGame(chaos_config())
    original = game.map_view
    stable_samples = []
    selection_samples = []

    def observe(frame):
        view = original(frame)
        if view is not None:
            stable_samples.append(game.captures)
        return view

    def clicked(g, key):
        if key.startswith("node_") and key != "node_enter":
            selection_samples.append(len(stable_samples))
            stable_samples.clear()

    monkeypatch.setattr(game, "map_view", observe)
    game.on_click = clicked
    session = AutomationSession(game.config, game.dependencies(), StopController(), lambda _: None)
    final = session.run_chaos(2)
    assert final.stop_reason == StopReason.CHAOS_COMPLETE
    assert final.rounds_completed == 2 and game.state == "home"
    # Covers entry, normal/elite/boss settlement (including next floors and
    # unclaimed vouchers), camp, supply, event and shop returns.
    assert selection_samples == [3] * (len(game.routes) * 2)


def test_map_handoff_is_consumed_once_without_recapture():
    game, flow, _ = map_flow()
    view = flow._map(1)
    before = game.captures
    assert flow._map_for_selection(1) is view
    assert game.captures == before
    flow._map_for_selection(1)
    assert game.captures == before + 3


@pytest.mark.parametrize("boundary", ["frame", "raw_capture", "recovery", "new_round", "cleanup"])
def test_intervening_work_invalidates_previous_map(boundary):
    game, flow, _ = map_flow()
    old = flow._map(1)
    if boundary == "frame":
        flow._frame()
    elif boundary == "raw_capture":
        flow.runtime.capture_raw()
    elif boundary == "recovery":
        game.network_remaining = 4
        flow.runtime.capture()
        assert game.actions.count("network_retry") == 4
    elif boundary == "new_round":
        flow.state = RoundState()
    else:
        flow.release_cached_game_frame()
        flow.state = RoundState()
    before = game.captures
    current = flow._map_for_selection(1)
    assert current is not old
    assert game.captures == before + 3


def test_floor_change_rejects_old_map_even_with_same_round():
    game, flow, _ = map_flow()
    flow._map(1)
    game.floor = flow.state.floor = 2
    before = game.captures
    assert flow._map_for_selection(2).floor == 2
    assert game.captures == before + 3


def test_click_cannot_leave_a_map_available_for_another_selection():
    game, flow, _ = map_flow()
    view = flow._map(1)
    flow._tap(view.nodes[0].detection)
    assert game.state == "node"
    with pytest.raises(StopExecution) as error:
        flow._map_for_selection(1)
    assert error.value.reason == StopReason.RECOGNITION_TIMEOUT
    assert game.actions == ["node_normal"]


@pytest.mark.parametrize("boundary", ["manual_stop", "window"])
def test_map_handoff_preserves_stop_and_window_checks_before_node_click(boundary):
    game, flow, stop = map_flow()
    flow._map(1)
    if boundary == "manual_stop":
        stop.request(StopReason.MANUAL_STOP)
        expected = StopReason.MANUAL_STOP
    else:
        windows = flow.runtime.deps.windows
        windows.state = replace(windows.state, exists=False)
        expected = StopReason.WINDOW_ABNORMAL
    with pytest.raises(StopExecution) as error:
        flow._explore()
    assert error.value.reason == expected
    assert game.actions == []


def test_handoff_uses_latest_node_only_after_three_stable_frames(monkeypatch):
    game, flow, _ = map_flow()
    original = game.map_view
    observations = []

    def changing(frame):
        view = original(frame)
        index = len(observations)
        if index == 2:
            view = None
        elif index >= 3:
            node = view.nodes[0]
            hit = replace(node.detection, bounds=Rect(800, 500, 100, 100), anchor=Point(850, 550))
            view = replace(view, nodes=(replace(node, detection=hit),))
        observations.append(view)
        return view

    monkeypatch.setattr(game, "map_view", changing)
    confirmed = flow._map(1)
    assert len(observations) == 6
    before = game.captures
    reused = flow._map_for_selection(1)
    assert reused is confirmed
    assert reused.nodes[0].detection.anchor == Point(850, 550)
    assert game.captures == before


def test_recovery_during_map_confirmation_discards_old_stable_samples(monkeypatch):
    game, flow, _ = map_flow()
    original = game.map_view
    generations = []

    def observe(frame):
        view = original(frame)
        if view is not None:
            generations.append(flow.runtime.network_recovery_generation)
            if len(generations) == 2:
                game.network_remaining = 4
        return view

    monkeypatch.setattr(game, "map_view", observe)
    confirmed = flow._map(1)
    assert generations == [0, 0, 1, 1, 1]
    assert game.actions.count("network_retry") == 4
    before = game.captures
    assert flow._map_for_selection(1) is confirmed
    assert game.captures == before


@pytest.mark.parametrize("event", ["event_trap", "event_blood_shelter", "event_tablet"])
def test_event_return_hands_its_confirmed_original_floor_to_next_selection(event, monkeypatch):
    game, flow, _ = map_flow()
    game.state, game.kind, game.node_index = "event", "event", 3
    game.event_type = event
    original = game.map_view
    maps = []

    def observe(frame):
        view = original(frame)
        if view is not None:
            maps.append(view)
        return view

    monkeypatch.setattr(game, "map_view", observe)
    flow._event()
    assert len(maps) == 3 and game.state == "map"
    before = game.captures
    view = flow._map_for_selection(1)
    assert view is maps[-1] and view.floor == flow.state.floor == 1
    assert game.captures == before
