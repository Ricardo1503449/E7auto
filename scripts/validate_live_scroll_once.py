from __future__ import annotations

import argparse
import ctypes
import json
import time
from pathlib import Path

import win32api
import win32con
import win32gui
import win32process

from e7auto.config import Point, Rect, Size
from e7auto.platform_windows import (
    MssCaptureService,
    Win32InputService,
    Win32WindowService,
    enable_per_monitor_dpi_awareness,
)
from e7auto.vision import measure_inventory_scroll


EXPECTED_EXECUTABLE = r"D:\Games\EpicSeven\EpicSeven.exe"
EXPECTED_TITLE = "第七史诗"
EXPECTED_CLIENT_SIZE = Size(2322, 1306)
CURSOR_POINT = Point(1500, 650)
INVENTORY_ROI = Rect(950, 110, 1320, 1180)


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
                attached = ctypes.windll.user32.AttachThreadInput(
                    current_thread, thread_id, True
                )
                if not attached:
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


def _write_result(path: Path | None, result: dict[str, object]) -> None:
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a guarded production-path wheel sequence and compare in-memory frames."
    )
    parser.add_argument("--acknowledge-real-input", action="store_true", required=True)
    parser.add_argument("--delta", type=int, default=-120)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--interval-ms", type=int, default=100)
    parser.add_argument("--settle-ms", type=int, default=800)
    parser.add_argument("--wait-for-foreground-ms", type=int, default=15000)
    parser.add_argument(
        "--focus-game",
        action="store_true",
        help="Bring the game to the foreground before the guarded validation.",
    )
    parser.add_argument(
        "--result-path",
        type=Path,
        help="Optional UTF-8 JSON result path; captured frames are never persisted.",
    )
    args = parser.parse_args()
    events_sent = 0
    try:
        if args.delta == 0:
            raise RuntimeError("delta must be non-zero")
        if args.repetitions <= 0:
            raise RuntimeError("repetitions must be positive")
        if args.interval_ms < 0:
            raise RuntimeError("interval-ms must be non-negative")
        if args.settle_ms <= 0:
            raise RuntimeError("settle-ms must be positive")
        if args.wait_for_foreground_ms <= 0:
            raise RuntimeError("wait-for-foreground-ms must be positive")

        enable_per_monitor_dpi_awareness()
        windows = Win32WindowService()
        capture = MssCaptureService()
        inputs = Win32InputService()
        window = windows.locate_unique(EXPECTED_EXECUTABLE, EXPECTED_TITLE)
        if args.focus_game:
            _focus_for_commissioning(window.hwnd)

        foreground_deadline = (
            time.monotonic() + args.wait_for_foreground_ms / 1000
        )
        before_state = windows.inspect(window)
        while time.monotonic() <= foreground_deadline:
            before_state = windows.inspect(window)
            if (
                before_state.exists
                and not before_state.minimized
                and before_state.foreground
            ):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("timed out waiting for the game to be foreground")
        if (
            before_state.client_bounds.width != EXPECTED_CLIENT_SIZE.width
            or before_state.client_bounds.height != EXPECTED_CLIENT_SIZE.height
        ):
            raise RuntimeError(f"game client size is not calibrated: {before_state}")

        before = capture.capture_client(window, before_state.client_bounds)
        screen_point = Point(
            before_state.client_bounds.x + CURSOR_POINT.x,
            before_state.client_bounds.y + CURSOR_POINT.y,
        )
        inputs.move(screen_point)
        actual_cursor = win32api.GetCursorPos()
        if actual_cursor != (screen_point.x, screen_point.y):
            raise RuntimeError(
                f"cursor verification failed: expected {(screen_point.x, screen_point.y)}, "
                f"found {actual_cursor}"
            )

        for event_index in range(args.repetitions):
            ready_state = windows.inspect(window)
            if (
                not ready_state.exists
                or ready_state.minimized
                or not ready_state.foreground
                or ready_state.client_bounds != before_state.client_bounds
            ):
                raise RuntimeError(
                    f"game window changed before input event {event_index + 1}: "
                    f"{ready_state}"
                )
            inputs.scroll(screen_point, args.delta)
            events_sent += 1
            if event_index + 1 < args.repetitions and args.interval_ms:
                time.sleep(args.interval_ms / 1000)
        time.sleep(args.settle_ms / 1000)

        after_state = windows.inspect(window)
        if (
            not after_state.exists
            or after_state.minimized
            or not after_state.foreground
            or after_state.client_bounds != before_state.client_bounds
        ):
            raise RuntimeError(f"game window changed after input: {after_state}")
        after = capture.capture_client(window, after_state.client_bounds)

        movement = measure_inventory_scroll(
            before,
            after,
            INVENTORY_ROI,
            difference_threshold=8,
        )
        result: dict[str, object] = {
            "status": "ok",
            "process": {
                "windows_admin": bool(ctypes.windll.shell32.IsUserAnAdmin()),
            },
            "window": {
                "hwnd": window.hwnd,
                "title": window.title,
                "executable_path": window.executable_path,
                "client_bounds": {
                    "x": before_state.client_bounds.x,
                    "y": before_state.client_bounds.y,
                    "width": before_state.client_bounds.width,
                    "height": before_state.client_bounds.height,
                },
            },
            "input": {
                "logical_cursor": {"x": CURSOR_POINT.x, "y": CURSOR_POINT.y},
                "screen_cursor": {"x": actual_cursor[0], "y": actual_cursor[1]},
                "delta_per_event": args.delta,
                "events": events_sent,
                "total_delta": args.delta * events_sent,
                "interval_ms": args.interval_ms,
                "settle_ms": args.settle_ms,
            },
            "inventory_difference": {
                "mean_absolute_difference": movement.mean_absolute_difference,
                "changed_pixel_fraction_over_8": movement.changed_fraction,
                "maximum_difference": movement.maximum_difference,
                "phase_shift_x": movement.phase_shift_x,
                "phase_shift_y": movement.phase_shift_y,
                "phase_response": movement.phase_response,
            },
            "screenshots_persisted": False,
        }
        _write_result(args.result_path, result)
        return 0
    except Exception as exc:
        failure: dict[str, object] = {
            "status": "error",
            "process": {
                "windows_admin": bool(ctypes.windll.shell32.IsUserAnAdmin()),
            },
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "events_sent": events_sent,
            "input_completed": events_sent == args.repetitions,
            "screenshots_persisted": False,
        }
        _write_result(args.result_path, failure)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
