"""Narrow capabilities supplied by ChaosFlow, without importing its implementation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from e7auto.core.domain import StopReason
from e7auto.runtime.context import RuntimeContext
from e7auto.runtime.stop_control import StopExecution
from ..configuration import ChaosSettings
from ..contracts import ChaosVisionPort, Detection, MapView
from ..state import RoundState


@dataclass(frozen=True, slots=True)
class EventContext:
    runtime: RuntimeContext[ChaosVisionPort]
    vision: ChaosVisionPort
    settings: ChaosSettings
    state: RoundState
    floor: int
    frame: Callable[[], object]
    wait: Callable[..., Any]
    tap: Callable[[Detection], None]
    act: Callable[..., None]
    fight: Callable[[str, tuple[str, ...]], Detection]
    rank_up: Callable[..., None]
    depart: Callable[[str], None]

    def choose(self, choice: Detection, *, next_page: Callable[[object], Any],
               name: str = "event_choice_result", moving_text: bool = False,
               reason: StopReason = StopReason.RECOGNITION_TIMEOUT,
               retry_blocked: Callable[[object], bool] | None = None) -> Any:
        """Select only this stage's option and confirm its declared destination."""
        from .choices import choose
        return choose(self, choice, next_page=next_page, name=name,
                      moving_text=moving_text, reason=reason, retry_blocked=retry_blocked)

    def run_rank_reward(self, event: Detection) -> None:
        """Selected non-experience route: one rank reward, then text/map."""
        hero = self.state.next_rank_hero()
        if hero is None:
            raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, "no supported rank-up target")
        self.choose(event, next_page=lambda frame: self.vision.control(frame, "event_rank_enter"))
        self.act("event_rank_enter")
        self.rank_up(hero)
        self.finish_text_and_map()

    def run_battle_reward(self, event: Detection) -> None:
        """Selected event battle route: direct reward card, then text/map."""
        self.choose(event, next_page=lambda frame: self.vision.control(frame, "prepare_event"))
        self.state.battle_floor = self.floor
        self.fight("prepare_event", ("event_reward_close",))
        self.state.reward_stage = "event_received"
        self.close_reward_cards()
        self.state.reward_stage = "event_closed"
        self.finish_text_and_map()

    def close_reward_cards(self, *, count: int = 1, close_key: str = "event_reward_close",
                           next_page: Callable[[object], Detection | None] | None = None) -> None:
        """Close known event cards with stable evidence and confirmed outcomes."""
        from .rewards import close_reward_cards
        close_reward_cards(self, count=count, close_key=close_key, next_page=next_page)

    def finish_text_and_map(self, *, choice: Detection | None = None) -> None:
        """Advance only text, then require the map from this event's original floor."""
        while True:
            def next_step(frame):
                text = self.vision.control(frame, "text_continue")
                if text is not None:
                    return text
                view = self.vision.map_view(frame)
                if view is not None and view.floor == self.floor:
                    return view
                return None

            if choice is not None:
                result = self.choose(choice, next_page=next_step, name="event_text_or_map", moving_text=True,
                                     reason=StopReason.CHAOS_UNKNOWN_CONTENT)
                choice = None
            else:
                result = self.wait("event_text_or_map", next_step, reason=StopReason.CHAOS_UNKNOWN_CONTENT,
                                   moving_text=True)
            if isinstance(result, MapView):
                return
            self.tap(result)
