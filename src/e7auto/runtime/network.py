"""One shared recovery service; mutable recovery state belongs to each runtime."""
from __future__ import annotations

from typing import Callable

from e7auto.core.domain import OverlayActivityStatus
from e7auto.core.observations import NetworkObservation
from e7auto.core.ports import NetworkVisionPort


class NetworkService:
    def __init__(self, detector: NetworkVisionPort, *, clear_frames: int, clear_ms: int,
                 clear_cache: Callable[[], None] | None = None):
        if type(clear_frames) is not int or clear_frames < 3:
            raise ValueError("Network clear confirmation requires at least three frames")
        if type(clear_ms) is not int or clear_ms < 300:
            raise ValueError("Network clear confirmation requires at least 300ms")
        self.detector = detector
        self.clear_frames = clear_frames
        self.clear_ms = clear_ms
        self._clear_cache = clear_cache

    def clear_template_cache(self) -> None:
        if self._clear_cache is not None:
            self._clear_cache()

    def observe(self, frame: object) -> NetworkObservation:
        """Read pixels only, so a held pointer can be released before recovery."""
        return self.detector.observe(frame)

    def recover(self, ctx, detected: NetworkObservation) -> object | None:
        if ctx.handling_network or not detected.blocked:
            return
        ctx.control.checkpoint()
        started = ctx.deps.clock.monotonic()
        ctx.network_pause_started = started
        ctx.handling_network = True
        ctx.network_status_before_reconnect = ctx.publisher.snapshot.overlay_status
        absent_since = None
        absent_frames = 0
        try:
            ctx.network_recovery_generation += 1
            ctx.on_recovery("network_recovery")
            if ctx.recovery_probe is not None:
                ctx.recovery_probe.reset()
            ctx.publisher.mutate(lambda snapshot: snapshot.with_overlay_status(
                OverlayActivityStatus.RECONNECTING))
            ctx.deps.logger.event("network_error_detected", action="pause_and_save",
                                  source="loading_icon" if detected.loading is not None else "error_text")
            while True:
                ctx.control.checkpoint()
                frame = ctx.capture_raw()
                current = ctx.probe_network(frame)
                ctx.control.checkpoint()
                now = ctx.deps.clock.monotonic()
                if current.visible:
                    if absent_frames and ctx.recovery_probe is not None:
                        ctx.recovery_probe.reset()
                    absent_since, absent_frames = None, 0
                    if current.loading is None and current.error is not None and current.retry is not None:
                        ctx.deps.logger.event("network_retry_detected",
                                              confidence=f"{current.retry.confidence:.6f}")
                        ctx.click("network_retry", ctx.config.points["main_screen_wake"])
                else:
                    if absent_since is None:
                        absent_since = now
                    absent_frames += 1
                    if ctx.recovery_probe is not None:
                        ctx.recovery_probe.sample(ctx, frame)
                    if (absent_frames >= self.clear_frames
                            and (now - absent_since) * 1000 >= self.clear_ms - 1e-6):
                        ctx.control.checkpoint()
                        ctx.deps.logger.event("network_recovered", action="signals_cleared")
                        ctx.restore_network_status()
                        return frame
                ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)
        finally:
            ctx.network_paused_seconds += ctx.deps.clock.monotonic() - started
            ctx.network_pause_started = None
            ctx.handling_network = False
            ctx.network_status_before_reconnect = None


def handle_network_exception(ctx, frame: object) -> object | None:
    if not ctx.handling_network:
        return ctx.recover_network(ctx.probe_network(frame))


def restore_network_status(ctx) -> None:
    status = ctx.network_status_before_reconnect
    if status is not None:
        ctx.publisher.mutate(lambda snapshot: snapshot.with_overlay_status(status))


def active_monotonic(ctx) -> float:
    """Business deadlines stop during both current and completed network waits."""
    now = ctx.deps.clock.monotonic()
    business_now = ctx.network_pause_started if ctx.network_pause_started is not None else now
    return business_now - ctx.network_paused_seconds
