from __future__ import annotations
from typing import TypeVar
from e7auto.core.domain import RunState, StopReason
from e7auto.features.shop.contracts import InventoryMatch, PurchaseOutcome
from e7auto.runtime.stop_control import StopExecution
from e7auto.features.shop.scrolling import FrameSample

def _record_inventory_skip(
    skipped: dict[tuple[str, str, str, str], tuple[int, float]],
    match: InventoryMatch,
    screen: str,
    reason: str,
) -> None:
    key = (screen, match.slot_id, match.target_id, reason)
    count, maximum = skipped.get(key, (0, 0.0))
    skipped[key] = (count + 1, max(maximum, match.confidence))


def _log_inventory_skip_summaries(
    self,
    skipped: dict[tuple[str, str, str, str], tuple[int, float]],
) -> None:
    for (screen, slot, target, reason), (frames, maximum) in sorted(skipped.items()):
        self.runtime.deps.logger.event(
            "target_skipped_summary",
            screen=screen,
            slot=slot,
            target=target,
            reason=reason,
            frames=frames,
            max_confidence=f"{maximum:.6f}",
        )


def _detect_actionable_inventory(
    self,
    frame: object,
    screen: str,
    completed_slot_ids: set[str],
    skipped: dict[tuple[str, str, str, str], tuple[int, float]],
) -> tuple[InventoryMatch, ...]:
    detected = self.runtime.vision_call(
        self.runtime.deps.vision.scan_inventory,
        frame,
        screen,
        self._enabled_target_ids,
        frozenset(completed_slot_ids),
    )
    matches: list[InventoryMatch] = []
    for match in detected:
        if match.is_purchased:
            self._record_inventory_skip(
                skipped,
                match,
                screen,
                "already_purchased_before_run",
            )
            continue
        if match.target_id not in self._enabled_target_ids:
            self._record_inventory_skip(
                skipped,
                match,
                screen,
                "not_enabled_for_run",
            )
            continue
        if match.slot_id in completed_slot_ids:
            self._record_inventory_skip(
                skipped,
                match,
                screen,
                "already_purchased_in_inventory",
            )
            continue
        matches.append(match)
    return tuple(matches)


def _log_inventory_frame(
    self,
    screen: str,
    matches: tuple[InventoryMatch, ...],
    stable: int,
    *,
    source: str | None = None,
) -> None:
    fields: dict[str, object] = {
        "screen": screen,
        "targets": len(matches),
        "stable": stable,
    }
    if source is not None:
        fields["source"] = source
    self.runtime.deps.logger.event("inventory_scan", **fields)
    for match in matches:
        recognition_fields: dict[str, object] = {
            "object": f"target:{match.target_id}",
            "detected": True,
            "confidence": f"{match.confidence:.6f}",
            "roi": f"{match.roi.x},{match.roi.y},{match.roi.width},{match.roi.height}",
            "screen": screen,
            "slot": match.slot_id,
        }
        if source is not None:
            recognition_fields["source"] = source
        self.runtime.deps.logger.event("recognition", **recognition_fields)


def _stable_scan(
    self,
    screen: str,
    completed_slot_ids: set[str],
    initial_samples: tuple[FrameSample, ...] = (),
) -> tuple[InventoryMatch, ...]:
    self.runtime.transition(RunState.SCANNING_TOP if screen == "top" else RunState.SCANNING_BOTTOM)
    mark = self.runtime.performance_mark()
    deadline = self.runtime.active_monotonic() + self.runtime.config.timing.scan_timeout_ms / 1000
    previous_key: tuple[tuple[str, str], ...] | None = None
    stable = 0
    frames = 0
    outcome = "timeout"
    skipped: dict[tuple[str, str, str, str], tuple[int, float]] = {}
    cached_samples = list(initial_samples)
    cached_frames_available = len(cached_samples)
    cached_frames_used = 0
    cached_stable_suffix: int | None = None
    fresh_frames = 0
    cache_outcome = "none" if not cached_samples else "pending"
    cache_wait_ms = 0.0
    reuse_capture_schedule = bool(cached_samples)
    next_capture_not_before: float | None = None
    try:
        while self.runtime.active_monotonic() <= deadline:
            if cached_samples:
                sample = cached_samples.pop(0)
                frame = sample.frame
                captured_at = sample.captured_at
                cached_frames_used += 1
                source = "scroll_settle_cache"
            else:
                if reuse_capture_schedule and next_capture_not_before is not None:
                    remaining = next_capture_not_before - self.runtime.active_monotonic()
                    if remaining > 0:
                        self.runtime.control.checkpoint()
                        self.runtime.deps.clock.sleep(remaining)
                        cache_wait_ms += remaining * 1000
                frame = self.runtime.capture()
                captured_at = self.runtime.active_monotonic()
                fresh_frames += 1
                source = "scroll_cache_fallback" if reuse_capture_schedule else None
            matches = self._detect_actionable_inventory(
                frame,
                screen,
                completed_slot_ids,
                skipped,
            )
            frames += 1
            key = tuple((match.slot_id, match.target_id) for match in matches)
            if key == previous_key:
                stable += 1
            else:
                previous_key = key
                stable = 1

            self._log_inventory_frame(screen, matches, stable, source=source)

            if stable >= self.runtime.config.timing.stable_frames:
                if reuse_capture_schedule:
                    cache_outcome = "hit" if fresh_frames == 0 else "suffix_fallback"
                    if cached_stable_suffix is None:
                        cached_stable_suffix = stable
                outcome = "stable"
                return matches
            self.runtime.control.checkpoint()
            if reuse_capture_schedule:
                if not cached_samples and cached_stable_suffix is None:
                    cached_stable_suffix = stable
                next_capture_not_before = (
                    captured_at + self.runtime.config.timing.poll_interval_ms / 1000
                )
            else:
                self.runtime.deps.clock.sleep(self.runtime.config.timing.poll_interval_ms / 1000)
        if reuse_capture_schedule:
            cache_outcome = "timeout"
        raise StopExecution(
            StopReason.RECOGNITION_TIMEOUT,
            f"timeout waiting for stable {screen} inventory scan",
        )
    finally:
        if cache_outcome == "pending":
            cache_outcome = "interrupted"
        self._log_inventory_skip_summaries(skipped)
        self.runtime.log_performance_stage(
            mark,
            "inventory_scan",
            screen=screen,
            frames=frames,
            stable=stable,
            outcome=outcome,
            cache_outcome=cache_outcome,
            cached_frames_available=cached_frames_available,
            cached_frames_used=cached_frames_used,
            cached_stable_suffix=cached_stable_suffix or 0,
            fresh_frames=fresh_frames,
            cache_wait_ms=f"{cache_wait_ms:.3f}",
        )


