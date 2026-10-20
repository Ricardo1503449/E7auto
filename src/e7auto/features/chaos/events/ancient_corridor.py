"""Clear the left corridor, advance its text and return to this floor."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.finish_text_and_map(choice=event)
