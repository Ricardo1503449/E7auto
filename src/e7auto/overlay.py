from __future__ import annotations

from dataclasses import dataclass

from .config import Point, Rect


@dataclass(frozen=True, slots=True)
class OverlaySecurityReport:
    set_succeeded: bool
    affinity_readback: int | None
    capture_excluded: bool
    fallback_safe: bool
    overlay_bounds: Rect
    safe: bool


def overlay_rect(client_bounds: Rect, offset: Point, width: int, height: int) -> Rect:
    return Rect(client_bounds.x + offset.x, client_bounds.y + offset.y, width, height)


def capture_is_safe(
    capture_excluded: bool,
    overlay_bounds: Rect,
    client_bounds: Rect,
    recognition_rois: tuple[Rect, ...],
) -> bool:
    if capture_excluded:
        return True
    screen_rois = tuple(
        Rect(
            client_bounds.x + roi.x,
            client_bounds.y + roi.y,
            roi.width,
            roi.height,
        )
        for roi in recognition_rois
    )
    return not any(overlay_bounds.intersects(roi) for roi in screen_rois)


def evaluate_overlay_security(
    set_succeeded: bool,
    affinity_readback: int | None,
    excluded_affinity: int,
    overlay_bounds: Rect,
    client_bounds: Rect,
    recognition_rois: tuple[Rect, ...],
) -> OverlaySecurityReport:
    capture_excluded = set_succeeded and affinity_readback == excluded_affinity
    fallback_safe = capture_is_safe(
        False,
        overlay_bounds,
        client_bounds,
        recognition_rois,
    )
    return OverlaySecurityReport(
        set_succeeded=set_succeeded,
        affinity_readback=affinity_readback,
        capture_excluded=capture_excluded,
        fallback_safe=fallback_safe,
        overlay_bounds=overlay_bounds,
        safe=capture_excluded or fallback_safe,
    )
