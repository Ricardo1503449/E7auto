from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from numpy.typing import NDArray
import numpy as np

from .config import Point, Rect, Size
from .vision_types import (
    Observation,
    InventoryMatch,
    SkyStoneBalanceObservation,
    ScrollMovementObservation,
    PurchaseOutcome,
)

Frame = NDArray[np.uint8]


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WindowRef:
    hwnd: int
    title: str
    process_name: str
    executable_path: str = ""
    process_id: int = 0


@dataclass(frozen=True, slots=True)
class WindowState:
    exists: bool
    minimized: bool
    foreground: bool
    client_bounds: Rect
    outer_bounds: Rect | None = None


@dataclass(frozen=True, slots=True)
class DisplayGeometry:
    monitor_id: int
    device_name: str
    monitor_bounds: Rect
    current_mode: Size | None
    dpi: int


class WindowService(Protocol):
    def locate_unique(self, executable_path: str, window_title: str) -> WindowRef: ...

    def restore_without_activation(self, window: WindowRef) -> None: ...

    def inspect_display(self, window: WindowRef, *, validate_mode: bool) -> DisplayGeometry: ...

    def fit_client_size(
        self,
        window: WindowRef,
        desired: Size,
        baseline: Size,
        monitor_bounds: Rect,
    ) -> Size: ...

    def resize_client(self, window: WindowRef, size: Size, monitor_bounds: Rect) -> None: ...

    def inspect(self, window: WindowRef) -> WindowState: ...


class CaptureService(Protocol):
    def capture_client(self, window: WindowRef, bounds: Rect) -> Frame: ...

    def close(self) -> None: ...


class InputService(Protocol):
    def click(self, window: WindowRef, point: Point) -> None: ...

    def scroll(self, window: WindowRef, point: Point, delta: int) -> None: ...


class RuntimeEnvironment(Protocol):
    def is_elevated(self) -> bool: ...


class HotkeyService(Protocol):
    def register_f5(
        self,
        callback: Callable[[], None],
    ) -> bool: ...

    def unregister_f5(self) -> None: ...


class OverlayService(Protocol):
    def position(
        self,
        client_bounds: Rect,
        offset: Point | None = None,
    ) -> bool: ...

class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class TextRunLogger(Protocol):
    def event(self, event: str, **fields: object) -> None: ...

    def close(self) -> None: ...


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
