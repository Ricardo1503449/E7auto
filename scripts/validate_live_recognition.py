from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import time
from pathlib import Path
from typing import Any, Callable, Hashable, Sequence, TypeVar

import numpy as np
import win32api
import win32con
import win32gui
import win32process
import yaml

from e7auto.config import (
    AppConfig,
    LoggingConfig,
    Point,
    Rect,
    RefreshStrategyConfig,
    ScrollConfig,
    Size,
    SlotConfig,
    TargetConfig,
    TimingConfig,
)
from e7auto.platform_windows import (
    MssCaptureService,
    Win32InputService,
    Win32WindowService,
    enable_per_monitor_dpi_awareness,
)
from e7auto.vision import OpenCvGameVision, TemplateRepository, measure_inventory_scroll


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "internal.yaml"
RESULT_PATH = ROOT / "logs" / "live-recognition-timing-admin.json"
INVENTORY_ROI = Rect(950, 110, 1320, 1180)
T = TypeVar("T", bound=Hashable)


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must be a mapping")
    return value


def _point(value: object, name: str) -> Point:
    raw = _mapping(value, name)
    x, y = raw.get("x"), raw.get("y")
    if not isinstance(x, int) or isinstance(x, bool):
        raise RuntimeError(f"{name}.x must be an integer")
    if not isinstance(y, int) or isinstance(y, bool):
        raise RuntimeError(f"{name}.y must be an integer")
    return Point(x, y)


def _rect(value: object, name: str) -> Rect:
    raw = _mapping(value, name)
    values = tuple(raw.get(key) for key in ("x", "y", "width", "height"))
    if any(not isinstance(item, int) or isinstance(item, bool) for item in values):
        raise RuntimeError(f"{name} must contain integer x/y/width/height")
    x, y, width, height = values
    if width <= 0 or height <= 0:
        raise RuntimeError(f"{name} must have positive width and height")
    return Rect(x, y, width, height)


