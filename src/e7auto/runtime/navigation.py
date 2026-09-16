from __future__ import annotations
from typing import Callable, TypeVar
from e7auto.core.domain import StopReason
from e7auto.core.observations import Observation
from e7auto.runtime.stop_control import StopExecution

_ENTRY_MAX_ATTEMPTS = 3
_STARTUP_MAIN_ICON_TIMEOUT_MS = 10_000

def wait_startup_icon(
    ctx,
    name: str,
    detector: Callable[[object], Observation | None],
    *,
    minimum_stable_frames: int = 1,
) -> Observation:
    observation = ctx.wait_stable_observation(
        name, detector, _STARTUP_MAIN_ICON_TIMEOUT_MS,
        startup_wake=True, minimum_stable_frames=minimum_stable_frames,
    )
    ctx.deps.logger.event(
        "startup_icon_confirmed", object=name, wake_sent=ctx.startup_wake_sent,
    )
    return observation


def wait_stable_observation(
    ctx,
    name: str,
    detector: Callable[[object], Observation | None],
    timeout_ms: int,
    *,
    startup_wake: bool = False,
    minimum_stable_frames: int = 1,
) -> Observation:
    deadline = ctx.active_monotonic() + timeout_ms / 1000
    stable = 0
    while ctx.active_monotonic() <= deadline:
        generation = ctx.network_recovery_generation
        frame = ctx.capture()
        if generation != ctx.network_recovery_generation:
            stable = 0
            if startup_wake:
                # Shop capture can return the interrupted frame after
                # recovery. Never use it to authorize a wake-up click.
                ctx.control.checkpoint()
                ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)
                continue
        observation = ctx.vision_call(detector, frame)
        if observation is None:
            stable = 0
            ctx.deps.logger.event("recognition", object=name, detected=False)
            if startup_wake and not ctx.startup_wake_sent:
                ctx.startup_wake_sent = True
                ctx.click(
                    "wake_main_screen_startup", ctx.config.points["main_screen_wake"],
                    object=name,
                )
                # Allow a complete redraw window after the single wake;
                # subsequent misses and network recovery never resend it.
                deadline = ctx.active_monotonic() + timeout_ms / 1000
        else:
            stable += 1
            ctx.deps.logger.event(
                "recognition",
                object=name,
                detected=True,
                confidence=f"{observation.confidence:.6f}",
                roi=f"{observation.roi.x},{observation.roi.y},{observation.roi.width},{observation.roi.height}",
                stable=stable,
            )
            if stable >= max(minimum_stable_frames, ctx.config.timing.stable_frames):
                return observation
        ctx.control.checkpoint()
        ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)
    raise StopExecution(StopReason.RECOGNITION_TIMEOUT, f"timeout waiting for {name}")


def confirm_destination_or_main_after_entry_timeout(
    ctx,
    entry_name: str,
    destination_detector: Callable[[object], Observation | None],
    main_detector: Callable[[object], Observation | None],
    timeout_ms: int | None = None,
) -> tuple[bool, Observation]:
    """Prefer the destination; authorize a retry only on a stable main screen."""

    deadline = ctx.active_monotonic() + (
        timeout_ms if timeout_ms is not None else ctx.config.timing.entry_timeout_ms
    ) / 1000
    destination_stable = 0
    main_stable = 0
    while ctx.active_monotonic() <= deadline:
        generation = ctx.network_recovery_generation
        frame = ctx.capture()
        if generation != ctx.network_recovery_generation:
            destination_stable = main_stable = 0
        destination = ctx.vision_call(destination_detector, frame)
        if destination is None:
            destination_stable = 0
        else:
            destination_stable += 1
            if destination_stable >= ctx.config.timing.stable_frames:
                return True, destination

        main = ctx.vision_call(main_detector, frame)
        if main is None:
            main_stable = 0
        else:
            main_stable += 1
            if main_stable >= ctx.config.timing.stable_frames:
                return False, main
        ctx.control.checkpoint()
        ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)
    raise StopExecution(
        StopReason.RECOGNITION_TIMEOUT,
        f"cannot confirm {entry_name} or main screen after entry timeout",
    )


def enter_with_retry(
    ctx,
    *,
    entry_name: str,
    main: Observation,
    main_detector: Callable[[object], Observation | None],
    destination_name: str,
    destination_detector: Callable[[object], Observation | None],
    click_action: str,
    timeout_ms: int | None = None,
) -> Observation:
    timeout_ms = ctx.config.timing.entry_timeout_ms if timeout_ms is None else timeout_ms
    for attempt in range(1, _ENTRY_MAX_ATTEMPTS + 1):
        ctx.click(click_action, main.anchor, attempt=attempt)
        try:
            return ctx.wait_stable_observation(
                destination_name,
                destination_detector,
                timeout_ms,
            )
        except StopExecution as exc:
            if exc.reason is not StopReason.RECOGNITION_TIMEOUT:
                raise
            arrived, observation = ctx.confirm_destination_or_main_after_entry_timeout(
                entry_name, destination_detector, main_detector, timeout_ms,
            )
            if arrived:
                return observation
            main = observation
            if attempt >= _ENTRY_MAX_ATTEMPTS:
                raise StopExecution(
                    StopReason.RECOGNITION_TIMEOUT,
                    f"{entry_name} entry failed after {_ENTRY_MAX_ATTEMPTS} attempts; "
                    "main screen remains visible",
                )
            ctx.deps.logger.event(
                f"{entry_name}_entry_retry",
                completed_attempt=attempt,
                next_attempt=attempt + 1,
                confidence=f"{main.confidence:.6f}",
                logical_x=main.anchor.x,
                logical_y=main.anchor.y,
            )
    raise AssertionError("entry retry loop must return or stop")
