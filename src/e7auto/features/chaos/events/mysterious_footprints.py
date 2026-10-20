"""Erase the footprints; handle an optional experience level-up within this event."""
from ..contracts import Detection
from .context import EventContext
from .experience import finish_with_optional_levelup


def run(context: EventContext, event: Detection) -> None:
    finish_with_optional_levelup(context, wait_name="footprints_text_or_map", choice=event)
