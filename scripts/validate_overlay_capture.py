from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import yaml

from e7auto.app import validate_source_environment
from e7auto.config import Point, Rect, Size, TargetConfig
from e7auto.overlay import capture_is_safe
from e7auto.ports import WindowState
if __package__:
    from scripts.calibrate_overlay_position import (
        CONFIG_PATH,
        ROOT,
        load_position_calibration_config,
    )
else:
    from calibrate_overlay_position import (  # type: ignore[no-redef]
        CONFIG_PATH,
        ROOT,
        load_position_calibration_config,
    )


POSITION_MANIFEST_PATH = (
    ROOT / "assets" / "templates" / "overlay_position_calibration_manifest.yaml"
)
CHANGE_THRESHOLD = 8


@dataclass(frozen=True, slots=True)
class CaptureValidationConfig:
    executable_path: Path
    window_title: str
    baseline_client_size: Size
    targets: tuple[TargetConfig, ...]
    offset: Point
    expected_overlay_size: Size
    overlay_font_size_px: int
    recognition_rois: tuple[Rect, ...]
    recognition_roi_names: tuple[str, ...]
    missing_roi_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FrameDifference:
    mean_absolute_difference: float
    changed_fraction: float


@dataclass(frozen=True, slots=True)
class CaptureContentEvidence:
    background_drift: FrameDifference
    visible_effect: FrameDifference
    excluded_effect: FrameDifference
    positive_control_detected: bool
    visible_matches_hidden: bool
    excluded_matches_hidden: bool
    exclusion_observed: bool


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must be a mapping")
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"{name} must be an integer")
    return value


def _rect(value: object, name: str) -> Rect:
    raw = _mapping(value, name)
    rect = Rect(
        _integer(raw.get("x"), f"{name}.x"),
        _integer(raw.get("y"), f"{name}.y"),
        _integer(raw.get("width"), f"{name}.width"),
        _integer(raw.get("height"), f"{name}.height"),
    )
    if rect.width <= 0 or rect.height <= 0:
        raise RuntimeError(f"{name} must have positive dimensions")
    return rect


def load_capture_validation_config(
    config_path: Path = CONFIG_PATH,
    position_manifest_path: Path = POSITION_MANIFEST_PATH,
) -> CaptureValidationConfig:
    base = load_position_calibration_config(config_path)
    raw = _mapping(
        yaml.safe_load(config_path.resolve().read_text(encoding="utf-8")),
        "root",
    )
    manifest = _mapping(
        yaml.safe_load(position_manifest_path.resolve().read_text(encoding="utf-8")),
        "overlay position manifest",
    )
    if manifest.get("status") != "operator_confirmed":
        raise RuntimeError("overlay position must be operator confirmed")
    if manifest.get("widget") != "e7auto.ui.StatsOverlay":
        raise RuntimeError("overlay position evidence uses the wrong widget")
    if manifest.get("client_baseline") != {
        "width": base.baseline_client_size.width,
        "height": base.baseline_client_size.height,
    }:
        raise RuntimeError("overlay position evidence uses the wrong client baseline")

    offset_raw = _mapping(manifest.get("offset"), "overlay position offset")
    offset = Point(
        _integer(offset_raw.get("x"), "overlay position offset.x"),
        _integer(offset_raw.get("y"), "overlay position offset.y"),
    )
    configured_offset = _mapping(
        _mapping(raw.get("overlay"), "overlay").get("offset"),
        "overlay.offset",
    )
    if configured_offset != {"x": offset.x, "y": offset.y}:
        raise RuntimeError("configured overlay offset does not match position evidence")

    overlay_bounds = _mapping(
        manifest.get("observed_overlay_bounds"),
        "observed_overlay_bounds",
    )
    expected_overlay_size = Size(
        _integer(overlay_bounds.get("width"), "observed_overlay_bounds.width"),
        _integer(overlay_bounds.get("height"), "observed_overlay_bounds.height"),
    )
    font_size = _integer(
        manifest.get("overlay_font_size_px"),
        "overlay_font_size_px",
    )

    names: list[str] = []
    rois: list[Rect] = []
    missing: list[str] = []
    for roi_name, value in _mapping(raw.get("rois"), "rois").items():
        # Network-error modal ROIs are monitored by the automation emergency
        # handler and are intentionally excluded from the overlay calibration
        # evidence set (the modal itself covers the game surface).
        if roi_name in {"network_error", "network_retry"}:
            continue
        qualified = f"rois.{roi_name}"
        if value is None:
            missing.append(qualified)
            continue
        names.append(qualified)
        rois.append(_rect(value, qualified))

    slots = raw.get("slots")
    if not isinstance(slots, list):
        raise RuntimeError("slots must be a list")
    for index, value in enumerate(slots):
        slot = _mapping(value, f"slots[{index}]")
        slot_id = slot.get("id")
        if not isinstance(slot_id, str) or not slot_id:
            raise RuntimeError(f"slots[{index}].id must be a non-empty string")
        qualified = f"slots.{slot_id}.item_roi"
        names.append(qualified)
        rois.append(_rect(slot.get("item_roi"), qualified))

    return CaptureValidationConfig(
        executable_path=base.executable_path,
        window_title=base.window_title,
        baseline_client_size=base.baseline_client_size,
        targets=base.targets,
        offset=offset,
        expected_overlay_size=expected_overlay_size,
        overlay_font_size_px=font_size,
        recognition_rois=tuple(rois),
        recognition_roi_names=tuple(names),
        missing_roi_names=tuple(missing),
    )


