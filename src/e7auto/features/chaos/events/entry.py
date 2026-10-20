"""Advance pre-option narrative until the dispatcher recognizes one event."""
from e7auto.core.domain import StopReason
from .context import EventContext
from . import dispatcher


def run(context: EventContext) -> None:
    context.vision.begin_event_recognition()
    try:
        while True:
            def detect(frame):
                text = context.vision.control(frame, "text_continue")
                if text is not None:
                    context.vision.invalidate_event_recognition()
                    return text
                return dispatcher.identify(context, frame)

            hit = context.wait("event_entry", detect, reason=StopReason.CHAOS_UNKNOWN_CONTENT, moving_text=True)
            if hit.key == "text_continue":
                context.tap(hit)
                continue
            break
    finally:
        summary = context.vision.end_event_recognition()
    dispatcher.dispatch(context, hit, summary)
