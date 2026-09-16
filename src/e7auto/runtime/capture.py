from __future__ import annotations
from typing import TypeVar
from datetime import datetime
import time
from e7auto.core.domain import StopReason
from e7auto.vision.frames import adapt_frame
from e7auto.core.ports import CachedGameFrame, CaptureError
from e7auto.runtime.stop_control import StopExecution


def capture_raw(ctx) -> object:
    started = time.perf_counter()
    try:
        ctx.ensure_window()
        assert ctx.window is not None and ctx.baseline_bounds is not None
        try:
            frame = ctx.deps.capture.capture_client(ctx.window, ctx.baseline_bounds)
        except CaptureError as exc:
            raise StopExecution(StopReason.CAPTURE_FAILURE, str(exc)) from exc
        ctx.last_captured_frame = CachedGameFrame(
            frame, datetime.now().astimezone().isoformat(timespec="milliseconds"),
            ctx.deps.clock.monotonic(),
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
        ctx.handle_network_exception(frame)
        if not ctx.discard_interrupted_frames or generation == ctx.network_recovery_generation:
            return frame
