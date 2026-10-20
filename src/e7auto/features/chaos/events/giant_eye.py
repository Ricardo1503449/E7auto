"""Rightmost attack option, event battle, direct reward and narrative return."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.run_battle_reward(event)
