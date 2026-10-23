from __future__ import annotations
from e7auto.core.types import Point
from e7auto.core.domain import OverlayActivityStatus, RunState, StopReason
from e7auto.runtime.stop_control import StopExecution
from e7auto.features.shop.scrolling import FrameSample, ScrollProgress, ScrollServices, scroll_to_bottom
from e7auto.runtime.context import RuntimeContext
from .inventory import InventoryScanner
from .refresh import RefreshController
from .state import ShopState

from e7auto.configuration.models import AppConfig
from e7auto.runtime.dependencies import AutomationDependencies
from e7auto.runtime.stop_control import StopController
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.core.ports import CachedGameFrame
from .contracts import ShopVisionPort

class ShopFlow:
    """Shop business state composed with a shared runtime."""
    def __init__(self, config: AppConfig, dependencies: AutomationDependencies[ShopVisionPort],
                 control: StopController, publisher: SnapshotPublisher,
                 enabled_target_ids: frozenset[str] = frozenset(), *,
                 continuous_refresh: bool = False) -> None:
        self.state = ShopState(
            enabled_target_ids,
            frozenset(target.target_id for target in config.targets if not target.user_selectable),
            continuous_refresh,
        )
        self.runtime = RuntimeContext(config, dependencies, control, publisher)
        self.inventory = InventoryScanner(self.runtime, self.state)
        self.refresh = RefreshController(self.runtime, self.state, self.inventory,
                                         enter_store=self._enter_store, exit_store=self._exit_store)
        self.runtime.on_recovery = self.refresh.invalidate_trusted_balance

    def cached_game_frame(self) -> CachedGameFrame | None:
        return self.runtime.cached_game_frame()

    def release_cached_game_frame(self) -> None:
        self.inventory.invalidate()
        self.runtime.release_cached_game_frame()


    def execute(self) -> None:
        self.runtime.prepare()
        self._enter_store(initial_start=True)
        self._scan_until_stopped()


    def finish_normal_run(self, reason: StopReason) -> None:
        """Return to the main screen before publishing a normal completion."""

        if reason not in {
            StopReason.BUDGET_COMPLETE,
            StopReason.REFRESH_STRATEGY_EXHAUSTED,
        }:
            return
        self.runtime.deps.logger.event(
            "normal_completion_exit_started",
            reason=reason.value,
        )
        self._exit_store()
        self.runtime.deps.logger.event(
            "normal_completion_exit_completed",
            reason=reason.value,
        )


    def _scroll_to_bottom(self) -> tuple[FrameSample, ...]:
        reference = self.inventory.take_scroll_reference()
        mark = self.runtime.performance_mark()
        progress = ScrollProgress()
        outcome = "failed"
        def dispatch_scroll(point: Point, delta: int, repetition: int) -> None:
            self.runtime.dispatch_input(
                "scroll_bottom",
                point,
                lambda window, client: self.runtime.deps.inputs.scroll(window, client, delta),
                delta=delta,
                repetition=repetition,
            )

        services = ScrollServices(
            capture=self.runtime.capture,
            dispatch_scroll=dispatch_scroll,
            checkpoint=self.runtime.control.checkpoint,
            sleep=self.runtime.deps.clock.sleep,
            active_monotonic=self.runtime.active_monotonic,
            measure_stability=lambda before, after: self.runtime.vision_call(
                self.runtime.deps.vision.inventory_scroll_stability, before, after
            ),
            measure_movement=lambda before, after: self.runtime.vision_call(
                self.runtime.deps.vision.inventory_scroll_movement, before, after
            ),
            recovery_generation=lambda: self.runtime.network_recovery_generation,
            logger=self.runtime.deps.logger,
            scene_state=lambda frame: self.runtime.vision_call(
                self.runtime.deps.vision.inventory_scene_state, frame, "bottom"),
        )
        try:
            samples = scroll_to_bottom(
                self.runtime.config.scroll, self.runtime.config.timing.stable_frames, services, progress,
                reference=reference, readiness=self.runtime.config.inventory_readiness,
            )
            outcome = "verified"
            self.inventory.retain_scroll_evidence(samples)
            return samples
        finally:
            self.runtime.log_performance_stage(
                mark,
                "scroll_to_bottom",
                outcome=outcome,
                repetitions=self.runtime.config.scroll.repetitions,
                interval_ms=self.runtime.config.scroll.interval_ms,
                settle_ms=self.runtime.config.scroll.settle_ms,
                settle_actual_ms=progress.elapsed_ms,
                settle_samples=progress.samples,
                stability_comparisons=progress.comparisons,
                early_exit_ms=progress.early_exit_ms,
            )


    def _enter_store(self, *, initial_start: bool = False) -> None:
        self.refresh.invalidate_trusted_balance("shop_entry")
        self.runtime.transition(RunState.ENTERING_STORE)
        if initial_start:
            main_shop = self.runtime.wait_startup_icon("main_shop_icon", self.runtime.deps.vision.main_shop_icon)
        else:
            main_shop = self.runtime.wait_stable_observation(
                "main_shop_icon", self.runtime.deps.vision.main_shop_icon, self.runtime.config.timing.entry_timeout_ms,
            )
        self.runtime.enter_with_retry(
            entry_name="shop", main=main_shop,
            main_detector=self.runtime.deps.vision.main_shop_icon,
            destination_name="shop_refresh_button",
            destination_detector=self.runtime.deps.vision.shop_ready,
            click_action="open_shop",
        )
        self.runtime.publisher.mutate(
            lambda snapshot: snapshot.with_overlay_status(
                OverlayActivityStatus.REFRESHING
            )
        )


    def _exit_store(self) -> None:
        self.refresh.invalidate_trusted_balance("shop_exit")
        self.runtime.wait_stable_observation(
            "shop_exit_icon",
            self.runtime.deps.vision.shop_exit_icon,
            self.runtime.config.timing.entry_timeout_ms,
        )
        self.runtime.click("exit_shop", self.runtime.config.points["shop_exit_button"])
        self.runtime.wait_stable_observation(
            "main_shop_icon",
            self.runtime.deps.vision.main_shop_icon,
            self.runtime.config.timing.entry_timeout_ms,
        )


    def _scan_until_stopped(self) -> None:
        inventory_came_from_refresh = False
        while True:
            self.runtime.control.checkpoint()
            completed_slot_ids: set[str] = set()
            mandatory_targets_found = set(self.inventory.scan_viewport("top", completed_slot_ids))
            bottom_samples = self._scroll_to_bottom()
            mandatory_targets_found.update(
                self.inventory.scan_viewport(
                    "bottom",
                    completed_slot_ids,
                    initial_samples=bottom_samples,
                )
            )

            recovery: tuple[str, int] | None = None
            if inventory_came_from_refresh:
                recovery = self.refresh.record_refresh_strategy_outcome(
                    frozenset(mandatory_targets_found)
                )

            snapshot = self.runtime.publisher.snapshot
            if snapshot.refresh_spent + self.runtime.config.refresh_cost > snapshot.refresh_limit:
                raise StopExecution(StopReason.BUDGET_COMPLETE)
            if recovery is not None:
                self.refresh.perform_refresh_strategy_recovery(*recovery)
            self.refresh.refresh_inventory()
            inventory_came_from_refresh = True
