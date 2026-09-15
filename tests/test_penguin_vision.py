from __future__ import annotations

from dataclasses import replace
import json

import cv2
import numpy as np
import pytest

from e7auto.config import ConfigError, Rect, Size, load_config
from e7auto.geometry import CoordinateTransform, adapt_frame
from e7auto.penguin_vision import PenguinVision, with_penguin_config
from e7auto.vision import TemplateRepository
from tests.helpers.paths import ROOT


@pytest.fixture(scope="module")
def vision():
    config = with_penguin_config(load_config(ROOT / "config/internal.yaml", template_profile="penguin"))
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


@pytest.mark.parametrize("dx,dy", [(0, 0), (7, -3)])
def test_configured_price_rect_controls_both_mask_and_relative_read(monkeypatch, dx, dy):
    config = with_penguin_config(load_config(ROOT / "config/internal.yaml", template_profile="penguin"))
    config = replace(config, penguin_price_rect=Rect(100, 22, 190, 60))
    templates = TemplateRepository(config)
    original_mask = templates.get("penguin_penguin_buy_5100").mask.copy()
    vision = PenguinVision(config, templates)
    expected_mask = original_mask.copy()
    expected_mask[22:82, 100:290] = 0
    np.testing.assert_array_equal(vision._purchase_body.mask, expected_mask)
    np.testing.assert_array_equal(templates.get("penguin_penguin_buy_5100").mask, original_mask)
    frame = cv2.warpAffine(dialog_frame(5100), np.float32([[1, 0, dx], [0, 1, dy]]), (2322, 1306))
    reads = []

    def read_price(frame, roi):
        reads.append(roi)
        return 5100

    monkeypatch.setattr(vision, "_price", read_price)
    assert vision.dialog(frame) is not None
    assert reads == [Rect(1155+dx, 1032+dy, 190, 60)]


def test_price_rect_cannot_remove_the_entire_button_template():
    config = with_penguin_config(load_config(ROOT / "config/internal.yaml", template_profile="penguin"))
    config = replace(config, penguin_price_rect=Rect(0, 0, 604, 118))
    with pytest.raises(ConfigError, match="removes the entire purchase-button mask"):
        PenguinVision(config, TemplateRepository(config))


def test_penguin_template_configuration_checks_baseline_and_required_assets(tmp_path):
    config = load_config(ROOT / "config/internal.yaml", template_profile="penguin")
    with pytest.raises(ConfigError):
        with_penguin_config(replace(config, baseline_client_size=Size(100, 80)))
    with pytest.raises(ConfigError):
        with_penguin_config(replace(config, template_manifest_paths={"penguin": tmp_path / "missing.json"}))


def test_sanctuary_uses_explicit_search_rectangle_without_expanding_or_copying_shop():
    config = load_config(ROOT / "config/internal.yaml", template_profile="penguin")
    requested = Rect(20, 245, 170, 190)
    changed = replace(config, rois={**config.rois, "left_icon_column": requested})
    result = with_penguin_config(changed)
    assert result.rois["left_icon_column"] == requested
    assert result.rois["penguin_forest_entry"] == config.rois["penguin_forest_entry"]


@pytest.mark.parametrize("search", [None, Rect(40, 260, 79, 114), Rect(40, 260, 80, 113),
                                    Rect(-1, 260, 128, 162), Rect(2300, 260, 128, 162),
                                    Rect(40, 1250, 128, 162)])
def test_sanctuary_missing_small_or_outside_search_is_rejected(search):
    config = load_config(ROOT / "config/internal.yaml", template_profile="penguin")
    rois = {k: v for k, v in config.rois.items() if k != "left_icon_column"}
    if search is not None:
        rois["left_icon_column"] = search
    with pytest.raises(ConfigError, match="rois.left_icon_column"):
        with_penguin_config(replace(config, rois=rois))


def test_approved_templates_have_masks_and_fit_baseline(vision):
    manifest = json.loads((ROOT / "assets/templates/penguin/manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["templates"]) == 13
    for name, entry in manifest["templates"].items():
        data = vision._templates.get("penguin_"+name)
        assert data.mask is not None and np.any(data.mask == 0) and np.any(data.mask == 255)
        x, y, w, h = entry["source"]["crop"]
        assert data.image.shape[:2] == (h, w)
        assert 0 <= x < x+w <= 2322 and 0 <= y < y+h <= 1306
    assert vision._templates.get("penguin_reward_close").image.shape[0] == 47
