from __future__ import annotations
from typing import TypeVar
from e7auto.core.types import Point
from e7auto.core.domain import OverlayActivityStatus, RunState, StopReason
from e7auto.core.observations import Observation
from e7auto.features.shop.contracts import InventoryMatch, ScrollOverlapObservation
from e7auto.runtime.stop_control import StopExecution
from e7auto.features.shop.scrolling import FrameSample, ScrollProgress, ScrollServices, scroll_to_bottom
from e7auto.runtime.context import RuntimeContext
from e7auto.features.shop import inventory, refresh

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
        self.runtime = RuntimeContext(config, dependencies, control, publisher,
                                      on_recovery=self._invalidate_trusted_balance)
        self._enabled_target_ids = enabled_target_ids
        self._continuous_refresh = continuous_refresh
        self._mandatory_target_ids = frozenset(
            target.target_id for target in config.targets if not target.user_selectable
        )
        self._refresh_strategy_stage = 0
        self._refreshes_without_mandatory_target = 0
        self._consecutive_no_target_refreshes = 0
        self._trusted_sky_stone_balance = None
        self._pending_top_scan = None

    def cached_game_frame(self) -> CachedGameFrame | None:
        return self.runtime.cached_game_frame()

    def release_cached_game_frame(self) -> None:
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


    def _invalidate_trusted_balance(self, reason: str) -> None:
        return refresh._invalidate_trusted_balance(self, reason)

    def _scroll_to_bottom(self) -> tuple[FrameSample, ...]:
        mark = self.runtime.performance_mark()
        progress = ScrollProgress()
        outcome = "failed"
        overlap_reference: object | None = None

        def verify_overlap(
            first: object, current: object, shift_x: float, shift_y: float,
        ) -> ScrollOverlapObservation:
            nonlocal overlap_reference
            if overlap_reference is None:
                overlap_reference = self.runtime.vision_call(
                    self.runtime.deps.vision.prepare_scroll_overlap_reference, first,
                )
            return self.runtime.vision_call(
                self.runtime.deps.vision.verify_scroll_overlap, overlap_reference, current, shift_x, shift_y,
            )

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
            verify_overlap=verify_overlap,
            inventory_height=self.runtime.config.rois["inventory_list"].height,
            logger=self.runtime.deps.logger,
        )
        try:
            samples = scroll_to_bottom(
                self.runtime.config.scroll, self.runtime.config.timing.stable_frames, services, progress
            )
            outcome = "verified"
            return samples
        finally:
            overlap_reference = None
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
        self._invalidate_trusted_balance("shop_entry")
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
        self._invalidate_trusted_balance("shop_exit")
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


    @staticmethod
    def _record_inventory_skip(skipped: dict[tuple[str, str, str, str], tuple[int, float]], match: InventoryMatch, screen: str, reason: str) -> None:
        return inventory._record_inventory_skip(skipped, match, screen, reason)

    def _log_inventory_skip_summaries(self, skipped: dict[tuple[str, str, str, str], tuple[int, float]]) -> None:
        return inventory._log_inventory_skip_summaries(self, skipped)

    def _detect_actionable_inventory(self, frame: object, screen: str, completed_slot_ids: set[str], skipped: dict[tuple[str, str, str, str], tuple[int, float]]) -> tuple[InventoryMatch, ...]:
        return inventory._detect_actionable_inventory(self, frame, screen, completed_slot_ids, skipped)

    def _log_inventory_frame(self, screen: str, matches: tuple[InventoryMatch, ...], stable: int, *, source: str | None=None) -> None:
        return inventory._log_inventory_frame(self, screen, matches, stable, source=source)

    def _stable_scan(self, screen: str, completed_slot_ids: set[str], initial_samples: tuple[FrameSample, ...]=()) -> tuple[InventoryMatch, ...]:
        return inventory._stable_scan(self, screen, completed_slot_ids, initial_samples)

    def _scan_viewport(self, screen: str, completed_slot_ids: set[str], initial_samples: tuple[FrameSample, ...]=()) -> frozenset[str]:
        return inventory._scan_viewport(self, screen, completed_slot_ids, initial_samples)

    def _record_refresh_strategy_outcome(self, mandatory_targets_found: frozenset[str]) -> tuple[str, int] | None:
        return refresh._record_refresh_strategy_outcome(self, mandatory_targets_found)

    def _perform_refresh_strategy_recovery(self, mode: str, seconds: int) -> None:
        return refresh._perform_refresh_strategy_recovery(self, mode, seconds)

    def _scan_until_stopped(self) -> None:
        inventory_came_from_refresh = False
        while True:
            self.runtime.control.checkpoint()
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

            snapshot = self.runtime.publisher.snapshot
            if snapshot.refresh_spent + self.runtime.config.refresh_cost > snapshot.refresh_limit:
                raise StopExecution(StopReason.BUDGET_COMPLETE)
            if recovery is not None:
                self._perform_refresh_strategy_recovery(*recovery)
            self._refresh_inventory()
            inventory_came_from_refresh = True


    def _purchase(self, match: InventoryMatch) -> None:
        return inventory._purchase(self, match)

    def _wait_refresh_confirmation_dialog(self, attempt: int, initial_observation: Observation | None=None) -> Observation:
        return refresh._wait_refresh_confirmation_dialog(self, attempt, initial_observation)

    def _wait_for_refresh_confirmation(self, before: int) -> Observation:
        return refresh._wait_for_refresh_confirmation(self, before)

    def _refresh_inventory(self) -> None:
        return refresh._refresh_inventory(self)

    def _read_stable_sky_stone_balance(self, stage: str) -> int:
        return refresh._read_stable_sky_stone_balance(self, stage)

    def _wait_for_refresh_balance(self, before: int, expected: int) -> tuple[InventoryMatch, ...] | None:
        return refresh._wait_for_refresh_balance(self, before, expected)
