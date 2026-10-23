"""Bounded, per-operation scene evidence; never reuse it across an input or recovery."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Callable

from e7auto.configuration.models import InventoryReadinessConfig, ScrollConfig
from .contracts import ScrollMovementObservation, RowPixelChange


@dataclass(frozen=True, slots=True)
class SceneSampleEvidence:
    """Exact-frame checks, handed off only after the enclosing scroll succeeds."""
    frame: object
    captured_at: float
    generation: int
    screen: str
    previous_at: float | None
    pair: ScrollMovementObservation | None
    first_at: float | None
    span: ScrollMovementObservation | None


def format_row_regions(rows: tuple[RowPixelChange, ...]) -> str:
    return "|".join(f"{row.row_id}:{row.bounds.x},{row.bounds.y},{row.bounds.width},{row.bounds.height}"
                    for row in rows) or "na"


def format_row_changes(rows: tuple[RowPixelChange, ...]) -> str:
    return "|".join(f"{row.row_id}:{row.changed_fraction:.6f}" for row in rows) or "na"


def pixels_are_stable(pair: ScrollMovementObservation, scroll: ScrollConfig,
                      settings: InventoryReadinessConfig) -> bool:
    values = (pair.phase_shift_x, pair.phase_shift_y, pair.phase_response, pair.maximum_row_changed_fraction)
    return (all(isfinite(value) for value in values)
            and max(abs(pair.phase_shift_x), abs(pair.phase_shift_y)) <= scroll.maximum_pairwise_shift_px
            and pair.phase_response >= scroll.minimum_phase_response
            and pair.maximum_row_changed_fraction <= settings.maximum_row_changed_fraction)


class SceneWindow:
    def __init__(self, frames: int, scroll: ScrollConfig, settings: InventoryReadinessConfig):
        self.frames, self.scroll, self.settings = frames, scroll, settings
        self.reset()

    def reset(self, reason: str = "not_sampled") -> None:
        self.first = self.previous = None
        self.first_at = self.previous_at = 0.0
        self.generation = -1
        self.count = 0
        self.ready = False
        self.reason = reason
        self.maximum_change = 0.0
        self.pair_rows: tuple[RowPixelChange, ...] = ()
        self.span_rows: tuple[RowPixelChange, ...] = ()

    def observe(self, frame: object, captured_at: float, generation: int, scene: str,
                measure: Callable[[object, object], ScrollMovementObservation]) -> bool:
        self.ready = False
        if scene != "ready":
            self.reset(scene)
            return False
        if self.previous is not None and (generation != self.generation or captured_at <= self.previous_at):
            self.reset("interrupted_evidence")
        if self.previous is not None:
            pair = measure(self.previous, frame)
            self.pair_rows, self.span_rows = pair.row_pixel_changes, ()
            self.maximum_change = pair.maximum_row_changed_fraction
            stable = pixels_are_stable(pair, self.scroll, self.settings)
            if stable and self.first is not self.previous:
                span = measure(self.first, frame)
                self.span_rows = span.row_pixel_changes
                self.maximum_change = max(self.maximum_change, span.maximum_row_changed_fraction)
                stable = pixels_are_stable(span, self.scroll, self.settings)
            if not stable:
                change = self.maximum_change
                pair_rows, span_rows = self.pair_rows, self.span_rows
                self.reset("changing_pixels_or_position")
                self.maximum_change = change
                self.pair_rows, self.span_rows = pair_rows, span_rows
        if self.first is None:
            self.first, self.first_at = frame, captured_at
            if self.reason != "changing_pixels_or_position":
                self.reason = "confirming"
        self.previous, self.previous_at, self.generation = frame, captured_at, generation
        self.count += 1
        self.ready = (self.count >= self.frames
                      and (captured_at - self.first_at) * 1000 + 1e-6 >= self.settings.minimum_stable_ms)
        if self.ready:
            self.reason = "ready"
        elif self.reason == "not_sampled":
            self.reason = "confirming"
        return self.ready
