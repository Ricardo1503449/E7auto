from __future__ import annotations

from dataclasses import replace

import pytest

from e7auto.automation import AutomationSession, SnapshotPublisher, StopController
from e7auto.automation.penguin import PenguinEngine
from e7auto.config import Point, Rect
from e7auto.domain import OverlayActivityStatus, RuntimeSnapshot, StopReason
from e7auto.penguin_vision import CONTROLS, PenguinDialog
from e7auto.vision_types import Observation
from tests.helpers import FakeClock, FakeHotkeys, FakeInput, make_config, make_dependencies


class PenguinGame:
    """Game-side state changes occur only when a matching input is dispatched."""
    def __init__(self, *, price=102, maximum=5100, empty_after=None,
                 missing_result=False, missing_close=False, uncertain=False):
        self.state = "home"
        self.price = price
        self.maximum = maximum
        self.empty_after = empty_after
        self.completed = 0
        self.missing_result = missing_result
        self.missing_close = missing_close
        self.uncertain = uncertain
        self.actions = []
        self.on_click = None
        self.network = False
        self.points = {name: Point(index*3+3, 20) for index, name in enumerate(CONTROLS)}

    def obs(self, name):
        return Observation(name, 1.0, Rect(0, 0, 3, 3), self.points[name])

    def control(self, frame, name):
        empty = self.empty_after is not None and self.completed >= self.empty_after
        visible = {
            "home": {"sanctuary_entry"},
            "sanctuary": {"forest_entry", "sanctuary_back"},
            "forest": {"growth_altar", "forest_back"},
            "altar": {"altar_close", "purchase_currency" if empty else "penguin_buy_102"},
            "dialog": {"quantity_max", "purchase_cancel"},
            "result": {"penguin_complete"} | (set() if self.missing_close else {"reward_close"}),
            "unknown": set(),
        }[self.state]
        return self.obs(name) if name in visible else None

    def dialog(self, frame):
        if self.state != "dialog":
            return None
        return PenguinDialog(None if self.uncertain else self.price,
                             self.obs("penguin_buy_5100"), self.obs("quantity_max"),
                             self.obs("purchase_cancel"), self.price == 5100)

    def network_connection_error(self, frame):
        return self.obs("sanctuary_entry") if self.network else None

    def network_retry(self, frame):
        return None

    def sky_stone_balance(self, frame):
        pytest.fail("Penguin flow must never read a balance")

    def click(self, point):
        name = next(name for name, value in self.points.items() if value == point)
        self.actions.append(name)
        expected = {
            "sanctuary_entry": ("home", "sanctuary"),
            "forest_entry": ("sanctuary", "forest"),
            "growth_altar": ("forest", "altar"),
            "penguin_buy_102": ("altar", "dialog"),
            "reward_close": ("result", "altar"),
            "purchase_cancel": ("dialog", "altar"),
            "altar_close": ("altar", "forest"),
            "forest_back": ("forest", "sanctuary"),
            "sanctuary_back": ("sanctuary", "home"),
        }
        if name == "quantity_max":
            assert self.state == "dialog"
            self.price = self.maximum
        elif name == "penguin_buy_5100":
            assert self.state == "dialog" and self.price == 5100
            self.completed += 1
            self.state = "unknown" if self.missing_result else "result"
        else:
            before, after = expected[name]
            assert self.state == before
            self.state = after
        if self.on_click:
            self.on_click(name)


def run(game, limit=2, stop_after=None, config=None, clock=None, hotkeys=None):
    class Inputs(FakeInput):
        def click(self, window, point):
            super().click(window, point)
            game.click(point)

    deps, *_ = make_dependencies(game, inputs=Inputs(), clock=clock)
    hotkeys = hotkeys or FakeHotkeys()
    snapshots = []
    if stop_after:
        game.on_click = lambda name: hotkeys.callback() if len(game.actions) == stop_after else None
    final = AutomationSession(config or make_config(), deps, hotkeys, snapshots.append).run_penguins(limit, "penguin-test")
    assert deps.capture.closed
    assert hotkeys.unregistered == 1
    assert sum(s.is_final for s in snapshots) == 1
    assert snapshots[-1] == final
    return final, snapshots, game


