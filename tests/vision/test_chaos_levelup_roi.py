"""Actual obscured-VICTORY screenshot and the user-selected levelup search area."""
import cv2
import numpy as np
import pytest

from e7auto.configuration.loader import load_config
from e7auto.core.types import Rect, Size
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import CoordinateTransform, adapt_frame
from tests.helpers.paths import ROOT


@pytest.fixture(scope='module')
def detector():
    config = with_chaos_config(load_config(ROOT/'config/internal.yaml', template_profile='chaos'))
    templates = TemplateRepository(config)
    vision = ChaosVision(config, templates)
    yield vision, config
    vision.close_event_recognition()
    vision.close_loot_recognition()


@pytest.fixture(scope='module')
def client():
    # Original screenshot bytes preserved; (45,99) is its measured client origin.
    image = cv2.imread(str(ROOT/'tests/fixtures/chaos/battle/levelup_victory_obscured.png'))
    assert image.shape == (1449, 2418, 3)
    return image[99:1405, 45:2367]


@pytest.mark.parametrize('size', [(2322, 1306), (1920, 1080), (1536, 864)])
def test_real_upgrade_is_recognized_while_victory_is_obscured(detector, client, size):
    vision, config = detector
    assert config.chaos.scenes['levelup'].region == Rect(998, 201, 334, 577)
    frame = adapt_frame(cv2.resize(client, size, interpolation=cv2.INTER_AREA),
                        CoordinateTransform(Size(2322, 1306), Size(*size)))
    assert vision.control(frame, 'levelup') is not None
    assert vision.control(frame, 'levelup_close') is not None
    assert vision.control(frame, 'victory') is None


@pytest.mark.parametrize('size', [(2322, 1306), (1920, 1080), (1536, 864)])
def test_title_lower_in_user_selected_region_is_not_limited_to_old_roi(detector, client, size):
    vision, _ = detector
    shifted = cv2.warpAffine(client, np.float32([[1, 0, 0], [0, 1, 300]]), (2322, 1306))
    frame = adapt_frame(cv2.resize(shifted, size, interpolation=cv2.INTER_AREA),
                        CoordinateTransform(Size(2322, 1306), Size(*size)))
    hit = vision.control(frame, 'levelup')
    assert hit is not None and 500*size[1]/1306 <= hit.bounds.y <= 510*size[1]/1306
    assert vision.control(frame, 'levelup', Rect(1000, 160, 400, 180)) is None
