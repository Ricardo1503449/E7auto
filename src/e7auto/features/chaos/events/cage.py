"""Touch the left lock, fight the event battle, close loot and return."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.run_battle_reward(event)
