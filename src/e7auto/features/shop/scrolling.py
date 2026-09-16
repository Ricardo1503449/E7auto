from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Callable

from e7auto.core.types import Point
from e7auto.configuration.models import ScrollConfig
from e7auto.core.domain import StopReason
from e7auto.core.ports import TextRunLogger
from e7auto.features.shop.contracts import ScrollMovementObservation, ScrollOverlapObservation, SCROLL_OVERLAP_MAXIMUM_HORIZONTAL_SHIFT_PX, SCROLL_OVERLAP_MINIMUM_HEIGHT_FRACTION
from e7auto.runtime.stop_control import StopExecution


@dataclass(frozen=True, slots=True)
class FrameSample:
    frame: object
    captured_at: float


@dataclass(slots=True)
class ScrollProgress:
    """Metrics remain available to the caller even when verification stops."""
    elapsed_ms: int = 0
    samples: int = 0
    comparisons: int = 0
    early_exit_ms: int = 0


@dataclass(frozen=True, slots=True)
class ScrollServices:
    """Narrow callbacks preserve the engine's guarded input and active clock."""
    capture: Callable[[], object]
    dispatch_scroll: Callable[[Point, int, int], None]
    checkpoint: Callable[[], None]
    sleep: Callable[[float], None]
    active_monotonic: Callable[[], float]
    measure_stability: Callable[[object, object], ScrollMovementObservation]
    measure_movement: Callable[[object, object], ScrollMovementObservation]
    verify_overlap: Callable[[object, object, float, float], ScrollOverlapObservation]
    inventory_height: int
    logger: TextRunLogger


