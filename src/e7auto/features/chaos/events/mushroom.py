"""Choose the left rank reward and update the same ranks used by camp priority."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.run_rank_reward(event)
