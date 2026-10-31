"""Exercise real feature wiring with dropped inputs, without game/window access."""
import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config
from tests.helpers import FakeClock, FakeInput, ScriptedVision, make_config, make_dependencies, match
from e7auto.features.shop.flow import ShopFlow
from e7auto.features.shop.contracts import PurchaseOutcome


class DroppedGame(ChaosGame):
    def __init__(self, config, dropped, *, always=False):
        super().__init__(config)
        self.dropped, self.always, self.ignored = dropped, always, 0
        self.click_times = []

    def click(self, window, point):
        key = self.points.get(point)
        if key == self.dropped:
            self.click_times.append(self.clock.now)
            if self.always or not self.ignored:
                self.ignored += 1
                self.actions.append(key)
                return
        super().click(window, point)


@pytest.fixture(scope="module")
def config():
    return chaos_config()


def setup(config, dropped, **kwargs):
    game = DroppedGame(config, dropped, **kwargs)
    flow = ChaosFlow(config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run("retry", 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState.for_lineup(config.chaos.lineup)
    return flow, game


@pytest.mark.parametrize("action", ["contract", "ticket_thief", "filter_dark", "filter_icon_dark", "hero_twins"])
def test_initial_reversible_selection_recovers_one_lost_click(config, action):
    flow, game = setup(config, action)
    game.state = "hall"
    flow._start_round()
    assert game.state == "map" and game.ignored == 1
    assert game.click_times[1]-game.click_times[0] >= 3
    assert len([e for e, f in flow.runtime.deps.logger.events if e == "click_retry"
                and f["action"].startswith("chaos_")]) == 1


def test_second_contract_miss_stops_without_confirmation(config):
    flow, game = setup(config, "contract", always=True)
    game.state = "hall"
    with pytest.raises(StopExecution):
        flow._start_round()
    assert game.actions.count("contract") == 2
    assert "supply_confirm" not in game.actions


@pytest.mark.parametrize("action", ["supply_confirm", "recruit_confirm", "start_exploration", "recruit_enter"])
def test_initial_final_submissions_remain_single_shot(config, action):
    flow, game = setup(config, action, always=True)
    game.state = "hall"
    with pytest.raises(StopExecution):
        flow._start_round()
    assert game.actions.count(action) == 1


@pytest.mark.parametrize("action", ["camp_rank", "hero_xiaochun", "rank_close", "camp_leave"])
def test_camp_safe_actions_retry_without_duplicate_rank_submission(config, action):
    flow, game = setup(config, action)
    game.state, game.kind, flow.state.node_kind = "camp", "rest", "rest"
    flow._camp()
    assert game.state == "map" and game.ignored == 1
    assert game.actions.count("rank_confirm") == 1
    assert game.click_times[1]-game.click_times[0] >= 3


@pytest.mark.parametrize("action", ["investment_entry", "shop_leave"])
def test_chaos_shop_safe_actions_retry_but_purchase_is_once(config, action):
    flow, game = setup(config, action)
    game.state, game.kind, flow.state.node_kind = "shop", "shop", "shop"
    flow._shop()
    assert game.state == "map" and game.ignored == 1
    assert game.actions.count("purchase_confirm") == 1


def test_contract_response_latches_before_confirm_button_is_ready(config):
    flow, game = setup(config, "unused")
    original = game.control
    selected_at = None
    def control(frame, key, region=None):
        nonlocal selected_at
        if frame["selected"] and frame["state"] == "contract":
            if selected_at is None:
                selected_at = game.clock.now
            if key == "supply_confirm" and game.clock.now-selected_at < 4:
                return None
        return original(frame, key, region)
    game.control = control
    game.state = "hall"
    flow._start_round()
    assert game.actions.count("contract") == game.actions.count("supply_confirm") == 1


@pytest.mark.parametrize("action", ["chaos_open_stage_selector", "chaos_stage_01", "chaos_stage_confirm"])
def test_stage_navigation_retries_original_target(config, action):
    flow, game = setup(config, "unused")
    game.state, game.current_stage, game.chosen_stage, game.stage_scrolled = "hall", 2, 2, False
    original, attempts = flow.runtime.click, []
    def click(name, point, **fields):
        if name == action:
            attempts.append(game.clock.now)
            if len(attempts) == 1:
                return
        original(name, point, **fields)
    flow.runtime.click = click
    flow.stages.ensure()
    assert game.state == "hall" and game.current_stage == 1
    assert len(attempts) == 2 and attempts[1]-attempts[0] >= 3


def test_stop_in_contract_retry_log_prevents_second_input(config):
    flow, game = setup(config, "contract", always=True)
    original = flow.runtime.deps.logger.event
    def log(name, **fields):
        original(name, **fields)
        if name == "click_retry":
            flow.runtime.control.request(StopReason.MANUAL_STOP)
    flow.runtime.deps.logger.event = log
    game.state = "hall"
    with pytest.raises(StopExecution) as exc:
        flow._start_round()
    assert exc.value.reason is StopReason.MANUAL_STOP
    assert game.actions.count("contract") == 1


@pytest.mark.parametrize("source_state", ["same", "different_item", "purchased", "missing"])
def test_shop_open_retries_only_original_unpurchased_slot(source_state):
    from dataclasses import replace
    selected, clock, clicks = match("wood"), FakeClock(), []
    config = make_config()
    class Inputs(FakeInput):
        def click(self, window, point):
            super().click(window, point)
            clicks.append((clock.now, point))
    class Vision(ScriptedVision):
        def confirm_dialog(self, frame, target_id):
            return self._observation("confirm") if sum(p == selected.buy_point for _, p in clicks) == 2 else None
        def scan_inventory(self, *args, **kwargs):
            return {"same": (selected,), "different_item": (match("ore"),),
                    "purchased": (replace(selected, is_purchased=True),), "missing": ()}[source_state]
    deps, *_ = make_dependencies(Vision(purchase=[PurchaseOutcome.SUCCESS]), inputs=Inputs(), clock=clock)
    snapshot = RuntimeSnapshot.initial("shop-retry", tuple((t.target_id,t.display_name) for t in config.targets), 0)
    flow = ShopFlow(config, deps, StopController(), SnapshotPublisher(snapshot, lambda _: None))
    flow.runtime.prepare()
    if source_state == "same":
        flow.inventory.purchase(selected)
        assert [p for _, p in clicks] == [selected.buy_point, selected.buy_point, config.points["confirm_button"]]
        assert clicks[1][0]-clicks[0][0] >= 3
    else:
        with pytest.raises(StopExecution):
            flow.inventory.purchase(selected)
        assert len(clicks) == 1


def test_shop_exit_recovers_one_lost_click():
    clock, config, exits = FakeClock(), make_config(), []
    class Inputs(FakeInput):
        def click(self, window, point):
            super().click(window, point)
            assert point == config.points["shop_exit_button"]
            exits.append(clock.now)
    class Vision(ScriptedVision):
        def main_shop_icon(self, frame):
            return self._observation("main") if len(exits) == 2 else None
    deps, *_ = make_dependencies(Vision(), inputs=Inputs(), clock=clock)
    flow = ShopFlow(config, deps, StopController(),
                    SnapshotPublisher(RuntimeSnapshot.initial("exit", (), 0), lambda _: None))
    flow.runtime.prepare()
    flow._exit_store()
    assert len(exits) == 2 and exits[1]-exits[0] >= 3