def game_state_is_valid(
    state: WindowState,
    expected_size: Size,
    expected_bounds: Rect | None = None,
) -> bool:
    if (
        not state.exists
        or state.minimized
        or not state.foreground
        or (state.client_bounds.width, state.client_bounds.height)
        != (expected_size.width, expected_size.height)
    ):
        return False
    return expected_bounds is None or state.client_bounds == expected_bounds


def build_outside_client_mask(
    overlay_bounds: Rect,
    client_bounds: Rect,
) -> np.ndarray:
    screen_x = np.arange(overlay_bounds.x, overlay_bounds.right)
    screen_y = np.arange(overlay_bounds.y, overlay_bounds.bottom)
    inside_x = (screen_x >= client_bounds.x) & (screen_x < client_bounds.right)
    inside_y = (screen_y >= client_bounds.y) & (screen_y < client_bounds.bottom)
    return ~(inside_y[:, None] & inside_x[None, :])


def _difference(
    left: np.ndarray,
    right: np.ndarray,
    mask: np.ndarray,
) -> FrameDifference:
    if left.shape != right.shape or left.ndim != 3 or left.shape[2] < 3:
        raise ValueError("capture frames must have matching HxWx3-or-4 shapes")
    if mask.shape != left.shape[:2] or not np.any(mask):
        raise ValueError("capture mask must match the frame and contain pixels")
    delta = np.abs(
        left[:, :, :3].astype(np.int16) - right[:, :, :3].astype(np.int16)
    )
    selected = delta[mask]
    changed = np.max(selected, axis=1) > CHANGE_THRESHOLD
    return FrameDifference(
        mean_absolute_difference=float(np.mean(selected)),
        changed_fraction=float(np.mean(changed)),
    )


def _minimum(left: FrameDifference, right: FrameDifference) -> FrameDifference:
    return FrameDifference(
        min(left.mean_absolute_difference, right.mean_absolute_difference),
        min(left.changed_fraction, right.changed_fraction),
    )


def _maximum(left: FrameDifference, right: FrameDifference) -> FrameDifference:
    return FrameDifference(
        max(left.mean_absolute_difference, right.mean_absolute_difference),
        max(left.changed_fraction, right.changed_fraction),
    )


def evaluate_capture_content(
    hidden_before: np.ndarray,
    visible: np.ndarray,
    hidden_middle: np.ndarray,
    excluded: np.ndarray,
    hidden_after: np.ndarray,
    mask: np.ndarray,
) -> CaptureContentEvidence:
    background_drift = _maximum(
        _difference(hidden_before, hidden_middle, mask),
        _difference(hidden_middle, hidden_after, mask),
    )
    visible_effect = _minimum(
        _difference(visible, hidden_before, mask),
        _difference(visible, hidden_middle, mask),
    )
    excluded_effect = _minimum(
        _difference(excluded, hidden_middle, mask),
        _difference(excluded, hidden_after, mask),
    )

    positive_control_detected = (
        visible_effect.mean_absolute_difference
        > background_drift.mean_absolute_difference * 5.0 + 0.5
        and visible_effect.changed_fraction
        > background_drift.changed_fraction * 5.0 + 0.005
    )
    background_mad_limit = background_drift.mean_absolute_difference * 3.0 + 0.5
    background_fraction_limit = background_drift.changed_fraction * 3.0 + 0.005
    visible_matches_hidden = (
        visible_effect.mean_absolute_difference <= background_mad_limit
        and visible_effect.changed_fraction <= background_fraction_limit
    )
    excluded_matches_hidden = (
        excluded_effect.mean_absolute_difference <= background_mad_limit
        and excluded_effect.changed_fraction <= background_fraction_limit
    )
    exclusion_observed = (
        positive_control_detected
        and excluded_matches_hidden
    )
    return CaptureContentEvidence(
        background_drift=background_drift,
        visible_effect=visible_effect,
        excluded_effect=excluded_effect,
        positive_control_detected=positive_control_detected,
        visible_matches_hidden=visible_matches_hidden,
        excluded_matches_hidden=excluded_matches_hidden,
        exclusion_observed=exclusion_observed,
    )


