"""Share warmth on the right, use normal event rank priority and return to map."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.run_rank_reward(event)
