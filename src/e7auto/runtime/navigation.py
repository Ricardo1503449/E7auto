from __future__ import annotations
from typing import Callable, TypeVar
from e7auto.core.domain import StopReason
from e7auto.core.observations import Observation
from e7auto.runtime.stop_control import StopExecution

_ENTRY_MAX_ATTEMPTS = 2
_STARTUP_MAIN_ICON_TIMEOUT_MS = 10_000
_STARTUP_WAKE_RETRY_MS = 3_000
_STARTUP_WAKE_MAX_ATTEMPTS = 2
_STARTUP_RETURN_TIMEOUT_SECONDS = 3.0
_STARTUP_RETURN_MAX_ATTEMPTS = 2
_STARTUP_HOME_BUFFER_SECONDS = 1.0

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
        "startup_icon_confirmed", object=name, wake_sent=ctx.startup_wake_attempts > 0,
        wake_attempts=ctx.startup_wake_attempts,
        return_home=ctx.startup_return_started,
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
    if startup_wake:
        return _wait_startup_observation(ctx, name, detector, timeout_ms, minimum_stable_frames)
    deadline = ctx.active_monotonic() + timeout_ms / 1000
    stable = 0
    while ctx.active_monotonic() <= deadline:
        generation = ctx.network_recovery_generation
        frame = ctx.capture()
        if generation != ctx.network_recovery_generation:
            stable = 0
        observation = ctx.vision_call(detector, frame)
        if observation is None:
            stable = 0
            ctx.deps.logger.event("recognition", object=name, detected=False)
        else:
            stable += 1
            ctx.deps.logger.event(
                "recognition",
                object=name,
                detected=True,
                confidence=f"{observation.confidence:.6f}",
                roi=f"{observation.bounds.x},{observation.bounds.y},{observation.bounds.width},{observation.bounds.height}",
                stable=stable,
            )
            if stable >= max(minimum_stable_frames, ctx.config.timing.stable_frames):
                return observation
        ctx.control.checkpoint()
        ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)
    raise StopExecution(StopReason.RECOGNITION_TIMEOUT, f"timeout waiting for {name}")


def _wait_startup_observation(ctx, name, detector, timeout_ms, minimum_stable_frames):
    if ctx.startup_return_started:
        raise StopExecution(StopReason.RECOGNITION_TIMEOUT, "startup return already attempted")
    deadline = ctx.active_monotonic() + timeout_ms / 1000
    required = max(minimum_stable_frames, ctx.config.timing.stable_frames)
    stable = menu_stable = 0
    while ctx.active_monotonic() <= deadline:
        generation = ctx.network_recovery_generation
        frame = ctx.capture()
        if generation != ctx.network_recovery_generation:
            stable = menu_stable = 0
            ctx.control.checkpoint()
            ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)
            continue
        observation = ctx.vision_call(detector, frame)
        if observation is not None:
            stable += 1
            menu_stable = 0
            ctx.deps.logger.event("recognition", object=name, detected=True,
                                  confidence=f"{observation.confidence:.6f}", stable=stable,
                                  roi=f"{observation.bounds.x},{observation.bounds.y},{observation.bounds.width},{observation.bounds.height}")
            ctx.control.checkpoint()
            if ctx.active_monotonic() > deadline:
                break
            if stable >= required:
                return observation
        else:
            stable = 0
            menu = ctx.vision_call(ctx.deps.vision.navigation_menu, frame)
            ctx.deps.logger.event("recognition", object=name, detected=False)
            ctx.control.checkpoint()
            now = ctx.active_monotonic()
            if now > deadline:
                break
            if menu is not None:
                menu_stable += 1
                if menu_stable >= required:
                    return _return_startup_home(ctx, name, detector, menu, required)
            else:
                menu_stable = 0
                due = ctx.startup_wake_attempts == 0 or (
                    ctx.startup_last_wake_at is not None and
                    now - ctx.startup_last_wake_at >= _STARTUP_WAKE_RETRY_MS / 1000)
                if ctx.startup_wake_attempts < _STARTUP_WAKE_MAX_ATTEMPTS and due:
                    ctx.startup_wake_attempts += 1
                    ctx.click("wake_main_screen_startup", ctx.config.points["main_screen_wake"],
                              object=name, attempt=ctx.startup_wake_attempts,
                              reason="entry_and_menu_missing")
                    ctx.startup_last_wake_at = ctx.active_monotonic()
                    deadline = ctx.startup_last_wake_at + timeout_ms / 1000
        ctx.control.checkpoint()
        ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)
    raise StopExecution(StopReason.RECOGNITION_TIMEOUT, f"timeout waiting for {name}")


