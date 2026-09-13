"""Batch penguin purchases using the existing guarded Windows runtime."""
from __future__ import annotations

from typing import Callable, TypeVar

from ..domain import OverlayActivityStatus, RunState, StopReason
from ..penguin_vision import PenguinDialog
from ..vision_types import Observation
from .engine import AutomationEngine
from .stop_control import StopExecution

T = TypeVar("T")
_SANCTUARY_ENTRY_TIMEOUT_MS = 10_000


class PenguinEngine(AutomationEngine):
    # Reuse guarded input, entry retries and network recovery. Shop scans,
    # refresh strategies and currency verification are never executed.
    _location = "main"

    def _capture(self) -> object:
        while True:
            generation = self._network_recovery_generation
            frame = self._capture_raw()
            self._handle_network_exception(frame)
            if generation == self._network_recovery_generation:
                return frame
            # The interrupted frame is obsolete after recovery. Read a fresh
            # game frame before resuming the pending recognition, never an input.

    def _status(self, state: RunState, status: OverlayActivityStatus) -> None:
        self._transition(state)
        self._publisher.mutate(lambda snapshot: snapshot.with_overlay_status(status))

    def _wait(self, name: str, detector: Callable[[object], T | None], *,
              signature: Callable[[T], object] = lambda value: value,
              timeout_ms: int | None = None,
              reason: StopReason = StopReason.RECOGNITION_TIMEOUT) -> T:
        deadline = self._active_monotonic() + (
            timeout_ms or self._config.timing.dialog_timeout_ms
        ) / 1000
        previous = None
        stable = 0
        while self._active_monotonic() <= deadline:
            self._control.checkpoint()
            generation = self._network_recovery_generation
            value = detector(self._capture())
            if generation != self._network_recovery_generation:
                previous, stable = None, 0
            key = signature(value) if value is not None else None
            stable = stable + 1 if key is not None and key == previous else int(key is not None)
            previous = key
            if value is not None and stable >= max(2, self._config.timing.stable_frames):
                self._deps.logger.event("penguin_state_confirmed", stage=name)
                return value
            self._deps.clock.sleep(self._config.timing.poll_interval_ms / 1000)
        raise StopExecution(reason, f"penguin state timeout: {name}")

    def _control_visible(self, name: str, *, timeout_ms: int | None = None) -> Observation:
        return self._wait(name, lambda frame: self._deps.vision.control(frame, name),
                          signature=lambda obs: obs.object_id, timeout_ms=timeout_ms or self._config.timing.entry_timeout_ms)

    def _click_control(self, name: str) -> None:
        observation = self._control_visible(name)
        self._click(f"penguin_{name}", observation.anchor)

    def _altar(self) -> str:
        def detect(frame: object) -> str | None:
            # First confirm the altar close control is active, not dimmed beneath a dialog.
            if self._deps.vision.control(frame, "altar_close") is None:
                return None
            for name in ("purchase_currency", "penguin_buy_102"):
                if self._deps.vision.control(frame, name) is not None:
                    return name
            return None
        result = self._wait("altar", detect)
        self._location = "altar"
        return result

    def _dialog(self, *, previous_price: int | None = None) -> PenguinDialog:
        # A lower price after max must survive a complete settling window.
        # 5100 can be acknowledged immediately; stale pre-click prices cannot
        # prematurely finish a run while the game is still processing max.
        settle_until = self._active_monotonic() + (
            max(0.5, self._config.timing.dialog_timeout_ms / 1000)
            if previous_price is not None else 0
        )
        def detect(frame: object) -> PenguinDialog | None:
            result = self._deps.vision.dialog(frame)
            if result is None or result.price is None:
                return None
            if result.price != 5100 and self._active_monotonic() < settle_until:
                return None
            return result
        dialog = self._wait("purchase_dialog", detect,
                            signature=lambda value: (value.price, value.full_price_button),
                            timeout_ms=(
                                max(500, self._config.timing.dialog_timeout_ms)
                                + self._config.timing.dialog_timeout_ms
                                if previous_price is not None else None
                            ))
        self._location = "dialog"
        return dialog

    def execute(self) -> None:
        self._location = "main"
        self._prepare()
        main = self._control_visible("sanctuary_entry", timeout_ms=10_000)
        if self._publisher.snapshot.purchase_limit == 0:
            raise StopExecution(StopReason.PENGUIN_LIMIT_COMPLETE)
        self._status(RunState.ENTERING_SANCTUARY, OverlayActivityStatus.NAVIGATING)
        self._enter_with_retry(
            entry_name="sanctuary", main=main,
            main_detector=lambda frame: self._deps.vision.control(frame, "sanctuary_entry"),
            destination_name="forest_entry",
            destination_detector=lambda frame: self._deps.vision.control(frame, "forest_entry"),
            click_action="penguin_sanctuary_entry",
            timeout_ms=_SANCTUARY_ENTRY_TIMEOUT_MS,
        )
        self._click_control("forest_entry")
        self._click_control("growth_altar")
        altar = self._altar()
        self._status(RunState.GROWING_PENGUINS, OverlayActivityStatus.BUYING_PENGUINS)
        while self._publisher.snapshot.purchases_completed < self._publisher.snapshot.purchase_limit:
            if altar == "purchase_currency":
                raise StopExecution(StopReason.PENGUIN_FUNDS_COMPLETE)
            self._click_control("penguin_buy_102")
            dialog = self._dialog()
            if dialog.price != 5100:
                self._click("penguin_quantity_max", dialog.maximum.anchor)
                dialog = self._dialog(previous_price=dialog.price)
                if dialog.price != 5100:
                    self._deps.logger.event("penguin_maximum_unaffordable", price=dialog.price)
                    raise StopExecution(StopReason.PENGUIN_FUNDS_COMPLETE)
            if not dialog.full_price_button:
                raise StopExecution(StopReason.RECOGNITION_TIMEOUT, "full 5100 button not confirmed")
            self._click("penguin_confirm_purchase", dialog.purchase.anchor)
            self._wait("purchase_complete", lambda frame: self._deps.vision.control(frame, "penguin_complete"),
                       signature=lambda obs: obs.object_id,
                       timeout_ms=self._config.timing.purchase_result_timeout_ms,
                       reason=StopReason.PURCHASE_RESULT_AMBIGUOUS)
            self._publisher.mutate(lambda snapshot: snapshot.with_penguin_purchase())
            self._deps.logger.event("penguin_batch_purchased", quantity=50,
                                    completed=self._publisher.snapshot.purchases_completed)
            self._click_control("reward_close")
            altar = self._altar()
        raise StopExecution(StopReason.PENGUIN_LIMIT_COMPLETE)

    def finish_normal_run(self, reason: StopReason) -> None:
        if reason not in {StopReason.PENGUIN_LIMIT_COMPLETE, StopReason.PENGUIN_FUNDS_COMPLETE}:
            return
        self._control.checkpoint()
        self._status(RunState.RETURNING_MAIN, OverlayActivityStatus.RETURNING)
        if self._location == "dialog":
            self._click_control("purchase_cancel")
            self._altar()
        if self._location == "altar":
            self._click_control("altar_close")
            # A forest header remains visible behind the altar. The undimmed
            # growth-altar button proves the close transition finished first.
            self._control_visible("growth_altar")
            self._click_control("forest_back")
            self._control_visible("forest_entry")
            self._click_control("sanctuary_back")
        self._control_visible("sanctuary_entry", timeout_ms=10_000)
        self._location = "main"
