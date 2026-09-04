from __future__ import annotations

import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from e7auto.config import Rect, Size, load_config
from e7auto.ports import WindowState
from e7auto.ui import StatsOverlay
from scripts.calibrate_overlay_position import (
    confirmed_result,
    load_position_calibration_config,
    position_calibration_initial_state_is_valid,
)
from scripts.verify_release import verify_template_assets
from tests.helpers import make_config


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "internal.yaml"


def test_position_loader_does_not_relax_production_config_gate() -> None:
    production = load_config(CONFIG_PATH)
    assert production.rois["purchase_result"] == Rect(975, 210, 400, 300)

    config = load_position_calibration_config(CONFIG_PATH)
    assert str(config.executable_path) == "EpicSeven.exe"
    assert config.window_title == "第七史诗"
    assert (config.baseline_client_size.width, config.baseline_client_size.height) == (
        2322,
        1306,
    )
    assert [target.display_name for target in config.targets] == [
        "圣约书签",
        "神秘奖牌",
        "友情点数",
    ]


def test_position_calibration_requires_game_foreground_before_start() -> None:
    bounds = Rect(100, 200, 2322, 1306)
    assert position_calibration_initial_state_is_valid(
        WindowState(True, False, True, bounds),
        Size(2322, 1306),
    )
    assert not position_calibration_initial_state_is_valid(
        WindowState(True, False, False, bounds),
        Size(2322, 1306),
    )


def test_position_mode_reuses_production_size_and_reports_exact_offset() -> None:
    application = QApplication.instance() or QApplication([])
    config = load_position_calibration_config(CONFIG_PATH)
    client = Rect(300, 200, 2322, 1306)
    production = StatsOverlay()
    overlay = StatsOverlay()
    try:
        production.configure(make_config(include_friendship=True))
        production.show()
        overlay.begin_position_calibration(config.targets, client)
        application.processEvents()
        calibration_size = overlay.size()
        assert calibration_size == production.size()
        assert not overlay.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )

        overlay.move(client.x + 417, client.y - 83)
        application.processEvents()
        payload = confirmed_result(client, overlay)

        assert payload["offset"] == {"x": 417, "y": -83}
        assert payload["overlay_bounds"] == {
            "x": client.x + 417,
            "y": client.y - 83,
            "width": calibration_size.width(),
            "height": calibration_size.height(),
        }
        overlay.finish_position_calibration()
        assert overlay.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
    finally:
        production.close()
        overlay.close()
        application.processEvents()


def test_release_asset_verifier_accepts_current_calibration_evidence() -> None:
    assert verify_template_assets(ROOT / "assets" / "templates") == []


def test_release_verifier_requires_stage_two_overlay_capture_evidence(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "templates"
    shutil.copytree(ROOT / "assets" / "templates", copied)
    (copied / "overlay_capture_validation_manifest.yaml").unlink()

    problems = verify_template_assets(copied)

    assert "missing template manifest: overlay_capture_validation_manifest.yaml" in problems


def test_release_verifier_rejects_an_undecodable_template(tmp_path: Path) -> None:
    copied = tmp_path / "templates"
    shutil.copytree(ROOT / "assets" / "templates", copied)
    (copied / "main_shop_icon.png").write_bytes(b"not a PNG")

    problems = verify_template_assets(copied)

    assert "invalid template asset: main_shop_icon.png" in problems


def test_release_verifier_requires_network_recovery_templates(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "templates"
    shutil.copytree(ROOT / "assets" / "templates", copied)
    (copied / "network_retry.png").unlink()

    problems = verify_template_assets(copied)

    assert "missing template asset: network_retry.png" in problems