def test_two_batches_only_set_max_once_and_return_home():
    final, snapshots, game = run(PenguinGame())
    assert final.stop_reason == StopReason.PENGUIN_LIMIT_COMPLETE
    assert final.purchases_completed == final.purchase_limit == 2
    assert final.targets == () and final.refresh_spent == 0
    assert game.actions.count("quantity_max") == 1
    assert game.actions.count("penguin_buy_5100") == 2
    assert game.actions[-3:] == ["altar_close", "forest_back", "sanctuary_back"]
    assert game.state == "home"


@pytest.mark.parametrize("limit", [0, 1, 3])
def test_exact_limit_never_opens_extra_purchase(limit):
    final, _, game = run(PenguinGame(price=5100), limit)
    assert final.purchases_completed == limit
    assert game.actions.count("penguin_buy_102") == limit
    assert "quantity_max" not in game.actions
    assert game.state == "home"


@pytest.mark.parametrize("after", [0, 1])
def test_purchase_currency_is_normal_completion_without_clicking_it(after):
    final, _, game = run(PenguinGame(empty_after=after))
    assert final.stop_reason == StopReason.PENGUIN_FUNDS_COMPLETE
    assert final.purchases_completed == after
    assert "purchase_currency" not in game.actions
    assert game.state == "home"


@pytest.mark.parametrize("maximum", [102, 4998])
def test_verified_lower_maximum_cancels_dialog_and_returns_home(maximum):
    final, _, game = run(PenguinGame(maximum=maximum))
    assert final.stop_reason == StopReason.PENGUIN_FUNDS_COMPLETE
    assert final.purchases_completed == 0
    assert "penguin_buy_5100" not in game.actions
    assert game.actions[-4:] == ["purchase_cancel", "altar_close", "forest_back", "sanctuary_back"]


def test_unknown_price_is_abnormal_and_does_not_return_or_buy():
    final, _, game = run(PenguinGame(uncertain=True))
    assert final.stop_reason == StopReason.RECOGNITION_TIMEOUT
    assert game.actions[-1] == "penguin_buy_102"
    assert final.purchases_completed == 0


def test_purchase_click_is_never_retried_if_result_is_missing():
    final, _, game = run(PenguinGame(missing_result=True))
    assert final.stop_reason == StopReason.PURCHASE_RESULT_AMBIGUOUS
    assert game.actions.count("penguin_buy_5100") == 1
    assert game.actions[-1] == "penguin_buy_5100"
    assert final.purchases_completed == 0


def test_success_count_survives_close_failure_without_recounting():
    final, _, game = run(PenguinGame(missing_close=True))
    assert final.stop_reason == StopReason.RECOGNITION_TIMEOUT
    assert final.purchases_completed == 1
    assert game.actions.count("penguin_buy_5100") == 1


@pytest.mark.parametrize("stop_after", range(1, 14))
def test_f5_blocks_all_later_inputs_including_normal_return(stop_after):
    # Two complete batches contain 13 inputs including first max and final return.
    final, _, game = run(PenguinGame(), stop_after=stop_after)
    assert final.stop_reason == StopReason.MANUAL_F5
    assert len(game.actions) == stop_after


def test_network_recovery_with_unknown_purchase_result_does_not_resubmit():
    clock = FakeClock()
    game = ReconnectingPenguinGame(clock, ["penguin_buy_5100"], after_recovery="dialog")
    final, _, game = run(game, clock=clock)
    assert final.stop_reason == StopReason.PURCHASE_RESULT_AMBIGUOUS
    assert game.actions.count("penguin_buy_5100") == 1
    assert game.actions[-1] == "network_retry"
    assert final.purchases_completed == 0


def test_quantity_memory_reset_sets_max_again_in_next_batch():
    game = PenguinGame()
    game.on_click = lambda name: setattr(game, "price", 102) if name == "reward_close" else None
    final, _, game = run(game)
    assert final.stop_reason == StopReason.PENGUIN_LIMIT_COMPLETE
    assert final.purchases_completed == 2
    assert game.actions.count("quantity_max") == 2


def test_price_becomes_unknown_after_max_is_abnormal():
    game = PenguinGame()
    game.on_click = lambda name: setattr(game, "uncertain", True) if name == "quantity_max" else None
    final, _, game = run(game)
    assert final.stop_reason == StopReason.RECOGNITION_TIMEOUT
    assert game.actions[-1] == "quantity_max"
    assert final.purchases_completed == 0


