from __future__ import annotations

from dataclasses import dataclass
import time

from ..ports import (
    GameVision,
    CaptureService,
    Clock,
    InputService,
    OverlayService,
    RuntimeEnvironment,
    TextRunLogger,
    WindowService,
)


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(slots=True)
class AutomationDependencies:
    windows: WindowService
    capture: CaptureService
    inputs: InputService
    overlay: OverlayService
    vision: GameVision
    clock: Clock
    logger: TextRunLogger
    runtime: RuntimeEnvironment
