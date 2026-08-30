from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from e7auto.config import ConfigError, Point, Rect, Size, load_config
from e7auto.domain import OverlayActivityStatus, RunState, RuntimeSnapshot, StopReason
from e7auto.overlay import (
    capture_is_safe,
    evaluate_overlay_security,
    overlay_rect,
)
from e7auto.run_logging import RunLogManager
from e7auto.config import LoggingConfig


def test_default_configuration_loads_after_completed_calibration() -> None:
    config = load_config(Path("config/internal.yaml"))

    assert config.rois["purchase_result"] == Rect(975, 210, 400, 300)
    assert config.template_paths["insufficient_funds"].name == "insufficient_funds.png"
    assert config.display.reference_mode == Size(3120, 2080)
    assert config.display.minimum_mode == Size(2560, 1440)
    assert config.display.client_width_fraction == 0.60


def test_calibration_complete_gate_still_fails_closed(tmp_path: Path) -> None:
    source = Path("config/internal.yaml").resolve()
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["calibration_complete"] = False
    raw["templates"] = {
        key: str((source.parent / value).resolve())
        for key, value in raw["templates"].items()
    }
    partial = tmp_path / "internal.yaml"
    partial.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ConfigError) as raised:
        load_config(partial)
    assert "calibration_complete" in str(raised.value)


def test_runtime_snapshot_is_immutable_and_freezes_after_final() -> None:
    initial = RuntimeSnapshot.initial("r1", (("wood", "木材"),), 20)
    counted = initial.with_incremented_target("wood").with_refresh_spent(10)
    final = counted.finalized(StopReason.BUDGET_COMPLETE)
    assert initial.targets[0].acquired == 0
    assert initial.refreshes_without_mandatory_target == 0
    assert initial.overlay_status is OverlayActivityStatus.STARTED
    assert final.targets[0].acquired == 1
    assert final.overlay_status is OverlayActivityStatus.STOPPED
    assert final.with_incremented_target("wood") is final
    assert final.transitioned(RunState.SCANNING_TOP) is final


def test_overlay_is_relative_and_fallback_fails_only_on_roi_overlap() -> None:
    client = Rect(100, 200, 800, 600)
    placed = overlay_rect(client, Point(12, 34), 200, 100)
    assert placed == Rect(112, 234, 200, 100)
    roi = Rect(0, 0, 80, 80)
    assert capture_is_safe(True, placed, client, (roi,))
    assert not capture_is_safe(False, Rect(110, 210, 50, 50), client, (roi,))
    assert capture_is_safe(False, Rect(700, 700, 50, 50), client, (roi,))


def test_overlay_security_requires_exact_affinity_readback_or_safe_fallback() -> None:
    client = Rect(100, 200, 800, 600)
    overlapping = Rect(110, 210, 50, 50)
    roi = Rect(0, 0, 80, 80)

    setter_only = evaluate_overlay_security(
        True,
        None,
        0x11,
        overlapping,
        client,
        (roi,),
    )
    assert not setter_only.capture_excluded
    assert not setter_only.fallback_safe
    assert not setter_only.safe

    excluded = evaluate_overlay_security(
        True,
        0x11,
        0x11,
        overlapping,
        client,
        (roi,),
    )
    assert excluded.capture_excluded
    assert excluded.safe

    fallback = evaluate_overlay_security(
        False,
        None,
        0x11,
        Rect(700, 700, 50, 50),
        client,
        (roi,),
    )
    assert fallback.fallback_safe
    assert fallback.safe


