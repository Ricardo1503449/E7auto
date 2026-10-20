"""Touch the bookmark and confirm a two-rank promotion before leaving."""
from e7auto.core.domain import StopReason
from e7auto.runtime.stop_control import StopExecution
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    hero = context.state.next_rank_hero()
    if hero is None:
        raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, "no supported rank-up target")
    context.choose(event, next_page=lambda frame: context.vision.control(frame, "event_rank_enter"))
    context.act("event_rank_enter")
    context.rank_up(hero, steps=2)
    context.finish_text_and_map()
