"""Salute the old empire; every saved hero gains one rank without a selection UI."""
from e7auto.core.domain import StopReason
from .context import EventContext
from ..contracts import Detection


def run(context: EventContext, event: Detection) -> None:
    context.choose(event, name="golden_patterns_result",
                   next_page=lambda frame: context.vision.control(frame, "text_continue"), moving_text=True,
                   reason=StopReason.CHAOS_UNKNOWN_CONTENT)
    # The option has advanced to its result. Update once, outside text/recovery polling.
    for hero, before in tuple(context.state.ranks.items()):
        after = min(5, before + 1)
        if after != before:
            context.state.ranks[hero] = after
            context.runtime.deps.logger.event("chaos_rank_changed", hero=hero, rank_before=before,
                                              rank_after=after, node_kind=context.state.node_kind)
    context.finish_text_and_map()
