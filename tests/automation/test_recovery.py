from __future__ import annotations

from dataclasses import replace

from .support import run_session, balances_for_refreshes, compact_strategy_config
from e7auto.automation import AutomationEngine, SnapshotPublisher, StopController
from e7auto.domain import OverlayActivityStatus, RuntimeSnapshot, StopReason
from e7auto.vision import PurchaseOutcome
from tests.helpers import (
    FakeHotkeys,
    FakeClock,
    ScriptedVision,
    make_config,
    make_dependencies,
    match,
)


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
        "exit_shop",
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
            "client_x": 50,
            "client_y": 40,
            "background_message_queued": True,
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
