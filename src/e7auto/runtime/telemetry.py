from __future__ import annotations
from typing import Callable, TypeVar
import time
from e7auto.core.domain import RunState
from e7auto.vision.frames import AdaptedFrame
T = TypeVar("T")


def transition(ctx, state: RunState) -> None:
    previous = ctx.publisher.snapshot.state
    ctx.publisher.mutate(lambda snapshot: snapshot.transitioned(state))
    ctx.deps.logger.event("state_transition", previous=previous.value, current=state.value)


def performance_mark(ctx) -> tuple[float, int, float, int, float, int, float]:
    return (
        time.perf_counter(),
        ctx.capture_count,
        ctx.capture_seconds,
        ctx.vision_calls,
        ctx.vision_seconds,
        ctx.normalization_count,
        ctx.normalization_seconds,
    )


def log_performance_stage(
    ctx,
    mark: tuple[float, int, float, int, float, int, float],
    stage: str,
    **fields: object,
) -> None:
    (
        started,
        capture_count,
        capture_seconds,
        vision_calls,
        vision_seconds,
        normalization_count,
        normalization_seconds,
    ) = mark
    ctx.deps.logger.event(
        "performance_stage",
        stage=stage,
        duration_ms=f"{(time.perf_counter() - started) * 1000:.3f}",
        capture_count=ctx.capture_count - capture_count,
        capture_ms=f"{(ctx.capture_seconds - capture_seconds) * 1000:.3f}",
        vision_calls=ctx.vision_calls - vision_calls,
        vision_ms=f"{(ctx.vision_seconds - vision_seconds) * 1000:.3f}",
        normalization_count=ctx.normalization_count - normalization_count,
        normalization_ms=f"{(ctx.normalization_seconds - normalization_seconds) * 1000:.3f}",
        **fields,
    )


def vision_call(ctx, detector: Callable[..., T], *args: object) -> T:
    adapted = tuple(arg for arg in args if isinstance(arg, AdaptedFrame))
    before_count = sum(frame.normalization_count for frame in adapted)
    before_seconds = sum(frame.normalization_seconds for frame in adapted)
    started = time.perf_counter()
    try:
        return detector(*args)
    finally:
        ctx.vision_calls += 1
        ctx.vision_seconds += time.perf_counter() - started
        ctx.normalization_count += (
            sum(frame.normalization_count for frame in adapted) - before_count
        )
        ctx.normalization_seconds += (
            sum(frame.normalization_seconds for frame in adapted) - before_seconds
        )
