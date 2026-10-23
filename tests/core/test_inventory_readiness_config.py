from pathlib import Path

import pytest
import yaml

from e7auto.configuration.loader import load_config
from e7auto.configuration.models import ConfigError
from tests.helpers.paths import ROOT


def config_file(tmp_path, key, value):
    source = ROOT / "config/internal.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["template_manifests"] = {k: str((source.parent / v).resolve()) for k, v in raw["template_manifests"].items()}
    raw["inventory_readiness"][key] = value
    path = tmp_path / "internal.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.mark.parametrize("key,value", [
    ("minimum_frames", 0), ("minimum_frames", 1), ("minimum_frames", 2),
    ("minimum_frames", True), ("minimum_frames", 9),
    ("minimum_stable_ms", 0), ("minimum_stable_ms", 800), ("minimum_stable_ms", True),
    ("difference_threshold", 0), ("difference_threshold", 255),
    ("maximum_row_changed_fraction", 0), ("maximum_row_changed_fraction", 1),
    ("maximum_row_changed_fraction", float("nan")),
    ("minimum_band_stddev", 0), ("minimum_band_stddev", float("inf")),
    ("minimum_band_stddev", float("nan")), ("reward_overlay_confidence", 0),
    ("reward_overlay_confidence", 1.1), ("reference_max_age_ms", 0),
    ("reference_max_age_ms", False), ("reward_overlay_roi", None),
    ("pixel_inset", {"x": -1, "y": 0}), ("pixel_inset", {"x": 120, "y": 0}),
    ("pixel_inset", {"x": 0, "y": 110}),
    ("reward_overlay_roi", {"x": 2300, "y": 90, "width": 250, "height": 215}),
])
def test_unsafe_readiness_configuration_is_rejected(tmp_path, key, value):
    with pytest.raises(ConfigError, match="inventory_readiness"):
        load_config(config_file(tmp_path, key, value))


@pytest.mark.parametrize("profile", ["shop", "penguin", "chaos"])
def test_shared_configuration_still_loads_each_feature_profile(profile):
    assert load_config(ROOT / "config/internal.yaml", template_profile=profile).inventory_readiness.minimum_stable_ms == 200
