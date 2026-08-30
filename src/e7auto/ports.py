from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np
from numpy.typing import NDArray

from .config import Point, Rect, Size

Frame = NDArray[np.uint8]


@dataclass(frozen=True, slots=True)
class WindowRef:
    hwnd: int
    title: str
    process_name: str
    executable_path: str = ""


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

    def restore_and_foreground(self, window: WindowRef) -> None: ...

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


class InputService(Protocol):
    def move(self, point: Point) -> None: ...

    def position(self) -> Point: ...

    def click(self, point: Point) -> None: ...

    def scroll(self, point: Point, delta: int) -> None: ...


class RuntimeEnvironment(Protocol):
    def is_elevated(self) -> bool: ...


class HotkeyService(Protocol):
    def register_f5(
        self,
        callback: Callable[[], None],
        move_callback: Callable[[], None] | None = None,
    ) -> bool: ...

    def unregister_f5(self) -> None: ...


class OverlayService(Protocol):
    def position_and_secure(
        self,
        client_bounds: Rect,
        recognition_rois: tuple[Rect, ...],
        offset: Point | None = None,
    ) -> bool: ...

    def begin_move(self) -> bool: ...

    def finish_move(self) -> bool: ...


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class TextRunLogger(Protocol):
    def event(self, event: str, **fields: object) -> None: ...

    def close(self) -> None: ...