def test_delayed_max_does_not_treat_old_102_as_insufficient():
    class SlowMax(PenguinGame):
        pending_reads = 0

        def click(self, point):
            super().click(point)
            if self.actions[-1] == "quantity_max":
                self.price = 102
                self.pending_reads = 20

        def dialog(self, frame):
            if self.pending_reads:
                self.pending_reads -= 1
                if not self.pending_reads:
                    self.price = 5100
            return super().dialog(frame)

    final, _, game = run(SlowMax(), 1)
    assert final.stop_reason == StopReason.PENGUIN_LIMIT_COMPLETE
    assert final.purchases_completed == 1


def test_return_failure_preserves_purchases_and_sends_no_blind_back_clicks():
    game = PenguinGame()
    game.on_click = lambda name: setattr(game, "state", "unknown") if name == "altar_close" else None
    final, _, game = run(game, 1)
    assert final.stop_reason == StopReason.RECOGNITION_TIMEOUT
    assert final.purchases_completed == 1
    assert game.actions[-1] == "altar_close"


def test_finalized_penguin_snapshot_cannot_be_recounted():
    final, _, _ = run(PenguinGame(), 1)
    assert final.with_penguin_purchase() is final


@pytest.mark.parametrize("limit", [-1, True, 1.5, 2_147_483_648])
def test_invalid_limit_rejected_before_session_inputs(limit):
    deps, *_ = make_dependencies(PenguinGame())
    with pytest.raises(ValueError):
        AutomationSession(make_config(), deps, FakeHotkeys(), lambda _: None).run_penguins(limit)
    assert deps.inputs.actions == []


class DroppedSanctuaryClicks(PenguinGame):
    def __init__(self, drops):
        super().__init__()
        self.drops = drops
        self.main_reads_since_click = 0
        self.reads_at_entry_click = []

    def control(self, frame, name):
        if self.state == "home" and name == "sanctuary_entry":
            self.main_reads_since_click += 1
        return super().control(frame, name)

    def click(self, point):
        if point == self.points["sanctuary_entry"]:
            self.reads_at_entry_click.append(self.main_reads_since_click)
            self.main_reads_since_click = 0
            if self.drops:
                self.drops -= 1
                self.actions.append("sanctuary_entry")
                if self.on_click:
                    self.on_click("sanctuary_entry")
                return
        super().click(point)


@pytest.mark.parametrize("drops", [0, 1, 2])
def test_sanctuary_retries_only_after_fresh_stable_main_confirmation(drops):
    game = DroppedSanctuaryClicks(drops)
    final, _, game = run(game, 1, config=make_config(stable_frames=3))
    assert final.stop_reason == StopReason.PENGUIN_LIMIT_COMPLETE
    assert game.actions.count("sanctuary_entry") == drops + 1
    # Exactly one three-frame recognition before each entry click, not two.
    assert game.reads_at_entry_click == [3] * (drops + 1)


def test_sanctuary_stops_after_three_confirmed_dropped_clicks():
    final, _, game = run(DroppedSanctuaryClicks(99), 1, config=make_config(stable_frames=3))
    assert final.stop_reason == StopReason.RECOGNITION_TIMEOUT
    assert game.actions == ["sanctuary_entry"] * 3
    assert final.purchases_completed == 0


def test_sanctuary_unknown_page_after_click_does_not_authorize_retry():
    game = PenguinGame()
    game.on_click = lambda name: setattr(game, "state", "unknown") if name == "sanctuary_entry" else None
    final, _, game = run(game, 1)
    assert final.stop_reason == StopReason.RECOGNITION_TIMEOUT
    assert game.actions == ["sanctuary_entry"]


def test_sanctuary_arrival_retains_independent_forest_preclick_recognition():
    class DelayedSanctuary(PenguinGame):
        forest_reads = 0
        reads_when_clicked = None

        def control(self, frame, name):
            if self.state == "sanctuary" and name == "forest_entry":
                self.forest_reads += 1
                # A short animation precedes the destination's appearance.
                if self.forest_reads <= 5:
                    return None
            return super().control(frame, name)

        def click(self, point):
            if point == self.points["forest_entry"]:
                self.reads_when_clicked = self.forest_reads
            super().click(point)

    final, _, game = run(DelayedSanctuary(), 1, config=make_config(stable_frames=3))
    assert final.stop_reason == StopReason.PENGUIN_LIMIT_COMPLETE
    assert game.actions.count("sanctuary_entry") == 1
    # Keep the independent three-frame pre-click check for the forest entry.
    # Only the main-screen sanctuary icon skips its former duplicate check.
    assert game.reads_when_clicked == 11  # five missing + three arrival + three pre-click


