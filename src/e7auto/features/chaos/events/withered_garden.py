"""Follow the spirit's scent to recover, then return to the map."""
from .context import EventContext
from ..contracts import Detection


def run(context: EventContext, event: Detection) -> None:
    context.finish_text_and_map(choice=event)
