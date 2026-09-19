from __future__ import annotations
from typing import TypeVar
from e7auto.core.domain import OverlayActivityStatus, RunState, StopReason
from e7auto.features.shop.contracts import InventoryMatch
from e7auto.core.observations import Observation
from e7auto.runtime.stop_control import StopExecution
_REFRESH_CONFIRM_FAST_CONFIDENCE = 0.99

def _invalidate_trusted_balance(self, reason: str) -> None:
    previous = self._trusted_sky_stone_balance
    self._trusted_sky_stone_balance = None
    self._pending_top_scan = None
    if previous is not None:
        self.runtime.deps.logger.event(
            "trusted_sky_stone_balance_invalidated",
            reason=reason,
            value=previous,
        )


def _record_refresh_strategy_outcome(
    self,
    mandatory_targets_found: frozenset[str],
) -> tuple[str, int] | None:
    # Statistics apply to both modes; only staged mode advances the strategy.
    if mandatory_targets_found:
        self._consecutive_no_target_refreshes = 0
    else:
        self._consecutive_no_target_refreshes += 1
    self.runtime.publisher.mutate(
        lambda snapshot: snapshot.with_refreshes_without_mandatory_target(
            self._consecutive_no_target_refreshes
        )
    )
    if self._continuous_refresh:
        return None

    if mandatory_targets_found:
        self.runtime.deps.logger.event(
            "refresh_strategy_reset",
            targets=",".join(sorted(mandatory_targets_found)),
            previous_stage=self._refresh_strategy_stage + 1,
            previous_stage_refreshes=self._refreshes_without_mandatory_target,
        )
        self._refresh_strategy_stage = 0
        self._refreshes_without_mandatory_target = 0
        return None

    self._refreshes_without_mandatory_target += 1
    batch_limit = self.runtime.config.refresh_strategy.batch_refreshes[
        self._refresh_strategy_stage
    ]
    self.runtime.deps.logger.event(
        "refresh_strategy_progress",
        stage=self._refresh_strategy_stage + 1,
        stage_refreshes=self._refreshes_without_mandatory_target,
        batch_limit=batch_limit,
    )
    if self._refreshes_without_mandatory_target < batch_limit:
        return None
    if self._refresh_strategy_stage == len(self.runtime.config.refresh_strategy.batch_refreshes) - 1:
        self.runtime.deps.logger.event(
            "refresh_strategy_exhausted",
            stage=self._refresh_strategy_stage + 1,
            stage_refreshes=self._refreshes_without_mandatory_target,
        )
        raise StopExecution(StopReason.REFRESH_STRATEGY_EXHAUSTED)

    completed_stage = self._refresh_strategy_stage
    wait_seconds = self.runtime.config.refresh_strategy.recovery_wait_seconds[completed_stage]
    self._refresh_strategy_stage += 1
    self._refreshes_without_mandatory_target = 0
    return "exit_and_reenter", wait_seconds


def _perform_refresh_strategy_recovery(self, mode: str, seconds: int) -> None:
    if mode == "exit_and_reenter":
        self._exit_store()
        self.runtime.publisher.mutate(
            lambda snapshot: snapshot.with_overlay_status(
                OverlayActivityStatus.TRANSFERRING
            )
        )
    self.runtime.deps.logger.event(
        "refresh_strategy_wait_started",
        mode=mode,
        seconds=seconds,
        next_stage=self._refresh_strategy_stage + 1,
    )
    self.runtime.interruptible_wait(seconds)
    if mode == "exit_and_reenter":
        if seconds == 180:
            self.runtime.click(
                "wake_main_screen",
                self.runtime.config.points["main_screen_wake"],
            )
        self._enter_store()
    self.runtime.deps.logger.event(
        "refresh_strategy_wait_completed",
        mode=mode,
        seconds=seconds,
        next_stage=self._refresh_strategy_stage + 1,
    )


