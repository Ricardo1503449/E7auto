from __future__ import annotations

import threading
from dataclasses import replace

from e7auto.automation import AutomationEngine, AutomationSession, SnapshotPublisher, StopController
from e7auto.config import Point, Rect, RefreshStrategyConfig, ScrollConfig
from e7auto.ports import WindowState
from e7auto.domain import OverlayActivityStatus, RuntimeSnapshot, StopReason
from e7auto.vision import Observation, PurchaseOutcome, ScrollMovementObservation

from .helpers import (
    FakeHotkeys,
    FakeClock,
    FakeInput,
    FakeOverlay,
    FakeRuntimeEnvironment,
    FakeWindowService,
    ScriptedVision,
    make_config,
    make_dependencies,
    match,
)


def run_session(
    vision: ScriptedVision,
    *,
    limit: int = 0,
    windows: FakeWindowService | None = None,
    inputs: FakeInput | None = None,
    overlay: FakeOverlay | None = None,
    hotkeys: FakeHotkeys | None = None,
    config=None,
    enabled_optional_target_ids: frozenset[str] = frozenset(),
    clock: FakeClock | None = None,
    runtime: FakeRuntimeEnvironment | None = None,
):
    snapshots: list[RuntimeSnapshot] = []
    deps, fake_windows, fake_inputs, fake_overlay, logger = make_dependencies(
        vision,
        windows=windows,
        inputs=inputs,
        overlay=overlay,
        clock=clock,
        runtime=runtime,
    )
    fake_hotkeys = hotkeys or FakeHotkeys()
    final = AutomationSession(config or make_config(), deps, fake_hotkeys, snapshots.append).run(
        limit,
        "test-run",
        enabled_optional_target_ids=enabled_optional_target_ids,
    )
    return final, snapshots, fake_windows, fake_inputs, fake_overlay, fake_hotkeys, logger


def balances_for_refreshes(count: int, start: int = 1000) -> list[int]:
    values: list[int] = []
    balance = start
    for _ in range(count):
        values.extend((balance, balance - 3))
        balance -= 3
    return values


def compact_strategy_config(
    batches: tuple[int, int, int, int] = (1, 1, 1, 1),
    waits: tuple[int, int, int] = (5, 180, 5),
):
    return replace(
        make_config(),
        refresh_strategy=RefreshStrategyConfig(batches, waits),
    )


def test_entry_success_resizes_window_and_scans_both_screens() -> None:
    final, _, windows, inputs, overlay, hotkeys, _ = run_session(
        ScriptedVision(top=[()], bottom=[()]), limit=0
    )
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert windows.resize_calls == [make_config().baseline_client_size]
    assert overlay.calls[0][0] == Rect(100, 200, 100, 80)
    assert [point for action, point, _ in inputs.actions if action == "click"] == [
        Point(105, 205)
    ]
    assert hotkeys.registered == hotkeys.unregistered == 1


