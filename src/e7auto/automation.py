from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar

from .config import AppConfig, Point, Rect
from .domain import OverlayActivityStatus, RunState, RuntimeSnapshot, StopReason
from .ports import (
    CaptureService,
    Clock,
    HotkeyService,
    InputService,
    OverlayService,
    RuntimeEnvironment,
    TextRunLogger,
    WindowRef,
    WindowService,
)
from .vision import (
    InventoryMatch,
    Observation,
    PurchaseOutcome,
    ScrollMovementObservation,
    SkyStoneBalanceObservation,
)

T = TypeVar("T")

_REFRESH_CONFIRM_FAST_CONFIDENCE = 0.99


@dataclass(frozen=True, slots=True)
class _FrameSample:
    frame: object
    captured_at: float


class GameVision(Protocol):
    def main_shop_icon(self, frame: object) -> Observation | None: ...

    def shop_ready(self, frame: object) -> Observation | None: ...

    def shop_exit_icon(self, frame: object) -> Observation | None: ...

    def refresh_confirm_dialog(self, frame: object) -> Observation | None: ...

    def confirm_dialog(self, frame: object, target_id: str) -> Observation | None: ...

    def purchase_outcome(self, frame: object, target_id: str, item_roi: Rect) -> PurchaseOutcome: ...

    def scan_inventory(
        self,
        frame: object,
        screen: str,
        enabled_target_ids: frozenset[str] | None = None,
        excluded_slot_ids: frozenset[str] = frozenset(),
    ) -> tuple[InventoryMatch, ...]: ...

    def inventory_scroll_movement(
        self,
        before: object,
        after: object,
    ) -> ScrollMovementObservation: ...

    def inventory_scroll_stability(
        self,
        before: object,
        after: object,
    ) -> ScrollMovementObservation: ...

    def sky_stone_balance(self, frame: object) -> SkyStoneBalanceObservation | None: ...

    def network_connection_error(self, frame: object) -> Observation | None: ...

    def network_retry(self, frame: object) -> Observation | None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class StopExecution(RuntimeError):
    def __init__(self, reason: StopReason, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason.value)


class StopController:
    """Serializes stop requests with input dispatch.

    If F5 wins the lock, subsequent input is rejected. If a single input call already
    owns the lock, that call is considered dispatched and cannot be retracted.
    """

    def __init__(self) -> None:
        self._lock = threading.Condition(threading.RLock())
        self._reason: StopReason | None = None
        self._paused = False

    def request(self, reason: StopReason) -> bool:
        with self._lock:
            if self._reason is not None:
                return False
            self._reason = reason
            self._lock.notify_all()
            return True

    def pause(self) -> bool:
        with self._lock:
            if self._reason is not None:
                return False
            self._paused = True
            return True

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._lock.notify_all()

    def _wait_until_runnable(self) -> None:
        while self._paused and self._reason is None:
            self._lock.wait()
        if self._reason is not None:
            raise StopExecution(self._reason)

    @property
    def reason(self) -> StopReason | None:
        with self._lock:
            return self._reason

    def checkpoint(self) -> None:
        with self._lock:
            self._wait_until_runnable()

    def dispatch(self, action: Callable[[], T]) -> T:
        with self._lock:
            self._wait_until_runnable()
            return action()


class SnapshotPublisher:
    def __init__(self, initial: RuntimeSnapshot, callback: Callable[[RuntimeSnapshot], None]):
        self._snapshot = initial
        self._callback = callback
        self._lock = threading.Lock()
        self._final_published = False

    @property
    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return self._snapshot

    def mutate(self, mutation: Callable[[RuntimeSnapshot], RuntimeSnapshot]) -> RuntimeSnapshot:
        with self._lock:
            if self._final_published:
                return self._snapshot
            self._snapshot = mutation(self._snapshot)
            value = self._snapshot
        self._callback(value)
        return value

    def finalize(self, reason: StopReason) -> RuntimeSnapshot:
        with self._lock:
            if self._final_published:
                return self._snapshot
            self._snapshot = self._snapshot.finalized(reason)
            self._final_published = True
            value = self._snapshot
        self._callback(value)
        return value


@dataclass(slots=True)
class AutomationDependencies:
    windows: WindowService
    capture: CaptureService
    inputs: InputService
    overlay: OverlayService
    vision: GameVision
    clock: Clock
    logger: TextRunLogger
    runtime: RuntimeEnvironment