def load_read_only_commissioning_config(path: Path = CONFIG_PATH) -> AppConfig:
    """Load only calibrated fields needed by this non-clicking validator.

    Production continues to use e7auto.config.load_config and remains fail-closed.
    """

    source_path = path.resolve()
    raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    root = _mapping(raw, "root")
    if root.get("schema_version") != 1:
        raise RuntimeError("schema_version must be 1")

    game = _mapping(root.get("game"), "game")
    executable_path = Path(str(game.get("executable_path", "")))
    window_title = game.get("window_title")
    size = _mapping(game.get("baseline_client_size"), "game.baseline_client_size")
    baseline_size = Size(int(size.get("width", 0)), int(size.get("height", 0)))
    if (
        executable_path.suffix.casefold() != ".exe"
        or (not executable_path.is_absolute() and executable_path.name != str(executable_path))
    ):
        raise RuntimeError(
            "commissioning executable must be an absolute .exe path or an .exe filename"
        )
    if not isinstance(window_title, str) or not window_title:
        raise RuntimeError("commissioning window title is required")
    if baseline_size != Size(2322, 1306):
        raise RuntimeError(f"unexpected commissioning baseline: {baseline_size}")

    templates_raw = _mapping(root.get("templates"), "templates")
    template_paths = {
        key: (source_path.parent / value).resolve()
        for key, value in templates_raw.items()
        if isinstance(value, str) and value
    }
    if any(not template.is_file() for template in template_paths.values()):
        missing = sorted(str(item) for item in template_paths.values() if not item.is_file())
        raise RuntimeError(f"commissioning templates are missing: {missing}")

    rois_raw = _mapping(root.get("rois"), "rois")
    rois = {
        key: _rect(value, f"rois.{key}")
        for key, value in rois_raw.items()
        if value is not None
    }
    points_raw = _mapping(root.get("points"), "points")
    points = {
        key: _point(value, f"points.{key}")
        for key, value in points_raw.items()
        if value is not None
    }

    targets: list[TargetConfig] = []
    for index, value in enumerate(root.get("targets", ())):
        target = _mapping(value, f"targets[{index}]")
        targets.append(
            TargetConfig(
                str(target["id"]),
                str(target["display_name"]),
                str(target["template"]),
                str(target["confirm_template"]),
                str(target["purchased_template"]),
                bool(target.get("user_selectable", False)),
            )
        )

    slots: list[SlotConfig] = []
    for index, value in enumerate(root.get("slots", ())):
        slot = _mapping(value, f"slots[{index}]")
        slots.append(
            SlotConfig(
                str(slot["id"]),
                str(slot["screen"]),
                int(slot["order"]),
                _rect(slot["item_roi"], f"slots[{index}].item_roi"),
                _point(slot["buy_point"], f"slots[{index}].buy_point"),
            )
        )

    scroll_raw = _mapping(root.get("scroll"), "scroll")
    scroll = ScrollConfig(
        _point(scroll_raw.get("cursor_point"), "scroll.cursor_point"),
        int(scroll_raw.get("delta", 0)),
        int(scroll_raw.get("repetitions", 0)),
        int(scroll_raw.get("interval_ms", 0)),
        int(scroll_raw.get("settle_ms", 0)),
        int(scroll_raw.get("minimum_settle_ms", 0)),
        int(scroll_raw.get("settle_poll_interval_ms", 0)),
        int(scroll_raw.get("stable_observations", 0)),
        float(scroll_raw.get("maximum_pairwise_shift_px", 0.0)),
        float(scroll_raw.get("minimum_phase_response", 0.0)),
        int(scroll_raw.get("downsample_factor", 0)),
        int(scroll_raw.get("minimum_upward_shift_px", 0)),
        int(scroll_raw.get("difference_threshold", 0)),
        float(scroll_raw.get("minimum_changed_fraction", 0.0)),
    )
    if scroll != ScrollConfig(
        Point(1500, 650),
        -120,
        6,
        100,
        800,
        100,
        100,
        2,
        1.0,
        0.80,
        4,
        300,
        8,
        0.30,
    ):
        raise RuntimeError(f"unexpected commissioning scroll config: {scroll}")

    timing_raw = _mapping(root.get("timing"), "timing")
    timing = TimingConfig(
        *(int(timing_raw[name]) for name in (
            "poll_interval_ms",
            "entry_timeout_ms",
            "scan_timeout_ms",
            "dialog_timeout_ms",
            "purchase_result_timeout_ms",
            "refresh_timeout_ms",
            "stable_frames",
        ))
    )
    vision = _mapping(root.get("vision"), "vision")
    logging = _mapping(root.get("logging"), "logging")
    economy = _mapping(root.get("economy"), "economy")

    required_templates = {
        "shop_refresh_button",
        "sky_stone_icon",
        *(f"sky_stone_digit_{digit}" for digit in range(10)),
        *(target.template for target in targets),
    }
    required_rois = {
        "shop_refresh_button",
        "sky_stone_icon",
        "sky_stone_digits",
        "inventory_list",
    }
    if not required_templates <= set(template_paths):
        raise RuntimeError("read-only commissioning templates are incomplete")
    if not required_rois <= set(rois):
        raise RuntimeError("read-only commissioning ROIs are incomplete")
    if len(slots) != 6 or {slot.screen for slot in slots} != {"top", "bottom"}:
        raise RuntimeError("read-only commissioning requires all six inventory slots")

    return AppConfig(
        source_path=source_path,
        executable_path=executable_path,
        process_name=executable_path.name,
        window_title=window_title,
        baseline_client_size=baseline_size,
        refresh_cost=int(economy["refresh_cost"]),
        template_paths=template_paths,
        rois=rois,
        points=points,
        targets=tuple(targets),
        slots=tuple(sorted(slots, key=lambda item: item.order)),
        scroll=scroll,
        timing=timing,
        refresh_strategy=RefreshStrategyConfig((13, 13, 13, 10), (3, 180, 5)),
        default_confidence=float(vision["default_confidence"]),
        anchor_confidence=float(vision["anchor_confidence"]),
        sky_stone_digit_confidence=float(vision["sky_stone_digit_confidence"]),
        sky_stone_digits_offset=_point(
            vision.get("sky_stone_digits_offset"),
            "vision.sky_stone_digits_offset",
        ),
        overlay_offset=Point(0, 0),
        logging=LoggingConfig(int(logging["keep_days"]), int(logging["keep_files"])),
    )


def first_stable_run(
    values: Sequence[T],
    required: int,
    is_valid: Callable[[T], bool] | None = None,
) -> tuple[int, T] | None:
    if required <= 0:
        raise ValueError("required must be positive")
    valid = is_valid or (lambda value: True)
    previous: T | None = None
    stable = 0
    for index, value in enumerate(values):
        if not valid(value):
            previous = None
            stable = 0
            continue
        if previous is not None and value == previous:
            stable += 1
        else:
            previous = value
            stable = 1
        if stable >= required:
            return index, value
    return None


