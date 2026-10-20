"""Offer the lineup's designated hero, confirm departure, then close both cards."""
from e7auto.core.domain import StopReason
from e7auto.runtime.stop_control import StopExecution
from ..contracts import Detection
from .context import EventContext


def run(context: EventContext, event: Detection) -> None:
    hero = context.settings.lineup.departure_hero
    if hero not in context.state.ranks:
        raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, "departure target is not in the roster")
    context.choose(event, next_page=lambda frame: context.vision.control(frame, "depart_title"))
    context.depart(hero)
    context.state.record_departure(hero)
    context.runtime.deps.logger.event("chaos_hero_departed", hero=hero, floor=context.floor)
    context.state.reward_stage = "hero_departed"
    context.close_reward_cards(count=2)
    context.state.reward_stage = "event_closed"
    context.finish_text_and_map()
