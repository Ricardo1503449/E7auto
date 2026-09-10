from __future__ import annotations

from dataclasses import replace

from .support import run_session
from e7auto.domain import StopReason
from e7auto.vision import PurchaseOutcome
from tests.helpers import ScriptedVision, make_config, match


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
    assert len([action for action, _, _ in inputs.actions if action == "click"]) == 2
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


def test_friendship_points_are_ignored_when_checkbox_option_is_disabled() -> None:
    config = make_config(include_friendship=True)
    vision = ScriptedVision(top=[(match("friendship_points"),)], bottom=[()])
    final, _, _, inputs, _, _, logger = run_session(vision, config=config)
    friendship = next(tally for tally in final.targets if tally.target_id == "friendship_points")
    assert friendship.acquired == 0
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert len([action for action, _, _ in inputs.actions if action == "click"]) == 2
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