def _scan_viewport(
    self,
    screen: str,
    completed_slot_ids: set[str],
    initial_samples: tuple[FrameSample, ...] = (),
) -> frozenset[str]:
    mandatory_targets_found: set[str] = set()
    pending: tuple[InventoryMatch, ...] | None = None
    if screen == "top" and not completed_slot_ids and self._pending_top_scan is not None:
        pending = self._pending_top_scan
        self._pending_top_scan = None
        self.runtime.transition(RunState.SCANNING_TOP)
        self.runtime.deps.logger.event(
            "inventory_scan_reused",
            screen="top",
            source="after_refresh_balance",
            targets=len(pending),
        )
    while True:
        matches = pending
        pending = None
        if matches is None:
            matches = self._stable_scan(
                screen,
                completed_slot_ids,
                initial_samples=initial_samples,
            )
            initial_samples = ()
        if not matches:
            return frozenset(mandatory_targets_found)
        match = matches[0]
        if match.target_id in self._mandatory_target_ids:
            mandatory_targets_found.add(match.target_id)
        self._purchase(match)
        completed_slot_ids.add(match.slot_id)


def _purchase(self, match: InventoryMatch) -> None:
    self.runtime.transition(RunState.PURCHASING)
    self.runtime.click(f"buy:{match.target_id}:{match.slot_id}", match.buy_point)
    self.runtime.wait_stable_observation(
        f"confirm_dialog:{match.target_id}",
        lambda frame: self.runtime.deps.vision.confirm_dialog(frame, match.target_id),
        self.runtime.config.timing.dialog_timeout_ms,
    )
    self.runtime.click("confirm_purchase", self.runtime.config.points["confirm_button"])

    deadline = self.runtime.active_monotonic() + self.runtime.config.timing.purchase_result_timeout_ms / 1000
    insufficient_stable = 0
    button_stable = 0
    while self.runtime.active_monotonic() <= deadline:
        outcome = self.runtime.vision_call(
            self.runtime.deps.vision.purchase_outcome,
            self.runtime.capture(),
            match.target_id,
            match.roi,
        )
        if outcome is PurchaseOutcome.INSUFFICIENT_FUNDS:
            insufficient_stable += 1
        else:
            insufficient_stable = 0
        if outcome is PurchaseOutcome.SUCCESS_BUTTON:
            button_stable += 1
        else:
            button_stable = 0
        self.runtime.deps.logger.event(
            "purchase_result",
            target=match.target_id,
            slot=match.slot_id,
            outcome=outcome.value,
            insufficient_stable=insufficient_stable,
            button_stable=button_stable,
        )
        if outcome is PurchaseOutcome.INSUFFICIENT_FUNDS:
            if insufficient_stable >= self.runtime.config.timing.stable_frames:
                raise StopExecution(StopReason.PURCHASE_FUNDS_INSUFFICIENT)
        if outcome is PurchaseOutcome.SUCCESS or (
            outcome is PurchaseOutcome.SUCCESS_BUTTON
            and button_stable >= self.runtime.config.timing.stable_frames
        ):
            updated = self.runtime.publisher.mutate(
                lambda snapshot: snapshot.with_incremented_target(match.target_id)
            )
            self.runtime.deps.logger.event(
                "purchase_counted",
                target=match.target_id,
                evidence=outcome.value,
                refresh_spent=updated.refresh_spent,
                count=next(
                    tally.acquired for tally in updated.targets if tally.target_id == match.target_id
                ),
            )
            return
        self.runtime.control.checkpoint()
        self.runtime.deps.clock.sleep(self.runtime.config.timing.poll_interval_ms / 1000)
    raise StopExecution(StopReason.PURCHASE_RESULT_AMBIGUOUS)
