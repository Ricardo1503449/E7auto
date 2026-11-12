from __future__ import annotations
from typing import Callable, Generic, TypeVar
from datetime import datetime
import time
from e7auto.core.domain import StopReason
from e7auto.vision.frames import adapt_frame
from e7auto.core.ports import CachedGameFrame, CaptureError, CaptureTiming
from e7auto.runtime.stop_control import StopExecution
from e7auto.runtime.confirmation import Confirmation

T = TypeVar("T")


class RecoveryProbe(Generic[T]):
    """Sample one wait's existing detector on network-clear frames as well.

    Callbacks only inspect the supplied frame and update local evidence. They
    must not capture, send input, or advance a business stage. The last result
    uses the existing single-use confirmation contract; no old frame is replayed.
    """
    def __init__(self, observe: Callable[[object], T], reset: Callable[[], None]):
        self.observe, self._reset = observe, reset
        self._handoff: Confirmation[tuple[T]] | None = None

    def reset(self) -> None:
        self._handoff = None
        self._reset()

    def sample(self, ctx, frame: object) -> None:
        self._handoff = Confirmation.record(ctx, (self.observe(frame),))

    def capture(self, ctx, capture: Callable[[], object] | None = None) -> T:
        previous = getattr(ctx, "recovery_probe", None)
        ctx.recovery_probe = self
        self._handoff = None
        try:
            while True:
                generation = ctx.network_recovery_generation
                frame = (capture or ctx.capture)()
                handoff, self._handoff = self._handoff, None
                value = handoff.take(ctx) if handoff is not None else None
                if value is not None:
                    return value[0]
                if handoff is not None or generation != ctx.network_recovery_generation:
                    self.reset()
                    continue  # Only a bound recovery handoff may replace a new capture.
                ctx.recovery_probe = previous
                return self.observe(frame)
        finally:
            self._handoff = None
            ctx.recovery_probe = previous


def capture_raw(ctx) -> object:
    started = time.perf_counter()
    try:
        ctx.ensure_window()
        assert ctx.window is not None and ctx.baseline_bounds is not None
        set_checkpoint = getattr(ctx.deps.capture, "set_wait_checkpoint", None)
        if set_checkpoint is not None:
            set_checkpoint(ctx.ensure_window)
        try:
            frame = ctx.deps.capture.capture_client(ctx.window, ctx.baseline_bounds)
        except CaptureError as exc:
            raise StopExecution(StopReason.CAPTURE_FAILURE, str(exc)) from exc
        finally:
            if set_checkpoint is not None:
                set_checkpoint(None)
        # Backends without source timestamps leave their age explicitly unknown.
        timing = getattr(ctx.deps.capture, "last_capture_timing", None)
        ctx.last_captured_frame = CachedGameFrame(
            frame, datetime.now().astimezone().isoformat(timespec="milliseconds"),
            ctx.deps.clock.monotonic(),
            timing if isinstance(timing, CaptureTiming) else None,
        )
        ctx.control.checkpoint()
        assert ctx.transform is not None
        return adapt_frame(frame, ctx.transform)
    finally:
        ctx.capture_count += 1
        ctx.capture_seconds += time.perf_counter() - started


def cached_game_frame(ctx) -> CachedGameFrame | None:
    return ctx.last_captured_frame


def release_cached_game_frame(ctx) -> None:
    ctx.last_captured_frame = None


def capture(ctx) -> object:
    while True:
        generation = ctx.network_recovery_generation
        frame = ctx.capture_raw()
        recovered = ctx.handle_network_exception(frame)
        if generation == ctx.network_recovery_generation:
            return frame
        if ctx.recovery_probe is not None and recovered is not None:
            return recovered
