from __future__ import annotations

from math import ceil, isfinite, sqrt
import cv2
import numpy as np
from e7auto.configuration.models import ScrollConfig
from e7auto.core.ports import Frame
from e7auto.core.types import Point, Rect, ClientPoint, ClientRect
from e7auto.features.shop.contracts import ScrollContentCandidate, ScrollMovementObservation, RowPixelChange
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
                           config: ScrollConfig, scale_x: float, scale_y: float,
                           item_rects: tuple[ClientRect, ...],
                           origin: ClientPoint = ClientPoint(0, 0)) -> tuple[float | None, ...]:
    """Compare complete icon rows, retaining one score per configured source row."""
    if abs(dx / scale_x) > config.maximum_horizontal_shift_px:
        return ()
    height, width = before.shape
    if 1 - abs(dy) / height < config.minimum_overlap_fraction:
        return ()
    ix, iy = round(dx), round(dy)
    rx = max(1, ceil(config.content_alignment_tolerance_px * scale_x))
    ry = max(1, ceil(config.content_alignment_tolerance_px * scale_y))
    scores: list[float | None] = []
    for rect in item_rects:
        x0, y0 = rect.x - origin.x, rect.y - origin.y
        x1, y1 = rect.right - origin.x, rect.bottom - origin.y
        left, top, right, bottom = x0 + ix - rx, y0 + iy - ry, x1 + ix + rx, y1 + iy + ry
        # A partly visible icon or a clipped alignment window is not evidence.
        if left < 0 or top < 0 or right > width or bottom > height:
            scores.append(None)
            continue
        reference = before[y0:y1, x0:x1]
        search = after[top:bottom, left:right]
        score = None
        if (_has_content_texture(reference, config.minimum_content_stddev)
                and _has_content_texture(search, config.minimum_content_stddev)):
            _, value, _, (x, _) = cv2.minMaxLoc(cv2.matchTemplate(search, reference, cv2.TM_CCOEFF_NORMED))
            actual_dx = ix + x - rx
            if isfinite(value) and abs(actual_dx / scale_x) <= config.maximum_horizontal_shift_px:
                score = float(value)
        scores.append(score)
    return tuple(scores)


def _item_rects(frame: FrameView, item_rois: tuple[Rect, ...]) -> tuple[ClientRect, ...]:
    """Project once using the full client transform; never infer scale from the crop."""
    rects = []
    for roi in item_rois:
        rect = frame.region(roi)
        if rect.width <= 0 or rect.height <= 0:
            raise ValueError("Scroll item rows must have positive area")
        rects.append(rect)
    ordered = sorted(rects, key=lambda rect: rect.y)
    if any(first.bottom > second.y for first, second in zip(ordered, ordered[1:])):
        raise ValueError("Scroll item rows must not overlap vertically")
    return tuple(rects)


