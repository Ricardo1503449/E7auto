from __future__ import annotations

from dataclasses import replace

import pytest

from e7auto.core.types import Rect
from e7auto.core.domain import StopReason
from e7auto.features.shop.contracts import ScrollMovementObservation, ScrollOverlapObservation
from tests.helpers import FakeClock, FakeHotkeys, ScriptedVision, make_config
from tests.automation.support import run_session


def movement(y: float = 0.0, x: float = 0.0, response: float = 0.95):
    return ScrollMovementObservation(25.0, 0.4, 254, x, y, response)


def config():
    base = make_config(stable_frames=3)
    return replace(base, rois={**base.rois, "inventory_list": Rect(0, 0, 1320, 1180)})


ACCEPTED = ScrollOverlapObservation(True, "matched", 0.667, (0.8, 0.8, 0.96, 0.98), 4)
REJECTED = ScrollOverlapObservation(False, "insufficient_matching_blocks", 0.667, (0.1,) * 4)
FAILED_TOTAL = movement(0.041, response=0.085457)


def trace(logger):
    return next(fields for event, fields in logger.events if event == "scroll_settle_trace")


def test_logged_motion_can_use_fallback_without_overwriting_total_or_losing_cached_frames():
    vision = ScriptedVision(
        top=[(), (), ()], bottom=[(), (), ()], scroll_movement=FAILED_TOTAL,
        scroll_stability=[movement(-393.380, response=0.212362), movement(-0.012), movement(0.004)],
        scroll_overlaps=[ACCEPTED],
    )
    final, _, _, _, _, _, logger = run_session(vision, config=config())
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    logged = trace(logger)
    assert logged["verification_method"] == "cumulative_overlap"
    assert logged["cumulative_shift_y"] == "-393.388"
    assert logged["total_phase_shift_y"] == "0.041"
    assert logged["sample_count"] == 4  # No fixed F7 wait.
    assert logged["fallback_checks"] == 1
    assert logged["fallback_result"] == "accepted"
    assert len(vision.overlap_reference_frames) == 1
    assert vision.overlap_reference_frames[0] is vision.scroll_stability_frame_pairs[0][0]
    assert vision.overlap_queries[0][1] is vision.scroll_stability_after_frames[-1]
    assert all(actual is expected for actual, expected in zip(
        vision.scan_frames[-3:], vision.scroll_stability_after_frames[-3:], strict=True,
    ))
    bottom = next(fields for event, fields in logger.events
                  if event == "performance_stage" and fields.get("screen") == "bottom")
    assert bottom["capture_count"] == 0
    assert bottom["cache_outcome"] == "hit"
    stage = next(fields for event, fields in logger.events
                 if event == "performance_stage" and fields.get("stage") == "scroll_to_bottom")
    assert stage["capture_count"] == 5  # B + F1..F4; fallback captures nothing.


@pytest.mark.parametrize("pairs,total,reason", [
    ([movement(-300)], FAILED_TOTAL, "insufficient_net_upward_shift"),
    ([movement(-299)], FAILED_TOTAL, "insufficient_net_upward_shift"),
    ([movement(393)], FAILED_TOTAL, "insufficient_net_upward_shift"),
    ([movement(-400), movement(110)], FAILED_TOTAL, "insufficient_net_upward_shift"),
    ([movement(-393, x=4.01)], FAILED_TOTAL, "horizontal_shift"),
    ([movement(-591)], FAILED_TOTAL, "insufficient_overlap"),
    ([movement(-393)], replace(FAILED_TOTAL, changed_fraction=0.30), "insufficient_total_change"),
    ([movement(-393)], replace(FAILED_TOTAL, changed_fraction=float("nan")), "insufficient_total_change"),
    ([movement()], FAILED_TOTAL, "insufficient_net_upward_shift"),
    ([movement(-393), movement(float("nan"))], FAILED_TOTAL, "non_finite_pair"),
    ([movement(-393, x=float("inf"))], FAILED_TOTAL, "non_finite_pair"),
    ([movement(-393, response=float("nan"))], FAILED_TOTAL, "non_finite_pair"),
    ([movement(-393, response=float("inf"))], FAILED_TOTAL, "non_finite_pair"),
])
def test_ineligible_fallback_does_no_image_work(pairs, total, reason):
    vision = ScriptedVision(scroll_movement=total, scroll_stability=pairs, scroll_overlaps=[ACCEPTED])
    final, _, _, _, _, _, logger = run_session(vision, config=config(), limit=3)
    assert final.stop_reason is StopReason.SCROLL_VERIFICATION_FAILED
    assert vision.overlap_queries == []
    assert vision.overlap_reference_frames == []
    assert "bottom" not in vision.scan_calls
    assert trace(logger)["fallback_checks"] == 0
    assert trace(logger)["fallback_reason"] == reason


def test_primary_success_skips_eligible_fallback():
    vision = ScriptedVision(scroll_stability=[movement(-393)], scroll_overlaps=[ACCEPTED])
    final, _, _, _, _, _, logger = run_session(vision, config=config())
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert trace(logger)["verification_method"] == "primary"
    assert "cumulative_shift_y" not in trace(logger)
    assert "fallback_result" not in trace(logger)
    assert vision.overlap_reference_frames == []
    assert vision.overlap_queries == []


