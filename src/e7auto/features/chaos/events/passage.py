"""Choose the middle passage, recognize recruitment and confirm forfeiture."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.choose(event, next_page=lambda frame: context.vision.control(frame, "event_tablet_continue")
                   if context.vision.control(frame, "event_recruit_enter") is not None else None)
    context.act("event_tablet_continue", lambda frame: context.vision.control(frame, "event_recruit_enter") is not None)
    context.act("unclaimed_confirm", lambda frame: context.vision.control(frame, "unclaimed_prompt") is not None)
    context.finish_text_and_map()
