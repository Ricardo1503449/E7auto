"""Choose the rightmost seal option and advance narrative to the original map."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.finish_text_and_map(choice=event)
