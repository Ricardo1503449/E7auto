"""Right tablet option, decline recruitment, confirm forfeiture, narrative and map."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.choose(event, next_page=lambda frame: context.vision.control(frame, "event_tablet_continue"))
    context.act("event_tablet_continue")
    context.act("unclaimed_confirm", lambda frame: context.vision.control(frame, "unclaimed_prompt") is not None)
    context.finish_text_and_map()
