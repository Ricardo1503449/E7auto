from __future__ import annotations

from math import ceil, isfinite, sqrt
import cv2
import numpy as np
from e7auto.configuration.models import ScrollConfig
from e7auto.core.ports import Frame
from e7auto.core.types import Rect
from e7auto.features.shop.contracts import ScrollMovementObservation
from e7auto.vision.frames import FrameView, frame_view


def _content_gray(frame: Frame | FrameView, roi: Rect) -> np.ndarray:
    return cv2.cvtColor(frame_view(frame).crop(roi).raw, cv2.COLOR_BGR2GRAY)


def _validate_pair(before: Frame | FrameView, after: Frame | FrameView) -> None:
    first, second = frame_view(before), frame_view(after)
    if first.shape != second.shape or first.transform != second.transform or first.origin != second.origin:
        raise ValueError("Scroll comparison frames must have identical shapes and client geometry")


def _even_dft_size(size: int) -> int:
    result = cv2.getOptimalDFTSize(size)
    while result % 2:
        result = cv2.getOptimalDFTSize(result + 1)
    return result


def _phase_candidates(before: np.ndarray, after: np.ndarray, count: int) -> list[tuple[float, float, float]]:
    """One content-only surface proposes translations; native pixels verify them."""
    height, width = (_even_dft_size(n) for n in before.shape)
    spectra = []
    for gray in (before, after):
        padded = np.zeros((height, width), np.float32)
        padded[:gray.shape[0], :gray.shape[1]] = gray
        spectra.append(cv2.dft(padded, flags=cv2.DFT_COMPLEX_OUTPUT))
    product = cv2.mulSpectrums(spectra[0], spectra[1], 0, conjB=True)
    magnitude = cv2.magnitude(product[:, :, 0], product[:, :, 1])
    product /= np.maximum(magnitude[:, :, None], np.finfo(np.float32).eps)
    surface = cv2.idft(product, flags=cv2.DFT_REAL_OUTPUT | cv2.DFT_SCALE)
    surface = np.roll(surface, (height // 2, width // 2), axis=(0, 1))
    peaks = surface.copy()
    candidates = []
    for _ in range(count):
        _, maximum, _, (x, y) = cv2.minMaxLoc(peaks)
        if not isfinite(maximum) or maximum <= 0:
            break
        left, right = max(0, x - 2), min(width, x + 3)
        top, bottom = max(0, y - 2), min(height, y + 3)
        weights = surface[top:bottom, left:right]
        response = float(weights.sum())
        if response > np.finfo(np.float32).eps:
            rows, columns = np.mgrid[top:bottom, left:right]
            dx = width / 2 - float(np.sum(weights * columns) / response)
            dy = height / 2 - float(np.sum(weights * rows) / response)
            if all(isfinite(v) for v in (dx, dy, response)):
                candidates.append((dx, dy, response))
        peaks[max(0, y - 5):y + 6, max(0, x - 5):x + 6] = -np.inf
    return candidates


def _has_content_texture(block: np.ndarray, minimum: float) -> bool:
    """Preserve the float32 std gate for native uint8 pixels cast to float32.

    C++ moments avoid NumPy's full-size deviation buffer. A conservative bound
    covers float32 mean/reduction, subtraction, square and sqrt rounding. Only
    decisions outside that interval are final; borderline blocks use the exact
    existing statistic. No empirical epsilon is used to move the threshold.
    """
    unit32 = float(np.finfo(np.float32).eps) / 2
    unit64 = float(np.finfo(np.float64).eps) / 2
    count32 = (block.size + 2) * unit32
    count64 = (block.size + 8) * unit64
    if block.dtype != np.float32 or count32 >= 0.5:
        return float(block.std()) >= minimum
    mean, deviation = cv2.meanStdDev(block)
    mean, deviation = float(mean[0, 0]), float(deviation[0, 0])
    if not isfinite(mean) or not isfinite(deviation):
        return float(block.std()) >= minimum
    gamma32 = count32 / (1 - count32)
    gamma64 = count64 / (1 - count64)
    # Raw gray values are in [0,255]. Double moments need an absolute bound
    # because variance subtracts two moments which can be almost equal.
    variance_error = (4 * gamma64 + 8 * unit64) * 255**2
    mean_error = gamma32 * (abs(mean) + gamma64 * 255) + gamma64 * 255
    variance = deviation**2
    lower = sqrt(max(0.0, variance - variance_error) * (1 - gamma32) * (1 - unit32)**3) * (1 - unit32)
    upper = sqrt((variance + variance_error + mean_error**2) * (1 + gamma32) * (1 + unit32)**3) * (1 + unit32)
    if lower * (1 - 8 * unit64) >= minimum:
        return True
    if upper * (1 + 8 * unit64) < minimum:
        return False
    return float(block.std()) >= minimum


def _native_content_scores(before: np.ndarray, after: np.ndarray, dx: float, dy: float,
                           config: ScrollConfig, scale_x: float, scale_y: float) -> tuple[float | None, ...]:
    """Check four native blocks with a bounded allowance for local raster alignment."""
    if abs(dx / scale_x) > config.maximum_horizontal_shift_px:
        return ()
    height, width = before.shape
    if 1 - abs(dy) / height < config.minimum_overlap_fraction:
        return ()
    ix, iy = round(dx), round(dy)
    rx = max(1, ceil(config.content_alignment_tolerance_px * scale_x))
    ry = max(1, ceil(config.content_alignment_tolerance_px * scale_y))
    left, right = max(0, -ix) + rx, min(width, width - ix) - rx
    top, bottom = max(0, -iy) + ry, min(height, height - iy) - ry
    if right - left < 4 or bottom - top < 4:
        return ()
    scores: list[float | None] = []
    failed = 0
    for y0, y1 in ((top, (top + bottom) // 2), ((top + bottom) // 2, bottom)):
        for x0, x1 in ((left, (left + right) // 2), ((left + right) // 2, right)):
            reference = before[y0:y1, x0:x1]
            search = after[y0 + iy - ry:y1 + iy + ry, x0 + ix - rx:x1 + ix + rx]
            score = None
            if (_has_content_texture(reference, config.minimum_content_stddev)
                    and _has_content_texture(search, config.minimum_content_stddev)):
                _, value, _, (x, _) = cv2.minMaxLoc(cv2.matchTemplate(search, reference, cv2.TM_CCOEFF_NORMED))
                actual_dx = ix + x - rx
                if isfinite(value) and abs(actual_dx / scale_x) <= config.maximum_horizontal_shift_px:
                    score = float(value)
            scores.append(score)
            if score is None or score < config.minimum_content_score:
                failed += 1
                if failed == 2:
                    return tuple(scores)
    return tuple(scores)


def measure_inventory_scroll(before: Frame | FrameView, after: Frame | FrameView, roi: Rect,
                             difference_threshold: int, *, config: ScrollConfig) -> ScrollMovementObservation:
    _validate_pair(before, after)
    if difference_threshold <= 0:
        raise ValueError("Scroll difference threshold must be positive")
    first, second = _content_gray(before, roi), _content_gray(after, roi)
    difference = cv2.absdiff(first, second)
    transform = frame_view(before).transform
    candidates = _phase_candidates(first, second, 4)
    first_float, second_float = first.astype(np.float32), second.astype(np.float32)
    reason = "insufficient_matching_content"
    verified = False
    chosen = candidates[0] if candidates else (0.0, 0.0, 0.0)
    scores: tuple[float | None, ...] = ()
    tolerance = config.content_alignment_tolerance_px * 2
    # Zero motion is a competing explanation, not assumed upward movement.
    for candidate in [*candidates, (0.0, 0.0, 0.0)]:
        dx, dy, _ = candidate
        current_scores = _native_content_scores(first_float, second_float, dx, dy, config, transform.scale_x, transform.scale_y)
        if sum(score is not None and score >= config.minimum_content_score for score in current_scores) < 3:
            continue
        if not verified:
            chosen, scores = candidate, current_scores
            verified, reason = True, "matched"
        elif (abs(dx - chosen[0]) / transform.scale_x > tolerance
              or abs(dy - chosen[1]) / transform.scale_y > tolerance):
            verified, reason = False, "ambiguous_content"
            break
    return ScrollMovementObservation(
        float(difference.mean()), float(np.mean(difference > difference_threshold)), int(difference.max()),
        chosen[0] / transform.scale_x, chosen[1] / transform.scale_y, chosen[2], verified, reason, scores,
    )


def measure_inventory_scroll_stability(before: Frame | FrameView, after: Frame | FrameView, roi: Rect,
                                      difference_threshold: int, downsample_factor: int) -> ScrollMovementObservation:
    _validate_pair(before, after)
    if difference_threshold <= 0 or downsample_factor <= 0:
        raise ValueError("Scroll thresholds and downsample factor must be positive")
    first, second = _content_gray(before, roi), _content_gray(after, roi)
    height, width = (max(1, n // downsample_factor) for n in first.shape)
    size = (width, height)
    a = cv2.resize(first, size, interpolation=cv2.INTER_AREA)
    b = cv2.resize(second, size, interpolation=cv2.INTER_AREA)
    difference = cv2.absdiff(a, b)
    proposals = _phase_candidates(a, b, 1)
    dx, dy, response = proposals[0] if proposals else (0.0, 0.0, 0.0)
    transform = frame_view(before).transform
    return ScrollMovementObservation(
        float(difference.mean()), float(np.mean(difference > difference_threshold)), int(difference.max()),
        dx * first.shape[1] / width / transform.scale_x,
        dy * first.shape[0] / height / transform.scale_y, response,
    )
