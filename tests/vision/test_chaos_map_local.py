"""Triangle-local node matching preserves geometry, ambiguity and map guards."""
import json
from math import floor

import cv2
import numpy as np
import pytest

from e7auto.core.types import Rect, Size
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.vision import ChaosVision, NODE_KINDS
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import adapt_frame, CoordinateTransform
from tests.helpers.chaos_game import chaos_config
from tests.helpers.paths import ROOT


@pytest.fixture
def setup():
    config=chaos_config()
    templates=TemplateRepository(config)
    vision=ChaosVision(config,templates)
    yield vision,config,templates
    vision.close_loot_recognition()


def paste(frame,config,templates,key,x,y):
    data=templates.get(config.chaos.scenes[key].template_key).image
    frame[y:y+len(data),x:x+data.shape[1]]=data
    return Rect(x,y,data.shape[1],len(data))


def map_frame(config,templates):
    frame=np.zeros((1306,2322,3),np.uint8)
    paste(frame,config,templates,'map_title_outer',20,15)
    paste(frame,config,templates,'map_hero',560,1170)
    return frame


@pytest.mark.parametrize('size',[(2322,1306),(1536,864)])
def test_all_seven_types_keep_stable_node_signatures_when_triangles_bounce(setup,size):
    vision,config,templates=setup
    signatures=[]
    for bounce in (-10,0,10):
        frame=map_frame(config,templates)
        for slot,kind in enumerate(NODE_KINDS):
            body=paste(frame,config,templates,'node_'+kind,200+290*slot,520)
            paste(frame,config,templates,'node_triangle',body.x+(body.width-42)//2,450+bounce)
        adapted=adapt_frame(cv2.resize(frame,size,interpolation=cv2.INTER_AREA),CoordinateTransform(Size(2322,1306),Size(*size)))
        view=vision.map_view(adapted)
        assert view is not None and {n.kind for n in view.nodes}==set(NODE_KINDS)
        signatures.append(ChaosFlow._signature(view))
    assert signatures[0]==signatures[1]==signatures[2]


@pytest.mark.parametrize('node_xy,triangle_xy',[
    ((0,260),(40,200)), ((2206,500),(2250,430)), ((1000,1000),(1040,930)),
])
def test_map_edges_clip_search_without_losing_whole_nodes(setup,monkeypatch,node_xy,triangle_xy):
    vision,config,templates=setup
    frame=map_frame(config,templates)
    expected=paste(frame,config,templates,'node_normal',*node_xy)
    paste(frame,config,templates,'node_triangle',*triangle_xy)
    calls=[]
    original=vision._matcher.locate_all
    def locate(frame,key,region,*args,**kwargs):
        if key.startswith('chaos:node_') and key!='chaos:node_triangle':
            origin=config.chaos.regions['map']
            calls.append(region)
        return original(frame,key,region,*args,**kwargs)
    monkeypatch.setattr(vision._matcher,'locate_all',locate)
    view=vision.map_view(frame)
    assert view is not None and len(view.nodes)==1 and view.nodes[0].detection.bounds==expected
    area=config.chaos.regions['map']
    assert len(calls)==7
    assert all(area.x<=r.x<r.right<=area.right and area.y<=r.y<r.bottom<=area.bottom for r in calls)
    assert all(r.width*r.height<area.width*area.height/10 for r in calls)


@pytest.mark.parametrize('side',[-1,1])
def test_horizontal_and_lower_pairing_limits_include_entire_template(setup,side):
    vision,config,templates=setup
    frame=map_frame(config,templates)
    triangle=paste(frame,config,templates,'node_triangle',1000,400)
    image=templates.get(config.chaos.scenes['node_boss'].template_key).image
    h,w=image.shape[:2]
    tx,ty=triangle.x+triangle.width/2,triangle.y+triangle.height/2
    x=int(tx+side*config.chaos.geometry['node_triangle_max_dx']-w/2)
    y=floor(ty+config.chaos.geometry['node_triangle_max_dy'])
    expected=paste(frame,config,templates,'node_boss',x,y)
    view=vision.map_view(frame)
    assert view is not None and view.nodes[0].detection.bounds==expected


@pytest.mark.parametrize('other_kind',['normal','rest'])
def test_two_distinct_nodes_under_one_triangle_are_ambiguous(setup,other_kind):
    vision,config,templates=setup
    frame=map_frame(config,templates)
    paste(frame,config,templates,'node_triangle',800,360)
    paste(frame,config,templates,'node_normal',690,480)
    paste(frame,config,templates,'node_'+other_kind,847,480)
    assert vision.map_view(frame) is None


def test_overlapping_triangle_windows_do_not_duplicate_the_same_node(setup):
    vision,config,templates=setup
    frame=map_frame(config,templates)
    body=paste(frame,config,templates,'node_rest',980,550)
    for x in (985,1065):paste(frame,config,templates,'node_triangle',x,480)
    view=vision.map_view(frame)
    assert view is not None and len(view.nodes)==1 and view.nodes[0].detection.bounds==body


@pytest.mark.parametrize('missing',['triangle','hero','title','node'])
def test_missing_map_evidence_still_rejects_frame(setup,missing):
    vision,config,templates=setup
    frame=map_frame(config,templates)
    if missing!='node':paste(frame,config,templates,'node_normal',700,500)
    if missing!='triangle':paste(frame,config,templates,'node_triangle',740,430)
    if missing=='hero':frame[1120:]=0
    if missing=='title':frame[:140]=0
    assert vision.map_view(frame) is None


def test_unmarked_nodes_do_not_enter_the_available_list(setup):
    vision,config,templates=setup
    frame=map_frame(config,templates)
    marked=paste(frame,config,templates,'node_normal',700,500)
    paste(frame,config,templates,'node_triangle',740,430)
    paste(frame,config,templates,'node_rest',1500,600)
    view=vision.map_view(frame)
    assert view is not None and [n.detection.bounds for n in view.nodes]==[marked]


def test_gray_node_with_a_triangle_does_not_become_selectable(setup):
    vision,config,templates=setup
    frame=map_frame(config,templates)
    manifest=json.loads((ROOT/'assets/templates/chaos/manifest.json').read_text(encoding='utf-8'))
    path=ROOT/'assets/templates/chaos'/manifest['templates']['node_gray_normal']['file']
    gray=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),cv2.IMREAD_UNCHANGED)[:,:,:3]
    frame[500:500+gray.shape[0],700:700+gray.shape[1]]=gray
    paste(frame,config,templates,'node_triangle',740,430)
    assert vision.map_view(frame) is None
