from dataclasses import replace

import pytest
import yaml

from e7auto.configuration.loader import load_config
from e7auto.configuration.models import ConfigError
from e7auto.configuration.shop_layout import visible_shop_rows
from tests.helpers.paths import ROOT


@pytest.fixture(scope="module")
def config():
    return load_config(ROOT / "config/internal.yaml")


def test_each_view_uses_complete_rows_at_its_own_calibrated_positions(config):
    top = visible_shop_rows(config.slots, config.rois["inventory_content"], "top")
    bottom = visible_shop_rows(config.slots, config.rois["inventory_content"], "bottom")
    assert [(r.row_id, r.roi.y) for r in top] == [("top_1", 145), ("top_2", 408), ("top_3", 671), ("top_4", 934)]
    assert [(r.row_id, r.roi.y) for r in bottom] == [("bottom_3", 277), ("bottom_4", 540), ("bottom_5", 803), ("bottom_6", 1066)]
    assert all(r.roi.width == 240 and r.roi.height == 220 for r in (*top, *bottom))


def test_bottom_rows_follow_calibration_changes_without_a_fixed_scroll_offset(config):
    slots = tuple(replace(s, item_roi=replace(s.item_roi, y=s.item_roi.y - 20)) if s.screen == "bottom" else s
                  for s in config.slots)
    rows = visible_shop_rows(slots, config.rois["inventory_content"], "bottom")
    assert [row.roi.y for row in rows] == [257, 520, 783, 1046]


@pytest.mark.parametrize("mutation", ["missing_bottom", "missing_order", "duplicate", "pitch", "shape", "outside"])
def test_inconsistent_layout_cannot_silently_choose_measurement_regions(config, mutation):
    slots = list(config.slots)
    if mutation == "missing_bottom": slots = [s for s in slots if s.screen == "top"]
    elif mutation == "missing_order": slots.pop(1)
    elif mutation == "duplicate": slots.append(slots[0])
    elif mutation == "pitch": slots[-1] = replace(slots[-1], item_roi=replace(slots[-1].item_roi, y=1065))
    elif mutation == "shape": slots[-1] = replace(slots[-1], item_roi=replace(slots[-1].item_roi, width=239))
    else: slots[-1] = replace(slots[-1], item_roi=replace(slots[-1].item_roi, y=1500))
    with pytest.raises(ValueError):
        visible_shop_rows(tuple(slots), config.rois["inventory_content"], "bottom")


def test_unknown_view_is_rejected(config):
    with pytest.raises(ValueError, match="Unknown inventory screen"):
        visible_shop_rows(config.slots, config.rois["inventory_content"], "middle")


def test_loader_rejects_a_drifted_bottom_anchor(tmp_path):
    source = ROOT / "config/internal.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["template_manifests"] = {k: str((source.parent / v).resolve()) for k, v in raw["template_manifests"].items()}
    raw["slots"][-1]["item_roi"]["y"] += 1
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="slots.viewport_layout"):
        load_config(path)
