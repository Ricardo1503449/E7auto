from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import yaml

from e7auto.config import Rect, Size
from e7auto.overlay import capture_is_safe
from e7auto.ports import WindowState
from scripts.validate_overlay_capture import (
    build_outside_client_mask,
    capture_path_excludes_overlay,
    evaluate_capture_content,
    game_state_is_valid,
    load_capture_validation_config,
)


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_MANIFEST_PATH = (
    ROOT / "assets" / "templates" / "overlay_capture_validation_manifest.yaml"
)


def test_capture_validation_loader_is_tied_to_confirmed_overlay_evidence() -> None:
    config = load_capture_validation_config()

    assert (config.offset.x, config.offset.y) == (-252, -145)
    assert (config.expected_overlay_size.width, config.expected_overlay_size.height) == (
        320,
        159,
    )
    assert config.overlay_font_size_px == 18
    assert len(config.recognition_rois) == 17
    assert "rois.main_shop_icon" in config.recognition_roi_names
    assert "slots.top_1.item_roi" in config.recognition_roi_names
    assert config.missing_roi_names == ()
    assert capture_is_safe(
        False,
        Rect(327, 346, 320, 159),
        Rect(579, 491, 2322, 1306),
        config.recognition_rois,
    )


def test_live_capture_state_requires_foreground_and_unchanged_geometry() -> None:
    bounds = Rect(100, 200, 2322, 1306)
    ready = WindowState(True, False, True, bounds)

    assert game_state_is_valid(ready, Size(2322, 1306))
    assert game_state_is_valid(ready, Size(2322, 1306), bounds)
    assert not game_state_is_valid(
        WindowState(True, False, False, bounds),
        Size(2322, 1306),
    )
    assert not game_state_is_valid(
        WindowState(True, False, True, Rect(101, 200, 2322, 1306)),
        Size(2322, 1306),
        bounds,
    )


def test_outside_client_mask_excludes_the_small_client_overlap() -> None:
    overlay = Rect(100, 100, 10, 6)
    client = Rect(106, 104, 20, 20)

    mask = build_outside_client_mask(overlay, client)

    assert mask.shape == (6, 10)
    assert int(mask.sum()) == 52
    assert mask[0, 0]
    assert not mask[4, 6]


def test_capture_content_evidence_accepts_excluded_frame_matching_hidden_control() -> None:
    hidden = np.full((8, 10, 4), 100, dtype=np.uint8)
    visible = hidden.copy()
    visible[:, :, :3] = 35
    excluded = hidden.copy()
    mask = np.ones((8, 10), dtype=bool)

    evidence = evaluate_capture_content(
        hidden,
        visible,
        hidden,
        excluded,
        hidden,
        mask,
    )

    assert evidence.positive_control_detected
    assert evidence.exclusion_observed
    assert not evidence.visible_matches_hidden
    assert evidence.excluded_matches_hidden
    assert capture_path_excludes_overlay(evidence, False)
    assert evidence.visible_effect.mean_absolute_difference == 65.0
    assert evidence.excluded_effect.mean_absolute_difference == 0.0


def test_capture_content_evidence_rejects_missing_positive_control() -> None:
    hidden = np.full((8, 10, 4), 100, dtype=np.uint8)
    mask = np.ones((8, 10), dtype=bool)

    evidence = evaluate_capture_content(
        hidden,
        hidden,
        hidden,
        hidden,
        hidden,
        mask,
    )

    assert not evidence.positive_control_detected
    assert not evidence.exclusion_observed
    assert evidence.visible_matches_hidden
    assert evidence.excluded_matches_hidden
    assert not capture_path_excludes_overlay(evidence, False)
    assert capture_path_excludes_overlay(evidence, True)


def test_capture_content_evidence_rejects_overlay_still_present_when_excluded() -> None:
    hidden = np.full((8, 10, 4), 100, dtype=np.uint8)
    visible = hidden.copy()
    visible[:, :, :3] = 35
    mask = np.ones((8, 10), dtype=bool)

    evidence = evaluate_capture_content(
        hidden,
        visible,
        hidden,
        visible,
        hidden,
        mask,
    )

    assert evidence.positive_control_detected
    assert not evidence.exclusion_observed
    assert not evidence.excluded_matches_hidden
    assert not capture_path_excludes_overlay(evidence, True)


def test_operator_confirmed_capture_manifest_records_exact_live_evidence() -> None:
    manifest = yaml.safe_load(CAPTURE_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["status"] == "operator_confirmed_passed"
    assert manifest["client_bounds"] == {
        "x": 579,
        "y": 491,
        "width": 2322,
        "height": 1306,
    }
    assert manifest["overlay_bounds"] == {
        "x": 327,
        "y": 346,
        "width": 320,
        "height": 159,
    }
    assert manifest["offset"] == {"x": -252, "y": -145}
    assert manifest["overlay_font_size_px"] == 18
    assert manifest["windows_build"] == 22631
    assert manifest["dwm_composition_enabled"] is True
    assert manifest["initial_game_foreground"] is True
    assert manifest["foreground_checks"] >= 8
    assert manifest["production_affinity"] == {
        "set_succeeded": True,
        "readback": 17,
        "capture_excluded": True,
    }
    assert manifest["capture_content"]["operator_confirmed_visible"] is True
    assert manifest["capture_content"]["outside_client_pixel_count"] == 49_928
    assert manifest["capture_content"]["hidden_visible_excluded_mad"] == {
        "background_drift": 0.0,
        "visible_effect": 0.0,
        "excluded_effect": 0.0,
    }
    assert manifest["configured_fallback"]["recognition_roi_count"] == 17
    assert manifest["configured_fallback"]["no_overlap"] is True
    assert manifest["configured_fallback"]["missing_roi_names"] == []
    assert manifest["configured_fallback"]["post_phase32_reassessment"] == {
        "source_config": "config/internal.yaml",
        "added_roi": "rois.purchase_result",
        "added_roi_geometry": {"x": 975, "y": 210, "width": 400, "height": 300},
        "all_current_rois_no_overlap": True,
        "game_capture_required": False,
    }
    assert manifest["configured_fallback"]["post_phase37_reassessment"] == {
        "source_config": "config/internal.yaml",
        "added_roi": "rois.shop_exit_icon",
        "added_roi_geometry": {"x": 39, "y": 25, "width": 267, "height": 70},
        "all_current_rois_no_overlap": True,
        "game_capture_required": False,
    }
    assert manifest["criteria_all_met"] is True
    assert manifest["screenshots_persisted"] is False
    assert manifest["game_input_sent"] is False
    assert "unresolved_purchase_result_roi_not_in_fallback_set" not in manifest[
        "limitations"
    ]
