from __future__ import annotations

from dataclasses import replace

from .support import run_session
from e7auto.automation import AutomationEngine, SnapshotPublisher, StopController
from e7auto.config import Point, Rect
from e7auto.domain import RuntimeSnapshot, StopReason
from e7auto.vision import Observation, PurchaseOutcome
from tests.helpers import (
    FakeHotkeys,
    FakeInput,
    ScriptedVision,
    make_config,
    make_dependencies,
    match,
)


def test_budget_below_cost_never_refreshes() -> None:
    final, _, _, inputs, _, _, _ = run_session(
        ScriptedVision(top=[()], bottom=[()]), limit=2
    )
    assert final.refresh_spent == 0
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    # Only the entry click; scrolling is not a click.
    assert len([action for action, _, _ in inputs.actions if action == "click"]) == 2


def test_exact_budget_refresh_scans_last_inventory_completely() -> None:
    vision = ScriptedVision(
        top=[(), ()],
        bottom=[(), ()],
        balances=[3927, 3924],
    )
    final, _, _, inputs, _, _, logger = run_session(vision, limit=3)
    assert final.refresh_spent == 3
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert vision.scan_calls == ["top", "bottom", "top", "bottom"]
    clicks = [point for action, point, _ in inputs.actions if action == "click"]
    assert clicks == [Point(5, 5), Point(90, 70), Point(55, 45), Point(6, 6)]
    assert len([action for action, _, _ in inputs.actions if action == "scroll"]) == 2
    input_actions = [fields["action"] for event, fields in logger.events if event == "input"]
    assert input_actions.count("scroll_bottom") == 2
    assert "scroll_top" not in input_actions


def test_missing_refresh_confirmation_never_confirms_or_charges() -> None:
    vision = ScriptedVision(
        top=[()],
        bottom=[()],
        balances=[100, 100],
        refresh_confirm_visible=False,
    )
    final, _, _, inputs, _, _, _ = run_session(vision, limit=3)
    assert final.stop_reason is StopReason.REFRESH_CLICK_UNACKNOWLEDGED
    assert final.refresh_spent == 0
    clicks = [point for action, point, _ in inputs.actions if action == "click"]
    assert clicks == [Point(5, 5), Point(90, 70), Point(90, 70)]


def test_high_confidence_refresh_confirmation_uses_fast_path_and_detected_anchor() -> None:
    class HighConfidenceDialogVision(ScriptedVision):
        def __init__(self) -> None:
            super().__init__()
            self.dialog_checks = 0

        def refresh_confirm_dialog(self, frame: object) -> Observation:
            self.dialog_checks += 1
            return Observation(
                "refresh-confirm",
                0.995,
                Rect(40, 40, 20, 10),
                Point(60, 50),
            )

    config = make_config(stable_frames=3)
    vision = HighConfidenceDialogVision()
    deps, _, inputs, _, logger = make_dependencies(vision)
    snapshots: list[RuntimeSnapshot] = []
    initial = RuntimeSnapshot.initial(
        "fast-refresh-confirm",
        tuple((target.target_id, target.display_name) for target in config.targets),
        3,
    )
    engine = AutomationEngine(
        config,
        deps,
        StopController(),
        SnapshotPublisher(initial, snapshots.append),
        frozenset(target.target_id for target in config.targets),
    )
    engine._prepare()
    engine._trusted_sky_stone_balance = 100
    engine._wait_for_refresh_balance = lambda before, expected: None  # type: ignore[method-assign]

    engine._refresh_inventory()

    assert vision.dialog_checks == 1
    clicks = [point for action, point, _ in inputs.actions if action == "click"]
    assert clicks == [Point(90, 70), Point(60, 50)]
    accepted = [
        fields
        for event, fields in logger.events
        if event == "refresh_confirmation_accepted"
    ]
    assert accepted == [
        {"attempt": 1, "mode": "fast", "confidence": "0.995000", "stable": 1}
    ]


def test_lower_confidence_refresh_confirmation_keeps_three_frame_gate() -> None:
    class LowerConfidenceDialogVision(ScriptedVision):
        def __init__(self) -> None:
            super().__init__()
            self.dialog_checks = 0

        def refresh_confirm_dialog(self, frame: object) -> Observation:
            self.dialog_checks += 1
            return Observation(
                "refresh-confirm",
                0.98,
                Rect(40, 40, 20, 10),
                Point(60, 50),
            )

    config = make_config(stable_frames=3)
    vision = LowerConfidenceDialogVision()
    deps, _, inputs, _, logger = make_dependencies(vision)
    snapshots: list[RuntimeSnapshot] = []
    initial = RuntimeSnapshot.initial(
        "stable-refresh-confirm",
        tuple((target.target_id, target.display_name) for target in config.targets),
        3,
    )
    engine = AutomationEngine(
        config,
        deps,
        StopController(),
        SnapshotPublisher(initial, snapshots.append),
        frozenset(target.target_id for target in config.targets),
    )
    engine._prepare()
    engine._trusted_sky_stone_balance = 100
    engine._wait_for_refresh_balance = lambda before, expected: None  # type: ignore[method-assign]

    engine._refresh_inventory()

    assert vision.dialog_checks == 3
    clicks = [point for action, point, _ in inputs.actions if action == "click"]
    assert clicks == [Point(90, 70), Point(60, 50)]
    accepted = [
        fields
        for event, fields in logger.events
        if event == "refresh_confirmation_accepted"
    ]
    assert accepted == [
        {"attempt": 1, "mode": "stable", "confidence": "0.980000", "stable": 3}
    ]


