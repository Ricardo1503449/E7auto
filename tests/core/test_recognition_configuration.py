"""Runtime parameters are configured independently of template provenance."""
from dataclasses import replace
from pathlib import Path
from tests.helpers.paths import ROOT

import pytest
import yaml

from e7auto.configuration.models import ConfigError, EntryThresholds
from e7auto.core.types import Rect
from e7auto.configuration.loader import load_config
from e7auto.features.penguin.vision import PenguinVision
from e7auto.features.penguin.configuration import with_penguin_config
from e7auto.features.shop.vision import ShopVision as OpenCvGameVision
from e7auto.resources.templates import TemplateRepository




@pytest.fixture
def configuration(tmp_path):
    source = ROOT / "config/internal.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["template_manifests"] = {
        key: str((source.parent / value).resolve()) for key, value in raw["template_manifests"].items()
    }
    return tmp_path / "internal.yaml", raw


@pytest.mark.parametrize("feature", ["shop", "penguin"])
@pytest.mark.parametrize("name", ["color", "structure"])
@pytest.mark.parametrize("value", [None, True, 0, 1.01, float("nan"), float("inf")])
def test_invalid_entry_thresholds_are_rejected(configuration, feature, name, value):
    path, raw = configuration
    raw["vision"]["entry_thresholds"][feature][name] = value
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match=f"vision.entry_thresholds.{feature}.{name}"):
        load_config(path)


@pytest.mark.parametrize("value", [None, {"x": -1, "y": 16}, {"x": 18, "y": -1},
                                  {"x": True, "y": 16}, {"x": 1.5, "y": 16},
                                  {"x": 10000, "y": 16}, {"x": 18, "y": 10000}])
def test_invalid_purchased_button_padding_is_rejected(configuration, value):
    path, raw = configuration
    raw["vision"]["purchased_button_padding"] = value
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="vision.purchased_button_padding"):
        load_config(path)


@pytest.mark.parametrize("value", [None, {"x": -1, "y": 25, "width": 210, "height": 62},
                                  {"x": 115, "y": 25, "width": 0, "height": 62},
                                  {"x": 115, "y": 25, "width": True, "height": 62},
                                  {"x": 115, "y": 25, "width": 490, "height": 62},
                                  {"x": 115, "y": 25, "width": 210, "height": 94}])
def test_invalid_penguin_price_rectangle_is_rejected(configuration, value):
    path, raw = configuration
    raw["vision"]["penguin_price_rect"] = value
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="vision.penguin_price_rect"):
        with_penguin_config(load_config(path, template_profile="penguin"))


def test_zero_padding_is_a_valid_exact_template_search(configuration):
    path, raw = configuration
    raw["vision"]["purchased_button_padding"] = {"x": 0, "y": 0}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_config(path).purchased_button_padding.x == 0


def test_both_entry_paths_use_shared_roi_and_their_configured_thresholds(monkeypatch):
    config = load_config(ROOT / "config/internal.yaml")
    requested = Rect(10, 200, 220, 950)
    config = replace(config, rois={**config.rois, "left_icon_column": requested},
                     entry_thresholds={"shop": EntryThresholds(.91, .83), "penguin": EntryThresholds(.97, .86)})
    shop = OpenCvGameVision(config, TemplateRepository(config))
    penguin_config = with_penguin_config(config)
    penguin = PenguinVision(penguin_config, TemplateRepository(penguin_config))
    calls = []

    def record(frame, key, roi, threshold, *, structure_threshold):
        calls.append((key, roi, threshold, structure_threshold))
        return None

    monkeypatch.setattr(shop, "match_entry", record)
    monkeypatch.setattr(penguin, "match_entry", record)
    shop.main_shop_icon(object())
    penguin.control(object(), "sanctuary_entry")
    assert calls == [("main_shop_icon", requested, .91, .83),
                     ("penguin_sanctuary_entry", requested, .97, .86)]


def test_penguin_control_parameters_are_not_generated_from_manifest(monkeypatch):
    config = load_config(ROOT / "config/internal.yaml", template_profile="penguin")
    requested = Rect(1700, 200, 250, 100)
    config = with_penguin_config(replace(
        config, rois={**config.rois, "penguin_forest_entry": requested}, penguin_control_confidence=.975,
    ))
    vision = PenguinVision(config, TemplateRepository(config))
    calls = []
    monkeypatch.setattr(vision, "match", lambda frame, key, roi, threshold: calls.append((key, roi, threshold)))
    vision.control(object(), "forest_entry")
    assert calls == [("penguin_forest_entry", requested, .975)]