def scroll_to_bottom(
    scroll: ScrollConfig,
    stable_frames: int,
    services: ScrollServices,
    progress: ScrollProgress,
) -> tuple[FrameSample, ...]:
    """Replay calibrated wheels and return only verified, time-separated frames."""
    before = services.capture()
    point = scroll.cursor_point
    delta = scroll.delta
    for index in range(scroll.repetitions):
        services.dispatch_scroll(point, delta, index + 1)
        if index + 1 < scroll.repetitions:
            services.checkpoint()
            services.sleep(scroll.interval_ms / 1000)

    services.checkpoint()
    settle_started = services.active_monotonic()
    settle_deadline = settle_started + scroll.settle_ms / 1000
    services.sleep(scroll.minimum_settle_ms / 1000)

    previous: object | None = None
    first: object | None = None
    cumulative_x = 0.0
    cumulative_y = 0.0
    cumulative_pairs = 0
    cumulative_valid = True
    verification_method = "none"
    fallback_checks = 0
    fallback_reason = "not_needed"
    last_overlap: ScrollOverlapObservation | None = None
    verified_frame: object | None = None
    verified_movement: ScrollMovementObservation | None = None
    last_total_movement: ScrollMovementObservation | None = None
    total_gate_checks = 0
    stable = 0
    sample_elapsed: list[str] = []
    pair_shift_y: list[str] = []
    pair_response: list[str] = []
    pair_changed_fraction: list[str] = []
    stable_counts: list[str] = []
    frame_samples: list[FrameSample] = []

    while True:
        services.checkpoint()
        current = services.capture()
        captured_at = services.active_monotonic()
        frame_samples.append(FrameSample(current, captured_at))
        if len(frame_samples) > stable_frames:
            del frame_samples[0]
        progress.samples += 1
        progress.elapsed_ms = max(
            0,
            round((captured_at - settle_started) * 1000),
        )
        sample_elapsed.append(str(progress.elapsed_ms))

        if previous is None:
            first = current
            pair_shift_y.append("na")
            pair_response.append("na")
            pair_changed_fraction.append("na")
        else:
            observation = services.measure_stability(
                previous,
                current,
            )
            progress.comparisons += 1
            if not all(isfinite(value) for value in (
                observation.phase_shift_x, observation.phase_shift_y, observation.phase_response,
            )):
                cumulative_valid = False
                fallback_reason = "non_finite_pair"
            if cumulative_valid:
                cumulative_x += observation.phase_shift_x
                cumulative_y += observation.phase_shift_y
                cumulative_pairs += 1
                if not isfinite(cumulative_x) or not isfinite(cumulative_y):
                    cumulative_valid = False
                    fallback_reason = "non_finite_sum"
            pair_shift_y.append(f"{observation.phase_shift_y:.3f}")
            pair_response.append(f"{observation.phase_response:.6f}")
            pair_changed_fraction.append(
                f"{observation.changed_fraction:.6f}"
            )
            if (
                abs(observation.phase_shift_y)
                <= scroll.maximum_pairwise_shift_px
                and observation.phase_response >= scroll.minimum_phase_response
            ):
                stable += 1
            else:
                stable = 0
            if stable >= scroll.stable_observations:
                last_total_movement = services.measure_movement(
                    before,
                    current,
                )
                total_gate_checks += 1
                if (
                    last_total_movement.phase_shift_y
                    < -scroll.minimum_upward_shift_px
                    and last_total_movement.changed_fraction
                    > scroll.minimum_changed_fraction
                ):
                    verified_frame = current
                    verified_movement = last_total_movement
                    verification_method = "primary"
                else:
                    overlap_height = max(0.0, 1.0 - abs(cumulative_y) / services.inventory_height)
                    if not cumulative_valid:
                        pass  # Preserve the reason for disabling this scroll's fallback.
                    elif cumulative_y >= -scroll.minimum_upward_shift_px:
                        fallback_reason = "insufficient_net_upward_shift"
                    elif abs(cumulative_x) > SCROLL_OVERLAP_MAXIMUM_HORIZONTAL_SHIFT_PX:
                        fallback_reason = "horizontal_shift"
                    elif not last_total_movement.changed_fraction > scroll.minimum_changed_fraction:
                        fallback_reason = "insufficient_total_change"
                    elif overlap_height < SCROLL_OVERLAP_MINIMUM_HEIGHT_FRACTION:
                        fallback_reason = "insufficient_overlap"
                    else:
                        assert first is not None
                        services.checkpoint()
                        last_overlap = services.verify_overlap(first, current, cumulative_x, cumulative_y)
                        fallback_checks += 1
                        fallback_reason = last_overlap.reason
                        if last_overlap.accepted:
                            verified_frame = current
                            # Keep B -> current metrics truthful even on fallback success.
                            verified_movement = last_total_movement
                            verification_method = "cumulative_overlap"

        stable_counts.append(str(stable))
        if verified_frame is not None:
            break

        previous = current
        remaining = settle_deadline - services.active_monotonic()
        if remaining <= 1e-9:
            break
        services.checkpoint()
        services.sleep(
            min(scroll.settle_poll_interval_ms / 1000, remaining)
        )

    progress.early_exit_ms = max(0, scroll.settle_ms - progress.elapsed_ms)
    trace_fields: dict[str, object] = {
        "minimum_ms": scroll.minimum_settle_ms,
        "poll_ms": scroll.settle_poll_interval_ms,
        "maximum_ms": scroll.settle_ms,
        "required_stable_observations": scroll.stable_observations,
        "shift_tolerance_px": f"{scroll.maximum_pairwise_shift_px:.3f}",
        "minimum_phase_response": f"{scroll.minimum_phase_response:.6f}",
        "downsample_factor": scroll.downsample_factor,
        "sample_count": progress.samples,
        "stability_comparisons": progress.comparisons,
        "total_gate_checks": total_gate_checks,
        "sample_elapsed_ms": ",".join(sample_elapsed),
        "pair_shift_y": ",".join(pair_shift_y),
        "pair_response": ",".join(pair_response),
        "pair_changed_fraction": ",".join(pair_changed_fraction),
        "stable_counts": ",".join(stable_counts),
        "settle_elapsed_ms": progress.elapsed_ms,
        "early_exit_ms": progress.early_exit_ms,
        "verification_method": verification_method,
        "fallback_checks": fallback_checks,
    }
    # A later primary success must not hide earlier failures or invalid samples.
    first_primary_success = (
        verification_method == "primary" and total_gate_checks == 1 and cumulative_valid
    )
    if not first_primary_success:
        trace_fields.update(
            cumulative_shift_x=f"{cumulative_x:.3f}",
            cumulative_shift_y=f"{cumulative_y:.3f}",
            cumulative_pairs=cumulative_pairs,
            cumulative_valid=cumulative_valid,
            fallback_reason=fallback_reason,
            fallback_result=(
                "not_checked" if last_overlap is None
                else "accepted" if last_overlap.accepted else "rejected"
            ),
            fallback_overlap_height_fraction=(
                "na" if last_overlap is None else f"{last_overlap.overlap_height_fraction:.6f}"
            ),
            fallback_block_scores=(
                "na" if last_overlap is None else ",".join(
                    "na" if score is None else f"{score:.6f}" for score in last_overlap.block_scores
                )
            ),
            fallback_passed_blocks=0 if last_overlap is None else last_overlap.passed_blocks,
        )
    if verified_frame is None:
        services.logger.event(
            "scroll_settle_trace",
            outcome="timeout",
            total_phase_shift_y=(
                "na"
                if last_total_movement is None
                else f"{last_total_movement.phase_shift_y:.3f}"
            ),
            total_phase_response=(
                "na"
                if last_total_movement is None
                else f"{last_total_movement.phase_response:.6f}"
            ),
            total_changed_fraction=(
                "na"
                if last_total_movement is None
                else f"{last_total_movement.changed_fraction:.6f}"
            ),
            **trace_fields,
        )
        raise StopExecution(
            StopReason.SCROLL_VERIFICATION_FAILED,
            "inventory did not become stable with verified bottom displacement before the "
            f"{scroll.settle_ms} ms maximum",
        )

    assert verified_movement is not None
    movement = verified_movement
    services.logger.event(
        "scroll_settle_trace",
        outcome="stable",
        total_phase_shift_y=f"{movement.phase_shift_y:.3f}",
        total_phase_response=f"{movement.phase_response:.6f}",
        total_changed_fraction=f"{movement.changed_fraction:.6f}",
        **trace_fields,
    )
    services.logger.event(
        "scroll_verified",
        phase_shift_x=f"{movement.phase_shift_x:.3f}",
        phase_shift_y=f"{movement.phase_shift_y:.3f}",
        phase_response=f"{movement.phase_response:.6f}",
        mean_absolute_difference=f"{movement.mean_absolute_difference:.6f}",
        maximum_difference=movement.maximum_difference,
        changed_fraction=f"{movement.changed_fraction:.6f}",
        difference_threshold=scroll.difference_threshold,
        settle_elapsed_ms=progress.elapsed_ms,
        settle_maximum_ms=scroll.settle_ms,
        early_exit_ms=progress.early_exit_ms,
        sample_count=progress.samples,
        stability_comparisons=progress.comparisons,
    )
    return tuple(frame_samples)
