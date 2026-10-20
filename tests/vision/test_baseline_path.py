"""Protect the validated baseline backend from non-reference adaptation changes."""
from types import SimpleNamespace
from dataclasses import fields

import cv2
import numpy as np
import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.ports import WindowRef
from e7auto.core.types import Point, Rect, Size, ClientPoint
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository
from e7auto.runtime.context import RuntimeContext
from e7auto.runtime.stop_control import StopController, StopExecution
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.vision.frames import FrameView, CoordinateTransform, frame_view, adapt_frame
from tests.helpers import FakeClock, FakeLogger
from tests.helpers.chaos_game import chaos_config
from tests.helpers.chaos_images import loot_frame
from tests.helpers.paths import ROOT


@pytest.fixture(scope='module')
def setup():
    config=chaos_config();templates=TemplateRepository(config);vision=ChaosVision(config,templates)
    yield config,templates,vision
    vision.close_event_recognition();vision.close_loot_recognition();vision.clear_template_cache()


def read(name):
    raw=cv2.imdecode(np.frombuffer((ROOT/'tests/fixtures/chaos'/name).read_bytes(),np.uint8),1)
    return adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(raw.shape[1],raw.shape[0])))


def forbidden(*args,**kwargs):
    pytest.fail('baseline recognition entered the scaled backend')


def test_full_template_backend_keeps_validated_contract_for_full_and_child_views(setup,monkeypatch):
    config,_,vision=setup;matcher=vision._matcher;frame=read('resolution/contract_baseline_2322.png')
    monkeypatch.setattr(matcher,'_search',forbidden)
    monkeypatch.setattr(matcher,'candidates',forbidden)
    monkeypatch.setattr(matcher.cache,'get',forbidden)
    scene=config.chaos.scenes['contract']
    for view in (frame,frame_view(frame).crop(Rect(0,190,2322,870))):
        hit=matcher.locate_all(view,scene.template_key,scene.region,scene.threshold,
                               scene.structure,limit=1)[0]
        assert hit.bounds==Rect(180,260,200,265)
        assert hit.anchor==ClientPoint(280,392)
        assert hit.confidence==pytest.approx(1.)
        # Ordinary controls and shop/penguin entry matching share this dispatch.
        entry=matcher.match_entry(view,scene.template_key,scene.region,scene.threshold,structure_threshold=scene.structure)
        assert entry.bounds==hit.bounds and entry.anchor==hit.anchor


def test_baseline_specialized_recognition_does_not_use_scaled_scoring(setup,monkeypatch):
    config,templates,vision=setup;matcher=vision._matcher
    monkeypatch.setattr(matcher,'_search',forbidden)
    monkeypatch.setattr(matcher,'candidates',forbidden)
    monkeypatch.setattr(matcher.cache,'get',forbidden)
    monkeypatch.setattr(type(matcher),'scores',staticmethod(forbidden))
    event=vision._events;key='event_torch';item=event.identities[key]
    raw=np.zeros((1306,2322,3),np.uint8);x,y=item.origin;h,w=item.image.shape[:2]
    raw[y:y+h,x:x+w]=item.image
    source=frame_view(raw).crop(event.region)
    hit=event.verify(source,key)
    assert hit is not None and hit.bounds==Rect(x,y,w,h)
    assert event.verify(source,key,previous=hit) is not None
    assert event.verify(source,key,try_reference=False) is not None
    keys=('loot_ominous_crystal','loot_old_sword','loot_lightning')
    assert tuple(hit.key for hit in vision.loot_cards(loot_frame(config,templates,keys)))==keys
    # Centre/ring recognition uses the original glyph and parameters at 1:1.
    vision.begin_auto_recognition(0.)
    vision.auto_state(read('resolution/contract_baseline_2322.png'),.1)
    assert vision._motion_bounds==vision._motion_roi


@pytest.mark.parametrize('name,key',[
    ('resolution/stage_selected_1536.png','stage_selected'),
    ('resolution/hero_ivanna_1536.png','hero_ivanna'),
    ('supply/client_1536_get_loot.png','supply_get_loot'),
    ('resolution/rank_list_baseline.png','hero_xiaochun'),
])
def test_nonreference_failures_stay_on_adapted_backend(setup,monkeypatch,name,key):
    config,_,vision=setup;frame=read(name)
    if key=='hero_xiaochun':
        raw=cv2.resize(frame_view(frame).raw,(1536,864),interpolation=cv2.INTER_AREA)
        frame=FrameView(raw,CoordinateTransform(Size(2322,1306),Size(1536,864)))
    monkeypatch.setattr(vision._matcher.baseline,'match',forbidden)
    monkeypatch.setattr(vision._matcher.baseline,'locate_all',forbidden)
    # A child keeps the complete client's transform; its own dimensions do not route it.
    view=frame_view(frame).crop(config.chaos.scenes[key].region)
    hit=vision.control(view,key)
    assert hit is not None and isinstance(hit.anchor,ClientPoint)


@pytest.mark.parametrize('mode',['slow_success','business_timeout','manual_stop'])
def test_contract_observation_keeps_original_wait_contract(setup,monkeypatch,mode):
    config,_,vision=setup;raw=frame_view(read('resolution/contract_baseline_2322.png')).raw
    clock=FakeClock();control=StopController();captures=[];inputs=[]
    def capture(*args):
        captures.append(True)
        return raw.copy()
    deps=SimpleNamespace(clock=clock,vision=vision,logger=FakeLogger(),
        capture=SimpleNamespace(capture_client=capture),inputs=SimpleNamespace(click=lambda *a:inputs.append(a)))
    runtime=RuntimeContext(config,deps,control,SnapshotPublisher(RuntimeSnapshot.chaos_run('baseline',1),lambda _:None))
    runtime.window=WindowRef(1,'offline','offline.exe')
    runtime.baseline_bounds=Rect(0,0,2322,1306)
    runtime.transform=CoordinateTransform(Size(2322,1306),Size(2322,1306))
    runtime.ensure_window=lambda **kwargs:control.checkpoint()
    flow=ChaosFlow.__new__(ChaosFlow)
    flow.stages=SimpleNamespace(invalidate_start=lambda:None)
    flow.runtime,flow.vision,flow.settings=runtime,vision,config.chaos
    flow._battle_pending=False;flow._map_handoff=None
    original=vision.control
    def slow(frame,key,*args,**kwargs):
        hit=original(frame,key,*args,**kwargs)
        if key=='contract':clock.sleep(.350)
        return hit
    monkeypatch.setattr(vision,'control',slow)
    if mode=='manual_stop':control.request(StopReason.MANUAL_STOP)
    if mode=='slow_success':
        hit=flow._control('contract')
        assert hit.anchor==ClientPoint(280,392)
        assert len(captures)==config.chaos.timing['stable_frames']==3
        assert clock.now==pytest.approx(1.050)
    else:
        with pytest.raises(StopExecution) as error:flow._control('contract',timeout_ms=500)
        assert error.value.reason==(StopReason.MANUAL_STOP if mode=='manual_stop' else StopReason.RECOGNITION_TIMEOUT)
        assert len(captures)==(0 if mode=='manual_stop' else 2)
    assert not inputs
    assert not any(f.name=='evidence' for f in fields(Point))
    assert not hasattr(config,'recognition_budget_ms')
    assert not hasattr(FrameView,'recognition')
