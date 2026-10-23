import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from e7auto.configuration.loader import load_config
from e7auto.configuration.models import ConfigError
from e7auto.configuration.schema import NAVIGATION_TEMPLATE_KEYS
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.features.chaos.vision import ChaosVision
from e7auto.features.penguin.configuration import with_penguin_config
from e7auto.features.penguin.vision import PenguinVision
from e7auto.features.shop.vision import ShopVision
from e7auto.resources.templates import TemplateRepository
from tests.helpers.paths import ROOT


def configured(tmp_path, profile):
    source = ROOT / 'config/internal.yaml'
    raw = yaml.safe_load(source.read_text(encoding='utf-8'))
    shutil.copytree(ROOT/'assets/templates/common', tmp_path/'common')
    raw['template_manifests'] = {
        key: str(tmp_path/'common/manifest.json') if key == 'common' else
        str((source.parent/value).resolve()) if key == profile else str(tmp_path/key/'missing.json')
        for key, value in raw['template_manifests'].items()
    }
    path = tmp_path/'internal.yaml'
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    return path


def load(path, profile):
    base = load_config(path, template_profile=profile)
    if profile == 'chaos':
        return with_chaos_config(base, ROOT/'config/chaos.yaml')
    if profile == 'penguin':
        return with_penguin_config(base)
    return base


@pytest.mark.parametrize('profile', ['shop', 'penguin', 'chaos'])
def test_all_features_load_shared_navigation_without_other_features(tmp_path, profile):
    config = load(configured(tmp_path, profile), profile)
    assert NAVIGATION_TEMPLATE_KEYS <= config.template_paths.keys()
    repository = TemplateRepository(config)
    cls = {'shop': ShopVision, 'penguin': PenguinVision, 'chaos': ChaosVision}[profile]
    vision = cls(config, repository)
    try:
        for key in sorted(NAVIGATION_TEMPLATE_KEYS):
            frame = np.zeros((1306, 2322, 3), np.uint8)
            data = repository.get(key)
            scene = config.startup_navigation[key]
            h, w = data.image.shape[:2]
            x, y = scene.region.x+5, scene.region.y+5
            frame[y:y+h,x:x+w] = data.image
            detector = vision.navigation_tavern if key == 'navigation_tavern' else vision.navigation_menu
            hit = detector(frame)
            assert hit is not None
            assert x <= hit.anchor.x < x+w and y <= hit.anchor.y < y+h
        assert config.startup_navigation['navigation_menu'].threshold == .92
        assert config.startup_navigation['navigation_tavern'].threshold == .92
        assert config.startup_navigation['navigation_menu_notification'].threshold == .96
    finally:
        if profile == 'chaos':
            vision.close_loot_recognition()
            vision.close_event_recognition()


@pytest.mark.parametrize('profile', ['shop', 'penguin', 'chaos'])
@pytest.mark.parametrize('key', sorted(NAVIGATION_TEMPLATE_KEYS))
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_navigation_required_resource_failure_rejects_start(tmp_path, profile, key, damage):
    path = configured(tmp_path, profile)
    catalog = json.loads((tmp_path/'common/manifest.json').read_text(encoding='utf-8'))
    png = tmp_path/'common'/catalog['templates'][key]['file']
    if damage == 'missing':png.unlink()
    else:png.write_bytes(b'corrupt')
    with pytest.raises(ConfigError, match=key):
        load(path, profile)


@pytest.mark.parametrize('damage', ['missing_block', 'missing_scene', 'unknown_scene', 'bad_roi', 'small_roi', 'nan', 'bool'])
def test_navigation_configuration_rejects_invalid_settings(tmp_path, damage):
    path = configured(tmp_path, 'shop')
    raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    scenes = raw['startup_navigation']
    if damage == 'missing_block':del raw['startup_navigation']
    elif damage == 'missing_scene':del scenes['navigation_menu']
    elif damage == 'unknown_scene':scenes['unknown'] = scenes['navigation_menu']
    elif damage == 'bad_roi':scenes['navigation_menu']['roi'] = [2300,0,100,100]
    elif damage == 'small_roi':scenes['navigation_menu']['roi'] = [0,0,1,1]
    else:scenes['navigation_menu']['threshold'] = float('nan') if damage == 'nan' else True
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    with pytest.raises(ConfigError, match='startup_navigation'):
        load(path, 'shop')
