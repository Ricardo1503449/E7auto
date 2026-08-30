from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np

from e7auto.automation import AutomationDependencies
from e7auto.config import (
    AppConfig,
    DisplayConfig,
    LoggingConfig,
    Point,
    Rect,
    RefreshStrategyConfig,
    ScrollConfig,
    Size,
    SlotConfig,
    TargetConfig,
    TimingConfig,
)
from e7auto.ports import DisplayGeometry, WindowRef, WindowState
from e7auto.vision import (
    InventoryMatch,
    Observation,
    PurchaseOutcome,
    ScrollMovementObservation,
    SkyStoneBalanceObservation,
)


def make_config(
    *, refresh_cost: int = 3, stable_frames: int = 1, include_friendship: bool = False
) -> AppConfig:
    targets = [
        TargetConfig("wood", "木材", "wood", "wood_confirm", "wood_purchased"),
        TargetConfig("ore", "矿石", "ore", "ore_confirm", "ore_purchased"),
    ]
    if include_friendship:
        targets.append(
            TargetConfig(
                "friendship_points",
                "友情点数",
                "friendship_points",
                "friendship_points_confirm",
                "friendship_points_purchased",
                True,
            )
        )
    return AppConfig(
        source_path=Path("synthetic.yaml"),
        executable_path=Path(r"D:\Games\Fake\Game.exe"),
        process_name="Game.exe",
        window_title="Game Window",
        baseline_client_size=Size(100, 80),
        display=DisplayConfig(Size(400, 400), Size(100, 80), 0.75),
        refresh_cost=refresh_cost,
        template_paths={},
        rois={
            "main_shop_icon": Rect(0, 0, 10, 10),
            "shop_refresh_button": Rect(80, 0, 10, 10),
            "shop_exit_icon": Rect(1, 1, 10, 10),
            "refresh_confirm_prompt": Rect(20, 20, 20, 10),
            "refresh_confirm_button": Rect(40, 40, 20, 10),
            "inventory_list": Rect(10, 10, 70, 50),
            "confirm_item": Rect(20, 20, 20, 20),
            "confirm_button": Rect(40, 40, 20, 10),
            "purchase_result": Rect(30, 20, 40, 20),
            "sky_stone_icon": Rect(60, 0, 10, 10),
            "sky_stone_digits": Rect(70, 0, 30, 10),
        },
        points={
            "shop_icon": Point(5, 5),
            "shop_exit_button": Point(6, 6),
            "main_screen_wake": Point(50, 40),
            "refresh_button": Point(90, 70),
            "refresh_confirm_button": Point(55, 45),
            "confirm_button": Point(50, 45),
        },
        targets=tuple(targets),
        slots=(
            SlotConfig("top-1", "top", 0, Rect(10, 10, 20, 20), Point(30, 20)),
            SlotConfig("top-2", "top", 1, Rect(40, 10, 20, 20), Point(60, 20)),
            SlotConfig("bottom-1", "bottom", 0, Rect(10, 35, 20, 20), Point(30, 45)),
        ),
        scroll=ScrollConfig(
            Point(50, 40),
            -120,
            1,
            100,
            800,
            200,
            100,
            2,
            1.0,
            0.80,
            4,
            300,
            8,
            0.30,
        ),
        timing=TimingConfig(10, 30, 30, 30, 30, 30, stable_frames),
        refresh_strategy=RefreshStrategyConfig((13, 13, 13, 10), (5, 180, 5)),
        default_confidence=0.9,
        anchor_confidence=0.93,
        sky_stone_digit_confidence=0.8,
        sky_stone_digit_margin=0.08,
        sky_stone_digits_offset=None,
        overlay_offset=Point(7, 9),
        logging=LoggingConfig(14, 100),
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


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeWindowService:
    def __init__(
        self,
        *,
        resize_succeeds: bool = True,
        abnormal_on_inspect: int | None = None,
        abnormal_state: WindowState | None = None,
        display_geometry: DisplayGeometry | None = None,
    ):
        self.ref = WindowRef(123, "Game Window", "Game.exe")
        self.state = WindowState(
            True,
            False,
            True,
            Rect(100, 200, 64, 64),
            Rect(100, 200, 64, 64),
        )
        self.display_geometry = display_geometry or DisplayGeometry(
            1,
            r"\\.\DISPLAY1",
            Rect(0, 0, 400, 400),
            Size(400, 400),
            96,
        )
        self.resize_succeeds = resize_succeeds
        self.abnormal_on_inspect = abnormal_on_inspect
        self.abnormal_state = abnormal_state or WindowState(True, False, True, Rect(101, 200, 100, 80))
        self.inspect_count = 0
        self.resize_calls: list[Size] = []
        self.fit_calls: list[tuple[Size, Size, Rect]] = []
        self.display_inspections: list[bool] = []
        self.locate_calls = 0
        self.restore_calls = 0

    def locate_unique(self, executable_path: str, window_title: str) -> WindowRef:
        assert executable_path == r"D:\Games\Fake\Game.exe"
        assert window_title == "Game Window"
        self.locate_calls += 1
        return self.ref

    def restore_and_foreground(self, window: WindowRef) -> None:
        assert window == self.ref
        self.restore_calls += 1

    def inspect_display(self, window: WindowRef, *, validate_mode: bool) -> DisplayGeometry:
        assert window == self.ref
        self.display_inspections.append(validate_mode)
        if validate_mode:
            return self.display_geometry
        return DisplayGeometry(
            self.display_geometry.monitor_id,
            self.display_geometry.device_name,
            self.display_geometry.monitor_bounds,
            None,
            self.display_geometry.dpi,
        )

    def fit_client_size(
        self,
        window: WindowRef,
        desired: Size,
        baseline: Size,
        monitor_bounds: Rect,
    ) -> Size:
        assert window == self.ref
        self.fit_calls.append((desired, baseline, monitor_bounds))
        return desired

    def resize_client(self, window: WindowRef, size: Size, monitor_bounds: Rect) -> None:
        assert monitor_bounds == self.display_geometry.monitor_bounds
        self.resize_calls.append(size)
        if self.resize_succeeds:
            monitor = self.display_geometry.monitor_bounds
            x = min(max(100, monitor.x), monitor.right - size.width)
            y = min(max(200, monitor.y), monitor.bottom - size.height)
            bounds = Rect(x, y, size.width, size.height)
            self.state = WindowState(True, False, True, bounds, bounds)

    def inspect(self, window: WindowRef) -> WindowState:
        self.inspect_count += 1
        if self.abnormal_on_inspect is not None and self.inspect_count >= self.abnormal_on_inspect:
            return self.abnormal_state
        return self.state


class FakeCapture:
    def __init__(self) -> None:
        self.calls = 0

    def capture_client(self, window: WindowRef, bounds: Rect) -> np.ndarray:
        self.calls += 1
        return np.full((bounds.height, bounds.width, 3), self.calls % 255, dtype=np.uint8)


class FakeInput:
    def __init__(
        self,
        *,
        reported_position: Point | None = None,
        fail_on_click: bool = False,
    ) -> None:
        self.actions: list[tuple[str, Point, int | None]] = []
        self.trigger_once: Callable[[], None] | None = None
        self.current_position = Point(0, 0)
        self.reported_position = reported_position
        self.fail_on_click = fail_on_click

    def _trigger(self) -> None:
        if self.trigger_once is not None:
            callback = self.trigger_once
            self.trigger_once = None
            callback()

    def move(self, point: Point) -> None:
        self.actions.append(("move", point, None))
        self.current_position = point
        self._trigger()

    def position(self) -> Point:
        return self.reported_position or self.current_position

    def click(self, point: Point) -> None:
        if self.fail_on_click:
            raise RuntimeError("synthetic click failure")
        self.actions.append(("click", point, None))
        self.current_position = point
        self._trigger()

    def scroll(self, point: Point, delta: int) -> None:
        self.actions.append(("scroll", point, delta))
        self.current_position = point
        self._trigger()


class FakeRuntimeEnvironment:
    def __init__(self, elevated: bool = True) -> None:
        self.elevated = elevated

    def is_elevated(self) -> bool:
        return self.elevated


class FakeOverlay:
    def __init__(self, safe: bool = True) -> None:
        self.safe = safe
        self.calls: list[tuple[Rect, tuple[Rect, ...], Point | None]] = []
        self.move_calls: list[str] = []

    def position_and_secure(
        self,
        client_bounds: Rect,
        recognition_rois: tuple[Rect, ...],
        offset: Point | None = None,
    ) -> bool:
        self.calls.append((client_bounds, recognition_rois, offset))
        return self.safe

    def begin_move(self) -> bool:
        self.move_calls.append("begin")
        return self.safe

    def finish_move(self) -> bool:
        self.move_calls.append("finish")
        return self.safe


class FakeHotkeys:
    def __init__(self, succeeds: bool = True, on_register: Callable[[Callable[[], None]], None] | None = None):
        self.succeeds = succeeds
        self.on_register = on_register
        self.registered = 0
        self.unregistered = 0
        self.callback: Callable[[], None] | None = None

    def register_f5(
        self,
        callback: Callable[[], None],
        move_callback: Callable[[], None] | None = None,
    ) -> bool:
        self.registered += 1
        self.callback = callback
        self.move_callback = move_callback
        if self.succeeds and self.on_register is not None:
            self.on_register(callback)
        return self.succeeds

    def unregister_f5(self) -> None:
        self.unregistered += 1


class FakeLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.closed = 0

    def event(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def close(self) -> None:
        self.closed += 1


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
        return self.scroll_movement

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


def make_dependencies(
    vision: ScriptedVision,
    *,
    windows: FakeWindowService | None = None,
    inputs: FakeInput | None = None,
    overlay: FakeOverlay | None = None,
    logger: FakeLogger | None = None,
    clock: FakeClock | None = None,
    runtime: FakeRuntimeEnvironment | None = None,
) -> tuple[AutomationDependencies, FakeWindowService, FakeInput, FakeOverlay, FakeLogger]:
    window_service = windows or FakeWindowService()
    input_service = inputs or FakeInput()
    overlay_service = overlay or FakeOverlay()
    run_logger = logger or FakeLogger()
    clock_service = clock or FakeClock()
    runtime_environment = runtime or FakeRuntimeEnvironment()
    dependencies = AutomationDependencies(
        windows=window_service,
        capture=FakeCapture(),
        inputs=input_service,
        overlay=overlay_service,
        vision=vision,
        clock=clock_service,
        logger=run_logger,
        runtime=runtime_environment,
    )
    return dependencies, window_service, input_service, overlay_service, run_logger