def _focus_for_commissioning(hwnd: int) -> None:
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError("game window no longer exists")
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    current_thread = win32api.GetCurrentThreadId()
    foreground = win32gui.GetForegroundWindow()
    foreground_thread = (
        win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    )
    target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
    attached_threads: list[int] = []
    try:
        for thread_id in {foreground_thread, target_thread}:
            if thread_id and thread_id != current_thread:
                if not ctypes.windll.user32.AttachThreadInput(
                    current_thread, thread_id, True
                ):
                    raise RuntimeError(
                        f"AttachThreadInput failed for thread {thread_id}"
                    )
                attached_threads.append(thread_id)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    finally:
        for thread_id in reversed(attached_threads):
            ctypes.windll.user32.AttachThreadInput(current_thread, thread_id, False)

    deadline = time.monotonic() + 2.0
    while time.monotonic() <= deadline:
        if win32gui.GetForegroundWindow() == hwnd:
            return
        time.sleep(0.05)
    raise RuntimeError("game window could not be focused within 2 seconds")


def _check_window(windows, window, expected_bounds: Rect) -> None:
    state = windows.inspect(window)
    if (
        not state.exists
        or state.minimized
        or not state.foreground
        or state.client_bounds != expected_bounds
    ):
        raise RuntimeError(f"game window changed during commissioning: {state}")


def _observation_dict(observation) -> dict[str, object]:
    if observation is None:
        return {"detected": False}
    return {
        "detected": True,
        "confidence": float(observation.confidence),
    }


def _sample_viewport(
    *,
    screen: str,
    sample_count: int,
    interval_ms: int,
    windows,
    capture,
    window,
    bounds: Rect,
    vision: OpenCvGameVision,
) -> tuple[list[dict[str, object]], np.ndarray, np.ndarray]:
    started = time.perf_counter()
    samples: list[dict[str, object]] = []
    first_frame: np.ndarray | None = None
    last_frame: np.ndarray | None = None
    for index in range(sample_count):
        _check_window(windows, window, bounds)
        capture_started = time.perf_counter()
        frame = capture.capture_client(window, bounds)
        capture_ms = (time.perf_counter() - capture_started) * 1000
        if first_frame is None:
            first_frame = frame
        last_frame = frame

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
    assert first_frame is not None and last_frame is not None
    return samples, first_frame, last_frame


def summarize_samples(
    samples: Sequence[dict[str, object]], stable_frames: int
) -> dict[str, object]:
    refresh_values = tuple(bool(sample["refresh"]["detected"]) for sample in samples)  # type: ignore[index]
    balance_values = tuple(
        sample["sky_stone"].get("value") if sample["sky_stone"]["detected"] else None  # type: ignore[index,union-attr]
        for sample in samples
    )
    inventory_values = tuple(
        tuple((match["slot_id"], match["target_id"]) for match in sample["matches"])  # type: ignore[index]
        for sample in samples
    )

    refresh_stable = first_stable_run(
        refresh_values, stable_frames, lambda value: value is True
    )
    balance_stable = first_stable_run(
        balance_values, stable_frames, lambda value: value is not None
    )
    inventory_stable = first_stable_run(inventory_values, stable_frames)

    def stability_result(result) -> dict[str, object]:
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
            "minimum_ms": min(float(sample["timing_ms"][key]) for sample in samples),  # type: ignore[index]
            "mean_ms": statistics.fmean(
                float(sample["timing_ms"][key]) for sample in samples  # type: ignore[index]
            ),
            "maximum_ms": max(float(sample["timing_ms"][key]) for sample in samples),  # type: ignore[index]
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


