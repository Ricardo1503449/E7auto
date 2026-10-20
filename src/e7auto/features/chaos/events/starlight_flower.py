"""Touch the left flower option and use the shared event rank-up flow."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.run_rank_reward(event)
