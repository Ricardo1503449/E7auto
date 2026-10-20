"""Choose the right map option and leave through the narrative."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.finish_text_and_map(choice=event)
