"""Pull the left sword, finish the event battle and close its direct reward."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.run_battle_reward(event)