def test_missing_first_refresh_dialog_retries_once_then_confirms_once() -> None:
    vision = ScriptedVision(
        top=[(), ()],
        bottom=[(), ()],
        balances=[100, 100, 97],
        refresh_confirm_visible=False,
    )

    class RevealDialogOnSecondRefresh(FakeInput):
        def __init__(self) -> None:
            super().__init__()
            self.refresh_attempts = 0

        def click(self, window, point: Point) -> None:
            super().click(window, point)
            if point == Point(90, 70):
                self.refresh_attempts += 1
                if self.refresh_attempts == 2:
                    vision.refresh_confirm_visible = True

    inputs = RevealDialogOnSecondRefresh()
    final, _, _, _, _, _, logger = run_session(
        vision,
        limit=3,
        inputs=inputs,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert final.refresh_spent == 3
    clicks = [point for action, point, _ in inputs.actions if action == "click"]
    assert clicks == [
        Point(5, 5),
        Point(90, 70),
        Point(90, 70),
        Point(55, 45),
        Point(6, 6),
    ]
    refresh_inputs = [
        fields
        for event, fields in logger.events
        if event == "input" and fields["action"] == "refresh_inventory"
    ]
    assert [fields["attempt"] for fields in refresh_inputs] == [1, 2]
    assert len([event for event, _ in logger.events if event == "refresh_counted"]) == 1


def test_delayed_refresh_dialog_suppresses_retry_click() -> None:
    class DelayedDialogVision(ScriptedVision):
        def __init__(self) -> None:
            super().__init__(
                top=[()],
                bottom=[()],
                balances=[100, 97],
                refresh_confirm_visible=False,
            )
            self.dialog_checks = 0

        def refresh_confirm_dialog(self, frame: object):
            self.dialog_checks += 1
            if self.dialog_checks == 1:
                return None
            return Observation(
                "refresh-confirm",
                0.99,
                Rect(40, 40, 20, 10),
                Point(55, 45),
            )

    config = make_config()
    config = replace(
        config,
        timing=replace(config.timing, dialog_timeout_ms=1),
    )
    vision = DelayedDialogVision()
    final, _, _, inputs, _, _, logger = run_session(
        vision,
        limit=3,
        config=config,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert final.refresh_spent == 3
    clicks = [point for action, point, _ in inputs.actions if action == "click"]
    assert clicks == [Point(5, 5), Point(90, 70), Point(55, 45), Point(6, 6)]
    assert any(event == "refresh_confirmation_delayed" for event, _ in logger.events)


def test_refresh_retry_requires_stable_normal_shop_control() -> None:
    vision = ScriptedVision(
        top=[()],
        bottom=[()],
        balances=[100],
        refresh_confirm_visible=False,
    )

    class HideShopAfterFirstRefresh(FakeInput):
        def click(self, window, point: Point) -> None:
            super().click(window, point)
            if point == Point(90, 70):
                vision.ready_visible = False

    inputs = HideShopAfterFirstRefresh()
    final, _, _, _, _, _, _ = run_session(
        vision,
        limit=3,
        inputs=inputs,
    )

    assert final.stop_reason is StopReason.RECOGNITION_TIMEOUT
    assert final.refresh_spent == 0
    clicks = [point for action, point, _ in inputs.actions if action == "click"]
    assert clicks == [Point(5, 5), Point(90, 70)]


def test_refresh_retry_rejects_changed_balance_without_second_click() -> None:
    vision = ScriptedVision(
        top=[()],
        bottom=[()],
        balances=[100, 97],
        refresh_confirm_visible=False,
    )
    final, _, _, inputs, _, _, _ = run_session(vision, limit=3)

    assert final.stop_reason is StopReason.REFRESH_BALANCE_MISMATCH
    assert final.refresh_spent == 0
    clicks = [point for action, point, _ in inputs.actions if action == "click"]
    assert clicks == [Point(5, 5), Point(90, 70)]


def test_f5_after_unacknowledged_refresh_blocks_retry_click() -> None:
    vision = ScriptedVision(
        top=[()],
        bottom=[()],
        balances=[100],
        refresh_confirm_visible=False,
    )
    hotkeys = FakeHotkeys()

    class StopAfterFirstRefresh(FakeInput):
        def click(self, window, point: Point) -> None:
            super().click(window, point)
            if point == Point(90, 70):
                assert hotkeys.callback is not None
                hotkeys.callback()

    inputs = StopAfterFirstRefresh()
    final, _, _, _, _, _, _ = run_session(
        vision,
        limit=3,
        inputs=inputs,
        hotkeys=hotkeys,
    )

    assert final.stop_reason is StopReason.MANUAL_F5
    assert final.refresh_spent == 0
    clicks = [point for action, point, _ in inputs.actions if action == "click"]
    assert clicks == [Point(5, 5), Point(90, 70)]


def test_refresh_failure_does_not_charge_budget() -> None:
    vision = ScriptedVision(top=[()], bottom=[()], balances=[3927, 3927])
    final, _, _, _, _, _, _ = run_session(vision, limit=3)
    assert final.stop_reason is StopReason.RECOGNITION_TIMEOUT
    assert final.refresh_spent == 0


def test_concurrent_top_detection_cannot_act_before_exact_minus_three_balance() -> None:
    vision = ScriptedVision(
        top=[(), (match("wood"),)],
        bottom=[()],
        purchase=[PurchaseOutcome.SUCCESS],
        balances=[100, 96],
    )

    final, _, _, inputs, _, _, _ = run_session(vision, limit=3)

    assert final.stop_reason is StopReason.REFRESH_BALANCE_MISMATCH
    assert final.refresh_spent == 0
    assert final.targets[0].acquired == 0
    assert not any(
        action == "click" and point == Point(130, 220)
        for action, point, _ in inputs.actions
    )


def test_unreadable_pre_refresh_balance_sends_no_refresh_input() -> None:
    vision = ScriptedVision(top=[()], bottom=[()], balances=[None])
    final, _, _, inputs, _, _, _ = run_session(vision, limit=3)
    assert final.stop_reason is StopReason.RECOGNITION_TIMEOUT
    assert final.refresh_spent == 0
    clicks = [point for action, point, _ in inputs.actions if action == "click"]
    assert clicks == [Point(5, 5)]


def test_refresh_waits_through_stable_old_balance_then_accepts_stable_minus_three() -> None:
    config = make_config(stable_frames=2)
    vision = ScriptedVision(
        top=[(), ()],
        bottom=[(), ()],
        balances=[100, 100, 100, 100, 97, 97],
    )
    final, _, _, _, _, _, logger = run_session(vision, limit=3, config=config)
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert final.refresh_spent == 3
    counted = [fields for event, fields in logger.events if event == "refresh_counted"]
    assert counted == [
        {
            "sky_stone_before": 100,
            "sky_stone_after": 97,
            "refresh_spent": 3,
            "refresh_limit": 3,
        }
    ]


def test_balance_below_refresh_cost_stops_before_refresh_click() -> None:
    vision = ScriptedVision(top=[()], bottom=[()], balances=[2])
    final, _, _, inputs, _, _, _ = run_session(vision, limit=3)
    assert final.stop_reason is StopReason.REFRESH_BALANCE_MISMATCH
    assert final.refresh_spent == 0
    clicks = [point for action, point, _ in inputs.actions if action == "click"]
    assert clicks == [Point(5, 5)]


def test_verified_balance_is_reused_between_ordinary_in_shop_refreshes() -> None:
    vision = ScriptedVision(
        top=[(), (), ()],
        bottom=[(), (), ()],
        balances=[100, 97, 94],
    )

    final, _, _, _, _, _, logger = run_session(vision, limit=6)

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert final.refresh_spent == 6
    assert vision.balance_queries == 3
    reused = [
        fields
        for event, fields in logger.events
        if event == "trusted_sky_stone_balance_used"
    ]
    assert reused == [{"stage": "before_refresh", "value": 97}]


def test_post_refresh_balance_frames_supply_reusable_stable_top_scan() -> None:
    config = make_config(stable_frames=2)
    vision = ScriptedVision(
        top=[(), (), (), ()],
        bottom=[(), (), (), ()],
        balances=[100, 100, 97, 97],
    )

    final, _, _, _, _, _, logger = run_session(
        vision,
        limit=3,
        config=config,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert vision.scan_calls.count("top") == 4
    reused = [
        fields
        for event, fields in logger.events
        if event == "inventory_scan_reused"
    ]
    assert reused == [{"screen": "top", "source": "after_refresh_balance", "targets": 0}]


def test_unstable_concurrent_top_scan_falls_back_to_normal_scan() -> None:
    config = make_config(stable_frames=2)
    vision = ScriptedVision(
        top=[(), (), (), (match("wood"),), (), ()],
        bottom=[(), (), (), ()],
        balances=[100, 100, 97, 97],
    )

    final, _, _, _, _, _, logger = run_session(
        vision,
        limit=3,
        config=config,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert vision.scan_calls.count("top") == 6
    assert not any(event == "inventory_scan_reused" for event, _ in logger.events)


def test_performance_logging_is_aggregate_and_stage_scoped() -> None:
    final, _, _, _, _, _, logger = run_session(
        ScriptedVision(top=[()], bottom=[()]),
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    stages = [
        fields
        for event, fields in logger.events
        if event == "performance_stage"
    ]
    assert {fields["stage"] for fields in stages} >= {
        "inventory_scan",
        "scroll_to_bottom",
    }
    assert all(float(fields["duration_ms"]) >= 0 for fields in stages)
    assert all(int(fields["capture_count"]) >= 0 for fields in stages)
    assert all(int(fields["vision_calls"]) >= 0 for fields in stages)