def test_complete_synthetic_configuration_loads(tmp_path: Path) -> None:
    template_keys = {
        "main_shop_icon",
        "shop_refresh_button",
        "shop_exit_icon",
        "refresh_confirm_prompt",
        "refresh_confirm_button",
        "confirm_button",
        "insufficient_funds",
        "sky_stone_icon",
        "covenant_bookmark",
        "covenant_bookmark_confirm",
        "covenant_bookmark_purchased",
        "mystic_medal",
        "mystic_medal_confirm",
        "mystic_medal_purchased",
        "friendship_points",
        "friendship_points_confirm",
        "friendship_points_purchased",
        *(f"sky_stone_digit_{digit}" for digit in range(10)),
        "sky_stone_digit_0_wide",
    }
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    for key in template_keys:
        (template_dir / f"{key}.bin").write_bytes(b"reference-template")
    rect = {"x": 0, "y": 0, "width": 10, "height": 10}
    raw = {
        "schema_version": 1,
        "calibration_complete": True,
        "game": {"executable_path": "Game.exe", "window_title": "Game", "baseline_client_size": {"width": 100, "height": 80}},
        "display": {
            "reference_mode": {"width": 3120, "height": 2080},
            "minimum_mode": {"width": 2560, "height": 1440},
            "client_width_fraction": 0.60,
        },
        "economy": {"refresh_cost": 3},
        "templates": {key: f"templates/{key}.bin" for key in template_keys},
        "rois": {key: rect for key in ("main_shop_icon", "shop_refresh_button", "shop_exit_icon", "refresh_confirm_prompt", "refresh_confirm_button", "inventory_list", "confirm_item", "confirm_button", "purchase_result", "sky_stone_icon", "sky_stone_digits")},
        "points": {
            key: {"x": 5, "y": 5}
            for key in (
                "shop_icon",
                "shop_exit_button",
                "main_screen_wake",
                "refresh_button",
                "refresh_confirm_button",
                "confirm_button",
            )
        },
        "targets": [
            {"id": "covenant_bookmark", "display_name": "圣约书签", "template": "covenant_bookmark", "confirm_template": "covenant_bookmark_confirm", "purchased_template": "covenant_bookmark_purchased"},
            {"id": "mystic_medal", "display_name": "神秘奖牌", "template": "mystic_medal", "confirm_template": "mystic_medal_confirm", "purchased_template": "mystic_medal_purchased"},
            {"id": "friendship_points", "display_name": "友情点数", "template": "friendship_points", "confirm_template": "friendship_points_confirm", "purchased_template": "friendship_points_purchased", "user_selectable": True},
        ],
        "slots": [
            {"id": "top-1", "screen": "top", "order": 0, "item_roi": rect, "buy_point": {"x": 5, "y": 5}},
            {"id": "bottom-1", "screen": "bottom", "order": 0, "item_roi": rect, "buy_point": {"x": 5, "y": 5}},
        ],
        "scroll": {
            "cursor_point": {"x": 5, "y": 5},
            "delta": -120,
            "repetitions": 1,
            "interval_ms": 100,
            "settle_ms": 800,
            "minimum_settle_ms": 200,
            "settle_poll_interval_ms": 100,
            "stable_observations": 2,
            "maximum_pairwise_shift_px": 1.0,
            "minimum_phase_response": 0.80,
            "downsample_factor": 4,
            "minimum_upward_shift_px": 300,
            "difference_threshold": 8,
            "minimum_changed_fraction": 0.30,
        },
        "timing": {"poll_interval_ms": 10, "entry_timeout_ms": 10, "scan_timeout_ms": 10, "dialog_timeout_ms": 10, "purchase_result_timeout_ms": 10, "refresh_timeout_ms": 10, "stable_frames": 1},
        "refresh_strategy": {
            "batch_refreshes": [13, 13, 13, 10],
            "recovery_wait_seconds": [5, 180, 5],
        },
        "vision": {
            "default_confidence": 0.9,
            "anchor_confidence": 0.95,
            "sky_stone_digit_confidence": 0.8,
            "sky_stone_digit_margin": 0.08,
            "sky_stone_digits_offset": {"x": 1, "y": 2},
        },
        "overlay": {"offset": {"x": 1, "y": 2}},
        "logging": {"keep_days": 7, "keep_files": 2},
    }
    path = tmp_path / "internal.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    loaded = load_config(path)
    assert loaded.refresh_cost == 3
    assert loaded.process_name == "Game.exe"
    assert loaded.sky_stone_digit_confidence == 0.8
    assert loaded.sky_stone_digit_margin == 0.08
    assert loaded.sky_stone_digits_offset == Point(1, 2)
    assert loaded.refresh_strategy.batch_refreshes == (13, 13, 13, 10)
    assert loaded.refresh_strategy.recovery_wait_seconds == (5, 180, 5)
    assert loaded.scroll.minimum_settle_ms == 200
    assert loaded.scroll.settle_poll_interval_ms == 100
    assert loaded.scroll.stable_observations == 2
    assert loaded.scroll.maximum_pairwise_shift_px == 1.0
    assert loaded.scroll.minimum_phase_response == 0.80
    assert loaded.scroll.downsample_factor == 4
    assert [target.target_id for target in loaded.targets] == [
        "covenant_bookmark",
        "mystic_medal",
        "friendship_points",
    ]
    assert loaded.slots[0].screen == "top"


def test_text_logger_is_utf8_and_file_count_is_bounded(tmp_path: Path) -> None:
    manager = RunLogManager(tmp_path, LoggingConfig(30, 2))
    for run_id in ("one", "two", "three"):
        logger = manager.start(run_id)
        logger.event("message", text="中文", roi="1,2,3,4")
        logger.close()
    files = list(tmp_path.glob("run-*.log"))
    assert len(files) <= 2
    assert any("中文" in path.read_text(encoding="utf-8") for path in files)
    assert not list(tmp_path.glob("*.png"))


def test_text_logger_prunes_numbered_rotations_but_not_lookalikes(tmp_path: Path) -> None:
    rotations = [tmp_path / "run-old.log.1", tmp_path / "run-old.log.2"]
    lookalike = tmp_path / "run-old.log.backup"
    for path in (*rotations, lookalike):
        path.write_text("old", encoding="utf-8")
        os.utime(path, (1_600_000_000, 1_600_000_000))

    logger = RunLogManager(tmp_path, LoggingConfig(1, 5)).start("current")
    logger.close()

    assert all(not path.exists() for path in rotations)
    assert lookalike.exists()


def test_compact_logger_keeps_only_user_actionable_events(tmp_path: Path) -> None:
    manager = RunLogManager(tmp_path, LoggingConfig(30, 2, "compact", 0))
    logger = manager.start("compact")
    logger.event("recognition", object="shop", detected=False)
    logger.event("recognition", object="shop", detected=True, stable=1, confidence="0.9")
    logger.event("recognition", object="shop", detected=True, stable=3, confidence="0.99")
    logger.event("inventory_scan", screen="top", targets=0, stable=1)
    logger.event("inventory_scan", screen="top", targets=1, stable=3)
    logger.event("sky_stone_observation", stage="before_refresh", value=100, stable=1)
    logger.event(
        "input_failed",
        action="confirm_refresh",
        logical_x=1485,
        logical_y=925,
        screen_x=1500,
        screen_y=950,
        error="denied",
    )
    logger.event("refresh_counted", sky_stone_before=100, sky_stone_after=97)
    logger.event("run_stopped", reason="input_failure", detail="confirm_refresh denied")
    logger.close()
    text = next(tmp_path.glob("run-*.log")).read_text(encoding="utf-8")
    assert "event=recognition" not in text
    assert "event=inventory_scan" not in text
    assert "event=sky_stone_observation" not in text
    assert "event=input_failed" not in text
    assert "logical_x=" not in text
    assert "screen_x=" not in text
    assert "event=refresh_counted" in text
    assert "event=run_stopped" in text
