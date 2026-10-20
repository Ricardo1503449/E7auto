"""Bounded retries for an event option while its own page remains visible."""
from __future__ import annotations

from math import ceil
from typing import TYPE_CHECKING, Callable, TypeVar

from e7auto.core.domain import StopReason
from e7auto.runtime.stop_control import StopExecution

if TYPE_CHECKING:
    from .context import EventContext
    from ..contracts import Detection

T = TypeVar("T")


def choose(context: EventContext, choice: Detection, *, next_page: Callable[[object], T | None],
           name: str, moving_text: bool = False,
           reason: StopReason = StopReason.RECOGNITION_TIMEOUT,
           retry_blocked: Callable[[object], bool] | None = None) -> T:
    runtime, timing = context.runtime, context.settings.timing
    deadline = runtime.active_monotonic() + timing["page_timeout_ms"] / 1000
    attempts = 1
    last_click = runtime.active_monotonic()
    generation = runtime.network_recovery_generation
    previous, stable = None, 0

    def fail(detail: str) -> None:
        raise StopExecution(reason,
                            f"event choice failed: {choice.key}; attempts={attempts}/"
                            f"{timing['event_choice_max_attempts']}; phase={name}; {detail}")

    def check_deadline() -> None:
        if runtime.active_monotonic() >= deadline:
            fail("timed out")

    context.tap(choice)
    check_deadline()

    def transition(frame):
        nonlocal attempts, last_click, generation, previous, stable
        check_deadline()
        if generation != runtime.network_recovery_generation:
            generation = runtime.network_recovery_generation
            previous, stable = None, 0
        # Any destination evidence takes priority, even before its third frame.
        result = next_page(frame)
        check_deadline()
        if result is not None:
            previous, stable = None, 0
            return result
        if retry_blocked is not None and retry_blocked(frame):
            previous, stable = None, 0
            check_deadline()
            return None
        current = context.vision.control(frame, choice.key)
        check_deadline()
        key = None if current is None else (current.key, current.bounds.x // 4,
                                            current.bounds.y // 4, current.bounds.width // 4,
                                            current.bounds.height // 4)
        stable = stable + 1 if key is not None and key == previous else int(key is not None)
        previous = key
        now = runtime.active_monotonic()
        if (stable >= timing["stable_frames"]
                and now - last_click >= timing["event_choice_retry_ms"] / 1000):
            if attempts >= timing["event_choice_max_attempts"]:
                fail("same option remains visible after maximum attempts")
            runtime.deps.logger.event("chaos_event_choice_retry", choice=choice.key,
                                      stage=name, next_attempt=attempts + 1)
            # Current pixels own the retry anchor; never replay the initial point.
            attempts += 1
            last_click = now
            context.tap(current)
            previous, stable = None, 0
        return None

    remaining_ms = max(1, ceil((deadline - runtime.active_monotonic()) * 1000))
    try:
        return context.wait(name, transition, timeout_ms=remaining_ms,
                            reason=reason, moving_text=moving_text)
    except StopExecution as exc:
        if exc.reason == reason and exc.detail == f"chaos timeout: {name}":
            fail("timed out")
        raise
