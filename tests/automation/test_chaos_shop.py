"""Shop-only branches, purchase guards and local completion evidence."""
from dataclasses import replace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.types import Point, Rect
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


class ShopGame(ChaosGame):
    def __init__(self, *, investment_dark=False, health="available", wrong_item=False,
                 result="correct", duplicate=False, network=None):
        config = chaos_config()
        config = replace(config, chaos=replace(config.chaos, timing=dict(
            config.chaos.timing, page_timeout_ms=1000)))
        super().__init__(config)
        self.state = "shop"; self.kind = "shop"; self.node_index = 4
        self.investment_dark = investment_dark; self.health = health
        self.wrong_item = wrong_item; self.result = result; self.duplicate = duplicate
        self.network = network; self.purchased = []

    def _visible(self):
        if self.state == "unknown":
            return []
        if self.state == "shop" and self.investment_dark:
            return ["investment_insufficient_item", "shop_leave"]
        if self.state == "shop_done":
            health = [] if self.health == "unknown" else ["health_restore_unavailable"] if self.health == "dim" else ["health_restore", "shop_health_entry"]
            return ["investment_done", "shop_leave"] + health
        if self.state == "health_purchase":
            return ["purchase_title", "purchase_confirm", "purchase_item" if self.wrong_item else "purchase_health_restore"]
        if self.state == "health_done":
            return ["shop_leave", "health_restore_done"] if self.result != "missing" else ["shop_leave"]
        return super()._visible()

    def _hit(self, key, frame):
        hit = super()._hit(key, frame)
        if key == "shop_health_entry":r = Rect(1080, 965, 280, 84)
        elif key == "health_restore_done":r = Rect(1425, 510, 277, 83) if self.result == "neighbour" else Rect(1080, 970, 277, 83)
        else:return hit
        hit = replace(hit, bounds=r, anchor=Point(r.x+r.width//2, r.y+r.height//2))
        self.points[hit.anchor] = key
        return hit

    def controls(self, frame, key, region=None):
        hits = super().controls(frame, key, region)
        if self.duplicate and key == "shop_health_entry" and hits:
            return hits + (replace(hits[0], anchor=Point(1240, 1007)),)
        return hits

    def click(self, window, point):
        if self.network_remaining:return super().click(window, point)
        key = self.points[point]
        if key == "shop_health_entry":
            self.actions.append(key); self.state = "health_purchase"
        elif key == "purchase_confirm" and self.state == "health_purchase":
            self.actions.append(key); self.purchased.append("health"); self.state = "health_done"
            if self.network == "health":self.network_remaining = 2
        else:
            if key == "purchase_confirm":self.purchased.append("investment")
            super().click(window, point)
            if key == "purchase_confirm" and self.network == "investment":self.network_remaining = 2


def shop_flow(game):
    control = StopController()
    publisher = SnapshotPublisher(RuntimeSnapshot.chaos_run("shop", 1), lambda _: None)
    flow = ChaosFlow(game.config, game.dependencies(), control, publisher)
    flow.runtime.prepare(); flow.state = RoundState()
    return flow


def run_shop(game):
    flow = shop_flow(game)
    flow._shop()
    return flow


@pytest.mark.parametrize("network", [None, "investment", "health"])
def test_buys_investment_then_health_once_and_returns_to_same_floor(network):
    game = ShopGame(network=network)
    flow = run_shop(game)
    assert game.purchased == ["investment", "health"]
    assert game.actions.count("purchase_confirm") == 2
    assert game.actions[-2:] == ["shop_leave", "shop_leave_confirm"]
    assert game.state == "map" and game.floor == flow.state.floor == 1


def test_dim_investment_leaves_without_any_purchase_or_health_scan():
    game = ShopGame(investment_dark=True)
    original = game.control
    def observe(frame, key, region=None):
        assert key not in ("health_restore", "health_restore_unavailable")
        return original(frame, key, region)
    game.control = observe
    run_shop(game)
    assert game.actions == ["shop_leave", "shop_leave_confirm"]


def test_dim_health_skips_purchase_after_investment():
    game = ShopGame(health="dim")
    run_shop(game)
    assert game.purchased == ["investment"]
    assert "shop_health_entry" not in game.actions
    assert game.state == "map"


def test_neighbour_completed_card_does_not_confirm_investment(monkeypatch):
    game = ShopGame()
    original = game._hit
    def neighbour(key, frame):
        hit = original(key, frame)
        return replace(hit, bounds=Rect(1080, 970, 277, 83)) if key == "investment_done" else hit
    monkeypatch.setattr(game, "_hit", neighbour)
    with pytest.raises(StopExecution):run_shop(game)
    assert game.purchased == ["investment"]
    assert "shop_health_entry" not in game.actions and "shop_leave" not in game.actions


def test_wrong_investment_popup_does_not_submit(monkeypatch):
    game = ShopGame()
    original = game._visible
    monkeypatch.setattr(game, "_visible", lambda: ["purchase_title", "purchase_health_restore", "purchase_confirm"]
                        if game.state == "purchase" else original())
    with pytest.raises(StopExecution):run_shop(game)
    assert game.purchased == [] and game.actions == ["investment_entry"]


@pytest.mark.parametrize("case", ["unknown", "duplicate", "wrong_item", "missing", "neighbour"])
def test_uncertain_health_state_or_result_stops_without_leaving(case):
    game = ShopGame(health="unknown" if case == "unknown" else "available",
                    duplicate=case == "duplicate", wrong_item=case == "wrong_item",
                    result=case if case in ("missing", "neighbour") else "correct")
    with pytest.raises(StopExecution) as stopped:run_shop(game)
    assert stopped.value.reason == StopReason.RECOGNITION_TIMEOUT
    assert "shop_leave" not in game.actions
    assert game.purchased == (["investment", "health"] if case in ("missing", "neighbour") else ["investment"])


@pytest.mark.parametrize('floor', [1, 2, 3])
def test_direct_map_needs_three_frames_and_no_confirmation_click(monkeypatch, floor):
    game = ShopGame(health='dim')
    flow = shop_flow(game)
    flow.state.floor = game.floor = floor
    game.on_click = lambda g, key: g._map_next() if key == 'shop_leave' else None
    samples = []
    original = game.map_view
    def observe(frame):
        view = original(frame)
        if view is not None:
            samples.append(frame)
        return view
    monkeypatch.setattr(game, 'map_view', observe)
    flow._shop()
    assert game.purchased == ['investment']
    assert game.actions.count('shop_leave') == 1 and 'shop_leave_confirm' not in game.actions
    assert len(samples) == 3 and len({id(frame) for frame in samples}) == 3
    before = game.captures
    assert flow._map_for_selection(floor).floor == floor
    assert game.captures == before  # Reuse the already-confirmed map.


def test_prompt_and_button_are_jointly_stable_and_use_last_anchor(monkeypatch):
    game = ShopGame(investment_dark=True)
    original, original_map = game.control, game.map_view
    frames, anchors = [], []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if key == 'shop_leave_confirm' and hit is not None:
            frames.append(frame)
            hit = replace(hit, anchor=Point(hit.anchor.x + len(frames), hit.anchor.y))
            anchors.append(hit.anchor)
            game.points[hit.anchor] = key
        return hit
    def map_view(frame):
        assert frame['state'] != 'shop_prompt', 'Do not inspect the map behind a prompt'
        return original_map(frame)
    clicks = []
    original_click = game.click
    def click(window, point):
        clicks.append(point)
        original_click(window, point)
    monkeypatch.setattr(game, 'control', control)
    monkeypatch.setattr(game, 'map_view', map_view)
    monkeypatch.setattr(game, 'click', click)
    run_shop(game)
    assert len(frames) == 3 and len({id(frame) for frame in frames}) == 3
    assert clicks[-1] == anchors[-1]
    assert game.actions == ['shop_leave', 'shop_leave_confirm']


@pytest.mark.parametrize('sequence', [
    ['map', 'map', 'shop_prompt', 'shop_prompt', 'map', 'map', 'map'],
    ['map', 'map', 'unknown', 'map', 'map', 'map'],
])
def test_switch_or_miss_resets_destination_stability(monkeypatch, sequence):
    game = ShopGame(investment_dark=True)
    original = game.control
    samples = []
    def control(frame, key, region=None):
        if key == 'shop_leave_prompt':
            state = sequence[len(samples)]
            samples.append(state)
            game.state = frame['state'] = state
            frame['visible'] = frozenset(('shop_leave_prompt', 'shop_leave_confirm') if state == 'shop_prompt' else ())
        return original(frame, key, region)
    monkeypatch.setattr(game, 'control', control)
    run_shop(game)
    assert samples == sequence
    assert game.actions == ['shop_leave']


def test_prompt_without_button_does_not_accept_underlying_map(monkeypatch):
    game = ShopGame(investment_dark=True)
    original = game.control
    monkeypatch.setattr(game, 'control', lambda frame, key, region=None:
                        None if key == 'shop_leave_confirm' else original(frame, key, region))
    monkeypatch.setattr(game, 'map_view', lambda frame: pytest.fail('Prompt must block map detection'))
    with pytest.raises(StopExecution) as error:
        run_shop(game)
    assert error.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert game.actions == ['shop_leave']


@pytest.mark.parametrize('after', ['shop_leave', 'shop_leave_confirm'])
def test_network_recovery_never_replays_leave_or_confirm(after):
    game = ShopGame(investment_dark=True)
    game.network_after = after
    flow = run_shop(game)
    assert game.actions.count('shop_leave') == game.actions.count('shop_leave_confirm') == 1
    assert game.actions.count('network_retry') == 4
    assert game.floor == flow.state.floor == 1 and game.state == 'map'


def test_recovery_discards_partial_map_stability(monkeypatch):
    game = ShopGame(investment_dark=True)
    flow = shop_flow(game)
    game.on_click = lambda g, key: g._map_next() if key == 'shop_leave' else None
    original = game.map_view
    generations = []
    def map_view(frame):
        view = original(frame)
        if view is not None:
            generations.append(flow.runtime.network_recovery_generation)
            if len(generations) == 2:
                game.network_remaining = 1
        return view
    monkeypatch.setattr(game, 'map_view', map_view)
    flow._shop()
    assert generations == [0, 0, 1, 1, 1]
    assert game.actions.count('shop_leave') == 1 and 'shop_leave_confirm' not in game.actions


@pytest.mark.parametrize('destination', ['wrong_floor', 'unknown', 'persistent_prompt'])
def test_invalid_exit_result_times_out_without_repeated_input(destination):
    game = ShopGame(investment_dark=True)
    def click(g, key):
        if key == 'shop_leave' and destination != 'persistent_prompt':
            g.state = 'map' if destination == 'wrong_floor' else 'unknown'
            g.floor = 2
        if key == 'shop_leave_confirm':
            g.state = 'shop_prompt'
    game.on_click = click
    with pytest.raises(StopExecution):
        run_shop(game)
    expected = ['shop_leave', 'shop_leave_confirm'] if destination == 'persistent_prompt' else ['shop_leave']
    assert game.actions == expected


def test_manual_stop_during_prompt_confirmation_prevents_confirmation_input(monkeypatch):
    game = ShopGame(investment_dark=True)
    flow = shop_flow(game)
    original = game.control
    samples = []
    def control(frame, key, region=None):
        hit = original(frame, key, region)
        if key == 'shop_leave_confirm' and hit is not None:
            samples.append(frame)
            if len(samples) == 2:
                flow.runtime.control.request(StopReason.MANUAL_STOP)
        return hit
    monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution) as error:
        flow._shop()
    assert error.value.reason is StopReason.MANUAL_STOP
    assert game.actions == ['shop_leave']
