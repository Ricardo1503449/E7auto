"""Both client branches share state rules; baseline keeps its fixed geometry."""
import cv2
import numpy as np
import pytest

from e7auto.core.types import ClientPoint, Size
from e7auto.features.chaos.auto_motion import AutoIconState
from e7auto.features.chaos.contracts import AutoState
from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import CoordinateTransform, adapt_frame
from tests.helpers.chaos_game import chaos_config
from tests.helpers.chaos_images import auto_icon_image
from tests.helpers.paths import ROOT


@pytest.fixture(scope='module')
def setup():
    config=chaos_config();templates=TemplateRepository(config)
    glyph=templates.get(config.chaos.scenes['auto_static'].template_key).image
    p=config.chaos.auto_motion;_,_,w,h=p['roi'];x,y=p['template_offset']
    base=np.zeros((h,w,3),np.uint8);base[y:h,x:w]=glyph[:h-y,:w-x]
    return config,templates,base


def full_frame(setup,image):
    x,y,w,h=setup[0].chaos.auto_motion['roi']
    frame=np.zeros((1306,2322,3),np.uint8);frame[y:y+h,x:x+w]=image
    return frame


def test_baseline_uses_shared_rule_without_localization_or_scaled_candidates(setup,monkeypatch):
    config,templates,base=setup;vision=ChaosVision(config,templates)
    def forbidden(*a,**kw):pytest.fail('baseline entered non-reference localization')
    monkeypatch.setattr(AutoIconState,'_locate_center',forbidden)
    monkeypatch.setattr(vision._matcher,'candidates',forbidden)
    vision.begin_auto_recognition(0.)
    assert vision.auto_state(full_frame(setup,auto_icon_image(base,45)),0.) is AutoState.ROTATING
    assert type(vision._motion) is AutoIconState and not vision._motion.localize
    vision.begin_auto_recognition(1.)
    for i in range(3):state=vision.auto_state(full_frame(setup,base),1+i*.1)
    assert state is AutoState.STATIC
    x,y,w,h=config.chaos.auto_motion['roi']
    assert vision.auto_anchor()==ClientPoint(x+w//2,y+h//2)
    assert vision.auto_recognition_summary()['method']=='static_reference_ring'
    assert vision.auto_state(np.zeros((1306,2322,3),np.uint8),3.) is AutoState.UNKNOWN
    assert vision.auto_anchor() is None


@pytest.mark.parametrize('filename',['client_1536_170131.png','client_1536_170345.png'])
def test_real_nonreference_offset_confirms_immediately_and_geometry_switch_clears_off_evidence(setup,filename):
    config,templates,base=setup;vision=ChaosVision(config,templates)
    path=ROOT/'tests/fixtures/chaos/auto_motion'/filename
    raw=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),1)
    frame=adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(1536,864)))
    assert vision.auto_state(frame,0.) is AutoState.ROTATING
    assert type(vision._motion) is AutoIconState and vision._motion.localize
    assert vision.auto_anchor() is not None
    assert vision.auto_state(full_frame(setup,base),.1) is AutoState.UNKNOWN
    assert not vision._motion.localize
    assert vision.auto_recognition_summary()['static_samples']==1
    vision.clear_template_cache()


@pytest.mark.parametrize('size',[(2322,1306),(1920,1080),(1536,864)])
def test_real_off_reference_never_reports_active_at_supported_sizes(setup,size):
    config,templates,_=setup;vision=ChaosVision(config,templates)
    path=ROOT/'tests/fixtures/chaos/auto_motion/reference_off.png'
    raw=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),1)
    if size!=(2322,1306):raw=cv2.resize(raw,size,interpolation=cv2.INTER_AREA)
    frame=adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(*size)))
    states=[vision.auto_state(frame,i*.1) for i in range(3)]
    assert states == [AutoState.UNKNOWN, AutoState.UNKNOWN, AutoState.STATIC]
    assert abs(vision.auto_recognition_summary()['reference_angle_deg'])<10
