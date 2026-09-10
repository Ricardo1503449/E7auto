from __future__ import annotations

from dataclasses import replace

from .support import run_session
from e7auto.automation import SnapshotPublisher
from e7auto.config import Point, Rect
from e7auto.domain import RuntimeSnapshot, StopReason
from e7auto.ports import WindowState
from e7auto.vision import Observation, PurchaseOutcome, ScrollMovementObservation
from tests.helpers import (
    FakeHotkeys,
    FakeClock,
    FakeInput,
    FakeOverlay,
    FakeRuntimeEnvironment,
    FakeWindowService,
    ScriptedVision,
    make_config,
    match,
)


def test_entry_success_resizes_window_and_scans_both_screens() -> None:
    final, _, windows, inputs, overlay, hotkeys, _ = run_session(
        ScriptedVision(top=[()], bottom=[()]), limit=0
    )
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert windows.resize_calls == [make_config().baseline_client_size]
    assert overlay.calls[0][0] == Rect(100, 200, 100, 80)
    assert [point for action, point, _ in inputs.actions if action == "click"] == [
        Point(5, 5),
        Point(6, 6),
    ]
    assert hotkeys.registered == hotkeys.unregistered == 1


def test_initial_background_window_starts_without_foreground_activation() -> None:
    windows = FakeWindowService()
    windows.state = WindowState(
        True,
        False,
        False,
        Rect(100, 200, 64, 64),
        Rect(100, 200, 64, 64),
    )

    final, _, _, inputs, _, _, logger = run_session(
        ScriptedVision(top=[()], bottom=[()]),
        windows=windows,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert windows.restore_calls == 1
    assert any(action == "click" for action, _, _ in inputs.actions)
    prepared = [fields for event, fields in logger.events if event == "window_prepared"]
    assert prepared[-1]["game_foreground"] is False


def test_initial_minimized_window_is_restored_without_becoming_foreground() -> None:
    windows = FakeWindowService()
    windows.state = WindowState(
        True,
        True,
        False,
        Rect(100, 200, 64, 64),
        Rect(100, 200, 64, 64),
    )

    final, _, _, inputs, _, _, logger = run_session(
        ScriptedVision(top=[()], bottom=[()]),
        windows=windows,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert windows.restore_calls == 1
    assert any(action == "click" for action, _, _ in inputs.actions)
    prepared = [fields for event, fields in logger.events if event == "window_prepared"]
    assert prepared[-1]["game_foreground"] is False


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
        Point(5, 35),
        Point(6, 6),
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

        def click(self, window, point: Point) -> None:
            super().click(window, point)
            self.clicks += 1
            if self.clicks == 2:
                vision.ready_visible = True
                vision.main_visible = False
            elif point == Point(6, 6):
                vision.main_visible = True

    inputs = EntryInput()
    final, _, _, _, _, _, logger = run_session(vision, inputs=inputs)

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert [action for action, _, _ in inputs.actions if action == "click"] == [
        "click",
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
        def click(self, window, point: Point) -> None:
            super().click(window, point)
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


def test_overlay_position_timeout_blocks_all_input() -> None:
    final, _, _, inputs, _, _, _ = run_session(
        ScriptedVision(), overlay=FakeOverlay(succeeds=False)
    )
    assert final.stop_reason is StopReason.INTERNAL_ERROR
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


def test_delayed_background_frame_waits_past_stale_stability_until_total_gate_passes() -> None:
    stable = ScrollMovementObservation(0.5, 0.005, 8, 0.0, 0.0, 0.95)
    transition = ScrollMovementObservation(25.0, 0.40, 254, 0.0, -350.0, 0.95)
    stale_total = ScrollMovementObservation(0.5, 0.005, 8, 0.0, 0.0, 0.99)
    valid_total = ScrollMovementObservation(25.0, 0.40, 254, 0.0, -350.0, 0.45)
    config = replace(
        make_config(),
        scroll=replace(make_config().scroll, settle_ms=1500),
    )
    vision = ScriptedVision(
        top=[()],
        bottom=[()],
        scroll_stability=[stable, stable, transition, stable, stable],
        scroll_movements=[stale_total, valid_total],
    )

    final, _, _, _, _, _, logger = run_session(
        vision,
        config=config,
        clock=FakeClock(),
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert vision.activity.count("verify_scroll") == 2
    assert vision.scan_calls == ["top", "bottom"]
    settle = next(fields for event, fields in logger.events if event == "scroll_settle_trace")
    assert settle["outcome"] == "stable"
    assert settle["total_gate_checks"] == 2
    assert settle["total_phase_shift_y"] == "-350.000"


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


def test_unexpected_sky_stone_delta_is_fail_closed_without_counting() -> None:
    vision = ScriptedVision(top=[()], bottom=[()], balances=[3927, 3923])
    final, _, _, _, _, _, logger = run_session(vision, limit=3)
    assert final.stop_reason is StopReason.REFRESH_BALANCE_MISMATCH
    assert final.refresh_spent == 0
    assert not any(event == "refresh_counted" for event, _ in logger.events)


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


def test_f5_after_one_dispatched_input_blocks_every_new_input() -> None:
    inputs = FakeInput()
    hotkeys = FakeHotkeys(on_register=lambda callback: setattr(inputs, "trigger_once", callback))
    final, snapshots, _, _, _, _, _ = run_session(
        ScriptedVision(top=[()], bottom=[()]), inputs=inputs, hotkeys=hotkeys
    )
    assert final.stop_reason is StopReason.MANUAL_F5
    assert [action for action, _, _ in inputs.actions] == ["click"]
    assert len([snapshot for snapshot in snapshots if snapshot.is_final]) == 1


def test_window_move_during_run_stops_before_next_input() -> None:
    windows = FakeWindowService(abnormal_on_inspect=3)
    final, _, _, inputs, _, _, _ = run_session(ScriptedVision(), windows=windows)
    assert final.stop_reason is StopReason.WINDOW_ABNORMAL
    assert inputs.actions == []


def test_minimize_disappear_and_resize_all_stop_safely() -> None:
    abnormal_states = (
        WindowState(True, True, True, Rect(100, 200, 100, 80)),
        WindowState(False, False, False, Rect(0, 0, 0, 0)),
        WindowState(True, False, True, Rect(100, 200, 99, 80)),
    )
    for state in abnormal_states:
        windows = FakeWindowService(abnormal_on_inspect=3, abnormal_state=state)
        final, _, _, inputs, _, _, _ = run_session(ScriptedVision(), windows=windows)
        assert final.stop_reason is StopReason.WINDOW_ABNORMAL
        assert inputs.actions == []


def test_focus_loss_is_allowed_after_initial_window_preparation() -> None:
    windows = FakeWindowService(
        abnormal_on_inspect=3,
        abnormal_state=WindowState(
            True,
            False,
            False,
            Rect(100, 200, 100, 80),
            Rect(100, 200, 100, 80),
        ),
    )

    final, _, _, inputs, _, _, _ = run_session(
        ScriptedVision(top=[()], bottom=[()]),
        windows=windows,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert any(action == "click" for action, _, _ in inputs.actions)


def test_new_session_starts_every_counter_at_zero() -> None:
    first, _, _, _, _, _, _ = run_session(
        ScriptedVision(top=[(match("wood"),), ()], bottom=[()], purchase=[PurchaseOutcome.SUCCESS])
    )
    second, snapshots, _, _, _, _, _ = run_session(ScriptedVision(top=[()], bottom=[()]))
    assert first.targets[0].acquired == 1
    assert second.targets[0].acquired == 0
    assert snapshots[0].refresh_spent == 0
    assert all(tally.acquired == 0 for tally in snapshots[0].targets)


def test_snapshot_publisher_ignores_mutation_after_final() -> None:
    emitted: list[RuntimeSnapshot] = []
    publisher = SnapshotPublisher(RuntimeSnapshot.initial("r", (("wood", "木材"),), 0), emitted.append)
    final = publisher.finalize(StopReason.MANUAL_F5)
    publisher.mutate(lambda snapshot: snapshot.with_incremented_target("wood"))
    publisher.finalize(StopReason.INTERNAL_ERROR)
    assert publisher.snapshot is final
    assert len(emitted) == 1
