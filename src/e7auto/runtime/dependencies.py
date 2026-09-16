from __future__ import annotations

from dataclasses import dataclass
import time

from e7auto.core.ports import NetworkVisionPort
from typing import Generic, TypeVar
VisionT = TypeVar("VisionT", bound=NetworkVisionPort)
from e7auto.core.ports import CaptureService, Clock, InputService, OverlayService, RuntimeEnvironment, TextRunLogger, WindowService


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(slots=True)
class AutomationDependencies(Generic[VisionT]):
    windows: WindowService
    capture: CaptureService
    inputs: InputService
    overlay: OverlayService
    vision: VisionT
    clock: Clock
    logger: TextRunLogger
    runtime: RuntimeEnvironment