def test_f5_during_sanctuary_retry_prevents_third_click():
    final, _, game = run(DroppedSanctuaryClicks(99), 1, stop_after=2)
    assert final.stop_reason == StopReason.MANUAL_F5
    assert game.actions == ["sanctuary_entry"] * 2


class AnimatedSanctuary(PenguinGame):
    def __init__(self, clock, delay):
        super().__init__()
        self.clock = clock
        self.delay = delay
        self.sanctuary_clicked_at = None
        self.forest_clicked_at = None

    def control(self, frame, name):
        if (self.state == "sanctuary" and name == "forest_entry"
                and self.clock.monotonic() < self.sanctuary_clicked_at + self.delay):
            return None
        return super().control(frame, name)

    def click(self, point):
        if point == self.points["sanctuary_entry"]:
            self.sanctuary_clicked_at = self.clock.monotonic()
        if point == self.points["forest_entry"]:
            self.forest_clicked_at = self.clock.monotonic()
        super().click(point)


@pytest.mark.parametrize("delay", [0, 8, 9, 11, 19, 21])
def test_sanctuary_long_animation_wait_and_recheck_do_not_repeat_successful_click(delay):
    clock = FakeClock()
    game = AnimatedSanctuary(clock, delay)
    config = make_config(stable_frames=3)
    config = replace(config, timing=replace(
        config.timing, poll_interval_ms=100, entry_timeout_ms=5000,
        dialog_timeout_ms=3000, purchase_result_timeout_ms=5000,
    ))
    final, _, game = run(game, 1, config=config, clock=clock)
    assert game.actions.count("sanctuary_entry") == 1
    if delay < 20:
        assert final.stop_reason == StopReason.PENGUIN_LIMIT_COMPLETE
        elapsed = game.forest_clicked_at - game.sanctuary_clicked_at
        assert delay <= elapsed < delay + 1.0  # no fixed 10-second sleep on fast entry
    else:
        assert final.stop_reason == StopReason.RECOGNITION_TIMEOUT
        assert game.actions == ["sanctuary_entry"]
        elapsed = clock.monotonic() - game.sanctuary_clicked_at
        assert 20 <= elapsed < 20.5


def test_f5_interrupts_long_sanctuary_animation_wait():
    class InterruptingClock(FakeClock):
        callback = None

        def sleep(self, seconds):
            super().sleep(seconds)
            if self.now >= 2 and self.callback is not None:
                self.callback()
                self.callback = None

    clock = InterruptingClock()
    hotkeys = FakeHotkeys(on_register=lambda cb: setattr(clock, "callback", cb))
    game = AnimatedSanctuary(clock, 20)
    final, _, game = run(game, 1, clock=clock, hotkeys=hotkeys)
    assert final.stop_reason == StopReason.MANUAL_F5
    assert game.actions == ["sanctuary_entry"]
    assert clock.monotonic() < 2.1


class ReconnectingPenguinGame(PenguinGame):
    def __init__(self, clock, triggers, *, after_recovery=None, auto_clear=False):
        super().__init__()
        self.clock = clock
        self.triggers = list(triggers)
        self.after_recovery = after_recovery
        self.auto_clear = auto_clear
        self.ready_at = 0
        self.recovery_seconds = 12  # exceeds even the 10-second sanctuary budget

    def network_connection_error(self, frame):
        if self.network and self.auto_clear and self.clock.monotonic() >= self.ready_at:
            self.network = False
        return super().network_connection_error(frame)

    def network_retry(self, frame):
        if self.network and not self.auto_clear and self.clock.monotonic() >= self.ready_at:
            return self.obs("reward_close")
        return None

    def click(self, point):
        if self.network and point == Point(50, 40):
            assert self.clock.monotonic() >= self.ready_at
            self.actions.append("network_retry")
            self.network = False
            if self.after_recovery:
                self.state = self.after_recovery
            return
        super().click(point)
        if self.triggers and self.actions[-1] == self.triggers[0]:
            self.triggers.pop(0)
            self.network = True
            self.ready_at = self.clock.monotonic() + self.recovery_seconds


