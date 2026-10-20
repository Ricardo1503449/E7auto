"""Confirmed, bounded closing of known event reward and rank cards."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from e7auto.core.domain import StopReason
from e7auto.runtime.stop_control import StopExecution

if TYPE_CHECKING:
    from .context import EventContext
    from ..contracts import Detection


def close_reward_cards(context: EventContext, *, count: int, close_key: str,
                       next_page: Callable[[object], Detection | None] | None = None) -> None:
    if count < 1 or close_key not in ("event_reward_close", "rank_close"):
        raise ValueError("invalid event reward close sequence")
    runtime, vision = context.runtime, context.vision
    timing = context.settings.timing
    page, attempts = 1, 0
    total_attempts = 0
    deadline = runtime.active_monotonic() + timing["page_timeout_ms"] / 1000
    last_click = 0.0
    clicked_card = None
    stable_card = stable_key = None
    stable_since, stable_frames = 0.0, 0
    terminal_key, terminal_frames = None, 0
    absent_frames = 0
    phase = "waiting_for_stable_card"

    def fail(detail):
        raise StopExecution(StopReason.RECOGNITION_TIMEOUT,
                            f"event reward close failed: {close_key}; page={page}/{count}; "
                            f"attempts={attempts}; phase={phase}; {detail}")

    while runtime.active_monotonic() <= deadline:
        sampled_at = runtime.active_monotonic()
        generation = runtime.network_recovery_generation
        frame = context.frame()
        now = runtime.active_monotonic()
        if generation != runtime.network_recovery_generation:
            # Keep submitted inputs, but rebuild stability entirely from fresh frames.
            stable_card = stable_key = terminal_key = None
            stable_frames = terminal_frames = 0
            absent_frames = 0
        close = vision.control(frame, close_key)
        if close is None:
            stable_card = stable_key = None
            stable_frames = 0
            absent_frames += 1
            if next_page is not None:
                # The caller owns the destination identity. A background map or
                # unrelated text cannot complete a multi-stage event's close.
                hit = next_page(frame) if attempts else None
                key = ("next_page", hit.key, hit.bounds.x // 4, hit.bounds.y // 4) if hit is not None else None
            else:
                view = vision.map_view(frame) if attempts else None
                text = vision.control(frame, "text_continue") if attempts and view is None else None
                key = ("map", view.floor) if view is not None and view.floor == context.floor else (
                    ("text", text.key) if text is not None else None)
            terminal_frames = terminal_frames + 1 if key is not None and key == terminal_key else int(key is not None)
            terminal_key = key
            # Moving text triangles retain their single reliable hit semantics;
            # it is the close prompt's absence that must remain stable.
            finished = (key is not None and key[0] == "text" and absent_frames >= timing["stable_frames"])
            if finished or terminal_frames >= timing["stable_frames"]:
                if page != count:
                    fail("returned before the expected next card")
                runtime.deps.logger.event("chaos_event_close_confirmed", control=close_key,
                                          pages=count, attempts=total_attempts, outcome=key[0])
                return
        else:
            terminal_key, terminal_frames = None, 0
            absent_frames = 0
            key = (close.key, close.bounds.x // 4, close.bounds.y // 4)
            if stable_card is None or key != stable_key or not vision.event_card_stable(frame, stable_card):
                stable_card = vision.event_card_snapshot(frame)
                stable_key, stable_since, stable_frames = key, now, 1
            else:
                stable_frames += 1
            ready = (stable_frames >= timing["stable_frames"]
                     and now - stable_since >= timing["event_close_stable_ms"] / 1000)
            click = ready and attempts == 0
            if ready and attempts:
                if vision.event_card_changed(frame, clicked_card):
                    if page < count:
                        page += 1
                        attempts = 0
                        deadline = now + timing["page_timeout_ms"] / 1000
                        click = True
                    else:
                        # Never turn an unexpected extra card into an authorized retry.
                        phase = "unexpected_card_after_final_close"
                elif (vision.event_card_stable(frame, clicked_card)
                      and now - last_click >= timing["event_close_retry_ms"] / 1000):
                    if attempts >= timing["event_close_max_attempts"]:
                        fail("card remains visible after maximum close attempts")
                    click = True
            if click:
                if attempts:
                    runtime.deps.logger.event("chaos_event_close_retry", control=close_key,
                                              page=page, next_attempt=attempts + 1)
                attempts += 1
                total_attempts += 1
                clicked_card = vision.event_card_snapshot(frame)
                last_click = now
                phase = "waiting_for_close_result"
                context.tap(close)
                stable_card = stable_key = None
                stable_frames = 0
        runtime.control.checkpoint()
        remaining = timing["poll_ms"] / 1000 - (runtime.active_monotonic() - sampled_at)
        if remaining > 1e-6:
            runtime.deps.clock.sleep(remaining)
    fail("timed out")
