from __future__ import annotations
from typing import TypeVar
from e7auto.core.domain import OverlayActivityStatus


def handle_network_exception(ctx, frame: object) -> None:
    error_detector = getattr(ctx.deps.vision, "network_connection_error", None)
    retry_detector = getattr(ctx.deps.vision, "network_retry", None)
    if error_detector is None or retry_detector is None or ctx.handling_network:
        return
    if ctx.vision_call(error_detector, frame) is None:
        return
    ctx.network_recovery_generation += 1
    ctx.on_recovery("network_recovery")
    ctx.handling_network = True
    recovery_started = ctx.deps.clock.monotonic()
    previous_status = ctx.publisher.snapshot.overlay_status
    ctx.network_status_before_reconnect = previous_status
    ctx.publisher.mutate(
        lambda snapshot: snapshot.with_overlay_status(
            OverlayActivityStatus.RECONNECTING
        )
    )
    try:
        ctx.deps.logger.event("network_error_detected", action="pause_and_save")
        while True:
            ctx.control.checkpoint()
            current = ctx.capture_raw()
            if ctx.vision_call(error_detector, current) is None:
                ctx.deps.logger.event("network_recovered", action="already_cleared")
                ctx.restore_network_status()
                return
            retry = ctx.vision_call(retry_detector, current)
            if retry is not None:
                ctx.deps.logger.event("network_retry_detected", confidence=f"{retry.confidence:.6f}")
                ctx.click("network_retry", ctx.config.points["main_screen_wake"])
                ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)
                if ctx.vision_call(error_detector, ctx.capture_raw()) is None:
                    ctx.deps.logger.event("network_recovered", action="retry_click")
                    ctx.restore_network_status()
                    return
            else:
                ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)
    finally:
        ctx.network_paused_seconds += (
            ctx.deps.clock.monotonic() - recovery_started
        )
        ctx.handling_network = False
        ctx.network_status_before_reconnect = None


def restore_network_status(ctx) -> None:
    status = ctx.network_status_before_reconnect
    if status is not None:
        ctx.publisher.mutate(
            lambda snapshot: snapshot.with_overlay_status(status)
        )


def active_monotonic(ctx) -> float:
    """Return elapsed automation time excluding completed network recovery."""

    now = ctx.deps.clock.monotonic()
    return now - ctx.network_paused_seconds