def measure_inventory_scroll(before: Frame | FrameView, after: Frame | FrameView, roi: Rect,
                             difference_threshold: int, *, config: ScrollConfig,
                             item_rois: tuple[Rect, ...]) -> ScrollMovementObservation:
    _validate_pair(before, after)
    if difference_threshold <= 0:
        raise ValueError("Scroll difference threshold must be positive")
    first, second = _content_gray(before, roi), _content_gray(after, roi)
    content_view = frame_view(before).crop(roi)
    item_rects = _item_rects(content_view, item_rois)
    difference = cv2.absdiff(first, second)
    transform = frame_view(before).transform
    candidates = _phase_candidates(first, second, 4)
    first_float, second_float = first.astype(np.float32), second.astype(np.float32)
    evaluated: list[ScrollContentCandidate] = []
    tolerance = config.content_alignment_tolerance_px * 2
    # Zero motion is a competing explanation, not assumed upward movement.
    for index, candidate in enumerate([*candidates, (0.0, 0.0, 0.0)], start=1):
        dx, dy, _ = candidate
        current_scores = _native_content_scores(first_float, second_float, dx, dy, config,
                                               transform.scale_x, transform.scale_y, item_rects, content_view.origin)
        supporting_scores = sorted(
            (score for score in current_scores
             if score is not None and isfinite(score) and score >= config.minimum_content_score),
            reverse=True,
        )
        # The weakest of the strongest N rows determines multi-row support.
        quality = (supporting_scores[config.minimum_content_rows - 1]
                   if len(supporting_scores) >= config.minimum_content_rows else None)
        evaluated.append(ScrollContentCandidate(
            index, dx / transform.scale_x, dy / transform.scale_y, candidate[2],
            current_scores, len(supporting_scores), quality,
        ))

    qualified = sorted(
        (candidate for candidate in evaluated if candidate.quality is not None),
        key=lambda candidate: -1.0 if candidate.quality is None else candidate.quality,
        reverse=True,
    )
    # Keep the primary proposal's rejected evidence when no candidate qualifies.
    chosen = qualified[0] if qualified else evaluated[0]
    runner_up = next((candidate for candidate in qualified[1:]
                      if abs(candidate.phase_shift_x - chosen.phase_shift_x) > tolerance
                      or abs(candidate.phase_shift_y - chosen.phase_shift_y) > tolerance), None)
    margin = None
    if runner_up is not None:
        assert chosen.quality is not None and runner_up.quality is not None
        margin = chosen.quality - runner_up.quality
    verified = bool(qualified) and (margin is None or margin >= config.minimum_content_margin)
    reason = ("matched" if verified else "ambiguous_content" if qualified else "insufficient_matching_rows")
    return ScrollMovementObservation(
        float(difference.mean()), float(np.mean(difference > difference_threshold)), int(difference.max()),
        chosen.phase_shift_x, chosen.phase_shift_y, chosen.phase_response, verified, reason, chosen.content_scores,
        content_candidates=tuple(evaluated),
        content_selected_candidate=chosen.candidate_id if qualified else None,
        content_runner_up_candidate=runner_up.candidate_id if runner_up is not None else None,
        content_margin=margin,
    )


def measure_inventory_scroll_stability(before: Frame | FrameView, after: Frame | FrameView, roi: Rect,
                                      difference_threshold: int, downsample_factor: int, *,
                                      item_rois: tuple[Rect, ...] = (),
                                      pixel_difference_threshold: int | None = None,
                                      pixel_inset: Point = Point(0, 0),
                                      item_ids: tuple[str, ...] = ()) -> ScrollMovementObservation:
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
    pixel_threshold = difference_threshold if pixel_difference_threshold is None else pixel_difference_threshold
    row_changes = []
    if item_ids and len(item_ids) != len(item_rois):
        raise ValueError("Pixel stability row ids must match the measured regions")
    for index, item in enumerate(item_rois):
        core = Rect(item.x + pixel_inset.x, item.y + pixel_inset.y,
                    item.width - 2 * pixel_inset.x, item.height - 2 * pixel_inset.y)
        if pixel_inset.x < 0 or pixel_inset.y < 0 or core.width <= 0 or core.height <= 0:
            raise ValueError("Pixel stability inset must leave a nonempty item interior")
        first_row, second_row = frame_view(before).crop(core).raw, frame_view(after).crop(core).raw
        row_difference = np.max(cv2.absdiff(first_row, second_row), axis=2)
        row_changes.append(RowPixelChange(item_ids[index] if item_ids else f"row_{index + 1}",
                                          frame_view(before).region(core),
                                          float(np.mean(row_difference > pixel_threshold))))
    return ScrollMovementObservation(
        float(difference.mean()), float(np.mean(difference > difference_threshold)), int(difference.max()),
        dx * first.shape[1] / width / transform.scale_x,
        dy * first.shape[0] / height / transform.scale_y, response,
        maximum_row_changed_fraction=max((row.changed_fraction for row in row_changes),
                                         default=float(np.mean(difference > pixel_threshold))),
        row_pixel_changes=tuple(row_changes),
    )
