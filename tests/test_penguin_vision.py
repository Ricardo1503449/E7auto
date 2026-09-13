from __future__ import annotations

from dataclasses import replace
import json

import cv2
import numpy as np
import pytest

from e7auto.config import ConfigError, Size, load_config
from e7auto.geometry import CoordinateTransform, adapt_frame
from e7auto.penguin_vision import PenguinVision, with_penguin_config
from e7auto.vision import TemplateRepository
from tests.helpers.paths import ROOT


@pytest.fixture(scope="module")
def vision():
    config = with_penguin_config(load_config(ROOT / "config/internal.yaml"))
    return PenguinVision(config, TemplateRepository(config))


def dialog_frame(amount):
    path = ROOT / f"tests/fixtures/penguin_dialog_{amount}.png"
    crop = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), 1)
    frame = np.zeros((1306, 2322, 3), np.uint8)
    frame[770:1160, 620:1740] = crop
    return frame


@pytest.mark.parametrize("amount", [102, 5100])
@pytest.mark.parametrize("width", [2322, 1920, 1536])
def test_real_offline_dialog_price_and_full_button_at_supported_scales(vision, amount, width):
    frame = dialog_frame(amount)
    height = round(width * 1306/2322)
    if width != 2322:
        frame = adapt_frame(cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA),
                            CoordinateTransform(Size(2322, 1306), Size(width, height)))
    result = vision.dialog(frame)
    assert result is not None
    assert result.price == amount
    assert result.full_price_button is (amount == 5100)


def test_missing_price_is_unknown_not_insufficient(vision):
    frame = dialog_frame(5100)
    frame[1035:1097, 1170:1380] = (18, 40, 15)
    result = vision.dialog(frame)
    assert result is not None and result.price is None
    assert not result.full_price_button


def test_background_button_cannot_be_mistaken_for_active_dialog(vision):
    frame = (dialog_frame(5100).astype(float)*0.3).astype(np.uint8)
    assert vision.dialog(frame) is None
    assert vision.dialog(np.zeros_like(frame)) is None


def test_penguin_template_configuration_checks_baseline_and_required_assets(tmp_path):
    config = load_config(ROOT / "config/internal.yaml")
    with pytest.raises(ConfigError):
        with_penguin_config(replace(config, baseline_client_size=Size(100, 80)))
    with pytest.raises(ConfigError):
        with_penguin_config(replace(config, source_path=tmp_path / "config/internal.yaml"))


def test_approved_templates_have_masks_and_fit_baseline(vision):
    manifest = json.loads((ROOT / "assets/templates/penguin/manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["templates"]) == 13
    for name, entry in manifest["templates"].items():
        data = vision._templates.get("penguin_"+name)
        assert data.mask is not None and np.any(data.mask == 0) and np.any(data.mask == 255)
        x, y, w, h = entry["roi"]
        assert data.image.shape[:2] == (h, w)
        assert 0 <= x < x+w <= 2322 and 0 <= y < y+h <= 1306
    assert vision._templates.get("penguin_reward_close").image.shape[0] == 47
