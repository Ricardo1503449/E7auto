from __future__ import annotations

from pathlib import Path

import yaml

from e7auto.config import Point, load_config
from scripts.calibrate_client_frames import SOURCE_SPECS, build_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "assets" / "templates" / "client_calibration_manifest.yaml"
OVERLAY_POSITION_MANIFEST_PATH = (
    ROOT / "assets" / "templates" / "overlay_position_calibration_manifest.yaml"
)
INSUFFICIENT_FUNDS_MANIFEST_PATH = (
    ROOT / "assets" / "templates" / "insufficient_funds_manifest.yaml"
)
INSUFFICIENT_FUNDS_LIVE_MANIFEST_PATH = (
    ROOT / "assets" / "templates" / "insufficient_funds_live_validation_manifest.yaml"
)
OVERLAY_CAPTURE_MANIFEST_PATH = (
    ROOT / "assets" / "templates" / "overlay_capture_validation_manifest.yaml"
)
MAIN_SHOP_LAYOUT_MANIFEST_PATH = (
    ROOT / "assets" / "templates" / "main_shop_layout_manifest.yaml"
)
CONFIG_PATH = ROOT / "config" / "internal.yaml"


def test_client_calibration_manifest_records_exact_in_memory_crops() -> None:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["baseline_client_size"] == {"width": 2322, "height": 1306}
    assert {
        role: source["client_crop"] for role, source in manifest["sources"].items()
    } == {
        "main": {"x": 49, "y": 108, "width": 2322, "height": 1306},
        "shop_top": {"x": 42, "y": 101, "width": 2322, "height": 1306},
        "shop_bottom": {"x": 49, "y": 111, "width": 2322, "height": 1306},
        "refresh_confirm": {"x": 31, "y": 90, "width": 2322, "height": 1306},
        "purchase_confirm": {"x": 32, "y": 125, "width": 2322, "height": 1306},
    }
    assert all(
        min(source["boundary_gradient_strength"].values()) >= 50
        for source in manifest["sources"].values()
    )
    assert manifest["matches"]["main_shop_icon"]["main"]["confidence"] >= 0.99
    assert (
        max(
            manifest["matches"]["main_shop_icon"][role]["confidence"]
            for role in ("shop_top_negative", "shop_bottom_negative")
        )
        < 0.93
    )
    assert manifest["matches"]["shop_exit_icon"]["shop_top"]["location"] == {
        "x": 39,
        "y": 25,
    }
    assert manifest["matches"]["shop_exit_icon"]["shop_bottom"]["location"] == {
        "x": 39,
        "y": 25,
    }
    assert manifest["calibrated"]["rois"]["shop_exit_icon"] == {
        "x": 39,
        "y": 25,
        "width": 267,
        "height": 70,
    }
    assert manifest["calibrated"]["rois"]["main_shop_icon"] == {
        "x": 25,
        "y": 525,
        "width": 165,
        "height": 175,
    }
    assert manifest["calibrated"]["points"]["shop_exit_button"] == {
        "x": 172,
        "y": 60,
    }
    assert manifest["calibrated"]["points"]["main_screen_wake"] == {
        "x": 1161,
        "y": 653,
    }
    assert manifest["sky_stone_balance"]["shop_top"]["value"] == 3840
    assert manifest["sky_stone_balance"]["shop_bottom"]["value"] == 3840
    assert manifest["target_slot_evidence"]["friendship_points_bottom_5"][
        "confidence"
    ] >= 0.99
    refresh_dialog = manifest["matches"]["refresh_confirm_dialog"]
    assert refresh_dialog["prompt"]["location"] == {"x": 949, "y": 564}
    assert refresh_dialog["button"]["location"] == {"x": 1176, "y": 779}
    assert min(refresh_dialog[key]["confidence"] for key in ("prompt", "button")) >= 0.99
    assert max(
        refresh_dialog[key]["confidence"]
        for key in ("purchase_prompt_negative", "purchase_button_negative")
    ) < 0.90

    purchase_dialog = manifest["matches"]["purchase_confirm_dialog"]
    assert purchase_dialog["friendship_points"]["location"] == {"x": 906, "y": 559}
    assert purchase_dialog["button"]["location"] == {"x": 1415, "y": 890}
    assert min(
        purchase_dialog[key]["confidence"]
        for key in ("friendship_points", "button")
    ) >= 0.99
    assert max(
        purchase_dialog[key]["confidence"]
        for key in (
            "covenant_bookmark_negative",
            "mystic_medal_negative",
            "refresh_identity_negative",
            "refresh_button_negative",
        )
    ) < 0.90

    live_scroll = manifest["live_scroll_validation"]
    assert live_scroll["windows_admin"] is True
    assert live_scroll["logical_cursor"] == {"x": 1500, "y": 650}
    assert live_scroll["delta_per_event"] == -120
    assert live_scroll["events"] == 6
    assert live_scroll["total_delta"] == -720
    assert live_scroll["interval_ms"] == 100
    assert live_scroll["settle_ms"] == 800
    assert live_scroll["inventory_difference"]["phase_shift_y"] < -300
    assert (
        live_scroll["inventory_difference"]["changed_pixel_fraction_over_8"]
        > 0.30
    )
    assert live_scroll["screenshots_persisted"] is False
    assert "scroll_delta_and_repetitions" not in manifest["unresolved"]

    live_recognition = manifest["live_recognition_validation"]
    assert live_recognition["windows_admin"] is True
    assert live_recognition["sample_count_per_viewport"] == 8
    assert live_recognition["configured_timing"] == {
        "poll_interval_ms": 100,
        "scan_timeout_ms": 3000,
        "stable_frames": 3,
    }
    assert live_recognition["input"] == {
        "clicks": 0,
        "refreshes": 0,
        "delta_per_event": -120,
        "events": 6,
        "interval_ms": 100,
        "settle_ms": 800,
    }
    assert live_recognition["top"]["sky_stone_value"] == 4625
    assert live_recognition["bottom"]["sky_stone_value"] == 4625
    assert live_recognition["top"]["time_to_three_frame_stability_ms"] < 3000
    assert live_recognition["bottom"]["time_to_three_frame_stability_ms"] < 3000
    assert live_recognition["top"]["refresh_confidence_minimum"] > 0.99
    assert live_recognition["bottom"]["refresh_confidence_minimum"] > 0.99
    assert live_recognition["top"]["sky_stone_confidence_minimum"] >= 0.80
    assert live_recognition["bottom"]["sky_stone_confidence_minimum"] >= 0.80
    assert live_recognition["criteria_all_met"] is True
    assert live_recognition["target_positive_evidence_observed"] is False
    assert live_recognition["screenshots_persisted"] is False
    assert manifest["external_calibrations"]["overlay_position"] == (
        OVERLAY_POSITION_MANIFEST_PATH.name
    )
    assert manifest["external_calibrations"]["insufficient_funds"] == (
        INSUFFICIENT_FUNDS_MANIFEST_PATH.name
    )
    assert manifest["external_calibrations"]["insufficient_funds_live_recognition"] == (
        INSUFFICIENT_FUNDS_LIVE_MANIFEST_PATH.name
    )
    assert manifest["external_calibrations"]["overlay_capture"] == (
        OVERLAY_CAPTURE_MANIFEST_PATH.name
    )
    assert manifest["external_calibrations"]["main_shop_activity_layout"] == (
        MAIN_SHOP_LAYOUT_MANIFEST_PATH.name
    )
    assert "overlay_offset" not in manifest["unresolved"]
    assert manifest["unresolved"] == []

    if all(Path(spec["path"]).is_file() for spec in SOURCE_SPECS.values()):
        assert build_manifest() == manifest


