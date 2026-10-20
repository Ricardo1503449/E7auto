"""Real clipped-card regression: selected name can confirm without a full border."""
import cv2
import pytest

from e7auto.core.types import Size
from e7auto.configuration.loader import load_config
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.features.chaos.recruitment import HeroRecruitment
from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import CoordinateTransform, adapt_frame
from tests.helpers.paths import ROOT


@pytest.mark.parametrize('size', [(2322,1306),(1920,1080),(1536,864)])
def test_right_edge_selected_arowell_and_button_confirm_in_same_frame(size,monkeypatch):
    config=with_chaos_config(load_config(ROOT/'config/internal.yaml',template_profile='chaos'))
    vision=ChaosVision(config,TemplateRepository(config))
    recruitment=HeroRecruitment(config.chaos,vision,frame=None,wait=None,tap=None,
                               act=None,control=None,first=None)
    try:
        image=cv2.imread(str(ROOT/'tests/fixtures/chaos/recruitment/right_edge_arowell.png'))
        frame=adapt_frame(cv2.resize(image,size,interpolation=cv2.INTER_AREA),
                          CoordinateTransform(Size(2322,1306),Size(*size)))
        target=vision.control(frame,'hero_arowell')
        assert target is not None
        assert vision.control(frame,'hero_selected_border') is None
        original=vision.control;reads=[]
        def control(current,key,region=None):
            assert current is frame
            reads.append(key)
            assert key not in ('hero_arowell','hero_selected_border')
            return original(current,key,region)
        monkeypatch.setattr(vision,'control',control)
        selected,button=recruitment._selected_button(frame,'arowell',target,'recruit_confirm')
        assert selected.key=='selected_name_arowell' and button.key=='recruit_confirm'
        assert reads.count('selected_name_arowell')==reads.count('recruit_confirm')==1
    finally:
        vision.close_loot_recognition();vision.close_event_recognition()
