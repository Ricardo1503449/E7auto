"""Left trap option, event battle, directly granted loot, narrative and map."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.run_battle_reward(event)
