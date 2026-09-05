from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml


USAGE_GUIDE_FILENAME = "\u4f7f\u7528\u8bf4\u660e.txt"
UI_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 1024)


def verify_ui_assets(ui_dir: Path) -> list[str]:
    problems: list[str] = []
    required_pngs = {
        f"e7auto-icon-{size}.png": (size, size) for size in UI_ICON_SIZES
    }
    required_pngs["shop-card-background.png"] = None
    for name, expected_size in required_pngs.items():
        path = ui_dir / name
        if not path.is_file():
            problems.append(f"missing UI asset: {name}")
            continue
        image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if image is None or image.size == 0:
            problems.append(f"invalid UI asset: {name}")
            continue
        if expected_size is not None and (image.shape[1], image.shape[0]) != expected_size:
            problems.append(f"UI icon has wrong dimensions: {name}")

    ico_path = ui_dir / "e7auto.ico"
    if not ico_path.is_file():
        problems.append("missing UI asset: e7auto.ico")
        return problems
    try:
        with ico_path.open("rb") as stream:
            reserved, icon_type, count = struct.unpack("<HHH", stream.read(6))
            sizes: set[int] = set()
            for _ in range(count):
                width, height, _colors, _reserved, _planes, _bits, _length, _offset = struct.unpack(
                    "<BBBBHHII", stream.read(16)
                )
                decoded_width = 256 if width == 0 else width
                decoded_height = 256 if height == 0 else height
                if decoded_width == decoded_height:
                    sizes.add(decoded_width)
        if reserved != 0 or icon_type != 1:
            problems.append("invalid UI icon header: e7auto.ico")
        if not set(UI_ICON_SIZES[:-1]).issubset(sizes):
            problems.append("UI icon is missing required Windows sizes")
    except (OSError, struct.error):
        problems.append("invalid UI asset: e7auto.ico")
    return problems


