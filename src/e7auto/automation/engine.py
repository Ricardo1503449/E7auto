from __future__ import annotations

from typing import Callable
import time

from ..config import AppConfig, Point, Rect
from ..domain import OverlayActivityStatus, RunState, StopReason
from ..geometry import AdaptedFrame, CoordinateTransform, adapt_frame, initial_client_size
from ..ports import CaptureError, DisplayGeometry, WindowRef
from ..vision_types import InventoryMatch, Observation, PurchaseOutcome, ScrollMovementObservation
from .dependencies import AutomationDependencies
from .scrolling import FrameSample, ScrollProgress, ScrollServices, scroll_to_bottom
from .snapshots import SnapshotPublisher
from .stop_control import T, StopExecution, StopController


_REFRESH_CONFIRM_FAST_CONFIDENCE = 0.99


_SHOP_ENTRY_MAX_ATTEMPTS = 3
_STARTUP_MAIN_SHOP_TIMEOUT_MS = 10_000


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
        self._display_geometry: DisplayGeometry | None = None
        self._transform: CoordinateTransform | None = None
        self._handling_network = False
        self._network_paused_seconds = 0.0
        self._network_status_before_reconnect: OverlayActivityStatus | None = None
        self._trusted_sky_stone_balance: int | None = None
        self._pending_top_scan: tuple[InventoryMatch, ...] | None = None
        self._capture_count = 0
        self._capture_seconds = 0.0
        self._vision_calls = 0
        self._vision_seconds = 0.0
        self._normalization_count = 0
        self._normalization_seconds = 0.0

    def execute(self) -> None:
        self._prepare()
        self._enter_store(initial_start=True)
        self._scan_until_stopped()

    def finish_normal_run(self, reason: StopReason) -> None:
        """Return to the main screen before publishing a normal completion."""

        if reason not in {
            StopReason.BUDGET_COMPLETE,
            StopReason.REFRESH_STRATEGY_EXHAUSTED,
        }:
            return
        self._deps.logger.event(
            "normal_completion_exit_started",
            reason=reason.value,
        )
        self._exit_store()
        self._deps.logger.event(
            "normal_completion_exit_completed",
            reason=reason.value,
        )

    def _transition(self, state: RunState) -> None:
        previous = self._publisher.snapshot.state
        self._publisher.mutate(lambda snapshot: snapshot.transitioned(state))
        self._deps.logger.event("state_transition", previous=previous.value, current=state.value)

    def _performance_mark(self) -> tuple[float, int, float, int, float, int, float]:
        return (
            time.perf_counter(),
            self._capture_count,
            self._capture_seconds,
            self._vision_calls,
            self._vision_seconds,
            self._normalization_count,
            self._normalization_seconds,
        )

    def _log_performance_stage(
        self,
        mark: tuple[float, int, float, int, float, int, float],
        stage: str,
        **fields: object,
    ) -> None:
        (
            started,
            capture_count,
            capture_seconds,
            vision_calls,
            vision_seconds,
            normalization_count,
            normalization_seconds,
        ) = mark
        self._deps.logger.event(
            "performance_stage",
            stage=stage,
            duration_ms=f"{(time.perf_counter() - started) * 1000:.3f}",
            capture_count=self._capture_count - capture_count,
            capture_ms=f"{(self._capture_seconds - capture_seconds) * 1000:.3f}",
            vision_calls=self._vision_calls - vision_calls,
            vision_ms=f"{(self._vision_seconds - vision_seconds) * 1000:.3f}",
            normalization_count=self._normalization_count - normalization_count,
            normalization_ms=f"{(self._normalization_seconds - normalization_seconds) * 1000:.3f}",
            **fields,
        )

    def _vision_call(self, detector: Callable[..., T], *args: object) -> T:
        adapted = tuple(arg for arg in args if isinstance(arg, AdaptedFrame))
        before_count = sum(frame.normalization_count for frame in adapted)
        before_seconds = sum(frame.normalization_seconds for frame in adapted)
        started = time.perf_counter()
        try:
            return detector(*args)
        finally:
            self._vision_calls += 1
            self._vision_seconds += time.perf_counter() - started
            self._normalization_count += (
                sum(frame.normalization_count for frame in adapted) - before_count
            )
            self._normalization_seconds += (
                sum(frame.normalization_seconds for frame in adapted) - before_seconds
            )

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
        except Exception as exc:
            raise StopExecution(StopReason.WINDOW_ABNORMAL, str(exc)) from exc
        try:
            display = self._deps.windows.inspect_display(window, validate_mode=True)
        except Exception as exc:
            raise StopExecution(StopReason.INVALID_DISPLAY_GEOMETRY, str(exc)) from exc
        if display.current_mode is None:
            raise StopExecution(
                StopReason.INVALID_DISPLAY_GEOMETRY,
                "validated current desktop mode is missing",
            )
        minimum = self._config.display.minimum_mode
        if (
            display.current_mode.width < minimum.width
            or display.current_mode.height < minimum.height
        ):
            raise StopExecution(
                StopReason.UNSUPPORTED_DISPLAY_RESOLUTION,
                f"unsupported current desktop mode: {display.current_mode}",
            )
        desired = initial_client_size(
            display.current_mode,
            self._config.display.reference_mode,
            self._config.baseline_client_size,
            self._config.display.client_width_fraction,
        )
        try:
            target = (
                desired
                if display.current_mode == self._config.display.reference_mode
                else self._deps.windows.fit_client_size(
                    window,
                    desired,
                    self._config.baseline_client_size,
                    display.monitor_bounds,
                )
            )
            self._deps.windows.restore_without_activation(window)
            self._deps.windows.resize_client(window, target, display.monitor_bounds)
            state = self._deps.windows.inspect(window)
        except Exception as exc:
            raise StopExecution(StopReason.WINDOW_ABNORMAL, str(exc)) from exc
        try:
            verified_display = self._deps.windows.inspect_display(window, validate_mode=True)
        except Exception as exc:
            raise StopExecution(StopReason.INVALID_DISPLAY_GEOMETRY, str(exc)) from exc
        if verified_display != display:
            raise StopExecution(
                StopReason.DISPLAY_CHANGED,
                f"display changed during preparation: {display} -> {verified_display}",
            )
        if (
            not state.exists
            or state.minimized
            or state.client_bounds.width != target.width
            or state.client_bounds.height != target.height
            or abs(
                state.client_bounds.width * self._config.baseline_client_size.height
                - state.client_bounds.height * self._config.baseline_client_size.width
            )
            > self._config.baseline_client_size.width
            or (
                state.outer_bounds is not None
                and (
                    state.outer_bounds.x < display.monitor_bounds.x
                    or state.outer_bounds.y < display.monitor_bounds.y
                    or state.outer_bounds.right > display.monitor_bounds.right
                    or state.outer_bounds.bottom > display.monitor_bounds.bottom
                )
            )
        ):
            raise StopExecution(
                StopReason.WINDOW_ABNORMAL,
                f"client verification failed: {state}",
            )
        self._window = window
        self._baseline_bounds = state.client_bounds
        self._display_geometry = display
        self._transform = CoordinateTransform(
            self._config.baseline_client_size,
            target,
        )
        if not self._deps.overlay.position(
            state.client_bounds,
            self._transform.point(self._config.overlay_offset),
        ):
            raise StopExecution(
                StopReason.INTERNAL_ERROR,
                "overlay positioning timed out",
            )
        self._deps.logger.event(
            "window_prepared",
            hwnd=window.hwnd,
            client_x=state.client_bounds.x,
            client_y=state.client_bounds.y,
            client_width=state.client_bounds.width,
            client_height=state.client_bounds.height,
            monitor_device=display.device_name,
            monitor_x=display.monitor_bounds.x,
            monitor_y=display.monitor_bounds.y,
            monitor_width=display.monitor_bounds.width,
            monitor_height=display.monitor_bounds.height,
            desktop_width=display.current_mode.width,
            desktop_height=display.current_mode.height,
            dpi=display.dpi,
            game_foreground=state.foreground,
            scale_x=f"{self._transform.scale_x:.6f}",
            scale_y=f"{self._transform.scale_y:.6f}",
            reference_path=self._transform.is_identity,
        )

    def _ensure_window(self, *, full_display_check: bool = False) -> None:
        self._control.checkpoint()
        assert (
            self._window is not None
            and self._baseline_bounds is not None
            and self._display_geometry is not None
        )
        try:
            state = self._deps.windows.inspect(self._window)
        except Exception as exc:
            raise StopExecution(StopReason.WINDOW_ABNORMAL, str(exc)) from exc
        if (
            not state.exists
            or state.minimized
            or state.client_bounds != self._baseline_bounds
        ):
            raise StopExecution(StopReason.WINDOW_ABNORMAL, f"window changed: {state}")
        try:
            display = self._deps.windows.inspect_display(
                self._window,
                validate_mode=full_display_check,
            )
        except Exception as exc:
            raise StopExecution(StopReason.INVALID_DISPLAY_GEOMETRY, str(exc)) from exc
        expected = self._display_geometry
        cheap_changed = (
            display.monitor_id != expected.monitor_id
            or display.device_name != expected.device_name
            or display.monitor_bounds != expected.monitor_bounds
            or display.dpi != expected.dpi
        )
        if cheap_changed and not full_display_check:
            try:
                display = self._deps.windows.inspect_display(
                    self._window,
                    validate_mode=True,
                )
            except Exception as exc:
                raise StopExecution(StopReason.INVALID_DISPLAY_GEOMETRY, str(exc)) from exc
        if cheap_changed or (
            full_display_check and display.current_mode != expected.current_mode
        ):
            raise StopExecution(
                StopReason.DISPLAY_CHANGED,
                f"display changed during run: {expected} -> {display}",
            )

    def _capture_raw(self) -> object:
        started = time.perf_counter()
        try:
            self._ensure_window()
            assert self._window is not None and self._baseline_bounds is not None
            try:
                frame = self._deps.capture.capture_client(self._window, self._baseline_bounds)
            except CaptureError as exc:
                raise StopExecution(StopReason.CAPTURE_FAILURE, str(exc)) from exc
            self._control.checkpoint()
            assert self._transform is not None
            return adapt_frame(frame, self._transform)
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

        now = self._deps.clock.monotonic()
        return now - self._network_paused_seconds

    def _capture(self) -> object:
        frame = self._capture_raw()
        self._handle_network_exception(frame)
        return frame

    def _dispatch_input(
        self,
        action: str,
        point: Point,
        callback: Callable[[WindowRef, Point], None],
        **fields: object,
    ) -> None:
        assert self._transform is not None
        client_point = self._transform.point(point)

        def guarded() -> None:
            self._ensure_window(full_display_check=True)
            assert self._window is not None
            try:
                callback(self._window, client_point)
            except StopExecution:
                raise
            except Exception as exc:
                self._deps.logger.event(
                    "input_failed",
                    action=action,
                    logical_x=point.x,
                    logical_y=point.y,
                    client_x=client_point.x,
                    client_y=client_point.y,
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
                client_x=client_point.x,
                client_y=client_point.y,
                background_message_queued=True,
                **fields,
            )

        self._control.dispatch(guarded)

    def _click(self, action: str, point: Point, **fields: object) -> None:
        self._dispatch_input(
            action,
            point,
            self._deps.inputs.click,
            **fields,
        )

    def _scroll_to_bottom(self) -> tuple[FrameSample, ...]:
        mark = self._performance_mark()
        progress = ScrollProgress()
        outcome = "failed"

        def dispatch_scroll(point: Point, delta: int, repetition: int) -> None:
            self._dispatch_input(
                "scroll_bottom",
                point,
                lambda window, client: self._deps.inputs.scroll(window, client, delta),
                delta=delta,
                repetition=repetition,
            )

        services = ScrollServices(
            capture=self._capture,
            dispatch_scroll=dispatch_scroll,
            checkpoint=self._control.checkpoint,
            sleep=self._deps.clock.sleep,
            active_monotonic=self._active_monotonic,
            measure_stability=lambda before, after: self._vision_call(
                self._deps.vision.inventory_scroll_stability, before, after
            ),
            measure_movement=lambda before, after: self._vision_call(
                self._deps.vision.inventory_scroll_movement, before, after
            ),
            logger=self._deps.logger,
        )
        try:
            samples = scroll_to_bottom(
                self._config.scroll, self._config.timing.stable_frames, services, progress
            )
            outcome = "verified"
            return samples
        finally:
            self._log_performance_stage(
                mark,
                "scroll_to_bottom",
                outcome=outcome,
                repetitions=self._config.scroll.repetitions,
                interval_ms=self._config.scroll.interval_ms,
                settle_ms=self._config.scroll.settle_ms,
                settle_actual_ms=progress.elapsed_ms,
                settle_samples=progress.samples,
                stability_comparisons=progress.comparisons,
                early_exit_ms=progress.early_exit_ms,
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

    def _confirm_shop_or_main_after_entry_timeout(self) -> Observation | None:
        """Return the stable main-screen anchor, or None once the shop is ready."""

        deadline = self._active_monotonic() + self._config.timing.entry_timeout_ms / 1000
        shop_stable = 0
        main_stable = 0
        latest_main: Observation | None = None
        while self._active_monotonic() <= deadline:
            frame = self._capture()
            shop = self._vision_call(self._deps.vision.shop_ready, frame)
            if shop is None:
                shop_stable = 0
            else:
                shop_stable += 1
                if shop_stable >= self._config.timing.stable_frames:
                    return None

            main = self._vision_call(self._deps.vision.main_shop_icon, frame)
            if main is None:
                main_stable = 0
                latest_main = None
            else:
                main_stable += 1
                latest_main = main
                if main_stable >= self._config.timing.stable_frames:
                    return latest_main
            self._control.checkpoint()
            self._deps.clock.sleep(self._config.timing.poll_interval_ms / 1000)
        raise StopExecution(
            StopReason.RECOGNITION_TIMEOUT,
            "cannot confirm shop or main screen after entry timeout",
        )

    def _enter_store(self, *, initial_start: bool = False) -> None:
        self._invalidate_trusted_balance("shop_entry")
        self._transition(RunState.ENTERING_STORE)
        main_shop = self._wait_stable_observation(
            "main_shop_icon",
            self._deps.vision.main_shop_icon,
            # Allow the game to redraw after the startup window resize.
            _STARTUP_MAIN_SHOP_TIMEOUT_MS
            if initial_start
            else self._config.timing.entry_timeout_ms,
        )
        for attempt in range(1, _SHOP_ENTRY_MAX_ATTEMPTS + 1):
            self._click("open_shop", main_shop.anchor, attempt=attempt)
            try:
                self._wait_stable_observation(
                    "shop_refresh_button",
                    self._deps.vision.shop_ready,
                    self._config.timing.entry_timeout_ms,
                )
                break
            except StopExecution as exc:
                if exc.reason is not StopReason.RECOGNITION_TIMEOUT:
                    raise
                main_shop = self._confirm_shop_or_main_after_entry_timeout()
                if main_shop is None:
                    break
                if attempt >= _SHOP_ENTRY_MAX_ATTEMPTS:
                    raise StopExecution(
                        StopReason.RECOGNITION_TIMEOUT,
                        f"shop entry failed after {_SHOP_ENTRY_MAX_ATTEMPTS} attempts; "
                        "main screen remains visible",
                    )
                self._deps.logger.event(
                    "shop_entry_retry",
                    completed_attempt=attempt,
                    next_attempt=attempt + 1,
                    confidence=f"{main_shop.confidence:.6f}",
                    logical_x=main_shop.anchor.x,
                    logical_y=main_shop.anchor.y,
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
        initial_samples: tuple[FrameSample, ...] = (),
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
        initial_samples: tuple[FrameSample, ...] = (),
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