def _write_result(path: Path, result: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run guarded no-click live recognition and timing commissioning."
    )
    parser.add_argument("--acknowledge-top-state", action="store_true", required=True)
    parser.add_argument("--sample-count", type=int, default=8)
    parser.add_argument("--interval-ms", type=int, default=100)
    parser.add_argument("--scroll-interval-ms", type=int, default=100)
    parser.add_argument("--settle-ms", type=int, default=800)
    parser.add_argument("--result-path", type=Path, default=RESULT_PATH)
    args = parser.parse_args()
    events_sent = 0
    try:
        if args.sample_count < 3:
            raise RuntimeError("sample-count must be at least 3")
        if min(args.interval_ms, args.scroll_interval_ms, args.settle_ms) < 0:
            raise RuntimeError("timing arguments must be non-negative")
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise RuntimeError("commissioning validator must run as Windows administrator")

        config = load_read_only_commissioning_config()
        if args.scroll_interval_ms != config.scroll.interval_ms:
            raise RuntimeError(
                "scroll-interval-ms must match the calibrated production configuration"
            )
        if args.settle_ms != config.scroll.settle_ms:
            raise RuntimeError("settle-ms must match the calibrated production configuration")
        templates = TemplateRepository(config)
        vision = OpenCvGameVision(config, templates)
        enable_per_monitor_dpi_awareness()
        windows = Win32WindowService()
        capture = MssCaptureService()
        inputs = Win32InputService()
        window = windows.locate_unique(
            str(config.executable_path), config.window_title
        )
        _focus_for_commissioning(window.hwnd)
        state = windows.inspect(window)
        bounds = state.client_bounds
        if (
            not state.exists
            or state.minimized
            or not state.foreground
            or bounds.width != config.baseline_client_size.width
            or bounds.height != config.baseline_client_size.height
        ):
            raise RuntimeError(f"game client is not in the calibrated state: {state}")

        top_samples, _, top_last = _sample_viewport(
            screen="top",
            sample_count=args.sample_count,
            interval_ms=args.interval_ms,
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            vision=vision,
        )

        cursor = Point(
            bounds.x + config.scroll.cursor_point.x,
            bounds.y + config.scroll.cursor_point.y,
        )
        inputs.move(cursor)
        if win32api.GetCursorPos() != (cursor.x, cursor.y):
            raise RuntimeError("cursor verification failed before downward navigation")
        for index in range(config.scroll.repetitions):
            _check_window(windows, window, bounds)
            inputs.scroll(cursor, config.scroll.delta)
            events_sent += 1
            if index + 1 < config.scroll.repetitions and args.scroll_interval_ms:
                time.sleep(args.scroll_interval_ms / 1000)
        if args.settle_ms:
            time.sleep(args.settle_ms / 1000)

        bottom_samples, bottom_first, _ = _sample_viewport(
            screen="bottom",
            sample_count=args.sample_count,
            interval_ms=args.interval_ms,
            windows=windows,
            capture=capture,
            window=window,
            bounds=bounds,
            vision=vision,
        )
        movement = measure_inventory_scroll(
            top_last,
            bottom_first,
            config.rois["inventory_list"],
            config.scroll.difference_threshold,
        )
        top = summarize_samples(top_samples, config.timing.stable_frames)
        bottom = summarize_samples(bottom_samples, config.timing.stable_frames)
        criteria = {
            "top_refresh_stable": bool(top["stability"]["refresh"]["achieved"]),  # type: ignore[index]
            "bottom_refresh_stable": bool(bottom["stability"]["refresh"]["achieved"]),  # type: ignore[index]
            "top_sky_stone_stable": bool(top["stability"]["sky_stone"]["achieved"]),  # type: ignore[index]
            "bottom_sky_stone_stable": bool(bottom["stability"]["sky_stone"]["achieved"]),  # type: ignore[index]
            "top_inventory_stable": bool(top["stability"]["inventory"]["achieved"]),  # type: ignore[index]
            "bottom_inventory_stable": bool(bottom["stability"]["inventory"]["achieved"]),  # type: ignore[index]
            "scroll_translation_detected": (
                movement.phase_shift_y < -config.scroll.minimum_upward_shift_px
                and movement.changed_fraction > config.scroll.minimum_changed_fraction
            ),
        }
        result: dict[str, object] = {
            "status": "ok" if all(criteria.values()) else "criteria_not_met",
            "process": {"windows_admin": True},
            "window": {
                "hwnd": window.hwnd,
                "title": window.title,
                "executable_path": window.executable_path,
                "client_bounds": {
                    "x": bounds.x,
                    "y": bounds.y,
                    "width": bounds.width,
                    "height": bounds.height,
                },
            },
            "configured_timing": {
                "poll_interval_ms": config.timing.poll_interval_ms,
                "scan_timeout_ms": config.timing.scan_timeout_ms,
                "stable_frames": config.timing.stable_frames,
            },
            "input": {
                "clicks": 0,
                "refreshes": 0,
                "delta_per_event": config.scroll.delta,
                "events": events_sent,
                "interval_ms": args.scroll_interval_ms,
                "settle_ms": args.settle_ms,
            },
            "top": top,
            "bottom": bottom,
            "scroll_difference": {
                "mean_absolute_difference": movement.mean_absolute_difference,
                "changed_pixel_fraction_over_8": movement.changed_fraction,
                "maximum_difference": movement.maximum_difference,
                "phase_shift_x": movement.phase_shift_x,
                "phase_shift_y": movement.phase_shift_y,
                "phase_response": movement.phase_response,
            },
            "criteria": criteria,
            "screenshots_persisted": False,
        }
        _write_result(args.result_path, result)
        return 0 if result["status"] == "ok" else 2
    except Exception as exc:
        _write_result(
            args.result_path,
            {
                "status": "error",
                "process": {
                    "windows_admin": bool(ctypes.windll.shell32.IsUserAnAdmin())
                },
                "error": {"type": type(exc).__name__, "message": str(exc)},
                "input": {"clicks": 0, "refreshes": 0, "events": events_sent},
                "screenshots_persisted": False,
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
