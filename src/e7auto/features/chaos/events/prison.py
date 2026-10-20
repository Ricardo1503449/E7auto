"""Left rusty-key option, event battle, direct loot and narrative return."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.run_battle_reward(event)
