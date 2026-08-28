from __future__ import annotations

import argparse
import ctypes
import json
import time
from pathlib import Path

from e7auto.config import Rect
from e7auto.platform_windows import (
    MssCaptureService,
    Win32WindowService,
    enable_per_monitor_dpi_awareness,
)
from e7auto.vision import OpenCvGameVision, TemplateRepository
if __package__:
    from scripts.validate_live_recognition import load_read_only_commissioning_config
else:
    from validate_live_recognition import (  # type: ignore[no-redef]
        load_read_only_commissioning_config,
    )


ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = ROOT / "logs" / "insufficient-funds-live-validation.json"


def _write_result(path: Path, result: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _rect_dict(rect: Rect) -> dict[str, int]:
    return {"x": rect.x, "y": rect.y, "width": rect.width, "height": rect.height}


def terminal_criteria(stable: int, required: int) -> dict[str, bool]:
    return {
        "initial_game_foreground": True,
        "geometry_unchanged": True,
        "stable_terminal_detection": stable >= required,
        "zero_input_events": True,
        "no_screenshots_persisted": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the terminal insufficient-gold prompt with capture only. "
            "The game must already be foreground; this script never focuses it or sends input."
        )
    )
    parser.add_argument("--acknowledge-prompt-visible", action="store_true", required=True)
    parser.add_argument("--sample-count", type=int, default=5)
    parser.add_argument("--interval-ms", type=int, default=100)
    parser.add_argument("--foreground-wait-seconds", type=float, default=30.0)
    parser.add_argument("--result-path", type=Path, default=RESULT_PATH)
    args = parser.parse_args()

    result: dict[str, object]
    try:
        if args.sample_count < 3:
            raise RuntimeError("sample-count must be at least 3")
        if args.interval_ms < 0:
            raise RuntimeError("interval-ms must be non-negative")
        if args.foreground_wait_seconds <= 0:
            raise RuntimeError("foreground-wait-seconds must be positive")
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise RuntimeError("validator must run as Windows administrator")

        config = load_read_only_commissioning_config()
        result_roi = config.rois["purchase_result"]
        vision = OpenCvGameVision(config, TemplateRepository(config))
        enable_per_monitor_dpi_awareness()
        windows = Win32WindowService()
        capture = MssCaptureService()
        window = windows.locate_unique(str(config.executable_path), config.window_title)

        wait_started = time.monotonic()
        wait_deadline = wait_started + args.foreground_wait_seconds
        while True:
            initial = windows.inspect(window)
            if (
                initial.exists
                and not initial.minimized
                and initial.foreground
                and initial.client_bounds.width == config.baseline_client_size.width
                and initial.client_bounds.height == config.baseline_client_size.height
            ):
                break
            if not initial.exists:
                raise RuntimeError("game window disappeared before validation")
            if time.monotonic() >= wait_deadline:
                raise RuntimeError(
                    "game was not observed foreground at the calibrated size before the "
                    f"validation deadline: {initial}"
                )
            time.sleep(0.1)
        foreground_wait_elapsed_ms = (time.monotonic() - wait_started) * 1000
        bounds = initial.client_bounds

        samples: list[dict[str, object]] = []
        stable = 0
        for index in range(args.sample_count):
            state = windows.inspect(window)
            if (
                not state.exists
                or state.minimized
                or not state.foreground
                or state.client_bounds != bounds
            ):
                raise RuntimeError(f"game window changed during validation: {state}")
            started = time.perf_counter()
            frame = capture.capture_client(window, bounds)
            observation = vision.match(
                frame,
                "insufficient_funds",
                result_roi,
                config.anchor_confidence,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            stable = stable + 1 if observation is not None else 0
            samples.append(
                {
                    "index": index + 1,
                    "detected": observation is not None,
                    "confidence": (
                        None if observation is None else float(observation.confidence)
                    ),
                    "elapsed_ms": elapsed_ms,
                }
            )
            if index + 1 < args.sample_count and args.interval_ms:
                time.sleep(args.interval_ms / 1000)

        criteria = terminal_criteria(stable, config.timing.stable_frames)
        result = {
            "status": "ok" if all(criteria.values()) else "criteria_not_met",
            "process": {"windows_admin": True},
            "window": {
                "title": window.title,
                "executable_path": window.executable_path,
                "client_bounds": _rect_dict(bounds),
                "foreground_wait_elapsed_ms": foreground_wait_elapsed_ms,
            },
            "recognition": {
                "template": "insufficient_funds",
                "roi": _rect_dict(result_roi),
                "threshold": config.anchor_confidence,
                "stable_frames_required": config.timing.stable_frames,
                "samples": samples,
            },
            "terminal_behavior": {
                "stop_reason": "purchase_funds_insufficient",
                "prompt_confirm_clicked": False,
            },
            "criteria": criteria,
            "criteria_all_met": all(criteria.values()),
            "game_input_sent": False,
            "screenshots_persisted": False,
        }
    except Exception as exc:
        result = {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "process": {
                "windows_admin": bool(ctypes.windll.shell32.IsUserAnAdmin())
            },
            "game_input_sent": False,
            "screenshots_persisted": False,
        }

    _write_result(args.result_path.resolve(), result)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
