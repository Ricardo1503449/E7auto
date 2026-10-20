"""Inspect the wall, handling an optional exploration level-up before returning."""
from ..contracts import Detection
from .context import EventContext
from .experience import finish_with_optional_levelup


def run(context: EventContext, event: Detection) -> None:
    finish_with_optional_levelup(context, wait_name="torch_text_or_map", choice=event)
