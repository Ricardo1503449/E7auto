from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
import numpy as np
from e7auto.core.types import Point, Rect, Size, ClientPoint, ClientRect
from e7auto.core.ports import Frame


def _round_half_up(value: float) -> int:
    return floor(value + 0.5)


@dataclass(frozen=True, slots=True)
class CoordinateTransform:
    baseline: Size
    actual: Size

    @property
    def is_identity(self):
        return self.baseline == self.actual

    @property
    def scale_x(self):
        return self.actual.width / self.baseline.width

    @property
    def scale_y(self):
        return self.actual.height / self.baseline.height

    def point(self, point: Point) -> ClientPoint:
        if isinstance(point, ClientPoint):
            return point
        return ClientPoint(_round_half_up(point.x*self.scale_x), _round_half_up(point.y*self.scale_y))

    def rect(self, rect: Rect) -> ClientRect:
        if isinstance(rect, ClientRect):
            return rect
        left, top = floor(rect.x*self.scale_x), floor(rect.y*self.scale_y)
        right, bottom = ceil(rect.right*self.scale_x), ceil(rect.bottom*self.scale_y)
        return ClientRect(left, top, right-left, bottom-top)


@dataclass(frozen=True, slots=True)
class FrameView:
    """Unresampled pixels with full-client geometry and a global crop origin."""
    raw: Frame
    transform: CoordinateTransform
    origin: ClientPoint = ClientPoint(0, 0)

    @property
    def shape(self):
        return self.raw.shape

    @property
    def bounds(self):
        return ClientRect(self.origin.x, self.origin.y, self.raw.shape[1], self.raw.shape[0])

    def region(self, roi: Rect, *, clip: bool = False) -> ClientRect:
        """Resolve a complete region, or explicitly intersect a dynamic search."""
        if roi.width < 0 or roi.height < 0:
            raise ValueError(f"Negative region size: {roi}")
        r, b = self.transform.rect(roi), self.bounds
        if not clip:
            if r.x < b.x or r.y < b.y or r.right > b.right or r.bottom > b.bottom:
                raise ValueError(f"ROI outside frame: {r} vs {b}")
            return r
        x, y = min(max(r.x,b.x),b.right), min(max(r.y,b.y),b.bottom)
        right, bottom = max(b.x,min(r.right,b.right)), max(b.y,min(r.bottom,b.bottom))
        return ClientRect(x,y,max(0,right-x),max(0,bottom-y))

    def crop(self, roi: Rect, *, clip: bool = False) -> FrameView:
        r = self.region(roi, clip=clip)
        x,y = r.x-self.origin.x,r.y-self.origin.y
        return FrameView(self.raw[y:y+r.height,x:x+r.width,:3], self.transform, ClientPoint(r.x,r.y))


def frame_view(frame: Frame | FrameView) -> FrameView:
    if isinstance(frame, FrameView):
        return frame
    if frame.ndim != 3 or frame.shape[2] not in (3,4) or frame.dtype != np.uint8:
        raise ValueError("Frame must be an HxWx3 or HxWx4 uint8 array")
    size = Size(frame.shape[1],frame.shape[0])
    return FrameView(frame[:,:,:3],CoordinateTransform(size,size))


def adapt_frame(frame: Frame, transform: CoordinateTransform) -> Frame | FrameView:
    if transform.is_identity:
        return frame  # Matching wraps this zero-copy path in the same FrameView.
    frame_view(frame)  # Validate without copying captured pixels.
    return FrameView(frame, transform)


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
