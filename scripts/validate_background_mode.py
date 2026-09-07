from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import win32api
import win32gui

from e7auto.background_windows import Win32WindowMessageInputService
from e7auto.config import Rect, load_config
from e7auto.platform_windows import Win32WindowService, enable_per_monitor_dpi_awareness
from e7auto.vision import OpenCvGameVision, TemplateRepository, measure_inventory_scroll

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "internal.yaml"


def _write_result(path: Path, result: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _check_background_state(
    windows: Win32WindowService,
    window,
    expected_bounds: Rect,
    expected_foreground: int,
) -> None:
    state = windows.inspect(window)
    if (
        not state.exists
        or state.minimized
        or state.client_bounds != expected_bounds
    ):
        raise RuntimeError(
            f"game window disappeared, minimized, moved, or resized: {state}"
        )
    foreground = int(win32gui.GetForegroundWindow())
    if state.foreground or foreground == window.hwnd:
        raise RuntimeError("game unexpectedly became the foreground window")
    if foreground != expected_foreground:
        raise RuntimeError(
            f"foreground window changed during validation: {expected_foreground} -> {foreground}"
        )


def _observation_dict(observation) -> dict[str, object]:
    if observation is None:
        return {"detected": False}
    return {"detected": True, "confidence": float(observation.confidence)}


def _first_stable_run(
    values: Sequence[Any],
    required: int,
    is_valid: Callable[[Any], bool] | None = None,
) -> tuple[int, Any] | None:
    if required <= 0:
        raise ValueError("required must be positive")
    valid = is_valid or (lambda _value: True)
    previous: Any = None
    has_previous = False
    stable = 0
    for index, value in enumerate(values):
        if not valid(value):
            previous = None
            has_previous = False
            stable = 0
            continue
        if has_previous and value == previous:
            stable += 1
        else:
            previous = value
            has_previous = True
            stable = 1
        if stable >= required:
            return index, value
    return None


def summarize_samples(
    samples: Sequence[dict[str, object]], stable_frames: int
) -> dict[str, object]:
    refresh_values = tuple(
        bool(sample["refresh"]["detected"]) for sample in samples  # type: ignore[index]
    )
    balance_values = tuple(
        sample["sky_stone"].get("value")  # type: ignore[union-attr]
        if sample["sky_stone"]["detected"]  # type: ignore[index]
        else None
        for sample in samples
    )
    inventory_values = tuple(
        tuple(
            (match["slot_id"], match["target_id"])
            for match in sample["matches"]  # type: ignore[union-attr]
        )
        for sample in samples
    )

    refresh_stable = _first_stable_run(
        refresh_values,
        stable_frames,
        lambda value: value is True,
    )
    balance_stable = _first_stable_run(
        balance_values,
        stable_frames,
        lambda value: value is not None,
    )
    inventory_stable = _first_stable_run(inventory_values, stable_frames)

    def stability_result(result: tuple[int, Any] | None) -> dict[str, object]:
        if result is None:
            return {"achieved": False}
        index, value = result
        serialized = [list(item) for item in value] if isinstance(value, tuple) else value
        return {
            "achieved": True,
            "sample": index + 1,
            "time_to_stable_ms": float(samples[index]["elapsed_ms"]),
            "value": serialized,
        }

    timing_keys = ("capture", "refresh", "sky_stone", "inventory", "total")
    timing_summary = {
        key: {
            "minimum_ms": min(
                float(sample["timing_ms"][key]) for sample in samples  # type: ignore[index]
            ),
            "mean_ms": statistics.fmean(
                float(sample["timing_ms"][key]) for sample in samples  # type: ignore[index]
            ),
            "maximum_ms": max(
                float(sample["timing_ms"][key]) for sample in samples  # type: ignore[index]
            ),
        }
        for key in timing_keys
    }
    observed_targets = sorted(
        {
            str(match["target_id"])
            for sample in samples
            for match in sample["matches"]  # type: ignore[union-attr]
        }
    )
    return {
        "stability": {
            "refresh": stability_result(refresh_stable),
            "sky_stone": stability_result(balance_stable),
            "inventory": stability_result(inventory_stable),
        },
        "timing": timing_summary,
        "observed_targets": observed_targets,
        "samples": list(samples),
    }


def _sample_viewport(
    *,
    screen: str,
    sample_count: int,
    interval_ms: int,
    windows: Win32WindowService,
    capture: Any,
    window,
    bounds: Rect,
    foreground: int,
    vision: OpenCvGameVision,
) -> tuple[list[dict[str, object]], np.ndarray, np.ndarray]:
    samples: list[dict[str, object]] = []
    first: np.ndarray | None = None
    last: np.ndarray | None = None
    started = time.perf_counter()
    for index in range(sample_count):
        _check_background_state(windows, window, bounds, foreground)
        capture_started = time.perf_counter()
        frame = capture.capture_client(window, bounds)
        capture_ms = (time.perf_counter() - capture_started) * 1000
        if frame.shape != (bounds.height, bounds.width, 4):
            raise RuntimeError(f"unexpected background frame shape: {frame.shape}")
        if first is None:
            first = frame
        last = frame

        refresh_started = time.perf_counter()
        refresh = vision.shop_ready(frame)
        refresh_ms = (time.perf_counter() - refresh_started) * 1000
        balance_started = time.perf_counter()
        balance = vision.sky_stone_balance(frame)
        balance_ms = (time.perf_counter() - balance_started) * 1000
        inventory_started = time.perf_counter()
        matches = vision.scan_inventory(frame, screen)
        inventory_ms = (time.perf_counter() - inventory_started) * 1000
        samples.append(
            {
                "index": index + 1,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
                "shape": list(frame.shape),
                "timing_ms": {
                    "capture": capture_ms,
                    "refresh": refresh_ms,
                    "sky_stone": balance_ms,
                    "inventory": inventory_ms,
                    "total": capture_ms + refresh_ms + balance_ms + inventory_ms,
                },
                "refresh": _observation_dict(refresh),
                "sky_stone": (
                    {"detected": False}
                    if balance is None
                    else {
                        "detected": True,
                        "value": balance.value,
                        "confidence": float(balance.confidence),
                    }
                ),
                "matches": [
                    {
                        "slot_id": match.slot_id,
                        "target_id": match.target_id,
                        "confidence": float(match.confidence),
                    }
                    for match in matches
                ],
            }
        )
        if index + 1 < sample_count and interval_ms:
            time.sleep(interval_ms / 1000)
    assert first is not None and last is not None
    return samples, first, last


def _capture_latency(samples: Sequence[dict[str, object]]) -> dict[str, float]:
    values = np.asarray(
        [float(sample["timing_ms"]["capture"]) for sample in samples],  # type: ignore[index]
        dtype=np.float64,
    )
    return {
        "minimum_ms": float(values.min()),
        "mean_ms": float(values.mean()),
        "p95_ms": float(np.percentile(values, 95)),
        "maximum_ms": float(values.max()),
    }


def _warm_capture(
    *,
    windows: Win32WindowService,
    capture,
    window,
    bounds: Rect,
    foreground: int,
) -> float:
    _check_background_state(windows, window, bounds, foreground)
    started = time.perf_counter()
    frame = capture.capture_client(window, bounds)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if frame.shape != (bounds.height, bounds.width, 4):
        raise RuntimeError(f"unexpected background warm-up frame shape: {frame.shape}")
    return elapsed_ms


def _base_context(args: argparse.Namespace):
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("background validator must run as Windows administrator")
    if args.sample_count < 3:
        raise RuntimeError("sample-count must be at least 3")
    if args.interval_ms < 0:
        raise RuntimeError("interval-ms must be non-negative")
    enable_per_monitor_dpi_awareness()
    config = load_config(CONFIG_PATH)
    templates = TemplateRepository(config)
    vision = OpenCvGameVision(config, templates)
    windows = Win32WindowService()
    window = windows.locate_unique(str(config.executable_path), config.window_title)
    state = windows.inspect(window)
    if (
        not state.exists
        or state.minimized
        or state.foreground
        or state.client_bounds.width != config.baseline_client_size.width
        or state.client_bounds.height != config.baseline_client_size.height
    ):
        raise RuntimeError(
            "game must be restored, covered, non-foreground, and at the exact "
            f"{config.baseline_client_size.width} x {config.baseline_client_size.height} "
            f"client size: {state}"
        )
    foreground = int(win32gui.GetForegroundWindow())
    if not foreground or foreground == window.hwnd:
        raise RuntimeError("a non-game foreground window must cover the game")
    return config, vision, windows, window, state.client_bounds, foreground


def _capture_service(name: str):
    if name == "wgc":
        from e7auto.wgc_capture import WindowsGraphicsCaptureService

        return WindowsGraphicsCaptureService()
    raise RuntimeError(f"unsupported capture backend: {name}")


def _wgc_diagnostic() -> str:
    from e7auto.wgc_capture import WindowsGraphicsCaptureService

    return WindowsGraphicsCaptureService.support_diagnostic()[1]


def _stable_observation(
    *,
    detector,
    label: str,
    timeout_ms: int,
    poll_interval_ms: int,
    stable_frames: int,
    windows: Win32WindowService,
    capture,
    window,
    bounds: Rect,
    foreground: int,
):
    deadline = time.monotonic() + timeout_ms / 1000
    previous_key: tuple[int, int] | None = None
    stable = 0
    while time.monotonic() <= deadline:
        _check_background_state(windows, window, bounds, foreground)
        frame = capture.capture_client(window, bounds)
        observation = detector(frame)
        if observation is None:
            previous_key = None
            stable = 0
        else:
            key = (observation.anchor.x, observation.anchor.y)
            stable = stable + 1 if key == previous_key else 1
            previous_key = key
            if stable >= stable_frames:
                return observation
        time.sleep(poll_interval_ms / 1000)
    raise RuntimeError(f"{label} did not reach stable recognition before timeout")


def _assert_cursor_and_foreground(cursor: tuple[int, int], foreground: int) -> None:
    if tuple(int(value) for value in win32api.GetCursorPos()) != cursor:
        raise RuntimeError("physical cursor changed during background input validation")
    if int(win32gui.GetForegroundWindow()) != foreground:
        raise RuntimeError("foreground window changed during background input validation")


def _observe_scroll_effect(
    *,
    config,
    windows: Win32WindowService,
    capture,
    window,
    bounds: Rect,
    foreground: int,
    cursor: tuple[int, int],
    before: np.ndarray,
    observation_ms: int,
    initial_wait_ms: int | None = None,
    started_at: float | None = None,
):
    if initial_wait_ms is None:
        initial_wait_ms = config.scroll.settle_ms
    if initial_wait_ms < 0:
        raise ValueError("initial_wait_ms must be non-negative")
    observation_started = time.monotonic() if started_at is None else started_at
    time.sleep(initial_wait_ms / 1000)
    deadline = observation_started + observation_ms / 1000
    trace: list[dict[str, float | bool]] = []
    latest_frame: np.ndarray | None = None
    latest_movement = None
    while True:
        _check_background_state(windows, window, bounds, foreground)
        _assert_cursor_and_foreground(cursor, foreground)
        latest_frame = capture.capture_client(window, bounds)
        latest_movement = measure_inventory_scroll(
            before,
            latest_frame,
            config.rois["inventory_list"],
            config.scroll.difference_threshold,
        )
        passed = (
            latest_movement.phase_shift_y < -config.scroll.minimum_upward_shift_px
            and latest_movement.changed_fraction > config.scroll.minimum_changed_fraction
        )
        trace.append(
            {
                "elapsed_after_last_input_ms": (
                    time.monotonic() - observation_started
                )
                * 1000,
                "phase_shift_y": latest_movement.phase_shift_y,
                "changed_fraction": latest_movement.changed_fraction,
                "passed": passed,
            }
        )
        if passed or time.monotonic() >= deadline:
            return latest_frame, latest_movement, trace
        remaining = deadline - time.monotonic()
        time.sleep(min(config.scroll.settle_poll_interval_ms / 1000, remaining))


def _run_capture(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    config, vision, windows, window, bounds, foreground = _base_context(args)
    cursor_before = tuple(int(value) for value in win32api.GetCursorPos())
    capture = _capture_service(args.capture_backend)
    try:
        startup_ms = _warm_capture(
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            foreground=foreground,
        )
        samples, _, _ = _sample_viewport(
            screen="top",
            sample_count=args.sample_count,
            interval_ms=args.interval_ms,
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            foreground=foreground,
            vision=vision,
        )
    finally:
        capture.close()
    _check_background_state(windows, window, bounds, foreground)
    cursor_after = tuple(int(value) for value in win32api.GetCursorPos())
    summary = summarize_samples(samples, config.timing.stable_frames)
    latency = _capture_latency(samples)
    stability = summary["stability"]
    criteria = {
        "capture_backend_available": True,
        "all_frames_exact_client_size": all(
            tuple(sample["shape"])
            == (bounds.height, bounds.width, 4)
            for sample in samples
        ),
        "refresh_stable": bool(stability["refresh"]["achieved"]),
        "sky_stone_stable": bool(stability["sky_stone"]["achieved"]),
        "inventory_stable": bool(stability["inventory"]["achieved"]),
        "capture_p95_at_most_50_ms": latency["p95_ms"] <= 50.0,
        "capture_maximum_at_most_100_ms": latency["maximum_ms"] <= 100.0,
        "foreground_unchanged": int(win32gui.GetForegroundWindow()) == foreground,
        "cursor_unchanged": cursor_after == cursor_before,
    }
    passed = all(criteria.values())
    return (
        {
            "status": "ok" if passed else "failed",
            "mode": "capture",
            "capture": {
                "backend": args.capture_backend,
                "wgc_diagnostic": (
                    _wgc_diagnostic() if args.capture_backend == "wgc" else "not_loaded"
                ),
                "startup_ms": startup_ms,
                "latency": latency,
            },
            "window": {
                "hwnd": window.hwnd,
                "process_id": window.process_id,
                "title": window.title,
                "executable_path": window.executable_path,
                "client_bounds": bounds.__dict__ if hasattr(bounds, "__dict__") else {
                    "x": bounds.x,
                    "y": bounds.y,
                    "width": bounds.width,
                    "height": bounds.height,
                },
            },
            "foreground_hwnd": foreground,
            "cursor_before": list(cursor_before),
            "cursor_after": list(cursor_after),
            "summary": summary,
            "criteria": criteria,
            "input_events": 0,
            "screenshots_persisted": False,
        },
        0 if passed else 1,
    )


def _run_scroll(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    config, vision, windows, window, bounds, foreground = _base_context(args)
    if args.interval_ms != config.scroll.interval_ms:
        raise RuntimeError("interval-ms must match the calibrated production value")
    observation_ms = getattr(args, "effect_observation_ms", config.scroll.settle_ms)
    if not config.scroll.settle_ms <= observation_ms <= 30000:
        raise RuntimeError(
            "effect-observation-ms must be between the calibrated settle time and 30000"
        )
    cursor_before = tuple(int(value) for value in win32api.GetCursorPos())
    capture = _capture_service(args.capture_backend)
    inputs = Win32WindowMessageInputService()
    events_sent = 0
    last_input_sent_at: float | None = None
    initial_observation_wait_ms = 0
    try:
        startup_ms = _warm_capture(
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            foreground=foreground,
        )
        top_samples, _, top_last = _sample_viewport(
            screen="top",
            sample_count=args.sample_count,
            interval_ms=args.interval_ms,
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            foreground=foreground,
            vision=vision,
        )
        for index in range(config.scroll.repetitions):
            _check_background_state(windows, window, bounds, foreground)
            if tuple(int(value) for value in win32api.GetCursorPos()) != cursor_before:
                raise RuntimeError("physical cursor moved before a background wheel message")
            inputs.scroll(window, config.scroll.cursor_point, config.scroll.delta)
            events_sent += 1
            if index + 1 == config.scroll.repetitions:
                last_input_sent_at = time.monotonic()
            _check_background_state(windows, window, bounds, foreground)
            if tuple(int(value) for value in win32api.GetCursorPos()) != cursor_before:
                raise RuntimeError("background wheel message moved the physical cursor")
            if index + 1 < config.scroll.repetitions:
                time.sleep(config.scroll.interval_ms / 1000)
        _, movement, effect_trace = _observe_scroll_effect(
            config=config,
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            foreground=foreground,
            cursor=cursor_before,
            before=top_last,
            observation_ms=observation_ms,
            initial_wait_ms=initial_observation_wait_ms,
            started_at=last_input_sent_at,
        )
        bottom_samples, _, _ = _sample_viewport(
            screen="bottom",
            sample_count=args.sample_count,
            interval_ms=args.interval_ms,
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            foreground=foreground,
            vision=vision,
        )
    finally:
        capture.close()
    top = summarize_samples(top_samples, config.timing.stable_frames)
    bottom = summarize_samples(bottom_samples, config.timing.stable_frames)
    combined_samples = [*top_samples, *bottom_samples]
    latency = _capture_latency(combined_samples)
    cursor_after = tuple(int(value) for value in win32api.GetCursorPos())
    top_stability = top["stability"]
    bottom_stability = bottom["stability"]
    criteria = {
        "events_exact": events_sent == config.scroll.repetitions,
        "foreground_unchanged": int(win32gui.GetForegroundWindow()) == foreground,
        "cursor_unchanged": cursor_after == cursor_before,
        "top_refresh_stable": bool(top_stability["refresh"]["achieved"]),
        "bottom_refresh_stable": bool(bottom_stability["refresh"]["achieved"]),
        "top_inventory_stable": bool(top_stability["inventory"]["achieved"]),
        "bottom_inventory_stable": bool(bottom_stability["inventory"]["achieved"]),
        "upward_translation_passed": (
            movement.phase_shift_y < -config.scroll.minimum_upward_shift_px
        ),
        "changed_fraction_passed": (
            movement.changed_fraction > config.scroll.minimum_changed_fraction
        ),
        "capture_p95_at_most_50_ms": latency["p95_ms"] <= 50.0,
        "capture_maximum_at_most_100_ms": latency["maximum_ms"] <= 100.0,
    }
    passed = all(criteria.values())
    return (
        {
            "status": "ok" if passed else "failed",
            "mode": "scroll",
            "capture": {
                "backend": args.capture_backend,
                "startup_ms": startup_ms,
                "latency": latency,
            },
            "input": {
                "backend": "window_messages",
                "delivery": "post",
                "client_point": {
                    "x": config.scroll.cursor_point.x,
                    "y": config.scroll.cursor_point.y,
                },
                "delta": config.scroll.delta,
                "events": events_sent,
                "interval_ms": config.scroll.interval_ms,
                "settle_ms": config.scroll.settle_ms,
                "initial_observation_wait_ms": initial_observation_wait_ms,
                "effect_observation_ms": observation_ms,
            },
            "movement": {
                "phase_shift_x": movement.phase_shift_x,
                "phase_shift_y": movement.phase_shift_y,
                "phase_response": movement.phase_response,
                "changed_fraction": movement.changed_fraction,
                "mean_absolute_difference": movement.mean_absolute_difference,
                "maximum_difference": movement.maximum_difference,
            },
            "effect_trace": effect_trace,
            "top": top,
            "bottom": bottom,
            "foreground_hwnd": foreground,
            "cursor_before": list(cursor_before),
            "cursor_after": list(cursor_after),
            "criteria": criteria,
            "screenshots_persisted": False,
        },
        0 if passed else 1,
    )


def _run_navigation(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    config, vision, windows, window, bounds, foreground = _base_context(args)
    cursor_before = tuple(int(value) for value in win32api.GetCursorPos())
    capture = _capture_service(args.capture_backend)
    inputs = Win32WindowMessageInputService()
    events_sent = 0
    try:
        startup_ms = _warm_capture(
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            foreground=foreground,
        )
        entry = _stable_observation(
            detector=vision.main_shop_icon,
            label="main shop entry",
            timeout_ms=config.timing.entry_timeout_ms,
            poll_interval_ms=config.timing.poll_interval_ms,
            stable_frames=config.timing.stable_frames,
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            foreground=foreground,
        )
        _assert_cursor_and_foreground(cursor_before, foreground)
        inputs.click(window, entry.anchor)
        events_sent += 1
        _assert_cursor_and_foreground(cursor_before, foreground)
        _stable_observation(
            detector=vision.shop_ready,
            label="shop refresh control",
            timeout_ms=config.timing.entry_timeout_ms,
            poll_interval_ms=config.timing.poll_interval_ms,
            stable_frames=config.timing.stable_frames,
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            foreground=foreground,
        )
        shop_exit = _stable_observation(
            detector=vision.shop_exit_icon,
            label="shop exit control",
            timeout_ms=config.timing.scan_timeout_ms,
            poll_interval_ms=config.timing.poll_interval_ms,
            stable_frames=config.timing.stable_frames,
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            foreground=foreground,
        )
        inputs.click(window, shop_exit.anchor)
        events_sent += 1
        _assert_cursor_and_foreground(cursor_before, foreground)
        returned = _stable_observation(
            detector=vision.main_shop_icon,
            label="returned main shop entry",
            timeout_ms=config.timing.entry_timeout_ms,
            poll_interval_ms=config.timing.poll_interval_ms,
            stable_frames=config.timing.stable_frames,
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            foreground=foreground,
        )
    finally:
        capture.close()
    cursor_after = tuple(int(value) for value in win32api.GetCursorPos())
    criteria = {
        "exactly_two_navigation_clicks": events_sent == 2,
        "returned_to_main_screen": returned is not None,
        "foreground_unchanged": int(win32gui.GetForegroundWindow()) == foreground,
        "cursor_unchanged": cursor_after == cursor_before,
    }
    passed = all(criteria.values())
    return (
        {
            "status": "ok" if passed else "failed",
            "mode": "navigation",
            "capture": {
                "backend": args.capture_backend,
                "startup_ms": startup_ms,
            },
            "input": {
                "backend": "window_messages",
                "delivery": "post",
                "events": events_sent,
                "actions": ["open_shop", "exit_shop"],
            },
            "foreground_hwnd": foreground,
            "cursor_before": list(cursor_before),
            "cursor_after": list(cursor_after),
            "criteria": criteria,
            "screenshots_persisted": False,
        },
        0 if passed else 1,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate non-minimized background capture, a no-purchase background "
            "scroll, or safe entry/exit navigation while another window remains foreground."
        )
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in ("capture", "scroll", "navigation"):
        sub = subparsers.add_parser(mode)
        if mode == "navigation":
            sub.add_argument(
                "--acknowledge-main-screen-covered",
                action="store_true",
                required=True,
                help=(
                    "Confirm the game is on the main screen, restored at the reference "
                    "client size, and another window fully covers it."
                ),
            )
        else:
            sub.add_argument(
                "--acknowledge-shop-top-covered",
                action="store_true",
                required=True,
                help=(
                    "Confirm the shop inventory is at the top, the game is restored at the "
                    "reference client size, and another window fully covers it."
                ),
            )
        sub.add_argument("--sample-count", type=int, default=30 if mode == "capture" else 3)
        sub.add_argument("--interval-ms", type=int, default=0 if mode == "capture" else 100)
        if mode == "scroll":
            sub.add_argument(
                "--effect-observation-ms",
                type=int,
                default=800,
                help="Observe captured scroll effect for 800-30000 ms after the last wheel message without extra input.",
            )
        sub.add_argument(
            "--capture-backend",
            choices=("wgc",),
            default="wgc",
            help="Use the production WGC capture backend.",
        )
        sub.add_argument(
            "--result-path",
            type=Path,
            default=None,
        )
    args = parser.parse_args()
    if args.result_path is None:
        delivery_suffix = "" if args.mode == "capture" else "-post"
        observation_suffix = (
            f"-{args.effect_observation_ms}ms"
            if args.mode == "scroll" and args.effect_observation_ms != 800
            else ""
        )
        args.result_path = (
            ROOT
            / "logs"
            / (
                f"background-{args.mode}-{args.capture_backend}"
                f"{delivery_suffix}{observation_suffix}-validation.json"
            )
        )
    result: dict[str, object]
    try:
        if args.mode == "capture":
            result, exit_code = _run_capture(args)
        elif args.mode == "scroll":
            result, exit_code = _run_scroll(args)
        else:
            result, exit_code = _run_navigation(args)
    except Exception as exc:
        result = {
            "status": "error",
            "mode": args.mode,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "capture_backend": args.capture_backend,
            "message_delivery": None if args.mode == "capture" else "post",
            "input_events": 0 if args.mode == "capture" else None,
            "screenshots_persisted": False,
        }
        exit_code = 1
    _write_result(args.result_path, result)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
