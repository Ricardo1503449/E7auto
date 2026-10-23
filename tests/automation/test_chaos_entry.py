"""Chaos wiring to shared startup wake and bounded entry recovery."""
from dataclasses import replace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


@pytest.fixture
def entry():
    game = ChaosGame(chaos_config())
    flow = ChaosFlow(game.config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run("entry", 1), lambda _: None))
    # Exercise real startup/entry, then stop before the unrelated exploration flow.
    game.on_click = lambda _, key: flow.runtime.control.request(StopReason.MANUAL_STOP) if key == "exploration_card" else None
    return flow, game


def execute_reason(flow):
    with pytest.raises(StopExecution) as stopped:
        flow.execute()
    return stopped.value.reason


def input_events(flow, action):
    return [fields for name, fields in flow.runtime.deps.logger.events
            if name == "input" and fields.get("action") == action]


def test_visible_home_enters_without_wake(entry):
    flow, game = entry
    assert execute_reason(flow) is StopReason.MANUAL_STOP
    assert game.actions == ["home_battle", "exploration_card"]
    assert not input_events(flow, "wake_main_screen_startup")


@pytest.mark.parametrize("wake_on", [None, 1, 2])
def test_sleep_wakes_at_most_twice_and_requires_fresh_home_evidence(entry, monkeypatch, wake_on):
    flow, game = entry
    visible, click = game._visible, game.click
    sleeping = True
    wake_captures = []
    entry_captures = []

    def wake(window, point):
        nonlocal sleeping
        if point == game.config.points["main_screen_wake"]:
            game.actions.append("wake")
            wake_captures.append(game.captures)
            sleeping = len(wake_captures) != wake_on
            return
        if game.state == "home":
            entry_captures.append(game.captures)
        click(window, point)

    monkeypatch.setattr(game, "_visible", lambda: [] if sleeping else visible())
    monkeypatch.setattr(game, "click", wake)
    reason = execute_reason(flow)
    expected_attempts = wake_on or 2
    assert wake_captures[0] == 1 and len(wake_captures) == expected_attempts
    assert [event['attempt'] for event in input_events(flow, "wake_main_screen_startup")] == list(range(1, expected_attempts+1))
    if wake_on:
        assert reason is StopReason.MANUAL_STOP
        assert entry_captures == [wake_captures[-1]+3]
        assert game.actions == ["wake"] * wake_on + ["home_battle", "exploration_card"]
    else:
        assert reason is StopReason.RECOGNITION_TIMEOUT
        assert game.actions == ["wake", "wake"]
        assert 13 <= game.clock.now < 13.3


@pytest.mark.parametrize("misses", [1, 2])
def test_entry_retries_only_confirmed_home_and_stops_at_two(entry, monkeypatch, misses):
    flow, game = entry
    click = game.click
    attempts = []

    def delayed_click(window, point):
        if game.points.get(point) == "home_battle":
            attempts.append(game.clock.now)
            if len(attempts) <= misses:
                game.actions.append("home_battle")
                return
        click(window, point)

    monkeypatch.setattr(game, "click", delayed_click)
    reason = execute_reason(flow)
    assert len(attempts) == min(misses + 1, 2)
    assert all(5 <= b - a < 6 for a, b in zip(attempts, attempts[1:]))
    assert not input_events(flow, "wake_main_screen_startup")
    if misses == 2:
        assert reason is StopReason.RECOGNITION_TIMEOUT
        assert game.actions == ["home_battle"] * 2
    else:
        assert reason is StopReason.MANUAL_STOP
        assert game.actions == ["home_battle", "home_battle", "exploration_card"]


def test_late_destination_is_confirmed_without_reclick(entry, monkeypatch):
    flow, game = entry
    control = game.control
    entered_at = None

    def delayed_title(frame, key, region=None):
        nonlocal entered_at
        if frame["state"] == "battle_menu":
            if entered_at is None:
                entered_at = game.clock.now
            if key == "battle_title" and game.clock.now - entered_at < 5.1:
                return None
        return control(frame, key, region)

    monkeypatch.setattr(game, "control", delayed_title)
    assert execute_reason(flow) is StopReason.MANUAL_STOP
    assert game.actions == ["home_battle", "exploration_card"]
    assert game.clock.now >= 5.1


def test_unknown_page_after_entry_does_not_authorize_retry(entry, monkeypatch):
    flow, game = entry
    visible = game._visible
    monkeypatch.setattr(game, "_visible", lambda: [] if game.state == "battle_menu" else visible())
    assert execute_reason(flow) is StopReason.RECOGNITION_TIMEOUT
    assert game.actions == ["home_battle"]


@pytest.mark.parametrize("recovery_at", ["startup", "entry"])
def test_network_recovery_pauses_entry_budget_and_never_wakes_or_reclicks(entry, recovery_at):
    flow, game = entry
    if recovery_at == "startup":
        game.on_capture = lambda g: setattr(g, "network_remaining", 4) if g.captures == 3 else None
    else:
        game.network_after = "home_battle"
    assert execute_reason(flow) is StopReason.MANUAL_STOP
    assert game.actions.count("home_battle") == 1
    assert game.actions.count("network_retry") == 4
    assert not input_events(flow, "wake_main_screen_startup")
    assert game.clock.now >= 40
    assert flow.runtime.active_monotonic() < 5


@pytest.mark.parametrize("stop", ["manual_stop", "window"])
def test_startup_guard_blocks_wake_and_entry(entry, monkeypatch, stop):
    flow, game = entry
    control = game.control

    def guarded_control(frame, key, region=None):
        if key == "home_battle":
            if stop == "manual_stop":
                flow.runtime.control.request(StopReason.MANUAL_STOP)
            elif stop == "window":
                windows = flow.runtime.deps.windows
                bounds = windows.state.client_bounds
                windows.state = replace(windows.state, client_bounds=replace(bounds, x=bounds.x + 1))
            return None
        return control(frame, key, region)

    if stop == "failed":
        game.state = "failed"
    monkeypatch.setattr(game, "control", guarded_control)
    assert execute_reason(flow) is {"manual_stop": StopReason.MANUAL_STOP,
                                   "window": StopReason.WINDOW_ABNORMAL,
                                   "failed": StopReason.CHAOS_FAILED}[stop]
    assert game.actions == []
