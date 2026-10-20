"""Follow the left blood trail, win the event battle and take its rank reward."""
from e7auto.core.domain import StopReason
from e7auto.runtime.stop_control import StopExecution
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    hero = context.state.next_rank_hero()
    if hero is None:
        raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, "no supported rank-up target")
    context.choose(event, next_page=lambda frame: context.vision.control(frame, "prepare_event"))
    context.state.battle_floor = context.floor
    context.fight("prepare_event", ("event_rank_enter",))
    context.state.reward_stage = "event_rank_received"
    context.act("event_rank_enter")
    context.rank_up(hero, battle_reward=True)
    context.state.reward_stage = "event_rank_completed"
    context.finish_text_and_map()
