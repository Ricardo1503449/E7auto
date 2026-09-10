from __future__ import annotations

from collections import deque

from e7auto.config import Point, Rect
from e7auto.vision import (
    InventoryMatch,
    Observation,
    PurchaseOutcome,
    ScrollMovementObservation,
    SkyStoneBalanceObservation,
)


def match(target: str, screen: str = "top", slot_order: int = 0) -> InventoryMatch:
    names = {"wood": "木材", "ore": "矿石", "friendship_points": "友情点数"}
    name = names[target]
    return InventoryMatch(
        target,
        name,
        f"{screen}-{slot_order + 1}",
        slot_order,
        Point(30 + slot_order * 30, 20 if screen == "top" else 45),
        0.99,
        Rect(10 + slot_order * 30, 10 if screen == "top" else 35, 20, 20),
    )


class ScriptedVision:
    def __init__(
        self,
        *,
        top: list[tuple[InventoryMatch, ...]] | None = None,
        bottom: list[tuple[InventoryMatch, ...]] | None = None,
        purchase: list[PurchaseOutcome] | None = None,
        balances: list[int | None] | None = None,
        main_visible: bool = True,
        ready_visible: bool = True,
        refresh_confirm_visible: bool = True,
        exit_visible: bool = True,
        scroll_movement: ScrollMovementObservation | None = None,
        scroll_movements: list[ScrollMovementObservation] | None = None,
        scroll_stability: list[ScrollMovementObservation] | None = None,
        network_errors: list[bool] | None = None,
        network_retries: list[bool] | None = None,
    ) -> None:
        self.scans = {
            "top": deque(top or []),
            "bottom": deque(bottom or []),
        }
        self.purchase = deque(purchase or [])
        self.balances = deque([100, 97] if balances is None else balances)
        self.main_visible = main_visible
        self.ready_visible = ready_visible
        self.refresh_confirm_visible = refresh_confirm_visible
        self.exit_visible = exit_visible
        self.scroll_movement = scroll_movement or ScrollMovementObservation(
            25.0,
            0.40,
            254,
            0.0,
            -350.0,
            0.45,
        )
        self.scroll_movements = deque(scroll_movements or [])
        self.scroll_stability = deque(scroll_stability or [])
        self.default_scroll_stability = ScrollMovementObservation(
            0.5,
            0.005,
            8,
            0.0,
            0.0,
            0.95,
        )
        self.scan_calls: list[str] = []
        self.scan_frames: list[object] = []
        self.scroll_stability_after_frames: list[object] = []
        self.scroll_stability_frame_pairs: list[tuple[object, object]] = []
        self.scan_requests: list[
            tuple[str, frozenset[str] | None, frozenset[str]]
        ] = []
        self.activity: list[str] = []
        self.confirm_targets: list[str] = []
        self.purchase_queries: list[tuple[str, Rect]] = []
        self.network_errors = deque(network_errors or [])
        self.network_retries = deque(network_retries or [])
        self.balance_queries = 0

    @staticmethod
    def _observation(name: str) -> Observation:
        return Observation(name, 0.99, Rect(0, 0, 10, 10), Point(5, 5))

    def main_shop_icon(self, frame: object) -> Observation | None:
        return self._observation("main") if self.main_visible else None

    def shop_ready(self, frame: object) -> Observation | None:
        return self._observation("ready") if self.ready_visible else None

    def shop_exit_icon(self, frame: object) -> Observation | None:
        return self._observation("shop-exit") if self.exit_visible else None

    def refresh_confirm_dialog(self, frame: object) -> Observation | None:
        if not self.refresh_confirm_visible:
            return None
        return Observation(
            "refresh-confirm",
            0.99,
            Rect(40, 40, 20, 10),
            Point(55, 45),
        )

    def confirm_dialog(self, frame: object, target_id: str) -> Observation | None:
        self.confirm_targets.append(target_id)
        return self._observation("confirm")

    def purchase_outcome(self, frame: object, target_id: str, item_roi: Rect) -> PurchaseOutcome:
        self.purchase_queries.append((target_id, item_roi))
        if self.purchase:
            return self.purchase.popleft()
        return PurchaseOutcome.PENDING

    def scan_inventory(
        self,
        frame: object,
        screen: str,
        enabled_target_ids: frozenset[str] | None = None,
        excluded_slot_ids: frozenset[str] = frozenset(),
    ) -> tuple[InventoryMatch, ...]:
        self.scan_calls.append(screen)
        self.scan_frames.append(frame)
        self.scan_requests.append((screen, enabled_target_ids, excluded_slot_ids))
        self.activity.append(f"scan:{screen}")
        queue = self.scans[screen]
        detected = queue.popleft() if queue else ()
        return tuple(
            item
            for item in detected
            if (
                (enabled_target_ids is None or item.target_id in enabled_target_ids)
                and item.slot_id not in excluded_slot_ids
            )
        )

    def inventory_scroll_movement(
        self,
        before: object,
        after: object,
    ) -> ScrollMovementObservation:
        self.activity.append("verify_scroll")
        return self.scroll_movements.popleft() if self.scroll_movements else self.scroll_movement

    def inventory_scroll_stability(
        self,
        before: object,
        after: object,
    ) -> ScrollMovementObservation:
        self.activity.append("observe_scroll_stability")
        self.scroll_stability_after_frames.append(after)
        self.scroll_stability_frame_pairs.append((before, after))
        if self.scroll_stability:
            return self.scroll_stability.popleft()
        return self.default_scroll_stability

    def sky_stone_balance(self, frame: object) -> SkyStoneBalanceObservation | None:
        self.balance_queries += 1
        if not self.balances:
            return None
        value = self.balances.popleft() if len(self.balances) > 1 else self.balances[0]
        if value is None:
            return None
        return SkyStoneBalanceObservation(value, 0.99, Rect(70, 0, 30, 10))

    def network_connection_error(self, frame: object) -> Observation | None:
        if self.network_errors:
            return self._observation("network-error") if self.network_errors.popleft() else None
        return None

    def network_retry(self, frame: object) -> Observation | None:
        if self.network_retries:
            return self._observation("network-retry") if self.network_retries.popleft() else None
        return None
