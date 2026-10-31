from __future__ import annotations
from dataclasses import dataclass, replace
from math import isfinite
from e7auto.core.domain import RunState, StopReason
from e7auto.features.shop.contracts import InventoryMatch, PurchaseOutcome, ScrollMovementObservation
from e7auto.runtime.stop_control import StopExecution
from e7auto.features.shop.scrolling import FrameSample

from e7auto.runtime.context import RuntimeContext
from e7auto.runtime.action_retry import Transition, click_and_wait, observation_signature
from .contracts import ShopVisionPort
from .state import ShopState
from .readiness import SceneSampleEvidence, SceneWindow, format_row_regions, format_row_changes


@dataclass(frozen=True, slots=True)
class _TopContinuation:
    window: SceneWindow
    previous_key: tuple[tuple[str, str], ...]
    stable: int
    epoch: int


@dataclass(frozen=True, slots=True)
class _PurchaseContext:
    epoch: int
    generation: int
    geometry: tuple[object, ...]
    layout: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _PostPurchaseScope:
    screen: str
    slot_ids: frozenset[str]
    after_slot: str
    context: _PurchaseContext


class InventoryScanner:
    def __init__(self, runtime: RuntimeContext[ShopVisionPort], state: ShopState):
        self.runtime, self.state = runtime, state
        self.reference: FrameSample | None = None
        self._evidence_epoch = 0
        self._top_continuation: _TopContinuation | None = None
        self._scroll_samples: tuple[FrameSample, ...] = ()
        self._scroll_epoch = -1
        self._scene_comparisons_reused = 0

    def invalidate(self) -> None:
        self.reference = None
        self.state.pending_top_scan = None
        self._evidence_epoch += 1
        self._top_continuation = None
        self._scroll_samples = ()
        self._scroll_epoch = -1

    def _purchase_context(self) -> _PurchaseContext:
        return _PurchaseContext(
            self._evidence_epoch, self.runtime.network_recovery_generation,
            (self.runtime.window, self.runtime.transform, self.runtime.baseline_bounds),
            (self.runtime.config.slots, self.runtime.config.rois["inventory_content"], self.state.enabled_target_ids),
        )

    def _scope_invalid_reason(self, scope: _PostPurchaseScope) -> str | None:
        current = self._purchase_context()
        if current.generation != scope.context.generation:
            return "network_recovery"
        if current.epoch != scope.context.epoch:
            return "inventory_invalidated"
        if current.geometry != scope.context.geometry or current.layout != scope.context.layout:
            return "geometry_or_selection_changed"
        return None

    def _log_scope_fallback(self, scope: _PostPurchaseScope, reason: str) -> None:
        self.runtime.deps.logger.event(
            "post_purchase_scope_fallback", screen=scope.screen, after_slot=scope.after_slot,
            slot_ids=",".join(sorted(scope.slot_ids)), reason=reason,
        )

    def _evidence_is_current(self, captured_at: float, generation: int, epoch: int) -> bool:
        age_ms = (self.runtime.active_monotonic() - captured_at) * 1000
        return (epoch == self._evidence_epoch and generation == self.runtime.network_recovery_generation
                and isfinite(age_ms) and 0 <= age_ms <= self.runtime.config.inventory_readiness.reference_max_age_ms)

    def retain_top_continuation(self, window: SceneWindow, previous_key: tuple[tuple[str, str], ...] | None,
                                stable: int) -> int:
        self._top_continuation = None
        if (window.count > 0 and window.previous is not None and previous_key is not None and stable > 0
                and self._evidence_is_current(window.previous_at, window.generation, self._evidence_epoch)):
            self._top_continuation = _TopContinuation(window, previous_key, stable, self._evidence_epoch)
            return window.count
        return 0

    def retain_scroll_evidence(self, samples: tuple[FrameSample, ...]) -> None:
        self._scroll_samples = samples
        self._scroll_epoch = self._evidence_epoch

    def wait_for_sample(self, next_capture_at: float | None, deadline: float) -> float:
        self.runtime.control.checkpoint()
        wait = (0.0 if next_capture_at is None else
                max(0.0, min(next_capture_at, deadline) - self.runtime.active_monotonic()))
        if wait:
            self.runtime.deps.clock.sleep(wait)
        self.runtime.control.checkpoint()
        return wait

    def new_scene_window(self) -> SceneWindow:
        return SceneWindow(max(self.runtime.config.timing.stable_frames,
                               self.runtime.config.inventory_readiness.minimum_frames), self.runtime.config.scroll,
                           self.runtime.config.inventory_readiness)

    def observe_scene(self, window: SceneWindow, frame: object, captured_at: float,
                      generation: int, screen: str, evidence: SceneSampleEvidence | None = None, *,
                      slot_ids: frozenset[str] | None = None) -> bool:
        if generation != self.runtime.network_recovery_generation:
            window.reset("network_recovery")
            self.invalidate()
            return False
        if slot_ids is not None:
            evidence = None  # Whole-viewport certificates cannot authorize a local window.
        if evidence is not None and not (evidence.frame is frame and evidence.captured_at == captured_at
                                        and evidence.generation == generation and evidence.screen == screen):
            evidence = None
        if slot_ids is not None:
            scene = self.runtime.vision_call(
                lambda current: self.runtime.deps.vision.inventory_scene_state(current, screen, slot_ids=slot_ids), frame)
        else:
            scene = ("ready" if evidence is not None else
                     self.runtime.vision_call(self.runtime.deps.vision.inventory_scene_state, frame, screen))
        self.runtime.control.checkpoint()

        def measure(before: object, after: object) -> ScrollMovementObservation:
            if slot_ids is not None:
                return self.runtime.vision_call(
                    lambda a, b: self.runtime.deps.vision.inventory_scene_stability(a, b, screen, slot_ids=slot_ids),
                    before, after)
            if evidence is not None and after is frame:
                if (before is window.previous and evidence.previous_at == window.previous_at
                        and evidence.pair is not None):
                    self._scene_comparisons_reused += 1
                    return evidence.pair
                if (before is window.first and evidence.first_at == window.first_at
                        and evidence.span is not None):
                    self._scene_comparisons_reused += 1
                    return evidence.span
            return self.runtime.vision_call(self.runtime.deps.vision.inventory_scene_stability, before, after, screen)

        ready = window.observe(frame, captured_at, generation, scene, measure)
        self.runtime.control.checkpoint()
        return ready

    def reference_is_valid(self) -> bool:
        sample = self.reference
        return (sample is not None and sample.generation == self.runtime.network_recovery_generation
                and 0 <= (self.runtime.active_monotonic() - sample.captured_at) * 1000
                <= self.runtime.config.inventory_readiness.reference_max_age_ms)

    def take_scroll_reference(self) -> FrameSample:
        if not self.reference_is_valid():
            self.invalidate()
            raise StopExecution(StopReason.RECOGNITION_TIMEOUT, "top inventory reference is stale or not ready")
        sample = self.reference
        self.invalidate()
        assert sample is not None
        return sample

    @staticmethod
    def record_inventory_skip(
        skipped: dict[tuple[str, str, str, str], tuple[int, float]],
        match: InventoryMatch,
        screen: str,
        reason: str,
    ) -> None:
        key = (screen, match.slot_id, match.target_id, reason)
        count, maximum = skipped.get(key, (0, 0.0))
        skipped[key] = (count + 1, max(maximum, match.confidence))


    def log_inventory_skip_summaries(
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


    def detect_actionable_inventory(
        self,
        frame: object,
        screen: str,
        completed_slot_ids: set[str],
        skipped: dict[tuple[str, str, str, str], tuple[int, float]],
        *, slot_ids: frozenset[str] | None = None,
    ) -> tuple[InventoryMatch, ...]:
        excluded = frozenset(completed_slot_ids)
        if slot_ids is not None:
            excluded |= frozenset(slot.slot_id for slot in self.runtime.config.slots
                                  if slot.screen == screen and slot.slot_id not in slot_ids)
        detected = self.runtime.vision_call(
            self.runtime.deps.vision.scan_inventory,
            frame,
            screen,
            self.state.enabled_target_ids,
            excluded,
        )
        matches: list[InventoryMatch] = []
        for match in detected:
            if slot_ids is not None and match.slot_id not in slot_ids:
                continue
            if match.is_purchased:
                self.record_inventory_skip(
                    skipped,
                    match,
                    screen,
                    "already_purchased_before_run",
                )
                continue
            if match.target_id not in self.state.enabled_target_ids:
                self.record_inventory_skip(
                    skipped,
                    match,
                    screen,
                    "not_enabled_for_run",
                )
                continue
            if match.slot_id in completed_slot_ids:
                self.record_inventory_skip(
                    skipped,
                    match,
                    screen,
                    "already_purchased_in_inventory",
                )
                continue
            matches.append(match)
        return tuple(matches)


    def log_inventory_frame(
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


    def stable_scan(
        self,
        screen: str,
        completed_slot_ids: set[str],
        initial_samples: tuple[FrameSample, ...] = (),
        *, scope: _PostPurchaseScope | None = None,
    ) -> tuple[InventoryMatch, ...]:
        if scope is not None and (scope.screen != screen or not scope.slot_ids or initial_samples):
            raise ValueError("Local purchase scans require a nonempty same-screen scope and fresh frames")
        requested_scope = scope
        scope_fallback_reason = None
        self.runtime.transition(RunState.SCANNING_TOP if screen == "top" else RunState.SCANNING_BOTTOM)
        mark = self.runtime.performance_mark()
        deadline = self.runtime.active_monotonic() + self.runtime.config.timing.scan_timeout_ms / 1000
        previous_key: tuple[tuple[str, str], ...] | None = None
        stable = 0
        frames = 0
        outcome = "timeout"
        skipped: dict[tuple[str, str, str, str], tuple[int, float]] = {}
        cached_samples = list(initial_samples)
        certified_scroll = (screen == "bottom" and bool(initial_samples)
                            and initial_samples is self._scroll_samples)
        scroll_epoch = self._scroll_epoch
        self._scroll_samples, self._scroll_epoch = (), -1
        cached_frames_available = len(cached_samples)
        cached_frames_used = 0
        cached_stable_suffix: int | None = None
        fresh_frames = 0
        cache_outcome = "none" if not cached_samples else "pending"
        cache_wait_ms = 0.0
        sample_wait_ms = 0.0
        reuse_capture_schedule = bool(cached_samples)
        next_capture_not_before: float | None = None
        window = self.new_scene_window()
        continuation_frames = 0
        if screen == "top":
            continuation, self._top_continuation = self._top_continuation, None
            if (scope is None and continuation is not None and not completed_slot_ids and not cached_samples
                    and self._evidence_is_current(continuation.window.previous_at,
                                                  continuation.window.generation, continuation.epoch)):
                window = continuation.window
                previous_key, stable = continuation.previous_key, continuation.stable
                continuation_frames = window.count
                next_capture_not_before = window.previous_at + self.runtime.config.timing.poll_interval_ms / 1000
        scene_frames_reused = 0
        comparisons_before = self._scene_comparisons_reused
        self.reference = None

        def discard_scope(reason: str) -> None:
            nonlocal scope, previous_key, stable, next_capture_not_before, scope_fallback_reason
            assert scope is not None
            self._log_scope_fallback(scope, reason)
            scope = None
            scope_fallback_reason = reason
            window.reset("post_purchase_scope_invalidated")
            previous_key, stable, next_capture_not_before = None, 0, None

        try:
            while self.runtime.active_monotonic() <= deadline:
                self.runtime.control.checkpoint()
                if scope is not None and (reason := self._scope_invalid_reason(scope)) is not None:
                    discard_scope(reason)
                if (certified_scroll and cached_samples
                        and not self._evidence_is_current(initial_samples[-1].captured_at,
                                                         initial_samples[-1].generation, scroll_epoch)):
                    cached_samples.clear()
                    window.reset("invalidated_scroll_evidence")
                    previous_key, stable = None, 0
                    certified_scroll = False
                evidence = None
                if cached_samples:
                    sample = cached_samples.pop(0)
                    frame = sample.frame
                    captured_at = sample.captured_at
                    cached_frames_used += 1
                    source = "scroll_settle_cache"
                    generation = sample.generation
                    if certified_scroll:
                        evidence = sample.scene_evidence
                else:
                    waited = self.wait_for_sample(next_capture_not_before, deadline) * 1000
                    sample_wait_ms += waited
                    if reuse_capture_schedule:
                        cache_wait_ms += waited
                    if self.runtime.active_monotonic() >= deadline:
                        break
                    generation = self.runtime.network_recovery_generation
                    frame = self.runtime.capture()
                    captured_at = self.runtime.active_monotonic()
                    fresh_frames += 1
                    source = "scroll_cache_fallback" if reuse_capture_schedule else None
                if scope is not None and (reason := self._scope_invalid_reason(scope)) is not None:
                    discard_scope(reason)
                    continue  # Do not mix a capture interrupted by recovery with the new full window.
                next_capture_not_before = captured_at + self.runtime.config.timing.poll_interval_ms / 1000
                if evidence is not None:
                    scene_frames_reused += 1
                slot_ids = None if scope is None else scope.slot_ids
                scene_ready = self.observe_scene(window, frame, captured_at, generation, screen, evidence,
                                                 slot_ids=slot_ids)
                if window.count <= 1:
                    previous_key, stable = None, 0
                if window.count == 0:
                    self.runtime.control.checkpoint()
                    continue
                matches = self.detect_actionable_inventory(
                    frame,
                    screen,
                    completed_slot_ids,
                    skipped,
                    slot_ids=slot_ids,
                )
                frames += 1
                key = tuple((match.slot_id, match.target_id) for match in matches)
                if key == previous_key:
                    stable += 1
                else:
                    previous_key = key
                    stable = 1

                self.log_inventory_frame(screen, matches, stable, source=source)

                self.runtime.control.checkpoint()
                if self.runtime.active_monotonic() > deadline:
                    break
                if scope is not None and (reason := self._scope_invalid_reason(scope)) is not None:
                    discard_scope(reason)
                    continue
                if (evidence is not None
                        and not self._evidence_is_current(initial_samples[-1].captured_at,
                                                         initial_samples[-1].generation, scroll_epoch)):
                    cached_samples.clear()
                    window.reset("invalidated_scroll_evidence")
                    previous_key, stable = None, 0
                    certified_scroll = False
                    continue
                if stable >= self.runtime.config.timing.stable_frames and scene_ready:
                    if reuse_capture_schedule:
                        cache_outcome = "hit" if fresh_frames == 0 else "suffix_fallback"
                        if cached_stable_suffix is None:
                            cached_stable_suffix = stable
                    outcome = "stable"
                    if screen == "top" and scope is None:
                        self.reference = FrameSample(frame, captured_at, generation)
                    return matches
                self.runtime.control.checkpoint()
                if reuse_capture_schedule and not cached_samples and cached_stable_suffix is None:
                    cached_stable_suffix = stable
            if reuse_capture_schedule:
                cache_outcome = "timeout"
            raise StopExecution(
                StopReason.RECOGNITION_TIMEOUT,
                f"timeout waiting for stable {screen} inventory scan: {window.reason}",
            )
        finally:
            if cache_outcome == "pending":
                cache_outcome = "interrupted"
            self.log_inventory_skip_summaries(skipped)
            self.runtime.log_performance_stage(
                mark,
                "inventory_scan",
                screen=screen,
                post_purchase_policy="scoped_followup_v1",
                scan_scope="full_viewport" if scope is None else "post_purchase_slots",
                requested_slot_ids="all" if requested_scope is None else ",".join(sorted(requested_scope.slot_ids)),
                scan_slot_ids="all" if scope is None else ",".join(sorted(scope.slot_ids)),
                scope_fallback_reason=scope_fallback_reason or "none",
                frames=frames,
                stable=stable,
                outcome=outcome,
                cache_outcome=cache_outcome,
                cached_frames_available=cached_frames_available,
                cached_frames_used=cached_frames_used,
                cached_stable_suffix=cached_stable_suffix or 0,
                fresh_frames=fresh_frames,
                cache_wait_ms=f"{cache_wait_ms:.3f}",
                sample_wait_ms=f"{sample_wait_ms:.3f}",
                poll_schedule="capture_interval",
                top_continuation_frames=continuation_frames,
                scene_frames_reused=scene_frames_reused,
                scene_comparisons_reused=self._scene_comparisons_reused - comparisons_before,
                scene_ready_frames=window.count,
                scene_ready_reason=window.reason,
                scene_maximum_row_change=window.maximum_change,
                scene_row_regions=format_row_regions(window.pair_rows),
                scene_pair_row_changes=format_row_changes(window.pair_rows),
                scene_span_row_changes=format_row_changes(window.span_rows),
            )


    def _scope_after_purchase(self, screen: str, matches: tuple[InventoryMatch, ...],
                              before: _PurchaseContext) -> _PostPurchaseScope | None:
        slots = sorted((slot for slot in self.runtime.config.slots if slot.screen == screen),
                       key=lambda slot: slot.order)
        by_id = {slot.slot_id: slot for slot in slots}
        # A suffix may only be derived from a correctly ordered, calibrated scan.
        if not matches or any(
            item.slot_id not in by_id or
            (item.slot_order, item.roi, item.buy_point) !=
            (by_id[item.slot_id].order, by_id[item.slot_id].item_roi, by_id[item.slot_id].buy_point)
            for item in matches
        ) or tuple(item.slot_order for item in matches) != tuple(sorted({item.slot_order for item in matches})):
            return None
        match = matches[0]
        if screen == "top" and len(matches) == 1:
            return None  # The last top target still needs a full, clean scroll reference.
        if screen == "bottom" and match.slot_order <= max(
                slot.order for slot in self.runtime.config.slots if slot.screen == "top"):
            return None
        remaining = frozenset(slot.slot_id for slot in slots if slot.order > match.slot_order)
        if any(slot.item_roi.y < self.runtime.config.inventory_readiness.reward_overlay_roi.bottom
               for slot in slots if slot.slot_id in remaining):
            return None
        scope = _PostPurchaseScope(screen, remaining, match.slot_id, replace(before, epoch=before.epoch + 1))
        # purchase() invalidates once before its own inputs. Any additional
        # invalidation, recovery or geometry change revokes the earlier scan proof.
        if (reason := self._scope_invalid_reason(scope)) is not None:
            self._log_scope_fallback(scope, reason)
            return None
        return scope

    def _finish_bottom_purchase(self, scope: _PostPurchaseScope) -> bool:
        if scope.screen != "bottom" or scope.slot_ids:
            raise ValueError("Fast purchase completion requires an exhausted bottom viewport")
        self.runtime.transition(RunState.SCANNING_BOTTOM)
        mark = self.runtime.performance_mark()
        deadline = self.runtime.active_monotonic() + self.runtime.config.timing.scan_timeout_ms / 1000
        next_capture_at = None
        frames = stable = 0
        outcome = "interrupted"
        try:
            while self.runtime.active_monotonic() <= deadline:
                self.runtime.control.checkpoint()
                if (reason := self._scope_invalid_reason(scope)) is not None:
                    self._log_scope_fallback(scope, reason)
                    outcome = "fallback"
                    return False
                self.wait_for_sample(next_capture_at, deadline)
                if self.runtime.active_monotonic() >= deadline:
                    break
                frame = self.runtime.capture()
                captured_at = self.runtime.active_monotonic()
                frames += 1
                if (reason := self._scope_invalid_reason(scope)) is not None:
                    self._log_scope_fallback(scope, reason)
                    outcome = "fallback"
                    return False
                ready = self.runtime.vision_call(self.runtime.deps.vision.shop_ready, frame)
                stable = stable + 1 if ready is not None else 0
                self.runtime.control.checkpoint()
                if (reason := self._scope_invalid_reason(scope)) is not None:
                    self._log_scope_fallback(scope, reason)
                    outcome = "fallback"
                    return False
                if self.runtime.active_monotonic() > deadline:
                    break
                if stable >= self.runtime.config.timing.stable_frames:
                    outcome = "ready"
                    self.runtime.deps.logger.event(
                        "bottom_purchase_fast_finish", after_slot=scope.after_slot,
                        generation=scope.context.generation, stable=stable,
                    )
                    return True
                next_capture_at = captured_at + self.runtime.config.timing.poll_interval_ms / 1000
            outcome = "timeout"
            raise StopExecution(StopReason.RECOGNITION_TIMEOUT, "shop controls did not settle after the last bottom purchase")
        finally:
            self.runtime.log_performance_stage(
                mark, "post_purchase_finish", screen="bottom", scan_scope="controls_only",
                post_purchase_policy="scoped_followup_v1",
                after_slot=scope.after_slot, frames=frames, stable=stable, outcome=outcome,
            )

    def scan_viewport(
        self,
        screen: str,
        completed_slot_ids: set[str],
        initial_samples: tuple[FrameSample, ...] = (),
    ) -> frozenset[str]:
        mandatory_targets_found: set[str] = set()
        pending: tuple[InventoryMatch, ...] | None = None
        scope: _PostPurchaseScope | None = None
        if screen == "top" and not self.reference_is_valid():
            # A partial refresh continuation intentionally has no completed reference yet.
            self.reference = None
            self.state.pending_top_scan = None
        if (screen == "top" and not completed_slot_ids and self.state.pending_top_scan is not None
                and self.reference_is_valid()):
            pending = self.state.pending_top_scan
            self.state.pending_top_scan = None
            self.runtime.transition(RunState.SCANNING_TOP)
            self.runtime.deps.logger.event(
                "inventory_scan_reused",
                screen="top",
                source="after_refresh_balance",
                targets=len(pending),
            )
        while True:
            self.runtime.control.checkpoint()
            matches = pending
            pending = None
            if matches is None:
                matches = self.stable_scan(
                    screen,
                    completed_slot_ids,
                    initial_samples=initial_samples,
                    scope=scope,
                )
                initial_samples = ()
                if scope is not None and self._scope_invalid_reason(scope) is not None:
                    scope = None  # stable_scan already rebuilt a complete window after invalidation.
            if not matches:
                if screen == "top" and scope is not None:
                    self.runtime.deps.logger.event("post_purchase_full_reference_required", screen=screen)
                    scope = None
                    continue
                return frozenset(mandatory_targets_found)
            match = matches[0]
            if match.target_id in self.state.mandatory_target_ids:
                mandatory_targets_found.add(match.target_id)
            before_purchase = self._purchase_context()
            self.purchase(match, screen=screen)
            completed_slot_ids.add(match.slot_id)
            scope = self._scope_after_purchase(screen, matches, before_purchase)
            self.runtime.deps.logger.event(
                "post_purchase_scan_selected", screen=screen, after_slot=match.slot_id,
                post_purchase_policy="scoped_followup_v1",
                scan_scope=("full_viewport" if scope is None else
                            "post_purchase_slots" if scope.slot_ids else "controls_only"),
                slot_ids="all" if scope is None else ",".join(sorted(scope.slot_ids)),
            )
            if scope is not None and not scope.slot_ids:
                if self._finish_bottom_purchase(scope):
                    return frozenset(mandatory_targets_found)
                scope = None


    def purchase(self, match: InventoryMatch, *, screen: str | None = None) -> None:
        self.invalidate()
        self.runtime.transition(RunState.PURCHASING)
        vision = self.runtime.deps.vision
        screen = screen or next((slot.screen for slot in self.runtime.config.slots if slot.slot_id == match.slot_id), None)
        def source(frame):
            if screen is None or vision.shop_ready(frame) is None:
                return None
            matches = [current for current in vision.scan_inventory(frame, screen, frozenset({match.target_id}))
                       if current.target_id == match.target_id and current.slot_id == match.slot_id
                       and not current.is_purchased]
            return matches[0] if len(matches) == 1 else None
        click_and_wait(self.runtime, name=f"buy:{match.target_id}:{match.slot_id}", initial=match,
                       click=lambda current: self.runtime.click(f"buy:{match.target_id}:{match.slot_id}", current.buy_point),
                       source=source, observe=lambda f: Transition(vision.confirm_dialog(f, match.target_id)),
                       signature=observation_signature,
                       target_signature=lambda current: (current.target_id, current.slot_id,
                                                          current.roi.x//4, current.roi.y//4),
                       timeout_ms=self.runtime.config.timing.dialog_timeout_ms)
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
