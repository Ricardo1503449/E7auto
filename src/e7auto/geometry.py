from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from time import perf_counter

import cv2
import numpy as np

from .config import Point, Rect, Size
from .ports import Frame


def _round_half_up(value: float) -> int:
    return floor(value + 0.5)


@dataclass(frozen=True, slots=True)
class CoordinateTransform:
    baseline: Size
    actual: Size

    @property
    def is_identity(self) -> bool:
        return self.baseline == self.actual

    @property
    def scale_x(self) -> float:
        return self.actual.width / self.baseline.width

    @property
    def scale_y(self) -> float:
        return self.actual.height / self.baseline.height

    def point(self, point: Point) -> Point:
        return Point(
            _round_half_up(point.x * self.scale_x),
            _round_half_up(point.y * self.scale_y),
        )

    def rect(self, rect: Rect) -> Rect:
        left = _round_half_up(rect.x * self.scale_x)
        top = _round_half_up(rect.y * self.scale_y)
        right = _round_half_up(rect.right * self.scale_x)
        bottom = _round_half_up(rect.bottom * self.scale_y)
        return Rect(left, top, max(1, right - left), max(1, bottom - top))


@dataclass(slots=True)
class AdaptedFrame:
    """One captured non-reference frame with per-baseline-ROI normalization cache."""

    raw: Frame
    transform: CoordinateTransform
    _roi_cache: dict[Rect, Frame] = field(default_factory=dict)
    normalization_count: int = 0
    normalization_seconds: float = 0.0

    @property
    def shape(self) -> tuple[int, ...]:
        return self.raw.shape

    def normalized_roi(self, roi: Rect) -> Frame:
        cached = self._roi_cache.get(roi)
        if cached is not None:
            return cached
        started = perf_counter()
        try:
            actual = self.transform.rect(roi)
            height, width = self.raw.shape[:2]
            if actual.x < 0 or actual.y < 0 or actual.right > width or actual.bottom > height:
                raise ValueError(f"Scaled ROI outside frame: {actual} vs {width}x{height}")
            if self.raw.ndim != 3 or self.raw.shape[2] not in (3, 4):
                raise ValueError("Frame must be an HxWx3 or HxWx4 uint8 array")
            source = self.raw[actual.y : actual.bottom, actual.x : actual.right, :3]
            if source.shape[1] != roi.width or source.shape[0] != roi.height:
                source = cv2.resize(source, (roi.width, roi.height), interpolation=cv2.INTER_AREA)
            normalized = np.ascontiguousarray(source)
            self._roi_cache[roi] = normalized
            return normalized
        finally:
            self.normalization_count += 1
            self.normalization_seconds += perf_counter() - started


def adapt_frame(frame: Frame, transform: CoordinateTransform) -> Frame | AdaptedFrame:
    if transform.is_identity:
        return frame
    return AdaptedFrame(frame, transform)


def initial_client_size(
    current_mode: Size,
    reference_mode: Size,
    baseline: Size,
    width_fraction: float,
) -> Size:
    if current_mode == reference_mode:
        return baseline
    width = _round_half_up(current_mode.width * width_fraction)
    height = _round_half_up(width * baseline.height / baseline.width)
    return Size(max(1, width), max(1, height))
