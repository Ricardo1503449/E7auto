"""Pray for experience, dismissing an exploration level-up only if it appears."""
from ..contracts import Detection
from .context import EventContext
from .experience import finish_with_optional_levelup


def run(context: EventContext, event: Detection) -> None:
    finish_with_optional_levelup(context, wait_name="prayer_text_or_map", choice=event)
