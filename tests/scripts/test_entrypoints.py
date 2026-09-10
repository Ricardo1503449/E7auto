from __future__ import annotations

from pathlib import Path
import importlib
import subprocess
import sys

import cv2
import numpy as np
import pytest

from scripts.common.image_io import read_color_rgba_png, read_png, read_rgba_png, write_png
from tests.helpers.paths import ROOT


CLI_MODULES = [
    "calibration.calibrate_client_frames",
    "calibration.calibrate_overlay_position",
    "calibration.crop_calibration_templates",
    "calibration.crop_network_templates",
    "calibration.extract_insufficient_funds_template",
    "calibration.extract_main_shop_icon_template",
    "calibration.extract_refresh_confirm_templates",
    "calibration.extract_shop_exit_icon_template",
    "calibration.extract_shop_refresh_button_template",
    "calibration.extract_sky_stone_templates",
    "calibration.extract_sky_stone_zero_wide_template",
    "calibration.make_network_text_templates",
    "calibration.promote_insufficient_funds_live_result",
    "validation.validate_background_mode",
    "validation.validate_insufficient_funds",
]


@pytest.mark.parametrize("module", CLI_MODULES)
def test_moved_cli_help_resolves_without_running_actions(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts." + module, "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


@pytest.mark.parametrize("module", [
    "crop_network_templates", "make_network_text_templates", "extract_sky_stone_templates",
    "extract_refresh_confirm_templates", "extract_sky_stone_zero_wide_template",
    "calibrate_client_frames",
])
def test_source_tools_require_explicit_inputs(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.calibration." + module],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 2, result.stderr
    assert "required" in result.stderr


def test_network_tool_imports_do_not_read_or_write_images(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Import must not process calibration images")

    monkeypatch.setattr(cv2, "imread", forbidden)
    monkeypatch.setattr(cv2, "imwrite", forbidden)
    monkeypatch.setattr(cv2, "imencode", forbidden)
    monkeypatch.setattr(np, "fromfile", forbidden)
    for module in ("crop_network_templates", "make_network_text_templates"):
        importlib.reload(importlib.import_module("scripts.calibration." + module))


def test_shared_png_io_preserves_pixels_channels_and_unicode_paths(tmp_path: Path) -> None:
    path = tmp_path / "校准 源.png"
    rgba = np.arange(4 * 5 * 4, dtype=np.uint8).reshape(4, 5, 4)
    write_png(path, rgba)
    assert np.array_equal(read_rgba_png(path), rgba)
    rgb = rgba[:, :, :3].copy()
    write_png(path, rgb)
    assert np.array_equal(read_png(path), rgb)
    with pytest.raises(RuntimeError, match="RGBA"):
        read_rgba_png(path)
    converted = read_color_rgba_png(path)
    assert np.array_equal(converted[:, :, :3], rgb)
    assert np.all(converted[:, :, 3] == 255)
    path.write_bytes(b"not a PNG")
    with pytest.raises(RuntimeError, match="decode"):
        read_png(path)


def test_tool_paths_resolve_outside_project_cwd(tmp_path: Path) -> None:
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
        "from scripts.common.paths import PROJECT_ROOT, TEMPLATES_DIR; "
        "assert PROJECT_ROOT == Path(sys.argv[1]); "
        "assert (TEMPLATES_DIR / 'manifest.yaml').is_file()"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(ROOT)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
