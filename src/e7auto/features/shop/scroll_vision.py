from __future__ import annotations
from math import ceil, floor, isfinite
import cv2
import numpy as np
from e7auto.core.types import Rect
from e7auto.vision.frames import AdaptedFrame
from e7auto.core.ports import Frame
from e7auto.features.shop.contracts import ScrollMovementObservation, ScrollOverlapObservation, SCROLL_OVERLAP_MINIMUM_HEIGHT_FRACTION, SCROLL_OVERLAP_MAXIMUM_HORIZONTAL_SHIFT_PX
_SCROLL_OVERLAP_BORDER = 2
_SCROLL_OVERLAP_MINIMUM_STDDEV = 8.0
_SCROLL_OVERLAP_MINIMUM_SCORE = 0.75
_SCROLL_OVERLAP_REQUIRED_BLOCKS = 3
def _inventory_gray(frame: Frame | AdaptedFrame, roi: Rect) -> np.ndarray:
    if isinstance(frame, AdaptedFrame):
        source = frame.normalized_roi(roi)
    else:
        height, width = frame.shape[:2]
        if roi.x < 0 or roi.y < 0 or roi.right > width or roi.bottom > height:
            raise ValueError(f"Scroll ROI outside frame: {roi} vs {width}x{height}")
        source = frame[roi.y : roi.bottom, roi.x : roi.right]
    if source.ndim != 3 or source.shape[2] not in (3, 4):
        raise ValueError("Scroll comparison frame must have 3 or 4 channels")
    conversion = cv2.COLOR_BGRA2GRAY if source.shape[2] == 4 else cv2.COLOR_BGR2GRAY
    return cv2.cvtColor(source, conversion)


def _measure_gray_movement(
    before_gray: np.ndarray,
    after_gray: np.ndarray,
    difference_threshold: int,
    *,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> ScrollMovementObservation:
    difference = cv2.absdiff(before_gray, after_gray)
    phase_shift, phase_response = cv2.phaseCorrelate(
        before_gray.astype(np.float32),
        after_gray.astype(np.float32),
    )
    return ScrollMovementObservation(
        mean_absolute_difference=float(np.mean(difference)),
        changed_fraction=float(np.mean(difference > difference_threshold)),
        maximum_difference=int(np.max(difference)),
        phase_shift_x=float(phase_shift[0] * scale_x),
        phase_shift_y=float(phase_shift[1] * scale_y),
        phase_response=float(phase_response),
    )


def measure_inventory_scroll(
    before: Frame,
    after: Frame,
    roi: Rect,
    difference_threshold: int,
) -> ScrollMovementObservation:
    if before.shape != after.shape:
        raise ValueError(
            f"Scroll comparison frames must have identical shapes: {before.shape} != {after.shape}"
        )
    if difference_threshold <= 0:
        raise ValueError("Scroll difference threshold must be positive")

    return _measure_gray_movement(
        _inventory_gray(before, roi),
        _inventory_gray(after, roi),
        difference_threshold,
    )


def measure_inventory_scroll_stability(
    before: Frame,
    after: Frame,
    roi: Rect,
    difference_threshold: int,
    downsample_factor: int,
) -> ScrollMovementObservation:
    if before.shape != after.shape:
        raise ValueError(
            f"Scroll comparison frames must have identical shapes: {before.shape} != {after.shape}"
        )
    if difference_threshold <= 0:
        raise ValueError("Scroll difference threshold must be positive")
    if downsample_factor <= 0:
        raise ValueError("Scroll stability downsample factor must be positive")

    before_gray = _inventory_gray(before, roi)
    after_gray = _inventory_gray(after, roi)
    width = max(1, before_gray.shape[1] // downsample_factor)
    height = max(1, before_gray.shape[0] // downsample_factor)
    size = (width, height)
    before_small = cv2.resize(before_gray, size, interpolation=cv2.INTER_AREA)
    after_small = cv2.resize(after_gray, size, interpolation=cv2.INTER_AREA)
    return _measure_gray_movement(
        before_small,
        after_small,
        difference_threshold,
        scale_x=before_gray.shape[1] / width,
        scale_y=before_gray.shape[0] / height,
    )


def prepare_scroll_overlap_reference(
    frame: Frame | AdaptedFrame, roi: Rect, downsample_factor: int,
) -> np.ndarray:
    """Prepare only on demand; the caller owns this scroll's reference cache."""
    if downsample_factor <= 0:
        raise ValueError("Scroll overlap downsample factor must be positive")
    gray = _inventory_gray(frame, roi)
    size = (max(1, roi.width // downsample_factor), max(1, roi.height // downsample_factor))
    return cv2.resize(gray, size, interpolation=cv2.INTER_AREA).astype(np.float32)


def verify_scroll_overlap(
    reference: np.ndarray,
    current: Frame | AdaptedFrame,
    roi: Rect,
    shift_x: float,
    shift_y: float,
) -> ScrollOverlapObservation:
    """Check the proposed translation, without searching for another match."""
    if not all(isfinite(value) for value in (shift_x, shift_y)):
        return ScrollOverlapObservation(False, "non_finite_shift", 0.0)
    overlap = max(0.0, 1.0 - abs(shift_y) / roi.height)
    if abs(shift_x) > SCROLL_OVERLAP_MAXIMUM_HORIZONTAL_SHIFT_PX:
        return ScrollOverlapObservation(False, "horizontal_shift", overlap)
    if overlap < SCROLL_OVERLAP_MINIMUM_HEIGHT_FRACTION:
        return ScrollOverlapObservation(False, "insufficient_overlap", overlap)

    height, width = reference.shape
    dx, dy = shift_x * width / roi.width, shift_y * height / roi.height
    border = _SCROLL_OVERLAP_BORDER
    left, right = max(0, ceil(dx)) + border, min(width, floor(width + dx)) - border
    top, bottom = max(0, ceil(dy)) + border, min(height, floor(height + dy)) - border
    if right - left < 2 or bottom - top < 2:
        return ScrollOverlapObservation(False, "insufficient_overlap", overlap)

    # AdaptedFrame reuses its normalized ROI, including work done by the main gate.
    gray = _inventory_gray(current, roi)
    current_small = cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA).astype(np.float32)
    aligned = cv2.warpAffine(
        reference, np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32),
        (width, height), flags=cv2.INTER_LINEAR,
    )
    first = aligned[top:bottom, left:right]
    second = current_small[top:bottom, left:right]
    scores: list[float | None] = []
    passed = 0
    for first_row, second_row in zip(np.array_split(first, 2), np.array_split(second, 2)):
        for first_block, second_block in zip(
            np.array_split(first_row, 2, axis=1), np.array_split(second_row, 2, axis=1),
        ):
            if min(float(first_block.std()), float(second_block.std())) < _SCROLL_OVERLAP_MINIMUM_STDDEV:
                scores.append(None)
                continue
            score = float(cv2.matchTemplate(second_block, first_block, cv2.TM_CCOEFF_NORMED)[0, 0])
            scores.append(score if isfinite(score) else None)
            if isfinite(score) and score >= _SCROLL_OVERLAP_MINIMUM_SCORE:
                passed += 1
    accepted = passed >= _SCROLL_OVERLAP_REQUIRED_BLOCKS
    return ScrollOverlapObservation(
        accepted, "matched" if accepted else "insufficient_matching_blocks",
        overlap, tuple(scores), passed,
    )
