from __future__ import annotations

from typing import Callable

import numpy as np

from e7auto.config import Point, Rect, Size
from e7auto.ports import DisplayGeometry, WindowRef, WindowState


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

    def restore_without_activation(self, window: WindowRef) -> None:
        assert window == self.ref
        self.restore_calls += 1
        if self.state.minimized:
            self.state = WindowState(
                self.state.exists,
                False,
                self.state.foreground,
                self.state.client_bounds,
                self.state.outer_bounds,
            )

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
            self.state = WindowState(
                True,
                False,
                self.state.foreground,
                bounds,
                bounds,
            )

    def inspect(self, window: WindowRef) -> WindowState:
        self.inspect_count += 1
        if self.abnormal_on_inspect is not None and self.inspect_count >= self.abnormal_on_inspect:
            return self.abnormal_state
        return self.state


class FakeCapture:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    def capture_client(self, window: WindowRef, bounds: Rect) -> np.ndarray:
        self.calls += 1
        return np.full((bounds.height, bounds.width, 3), self.calls % 255, dtype=np.uint8)

    def close(self) -> None:
        self.closed = True


class FakeInput:
    def __init__(
        self,
        *,
        fail_on_click: bool = False,
    ) -> None:
        self.actions: list[tuple[str, Point, int | None]] = []
        self.trigger_once: Callable[[], None] | None = None
        self.fail_on_click = fail_on_click

    def _trigger(self) -> None:
        if self.trigger_once is not None:
            callback = self.trigger_once
            self.trigger_once = None
            callback()

    def click(self, window: WindowRef, point: Point) -> None:
        if self.fail_on_click:
            raise RuntimeError("synthetic click failure")
        self.actions.append(("click", point, None))
        self._trigger()

    def scroll(self, window: WindowRef, point: Point, delta: int) -> None:
        self.actions.append(("scroll", point, delta))
        self._trigger()


class FakeRuntimeEnvironment:
    def __init__(self, elevated: bool = True) -> None:
        self.elevated = elevated

    def is_elevated(self) -> bool:
        return self.elevated


class FakeOverlay:
    def __init__(self, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.calls: list[tuple[Rect, Point | None]] = []

    def position(
        self,
        client_bounds: Rect,
        offset: Point | None = None,
    ) -> bool:
        self.calls.append((client_bounds, offset))
        return self.succeeds


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
    ) -> bool:
        self.registered += 1
        self.callback = callback
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
