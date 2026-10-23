from dataclasses import replace
import cv2
import numpy as np
import pytest

from e7auto.configuration.loader import load_config
from e7auto.core.types import Size
from e7auto.features.shop.readiness import SceneWindow
from e7auto.features.shop.readiness import pixels_are_stable
from e7auto.features.shop.vision import ShopVision
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import CoordinateTransform, FrameView
from tests.helpers.shop_scenes import readiness_frame
from tests.helpers.paths import ROOT
from e7auto.core.types import ClientPoint


@pytest.fixture(scope="module")
def config():
    return load_config(ROOT / "config/internal.yaml")


@pytest.fixture(scope="module")
def vision(config):
    return ShopVision(config, TemplateRepository(config))


@pytest.mark.parametrize("size", [Size(2322, 1306), Size(2321, 1305), Size(1920, 1080), Size(1536, 864)])
@pytest.mark.parametrize("key,screen,expected", [
    ("clean_top", "top", "ready"), ("previous_clean_top", "top", "ready"),
    ("clean_bottom", "bottom", "ready"), ("reward_overlay", "bottom", "reward_overlay"),
])
def test_real_ready_and_reward_scenes(config, vision, size, key, screen, expected):
    raw = readiness_frame(key)
    if size != config.baseline_client_size:
        raw = cv2.resize(raw, (size.width, size.height), interpolation=cv2.INTER_AREA)
    frame = FrameView(raw, CoordinateTransform(config.baseline_client_size, size))
    assert vision.inventory_scene_state(frame, screen) == expected


@pytest.mark.parametrize("variant", ["blank", "partial", "fading"])
def test_static_unfinished_rows_cannot_become_ready(config, vision, variant):
    frame = readiness_frame("clean_top")
    roi = config.slots[2].item_roi
    pixels = frame[roi.y:roi.bottom, roi.x:roi.right]
    if variant == "blank":
        pixels[:] = 0
    elif variant == "partial":
        pixels[:roi.height // 2] = 0
    else:
        pixels[:] = (pixels * .1).astype(np.uint8)
    window = SceneWindow(3, config.scroll, config.inventory_readiness)
    for i in range(6):
        scene = vision.inventory_scene_state(frame, "top")
        assert scene == "incomplete_rows"
        assert not window.observe(frame, i * .1, 0, scene, lambda a, b: vision.inventory_scene_stability(a, b, "top"))


def test_static_reward_toast_is_rejected_even_when_every_pixel_is_stationary(config, vision):
    frame = readiness_frame("reward_overlay")
    assert vision.inventory_scene_stability(frame, frame, "bottom").maximum_row_changed_fraction == 0
    window = SceneWindow(3, config.scroll, config.inventory_readiness)
    for i in range(6):
        assert not window.observe(frame, i * .1, 0, vision.inventory_scene_state(frame, "bottom"),
                                  lambda a, b: vision.inventory_scene_stability(a, b, "bottom"))
    assert window.reason == "reward_overlay"


def test_small_local_change_is_not_diluted_by_the_whole_content_roi(config, vision):
    before = readiness_frame("clean_top")
    after = before.copy()
    roi = config.slots[2].item_roi
    inset = config.inventory_readiness.pixel_inset
    after[roi.y + inset.y:roi.y + inset.y + 20, roi.x:roi.right] = 100
    result = vision.inventory_scene_stability(before, after, "top")
    assert result.phase_response > config.scroll.minimum_phase_response
    assert abs(result.phase_shift_y) < 1
    assert result.maximum_row_changed_fraction > config.inventory_readiness.maximum_row_changed_fraction


def test_original_pixel_checks_and_template_checks_do_not_resize_reference_frames(config, vision, monkeypatch):
    frame = readiness_frame("clean_top")
    def forbidden(*args, **kwargs):
        pytest.fail("Readiness layout/toast check resized a reference frame")
    # The existing baseline template matcher may use its approved proposal resize;
    # exercise the new raw layout path after supplying the existing detector results.
    monkeypatch.setattr(vision, "shop_ready", lambda _: object())
    monkeypatch.setattr(vision, "match", lambda *_: None)
    monkeypatch.setattr(cv2, "resize", forbidden)
    assert vision.inventory_scene_state(frame, "top") == "ready"


def test_two_real_stable_stop_captures_fit_the_row_change_limit(config, vision):
    with np.load(ROOT / "tests/fixtures/shop/scroll_rows_recent.npz") as data:
        transform = CoordinateTransform(config.baseline_client_size, config.baseline_client_size)
        frames = [FrameView(data[key], transform, ClientPoint(950, 110))
                  for key in ("after_152637", "after_152707")]
        change = vision.inventory_scene_stability(*frames, "bottom")
    assert pixels_are_stable(change, config.scroll, config.inventory_readiness)


@pytest.mark.parametrize("extra,accepted", [(0, True), (1, False)])
def test_row_change_fraction_boundary_uses_native_color_pixels(config, vision, extra, accepted):
    before = readiness_frame("clean_top")
    after = before.copy()
    roi = config.slots[2].item_roi
    inset = config.inventory_readiness.pixel_inset
    width, height = roi.width - inset.x * 2, roi.height - inset.y * 2
    amount = int(width * height * config.inventory_readiness.maximum_row_changed_fraction) + extra
    ys, xs = np.divmod(np.arange(amount), width)
    after[roi.y + inset.y + ys, roi.x + inset.x + xs, 0] ^= 128
    change = vision.inventory_scene_stability(before, after, "top")
    assert change.maximum_row_changed_fraction == pytest.approx(amount / (width * height))
    change = replace(change, phase_shift_x=0, phase_shift_y=0, phase_response=.99)
    assert pixels_are_stable(change, config.scroll, config.inventory_readiness) is accepted
