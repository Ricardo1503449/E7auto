from __future__ import annotations

from dataclasses import replace

from .support import run_session
from e7auto.config import Point, ScrollConfig
from e7auto.domain import StopReason
from e7auto.vision import PurchaseOutcome, ScrollMovementObservation
from tests.helpers import FakeClock, ScriptedVision, make_config, match


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
            "total_gate_checks": 1,
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
        "exit_shop",
    ]
