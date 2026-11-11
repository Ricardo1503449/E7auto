"""Shared network pixels with the existing feature text search strategies."""
from __future__ import annotations

from dataclasses import replace

from e7auto.configuration.models import NetworkConfig, NetworkTargetConfig
from e7auto.core.observations import NetworkObservation, Observation
from e7auto.core.types import ClientPoint
from e7auto.vision.frames import frame_view


class NetworkVision:
    def __init__(self, config: NetworkConfig, matcher, *, text_search: str = "match"):
        if text_search not in {"match", "locate_all"}:
            raise ValueError("Unknown network text search strategy")
        self.config = config
        self.matcher = matcher
        self.text_search = text_search

    def _text(self, frame: object, target: NetworkTargetConfig) -> Observation | None:
        key, = target.templates
        if self.text_search == "match":
            return self.matcher.match(frame, key, target.region, target.color,
                                      structure_threshold=target.structure)
        view = frame_view(frame)
        region = view.region(target.region, clip=True)
        hits = self.matcher.locate_all(view, key, region, target.color,
                                       target.structure, limit=1)
        if not hits:
            return None
        hit = hits[0]
        if view.transform.is_identity:
            hit = replace(hit, anchor=ClientPoint(round(hit.bounds.x + hit.bounds.width * .5),
                                                  round(hit.bounds.y + hit.bounds.height * .5)))
        return hit

    def observe(self, frame: object) -> NetworkObservation:
        return NetworkObservation(
            error=self._text(frame, self.config.error),
            retry=self._text(frame, self.config.retry),
        )