def _wait_refresh_confirmation_dialog(
    self,
    attempt: int,
    initial_observation: Observation | None = None,
) -> Observation:
    dialog_name = "refresh_confirm_dialog"
    deadline = self.runtime.active_monotonic() + self.runtime.config.timing.dialog_timeout_ms / 1000
    stable = 0
    pending = initial_observation
    while self.runtime.active_monotonic() <= deadline:
        observation = pending
        pending = None
        if observation is None:
            observation = self.runtime.vision_call(
                self.runtime.deps.vision.refresh_confirm_dialog,
                self.runtime.capture(),
            )
        if observation is None:
            stable = 0
            self.runtime.deps.logger.event(
                "recognition",
                object=dialog_name,
                detected=False,
            )
        else:
            stable += 1
            fast = observation.confidence >= _REFRESH_CONFIRM_FAST_CONFIDENCE
            self.runtime.deps.logger.event(
                "recognition",
                object=dialog_name,
                detected=True,
                confidence=f"{observation.confidence:.6f}",
                roi=f"{observation.roi.x},{observation.roi.y},{observation.roi.width},{observation.roi.height}",
                stable=stable,
                fast_eligible=fast,
            )
            if fast or stable >= self.runtime.config.timing.stable_frames:
                mode = "fast" if fast else "stable"
                self.runtime.deps.logger.event(
                    "refresh_confirmation_accepted",
                    attempt=attempt,
                    mode=mode,
                    confidence=f"{observation.confidence:.6f}",
                    stable=stable,
                )
                return observation
        self.runtime.control.checkpoint()
        self.runtime.deps.clock.sleep(self.runtime.config.timing.poll_interval_ms / 1000)
    raise StopExecution(
        StopReason.RECOGNITION_TIMEOUT,
        f"timeout waiting for {dialog_name}",
    )


def _wait_for_refresh_confirmation(self, before: int) -> Observation:
    dialog_name = "refresh_confirm_dialog"

    try:
        return self._wait_refresh_confirmation_dialog(attempt=1)
    except StopExecution as exc:
        if (
            exc.reason is not StopReason.RECOGNITION_TIMEOUT
            or exc.detail != f"timeout waiting for {dialog_name}"
        ):
            raise

    self.runtime.deps.logger.event(
        "refresh_click_unacknowledged",
        attempt=1,
        sky_stone_before=before,
    )

    frame = self.runtime.capture()
    observation = self.runtime.vision_call(self.runtime.deps.vision.refresh_confirm_dialog, frame)
    if observation is not None:
        self.runtime.deps.logger.event(
            "refresh_confirmation_delayed",
            after_attempt=1,
        )
        return self._wait_refresh_confirmation_dialog(
            attempt=1,
            initial_observation=observation,
        )

    self.runtime.wait_stable_observation(
        "shop_refresh_button:refresh_retry",
        self.runtime.deps.vision.shop_ready,
        self.runtime.config.timing.dialog_timeout_ms,
    )
    retry_balance = self._read_stable_sky_stone_balance("before_refresh_retry")
    if retry_balance != before:
        raise StopExecution(
            StopReason.REFRESH_BALANCE_MISMATCH,
            "Sky Stone balance changed before refresh click retry: "
            f"expected unchanged {before}, observed {retry_balance}",
        )

    frame = self.runtime.capture()
    observation = self.runtime.vision_call(self.runtime.deps.vision.refresh_confirm_dialog, frame)
    if observation is not None:
        self.runtime.deps.logger.event(
            "refresh_confirmation_delayed",
            after_attempt=1,
        )
        return self._wait_refresh_confirmation_dialog(
            attempt=1,
            initial_observation=observation,
        )
    if self.runtime.vision_call(self.runtime.deps.vision.shop_ready, frame) is None:
        raise StopExecution(
            StopReason.RECOGNITION_TIMEOUT,
            "shop refresh button disappeared before refresh click retry",
        )

    self.runtime.deps.logger.event(
        "refresh_click_retry",
        attempt=2,
        sky_stone_before=before,
    )
    self.runtime.click(
        "refresh_inventory",
        self.runtime.config.points["refresh_button"],
        attempt=2,
    )
    try:
        return self._wait_refresh_confirmation_dialog(attempt=2)
    except StopExecution as exc:
        if (
            exc.reason is not StopReason.RECOGNITION_TIMEOUT
            or exc.detail != f"timeout waiting for {dialog_name}"
        ):
            raise
        self.runtime.deps.logger.event(
            "refresh_click_unacknowledged",
            attempt=2,
            sky_stone_before=before,
        )
        raise StopExecution(
            StopReason.REFRESH_CLICK_UNACKNOWLEDGED,
            "refresh confirmation dialog missing after two refresh clicks",
        ) from exc


