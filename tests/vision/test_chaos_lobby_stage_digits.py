"""Real lobby pixels reject other digits without a multi-stage score comparison."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from e7auto.configuration.loader import load_config
from e7auto.core.types import ClientPoint, Size
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import CoordinateTransform, FrameView
from e7auto.vision.matching import TemplateMatcher
from tests.helpers.paths import ROOT


FIXTURE = ROOT / 'tests/fixtures/chaos/lobby_stage_digits.npz'
EVIDENCE = json.loads(FIXTURE.with_suffix('.json').read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def digit_matcher():
    base = load_config(ROOT / 'config/internal.yaml', template_profile='chaos')
    scenes, paths = {}, {}
    for target in range(1, 6):
        config = with_chaos_config(base, target_stage=target)
        scene = config.chaos.scenes[config.chaos.stage_selection['lobby_scenes'][target]]
        scenes[target] = scene
        paths[scene.template_key] = config.template_paths[scene.template_key]
    return TemplateMatcher(TemplateRepository(SimpleNamespace(template_paths=paths))), scenes


@pytest.fixture(scope='module')
def captured_regions():
    with np.load(FIXTURE) as arrays:
        return {key: arrays[key] for key in arrays.files}


@pytest.mark.parametrize('target', range(1, 6))
@pytest.mark.parametrize('sample', EVIDENCE['samples'], ids=lambda sample: sample['id'])
def test_single_stage_digit_on_recorded_pixels(digit_matcher, captured_regions, sample, target):
    matcher, scenes = digit_matcher
    scene = scenes[target]
    frame = FrameView(captured_regions[sample['id']],
                      CoordinateTransform(Size(*EVIDENCE['baseline']), Size(*sample['client_size'])),
                      ClientPoint(*sample['origin']))
    hit = matcher.locate_all(frame, scene.template_key, scene.region,
                             scene.threshold, scene.structure, limit=1)
    assert bool(hit) is (target == sample['expected_stage'])
