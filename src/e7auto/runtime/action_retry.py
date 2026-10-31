"""Bounded retries owned by a page transition, never by the mouse driver."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from e7auto.core.domain import StopReason
from e7auto.runtime.stop_control import StopExecution

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class Transition(Generic[R]):
    value: R | None = None
    responded: bool = False
    blocked: bool = False


def observation_signature(hit):
    return hit.object_id, hit.bounds.x // 4, hit.bounds.y // 4


def click_and_wait(runtime, *, name: str, initial: T, click: Callable[[T], None],
                   source: Callable[[object], T | None],
                   observe: Callable[[object], Transition[R]],
                   signature: Callable[[R], object],
                   target_signature: Callable[[T], object], timeout_ms: int,
                   capture: Callable[[], object] | None = None,
                   settle: Callable[[float], None] | None = None, settle_ms: int = 0,
                   outer_deadline: float | None = None, stable_frames: int | None = None,
                   single_success: Callable[[R], bool] | None = None,
                   reason: StopReason = StopReason.RECOGNITION_TIMEOUT,
                   stage: str | None = None) -> R:
    """Callbacks recognize the business state; this loop owns only retry timing.

    A single reliable response permanently disables retries in this invocation.
    Source evidence must describe the original target on the original page.
    Neither an absent result nor elapsed time alone authorizes another input.
    """
    policy = runtime.config.click_retry
    started = runtime.active_monotonic()
    deadline = started + max(timeout_ms, policy.minimum_timeout_ms) / 1000
    if outer_deadline is not None:
        deadline = min(deadline, outer_deadline)
    frame = capture or runtime.capture
    required = max(1, stable_frames or runtime.config.timing.stable_frames)
    retry_required = max(3, required)
    attempts, response_seen, outcome = 0, False, "interrupted"
    last_click = started
    previous = source_previous = None
    stable = source_stable = 0
    generation = runtime.network_recovery_generation

    def check():
        runtime.control.checkpoint()
        if runtime.active_monotonic() >= deadline:
            raise StopExecution(reason, f"click transition timeout: {name}; attempts={attempts}; "
                                f"response_seen={response_seen}")

    def send(target):
        nonlocal attempts, last_click, generation
        check()
        attempts += 1
        click(target)  # Must send only this action; do not wrap another retry owner.
        last_click = runtime.active_monotonic()
        check()
        if settle_ms:
            if settle is None:
                raise ValueError("click buffer requires the feature's guarded delay")
            settle(min(settle_ms / 1000, deadline - last_click))
            check()
        generation = runtime.network_recovery_generation

    try:
        send(initial)
        while True:
            check()
            sampled_at = runtime.active_monotonic()
            pixels = frame()
            check()
            if generation != runtime.network_recovery_generation:
                generation = runtime.network_recovery_generation
                previous = source_previous = None
                stable = source_stable = 0
                continue  # An interrupted frame cannot authorize any input.
            state = observe(pixels)
            check()
            response_seen |= state.responded or state.value is not None
            value = None if state.blocked else state.value
            key = None if value is None else signature(value)
            stable = stable + 1 if key is not None and key == previous else int(key is not None)
            previous = key
            if value is not None and (stable >= required or (single_success and single_success(value))):
                outcome = "confirmed"
                return value
            if not response_seen and not state.blocked and attempts < policy.max_attempts:
                target = source(pixels)
                check()
                key = None if target is None else target_signature(target)
                source_stable = (source_stable + 1 if key is not None and key == source_previous
                                 else int(key is not None))
                source_previous = key
                if (target is not None and source_stable >= retry_required
                        and runtime.active_monotonic() - last_click >= policy.interval_ms / 1000):
                    runtime.deps.logger.event("click_retry", action=name, stage=stage or name,
                                              next_attempt=attempts + 1,
                                              interval_ms=round((runtime.active_monotonic()-last_click)*1000))
                    check()
                    send(target)
                    previous = source_previous = None
                    stable = source_stable = 0
                    continue
            else:
                source_previous, source_stable = None, 0
            pause = min(deadline-runtime.active_monotonic(),
                        runtime.config.timing.poll_interval_ms/1000 - (runtime.active_monotonic()-sampled_at))
            if pause > 0:
                runtime.deps.clock.sleep(pause)
    except StopExecution as exc:
        outcome = exc.reason.value
        raise
    finally:
        runtime.deps.logger.event("click_transition", action=name, stage=stage or name, attempts=attempts,
                                  response_seen=response_seen, outcome=outcome,
                                  elapsed_ms=round((runtime.active_monotonic()-started)*1000, 3))
        if outcome == "confirmed":
            check()  # Slow confirmation logging must not authorize a late next action.
