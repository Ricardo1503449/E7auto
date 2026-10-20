"""Clear the right and then left armory, confirming each separate battle reward."""
from e7auto.core.domain import StopReason
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.choose(event, next_page=lambda frame: context.vision.control(frame, "prepare_event"))
    context.state.battle_floor = context.floor
    context.fight("prepare_event", ("event_reward_close",))
    context.state.reward_stage = "event_received"
    next_choice = lambda frame: context.vision.control(frame, "event_old_armory_left")
    context.close_reward_cards(next_page=next_choice)
    context.state.reward_stage = "event_closed"

    choice = context.wait("armory_second_choice", next_choice, reason=StopReason.CHAOS_UNKNOWN_CONTENT)
    context.choose(choice, next_page=lambda frame: context.vision.control(frame, "prepare_event"))
    context.state.reward_stage = None
    context.fight("prepare_event", ("event_reward_close",))
    context.state.reward_stage = "event_received"
    context.close_reward_cards()
    context.state.reward_stage = "event_closed"
    context.finish_text_and_map()
