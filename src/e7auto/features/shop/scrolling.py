from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Callable
from e7auto.core.types import Point
from e7auto.configuration.models import ScrollConfig
from e7auto.core.domain import StopReason
from e7auto.core.ports import TextRunLogger
from e7auto.features.shop.contracts import ScrollMovementObservation
from e7auto.runtime.stop_control import StopExecution


@dataclass(frozen=True, slots=True)
class FrameSample:
    frame: object
    captured_at: float


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
    recovery_generation: Callable[[], int] = lambda: 0


def scroll_to_bottom(scroll: ScrollConfig, stable_frames: int, services: ScrollServices,
                     progress: ScrollProgress) -> tuple[FrameSample, ...]:
    """One content-verification path; all work shares the original active deadline."""
    before = services.capture()
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
    generation = services.recovery_generation()
    stable = 0
    samples: list[FrameSample] = []
    elapsed: list[str] = []
    shifts: list[str] = []
    responses: list[str] = []
    changes: list[str] = []
    stable_counts: list[str] = []
    total: ScrollMovementObservation | None = None
    total_checks = 0
    verified = False

    while services.active_monotonic() <= deadline + 1e-9:
        services.checkpoint()
        current = services.capture()
        services.checkpoint()
        captured_at = services.active_monotonic()
        progress.samples += 1
        progress.elapsed_ms = max(0, round((captured_at - started) * 1000))
        elapsed.append(str(max(0, round((captured_at - started) * 1000))))
        if services.recovery_generation() != generation:
            generation = services.recovery_generation()
            previous, stable, samples = None, 0, []
        sample = FrameSample(current, captured_at)
        samples.append(sample)
        samples = samples[-stable_frames:]
        fresh = previous is None or captured_at > previous.captured_at
        if captured_at > deadline + 1e-9:
            shifts.append("na"); responses.append("na"); changes.append("na")
            stable_counts.append(str(stable))
            break
        if previous is None or not fresh:
            stable = 0
            samples = [sample]
            shifts.append("na"); responses.append("na"); changes.append("na")
        else:
            pair = services.measure_stability(previous.frame, current)
            progress.comparisons += 1
            shifts.append(f"{pair.phase_shift_y:.3f}")
            responses.append(f"{pair.phase_response:.6f}")
            changes.append(f"{pair.changed_fraction:.6f}")
            finite = all(isfinite(v) for v in (pair.phase_shift_x, pair.phase_shift_y, pair.phase_response))
            if (finite and max(abs(pair.phase_shift_x), abs(pair.phase_shift_y)) <= scroll.maximum_pairwise_shift_px
                    and pair.phase_response >= scroll.minimum_phase_response):
                stable += 1
            else:
                stable = 0
                samples = [sample]
            if stable >= scroll.stable_observations and services.active_monotonic() <= deadline + 1e-9:
                services.checkpoint()
                total = services.measure_movement(before, current)
                total_checks += 1
                finite = all(isfinite(v) for v in (total.phase_shift_x, total.phase_shift_y, total.changed_fraction))
                verified = (finite and total.content_verified
                            and total.phase_shift_y < -scroll.minimum_upward_shift_px
                            and abs(total.phase_shift_x) <= scroll.maximum_horizontal_shift_px
                            and total.changed_fraction > scroll.minimum_changed_fraction)
                if not verified:
                    stable = 0
                    samples = [sample]
        services.checkpoint()
        stable_counts.append(str(stable))
        now = services.active_monotonic()
        if now > deadline + 1e-9:
            verified = False
            break
        if verified:
            break
        previous = sample
        remaining = deadline - now
        if remaining <= 1e-9:
            break
        services.sleep(min(scroll.settle_poll_interval_ms / 1000, remaining))

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
        sample_elapsed_ms=",".join(elapsed), pair_shift_y=",".join(shifts), pair_response=",".join(responses),
        pair_changed_fraction=",".join(changes), stable_counts=",".join(stable_counts),
        settle_elapsed_ms=progress.elapsed_ms, early_exit_ms=progress.early_exit_ms,
        total_phase_shift_y="na" if total is None else f"{total.phase_shift_y:.3f}",
        total_phase_response="na" if total is None else f"{total.phase_response:.6f}",
        total_changed_fraction="na" if total is None else f"{total.changed_fraction:.6f}",
        content_reason="not_checked" if total is None else total.content_reason,
        content_scores="na" if total is None else ",".join("na" if s is None else f"{s:.6f}" for s in total.content_scores),
    )
    services.logger.event("scroll_settle_trace", **fields)
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
