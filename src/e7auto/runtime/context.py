from __future__ import annotations
from typing import Callable, Generic
from e7auto.runtime.dependencies import AutomationDependencies, VisionT
from e7auto.configuration.models import AppConfig
from e7auto.runtime.stop_control import StopController, T
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.core.types import Point
from e7auto.core.domain import RunState
from e7auto.core.observations import Observation
from e7auto.core.types import Rect
from e7auto.core.domain import OverlayActivityStatus
from e7auto.vision.frames import CoordinateTransform
from e7auto.core.ports import CachedGameFrame, DisplayGeometry, WindowRef
from e7auto.runtime import windows
from e7auto.runtime import capture
from e7auto.runtime import network
from e7auto.runtime import guarded_input
from e7auto.runtime import navigation
from e7auto.runtime import telemetry

class RuntimeContext(Generic[VisionT]):
    """One run's guarded services and state; no feature-specific counters or cache."""
    def __init__(self, config: AppConfig, dependencies: AutomationDependencies[VisionT],
                 control: StopController, publisher: SnapshotPublisher, *,
                 on_recovery: Callable[[str], None] | None = None,
                 discard_interrupted_frames: bool = False) -> None:
        self.config = config
        self.deps = dependencies
        self.control = control
        self.publisher = publisher
        self.on_recovery = on_recovery or (lambda reason: None)
        self.discard_interrupted_frames = discard_interrupted_frames
        self.window: WindowRef | None = None
        self.baseline_bounds: Rect | None = None
        self.display_geometry: DisplayGeometry | None = None
        self.transform: CoordinateTransform | None = None
        self.last_captured_frame: CachedGameFrame | None = None
        self.handling_network = False
        self.network_recovery_generation = 0
        self.startup_wake_sent = False
        self.network_paused_seconds = 0.0
        self.network_status_before_reconnect: OverlayActivityStatus | None = None
        self.capture_count = 0
        self.capture_seconds = 0.0
        self.vision_calls = 0
        self.vision_seconds = 0.0
        self.normalization_count = 0
        self.normalization_seconds = 0.0


    def prepare(self) -> None:
        return windows.prepare(self)

    def ensure_window(self, *, full_display_check: bool=False) -> None:
        return windows.ensure_window(self, full_display_check=full_display_check)

    def capture_raw(self) -> object:
        return capture.capture_raw(self)

    def cached_game_frame(self) -> CachedGameFrame | None:
        return capture.cached_game_frame(self)

    def release_cached_game_frame(self) -> None:
        return capture.release_cached_game_frame(self)

    def capture(self) -> object:
        return capture.capture(self)

    def handle_network_exception(self, frame: object) -> None:
        return network.handle_network_exception(self, frame)

    def restore_network_status(self) -> None:
        return network.restore_network_status(self)

    def active_monotonic(self) -> float:
        return network.active_monotonic(self)

    def dispatch_input(self, action: str, point: Point, callback: Callable[[WindowRef, Point], None], **fields: object) -> None:
        return guarded_input.dispatch_input(self, action, point, callback, **fields)

    def click(self, action: str, point: Point, **fields: object) -> None:
        return guarded_input.click(self, action, point, **fields)

    def interruptible_wait(self, seconds: int) -> None:
        return guarded_input.interruptible_wait(self, seconds)

    def wait_startup_icon(self, name: str, detector: Callable[[object], Observation | None], *, minimum_stable_frames: int=1) -> Observation:
        return navigation.wait_startup_icon(self, name, detector, minimum_stable_frames=minimum_stable_frames)

    def wait_stable_observation(self, name: str, detector: Callable[[object], Observation | None], timeout_ms: int, *, startup_wake: bool=False, minimum_stable_frames: int=1) -> Observation:
        return navigation.wait_stable_observation(self, name, detector, timeout_ms, startup_wake=startup_wake, minimum_stable_frames=minimum_stable_frames)

    def confirm_destination_or_main_after_entry_timeout(self, entry_name: str, destination_detector: Callable[[object], Observation | None], main_detector: Callable[[object], Observation | None], timeout_ms: int | None=None) -> tuple[bool, Observation]:
        return navigation.confirm_destination_or_main_after_entry_timeout(self, entry_name, destination_detector, main_detector, timeout_ms)

    def enter_with_retry(self, *, entry_name: str, main: Observation, main_detector: Callable[[object], Observation | None], destination_name: str, destination_detector: Callable[[object], Observation | None], click_action: str, timeout_ms: int | None=None) -> Observation:
        return navigation.enter_with_retry(self, entry_name=entry_name, main=main, main_detector=main_detector, destination_name=destination_name, destination_detector=destination_detector, click_action=click_action, timeout_ms=timeout_ms)

    def transition(self, state: RunState) -> None:
        return telemetry.transition(self, state)

    def performance_mark(self) -> tuple[float, int, float, int, float, int, float]:
        return telemetry.performance_mark(self)

    def log_performance_stage(self, mark: tuple[float, int, float, int, float, int, float], stage: str, **fields: object) -> None:
        return telemetry.log_performance_stage(self, mark, stage, **fields)

    def vision_call(self, detector: Callable[..., T], *args: object) -> T:
        return telemetry.vision_call(self, detector, *args)