def _refresh_inventory(self) -> None:
    mark = self.runtime.performance_mark()
    outcome = "failed"
    self.runtime.transition(RunState.REFRESHING)
    try:
        if self._trusted_sky_stone_balance is None:
            before = self._read_stable_sky_stone_balance("before_refresh")
        else:
            before = self._trusted_sky_stone_balance
            self.runtime.deps.logger.event(
                "trusted_sky_stone_balance_used",
                stage="before_refresh",
                value=before,
            )
        expected = before - self.runtime.config.refresh_cost
        if expected < 0:
            self._invalidate_trusted_balance("balance_below_refresh_cost")
            raise StopExecution(
                StopReason.REFRESH_BALANCE_MISMATCH,
                f"Sky Stone balance {before} is below refresh cost {self.runtime.config.refresh_cost}",
            )
        self._invalidate_trusted_balance("refresh_started")
        self.runtime.click(
            "refresh_inventory",
            self.runtime.config.points["refresh_button"],
            attempt=1,
        )
        confirmation = self._wait_for_refresh_confirmation(before)
        self.runtime.click(
            "confirm_refresh",
            confirmation.anchor,
        )

        pending_top = self._wait_for_refresh_balance(before, expected)
        updated = self.runtime.publisher.mutate(
            lambda snapshot: snapshot.with_refresh_spent(
                snapshot.refresh_spent + self.runtime.config.refresh_cost
            )
        )
        self._trusted_sky_stone_balance = expected
        self._pending_top_scan = pending_top
        self.runtime.deps.logger.event(
            "refresh_counted",
            sky_stone_before=before,
            sky_stone_after=expected,
            refresh_spent=updated.refresh_spent,
            refresh_limit=updated.refresh_limit,
        )
        outcome = "counted"
    finally:
        self.runtime.log_performance_stage(
            mark,
            "refresh_inventory",
            outcome=outcome,
        )


def _read_stable_sky_stone_balance(self, stage: str) -> int:
    mark = self.runtime.performance_mark()
    deadline = self.runtime.active_monotonic() + self.runtime.config.timing.refresh_timeout_ms / 1000
    candidate: int | None = None
    stable_count = 0
    frames = 0
    outcome = "timeout"
    try:
        while self.runtime.active_monotonic() <= deadline:
            frame = self.runtime.capture()
            frames += 1
            observation = self.runtime.vision_call(
                self.runtime.deps.vision.sky_stone_balance,
                frame,
            )
            if observation is not None:
                if observation.value == candidate:
                    stable_count += 1
                else:
                    candidate = observation.value
                    stable_count = 1
                self.runtime.deps.logger.event(
                    "sky_stone_observation",
                    stage=stage,
                    value=observation.value,
                    confidence=f"{observation.confidence:.6f}",
                    roi=f"{observation.roi.x},{observation.roi.y},{observation.roi.width},{observation.roi.height}",
                    stable=stable_count,
                )
                if stable_count >= self.runtime.config.timing.stable_frames:
                    outcome = "stable"
                    return observation.value
            else:
                candidate = None
                stable_count = 0
            self.runtime.control.checkpoint()
            self.runtime.deps.clock.sleep(self.runtime.config.timing.poll_interval_ms / 1000)
        raise StopExecution(
            StopReason.RECOGNITION_TIMEOUT,
            f"cannot read stable Sky Stone balance: {stage}",
        )
    finally:
        if outcome != "stable":
            self._invalidate_trusted_balance(f"sky_stone_{stage}_{outcome}")
        self.runtime.log_performance_stage(
            mark,
            "sky_stone_balance_read",
            balance_stage=stage,
            frames=frames,
            stable=stable_count,
            outcome=outcome,
        )


