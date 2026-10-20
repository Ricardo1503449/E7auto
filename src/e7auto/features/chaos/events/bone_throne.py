"""Offer shards on the right, promote one hero using lineup priority, then leave."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.run_rank_reward(event)
