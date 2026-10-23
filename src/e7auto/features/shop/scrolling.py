from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from math import isfinite
from typing import Callable
from e7auto.core.types import Point
from e7auto.configuration.models import ScrollConfig, InventoryReadinessConfig
from e7auto.core.domain import StopReason
from e7auto.core.ports import TextRunLogger
from e7auto.features.shop.contracts import ScrollMovementObservation
from e7auto.runtime.stop_control import StopExecution
from e7auto.features.shop.readiness import SceneSampleEvidence, pixels_are_stable, format_row_regions, format_row_changes


def _poll_schedule(scroll: ScrollConfig, stable_frames: int, readiness: InventoryReadinessConfig) -> str:
    return ("stable_window_target" if max(stable_frames, readiness.minimum_frames) == 3
            and scroll.stable_observations == 2 else "capture_interval")


@dataclass(frozen=True, slots=True)
class FrameSample:
    frame: object
    captured_at: float
    generation: int = 0
    scene_evidence: SceneSampleEvidence | None = None


@dataclass(slots=True)
class ScrollProgress:
    elapsed_ms: int = 0
    samples: int = 0
    comparisons: int = 0
    early_exit_ms: int = 0


@dataclass(frozen=True, slots=True)
class ScrollServices:
    capture: Callable[[], object]
    dispatch_scroll: Callable[[Point, int, int], None]
    checkpoint: Callable[[], None]
    sleep: Callable[[float], None]
    active_monotonic: Callable[[], float]
    measure_stability: Callable[[object, object], ScrollMovementObservation]
    measure_movement: Callable[[object, object], ScrollMovementObservation]
    logger: TextRunLogger
    scene_state: Callable[[object], str]
    recovery_generation: Callable[[], int] = lambda: 0


