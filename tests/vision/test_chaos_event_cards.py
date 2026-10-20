"""Real card observations exclude animated artwork while preserving page changes."""
import cv2
import numpy as np
import pytest

from e7auto.configuration.loader import load_config
from e7auto.core.types import Size
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import CoordinateTransform, adapt_frame
from tests.helpers.paths import ROOT


@pytest.fixture
def cards():
    config = with_chaos_config(load_config(ROOT / 'config/internal.yaml', template_profile='chaos'))
    vision = ChaosVision(config, TemplateRepository(config))
    images = {key: cv2.imread(str(ROOT / f'tests/fixtures/chaos/event_cards/{key}.png'))
              for key in ('hp_pose_closed', 'hp_pose_open', 'loot', 'rank')}
    assert all(image is not None for image in images.values())
    yield vision, images
    vision.close_loot_recognition()


@pytest.mark.parametrize('size', [(2322, 1306), (1920, 1080), (1536, 864)])
def test_real_hero_poses_remain_same_card_and_loot_is_new_card(cards, size):
    vision, images = cards
    transform = CoordinateTransform(Size(2322, 1306), Size(*size))
    frames = {key: adapt_frame(cv2.resize(image, size, interpolation=cv2.INTER_AREA), transform)
              for key, image in images.items()}
    previous = vision.event_card_snapshot(frames['hp_pose_closed'])
    assert vision.event_card_stable(frames['hp_pose_open'], previous)
    assert not vision.event_card_changed(frames['hp_pose_open'], previous)
    assert vision.event_card_changed(frames['loot'], previous)
    assert not vision.event_card_stable(frames['loot'], previous)
    assert vision.event_card_changed(frames['hp_pose_open'], vision.event_card_snapshot(frames['loot']))
    for key, frame in frames.items():
        close_key = 'rank_close' if key == 'rank' else 'event_reward_close'
        assert vision.control(frame, close_key) is not None


@pytest.mark.parametrize('key,artwork', [
    ('hp_pose_open', (920, 240, 480, 570)),
    ('rank', (580, 290, 410, 510)),
])
def test_portrait_motion_does_not_reset_card_evidence(cards, key, artwork):
    vision, images = cards
    original = images[key]
    changed = original.copy()
    x, y, width, height = artwork
    changed[y:y+height, x:x+width] = 255 - changed[y:y+height, x:x+width]
    previous = vision.event_card_snapshot(original)
    assert vision.event_card_stable(changed, previous)
    assert not vision.event_card_changed(changed, previous)


def test_static_information_changes_still_break_stability(cards):
    vision, images = cards
    original = images['hp_pose_open']
    changed = original.copy()
    roi = vision.settings.regions['event_card_change']
    changed[roi.y:roi.bottom, roi.x:roi.right] = 255 - changed[roi.y:roi.bottom, roi.x:roi.right]
    previous = vision.event_card_snapshot(original)
    assert not vision.event_card_stable(changed, previous)
    assert vision.event_card_changed(changed, previous)