@pytest.mark.parametrize("trigger,status", [
    ("sanctuary_entry", OverlayActivityStatus.NAVIGATING),
    ("penguin_buy_102", OverlayActivityStatus.BUYING_PENGUINS),
    ("quantity_max", OverlayActivityStatus.BUYING_PENGUINS),
    ("penguin_buy_5100", OverlayActivityStatus.BUYING_PENGUINS),
    ("reward_close", OverlayActivityStatus.BUYING_PENGUINS),
    ("altar_close", OverlayActivityStatus.RETURNING),
])
def test_network_recovery_excludes_paused_time_and_restores_current_stage(trigger, status):
    clock = FakeClock()
    final, snapshots, game = run(ReconnectingPenguinGame(clock, [trigger]), 1, clock=clock)
    assert final.stop_reason == StopReason.PENGUIN_LIMIT_COMPLETE
    assert final.purchases_completed == 1
    assert game.actions.count("network_retry") == 1
    assert game.actions.count("penguin_buy_5100") == 1
    assert game.state == "home"
    reconnect_index = next(i for i,s in enumerate(snapshots) if s.overlay_status == OverlayActivityStatus.RECONNECTING)
    assert snapshots[reconnect_index-1].overlay_status == status
    assert snapshots[reconnect_index+1].overlay_status == status
    assert snapshots[reconnect_index].purchases_completed == snapshots[reconnect_index+1].purchases_completed
    assert clock.monotonic() >= 12


def test_multiple_recoveries_do_not_duplicate_purchase_counts():
    clock = FakeClock()
    final, _, game = run(ReconnectingPenguinGame(clock, ["penguin_buy_5100"]*2), clock=clock)
    assert final.purchases_completed == 2
    assert final.stop_reason == StopReason.PENGUIN_LIMIT_COMPLETE
    assert game.actions.count("network_retry") == 2
    assert game.actions.count("penguin_buy_5100") == 2


def test_network_prompt_clearing_itself_needs_no_retry_click():
    clock = FakeClock()
    game = ReconnectingPenguinGame(clock, ["penguin_buy_5100"], auto_clear=True)
    final, snapshots, game = run(game, 1, clock=clock)
    assert final.stop_reason == StopReason.PENGUIN_LIMIT_COMPLETE
    assert final.purchases_completed == 1
    assert "network_retry" not in game.actions
    assert any(s.overlay_status == OverlayActivityStatus.RECONNECTING for s in snapshots)


def test_f5_interrupts_network_recovery_before_retry_button_is_ready():
    class StopClock(FakeClock):
        callback = None

        def sleep(self, seconds):
            super().sleep(seconds)
            if self.now >= 1 and self.callback:
                self.callback()
                self.callback = None

    clock = StopClock()
    hotkeys = FakeHotkeys(on_register=lambda cb: setattr(clock, "callback", cb))
    game = ReconnectingPenguinGame(clock, ["penguin_buy_5100"])
    final, snapshots, game = run(game, 1, clock=clock, hotkeys=hotkeys)
    assert final.stop_reason == StopReason.MANUAL_F5
    assert final.purchases_completed == 0
    assert game.actions[-1] == "penguin_buy_5100"
    assert any(s.overlay_status == OverlayActivityStatus.RECONNECTING for s in snapshots)
    assert clock.monotonic() < 1.1


def test_recovery_discards_interrupted_frame_and_restarts_stability():
    class FrameVision(PenguinGame):
        def network_connection_error(self, frame):
            return self.obs("sanctuary_entry") if frame == "error" else None

        def network_retry(self, frame):
            return None

    deps, *_ = make_dependencies(FrameVision())
    engine = PenguinEngine(make_config(stable_frames=3), deps, StopController(),
                           SnapshotPublisher(RuntimeSnapshot.penguins("reconnect", 1), lambda _: None), frozenset())
    # Two stable observations before interruption cannot count toward the
    # three fresh stable observations required after recovery.
    frames = iter(["before1", "before2", "error", "cleared", "after1", "after2", "after3"])
    engine._capture_raw = lambda: next(frames)
    seen = []
    def detector(frame):
        seen.append(frame)
        return "same_state"
    assert engine._wait("fresh", detector, timeout_ms=100) == "same_state"
    assert seen == ["before1", "before2", "after1", "after2", "after3"]