def capture_path_excludes_overlay(
    evidence: CaptureContentEvidence,
    operator_confirmed_visible: bool,
) -> bool:
    return evidence.exclusion_observed or (
        operator_confirmed_visible
        and evidence.visible_matches_hidden
        and evidence.excluded_matches_hidden
    )


def _validated_result_path(value: str) -> Path:
    result = Path(value).resolve()
    if not result.is_relative_to(ROOT.resolve()):
        raise argparse.ArgumentTypeError("result path must stay inside the project")
    return result


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate production overlay capture exclusion"
    )
    parser.add_argument("--result", required=True, type=_validated_result_path)
    parser.add_argument(
        "--operator-confirmed-visible",
        action="store_true",
        help="record that the operator visibly observed the overlay flash",
    )
    parser.add_argument("--wait-for-foreground-ms", type=int, default=15000)
    return parser.parse_args(argv)


def _settle(application: object) -> None:
    application.processEvents()
    try:
        import ctypes

        ctypes.windll.dwmapi.DwmFlush()
    except (AttributeError, OSError):
        pass
    time.sleep(0.15)
    application.processEvents()


def run_live_validation(
    config: CaptureValidationConfig,
    operator_confirmed_visible: bool = False,
    wait_for_foreground_ms: int = 15000,
) -> dict[str, object]:
    import ctypes

    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from e7auto.platform_windows import (
        MssCaptureService,
        WDA_EXCLUDEFROMCAPTURE,
        WDA_NONE,
        Win32WindowService,
        dwm_composition_enabled,
        enable_per_monitor_dpi_awareness,
        get_window_display_affinity,
        set_window_display_affinity,
    )
    from e7auto.ui import StatsOverlay

    enable_per_monitor_dpi_awareness()
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("E7auto overlay capture validation")
    windows = Win32WindowService()
    game_window = windows.locate_unique(
        str(config.executable_path),
        config.window_title,
    )
    expected = config.baseline_client_size
    if wait_for_foreground_ms <= 0:
        raise RuntimeError("wait-for-foreground-ms must be positive")
    wait_started = time.monotonic()
    foreground_deadline = wait_started + wait_for_foreground_ms / 1000
    initial_state = windows.inspect(game_window)
    while (
        not game_state_is_valid(initial_state, expected)
        and time.monotonic() <= foreground_deadline
    ):
        time.sleep(0.1)
        initial_state = windows.inspect(game_window)
    if not game_state_is_valid(initial_state, expected):
        raise RuntimeError(
            "timed out waiting for a foreground game client at the exact baseline: "
            f"{initial_state}"
        )
    foreground_wait_elapsed_ms = (time.monotonic() - wait_started) * 1000
    initial_bounds = initial_state.client_bounds

    overlay = StatsOverlay()
    capture = MssCaptureService()
    transitions: list[dict[str, object]] = []
    foreground_checks = 1

    def ensure_unchanged(expected_overlay: Rect | None = None) -> None:
        nonlocal foreground_checks
        state = windows.inspect(game_window)
        if not game_state_is_valid(state, expected, initial_bounds):
            raise RuntimeError(
                "game client lost foreground, moved, resized, minimized, or closed"
            )
        foreground_checks += 1
        if expected_overlay is not None:
            geometry = overlay.frameGeometry()
            actual = Rect(
                geometry.x(), geometry.y(), geometry.width(), geometry.height()
            )
            if actual != expected_overlay:
                raise RuntimeError("overlay geometry changed during capture validation")

    def set_affinity(hwnd: int, requested: int) -> tuple[bool, int | None]:
        set_succeeded = set_window_display_affinity(hwnd, requested)
        _settle(application)
        readback = get_window_display_affinity(hwnd)
        transitions.append(
            {
                "requested": requested,
                "set_succeeded": set_succeeded,
                "readback": readback,
            }
        )
        return set_succeeded, readback

    try:
        report = overlay.begin_capture_validation(
            config.targets,
            QPoint(config.offset.x, config.offset.y),
            initial_bounds,
            config.recognition_rois,
        )
        _settle(application)
        geometry = overlay.frameGeometry()
        overlay_bounds = Rect(
            geometry.x(), geometry.y(), geometry.width(), geometry.height()
        )
        expected_overlay_bounds = Rect(
            initial_bounds.x + config.offset.x,
            initial_bounds.y + config.offset.y,
            config.expected_overlay_size.width,
            config.expected_overlay_size.height,
        )
        if overlay_bounds != expected_overlay_bounds:
            raise RuntimeError(
                f"live overlay geometry {overlay_bounds} does not match confirmed "
                f"geometry {expected_overlay_bounds}"
            )
        if config.overlay_font_size_px != StatsOverlay._FONT_SIZE_PX:
            raise RuntimeError("live overlay font does not match position evidence")
        ensure_unchanged(overlay_bounds)
        hwnd = int(overlay.winId())

        set_affinity(hwnd, WDA_NONE)
        overlay.hide()
        _settle(application)
        ensure_unchanged(overlay_bounds)
        hidden_before = capture.capture_client(game_window, overlay_bounds)

        overlay.show()
        overlay.raise_()
        set_affinity(hwnd, WDA_NONE)
        ensure_unchanged(overlay_bounds)
        visible = capture.capture_client(game_window, overlay_bounds)

        overlay.hide()
        _settle(application)
        ensure_unchanged(overlay_bounds)
        hidden_middle = capture.capture_client(game_window, overlay_bounds)

        overlay.show()
        overlay.raise_()
        excluded_set, excluded_readback = set_affinity(
            hwnd, WDA_EXCLUDEFROMCAPTURE
        )
        ensure_unchanged(overlay_bounds)
        excluded = capture.capture_client(game_window, overlay_bounds)

        overlay.hide()
        _settle(application)
        ensure_unchanged(overlay_bounds)
        hidden_after = capture.capture_client(game_window, overlay_bounds)

        overlay.show()
        overlay.raise_()
        final_set, final_readback = set_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        ensure_unchanged(overlay_bounds)

        mask = build_outside_client_mask(overlay_bounds, initial_bounds)
        content = evaluate_capture_content(
            hidden_before,
            visible,
            hidden_middle,
            excluded,
            hidden_after,
            mask,
        )
        fallback_safe = capture_is_safe(
            False,
            overlay_bounds,
            initial_bounds,
            config.recognition_rois,
        )
        windows_build = int(sys.getwindowsversion().build)
        criteria = {
            "windows_build_supports_exclude": windows_build >= 19041,
            "dwm_composition_enabled": dwm_composition_enabled(),
            "production_set_succeeded": report.set_succeeded,
            "production_affinity_readback_exact": (
                report.affinity_readback == WDA_EXCLUDEFROMCAPTURE
            ),
            "explicit_exclude_set_succeeded": excluded_set and final_set,
            "explicit_affinity_readback_exact": (
                excluded_readback == WDA_EXCLUDEFROMCAPTURE
                and final_readback == WDA_EXCLUDEFROMCAPTURE
            ),
            "visible_overlay_confirmed": (
                content.positive_control_detected or operator_confirmed_visible
            ),
            "overlay_absent_from_production_capture": capture_path_excludes_overlay(
                content,
                operator_confirmed_visible,
            ),
            "configured_fallback_rois_do_not_overlap": fallback_safe,
            "game_foreground_and_geometry_unchanged": True,
            "no_screenshots_persisted": True,
            "no_game_input_sent": True,
        }
        return {
            "status": "passed" if all(criteria.values()) else "failed",
            "client_bounds": asdict(initial_bounds),
            "overlay_bounds": asdict(overlay_bounds),
            "offset": asdict(config.offset),
            "overlay_font_size_px": config.overlay_font_size_px,
            "windows_build": windows_build,
            "dwm_composition_enabled": criteria["dwm_composition_enabled"],
            "production_security_report": asdict(report),
            "affinity_transitions": transitions,
            "outside_client_pixel_count": int(mask.sum()),
            "capture_content": asdict(content),
            "operator_confirmed_visible": operator_confirmed_visible,
            "initial_game_foreground": initial_state.foreground,
            "foreground_checks": foreground_checks,
            "foreground_wait_elapsed_ms": foreground_wait_elapsed_ms,
            "recognition_roi_names": list(config.recognition_roi_names),
            "missing_roi_names": list(config.missing_roi_names),
            "criteria": criteria,
            "criteria_all_met": all(criteria.values()),
        }
    finally:
        if int(overlay.winId()):
            try:
                set_window_display_affinity(int(overlay.winId()), WDA_NONE)
            except Exception:
                pass
        overlay.close()
        application.processEvents()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    validate_source_environment(ROOT)
    try:
        config = load_capture_validation_config()
        payload = run_live_validation(
            config,
            args.operator_confirmed_visible,
            args.wait_for_foreground_ms,
        )
        exit_code = 0 if payload.get("criteria_all_met") is True else 1
    except Exception as exc:
        payload = {"status": "error", "error": str(exc)}
        exit_code = 2
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
