from dataclasses import replace

import cv2
import numpy as np
import pytest

from e7auto.configuration.loader import load_config
from e7auto.configuration.shop_layout import visible_shop_rows
from e7auto.core.types import Rect, Size, ClientPoint
from e7auto.features.shop.readiness import pixels_are_stable
from e7auto.features.shop.scroll_vision import measure_inventory_scroll_stability
from e7auto.features.shop.vision import ShopVision
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import CoordinateTransform, FrameView
from tests.helpers.paths import ROOT
from tests.helpers.shop_scenes import readiness_frame, viewport_frame


@pytest.fixture(scope="module")
def config():
    return load_config(ROOT / "config/internal.yaml")


@pytest.fixture(scope="module")
def vision(config):
    return ShopVision(config, TemplateRepository(config))


def legacy_measure(config, before, after):
    return measure_inventory_scroll_stability(before, after, config.rois["inventory_content"], 8, 4,
                                             item_rois=tuple(s.item_roi for s in config.slots),
                                             pixel_inset=config.inventory_readiness.pixel_inset)


@pytest.mark.parametrize("size", [Size(2322, 1306), Size(2321, 1305), Size(2323, 1307), Size(1920, 1080), Size(1536, 864)])
def test_native_diagnostic_boxes_match_the_current_bottom_view(config, vision, size):
    raw = viewport_frame()
    if size != config.baseline_client_size:
        raw = cv2.resize(raw, (size.width, size.height), interpolation=cv2.INTER_AREA)
    transform = CoordinateTransform(config.baseline_client_size, size)
    frame = FrameView(raw, transform)
    result = vision.inventory_scroll_stability(frame, frame)
    assert [r.row_id for r in result.row_pixel_changes] == ["bottom_3", "bottom_4", "bottom_5", "bottom_6"]
    inset = config.inventory_readiness.pixel_inset
    for row, measured in zip(visible_shop_rows(config.slots, config.rois["inventory_content"], "bottom"), result.row_pixel_changes):
        r = row.roi
        expected = transform.rect(Rect(r.x + inset.x, r.y + inset.y, r.width - 2 * inset.x, r.height - 2 * inset.y))
        assert measured.bounds == expected and measured.changed_fraction == 0
    assert vision.inventory_scene_state(frame, "bottom") == "ready"


@pytest.mark.parametrize("screen,band", [("top", (901, 921)), ("bottom", (250, 270))])
def test_changes_in_other_view_windows_do_not_veto_current_icons(config, vision, screen, band):
    before = readiness_frame("clean_top") if screen == "top" else viewport_frame()
    after = before.copy()
    after[band[0]:band[1], 1020:1150, 0] ^= 128
    old = legacy_measure(config, before, after)
    current = vision.inventory_scene_stability(before, after, screen)
    assert old.maximum_row_changed_fraction > .02
    assert current.maximum_row_changed_fraction == 0
    assert pixels_are_stable(current, config.scroll, config.inventory_readiness)


@pytest.mark.parametrize("row_id,band", [("bottom_3", (350, 380)), ("bottom_4", (625, 655))])
def test_upper_visible_bottom_icons_are_measured_even_when_old_boxes_miss_them(config, vision, row_id, band):
    before = viewport_frame()
    after = before.copy()
    after[band[0]:band[1], 1030:1060, 0] ^= 128
    assert legacy_measure(config, before, after).maximum_row_changed_fraction == 0
    current = vision.inventory_scroll_stability(before, after)
    changed = {row.row_id: row.changed_fraction for row in current.row_pixel_changes}
    assert changed[row_id] > .02
    assert all(value == 0 for key, value in changed.items() if key != row_id)
    assert not pixels_are_stable(current, config.scroll, config.inventory_readiness)


@pytest.mark.parametrize("row_id", ["bottom_3", "bottom_4"])
def test_bottom_completeness_check_includes_all_fully_visible_rows(config, vision, row_id):
    frame = viewport_frame()
    row = next(r.roi for r in visible_shop_rows(config.slots, config.rois["inventory_content"], "bottom") if r.row_id == row_id)
    frame[row.y:row.bottom, row.x:row.right] = 0
    assert vision.inventory_scene_state(frame, "bottom") == "incomplete_rows"


def test_subframe_origin_does_not_move_diagnostic_boxes(config, vision):
    raw = viewport_frame()
    parent = FrameView(raw, CoordinateTransform(config.baseline_client_size, config.baseline_client_size))
    cropped = parent.crop(config.rois["inventory_content"])
    assert cropped.origin == ClientPoint(950, 110)
    assert vision.inventory_scroll_stability(cropped, cropped).row_pixel_changes == vision.inventory_scroll_stability(parent, parent).row_pixel_changes


def test_viewport_is_explicit_and_unknown_values_fail_closed(vision):
    frame = viewport_frame()
    with pytest.raises(TypeError):
        vision.inventory_scene_stability(frame, frame)
    with pytest.raises(ValueError, match="Unknown inventory screen"):
        vision.inventory_scene_stability(frame, frame, "middle")
