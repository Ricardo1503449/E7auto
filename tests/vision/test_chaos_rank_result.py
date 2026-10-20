"""Result-column pairing and real small-client rank icon/digit evidence."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from e7auto.configuration.loader import load_config
from e7auto.core.types import Rect, Point, Size
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.features.chaos.contracts import Detection
from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import CoordinateTransform, adapt_frame
from tests.helpers.paths import ROOT


@pytest.mark.parametrize('left,right,expected', [
    *[([a],[b],(a,b)) for a in range(1,5) for b in range(a+1,6)],
    ([],[3],None),([1],[],None),([1,2],[3],None),([1],[2,3],None),
    ([3],[1],None),([3],[3],None),
])
def test_pair_requires_unique_increasing_values_in_same_frame(left,right,expected):
    regions={'rank_result_before':Rect(1300,335,235,170),'rank_result_after':Rect(1570,335,380,170)}
    token=object()
    def control(frame,key,region=None):
        assert frame is token and key.startswith('rank_value_')
        assert region in regions.values()
        values=left if region==regions['rank_result_before'] else right
        rank=int(key.rsplit('_',1)[1])
        return Detection(key,1.,region,Point(region.x,region.y)) if rank in values else None
    detector=SimpleNamespace(settings=SimpleNamespace(regions=regions),control=control)
    assert ChaosVision.rank_result(detector,token)==expected


@pytest.fixture(scope='module')
def pixel_detector():
    config = with_chaos_config(load_config(ROOT/'config/internal.yaml', template_profile='chaos'))
    vision = ChaosVision(config, TemplateRepository(config))
    yield vision, config
    vision.close_loot_recognition()
    vision.close_event_recognition()


@pytest.mark.parametrize('case,expected', [
    ('original', (4,5)), ('without_max', (4,5)),
    ('missing_digit', None), ('missing_icon', None),
])
def test_real_small_client_rank_five_requires_icon_and_digit(pixel_detector, case, expected):
    vision, config = pixel_detector
    path = ROOT/'tests/fixtures/chaos/resolution/rank_result_4_to_5_1536.png'
    raw = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    # These bounds refer to the original 1536x864 failure capture, without resampling.
    if case == 'without_max':
        raw[252:298,1134:1235] = 0
    elif case == 'missing_digit':
        raw[252:298,1107:1134] = 0
    elif case == 'missing_icon':
        raw[252:298,1065:1106] = 0
    transform = CoordinateTransform(config.baseline_client_size, Size(raw.shape[1], raw.shape[0]))
    assert vision.rank_result(adapt_frame(raw, transform)) == expected


def rank_three_sample(size, missing=None):
    path = ROOT/'tests/fixtures/chaos/resolution/rank_result_2_to_3_2322.png'
    raw = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert raw.shape == (1306,2322,3)
    # Measured source-pixel bounds in this full-client regression capture.
    if missing == 'digit':
        raw[385:451,1673:1720] = (16,12,9)
    elif missing == 'icon':
        raw[384:450,1605:1672] = (16,12,9)
    if size != (2322,1306):
        raw = cv2.resize(raw,size,interpolation=cv2.INTER_AREA if size[0]<2322 else cv2.INTER_LINEAR)
    return adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(*size)))


@pytest.mark.parametrize('size', [(2322,1306),(1536,864),(1920,1080),(2304,1296),(2560,1440)])
def test_real_rank_three_replacement_keeps_unique_two_to_three(pixel_detector, size):
    vision, config = pixel_detector
    scene = config.chaos.scenes['rank_value_3']
    assert (scene.threshold,scene.structure) == (.94,.8)
    assert vision.rank_result(rank_three_sample(size)) == (2,3)


@pytest.mark.parametrize('missing', ['digit','icon'])
@pytest.mark.parametrize('size', [(2322,1306),(1536,864)])
def test_rank_three_replacement_rejects_missing_evidence(pixel_detector, missing, size):
    vision, _ = pixel_detector
    assert vision.rank_result(rank_three_sample(size,missing)) is None