def _wait_for_refresh_balance(
    self,
    before: int,
    expected: int,
) -> tuple[InventoryMatch, ...] | None:
    mark = self.runtime.performance_mark()
    deadline = self.runtime.active_monotonic() + self.runtime.config.timing.refresh_timeout_ms / 1000
    candidate: int | None = None
    stable_count = 0
    top_previous_key: tuple[tuple[str, str], ...] | None = None
    top_stable = 0
    top_candidate: tuple[InventoryMatch, ...] | None = None
    skipped: dict[tuple[str, str, str, str], tuple[int, float]] = {}
    frames = 0
    outcome = "timeout"
    try:
        while self.runtime.active_monotonic() <= deadline:
            frame = self.runtime.capture()
            frames += 1
            observation = self.runtime.vision_call(
                self.runtime.deps.vision.sky_stone_balance,
                frame,
            )
            if observation is None or observation.value == before:
                candidate = None
                stable_count = 0
                top_previous_key = None
                top_stable = 0
                top_candidate = None
            else:
                if observation.value == candidate:
                    stable_count += 1
                else:
                    candidate = observation.value
                    stable_count = 1
                self.runtime.deps.logger.event(
                    "sky_stone_observation",
                    stage="after_refresh",
                    value=observation.value,
                    expected=expected,
                    confidence=f"{observation.confidence:.6f}",
                    roi=f"{observation.roi.x},{observation.roi.y},{observation.roi.width},{observation.roi.height}",
                    stable=stable_count,
                )

                if observation.value == expected:
                    top_matches = self._detect_actionable_inventory(
                        frame,
                        "top",
                        set(),
                        skipped,
                    )
                    top_key = tuple(
                        (match.slot_id, match.target_id)
                        for match in top_matches
                    )
                    if top_key == top_previous_key:
                        top_stable += 1
                    else:
                        top_previous_key = top_key
                        top_stable = 1
                    self._log_inventory_frame(
                        "top",
                        top_matches,
                        top_stable,
                        source="after_refresh_balance",
                    )
                    if top_stable >= self.runtime.config.timing.stable_frames:
                        top_candidate = top_matches
                else:
                    top_previous_key = None
                    top_stable = 0
                    top_candidate = None

                if stable_count >= self.runtime.config.timing.stable_frames:
                    if observation.value != expected:
                        outcome = "mismatch"
                        raise StopExecution(
                            StopReason.REFRESH_BALANCE_MISMATCH,
                            f"expected Sky Stone balance {expected}, observed {observation.value}",
                        )
                    outcome = "stable"
                    return top_candidate
            self.runtime.control.checkpoint()
            self.runtime.deps.clock.sleep(self.runtime.config.timing.poll_interval_ms / 1000)
        raise StopExecution(
            StopReason.RECOGNITION_TIMEOUT,
            f"Sky Stone balance did not reach expected value {expected}",
        )
    finally:
        self._log_inventory_skip_summaries(skipped)
        self.runtime.log_performance_stage(
            mark,
            "refresh_balance_wait",
            frames=frames,
            balance_stable=stable_count,
            top_stable=top_stable,
            top_reused=top_candidate is not None and outcome == "stable",
            outcome=outcome,
        )