def _return_startup_home(ctx, name, home_detector, menu, required):
    ctx.startup_return_started = True
    stage, button = "menu", menu
    while True:
        ctx.control.checkpoint()
        ctx.startup_return_attempts[stage] += 1
        attempt = ctx.startup_return_attempts[stage]
        ctx.click("startup_open_menu" if stage == "menu" else "startup_return_home",
                  button.anchor, object=name, attempt=attempt)
        deadline = ctx.active_monotonic() + _STARTUP_RETURN_TIMEOUT_SECONDS
        home_stable = target_stable = source_stable = 0
        ctx.deps.logger.event("startup_navigation", phase=stage, attempt=attempt, status="waiting")
        while True:
            ctx.control.checkpoint()
            generation = ctx.network_recovery_generation
            frame = ctx.capture()
            if generation != ctx.network_recovery_generation:
                home_stable = target_stable = source_stable = 0
                continue  # Recovery neither renews the deadline nor authorizes a click.
            home = ctx.vision_call(home_detector, frame)
            target = source = None
            if home is not None:
                home_stable += 1
                target_stable = source_stable = 0
            else:
                home_stable = 0
                if stage == "menu":
                    target = ctx.vision_call(ctx.deps.vision.navigation_tavern, frame)
                if target is not None:
                    target_stable += 1
                    source_stable = 0
                else:
                    target_stable = 0
                    source_detector = (ctx.deps.vision.navigation_menu if stage == "menu"
                                       else ctx.deps.vision.navigation_tavern)
                    source = ctx.vision_call(source_detector, frame)
                    source_stable = source_stable + 1 if source is not None else 0
            ctx.control.checkpoint()
            now = ctx.active_monotonic()
            if now <= deadline and home_stable >= required:
                ctx.deps.logger.event("startup_navigation", phase="home", status="confirmed",
                                      menu_attempts=ctx.startup_return_attempts["menu"],
                                      tavern_attempts=ctx.startup_return_attempts["tavern"])
                if ctx.startup_return_attempts["tavern"]:
                    return _buffer_startup_home(ctx, name, home_detector, home, required)
                return home
            if now <= deadline and target_stable >= required:
                stage, button = "tavern", target
                break
            if now >= deadline:
                # Only current source evidence, never a remembered anchor, can retry.
                if (attempt < _STARTUP_RETURN_MAX_ATTEMPTS and source_stable >= required
                        and source is not None and home is None and target is None):
                    button = source
                    ctx.deps.logger.event("startup_navigation", phase=stage, status="retry",
                                          completed_attempt=attempt)
                    break
                ctx.deps.logger.event("startup_navigation", phase=stage, attempt=attempt, status="timeout")
                raise StopExecution(StopReason.RECOGNITION_TIMEOUT,
                                    f"startup return timed out: {stage}, attempt={attempt}")
            ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)


def _buffer_startup_home(ctx, name, detector, home, required):
    """Keep the confirmed anchor while allowing the return animation to finish."""
    while True:
        started = ctx.active_monotonic()
        ctx.deps.logger.event(
            "startup_navigation", phase="home_buffer", status="waiting", object=name,
            duration_ms=int(_STARTUP_HOME_BUFFER_SECONDS * 1000),
        )
        while True:
            ctx.control.checkpoint()
            generation = ctx.network_recovery_generation
            ctx.capture()  # Window/network guards only; reuse the confirmed entry.
            ctx.control.checkpoint()
            if generation != ctx.network_recovery_generation:
                ctx.deps.logger.event(
                    "startup_navigation", phase="home_buffer", status="invalidated",
                    object=name, reason="network_recovery",
                )
                home = _reconfirm_startup_home(ctx, name, detector, required)
                break  # A fresh confirmation starts a full one-second buffer.
            elapsed = ctx.active_monotonic() - started
            if elapsed >= _STARTUP_HOME_BUFFER_SECONDS:
                ctx.deps.logger.event(
                    "startup_navigation", phase="home_buffer", status="completed",
                    object=name, elapsed_ms=round(elapsed * 1000),
                    client_x=home.anchor.x, client_y=home.anchor.y,
                )
                ctx.control.checkpoint()
                return home
            ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)


def _reconfirm_startup_home(ctx, name, detector, required):
    """Discard pre-recovery evidence without restarting navigation or waking."""
    deadline = ctx.active_monotonic() + ctx.config.timing.entry_timeout_ms / 1000
    stable = 0
    while ctx.active_monotonic() <= deadline:
        ctx.control.checkpoint()
        generation = ctx.network_recovery_generation
        frame = ctx.capture()
        if generation != ctx.network_recovery_generation:
            stable = 0
        elif ctx.active_monotonic() <= deadline:
            home = ctx.vision_call(detector, frame)
            stable = stable + 1 if home is not None else 0
            ctx.control.checkpoint()
            if ctx.active_monotonic() <= deadline and stable >= required:
                ctx.deps.logger.event(
                    "startup_navigation", phase="home_reconfirm", status="confirmed",
                    object=name, stable=stable,
                )
                if ctx.active_monotonic() <= deadline:
                    return home
                break
        ctx.control.checkpoint()
        ctx.deps.clock.sleep(ctx.config.timing.poll_interval_ms / 1000)
    ctx.deps.logger.event("startup_navigation", phase="home_reconfirm", status="timeout",
                          object=name, reason="entry_missing_after_network_recovery")
    raise StopExecution(StopReason.RECOGNITION_TIMEOUT,
                        f"timeout reconfirming {name} after startup return network recovery")


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
