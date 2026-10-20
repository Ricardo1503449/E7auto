"""Visible pale marker, dark marker, scenery, shapes and window adaptation."""
import cv2
import numpy as np
import pytest

from e7auto.configuration.loader import load_config
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.features.chaos.text_triangle import bright_triangle
from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository
from e7auto.core.types import Size
from e7auto.vision.frames import adapt_frame, CoordinateTransform
from tests.helpers.paths import ROOT


@pytest.fixture(scope='module')
def detector():
    config=with_chaos_config(load_config(ROOT/'config/internal.yaml',template_profile='chaos'))
    return ChaosVision(config,TemplateRepository(config)), config


@pytest.mark.parametrize('name,expected',[('bright',True),('dark',True),('options',False),('reward',False),('feathers',False)])
@pytest.mark.parametrize('size',[Size(2322,1306),Size(1536,864)])
def test_real_roi_and_scaled_window(detector,name,expected,size):
    vision,config=detector
    frame=np.zeros((1306,2322,3),np.uint8)
    roi=cv2.imread(str(ROOT/'tests/fixtures/chaos/text_triangle'/f'{name}.png'))
    frame[1190:1306,1080:1270]=roi
    raw=frame if size.width==2322 else cv2.resize(frame,(size.width,size.height),interpolation=cv2.INTER_AREA)
    hit=vision.control(adapt_frame(raw,CoordinateTransform(Size(2322,1306),size)),'text_continue')
    assert (hit is not None)==expected
    if hit:
        assert abs(hit.anchor.x-1160*size.width/2322)<=2
        assert abs(hit.anchor.y-(1266 if name=='bright' else 1276)*size.height/1306)<=2


@pytest.mark.parametrize('shape',['flat','circle','rectangle','up','right','chevron','two_down'])
def test_non_triangles_and_multiple_shapes_rejected(detector,shape):
    _,config=detector
    roi=np.full((116,190,3),220,np.uint8)
    ink=(248,248,248)
    if shape=='circle':cv2.circle(roi,(80,75),11,ink,-1)
    if shape=='rectangle':cv2.rectangle(roi,(69,68),(91,83),ink,-1)
    if shape=='up':cv2.fillPoly(roi,[np.array([[69,83],[91,83],[80,68]])],ink)
    if shape=='right':cv2.fillPoly(roi,[np.array([[69,63],[69,85],[84,74]])],ink)
    if shape=='chevron':cv2.polylines(roi,[np.array([[69,68],[80,83],[91,68]])],False,ink,2)
    if shape=='two_down':
        for x in (35,115):cv2.fillPoly(roi,[np.array([[x,68],[x+22,68],[x+11,83]])],ink)
    assert bright_triangle(roi,(23,16),config.chaos.text_triangle) is None


def test_bouncing_marker_is_located_at_each_latest_position(detector):
    vision,_=detector
    roi=cv2.imread(str(ROOT/'tests/fixtures/chaos/text_triangle/bright.png'))
    for dy in (-9,-4,3,8,2,-6):
        moved=cv2.warpAffine(roi,np.float32([[1,0,0],[0,1,dy]]),(190,116),borderMode=cv2.BORDER_REPLICATE)
        frame=np.zeros((1306,2322,3),np.uint8);frame[1190:1306,1080:1270]=moved
        hit=vision.control(frame,'text_continue')
        assert hit is not None and abs(hit.anchor.y-(1266+dy))<=1


def test_contrastless_white_patch_never_authorizes_click(detector):
    vision,_=detector
    frame=np.full((1306,2322,3),248,np.uint8)
    assert vision.control(frame,'text_continue') is None
