"""Batch penguin purchases using the existing guarded Windows runtime."""
from __future__ import annotations

from typing import Callable, TypeVar

from e7auto.core.domain import OverlayActivityStatus, RunState, StopReason
from e7auto.features.penguin.contracts import PenguinDialog
from e7auto.core.observations import Observation
from e7auto.runtime.context import RuntimeContext
from e7auto.runtime.stop_control import StopExecution
from e7auto.runtime.action_retry import Transition, click_and_wait, observation_signature

T = TypeVar("T")


from e7auto.configuration.models import AppConfig
from e7auto.runtime.dependencies import AutomationDependencies
from e7auto.runtime.stop_control import StopController
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.core.ports import CachedGameFrame
from .contracts import PenguinVisionPort

class PenguinFlow:
    def __init__(self, config: AppConfig, dependencies: AutomationDependencies[PenguinVisionPort],
                 control: StopController, publisher: SnapshotPublisher,
                 enabled_target_ids: frozenset[str] = frozenset()) -> None:
        self.runtime = RuntimeContext(config, dependencies, control, publisher,
                                      discard_interrupted_frames=True)
        self._location = "main"

    def cached_game_frame(self) -> CachedGameFrame | None:
        return self.runtime.cached_game_frame()

    def release_cached_game_frame(self) -> None:
        self.runtime.release_cached_game_frame()

    def _status(self, state: RunState, status: OverlayActivityStatus) -> None:
        self.runtime.transition(state)
        self.runtime.publisher.mutate(lambda snapshot: snapshot.with_overlay_status(status))

    def _wait(self, name: str, detector: Callable[[object], T | None], *,
              signature: Callable[[T], object] = lambda value: value,
              timeout_ms: int | None = None,
              reason: StopReason = StopReason.RECOGNITION_TIMEOUT) -> T:
        deadline = self.runtime.active_monotonic() + (
            timeout_ms or self.runtime.config.timing.dialog_timeout_ms
        ) / 1000
        previous = None
        stable = 0
        while self.runtime.active_monotonic() <= deadline:
            self.runtime.control.checkpoint()
            generation = self.runtime.network_recovery_generation
            value = detector(self.runtime.capture())
            if generation != self.runtime.network_recovery_generation:
                previous, stable = None, 0
            key = signature(value) if value is not None else None
            stable = stable + 1 if key is not None and key == previous else int(key is not None)
            previous = key
            if value is not None and stable >= max(2, self.runtime.config.timing.stable_frames):
                self.runtime.deps.logger.event("penguin_state_confirmed", stage=name)
                return value
            self.runtime.deps.clock.sleep(self.runtime.config.timing.poll_interval_ms / 1000)
        raise StopExecution(reason, f"penguin state timeout: {name}")

    def _control_visible(self, name: str, *, timeout_ms: int | None = None) -> Observation:
        return self._wait(name, lambda frame: self.runtime.deps.vision.control(frame, name),
                          signature=lambda obs: obs.object_id, timeout_ms=timeout_ms or self.runtime.config.timing.entry_timeout_ms)

    def _click_control(self, name: str):
        observation = self._control_visible(name)
        destinations = {"forest_entry": "growth_altar", "altar_close": "growth_altar",
                        "forest_back": "forest_entry", "sanctuary_back": "sanctuary_entry"}
        vision = self.runtime.deps.vision
        def observe(frame):
            if name == "penguin_buy_102":
                dialog = vision.dialog(frame)
                partial = dialog is not None or vision.control(frame, "purchase_cancel") is not None
                return Transition(dialog if dialog is not None and dialog.price is not None else None,
                                  responded=partial)
            if name in ("growth_altar", "reward_close", "purchase_cancel"):
                return Transition(self._altar_state(frame))
            return Transition(vision.control(frame, destinations[name]))
        def source(frame):
            if name == "reward_close" and vision.control(frame, "penguin_complete") is None:
                return None
            if name == "purchase_cancel":
                dialog = vision.dialog(frame)
                return None if dialog is None else dialog.cancel
            return vision.control(frame, name)
        def signature(value):
            if isinstance(value, PenguinDialog):
                return value.price, value.full_price_button
            return value if isinstance(value, str) else observation_signature(value)
        return click_and_wait(self.runtime, name="penguin_"+name, initial=observation,
                              click=lambda hit: self.runtime.click("penguin_"+name, hit.anchor),
                              source=source, observe=observe, signature=signature,
                              target_signature=observation_signature,
                              stable_frames=max(2, self.runtime.config.timing.stable_frames),
                              timeout_ms=self.runtime.config.timing.entry_timeout_ms)

    def _altar_state(self, frame):
        vision = self.runtime.deps.vision
        if vision.control(frame, "altar_close") is None:
            return None
        return next((name for name in ("purchase_currency", "penguin_buy_102")
                     if vision.control(frame, name) is not None), None)

    def _maximum(self, original: PenguinDialog) -> PenguinDialog:
        runtime, vision = self.runtime, self.runtime.deps.vision
        settle_seconds = max(.5, runtime.config.timing.dialog_timeout_ms / 1000)
        last_click, attempts = runtime.active_monotonic(), 0
        def click(hit):
            nonlocal last_click, attempts
            runtime.click("penguin_quantity_max", hit.anchor)
            attempts += 1
            last_click = runtime.active_monotonic()
        def observe(frame):
            dialog = vision.dialog(frame)
            if dialog is None or dialog.price is None:
                return Transition(blocked=True)
            changed = dialog.price != original.price
            ready = (dialog.price == 5100 or
                     ((changed or attempts == runtime.config.click_retry.max_attempts)
                      and runtime.active_monotonic()-last_click >= settle_seconds))
            return Transition(dialog if ready else None, responded=changed or dialog.price == 5100)
        def source(frame):
            dialog = vision.dialog(frame)
            return dialog.maximum if dialog is not None and dialog.price == original.price else None
        return click_and_wait(runtime, name="penguin_quantity_max", initial=original.maximum,
                              click=click, observe=observe, source=source,
                              signature=lambda value: (value.price, value.full_price_button),
                              target_signature=observation_signature,
                              stable_frames=max(2, runtime.config.timing.stable_frames),
                              timeout_ms=round(settle_seconds*1000)+runtime.config.timing.dialog_timeout_ms)

    def execute(self) -> None:
        self._location = "main"
        self.runtime.prepare()
        main = self.runtime.wait_startup_icon(
            "sanctuary_entry", lambda frame: self.runtime.deps.vision.control(frame, "sanctuary_entry"),
            minimum_stable_frames=2,
        )
        self.runtime.deps.logger.event("penguin_state_confirmed", stage="sanctuary_entry")
        if self.runtime.publisher.snapshot.purchase_limit == 0:
            raise StopExecution(StopReason.PENGUIN_LIMIT_COMPLETE)
        self._status(RunState.ENTERING_SANCTUARY, OverlayActivityStatus.NAVIGATING)
        self.runtime.enter_with_retry(
            entry_name="sanctuary", main=main,
            main_detector=lambda frame: self.runtime.deps.vision.control(frame, "sanctuary_entry"),
            destination_name="forest_entry",
            destination_detector=lambda frame: self.runtime.deps.vision.control(frame, "forest_entry"),
            click_action="penguin_sanctuary_entry",
        )
        self._click_control("forest_entry")
        altar = self._click_control("growth_altar")
        self._location = "altar"
        self._status(RunState.GROWING_PENGUINS, OverlayActivityStatus.BUYING_PENGUINS)
        while self.runtime.publisher.snapshot.purchases_completed < self.runtime.publisher.snapshot.purchase_limit:
            if altar == "purchase_currency":
                raise StopExecution(StopReason.PENGUIN_FUNDS_COMPLETE)
            dialog = self._click_control("penguin_buy_102")
            self._location = "dialog"
            if dialog.price != 5100:
                dialog = self._maximum(dialog)
                if dialog.price != 5100:
                    self.runtime.deps.logger.event("penguin_maximum_unaffordable", price=dialog.price)
                    raise StopExecution(StopReason.PENGUIN_FUNDS_COMPLETE)
            if not dialog.full_price_button:
                raise StopExecution(StopReason.RECOGNITION_TIMEOUT, "full 5100 button not confirmed")
            self.runtime.click("penguin_confirm_purchase", dialog.purchase.anchor)
            self._wait("purchase_complete", lambda frame: self.runtime.deps.vision.control(frame, "penguin_complete"),
                       signature=lambda obs: obs.object_id,
                       timeout_ms=self.runtime.config.timing.purchase_result_timeout_ms,
                       reason=StopReason.PURCHASE_RESULT_AMBIGUOUS)
            self.runtime.publisher.mutate(lambda snapshot: snapshot.with_penguin_purchase())
            self.runtime.deps.logger.event("penguin_batch_purchased", quantity=50,
                                    completed=self.runtime.publisher.snapshot.purchases_completed)
            altar = self._click_control("reward_close")
            self._location = "altar"
        raise StopExecution(StopReason.PENGUIN_LIMIT_COMPLETE)

    def finish_normal_run(self, reason: StopReason) -> None:
        if reason not in {StopReason.PENGUIN_LIMIT_COMPLETE, StopReason.PENGUIN_FUNDS_COMPLETE}:
            return
        self.runtime.control.checkpoint()
        self._status(RunState.RETURNING_MAIN, OverlayActivityStatus.RETURNING)
        if self._location == "dialog":
            self._click_control("purchase_cancel")
            self._location = "altar"
        if self._location == "altar":
            self._click_control("altar_close")
            # A forest header remains visible behind the altar. The undimmed
            # growth-altar button proves the close transition finished first.
            self._click_control("forest_back")
            self._click_control("sanctuary_back")
        else:
            self._control_visible("sanctuary_entry", timeout_ms=10_000)
        self._location = "main"
