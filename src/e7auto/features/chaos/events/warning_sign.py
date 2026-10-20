"""Follow the warning and leave."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.finish_text_and_map(choice=event)
