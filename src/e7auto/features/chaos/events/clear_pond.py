"""Use the recognized pond choice, close its single reward card, then leave."""
from .context import EventContext
from ..contracts import Detection


def run(context: EventContext, event: Detection) -> None:
    context.choose(event, next_page=lambda frame: context.vision.control(frame, "event_reward_close"))
    context.close_reward_cards()
    context.state.reward_stage = "event_closed"
    context.finish_text_and_map()
