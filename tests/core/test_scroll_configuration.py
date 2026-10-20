from pathlib import Path

import pytest
import yaml

from e7auto.configuration.loader import load_config
from e7auto.configuration.models import ConfigError
from tests.helpers.paths import ROOT


def configured_file(tmp_path, mutate):
    source = ROOT / "config/internal.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["template_manifests"] = {key: str((source.parent / value).resolve()) for key, value in raw["template_manifests"].items()}
    mutate(raw)
    target = tmp_path / "internal.yaml"
    target.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return target


@pytest.mark.parametrize("key", ["maximum_horizontal_shift_px", "minimum_content_stddev", "content_alignment_tolerance_px",
                                 "minimum_overlap_fraction", "minimum_content_score"])
@pytest.mark.parametrize("value", [None, 0, -1, True, float("nan"), float("inf")])
def test_invalid_content_matching_parameters_fail_closed(tmp_path, key, value):
    path = configured_file(tmp_path, lambda raw: raw["scroll"].update({key: value}))
    with pytest.raises(ConfigError, match=f"scroll.{key}"):
        load_config(path)


@pytest.mark.parametrize("roi", [None, {"x": 950, "y": 110, "width": 1320, "height": 1180},
                                {"x": 900, "y": 110, "width": 900, "height": 1180}])
def test_content_roi_must_exist_and_exclude_the_full_inventory_width(tmp_path, roi):
    path = configured_file(tmp_path, lambda raw: raw["rois"].update(inventory_content=roi))
    with pytest.raises(ConfigError, match="rois.inventory_content"):
        load_config(path)


def test_confirmation_window_must_fit_the_original_timeout(tmp_path):
    path = configured_file(tmp_path, lambda raw: raw["scroll"].update(stable_observations=8))
    with pytest.raises(ConfigError, match="stable observations must fit"):
        load_config(path)