def test_activity_shifted_main_shop_layout_expands_only_the_vertical_search() -> None:
    manifest = yaml.safe_load(
        MAIN_SHOP_LAYOUT_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    assert manifest["status"] == "operator_confirmed_passed"
    assert manifest["baseline_client_size"] == {"width": 2322, "height": 1306}
    assert manifest["source"]["client_crop"] == {
        "x": 52,
        "y": 103,
        "width": 2322,
        "height": 1306,
    }
    assert manifest["production_before"]["confidence"] < 0.93
    assert manifest["expanded_search"]["roi"] == {
        "x": 25,
        "y": 525,
        "width": 165,
        "height": 175,
    }
    assert manifest["expanded_search"]["confidence"] >= 0.99
    assert manifest["expanded_search"]["center"] == {"x": 102, "y": 623}
    assert max(
        control["confidence"]
        for control in manifest["shop_negative_controls"].values()
    ) < 0.93
    assert manifest["click"]["use_recognized_anchor"] is True
    assert manifest["criteria_all_met"] is True
    assert manifest["game_input_sent"] is False
    assert manifest["screenshots_persisted"] is False


def test_internal_config_contains_only_evidence_supported_partial_calibration() -> None:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    calibrated = manifest["calibrated"]
    for key, value in calibrated["rois"].items():
        assert config["rois"][key] == value
    for key, value in calibrated["points"].items():
        assert config["points"][key] == value
    assert config["slots"] == calibrated["slots"]
    assert config["scroll"]["cursor_point"] == calibrated["scroll_cursor_point"]
    assert config["scroll"]["delta"] == calibrated["scroll_delta"] == -120
    assert config["scroll"]["repetitions"] == calibrated["scroll_repetitions"] == 6
    assert config["scroll"]["interval_ms"] == calibrated["scroll_interval_ms"] == 100
    assert config["scroll"]["settle_ms"] == calibrated["scroll_settle_ms"] == 800
    assert (
        config["scroll"]["minimum_upward_shift_px"]
        == calibrated["scroll_minimum_upward_shift_px"]
        == 300
    )
    assert (
        config["scroll"]["difference_threshold"]
        == calibrated["scroll_difference_threshold"]
        == 8
    )
    assert (
        config["scroll"]["minimum_changed_fraction"]
        == calibrated["scroll_minimum_changed_fraction"]
        == 0.30
    )
    assert config["vision"]["anchor_confidence"] == calibrated["anchor_confidence"]
    assert (
        config["vision"]["sky_stone_digit_confidence"]
        == calibrated["sky_stone_digit_confidence"]
    )
    assert (
        config["vision"]["sky_stone_digit_margin"]
        == calibrated["sky_stone_digit_margin"]
    )
    assert (
        config["vision"]["sky_stone_digits_offset"]
        == calibrated["sky_stone_digits_offset"]
        == {"x": 57, "y": 16}
    )

    assert config["calibration_complete"] is True
    assert "top_anchor" not in config["templates"]
    assert "bottom_anchor" not in config["templates"]
    assert "top_anchor" not in config["rois"]
    assert "bottom_anchor" not in config["rois"]
    assert config["templates"]["insufficient_funds"] == (
        "../assets/templates/insufficient_funds.png"
    )
    insufficient_manifest = yaml.safe_load(
        INSUFFICIENT_FUNDS_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    assert config["rois"]["purchase_result"] == insufficient_manifest["calibrated"][
        "purchase_result_roi"
    ]
    position_manifest = yaml.safe_load(
        OVERLAY_POSITION_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    assert config["overlay"]["offset"] == position_manifest["offset"] == {
        "x": -252,
        "y": -145,
    }

    loaded = load_config(CONFIG_PATH)
    assert loaded.rois["purchase_result"].x == 975
    assert loaded.template_paths["insufficient_funds"].is_file()
    assert loaded.scroll.interval_ms == 100
    assert loaded.scroll.settle_ms == 800
    assert loaded.scroll.minimum_settle_ms == 100
    assert loaded.scroll.settle_poll_interval_ms == 100
    assert loaded.scroll.stable_observations == 2
    assert loaded.scroll.maximum_pairwise_shift_px == 1.0
    assert loaded.scroll.minimum_phase_response == 0.80
    assert loaded.scroll.downsample_factor == 4
    assert loaded.scroll.minimum_upward_shift_px == 300
    assert loaded.scroll.difference_threshold == 8
    assert loaded.scroll.minimum_changed_fraction == 0.30
    assert loaded.sky_stone_digits_offset == Point(57, 16)


def test_operator_confirmed_overlay_position_geometry_is_exact() -> None:
    manifest = yaml.safe_load(
        OVERLAY_POSITION_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    client = manifest["observed_client_bounds"]
    overlay = manifest["observed_overlay_bounds"]
    offset = manifest["offset"]

    assert manifest["status"] == "operator_confirmed"
    assert manifest["client_baseline"] == {"width": 2322, "height": 1306}
    assert offset == {
        "x": overlay["x"] - client["x"],
        "y": overlay["y"] - client["y"],
    } == {"x": -252, "y": -145}
    assert (overlay["width"], overlay["height"]) == (320, 159)
    assert manifest["widget"] == "e7auto.ui.StatsOverlay"
    assert manifest["overlay_font_size_px"] == 18
    assert "no_game_input_was_sent" in manifest["limitations"]
    assert "capture_exclusion_not_validated" in manifest["limitations"]
