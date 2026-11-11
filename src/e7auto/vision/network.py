"""Shared network pixels; baseline text uses match, scaled text keeps its strategy."""
from __future__ import annotations

from dataclasses import replace

from e7auto.configuration.models import NetworkConfig, NetworkTargetConfig
from e7auto.core.observations import NetworkObservation, Observation
from e7auto.core.types import Size
from e7auto.vision.frames import frame_view


class NetworkVision:
    def __init__(self, config: NetworkConfig, matcher, *, baseline_size: Size,
                 scaled_text_search: str = "match"):
        if scaled_text_search not in {"match", "locate_all"}:
            raise ValueError("Unknown network text search strategy")
        self.config = config
        self.matcher = matcher
        self.baseline_size = baseline_size
        self.scaled_text_search = scaled_text_search

    def network_loading(self, frame: object) -> Observation | None:
        target = self.config.loading
        for key in target.templates:
            hit = self.matcher.match(frame, key, target.region, target.color,
                                     structure_threshold=target.structure)
            if hit is not None:
                return replace(hit, object_id="network_loading")
        return None

    def _text(self, frame: object, target: NetworkTargetConfig) -> Observation | None:
        key, = target.templates
        view = frame_view(frame)
        # A cropped view retains its parent's complete client size and transform.
        baseline = (view.transform.is_identity
                    and view.transform.actual == self.baseline_size)
        if baseline or self.scaled_text_search == "match":
            return self.matcher.match(frame, key, target.region, target.color,
                                      structure_threshold=target.structure)
        region = view.region(target.region, clip=True)
        hits = self.matcher.locate_all(view, key, region, target.color,
                                       target.structure, limit=1)
        return hits[0] if hits else None

    def observe(self, frame: object) -> NetworkObservation:
        return NetworkObservation(
            loading=self.network_loading(frame),
            error=self._text(frame, self.config.error),
            retry=self._text(frame, self.config.retry),
        )
