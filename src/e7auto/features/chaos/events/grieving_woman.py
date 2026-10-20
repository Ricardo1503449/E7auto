"""Comfort the woman in the middle and confirm both result cards close."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.choose(event, next_page=lambda frame: context.vision.control(frame, "event_reward_close"))
    context.close_reward_cards(count=2)
    context.state.reward_stage = "event_closed"
    context.finish_text_and_map()