def test_motion_without_stability_never_checks_fallback():
    vision = ScriptedVision(scroll_movement=FAILED_TOTAL,
                            scroll_stability=[movement(-393)] + [movement(-2)] * 10)
    final, _, _, _, _, _, logger = run_session(vision, config=config())
    assert final.stop_reason is StopReason.SCROLL_VERIFICATION_FAILED
    assert "verify_scroll" not in vision.activity
    assert vision.overlap_queries == []
    assert trace(logger)["settle_elapsed_ms"] == 800


def test_rejected_overlap_retries_with_one_reference_then_primary_takes_priority():
    vision = ScriptedVision(
        scroll_movements=[FAILED_TOTAL, FAILED_TOTAL, movement(-393)],
        scroll_stability=[movement(-393)], scroll_overlaps=[REJECTED, REJECTED, ACCEPTED],
    )
    final, _, _, _, _, _, logger = run_session(vision, config=config())
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert trace(logger)["verification_method"] == "primary"
    assert trace(logger)["fallback_checks"] == 2
    assert trace(logger)["cumulative_shift_y"] == "-393.000"
    assert trace(logger)["fallback_result"] == "rejected"
    assert trace(logger)["fallback_block_scores"] == "0.100000,0.100000,0.100000,0.100000"
    assert len(vision.overlap_reference_frames) == 1
    assert len(vision.overlap_queries) == 2
    assert vision.overlap_queries[0][0] is vision.overlap_queries[1][0]
    assert len(vision.scroll_overlaps) == 1  # Third, successful primary gate did not invoke it.


def test_rejected_overlap_does_not_extend_timeout_or_scan_bottom():
    clock = FakeClock()
    vision = ScriptedVision(scroll_movement=FAILED_TOTAL, scroll_stability=[movement(-393)])
    final, _, _, _, _, _, logger = run_session(vision, config=config(), clock=clock)
    assert final.stop_reason is StopReason.SCROLL_VERIFICATION_FAILED
    assert trace(logger)["settle_elapsed_ms"] == 800
    assert trace(logger)["fallback_result"] == "rejected"
    assert "bottom" not in vision.scan_calls
    assert len(vision.overlap_reference_frames) == 1
    assert len(vision.overlap_queries) > 1


def test_fallback_reference_and_motion_are_not_reused_on_next_scroll():
    vision = ScriptedVision(
        scroll_movement=FAILED_TOTAL,
        scroll_movements=[FAILED_TOTAL, FAILED_TOTAL],
        scroll_stability=[movement(-393), movement(), movement()], scroll_overlaps=[ACCEPTED],
    )
    base = config()
    single_frame_config = replace(base, timing=replace(base.timing, stable_frames=1))
    final, _, _, _, _, _, logger = run_session(vision, config=single_frame_config, limit=3)
    assert final.stop_reason is StopReason.SCROLL_VERIFICATION_FAILED
    traces = [fields for event, fields in logger.events if event == "scroll_settle_trace"]
    assert len(traces) == 2
    assert traces[0]["verification_method"] == "cumulative_overlap"
    assert traces[1]["cumulative_shift_y"] == "0.000"
    assert traces[1]["fallback_checks"] == 0
    assert len(vision.overlap_queries) == 1


def test_stop_requested_by_failed_primary_gate_prevents_fallback_work():
    hotkeys = FakeHotkeys()

    class StoppingVision(ScriptedVision):
        def inventory_scroll_movement(self, before, after):
            result = super().inventory_scroll_movement(before, after)
            hotkeys.callback()
            return result

    vision = StoppingVision(scroll_movement=FAILED_TOTAL, scroll_stability=[movement(-393)])
    final, _, _, _, _, _, _ = run_session(vision, config=config(), hotkeys=hotkeys)
    assert final.stop_reason is StopReason.MANUAL_F5
    assert vision.overlap_reference_frames == []
    assert vision.overlap_queries == []
    assert "bottom" not in vision.scan_calls


def test_primary_recovers_without_overlap_but_keeps_earlier_failure_details():
    vision = ScriptedVision(scroll_movements=[FAILED_TOTAL, movement(-393)])
    final, _, _, _, _, _, logger = run_session(vision, config=config())
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    logged = trace(logger)
    assert logged["verification_method"] == "primary"
    assert logged["total_gate_checks"] == 2
    assert logged["fallback_checks"] == 0
    assert logged["fallback_reason"] == "insufficient_net_upward_shift"
    assert logged["fallback_result"] == "not_checked"
    assert logged["cumulative_shift_y"] == "0.000"


def test_first_primary_success_does_not_hide_non_finite_pair():
    vision = ScriptedVision(scroll_stability=[movement(-393, response=float("nan"))])
    final, _, _, _, _, _, logger = run_session(vision, config=config())
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    logged = trace(logger)
    assert logged["verification_method"] == "primary"
    assert logged["total_gate_checks"] == 1
    assert logged["cumulative_valid"] is False
    assert logged["fallback_reason"] == "non_finite_pair"