def test_entry_click_uses_the_recognized_main_shop_anchor() -> None:
    class ShiftedMainShopVision(ScriptedVision):
        def main_shop_icon(self, frame: object) -> Observation | None:
            return Observation(
                "main",
                0.99,
                Rect(0, 0, 10, 40),
                Point(5, 35),
            )

    final, _, _, inputs, _, _, _ = run_session(
        ShiftedMainShopVision(top=[()], bottom=[()]), limit=0
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert [point for action, point, _ in inputs.actions if action == "click"] == [
        Point(105, 235)
    ]


def test_entry_recognition_failure_sends_no_input_and_restores_failure_reason() -> None:
    final, _, _, inputs, _, hotkeys, _ = run_session(
        ScriptedVision(main_visible=False), limit=0
    )
    assert final.stop_reason is StopReason.RECOGNITION_TIMEOUT
    assert inputs.actions == []
    assert hotkeys.unregistered == 1


def test_entry_retries_only_after_stable_main_screen_confirmation() -> None:
    vision = ScriptedVision(top=[()], bottom=[()], ready_visible=False)

    class EntryInput(FakeInput):
        def __init__(self) -> None:
            super().__init__()
            self.clicks = 0

        def click(self, point: Point) -> None:
            super().click(point)
            self.clicks += 1
            if self.clicks == 2:
                vision.ready_visible = True
                vision.main_visible = False

    inputs = EntryInput()
    final, _, _, _, _, _, logger = run_session(vision, inputs=inputs)

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert [action for action, _, _ in inputs.actions if action == "click"] == [
        "click",
        "click",
    ]
    assert [
        fields["next_attempt"]
        for event, fields in logger.events
        if event == "shop_entry_retry"
    ] == [2]


def test_entry_does_not_retry_when_neither_shop_nor_main_is_confirmed() -> None:
    vision = ScriptedVision(top=[()], bottom=[()], ready_visible=False)

    class EntryInput(FakeInput):
        def click(self, point: Point) -> None:
            super().click(point)
            vision.main_visible = False

    inputs = EntryInput()
    final, _, _, _, _, _, _ = run_session(vision, inputs=inputs)

    assert final.stop_reason is StopReason.RECOGNITION_TIMEOUT
    assert [action for action, _, _ in inputs.actions if action == "click"] == [
        "click"
    ]


def test_entry_stops_after_three_confirmed_dropped_clicks() -> None:
    inputs = FakeInput()
    final, _, _, _, _, _, logger = run_session(
        ScriptedVision(ready_visible=False),
        inputs=inputs,
    )

    assert final.stop_reason is StopReason.RECOGNITION_TIMEOUT
    assert [action for action, _, _ in inputs.actions if action == "click"] == [
        "click",
        "click",
        "click",
    ]
    stopped = [fields for event, fields in logger.events if event == "run_stopped"]
    assert stopped[-1]["detail"] == (
        "shop entry failed after 3 attempts; main screen remains visible"
    )


def test_client_resize_second_verification_failure_is_fail_closed() -> None:
    windows = FakeWindowService(resize_succeeds=False)
    final, _, _, inputs, _, _, _ = run_session(ScriptedVision(), windows=windows)
    assert final.stop_reason is StopReason.WINDOW_ABNORMAL
    assert inputs.actions == []


def test_overlay_capture_unsafe_blocks_all_input() -> None:
    final, _, _, inputs, _, _, _ = run_session(
        ScriptedVision(), overlay=FakeOverlay(safe=False)
    )
    assert final.stop_reason is StopReason.OVERLAY_CAPTURE_UNSAFE
    assert inputs.actions == []


def test_non_elevated_runtime_stops_before_window_lookup_or_input() -> None:
    runtime = FakeRuntimeEnvironment(elevated=False)

    final, _, windows, inputs, _, _, _ = run_session(
        ScriptedVision(),
        runtime=runtime,
    )

    assert final.stop_reason is StopReason.PERMISSION_REQUIRED
    assert windows.locate_calls == 0
    assert inputs.actions == []


def test_cursor_readback_mismatch_blocks_the_first_game_click() -> None:
    inputs = FakeInput(reported_position=Point(999, 999))

    final, _, _, _, _, _, logger = run_session(
        ScriptedVision(top=[()], bottom=[()]),
        inputs=inputs,
    )

    assert final.stop_reason is StopReason.INPUT_FAILURE
    assert [action for action, _, _ in inputs.actions] == ["move"]
    assert not any(event == "input" for event, _ in logger.events)


def test_input_success_log_is_written_only_after_platform_call_completes() -> None:
    inputs = FakeInput(fail_on_click=True)

    final, _, _, _, _, _, logger = run_session(
        ScriptedVision(top=[()], bottom=[()]),
        inputs=inputs,
    )

    assert final.stop_reason is StopReason.INPUT_FAILURE
    assert not any(event == "input" for event, _ in logger.events)
    failed = [fields for event, fields in logger.events if event == "input_failed"]
    assert len(failed) == 1
    assert failed[0]["action"] == "open_shop"


def test_top_empty_bottom_purchase_success_rescans_bottom_in_place_and_counts() -> None:
    vision = ScriptedVision(
        top=[(), ()],
        bottom=[(match("wood", "bottom"),), ()],
        purchase=[PurchaseOutcome.SUCCESS],
    )
    final, snapshots, _, _, _, _, _ = run_session(vision)
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert final.targets[0].acquired == 1
    assert vision.scan_calls == ["top", "bottom", "bottom"]
    assert max(snapshot.targets[0].acquired for snapshot in snapshots) == 1


def test_scroll_replays_calibrated_spacing_settle_and_verifies_before_bottom_scan() -> None:
    clock = FakeClock()
    config = replace(
        make_config(),
        scroll=ScrollConfig(
            Point(50, 40),
            -120,
            3,
            100,
            800,
            200,
            100,
            2,
            1.0,
            0.80,
            4,
            300,
            8,
            0.30,
        ),
    )
    vision = ScriptedVision(top=[()], bottom=[()])

    final, _, _, inputs, _, _, logger = run_session(
        vision,
        config=config,
        clock=clock,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert [action for action, _, _ in inputs.actions if action == "scroll"] == [
        "scroll",
        "scroll",
        "scroll",
    ]
    assert clock.sleeps == [0.1, 0.1, 0.2, 0.1, 0.1]
    assert vision.activity == [
        "scan:top",
        "observe_scroll_stability",
        "observe_scroll_stability",
        "verify_scroll",
        "scan:bottom",
    ]
    assert vision.scan_frames[-1] is vision.scroll_stability_after_frames[-1]
    settle = [fields for event, fields in logger.events if event == "scroll_settle_trace"]
    assert settle == [
        {
            "outcome": "stable",
            "total_phase_shift_y": "-350.000",
            "total_phase_response": "0.450000",
            "total_changed_fraction": "0.400000",
            "minimum_ms": 200,
            "poll_ms": 100,
            "maximum_ms": 800,
            "required_stable_observations": 2,
            "shift_tolerance_px": "1.000",
            "minimum_phase_response": "0.800000",
            "downsample_factor": 4,
            "sample_count": 3,
            "stability_comparisons": 2,
            "sample_elapsed_ms": "200,300,400",
            "pair_shift_y": "na,0.000,0.000",
            "pair_response": "na,0.950000,0.950000",
            "pair_changed_fraction": "na,0.005000,0.005000",
            "stable_counts": "0,1,2",
            "settle_elapsed_ms": 400,
            "early_exit_ms": 400,
        }
    ]
    verified = [fields for event, fields in logger.events if event == "scroll_verified"]
    assert len(verified) == 1
    assert verified[0]["phase_shift_y"] == "-350.000"
    assert verified[0]["changed_fraction"] == "0.400000"
    assert verified[0]["difference_threshold"] == 8
    assert verified[0]["settle_elapsed_ms"] == 400
    assert verified[0]["early_exit_ms"] == 400


def test_unverified_scroll_never_scans_bottom_or_refreshes() -> None:
    vision = ScriptedVision(
        top=[()],
        bottom=[()],
        scroll_movement=ScrollMovementObservation(2.0, 0.05, 30, 0.0, -12.0, 0.1),
    )

    final, _, _, inputs, _, _, _ = run_session(vision, limit=3)

    assert final.stop_reason is StopReason.SCROLL_VERIFICATION_FAILED
    assert vision.scan_calls == ["top"]
    assert len([action for action, _, _ in inputs.actions if action == "scroll"]) == 1
    assert len([action for action, _, _ in inputs.actions if action == "click"]) == 1


def test_unstable_scroll_times_out_at_maximum_without_full_comparison() -> None:
    moving = ScrollMovementObservation(10.0, 0.20, 80, 0.0, -8.0, 0.95)
    vision = ScriptedVision(
        top=[()],
        bottom=[()],
        scroll_stability=[moving] * 6,
    )
    clock = FakeClock()

    final, _, _, _, _, _, logger = run_session(vision, limit=3, clock=clock)

    assert final.stop_reason is StopReason.SCROLL_VERIFICATION_FAILED
    assert vision.scan_calls == ["top"]
    assert "verify_scroll" not in vision.activity
    assert clock.sleeps == [0.2, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
    settle = [fields for event, fields in logger.events if event == "scroll_settle_trace"]
    assert len(settle) == 1
    assert settle[0]["outcome"] == "timeout"
    assert settle[0]["sample_count"] == 7
    assert settle[0]["stability_comparisons"] == 6
    assert settle[0]["settle_elapsed_ms"] == 800
    assert settle[0]["early_exit_ms"] == 0


def test_bottom_scan_uses_all_three_stable_scroll_frames_without_new_capture() -> None:
    config = make_config(stable_frames=3)
    vision = ScriptedVision(
        top=[(), (), ()],
        bottom=[(), (), ()],
    )

    final, _, _, _, _, _, logger = run_session(vision, config=config)

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    first, second = vision.scroll_stability_frame_pairs
    expected_frames = (first[0], first[1], second[1])
    assert all(
        actual is expected
        for actual, expected in zip(vision.scan_frames[-3:], expected_frames)
    )
    bottom_stage = next(
        fields
        for event, fields in logger.events
        if event == "performance_stage"
        and fields.get("stage") == "inventory_scan"
        and fields.get("screen") == "bottom"
    )
    assert bottom_stage["cache_outcome"] == "hit"
    assert bottom_stage["cached_frames_available"] == 3
    assert bottom_stage["cached_frames_used"] == 3
    assert bottom_stage["cached_stable_suffix"] == 3
    assert bottom_stage["fresh_frames"] == 0
    assert bottom_stage["capture_count"] == 0
    assert bottom_stage["cache_wait_ms"] == "0.000"


def test_bottom_cache_mismatch_keeps_stable_suffix_and_adds_only_one_frame() -> None:
    config = make_config(stable_frames=3)
    vision = ScriptedVision(
        top=[(), (), ()],
        bottom=[(match("wood", "bottom"),), (), (), ()],
    )

    final, _, _, _, _, _, logger = run_session(vision, config=config)

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert vision.scan_calls.count("bottom") == 4
    bottom_stage = next(
        fields
        for event, fields in logger.events
        if event == "performance_stage"
        and fields.get("stage") == "inventory_scan"
        and fields.get("screen") == "bottom"
    )
    assert bottom_stage["cache_outcome"] == "suffix_fallback"
    assert bottom_stage["cached_frames_used"] == 3
    assert bottom_stage["cached_stable_suffix"] == 2
    assert bottom_stage["fresh_frames"] == 1
    assert bottom_stage["capture_count"] == 1
    assert bottom_stage["cache_wait_ms"] == "10.000"


def test_top_purchase_success_rescans_top_without_scrolling() -> None:
    vision = ScriptedVision(
        top=[(match("wood"),), ()],
        bottom=[()],
        purchase=[PurchaseOutcome.SUCCESS],
    )
    final, _, _, inputs, _, _, logger = run_session(vision)
    assert final.targets[0].acquired == 1
    assert vision.scan_calls == ["top", "top", "bottom"]
    assert vision.confirm_targets == ["wood"]
    assert vision.purchase_queries == [("wood", match("wood").roi)]
    assert len([action for action, _, _ in inputs.actions if action == "scroll"]) == 1
    input_actions = [fields["action"] for event, fields in logger.events if event == "input"]
    assert input_actions.count("scroll_bottom") == 1
    assert "scroll_top" not in input_actions


def test_row_one_and_three_targets_are_processed_one_at_a_time_with_top_rescan() -> None:
    vision = ScriptedVision(
        top=[(match("wood"), match("ore", slot_order=2)), (match("ore", slot_order=2),), ()],
        bottom=[()],
        purchase=[PurchaseOutcome.SUCCESS, PurchaseOutcome.SUCCESS],
    )
    final, _, _, inputs, _, _, _ = run_session(vision)
    assert [tally.acquired for tally in final.targets] == [1, 1]
    assert vision.scan_calls == ["top", "top", "top", "bottom"]
    assert len([action for action, _, _ in inputs.actions if action == "scroll"]) == 1


def test_row_one_and_six_targets_are_bought_with_one_downward_scroll() -> None:
    vision = ScriptedVision(
        top=[(match("wood"),), ()],
        bottom=[(match("ore", "bottom", slot_order=1),), ()],
        purchase=[PurchaseOutcome.SUCCESS, PurchaseOutcome.SUCCESS],
    )
    final, _, _, inputs, _, _, logger = run_session(vision)
    assert [tally.acquired for tally in final.targets] == [1, 1]
    assert vision.scan_calls == ["top", "top", "bottom", "bottom"]
    assert len([action for action, _, _ in inputs.actions if action == "scroll"]) == 1
    input_actions = [fields["action"] for event, fields in logger.events if event == "input"]
    assert input_actions == [
        "open_shop",
        "buy:wood:top-1",
        "confirm_purchase",
        "scroll_bottom",
        "buy:ore:bottom-2",
        "confirm_purchase",
    ]


def test_multiple_bottom_targets_are_processed_without_returning_top() -> None:
    vision = ScriptedVision(
        top=[()],
        bottom=[
            (match("wood", "bottom"), match("ore", "bottom", slot_order=1)),
            (match("ore", "bottom", slot_order=1),),
            (),
        ],
        purchase=[PurchaseOutcome.SUCCESS, PurchaseOutcome.SUCCESS],
    )
    final, _, _, inputs, _, _, _ = run_session(vision)
    assert [tally.acquired for tally in final.targets] == [1, 1]
    assert vision.scan_calls == ["top", "bottom", "bottom", "bottom"]
    assert len([action for action, _, _ in inputs.actions if action == "scroll"]) == 1


def test_stable_scan_does_not_accept_one_transient_empty_frame() -> None:
    config = make_config(stable_frames=2)
    vision = ScriptedVision(
        top=[(), (match("wood"),), (match("wood"),), (), ()],
        bottom=[(), ()],
        purchase=[PurchaseOutcome.SUCCESS],
    )
    final, _, _, _, _, _, _ = run_session(vision, config=config)
    assert final.targets[0].acquired == 1
    assert vision.scan_calls == ["top", "top", "top", "top", "top", "bottom", "bottom"]


def test_completed_slot_is_suppressed_if_stale_scan_matches_it_again() -> None:
    stale = match("wood")
    vision = ScriptedVision(
        top=[(stale,), (stale,)],
        bottom=[()],
        purchase=[PurchaseOutcome.SUCCESS],
    )
    final, _, _, inputs, _, _, logger = run_session(vision)
    assert final.targets[0].acquired == 1
    buy_clicks = [
        fields
        for event, fields in logger.events
        if event == "input" and str(fields["action"]).startswith("buy:")
    ]
    assert len(buy_clicks) == 1
    assert len([action for action, _, _ in inputs.actions if action == "scroll"]) == 1
    assert any(
        excluded == frozenset({"top-1"})
        for screen, _, excluded in vision.scan_requests
        if screen == "top"
    )


def test_previously_purchased_slot_is_skipped_without_counting_or_clicking() -> None:
    purchased = replace(match("wood"), is_purchased=True)
    vision = ScriptedVision(top=[(purchased,), ()], bottom=[()])

    final, _, _, inputs, _, _, logger = run_session(vision)

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert final.targets[0].acquired == 0
    assert len([action for action, _, _ in inputs.actions if action == "click"]) == 1
    assert any(
        event == "target_skipped_summary"
        and fields.get("reason") == "already_purchased_before_run"
        for event, fields in logger.events
    )


def test_repeated_previously_purchased_detection_is_logged_once_as_summary() -> None:
    purchased = replace(match("wood"), is_purchased=True)
    config = make_config(stable_frames=3)
    vision = ScriptedVision(
        top=[(purchased,), (purchased,), (purchased,)],
        bottom=[(), (), ()],
    )

    final, _, _, _, _, _, logger = run_session(vision, config=config)

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    summaries = [
        fields
        for event, fields in logger.events
        if event == "target_skipped_summary"
    ]
    assert summaries == [
        {
            "screen": "top",
            "slot": "top-1",
            "target": "wood",
            "reason": "already_purchased_before_run",
            "frames": 3,
            "max_confidence": "0.990000",
        }
    ]
    assert not any(event == "target_skipped" for event, _ in logger.events)


def test_purchase_funds_warning_stops_without_counting() -> None:
    vision = ScriptedVision(
        top=[(match("wood"),)],
        purchase=[PurchaseOutcome.INSUFFICIENT_FUNDS],
    )
    final, _, _, inputs, _, _, _ = run_session(vision)
    assert final.stop_reason is StopReason.PURCHASE_FUNDS_INSUFFICIENT
    assert final.targets[0].acquired == 0
    clicks = [action for action, _, _ in inputs.actions if action == "click"]
    assert len(clicks) == 3  # enter shop, buy, confirm purchase; never click warning confirm


def test_purchase_funds_warning_requires_consecutive_configured_stable_frames() -> None:
    base = make_config(stable_frames=3)
    config = replace(
        base,
        timing=replace(base.timing, purchase_result_timeout_ms=100),
    )
    vision = ScriptedVision(
        top=[(match("wood"),)] * 3,
        purchase=[
            PurchaseOutcome.INSUFFICIENT_FUNDS,
            PurchaseOutcome.PENDING,
            PurchaseOutcome.INSUFFICIENT_FUNDS,
            PurchaseOutcome.INSUFFICIENT_FUNDS,
            PurchaseOutcome.INSUFFICIENT_FUNDS,
        ],
    )

    final, _, _, inputs, _, _, logger = run_session(vision, config=config)

    assert final.stop_reason is StopReason.PURCHASE_FUNDS_INSUFFICIENT
    assert len(vision.purchase_queries) == 5
    assert [
        fields["insufficient_stable"]
        for event, fields in logger.events
        if event == "purchase_result"
    ] == [1, 0, 1, 2, 3]
    assert len([action for action, _, _ in inputs.actions if action == "click"]) == 3


def test_ambiguous_purchase_result_never_retries_or_counts() -> None:
    vision = ScriptedVision(top=[(match("wood"),)], purchase=[])
    final, _, _, inputs, _, _, _ = run_session(vision)
    assert final.stop_reason is StopReason.PURCHASE_RESULT_AMBIGUOUS
    assert final.targets[0].acquired == 0
    clicks = [action for action, _, _ in inputs.actions if action == "click"]
    assert len(clicks) == 3  # enter shop, buy, confirm; no retry


def test_budget_below_cost_never_refreshes() -> None:
    final, _, _, inputs, _, _, _ = run_session(
        ScriptedVision(top=[()], bottom=[()]), limit=2
    )
    assert final.refresh_spent == 0
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    # Only the entry click; scrolling is not a click.
    assert len([action for action, _, _ in inputs.actions if action == "click"]) == 1


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
    assert clicks == [Point(105, 205), Point(190, 270), Point(155, 245)]
    assert len([action for action, _, _ in inputs.actions if action == "scroll"]) == 2
    input_actions = [fields["action"] for event, fields in logger.events if event == "input"]
    assert input_actions.count("scroll_bottom") == 2
    assert "scroll_top" not in input_actions


def test_no_target_strategy_runs_all_recovery_stages_then_stops() -> None:
    config = compact_strategy_config()
    vision = ScriptedVision(
        top=[()] * 5,
        bottom=[()] * 5,
        balances=balances_for_refreshes(4),
    )

    final, snapshots, _, _, _, _, logger = run_session(vision, limit=12, config=config)

    assert final.stop_reason is StopReason.REFRESH_STRATEGY_EXHAUSTED
    assert final.refresh_spent == 12
    counter_changes: list[int] = []
    for snapshot in snapshots:
        value = snapshot.refreshes_without_mandatory_target
        if not counter_changes or counter_changes[-1] != value:
            counter_changes.append(value)
    assert counter_changes == [0, 1, 2, 3, 4]
    assert final.refreshes_without_mandatory_target == 4
    statuses = [snapshot.overlay_status for snapshot in snapshots]
    assert statuses[0] is OverlayActivityStatus.STARTED
    status_changes: list[OverlayActivityStatus] = []
    for status in statuses:
        if not status_changes or status_changes[-1] is not status:
            status_changes.append(status)
    assert status_changes == [
        OverlayActivityStatus.STARTED,
        OverlayActivityStatus.REFRESHING,
        OverlayActivityStatus.TRANSFERRING,
        OverlayActivityStatus.REFRESHING,
        OverlayActivityStatus.TRANSFERRING,
        OverlayActivityStatus.REFRESHING,
        OverlayActivityStatus.TRANSFERRING,
        OverlayActivityStatus.REFRESHING,
        OverlayActivityStatus.STOPPED,
    ]
    waits = [fields for event, fields in logger.events if event == "refresh_strategy_wait_started"]
    assert [(item["mode"], item["seconds"]) for item in waits] == [
        ("exit_and_reenter", 5),
        ("exit_and_reenter", 180),
        ("exit_and_reenter", 5),
    ]
    click_actions = [
        fields["action"]
        for event, fields in logger.events
        if event == "input" and fields["action"] != "scroll_bottom"
    ]
    assert click_actions == [
        "open_shop",
        "refresh_inventory",
        "confirm_refresh",
        "exit_shop",
        "open_shop",
        "refresh_inventory",
        "confirm_refresh",
        "exit_shop",
        "wake_main_screen",
        "open_shop",
        "refresh_inventory",
        "confirm_refresh",
        "exit_shop",
        "open_shop",
        "refresh_inventory",
        "confirm_refresh",
    ]
    wake_inputs = [
        fields
        for event, fields in logger.events
        if event == "input" and fields["action"] == "wake_main_screen"
    ]
    assert wake_inputs == [
        {
            "action": "wake_main_screen",
            "logical_x": 50,
            "logical_y": 40,
            "screen_x": 150,
            "screen_y": 240,
            "cursor_verified": True,
        }
    ]


def test_mandatory_target_resets_strategy_to_first_batch() -> None:
    config = compact_strategy_config((2, 2, 2, 2))
    vision = ScriptedVision(
        top=[(), (match("wood"),), (), (), (), ()],
        bottom=[()] * 5,
        purchase=[PurchaseOutcome.SUCCESS],
        balances=balances_for_refreshes(4),
    )

    final, snapshots, _, _, _, _, logger = run_session(
        vision,
        limit=12,
        config=config,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    resets = [fields for event, fields in logger.events if event == "refresh_strategy_reset"]
    assert [item["targets"] for item in resets] == ["wood"]
    waits = [fields for event, fields in logger.events if event == "refresh_strategy_wait_started"]
    assert [(item["mode"], item["seconds"]) for item in waits] == [
        ("exit_and_reenter", 5)
    ]


def test_displayed_no_target_streak_resets_after_mandatory_target() -> None:
    config = compact_strategy_config((3, 3, 3, 3))
    vision = ScriptedVision(
        top=[(), (), (match("wood"),)],
        bottom=[()] * 3,
        purchase=[PurchaseOutcome.SUCCESS],
        balances=balances_for_refreshes(2),
    )

    final, snapshots, _, _, _, _, logger = run_session(
        vision,
        limit=6,
        config=config,
    )

    values = [snapshot.refreshes_without_mandatory_target for snapshot in snapshots]
    assert 1 in values
    reset_index = values.index(1) + 1
    assert 0 in values[reset_index:]
    assert final.refreshes_without_mandatory_target == 0
    assert any(event == "refresh_strategy_reset" for event, _ in logger.events)


def test_optional_target_does_not_reset_no_target_strategy() -> None:
    config = replace(
        compact_strategy_config(),
        targets=make_config(include_friendship=True).targets,
    )
    vision = ScriptedVision(
        top=[(), (match("friendship_points"),), (), ()],
        bottom=[()] * 3,
        purchase=[PurchaseOutcome.SUCCESS],
        balances=balances_for_refreshes(2),
    )

    final, _, _, _, _, _, logger = run_session(
        vision,
        limit=6,
        config=config,
        enabled_optional_target_ids=frozenset({"friendship_points"}),
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert not any(event == "refresh_strategy_reset" for event, _ in logger.events)
    waits = [fields for event, fields in logger.events if event == "refresh_strategy_wait_started"]
    assert [(item["mode"], item["seconds"]) for item in waits] == [
        ("exit_and_reenter", 5)
    ]


def test_budget_completion_preempts_unnecessary_strategy_recovery() -> None:
    config = compact_strategy_config()
    vision = ScriptedVision(
        top=[(), ()],
        bottom=[(), ()],
        balances=balances_for_refreshes(1),
    )

    final, _, _, _, _, _, logger = run_session(vision, limit=3, config=config)

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert not any(event == "refresh_strategy_wait_started" for event, _ in logger.events)


def test_missing_exit_recognition_stops_before_exit_click() -> None:
    config = compact_strategy_config()
    vision = ScriptedVision(
        top=[()] * 3,
        bottom=[()] * 3,
        balances=balances_for_refreshes(2),
        exit_visible=False,
    )

    final, _, _, _, _, _, logger = run_session(vision, limit=9, config=config)

    assert final.stop_reason is StopReason.RECOGNITION_TIMEOUT
    assert not any(
        event == "input" and fields["action"] == "exit_shop"
        for event, fields in logger.events
    )


def test_f5_interrupts_checkpointed_strategy_wait() -> None:
    hotkeys = FakeHotkeys()

    class F5DuringWaitClock(FakeClock):
        def sleep(self, seconds: float) -> None:
            super().sleep(seconds)
            if seconds == 1.0 and hotkeys.callback is not None:
                callback = hotkeys.callback
                hotkeys.callback = None
                callback()

    config = compact_strategy_config()
    vision = ScriptedVision(
        top=[(), ()],
        bottom=[(), ()],
        balances=balances_for_refreshes(1),
    )
    clock = F5DuringWaitClock()

    final, _, _, _, _, _, logger = run_session(
        vision,
        limit=6,
        config=config,
        clock=clock,
        hotkeys=hotkeys,
    )

    assert final.stop_reason is StopReason.MANUAL_F5
    assert any(event == "refresh_strategy_wait_started" for event, _ in logger.events)
    assert final.refresh_spent == 3


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
    assert clicks == [Point(105, 205), Point(190, 270), Point(190, 270)]


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
    assert clicks == [Point(190, 270), Point(160, 250)]
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
    assert clicks == [Point(190, 270), Point(160, 250)]
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

        def click(self, point: Point) -> None:
            super().click(point)
            if point == Point(190, 270):
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
        Point(105, 205),
        Point(190, 270),
        Point(190, 270),
        Point(155, 245),
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
    assert clicks == [Point(105, 205), Point(190, 270), Point(155, 245)]
    assert any(event == "refresh_confirmation_delayed" for event, _ in logger.events)


def test_refresh_retry_requires_stable_normal_shop_control() -> None:
    vision = ScriptedVision(
        top=[()],
        bottom=[()],
        balances=[100],
        refresh_confirm_visible=False,
    )

    class HideShopAfterFirstRefresh(FakeInput):
        def click(self, point: Point) -> None:
            super().click(point)
            if point == Point(190, 270):
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
    assert clicks == [Point(105, 205), Point(190, 270)]


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
    assert clicks == [Point(105, 205), Point(190, 270)]


def test_f5_after_unacknowledged_refresh_blocks_retry_click() -> None:
    vision = ScriptedVision(
        top=[()],
        bottom=[()],
        balances=[100],
        refresh_confirm_visible=False,
    )
    hotkeys = FakeHotkeys()

    class StopAfterFirstRefresh(FakeInput):
        def click(self, point: Point) -> None:
            super().click(point)
            if point == Point(190, 270):
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
    assert clicks == [Point(105, 205), Point(190, 270)]


def test_refresh_failure_does_not_charge_budget() -> None:
    vision = ScriptedVision(top=[()], bottom=[()], balances=[3927, 3927])
    final, _, _, _, _, _, _ = run_session(vision, limit=3)
    assert final.stop_reason is StopReason.RECOGNITION_TIMEOUT
    assert final.refresh_spent == 0


def test_unexpected_sky_stone_delta_is_fail_closed_without_counting() -> None:
    vision = ScriptedVision(top=[()], bottom=[()], balances=[3927, 3923])
    final, _, _, _, _, _, logger = run_session(vision, limit=3)
    assert final.stop_reason is StopReason.REFRESH_BALANCE_MISMATCH
    assert final.refresh_spent == 0
    assert not any(event == "refresh_counted" for event, _ in logger.events)


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
    assert clicks == [Point(105, 205)]


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
    assert clicks == [Point(105, 205)]


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


def test_shop_reentry_invalidates_verified_balance_before_next_refresh() -> None:
    config = compact_strategy_config(batches=(1, 13, 13, 10))
    vision = ScriptedVision(
        top=[(), (), ()],
        bottom=[(), (), ()],
        balances=[100, 97, 97, 94],
    )

    final, _, _, _, _, _, logger = run_session(
        vision,
        limit=6,
        config=config,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert final.refresh_spent == 6
    assert vision.balance_queries == 4
    assert not any(
        event == "trusted_sky_stone_balance_used"
        for event, _ in logger.events
    )
    assert any(
        event == "trusted_sky_stone_balance_invalidated"
        and fields.get("reason") == "shop_exit"
        for event, fields in logger.events
    )


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


def test_network_reconnect_pauses_active_clock_and_restores_overlay_status() -> None:
    vision = ScriptedVision(
        network_errors=[True, True, False],
        network_retries=[False],
    )
    deps, _, _, _, logger = make_dependencies(vision)
    snapshots: list[RuntimeSnapshot] = []
    initial = RuntimeSnapshot.initial(
        "network-status",
        tuple((target.target_id, target.display_name) for target in make_config().targets),
        3,
    )
    publisher = SnapshotPublisher(initial, snapshots.append)
    control = StopController()
    engine = AutomationEngine(
        make_config(),
        deps,
        control,
        publisher,
        frozenset(target.target_id for target in make_config().targets),
    )
    engine._capture_raw = lambda: object()  # type: ignore[method-assign]
    engine._trusted_sky_stone_balance = 321
    engine._pending_top_scan = ()
    publisher.mutate(
        lambda snapshot: snapshot.with_overlay_status(OverlayActivityStatus.REFRESHING)
    )

    engine._handle_network_exception(object())

    assert snapshots[-2].overlay_status is OverlayActivityStatus.RECONNECTING
    assert snapshots[-1].overlay_status is OverlayActivityStatus.REFRESHING
    assert engine._active_monotonic() == 0.0
    assert engine._trusted_sky_stone_balance is None
    assert engine._pending_top_scan is None
    assert any(
        event == "trusted_sky_stone_balance_invalidated"
        and fields == {"reason": "network_recovery", "value": 321}
        for event, fields in logger.events
    )


def test_hotkey_registration_failure_never_prepares_or_inputs() -> None:
    hotkeys = FakeHotkeys(succeeds=False)
    final, snapshots, windows, inputs, _, _, _ = run_session(
        ScriptedVision(), hotkeys=hotkeys
    )
    assert final.stop_reason is StopReason.HOTKEY_FAILURE
    assert windows.locate_calls == 0
    assert inputs.actions == []
    assert hotkeys.unregistered == 0
    assert len([snapshot for snapshot in snapshots if snapshot.is_final]) == 1


def test_f6_toggle_enters_and_locks_overlay_move_mode_before_engine_runs() -> None:
    overlay = FakeOverlay()

    def toggle_twice(_f5_callback: object) -> None:
        assert hotkeys.move_callback is not None
        hotkeys.move_callback()
        hotkeys.move_callback()

    hotkeys = FakeHotkeys(on_register=toggle_twice)
    final, _, _, _, _, _, logger = run_session(
        ScriptedVision(top=[()], bottom=[()]),
        overlay=overlay,
        hotkeys=hotkeys,
    )

    assert overlay.move_calls == ["begin", "finish"]
    assert [event for event, _ in logger.events if event.startswith("overlay_move_")] == [
        "overlay_move_started",
        "overlay_move_finished",
    ]
    assert final.is_final


def test_pause_blocks_checkpoint_until_resume() -> None:
    control = StopController()
    reached: list[str] = []
    assert control.pause()

    worker = threading.Thread(
        target=lambda: (control.checkpoint(), reached.append("resumed")),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=0.05)
    assert reached == []

    control.resume()
    worker.join(timeout=1.0)
    assert reached == ["resumed"]


def test_f6_pause_duration_is_excluded_from_active_clock() -> None:
    clock = FakeClock()
    vision = ScriptedVision()
    deps, _, _, _, _ = make_dependencies(vision, clock=clock)
    initial = RuntimeSnapshot.initial(
        "pause-clock",
        tuple((target.target_id, target.display_name) for target in make_config().targets),
        0,
    )
    control = StopController()
    engine = AutomationEngine(
        make_config(),
        deps,
        control,
        SnapshotPublisher(initial, lambda _snapshot: None),
        frozenset(target.target_id for target in make_config().targets),
    )

    assert control.pause(clock.monotonic())
    clock.sleep(4.75)
    assert engine._active_monotonic() == 0.0
    control.resume(clock.monotonic())
    clock.sleep(0.25)

    assert engine._active_monotonic() == 0.25


def test_f5_after_one_dispatched_input_blocks_every_new_input() -> None:
    inputs = FakeInput()
    hotkeys = FakeHotkeys(on_register=lambda callback: setattr(inputs, "trigger_once", callback))
    final, snapshots, _, _, _, _, _ = run_session(
        ScriptedVision(top=[()], bottom=[()]), inputs=inputs, hotkeys=hotkeys
    )
    assert final.stop_reason is StopReason.MANUAL_F5
    assert [action for action, _, _ in inputs.actions] == ["move", "click"]
    assert len([snapshot for snapshot in snapshots if snapshot.is_final]) == 1


def test_window_move_during_run_stops_before_next_input() -> None:
    windows = FakeWindowService(abnormal_on_inspect=3)
    final, _, _, inputs, _, _, _ = run_session(ScriptedVision(), windows=windows)
    assert final.stop_reason is StopReason.WINDOW_ABNORMAL
    assert inputs.actions == []


def test_minimize_disappear_resize_and_focus_loss_all_stop_safely() -> None:
    abnormal_states = (
        WindowState(True, True, True, Rect(100, 200, 100, 80)),
        WindowState(False, False, False, Rect(0, 0, 0, 0)),
        WindowState(True, False, True, Rect(100, 200, 99, 80)),
        WindowState(True, False, False, Rect(100, 200, 100, 80)),
    )
    for state in abnormal_states:
        windows = FakeWindowService(abnormal_on_inspect=3, abnormal_state=state)
        final, _, _, inputs, _, _, _ = run_session(ScriptedVision(), windows=windows)
        assert final.stop_reason is StopReason.WINDOW_ABNORMAL
        assert inputs.actions == []


def test_new_session_starts_every_counter_at_zero() -> None:
    first, _, _, _, _, _, _ = run_session(
        ScriptedVision(top=[(match("wood"),), ()], bottom=[()], purchase=[PurchaseOutcome.SUCCESS])
    )
    second, snapshots, _, _, _, _, _ = run_session(ScriptedVision(top=[()], bottom=[()]))
    assert first.targets[0].acquired == 1
    assert second.targets[0].acquired == 0
    assert snapshots[0].refresh_spent == 0
    assert all(tally.acquired == 0 for tally in snapshots[0].targets)


def test_friendship_points_are_ignored_when_checkbox_option_is_disabled() -> None:
    config = make_config(include_friendship=True)
    vision = ScriptedVision(top=[(match("friendship_points"),)], bottom=[()])
    final, _, _, inputs, _, _, logger = run_session(vision, config=config)
    friendship = next(tally for tally in final.targets if tally.target_id == "friendship_points")
    assert friendship.acquired == 0
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert len([action for action, _, _ in inputs.actions if action == "click"]) == 1
    assert not any(event == "target_skipped" for event, _ in logger.events)
    assert all(
        enabled == frozenset({"wood", "ore"})
        for _, enabled, _ in vision.scan_requests
    )


def test_friendship_points_are_purchased_when_checkbox_option_is_enabled() -> None:
    config = make_config(include_friendship=True)
    vision = ScriptedVision(
        top=[(match("friendship_points"),), ()],
        bottom=[()],
        purchase=[PurchaseOutcome.SUCCESS],
    )
    final, _, _, _, _, _, _ = run_session(
        vision,
        config=config,
        enabled_optional_target_ids=frozenset({"friendship_points"}),
    )
    friendship = next(tally for tally in final.targets if tally.target_id == "friendship_points")
    assert friendship.acquired == 1


def test_mandatory_targets_cannot_be_disabled_by_optional_selection() -> None:
    config = make_config(include_friendship=True)
    vision = ScriptedVision(
        top=[(match("wood"),), ()],
        bottom=[()],
        purchase=[PurchaseOutcome.SUCCESS],
    )
    final, _, _, _, _, _, _ = run_session(vision, config=config)
    assert next(tally for tally in final.targets if tally.target_id == "wood").acquired == 1


def test_snapshot_publisher_ignores_mutation_after_final() -> None:
    emitted: list[RuntimeSnapshot] = []
    publisher = SnapshotPublisher(RuntimeSnapshot.initial("r", (("wood", "木材"),), 0), emitted.append)
    final = publisher.finalize(StopReason.MANUAL_F5)
    publisher.mutate(lambda snapshot: snapshot.with_incremented_target("wood"))
    publisher.finalize(StopReason.INTERNAL_ERROR)
    assert publisher.snapshot is final
    assert len(emitted) == 1