def scroll_to_bottom(scroll: ScrollConfig, stable_frames: int, services: ScrollServices,
                     progress: ScrollProgress, *, reference: FrameSample,
                     readiness: InventoryReadinessConfig) -> tuple[FrameSample, ...]:
    """One content-verification path; all work shares the original active deadline."""
    services.checkpoint()
    age_ms = (services.active_monotonic() - reference.captured_at) * 1000
    if (reference.generation != services.recovery_generation() or not isfinite(age_ms)
            or age_ms < 0 or age_ms > readiness.reference_max_age_ms):
        raise StopExecution(StopReason.RECOGNITION_TIMEOUT, "scroll reference is stale or invalidated")
    before = reference.frame
    services.logger.event("scroll_reference_accepted", age_ms=round(age_ms, 3), generation=reference.generation,
                          source="validated_top_inventory")
    for index in range(scroll.repetitions):
        services.dispatch_scroll(scroll.cursor_point, scroll.delta, index + 1)
        if index + 1 < scroll.repetitions:
            services.checkpoint()
            services.sleep(scroll.interval_ms / 1000)
    services.checkpoint()
    started = services.active_monotonic()
    deadline = started + scroll.settle_ms / 1000
    services.sleep(scroll.minimum_settle_ms / 1000)
    previous: FrameSample | None = None
    generation = reference.generation
    if generation != services.recovery_generation():
        raise StopExecution(StopReason.SCROLL_VERIFICATION_FAILED, "network recovery invalidated the scroll reference")
    stable = 0
    samples: list[FrameSample] = []
    elapsed: list[str] = []
    shifts: list[str] = []
    responses: list[str] = []
    changes: list[str] = []
    stable_counts: list[str] = []
    row_changes: list[str] = []
    scene_states: list[str] = []
    pair_rows: list[str] = []
    span_rows: list[str] = []
    row_regions = "na"
    total: ScrollMovementObservation | None = None
    content_checks: list[tuple[int, ScrollMovementObservation]] = []
    total_checks = 0
    verified = False
    window_schedule = _poll_schedule(scroll, stable_frames, readiness) == "stable_window_target"

    while services.active_monotonic() <= deadline + 1e-9:
        services.checkpoint()
        capture_started = services.active_monotonic()
        current = services.capture()
        services.checkpoint()
        captured_at = services.active_monotonic()
        progress.samples += 1
        progress.elapsed_ms = max(0, round((captured_at - started) * 1000))
        elapsed.append(str(max(0, round((captured_at - started) * 1000))))
        if services.recovery_generation() != generation:
            raise StopExecution(StopReason.SCROLL_VERIFICATION_FAILED, "network recovery invalidated the scroll reference")
        sample = FrameSample(current, captured_at, generation)
        pair_rows.append("na")
        span_rows.append("na")
        samples.append(sample)
        samples = samples[-max(stable_frames, readiness.minimum_frames):]
        fresh = previous is None or captured_at > previous.captured_at
        if captured_at > deadline + 1e-9:
            shifts.append("na"); responses.append("na"); changes.append("na")
            row_changes.append("na")
            scene_states.append("deadline")
            stable_counts.append(str(stable))
            break
        scene = services.scene_state(current)
        scene_states.append(scene)
        pair = span = None
        pair_before_at = span_before_at = None
        if scene != "ready":
            previous, stable, samples = None, 0, []
            shifts.append("na"); responses.append("na"); changes.append("na"); row_changes.append("na")
        elif previous is None or not fresh:
            stable = 0
            samples = [sample]
            shifts.append("na"); responses.append("na"); changes.append("na")
            row_changes.append("na")
        else:
            pair_before_at = previous.captured_at
            pair = services.measure_stability(previous.frame, current)
            pair_rows[-1] = format_row_changes(pair.row_pixel_changes)
            row_regions = format_row_regions(pair.row_pixel_changes)
            progress.comparisons += 1
            shifts.append(f"{pair.phase_shift_y:.3f}")
            responses.append(f"{pair.phase_response:.6f}")
            changes.append(f"{pair.changed_fraction:.6f}")
            maximum_change = pair.maximum_row_changed_fraction
            stable_pair = pixels_are_stable(pair, scroll, readiness)
            if stable_pair and len(samples) >= 3:
                span_before_at = samples[0].captured_at
                span = services.measure_stability(samples[0].frame, current)
                span_rows[-1] = format_row_changes(span.row_pixel_changes)
                stable_pair = pixels_are_stable(span, scroll, readiness)
                maximum_change = max(maximum_change, span.maximum_row_changed_fraction)
            row_changes.append(f"{maximum_change:.6f}")
            if stable_pair:
                stable += 1
            else:
                stable = 0
                samples = [sample]
            duration_ready = ((captured_at - samples[0].captured_at) * 1000 + 1e-6
                              >= readiness.minimum_stable_ms)
            if (stable >= scroll.stable_observations and len(samples) >= readiness.minimum_frames and duration_ready
                    and services.active_monotonic() <= deadline + 1e-9):
                services.checkpoint()
                total = services.measure_movement(before, current)
                total_checks += 1
                if total.content_candidates:
                    content_checks.append((progress.samples, total))
                finite = all(isfinite(v) for v in (total.phase_shift_x, total.phase_shift_y, total.changed_fraction))
                verified = (finite and total.content_verified
                            and total.phase_shift_y < -scroll.minimum_upward_shift_px
                            and abs(total.phase_shift_x) <= scroll.maximum_horizontal_shift_px
                            and total.changed_fraction > scroll.minimum_changed_fraction)
                if not verified:
                    stable = 0
                    samples = [sample]
        if scene == "ready":
            sample = replace(sample, scene_evidence=SceneSampleEvidence(
                current, captured_at, generation, "bottom", pair_before_at, pair, span_before_at, span,
            ))
            samples[-1] = sample
        services.checkpoint()
        stable_counts.append(str(stable))
        now = services.active_monotonic()
        if now > deadline + 1e-9:
            verified = False
            break
        if verified:
            break
        previous = sample if scene == "ready" else None
        remaining = deadline - now
        if remaining <= 1e-9:
            break
        interval = scroll.settle_poll_interval_ms / 1000
        next_capture_at = captured_at + interval
        next_window_aligned = (window_schedule and scene == "ready"
                               and fresh and stable == 1 and len(samples) == 2)
        if next_window_aligned:
            # The third capture starts only after the full stable window, while
            # including the second capture's work in the sampling interval.
            next_capture_at = max(samples[0].captured_at + readiness.minimum_stable_ms / 1000,
                                  capture_started + interval)
        wait = min(next_capture_at, deadline) - now
        if wait > 0:
            services.sleep(wait)

    services.checkpoint()
    progress.elapsed_ms = max(0, round((services.active_monotonic() - started) * 1000))
    progress.early_exit_ms = max(0, scroll.settle_ms - progress.elapsed_ms)
    fields = dict(
        outcome="stable" if verified else "timeout", verification_method="content" if verified else "none",
        minimum_ms=scroll.minimum_settle_ms, poll_ms=scroll.settle_poll_interval_ms, maximum_ms=scroll.settle_ms,
        required_stable_observations=scroll.stable_observations,
        shift_tolerance_px=f"{scroll.maximum_pairwise_shift_px:.3f}",
        minimum_phase_response=f"{scroll.minimum_phase_response:.6f}", downsample_factor=scroll.downsample_factor,
        sample_count=progress.samples, stability_comparisons=progress.comparisons, total_gate_checks=total_checks,
        poll_schedule=_poll_schedule(scroll, stable_frames, readiness),
        sample_elapsed_ms=",".join(elapsed), pair_shift_y=",".join(shifts), pair_response=",".join(responses),
        pair_changed_fraction=",".join(changes), stable_counts=",".join(stable_counts),
        maximum_row_changed_fraction=",".join(row_changes),
        scene_states=",".join(scene_states),
        row_regions=row_regions,
        pair_row_changes=";".join(pair_rows),
        span_row_changes=";".join(span_rows),
        settle_elapsed_ms=progress.elapsed_ms, early_exit_ms=progress.early_exit_ms,
        total_phase_shift_y="na" if total is None else f"{total.phase_shift_y:.3f}",
        total_phase_response="na" if total is None else f"{total.phase_response:.6f}",
        total_changed_fraction="na" if total is None else f"{total.changed_fraction:.6f}",
        content_reason="not_checked" if total is None else total.content_reason,
        content_scores="na" if total is None else ",".join("na" if s is None else f"{s:.6f}" for s in total.content_scores),
    )
    if content_checks:
        fields["minimum_content_margin"] = scroll.minimum_content_margin
        fields["content_candidate_checks"] = json.dumps([
            dict(sample=sample_number, selected=observation.content_selected_candidate,
                 runner_up=observation.content_runner_up_candidate, margin=observation.content_margin,
                 reason=observation.content_reason,
                 candidates=[asdict(candidate) for candidate in observation.content_candidates])
            for sample_number, observation in content_checks
        ], separators=(",", ":"), allow_nan=False)
    try:
        services.logger.event("scroll_settle_trace", **fields)
    except Exception:
        pass  # Trace output must not change the scroll result.
    if not verified:
        raise StopExecution(StopReason.SCROLL_VERIFICATION_FAILED,
                            f"inventory content did not verify a stable bottom displacement within {scroll.settle_ms} ms")
    assert total is not None
    services.logger.event(
        "scroll_verified", phase_shift_x=f"{total.phase_shift_x:.3f}", phase_shift_y=f"{total.phase_shift_y:.3f}",
        phase_response=f"{total.phase_response:.6f}", mean_absolute_difference=f"{total.mean_absolute_difference:.6f}",
        changed_fraction=f"{total.changed_fraction:.6f}", maximum_difference=total.maximum_difference,
        difference_threshold=scroll.difference_threshold, settle_elapsed_ms=progress.elapsed_ms,
        early_exit_ms=progress.early_exit_ms, settle_maximum_ms=scroll.settle_ms,
        sample_count=progress.samples, stability_comparisons=progress.comparisons,
    )
    return tuple(samples)
