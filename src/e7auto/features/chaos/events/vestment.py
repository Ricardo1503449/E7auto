"""Take the vestment, choose red light in either known subchoice state, then leave."""
from e7auto.core.domain import StopReason
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    def subchoice(frame):
        for key in ("event_vestment_no_loot", "event_vestment_with_loot"):
            hit = context.vision.control(frame, key)
            if hit is not None:
                return hit
        return None

    # Both templates belong only to this stage and target the same left option.
    choice = context.choose(event, next_page=subchoice, name="vestment_subchoice",
                            reason=StopReason.CHAOS_UNKNOWN_CONTENT)
    context.finish_text_and_map(choice=choice)
