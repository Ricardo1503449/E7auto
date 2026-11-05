"""Clear the right and then left armory, confirming each separate battle reward."""
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    context.choose_battle(event)
    context.state.battle_floor = context.floor
    context.fight("prepare_event", ("event_reward_close",))
    context.state.reward_stage = "event_received"
    next_choice = lambda frame: context.vision.control(frame, "event_old_armory_left")
    choice = context.close_reward_cards(next_page=next_choice)
    context.state.reward_stage = "event_closed"

    context.choose_battle(choice)
    context.state.reward_stage = None
    context.fight("prepare_event", ("event_reward_close",))
    context.state.reward_stage = "event_received"
    context.close_reward_cards()
    context.state.reward_stage = "event_closed"
    context.finish_text_and_map()
