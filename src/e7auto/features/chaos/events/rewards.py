"""Confirmed, bounded closing of known event reward and rank cards."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from e7auto.core.domain import StopReason
from e7auto.runtime.stop_control import StopExecution

if TYPE_CHECKING:
    from .context import EventContext
    from ..contracts import Detection


class CardWindow:
    """The original card stability checks, also sampled during result recognition."""
    def __init__(self, settings, vision, close_key):
        self.settings, self.vision, self.close_key = settings, vision, close_key
        self.reset()

    def reset(self):
        self.card = self.current_card = self.close = self.key = None
        self.since, self.frames, self.generation = 0., 0, -1

    def observe(self, runtime, frame, close):
        if self.generation != runtime.network_recovery_generation:
            self.reset()
        self.generation = runtime.network_recovery_generation
        if close is None:
            self.reset()
            return
        key = close.key, close.bounds.x // 4, close.bounds.y // 4
        if self.card is None or key != self.key or not self.vision.event_card_stable(frame, self.card):
            self.card = self.vision.event_card_snapshot(frame)
            self.key, self.since, self.frames = key, runtime.active_monotonic(), 1
        else:
            self.frames += 1
        self.close = close
        self.current_card = self.vision.event_card_snapshot(frame)

    def ready(self, now):
        return (self.frames >= self.settings.timing['stable_frames']
                and now-self.since >= self.settings.timing['event_close_stable_ms']/1000 - 1e-9)


def close_reward_cards(context: EventContext, *, count: int, close_key: str,
                       next_page: Callable[[object], Detection | None] | None = None):
    if count < 1 or close_key not in ("event_reward_close", "rank_close"):
        raise ValueError("invalid event reward close sequence")
    runtime, vision = context.runtime, context.vision
    timing = context.settings.timing
    page, attempts = 1, 0
    total_attempts = 0
    deadline = runtime.active_monotonic() + timing["page_timeout_ms"] / 1000
    last_click = 0.0
    clicked_card = None
    window = context.take_card(close_key)
    pending = window is not None
    window = window or CardWindow(context.settings, vision, close_key)
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
        frame = None if pending else context.frame()
        now = runtime.active_monotonic()
        if not pending and generation != runtime.network_recovery_generation:
            # Keep submitted inputs, but rebuild stability entirely from fresh frames.
            window.reset()
            terminal_key, terminal_frames = None, 0
            absent_frames = 0
        close = window.close if pending else vision.control(frame, close_key)
        if not pending:
            window.observe(runtime, frame, close)
        pending = False
        if close is None:
            absent_frames += 1
            hit = view = text = None
            if next_page is not None:
                # The caller owns the destination identity. A background map or
                # unrelated text cannot complete a multi-stage event's close.
                hit = next_page(frame) if attempts else None
                key = ("next_page", hit.key, hit.bounds.x // 4, hit.bounds.y // 4) if hit is not None else None
            else:
                view = vision.map_view(frame) if attempts else None
                text = vision.control(frame, "text_continue") if attempts else None
                key = (("text", text.key) if text is not None else
                       ("map", view.floor) if view is not None and view.floor == context.floor else None)
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
                result = hit if next_page is not None else text if text is not None else view
                if next_page is None:
                    context.remember_terminal(result)
                return result
        else:
            terminal_key, terminal_frames = None, 0
            absent_frames = 0
            ready = window.ready(now)
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
                clicked_card = window.current_card
                last_click = now
                phase = "waiting_for_close_result"
                context.tap(close)
                window.reset()
        runtime.control.checkpoint()
        remaining = timing["poll_ms"] / 1000 - (runtime.active_monotonic() - sampled_at)
        if remaining > 1e-6:
            runtime.deps.clock.sleep(remaining)
    fail("timed out")
