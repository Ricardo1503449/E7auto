"""Destroy the cage, fight, and close the single page showing both rewards."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.choose(event, next_page=lambda frame: context.vision.control(frame, "prepare_event"))
    context.state.battle_floor = context.floor
    context.fight("prepare_event", ("event_reward_close",))
    context.state.reward_stage = "event_received"
    # Two items are displayed together; count tracks pages, not item cards.
    context.close_reward_cards(count=1)
    context.state.reward_stage = "event_closed"
    context.finish_text_and_map()