class AutomationEngine:
    def __init__(
        self,
        config: AppConfig,
        dependencies: AutomationDependencies,
        control: StopController,
        publisher: SnapshotPublisher,
        enabled_target_ids: frozenset[str],
    ) -> None:
        self._config = config
        self._deps = dependencies
        self._control = control
        self._publisher = publisher
        self._enabled_target_ids = enabled_target_ids
        self._mandatory_target_ids = frozenset(
            target.target_id for target in config.targets if not target.user_selectable
        )
        self._refresh_strategy_stage = 0
        self._refreshes_without_mandatory_target = 0
        self._consecutive_no_target_refreshes = 0
        self._window: WindowRef | None = None
        self._baseline_bounds: Rect | None = None
        self._handling_network = False
        self._network_paused_seconds = 0.0
        self._network_status_before_reconnect: OverlayActivityStatus | None = None
        self._trusted_sky_stone_balance: int | None = None
        self._pending_top_scan: tuple[InventoryMatch, ...] | None = None
        self._capture_count = 0
        self._capture_seconds = 0.0
        self._vision_calls = 0
        self._vision_seconds = 0.0

    def execute(self) -> None:
        self._prepare()
        self._enter_store()
        self._scan_until_stopped()

    def _transition(self, state: RunState) -> None:
        previous = self._publisher.snapshot.state
        self._publisher.mutate(lambda snapshot: snapshot.transitioned(state))
        self._deps.logger.event("state_transition", previous=previous.value, current=state.value)

    def _performance_mark(self) -> tuple[float, int, float, int, float]:
        return (
            time.perf_counter(),
            self._capture_count,
            self._capture_seconds,
            self._vision_calls,
            self._vision_seconds,
        )

    def _log_performance_stage(
        self,
        mark: tuple[float, int, float, int, float],
        stage: str,
        **fields: object,
    ) -> None:
        started, capture_count, capture_seconds, vision_calls, vision_seconds = mark
        self._deps.logger.event(
            "performance_stage",
            stage=stage,
            duration_ms=f"{(time.perf_counter() - started) * 1000:.3f}",
            capture_count=self._capture_count - capture_count,
            capture_ms=f"{(self._capture_seconds - capture_seconds) * 1000:.3f}",
            vision_calls=self._vision_calls - vision_calls,
            vision_ms=f"{(self._vision_seconds - vision_seconds) * 1000:.3f}",
            **fields,
        )

    def _vision_call(self, detector: Callable[..., T], *args: object) -> T:
        started = time.perf_counter()
        try:
            return detector(*args)
        finally:
            self._vision_calls += 1
            self._vision_seconds += time.perf_counter() - started

    def _invalidate_trusted_balance(self, reason: str) -> None:
        previous = self._trusted_sky_stone_balance
        self._trusted_sky_stone_balance = None
        self._pending_top_scan = None
        if previous is not None:
            self._deps.logger.event(
                "trusted_sky_stone_balance_invalidated",
                reason=reason,
                value=previous,
            )

    def _prepare(self) -> None:
        self._transition(RunState.PREPARING)
        self._control.checkpoint()
        try:
            elevated = self._deps.runtime.is_elevated()
        except Exception as exc:
            raise StopExecution(
                StopReason.PERMISSION_REQUIRED,
                f"cannot verify administrator integrity: {exc}",
            ) from exc
        if not elevated:
            raise StopExecution(
                StopReason.PERMISSION_REQUIRED,
                "automation must run with Windows administrator integrity",
            )
        try:
            window = self._deps.windows.locate_unique(
                str(self._config.executable_path), self._config.window_title
            )
            self._deps.windows.restore_and_foreground(window)
            self._deps.windows.resize_client(window, self._config.baseline_client_size)
            state = self._deps.windows.inspect(window)
        except Exception as exc:
            raise StopExecution(StopReason.WINDOW_ABNORMAL, str(exc)) from exc
        expected = self._config.baseline_client_size
        if (
            not state.exists
            or state.minimized
            or not state.foreground
            or state.client_bounds.width != expected.width
            or state.client_bounds.height != expected.height
        ):
            raise StopExecution(
                StopReason.WINDOW_ABNORMAL,
                f"client verification failed: {state}",
            )
        self._window = window
        self._baseline_bounds = state.client_bounds
        recognition_rois = tuple(self._config.rois.values()) + tuple(
            slot.item_roi for slot in self._config.slots
        )
        if not self._deps.overlay.position_and_secure(state.client_bounds, recognition_rois):
            raise StopExecution(
                StopReason.OVERLAY_CAPTURE_UNSAFE,
                "overlay capture exclusion unavailable and overlay intersects recognition ROI",
            )
        self._deps.logger.event(
            "window_prepared",
            hwnd=window.hwnd,
            client_x=state.client_bounds.x,
            client_y=state.client_bounds.y,
            client_width=state.client_bounds.width,
            client_height=state.client_bounds.height,
        )

    def _ensure_window(self) -> None:
        self._control.checkpoint()
        assert self._window is not None and self._baseline_bounds is not None
        try:
            state = self._deps.windows.inspect(self._window)
        except Exception as exc:
            raise StopExecution(StopReason.WINDOW_ABNORMAL, str(exc)) from exc
        if (
            not state.exists
            or state.minimized
            or not state.foreground
            or state.client_bounds != self._baseline_bounds
        ):
            raise StopExecution(StopReason.WINDOW_ABNORMAL, f"window changed: {state}")

    def _capture_raw(self) -> object:
        started = time.perf_counter()
        try:
            self._ensure_window()
            assert self._window is not None and self._baseline_bounds is not None
            frame = self._deps.capture.capture_client(self._window, self._baseline_bounds)
            self._control.checkpoint()
            return frame
        finally:
            self._capture_count += 1
            self._capture_seconds += time.perf_counter() - started

    def _handle_network_exception(self, frame: object) -> None:
        error_detector = getattr(self._deps.vision, "network_connection_error", None)
        retry_detector = getattr(self._deps.vision, "network_retry", None)
        if error_detector is None or retry_detector is None or self._handling_network:
            return
        if self._vision_call(error_detector, frame) is None:
            return
        self._invalidate_trusted_balance("network_recovery")
        self._handling_network = True
        recovery_started = self._deps.clock.monotonic()
        previous_status = self._publisher.snapshot.overlay_status
        self._network_status_before_reconnect = previous_status
        self._publisher.mutate(
            lambda snapshot: snapshot.with_overlay_status(
                OverlayActivityStatus.RECONNECTING
            )
        )
        try:
            self._deps.logger.event("network_error_detected", action="pause_and_save")
            while True:
                self._control.checkpoint()
                current = self._capture_raw()
                if self._vision_call(error_detector, current) is None:
                    self._deps.logger.event("network_recovered", action="already_cleared")
                    self._restore_network_status()
                    return
                retry = self._vision_call(retry_detector, current)
                if retry is not None:
                    self._deps.logger.event("network_retry_detected", confidence=f"{retry.confidence:.6f}")
                    self._click("network_retry", self._config.points["main_screen_wake"])
                    self._deps.clock.sleep(self._config.timing.poll_interval_ms / 1000)
                    if self._vision_call(error_detector, self._capture_raw()) is None:
                        self._deps.logger.event("network_recovered", action="retry_click")
                        self._restore_network_status()
                        return
                else:
                    self._deps.clock.sleep(self._config.timing.poll_interval_ms / 1000)
        finally:
            self._network_paused_seconds += (
                self._deps.clock.monotonic() - recovery_started
            )
            self._handling_network = False
            self._network_status_before_reconnect = None

    def _restore_network_status(self) -> None:
        status = self._network_status_before_reconnect
        if status is not None:
            self._publisher.mutate(
                lambda snapshot: snapshot.with_overlay_status(status)
            )

    def _active_monotonic(self) -> float:
        """Return elapsed automation time excluding completed network recovery."""

        return self._deps.clock.monotonic() - self._network_paused_seconds

    def _capture(self) -> object:
        frame = self._capture_raw()
        self._handle_network_exception(frame)
        return frame

    def _screen_point(self, point: Point) -> Point:
        assert self._baseline_bounds is not None
        return self._config.screen_point(self._baseline_bounds, point)

    def _dispatch_input(self, action: str, point: Point, callback: Callable[[], None], **fields: object) -> None:
        screen_point = self._screen_point(point)

        def guarded() -> None:
            self._ensure_window()
            try:
                self._deps.inputs.move(screen_point)
                actual = self._deps.inputs.position()
                if actual != screen_point:
                    raise StopExecution(
                        StopReason.INPUT_FAILURE,
                        f"cursor verification failed for {action}: "
                        f"expected {screen_point}, observed {actual}",
                    )
                callback()
            except StopExecution:
                raise
            except Exception as exc:
                self._deps.logger.event(
                    "input_failed",
                    action=action,
                    logical_x=point.x,
                    logical_y=point.y,
                    screen_x=screen_point.x,
                    screen_y=screen_point.y,
                    error=repr(exc),
                )
                raise StopExecution(
                    StopReason.INPUT_FAILURE,
                    f"platform input failed for {action}: {exc}",
                ) from exc
            self._deps.logger.event(
                "input",
                action=action,
                logical_x=point.x,
                logical_y=point.y,
                screen_x=screen_point.x,
                screen_y=screen_point.y,
                cursor_verified=True,
                **fields,
            )

        self._control.dispatch(guarded)

    def _click(self, action: str, point: Point, **fields: object) -> None:
        screen = self._screen_point(point)
        self._dispatch_input(
            action,
            point,
            lambda: self._deps.inputs.click(screen),
            **fields,
        )

    def _scroll_to_bottom(self) -> tuple[_FrameSample, ...]:
        mark = self._performance_mark()
        outcome = "failed"
        settle_elapsed_ms = 0
        settle_samples = 0
        stability_comparisons = 0
        early_exit_ms = 0
        try:
            before = self._capture()
            point = self._config.scroll.cursor_point
            screen = self._screen_point(point)
            delta = self._config.scroll.delta
            for index in range(self._config.scroll.repetitions):
                self._dispatch_input(
                    "scroll_bottom",
                    point,
                    lambda screen=screen, delta=delta: self._deps.inputs.scroll(screen, delta),
                    delta=delta,
                    repetition=index + 1,
                )
                if index + 1 < self._config.scroll.repetitions:
                    self._control.checkpoint()
                    self._deps.clock.sleep(self._config.scroll.interval_ms / 1000)

            scroll = self._config.scroll
            self._control.checkpoint()
            settle_started = self._active_monotonic()
            settle_deadline = settle_started + scroll.settle_ms / 1000
            self._deps.clock.sleep(scroll.minimum_settle_ms / 1000)

            previous: object | None = None
            verified_frame: object | None = None
            stable = 0
            sample_elapsed: list[str] = []
            pair_shift_y: list[str] = []
            pair_response: list[str] = []
            pair_changed_fraction: list[str] = []
            stable_counts: list[str] = []
            frame_samples: list[_FrameSample] = []

            while True:
                self._control.checkpoint()
                current = self._capture()
                captured_at = self._active_monotonic()
                frame_samples.append(_FrameSample(current, captured_at))
                if len(frame_samples) > self._config.timing.stable_frames:
                    del frame_samples[0]
                settle_samples += 1
                settle_elapsed_ms = max(
                    0,
                    round((captured_at - settle_started) * 1000),
                )
                sample_elapsed.append(str(settle_elapsed_ms))

                if previous is None:
                    pair_shift_y.append("na")
                    pair_response.append("na")
                    pair_changed_fraction.append("na")
                else:
                    observation = self._vision_call(
                        self._deps.vision.inventory_scroll_stability,
                        previous,
                        current,
                    )
                    stability_comparisons += 1
                    pair_shift_y.append(f"{observation.phase_shift_y:.3f}")
                    pair_response.append(f"{observation.phase_response:.6f}")
                    pair_changed_fraction.append(
                        f"{observation.changed_fraction:.6f}"
                    )
                    if (
                        abs(observation.phase_shift_y)
                        <= scroll.maximum_pairwise_shift_px
                        and observation.phase_response >= scroll.minimum_phase_response
                    ):
                        stable += 1
                    else:
                        stable = 0
                    if stable >= scroll.stable_observations:
                        verified_frame = current

                stable_counts.append(str(stable))
                if verified_frame is not None:
                    break

                previous = current
                remaining = settle_deadline - self._active_monotonic()
                if remaining <= 1e-9:
                    break
                self._control.checkpoint()
                self._deps.clock.sleep(
                    min(scroll.settle_poll_interval_ms / 1000, remaining)
                )

            early_exit_ms = max(0, scroll.settle_ms - settle_elapsed_ms)
            trace_fields: dict[str, object] = {
                "minimum_ms": scroll.minimum_settle_ms,
                "poll_ms": scroll.settle_poll_interval_ms,
                "maximum_ms": scroll.settle_ms,
                "required_stable_observations": scroll.stable_observations,
                "shift_tolerance_px": f"{scroll.maximum_pairwise_shift_px:.3f}",
                "minimum_phase_response": f"{scroll.minimum_phase_response:.6f}",
                "downsample_factor": scroll.downsample_factor,
                "sample_count": settle_samples,
                "stability_comparisons": stability_comparisons,
                "sample_elapsed_ms": ",".join(sample_elapsed),
                "pair_shift_y": ",".join(pair_shift_y),
                "pair_response": ",".join(pair_response),
                "pair_changed_fraction": ",".join(pair_changed_fraction),
                "stable_counts": ",".join(stable_counts),
                "settle_elapsed_ms": settle_elapsed_ms,
                "early_exit_ms": early_exit_ms,
            }
            if verified_frame is None:
                self._deps.logger.event(
                    "scroll_settle_trace",
                    outcome="timeout",
                    total_phase_shift_y="na",
                    total_phase_response="na",
                    total_changed_fraction="na",
                    **trace_fields,
                )
                raise StopExecution(
                    StopReason.SCROLL_VERIFICATION_FAILED,
                    "inventory did not become visually stable before the calibrated "
                    f"{scroll.settle_ms} ms maximum",
                )

            movement = self._vision_call(
                self._deps.vision.inventory_scroll_movement,
                before,
                verified_frame,
            )
            self._deps.logger.event(
                "scroll_settle_trace",
                outcome="stable",
                total_phase_shift_y=f"{movement.phase_shift_y:.3f}",
                total_phase_response=f"{movement.phase_response:.6f}",
                total_changed_fraction=f"{movement.changed_fraction:.6f}",
                **trace_fields,
            )
            self._deps.logger.event(
                "scroll_verified",
                phase_shift_x=f"{movement.phase_shift_x:.3f}",
                phase_shift_y=f"{movement.phase_shift_y:.3f}",
                phase_response=f"{movement.phase_response:.6f}",
                mean_absolute_difference=f"{movement.mean_absolute_difference:.6f}",
                maximum_difference=movement.maximum_difference,
                changed_fraction=f"{movement.changed_fraction:.6f}",
                difference_threshold=scroll.difference_threshold,
                settle_elapsed_ms=settle_elapsed_ms,
                settle_maximum_ms=scroll.settle_ms,
                early_exit_ms=early_exit_ms,
                sample_count=settle_samples,
                stability_comparisons=stability_comparisons,
            )
            if (
                movement.phase_shift_y >= -scroll.minimum_upward_shift_px
                or movement.changed_fraction <= scroll.minimum_changed_fraction
            ):
                raise StopExecution(
                    StopReason.SCROLL_VERIFICATION_FAILED,
                    "inventory did not reach the calibrated bottom displacement gate: "
                    f"phase_shift_y={movement.phase_shift_y:.3f}, "
                    f"changed_fraction={movement.changed_fraction:.6f}",
                )
            outcome = "verified"
            return tuple(frame_samples)
        finally:
            self._log_performance_stage(
                mark,
                "scroll_to_bottom",
                outcome=outcome,
                repetitions=self._config.scroll.repetitions,
                interval_ms=self._config.scroll.interval_ms,
                settle_ms=self._config.scroll.settle_ms,
                settle_actual_ms=settle_elapsed_ms,
                settle_samples=settle_samples,
                stability_comparisons=stability_comparisons,
                early_exit_ms=early_exit_ms,
            )

    def _wait_stable_observation(
        self,
        name: str,
        detector: Callable[[object], Observation | None],
        timeout_ms: int,
    ) -> Observation:
        deadline = self._active_monotonic() + timeout_ms / 1000
        stable = 0
        latest: Observation | None = None
        while self._active_monotonic() <= deadline:
            observation = self._vision_call(detector, self._capture())
            if observation is None:
                stable = 0
                self._deps.logger.event("recognition", object=name, detected=False)
            else:
                stable += 1
                latest = observation
                self._deps.logger.event(
                    "recognition",
                    object=name,
                    detected=True,
                    confidence=f"{observation.confidence:.6f}",
                    roi=f"{observation.roi.x},{observation.roi.y},{observation.roi.width},{observation.roi.height}",
                    stable=stable,
                )
                if stable >= self._config.timing.stable_frames:
                    return observation
            self._control.checkpoint()
            self._deps.clock.sleep(self._config.timing.poll_interval_ms / 1000)
        raise StopExecution(StopReason.RECOGNITION_TIMEOUT, f"timeout waiting for {name}")

    def _enter_store(self) -> None:
        self._invalidate_trusted_balance("shop_entry")
        self._transition(RunState.ENTERING_STORE)
        main_shop = self._wait_stable_observation(
            "main_shop_icon",
            self._deps.vision.main_shop_icon,
            self._config.timing.entry_timeout_ms,
        )
        self._click("open_shop", main_shop.anchor)
        self._wait_stable_observation(
            "shop_refresh_button",
            self._deps.vision.shop_ready,
            self._config.timing.entry_timeout_ms,
        )
        self._publisher.mutate(
            lambda snapshot: snapshot.with_overlay_status(
                OverlayActivityStatus.REFRESHING
            )
        )

    def _exit_store(self) -> None:
        self._invalidate_trusted_balance("shop_exit")
        self._wait_stable_observation(
            "shop_exit_icon",
            self._deps.vision.shop_exit_icon,
            self._config.timing.entry_timeout_ms,
        )
        self._click("exit_shop", self._config.points["shop_exit_button"])
        self._wait_stable_observation(
            "main_shop_icon",
            self._deps.vision.main_shop_icon,
            self._config.timing.entry_timeout_ms,
        )

    def _interruptible_wait(self, seconds: int) -> None:
        deadline = self._deps.clock.monotonic() + seconds
        while True:
            self._ensure_window()
            remaining = deadline - self._deps.clock.monotonic()
            if remaining <= 0:
                return
            self._deps.clock.sleep(min(1.0, remaining))
            self._control.checkpoint()

    @staticmethod
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
            self._deps.logger.event(
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
        detected = self._vision_call(
            self._deps.vision.scan_inventory,
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
        self._deps.logger.event("inventory_scan", **fields)
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
            self._deps.logger.event("recognition", **recognition_fields)

    def _stable_scan(
        self,
        screen: str,
        completed_slot_ids: set[str],
        initial_samples: tuple[_FrameSample, ...] = (),
    ) -> tuple[InventoryMatch, ...]:
        self._transition(RunState.SCANNING_TOP if screen == "top" else RunState.SCANNING_BOTTOM)
        mark = self._performance_mark()
        deadline = self._active_monotonic() + self._config.timing.scan_timeout_ms / 1000
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
            while self._active_monotonic() <= deadline:
                if cached_samples:
                    sample = cached_samples.pop(0)
                    frame = sample.frame
                    captured_at = sample.captured_at
                    cached_frames_used += 1
                    source = "scroll_settle_cache"
                else:
                    if reuse_capture_schedule and next_capture_not_before is not None:
                        remaining = next_capture_not_before - self._active_monotonic()
                        if remaining > 0:
                            self._control.checkpoint()
                            self._deps.clock.sleep(remaining)
                            cache_wait_ms += remaining * 1000
                    frame = self._capture()
                    captured_at = self._active_monotonic()
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

                if stable >= self._config.timing.stable_frames:
                    if reuse_capture_schedule:
                        cache_outcome = "hit" if fresh_frames == 0 else "suffix_fallback"
                        if cached_stable_suffix is None:
                            cached_stable_suffix = stable
                    outcome = "stable"
                    return matches
                self._control.checkpoint()
                if reuse_capture_schedule:
                    if not cached_samples and cached_stable_suffix is None:
                        cached_stable_suffix = stable
                    next_capture_not_before = (
                        captured_at + self._config.timing.poll_interval_ms / 1000
                    )
                else:
                    self._deps.clock.sleep(self._config.timing.poll_interval_ms / 1000)
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
            self._log_performance_stage(
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
        initial_samples: tuple[_FrameSample, ...] = (),
    ) -> frozenset[str]:
        mandatory_targets_found: set[str] = set()
        pending: tuple[InventoryMatch, ...] | None = None
        if screen == "top" and not completed_slot_ids and self._pending_top_scan is not None:
            pending = self._pending_top_scan
            self._pending_top_scan = None
            self._transition(RunState.SCANNING_TOP)
            self._deps.logger.event(
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

    def _record_refresh_strategy_outcome(
        self,
        mandatory_targets_found: frozenset[str],
    ) -> tuple[str, int] | None:
        if mandatory_targets_found:
            self._deps.logger.event(
                "refresh_strategy_reset",
                targets=",".join(sorted(mandatory_targets_found)),
                previous_stage=self._refresh_strategy_stage + 1,
                previous_stage_refreshes=self._refreshes_without_mandatory_target,
            )
            self._refresh_strategy_stage = 0
            self._refreshes_without_mandatory_target = 0
            self._consecutive_no_target_refreshes = 0
            self._publisher.mutate(
                lambda snapshot: snapshot.with_refreshes_without_mandatory_target(0)
            )
            return None

        self._refreshes_without_mandatory_target += 1
        self._consecutive_no_target_refreshes += 1
        self._publisher.mutate(
            lambda snapshot: snapshot.with_refreshes_without_mandatory_target(
                self._consecutive_no_target_refreshes
            )
        )
        batch_limit = self._config.refresh_strategy.batch_refreshes[
            self._refresh_strategy_stage
        ]
        self._deps.logger.event(
            "refresh_strategy_progress",
            stage=self._refresh_strategy_stage + 1,
            stage_refreshes=self._refreshes_without_mandatory_target,
            batch_limit=batch_limit,
        )
        if self._refreshes_without_mandatory_target < batch_limit:
            return None
        if self._refresh_strategy_stage == len(self._config.refresh_strategy.batch_refreshes) - 1:
            self._deps.logger.event(
                "refresh_strategy_exhausted",
                stage=self._refresh_strategy_stage + 1,
                stage_refreshes=self._refreshes_without_mandatory_target,
            )
            raise StopExecution(StopReason.REFRESH_STRATEGY_EXHAUSTED)

        completed_stage = self._refresh_strategy_stage
        wait_seconds = self._config.refresh_strategy.recovery_wait_seconds[completed_stage]
        self._refresh_strategy_stage += 1
        self._refreshes_without_mandatory_target = 0
        return "exit_and_reenter", wait_seconds

    def _perform_refresh_strategy_recovery(self, mode: str, seconds: int) -> None:
        if mode == "exit_and_reenter":
            self._exit_store()
            self._publisher.mutate(
                lambda snapshot: snapshot.with_overlay_status(
                    OverlayActivityStatus.TRANSFERRING
                )
            )
        self._deps.logger.event(
            "refresh_strategy_wait_started",
            mode=mode,
            seconds=seconds,
            next_stage=self._refresh_strategy_stage + 1,
        )
        self._interruptible_wait(seconds)
        if mode == "exit_and_reenter":
            if seconds == 180:
                self._click(
                    "wake_main_screen",
                    self._config.points["main_screen_wake"],
                )
            self._enter_store()
        self._deps.logger.event(
            "refresh_strategy_wait_completed",
            mode=mode,
            seconds=seconds,
            next_stage=self._refresh_strategy_stage + 1,
        )

    def _scan_until_stopped(self) -> None:
        inventory_came_from_refresh = False
        while True:
            self._control.checkpoint()
            completed_slot_ids: set[str] = set()
            mandatory_targets_found = set(self._scan_viewport("top", completed_slot_ids))
            bottom_samples = self._scroll_to_bottom()
            mandatory_targets_found.update(
                self._scan_viewport(
                    "bottom",
                    completed_slot_ids,
                    initial_samples=bottom_samples,
                )
            )

            recovery: tuple[str, int] | None = None
            if inventory_came_from_refresh:
                recovery = self._record_refresh_strategy_outcome(
                    frozenset(mandatory_targets_found)
                )

            snapshot = self._publisher.snapshot
            if snapshot.refresh_spent + self._config.refresh_cost > snapshot.refresh_limit:
                raise StopExecution(StopReason.BUDGET_COMPLETE)
            if recovery is not None:
                self._perform_refresh_strategy_recovery(*recovery)
            self._refresh_inventory()
            inventory_came_from_refresh = True

    def _purchase(self, match: InventoryMatch) -> None:
        self._transition(RunState.PURCHASING)
        self._click(f"buy:{match.target_id}:{match.slot_id}", match.buy_point)
        self._wait_stable_observation(
            f"confirm_dialog:{match.target_id}",
            lambda frame: self._deps.vision.confirm_dialog(frame, match.target_id),
            self._config.timing.dialog_timeout_ms,
        )
        self._click("confirm_purchase", self._config.points["confirm_button"])

        deadline = self._active_monotonic() + self._config.timing.purchase_result_timeout_ms / 1000
        insufficient_stable = 0
        while self._active_monotonic() <= deadline:
            outcome = self._vision_call(
                self._deps.vision.purchase_outcome,
                self._capture(),
                match.target_id,
                match.roi,
            )
            if outcome is PurchaseOutcome.INSUFFICIENT_FUNDS:
                insufficient_stable += 1
            else:
                insufficient_stable = 0
            self._deps.logger.event(
                "purchase_result",
                target=match.target_id,
                outcome=outcome.value,
                insufficient_stable=insufficient_stable,
            )
            if outcome is PurchaseOutcome.INSUFFICIENT_FUNDS:
                if insufficient_stable >= self._config.timing.stable_frames:
                    raise StopExecution(StopReason.PURCHASE_FUNDS_INSUFFICIENT)
            if outcome is PurchaseOutcome.SUCCESS:
                updated = self._publisher.mutate(
                    lambda snapshot: snapshot.with_incremented_target(match.target_id)
                )
                self._deps.logger.event(
                    "purchase_counted",
                    target=match.target_id,
                    refresh_spent=updated.refresh_spent,
                    count=next(
                        tally.acquired for tally in updated.targets if tally.target_id == match.target_id
                    ),
                )
                return
            self._control.checkpoint()
            self._deps.clock.sleep(self._config.timing.poll_interval_ms / 1000)
        raise StopExecution(StopReason.PURCHASE_RESULT_AMBIGUOUS)

    def _wait_refresh_confirmation_dialog(
        self,
        attempt: int,
        initial_observation: Observation | None = None,
    ) -> Observation:
        dialog_name = "refresh_confirm_dialog"
        deadline = self._active_monotonic() + self._config.timing.dialog_timeout_ms / 1000
        stable = 0
        pending = initial_observation
        while self._active_monotonic() <= deadline:
            observation = pending
            pending = None
            if observation is None:
                observation = self._vision_call(
                    self._deps.vision.refresh_confirm_dialog,
                    self._capture(),
                )
            if observation is None:
                stable = 0
                self._deps.logger.event(
                    "recognition",
                    object=dialog_name,
                    detected=False,
                )
            else:
                stable += 1
                fast = observation.confidence >= _REFRESH_CONFIRM_FAST_CONFIDENCE
                self._deps.logger.event(
                    "recognition",
                    object=dialog_name,
                    detected=True,
                    confidence=f"{observation.confidence:.6f}",
                    roi=f"{observation.roi.x},{observation.roi.y},{observation.roi.width},{observation.roi.height}",
                    stable=stable,
                    fast_eligible=fast,
                )
                if fast or stable >= self._config.timing.stable_frames:
                    mode = "fast" if fast else "stable"
                    self._deps.logger.event(
                        "refresh_confirmation_accepted",
                        attempt=attempt,
                        mode=mode,
                        confidence=f"{observation.confidence:.6f}",
                        stable=stable,
                    )
                    return observation
            self._control.checkpoint()
            self._deps.clock.sleep(self._config.timing.poll_interval_ms / 1000)
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

        self._deps.logger.event(
            "refresh_click_unacknowledged",
            attempt=1,
            sky_stone_before=before,
        )

        frame = self._capture()
        observation = self._vision_call(self._deps.vision.refresh_confirm_dialog, frame)
        if observation is not None:
            self._deps.logger.event(
                "refresh_confirmation_delayed",
                after_attempt=1,
            )
            return self._wait_refresh_confirmation_dialog(
                attempt=1,
                initial_observation=observation,
            )

        self._wait_stable_observation(
            "shop_refresh_button:refresh_retry",
            self._deps.vision.shop_ready,
            self._config.timing.dialog_timeout_ms,
        )
        retry_balance = self._read_stable_sky_stone_balance("before_refresh_retry")
        if retry_balance != before:
            raise StopExecution(
                StopReason.REFRESH_BALANCE_MISMATCH,
                "Sky Stone balance changed before refresh click retry: "
                f"expected unchanged {before}, observed {retry_balance}",
            )

        frame = self._capture()
        observation = self._vision_call(self._deps.vision.refresh_confirm_dialog, frame)
        if observation is not None:
            self._deps.logger.event(
                "refresh_confirmation_delayed",
                after_attempt=1,
            )
            return self._wait_refresh_confirmation_dialog(
                attempt=1,
                initial_observation=observation,
            )
        if self._vision_call(self._deps.vision.shop_ready, frame) is None:
            raise StopExecution(
                StopReason.RECOGNITION_TIMEOUT,
                "shop refresh button disappeared before refresh click retry",
            )

        self._deps.logger.event(
            "refresh_click_retry",
            attempt=2,
            sky_stone_before=before,
        )
        self._click(
            "refresh_inventory",
            self._config.points["refresh_button"],
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
            self._deps.logger.event(
                "refresh_click_unacknowledged",
                attempt=2,
                sky_stone_before=before,
            )
            raise StopExecution(
                StopReason.REFRESH_CLICK_UNACKNOWLEDGED,
                "refresh confirmation dialog missing after two refresh clicks",
            ) from exc

    def _refresh_inventory(self) -> None:
        mark = self._performance_mark()
        outcome = "failed"
        self._transition(RunState.REFRESHING)
        try:
            if self._trusted_sky_stone_balance is None:
                before = self._read_stable_sky_stone_balance("before_refresh")
            else:
                before = self._trusted_sky_stone_balance
                self._deps.logger.event(
                    "trusted_sky_stone_balance_used",
                    stage="before_refresh",
                    value=before,
                )
            expected = before - self._config.refresh_cost
            if expected < 0:
                self._invalidate_trusted_balance("balance_below_refresh_cost")
                raise StopExecution(
                    StopReason.REFRESH_BALANCE_MISMATCH,
                    f"Sky Stone balance {before} is below refresh cost {self._config.refresh_cost}",
                )
            self._invalidate_trusted_balance("refresh_started")
            self._click(
                "refresh_inventory",
                self._config.points["refresh_button"],
                attempt=1,
            )
            confirmation = self._wait_for_refresh_confirmation(before)
            self._click(
                "confirm_refresh",
                confirmation.anchor,
            )

            pending_top = self._wait_for_refresh_balance(before, expected)
            updated = self._publisher.mutate(
                lambda snapshot: snapshot.with_refresh_spent(
                    snapshot.refresh_spent + self._config.refresh_cost
                )
            )
            self._trusted_sky_stone_balance = expected
            self._pending_top_scan = pending_top
            self._deps.logger.event(
                "refresh_counted",
                sky_stone_before=before,
                sky_stone_after=expected,
                refresh_spent=updated.refresh_spent,
                refresh_limit=updated.refresh_limit,
            )
            outcome = "counted"
        finally:
            self._log_performance_stage(
                mark,
                "refresh_inventory",
                outcome=outcome,
            )

    def _read_stable_sky_stone_balance(self, stage: str) -> int:
        mark = self._performance_mark()
        deadline = self._active_monotonic() + self._config.timing.refresh_timeout_ms / 1000
        candidate: int | None = None
        stable_count = 0
        frames = 0
        outcome = "timeout"
        try:
            while self._active_monotonic() <= deadline:
                frame = self._capture()
                frames += 1
                observation = self._vision_call(
                    self._deps.vision.sky_stone_balance,
                    frame,
                )
                if observation is not None:
                    if observation.value == candidate:
                        stable_count += 1
                    else:
                        candidate = observation.value
                        stable_count = 1
                    self._deps.logger.event(
                        "sky_stone_observation",
                        stage=stage,
                        value=observation.value,
                        confidence=f"{observation.confidence:.6f}",
                        roi=f"{observation.roi.x},{observation.roi.y},{observation.roi.width},{observation.roi.height}",
                        stable=stable_count,
                    )
                    if stable_count >= self._config.timing.stable_frames:
                        outcome = "stable"
                        return observation.value
                else:
                    candidate = None
                    stable_count = 0
                self._control.checkpoint()
                self._deps.clock.sleep(self._config.timing.poll_interval_ms / 1000)
            raise StopExecution(
                StopReason.RECOGNITION_TIMEOUT,
                f"cannot read stable Sky Stone balance: {stage}",
            )
        finally:
            if outcome != "stable":
                self._invalidate_trusted_balance(f"sky_stone_{stage}_{outcome}")
            self._log_performance_stage(
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
        mark = self._performance_mark()
        deadline = self._active_monotonic() + self._config.timing.refresh_timeout_ms / 1000
        candidate: int | None = None
        stable_count = 0
        top_previous_key: tuple[tuple[str, str], ...] | None = None
        top_stable = 0
        top_candidate: tuple[InventoryMatch, ...] | None = None
        skipped: dict[tuple[str, str, str, str], tuple[int, float]] = {}
        frames = 0
        outcome = "timeout"
        try:
            while self._active_monotonic() <= deadline:
                frame = self._capture()
                frames += 1
                observation = self._vision_call(
                    self._deps.vision.sky_stone_balance,
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
                    self._deps.logger.event(
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
                        if top_stable >= self._config.timing.stable_frames:
                            top_candidate = top_matches
                    else:
                        top_previous_key = None
                        top_stable = 0
                        top_candidate = None

                    if stable_count >= self._config.timing.stable_frames:
                        if observation.value != expected:
                            outcome = "mismatch"
                            raise StopExecution(
                                StopReason.REFRESH_BALANCE_MISMATCH,
                                f"expected Sky Stone balance {expected}, observed {observation.value}",
                            )
                        outcome = "stable"
                        return top_candidate
                self._control.checkpoint()
                self._deps.clock.sleep(self._config.timing.poll_interval_ms / 1000)
            raise StopExecution(
                StopReason.RECOGNITION_TIMEOUT,
                f"Sky Stone balance did not reach expected value {expected}",
            )
        finally:
            self._log_inventory_skip_summaries(skipped)
            self._log_performance_stage(
                mark,
                "refresh_balance_wait",
                frames=frames,
                balance_stable=stable_count,
                top_stable=top_stable,
                top_reused=top_candidate is not None and outcome == "stable",
                outcome=outcome,
            )


class AutomationSession:
    def __init__(
        self,
        config: AppConfig,
        dependencies: AutomationDependencies,
        hotkeys: HotkeyService,
        on_snapshot: Callable[[RuntimeSnapshot], None],
    ) -> None:
        self._config = config
        self._dependencies = dependencies
        self._hotkeys = hotkeys
        self._on_snapshot = on_snapshot
        self._control = StopController()
        self._move_lock = threading.Lock()
        self._overlay_moving = False

    def request_f5_stop(self) -> None:
        if self._control.request(StopReason.MANUAL_F5):
            self._dependencies.logger.event("stop_requested", source="F5")

    def request_f6_move_toggle(self) -> None:
        with self._move_lock:
            if self._control.reason is not None:
                return
            if not self._overlay_moving:
                if not self._control.pause():
                    return
                if self._dependencies.overlay.begin_move():
                    self._overlay_moving = True
                    self._dependencies.logger.event("overlay_move_started", source="F6")
                    return
                self._control.request(StopReason.OVERLAY_CAPTURE_UNSAFE)
                self._control.resume()
                return

            secure = self._dependencies.overlay.finish_move()
            self._overlay_moving = False
            if secure:
                self._dependencies.logger.event("overlay_move_finished", source="F6")
            else:
                self._control.request(StopReason.OVERLAY_CAPTURE_UNSAFE)
            self._control.resume()

    def _finish_overlay_move_on_stop(self) -> None:
        with self._move_lock:
            if not self._overlay_moving:
                return
            self._dependencies.overlay.finish_move()
            self._overlay_moving = False
            self._control.resume()

    def run(
        self,
        refresh_limit: int,
        run_id: str | None = None,
        enabled_optional_target_ids: frozenset[str] = frozenset(),
    ) -> RuntimeSnapshot:
        if isinstance(refresh_limit, bool) or not isinstance(refresh_limit, int) or refresh_limit < 0:
            raise ValueError("refresh_limit must be a non-negative integer")
        run_id = run_id or uuid.uuid4().hex[:12]
        selectable_ids = {
            target.target_id for target in self._config.targets if target.user_selectable
        }
        unknown_optional_ids = set(enabled_optional_target_ids) - selectable_ids
        if unknown_optional_ids:
            raise ValueError(
                f"Unknown or non-selectable optional targets: {sorted(unknown_optional_ids)}"
            )
        enabled_target_ids = frozenset(
            target.target_id
            for target in self._config.targets
            if not target.user_selectable or target.target_id in enabled_optional_target_ids
        )
        initial = RuntimeSnapshot.initial(
            run_id,
            tuple((target.target_id, target.display_name) for target in self._config.targets),
            refresh_limit,
        )
        publisher = SnapshotPublisher(initial, self._on_snapshot)
        self._on_snapshot(initial)
        self._dependencies.logger.event(
            "target_selection",
            enabled=",".join(sorted(enabled_target_ids)),
            disabled=",".join(sorted(selectable_ids - set(enabled_target_ids))),
        )
        registered = False
        reason = StopReason.INTERNAL_ERROR
        detail = ""
        try:
            registered = self._hotkeys.register_f5(
                self.request_f5_stop,
                self.request_f6_move_toggle,
            )
            if not registered:
                reason = StopReason.HOTKEY_FAILURE
                detail = "RegisterHotKey(F5/F6) failed"
                return publisher.finalize(reason)
            engine = AutomationEngine(
                self._config,
                self._dependencies,
                self._control,
                publisher,
                enabled_target_ids,
            )
            engine.execute()
            reason = StopReason.INTERNAL_ERROR
            detail = "engine returned without terminal reason"
        except StopExecution as exc:
            reason = exc.reason
            detail = exc.detail
        except Exception as exc:
            reason = StopReason.INTERNAL_ERROR
            detail = repr(exc)
        finally:
            self._finish_overlay_move_on_stop()
            if registered:
                try:
                    self._hotkeys.unregister_f5()
                except Exception as exc:
                    detail = f"{detail}; hotkey unregister failed: {exc}".strip("; ")
            final = publisher.finalize(reason)
            self._dependencies.logger.event(
                "run_stopped",
                reason=reason.value,
                detail=detail,
                refresh_spent=final.refresh_spent,
                refresh_limit=final.refresh_limit,
            )
            self._dependencies.logger.close()
        return publisher.snapshot
