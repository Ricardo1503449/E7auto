"""Guarded target-stage selection."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
from e7auto.core.domain import StopReason
from e7auto.core.types import Point, ClientPoint
from e7auto.runtime.context import RuntimeContext
from e7auto.runtime.stop_control import StopExecution
from e7auto.vision.frames import CoordinateTransform
from .configuration import ChaosSettings
from .contracts import ChaosVisionPort, Detection


class DateChangePending(Exception):
    """Interrupt lobby actions without intercepting active exploration."""


@dataclass(frozen=True, slots=True)
class _StartConfirmation:
    button: Detection
    capture_count: int
    network_generation: int
    transform: CoordinateTransform
    target_stage: int


class StageSelection:
    def __init__(self, runtime: RuntimeContext[ChaosVisionPort], settings: ChaosSettings,
                 vision: ChaosVisionPort, *, frame: Callable, wait: Callable,
                 tap: Callable, delay: Callable, lobby_guard: Callable):
        self.runtime, self.settings, self.vision = runtime, settings, vision
        self.frame, self.wait, self.tap = frame, wait, tap
        self.delay, self.lobby_guard = delay, lobby_guard
        self.target_stage = settings.target_stage
        self.stage_entry_key = f"stage_{self.target_stage:02d}"
        self._pending_start: _StartConfirmation | None = None
        self._confirmed_start: _StartConfirmation | None = None

    def invalidate_start(self):
        self._pending_start = None
        self._confirmed_start = None

    def take_start(self) -> Detection | None:
        """Consume the last stable lobby's button once, without retaining pixels."""
        self.runtime.control.checkpoint()
        confirmed, self._confirmed_start = self._confirmed_start, None
        if (confirmed is not None
                and confirmed.capture_count == self.runtime.capture_count
                and confirmed.network_generation == self.runtime.network_recovery_generation
                and confirmed.transform is self.runtime.transform
                and confirmed.target_stage == self.target_stage):
            return confirmed.button
        return None

    def _lobby_start_state(self, frame):
        self.invalidate_start()
        state = self.lobby_state(frame)
        if state is not None and state[1]:
            assert self.runtime.transform is not None
            self._pending_start = _StartConfirmation(
                state[0], self.runtime.capture_count,
                self.runtime.network_recovery_generation, self.runtime.transform,
                self.target_stage)
        return state

    def lobby_state(self, frame):
        if not self.lobby_guard(frame):
            return None
        start = self.vision.control(frame, "start_exploration")
        if start is None:
            return None
        stages = [(n, hit) for n, key in self.settings.stage_selection["lobby_scenes"].items()
                  if (hit := self.vision.control(frame, key)) is not None]
        current = self.resolve_lobby_stage(stages, self.settings.stage_selection["lobby_ambiguity_margin"])
        if stages and current is None:
            return None  # Similar competing candidates cannot authorize exploration.
        return start, current == self.target_stage


    @staticmethod
    def resolve_lobby_stage(stages, margin):
        ranked = sorted(stages, key=lambda pair: pair[1].confidence, reverse=True)
        if not ranked or (len(ranked) > 1 and ranked[0][1].confidence-ranked[1][1].confidence < margin):
            return None
        return ranked[0][0]


    def _selection_evidence(self, frame, entry, button):
        if entry is None or button is None:
            return None
        borders = [hit for hit in self.vision.stage_highlights(frame, entry)
                   if hit.bounds.x <= entry.bounds.x and entry.bounds.right <= hit.bounds.right
                   and hit.bounds.y <= entry.bounds.y and entry.bounds.bottom <= hit.bounds.bottom]
        return (entry, borders[0], button) if len(borders) == 1 else None

    def _selected_state(self, frame):
        button = self.vision.control(frame, "stage_confirm")
        if button is None:
            return None
        entry = self.vision.control(frame, self.stage_entry_key)
        return self._selection_evidence(frame, entry, button)

    def selected(self, frame):
        # Date popups are a lobby concern, never a selector-panel concern.
        selected = self._selected_state(frame)
        return None if selected is None else selected[-1]

    def selector_point(self, start: Detection) -> ClientPoint:
        """Validate the entry relative to the current button in client pixels."""
        transform = self.runtime.transform
        assert transform is not None
        dx, dy = self.settings.stage_selection["open_offset"]
        anchor, bounds = transform.point(start.anchor), transform.rect(start.bounds)
        allowed = transform.rect(self.settings.scenes["start_exploration"].region)
        point = ClientPoint(round(anchor.x+dx*transform.scale_x),
                            round(anchor.y+dy*transform.scale_y))
        if not (start.key == "start_exploration" and dx > 0 and dy < 0
                and allowed.x <= bounds.x < bounds.right <= allowed.right
                and allowed.y <= bounds.y < bounds.bottom <= allowed.bottom
                and bounds.x <= anchor.x < bounds.right
                and bounds.y <= anchor.y < bounds.bottom
                and bounds.x <= point.x < bounds.right
                and bounds.y-bounds.height <= point.y < bounds.y
                and 0 <= point.x < transform.actual.width
                and 0 <= point.y < transform.actual.height):
            raise StopExecution(StopReason.CONFIG_INCOMPLETE,
                                "stage selector opening point is outside the button-relative limits")
        return point


    def ensure(self):
        self.invalidate_start()
        params = self.settings.stage_selection
        deadline = self.runtime.active_monotonic()+params["timeout_ms"]/1000

        def wait(name, detector):
            remaining = round((deadline-self.runtime.active_monotonic())*1000)
            if remaining <= 0:
                raise StopExecution(StopReason.CHAOS_STAGE_SWITCH_FAILED, "stage selection time budget exhausted")
            return self.wait(name, detector, timeout_ms=min(remaining, self.settings.timing["page_timeout_ms"]),
                              reason=StopReason.CHAOS_STAGE_SWITCH_FAILED)

        start, already_target = wait("lobby_stage", self._lobby_start_state)
        self._confirmed_start, self._pending_start = self._pending_start, None
        self.runtime.deps.logger.event("chaos_stage_checked", target_stage=self.target_stage, already_target=already_target)
        if already_target:
            return
        point = self.selector_point(start)
        self.invalidate_start()
        self.runtime.click("chaos_open_stage_selector", point)
        self.delay(self.settings.timing["action_settle_ms"]/1000)
        drags = 0
        while True:
            def panel(frame):
                button = self.vision.control(frame, "stage_confirm")
                if button is None:
                    return None
                entry = self.vision.control(frame, self.stage_entry_key)
                first = entry if self.target_stage == 1 else self.vision.control(frame, "stage_01")
                selected = self._selection_evidence(frame, entry, button)
                return button, entry, first, selected
            _, entry, first, selected = wait("stage_selector", panel)
            if entry is not None:
                if selected is None:
                    self.tap(entry)
                    selected = wait("target_stage_selected", self._selected_state)
                else:
                    self.runtime.deps.logger.event("chaos_confirmed", stage="target_stage_selected")
                break
            if first is not None and first.bounds.y <= params["top_entry_max_y"]*(self.runtime.transform.scale_y if isinstance(first.anchor,ClientPoint) else 1):
                raise StopExecution(StopReason.CHAOS_STAGE_SWITCH_FAILED,
                                    f"stage {self.target_stage:02d} unavailable in the stable top list")
            if drags >= params["max_drags"]:
                raise StopExecution(StopReason.CHAOS_STAGE_SWITCH_FAILED, "target stage not found within drag limit")
            region = self.settings.scenes[self.stage_entry_key].region
            def relative_point(fractions):
                return Point(round(region.x+region.width*fractions[0]), round(region.y+region.height*fractions[1]))
            drag_start = relative_point(params["drag_start"])
            drag_end = Point(drag_start.x, drag_start.y+params["drag_steps"]*params["step_distance_px"])
            completed = self.runtime.drag(
                "chaos_stage_drag", drag_start, drag_end,
                duration_ms=params["drag_steps"]*params["step_ms"], step_ms=params["step_ms"],
                allow_outside_client=True, frame_check_ms=params["frame_check_ms"],
                step_guard=lambda: self.runtime.active_monotonic() < deadline,
                frame_guard=lambda f: self.runtime.active_monotonic() < deadline and self.vision.control(f, "stage_confirm") is not None,
            )
            if completed:
                drags += 1
            self.runtime.deps.logger.event("chaos_stage_drag_result", completed=completed, completed_drags=drags)
            self.delay(params["settle_ms"]/1000)
            # Always reacquire, including after a partially completed drag or recovery.
        self.tap(selected[-1])  # Submit once, using the final joint frame's button.
        wait("lobby_target_stage", lambda f: (state if (state := self._lobby_start_state(f)) is not None and state[1] else None))
        self._confirmed_start, self._pending_start = self._pending_start, None
        self.runtime.deps.logger.event("chaos_stage_switched", target_stage=self.target_stage, completed_drags=drags)