def verify_template_assets(template_dir: Path) -> list[str]:
    problems: list[str] = []
    crop_manifest_path = template_dir / "manifest.yaml"
    single_manifest_paths = (
        template_dir / "main_shop_icon_manifest.yaml",
        template_dir / "shop_refresh_button_manifest.yaml",
        template_dir / "shop_exit_icon_manifest.yaml",
    )
    multi_manifest_paths = (template_dir / "refresh_confirm_manifest.yaml",)
    sky_stone_manifest_path = template_dir / "sky_stone_manifest.yaml"
    sky_stone_zero_wide_manifest_path = (
        template_dir / "sky_stone_zero_wide_manifest.yaml"
    )
    insufficient_funds_manifest_path = template_dir / "insufficient_funds_manifest.yaml"
    insufficient_funds_live_manifest_path = (
        template_dir / "insufficient_funds_live_validation_manifest.yaml"
    )
    client_calibration_manifest_path = template_dir / "client_calibration_manifest.yaml"
    overlay_position_manifest_path = (
        template_dir / "overlay_position_calibration_manifest.yaml"
    )
    overlay_capture_manifest_path = (
        template_dir / "overlay_capture_validation_manifest.yaml"
    )
    for manifest_path in (
        crop_manifest_path,
        *single_manifest_paths,
        *multi_manifest_paths,
        sky_stone_manifest_path,
        sky_stone_zero_wide_manifest_path,
        insufficient_funds_manifest_path,
        insufficient_funds_live_manifest_path,
        client_calibration_manifest_path,
        overlay_position_manifest_path,
        overlay_capture_manifest_path,
    ):
        if not manifest_path.is_file():
            problems.append(f"missing template manifest: {manifest_path.name}")
    if problems:
        return problems

    try:
        crop_manifest = yaml.safe_load(crop_manifest_path.read_text(encoding="utf-8"))
        expected = [entry["output_path"] for entry in crop_manifest["templates"]]
        for manifest_path in single_manifest_paths:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            expected.append(manifest["output_path"])
        for manifest_path in multi_manifest_paths:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            expected.extend(entry["output_path"] for entry in manifest["templates"])
        sky_stone_manifest = yaml.safe_load(
            sky_stone_manifest_path.read_text(encoding="utf-8")
        )
        expected.append(sky_stone_manifest["icon"]["output_path"])
        expected.extend(
            entry["output_path"] for entry in sky_stone_manifest["digits"]
        )
        sky_stone_zero_wide_manifest = yaml.safe_load(
            sky_stone_zero_wide_manifest_path.read_text(encoding="utf-8")
        )
        wide_zero_template = sky_stone_zero_wide_manifest["template"]
        expected.append(wide_zero_template["output_path"])
        insufficient_funds_manifest = yaml.safe_load(
            insufficient_funds_manifest_path.read_text(encoding="utf-8")
        )
        insufficient_funds_live_manifest = yaml.safe_load(
            insufficient_funds_live_manifest_path.read_text(encoding="utf-8")
        )
        insufficient_template = insufficient_funds_manifest["template"]
        expected.append(insufficient_template["output_path"])
        client_calibration = yaml.safe_load(
            client_calibration_manifest_path.read_text(encoding="utf-8")
        )
        overlay_position = yaml.safe_load(
            overlay_position_manifest_path.read_text(encoding="utf-8")
        )
        overlay_capture = yaml.safe_load(
            overlay_capture_manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        return [f"invalid template manifest: {exc}"]

    if client_calibration.get("baseline_client_size") != {
        "width": 2322,
        "height": 1306,
    }:
        problems.append("client calibration manifest has the wrong baseline size")
    calibrated = client_calibration.get("calibrated", {})
    if set(calibrated.get("rois", {})) != {
        "main_shop_icon",
        "shop_refresh_button",
        "shop_exit_icon",
        "refresh_confirm_prompt",
        "refresh_confirm_button",
        "inventory_list",
        "confirm_item",
        "confirm_button",
        "purchase_result",
        "sky_stone_icon",
        "sky_stone_digits",
    }:
        problems.append("client calibration manifest has unexpected partial ROIs")
    if len(calibrated.get("slots", ())) != 6:
        problems.append("client calibration manifest must describe six inventory slots")
    if calibrated.get("scroll_delta") != -120:
        problems.append("client calibration manifest has the wrong scroll delta")
    if calibrated.get("scroll_repetitions") != 6:
        problems.append("client calibration manifest has the wrong scroll repetitions")
    if {
        "scroll_interval_ms": calibrated.get("scroll_interval_ms"),
        "scroll_settle_ms": calibrated.get("scroll_settle_ms"),
        "scroll_minimum_upward_shift_px": calibrated.get(
            "scroll_minimum_upward_shift_px"
        ),
        "scroll_difference_threshold": calibrated.get("scroll_difference_threshold"),
        "scroll_minimum_changed_fraction": calibrated.get(
            "scroll_minimum_changed_fraction"
        ),
    } != {
        "scroll_interval_ms": 100,
        "scroll_settle_ms": 800,
        "scroll_minimum_upward_shift_px": 300,
        "scroll_difference_threshold": 8,
        "scroll_minimum_changed_fraction": 0.30,
    }:
        problems.append("client calibration manifest has the wrong scroll safety gates")
    live_scroll = client_calibration.get("live_scroll_validation", {})
    if not live_scroll.get("windows_admin"):
        problems.append("client calibration manifest lacks elevated scroll evidence")
    if live_scroll.get("events") != 6 or live_scroll.get("delta_per_event") != -120:
        problems.append("client calibration manifest has the wrong live scroll sequence")
    if live_scroll.get("interval_ms") != 100 or live_scroll.get("settle_ms") != 800:
        problems.append("client calibration manifest has the wrong live scroll timing")
    inventory_difference = live_scroll.get("inventory_difference", {})
    if (
        inventory_difference.get("phase_shift_y", 0.0) >= -300
        or inventory_difference.get("changed_pixel_fraction_over_8", 0.0) <= 0.30
    ):
        problems.append("client calibration manifest lacks successful scroll movement")
    if live_scroll.get("screenshots_persisted") is not False:
        problems.append("client calibration manifest has invalid screenshot persistence evidence")
    live_recognition = client_calibration.get("live_recognition_validation", {})
    if not live_recognition.get("windows_admin"):
        problems.append("client calibration manifest lacks elevated recognition evidence")
    if live_recognition.get("configured_timing") != {
        "poll_interval_ms": 100,
        "scan_timeout_ms": 3000,
        "stable_frames": 3,
    }:
        problems.append("client calibration manifest has the wrong live scan timing")
    if live_recognition.get("criteria_all_met") is not True:
        problems.append("client calibration manifest lacks successful recognition criteria")
    if live_recognition.get("screenshots_persisted") is not False:
        problems.append("client recognition evidence has invalid screenshot persistence")
    if client_calibration.get("external_calibrations", {}).get(
        "overlay_position"
    ) != overlay_position_manifest_path.name:
        problems.append("client calibration manifest lacks overlay position evidence")
    if client_calibration.get("external_calibrations", {}).get(
        "overlay_capture"
    ) != overlay_capture_manifest_path.name:
        problems.append("client calibration manifest lacks overlay capture evidence")
    if client_calibration.get("external_calibrations", {}).get(
        "insufficient_funds"
    ) != insufficient_funds_manifest_path.name:
        problems.append("client calibration manifest lacks insufficient-funds evidence")
    if client_calibration.get("external_calibrations", {}).get(
        "insufficient_funds_live_recognition"
    ) != insufficient_funds_live_manifest_path.name:
        problems.append("client calibration manifest lacks insufficient-funds live evidence")

    if insufficient_funds_manifest.get("baseline_client_size") != {
        "width": 2322,
        "height": 1306,
    }:
        problems.append("insufficient-funds manifest has the wrong baseline size")
    if insufficient_funds_manifest.get("calibrated", {}).get(
        "purchase_result_roi"
    ) != {"x": 975, "y": 210, "width": 400, "height": 300}:
        problems.append("insufficient-funds manifest has the wrong result ROI")
    if insufficient_funds_manifest.get("positive_evidence", {}).get(
        "confidence", 0.0
    ) < 0.999:
        problems.append("insufficient-funds manifest lacks positive recognition evidence")
    if insufficient_funds_manifest.get("safety") != {
        "terminal_stop_reason": "purchase_funds_insufficient",
        "terminal_confirm_clicked": False,
        "full_client_crops_persisted": False,
    }:
        problems.append("insufficient-funds manifest has invalid terminal safety evidence")
    if (
        insufficient_funds_live_manifest.get("status")
        != "operator_confirmed_passed"
        or insufficient_funds_live_manifest.get("criteria_all_met") is not True
        or insufficient_funds_live_manifest.get("game_input_sent") is not False
        or insufficient_funds_live_manifest.get("screenshots_persisted") is not False
    ):
        problems.append("insufficient-funds live evidence is incomplete")

    if overlay_position.get("status") != "operator_confirmed":
        problems.append("overlay position was not operator confirmed")
    if overlay_position.get("client_baseline") != {
        "width": 2322,
        "height": 1306,
    }:
        problems.append("overlay position manifest has the wrong baseline size")
    client_bounds = overlay_position.get("observed_client_bounds", {})
    overlay_bounds = overlay_position.get("observed_overlay_bounds", {})
    offset = overlay_position.get("offset", {})
    expected_offset = {
        "x": overlay_bounds.get("x", 0) - client_bounds.get("x", 0),
        "y": overlay_bounds.get("y", 0) - client_bounds.get("y", 0),
    }
    if offset != expected_offset or offset != {"x": -252, "y": -145}:
        problems.append("overlay position manifest has inconsistent geometry")
    if overlay_position.get("overlay_font_size_px") != 18:
        problems.append("overlay position manifest has the wrong font size")
    if {
        "capture_exclusion_not_validated",
        "fallback_recognition_roi_overlap_not_validated",
        "no_game_input_was_sent",
    } != set(overlay_position.get("limitations", ())):
        problems.append("overlay position manifest has unexpected limitations")

    if (
        overlay_capture.get("status") != "operator_confirmed_passed"
        or overlay_capture.get("criteria_all_met") is not True
        or overlay_capture.get("screenshots_persisted") is not False
        or overlay_capture.get("game_input_sent") is not False
    ):
        problems.append("overlay capture evidence is incomplete")
    if (
        overlay_capture.get("client_bounds") != client_bounds
        or overlay_capture.get("overlay_bounds") != overlay_bounds
        or overlay_capture.get("offset") != offset
        or overlay_capture.get("overlay_font_size_px") != 18
    ):
        problems.append("overlay capture evidence has inconsistent geometry")
    if (
        overlay_capture.get("initial_game_foreground") is not True
        or overlay_capture.get("foreground_checks", 0) < 8
    ):
        problems.append("overlay capture evidence lacks foreground checks")
    if overlay_capture.get("production_affinity") != {
        "set_succeeded": True,
        "readback": 17,
        "capture_excluded": True,
    }:
        problems.append("overlay capture evidence lacks exact affinity readback")
    configured_fallback = overlay_capture.get("configured_fallback", {})
    if (
        configured_fallback.get("recognition_roi_count") != 17
        or configured_fallback.get("no_overlap") is not True
        or configured_fallback.get("missing_roi_names") != []
        or configured_fallback.get("post_phase32_reassessment")
        != {
            "source_config": "config/internal.yaml",
            "added_roi": "rois.purchase_result",
            "added_roi_geometry": {
                "x": 975,
                "y": 210,
                "width": 400,
                "height": 300,
            },
            "all_current_rois_no_overlap": True,
            "game_capture_required": False,
        }
        or configured_fallback.get("post_phase37_reassessment")
        != {
            "source_config": "config/internal.yaml",
            "added_roi": "rois.shop_exit_icon",
            "added_roi_geometry": {
                "x": 39,
                "y": 25,
                "width": 267,
                "height": 70,
            },
            "all_current_rois_no_overlap": True,
            "game_capture_required": False,
        }
    ):
        problems.append("overlay capture evidence has stale fallback geometry")
    capture_content = overlay_capture.get("capture_content", {})
    if (
        capture_content.get("operator_confirmed_visible") is not True
        or capture_content.get("production_capture_omits_visible_overlay") is not True
    ):
        problems.append("overlay capture evidence lacks the visible positive control")

    if len(expected) != 28 or len(set(expected)) != 28:
        problems.append("template manifests must describe exactly 28 unique PNG files")
    required = set(expected) | {
        "network_connection_abnormal.png",
        "network_retry.png",
    }
    actual = {path.name for path in template_dir.glob("*.png") if path.is_file()}
    for name in sorted(required - actual):
        problems.append(f"missing template asset: {name}")
    for name in sorted(actual - required):
        problems.append(f"unexpected template asset: {name}")
    for name in sorted(required & actual):
        path = template_dir / name
        loaded = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if (
            loaded is None
            or loaded.size == 0
            or loaded.ndim != 3
            or loaded.shape[2] not in (3, 4)
        ):
            problems.append(f"invalid template asset: {name}")
            continue
        if (
            loaded.shape[2] == 4
            and not np.all(loaded[:, :, 3] == 255)
            and not np.any(loaded[:, :, 3])
        ):
            problems.append(f"empty template alpha mask: {name}")
    return problems


def pe_machine(path: Path) -> int:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("not a PE executable")
        stream.seek(0x3C)
        pe_offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\0\0":
            raise ValueError("invalid PE signature")
        return struct.unpack("<H", stream.read(2))[0]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    release = root / "dist" / "launcher.dist"
    executable = release / "E7auto.exe"
    problems: list[str] = []
    if not executable.is_file():
        problems.append(f"missing executable: {executable}")
    else:
        if pe_machine(executable) != 0x8664:
            problems.append("executable is not PE32+ AMD64")
    if not (release / "config" / "internal.yaml").is_file():
        problems.append("missing internal configuration")
    if not (release / USAGE_GUIDE_FILENAME).is_file():
        problems.append(f"missing end-user usage guide: {USAGE_GUIDE_FILENAME}")
    template_dir = release / "assets" / "templates"
    if not template_dir.is_dir():
        problems.append("missing templates directory")
    else:
        problems.extend(verify_template_assets(template_dir))
    ui_dir = release / "assets" / "ui"
    if not ui_dir.is_dir():
        problems.append("missing UI assets directory")
    else:
        problems.extend(verify_ui_assets(ui_dir))
    if any(path.name == ".venv" for path in release.rglob(".venv")):
        problems.append(".venv was bundled")
    if (release / "logs").exists():
        problems.append("runtime logs were bundled")
    if not problems and (release / "config" / "internal.yaml").is_file():
        config_text = (release / "config" / "internal.yaml").read_text(encoding="utf-8")
        if "profile: compact" not in config_text:
            problems.append("release logging profile is not compact")
    self_check: dict[str, object] | None = None
    if not problems:
        completed = subprocess.run(
            [str(executable), "--self-check"],
            cwd=release,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            problems.append(f"compiled self-check failed: {completed.returncode} {completed.stderr.strip()}")
        elif completed.stdout.strip():
            self_check = json.loads(completed.stdout.strip().splitlines()[-1])
    print(json.dumps({"release": str(release), "problems": problems, "self_check": self_check}, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
