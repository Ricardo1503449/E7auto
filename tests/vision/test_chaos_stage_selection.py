from types import SimpleNamespace

import numpy as np
import pytest

from e7auto.core.observations import Observation
from e7auto.core.types import Point, Rect, Size
from e7auto.features.chaos.configuration import Scene
from e7auto.features.chaos.contracts import Detection
from e7auto.features.chaos.vision import ChaosVision
from e7auto.features.chaos.stage_selection import StageSelection
from e7auto.resources.templates import TemplateData
from e7auto.vision.frames import CoordinateTransform, adapt_frame
from e7auto.vision.matching import TemplateMatcher


def detector_with_recorded_search(height=146):
    region=Rect(960,20,1300,1060)
    scene=Scene('border',region,.97,.8,None)
    templates=SimpleNamespace(get=lambda _:SimpleNamespace(image=np.zeros((height,1199,3),np.uint8)))
    matcher=TemplateMatcher(templates);calls=[]
    def locate(frame,key,roi,threshold,structure_threshold,*,limit):
        calls.append((frame,key,roi,threshold,structure_threshold,limit))
        bounds=Rect(roi.x,roi.y,1199,height)
        return (Observation(key,1.,bounds,Point(bounds.x+599,bounds.y+height//2)),)
    matcher.locate_all=locate
    vision=ChaosVision.__new__(ChaosVision)
    vision.settings=SimpleNamespace(scenes={'stage_selected':scene})
    vision._templates=templates;vision._matcher=matcher
    return vision,calls,region


@pytest.mark.parametrize('y',[20,96,422,1007])
@pytest.mark.parametrize('height',[100,146,180])
def test_stage_band_keeps_every_containing_border_and_returns_global_bounds(y,height):
    vision,calls,region=detector_with_recorded_search(height)
    entry=Detection('stage_03',1.,Rect(1024,y,110,73),Point(1079,y+36))
    hits=vision.stage_highlights(np.zeros((1306,2322,3),np.uint8),entry)
    source,key,local,threshold,structure,limit=calls[0]
    band=local
    assert source.shape==(1306,2322,3)
    assert band.x==region.x and band.width==region.width and band.height<region.height
    assert region.y<=band.y<band.bottom<=region.bottom
    for border_y in range(max(region.y,entry.bounds.bottom-height),min(entry.bounds.y,region.bottom-height)+1):
        assert band.y<=border_y and border_y+height<=band.bottom
    assert hits[0].bounds==Rect(band.x,band.y,1199,height)
    assert (key,threshold,structure,limit)==('border',.97,.8,12)


def test_scaled_stage_bands_preserve_raw_pixels_and_global_coordinates():
    vision,calls,region=detector_with_recorded_search()
    raw=np.zeros((864,1536,3),np.uint8)
    raw[:,:,0]=np.arange(1536,dtype=np.uint16)%255
    frame=adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(1536,864)))
    for y in [96,748,259]:
        bounds=frame.transform.rect(Rect(1024,y,110,73))
        entry=Detection('stage_01',1.,bounds,frame.transform.point(Point(1079,y+36)))
        vision.stage_highlights(frame,entry)
    assert all(np.shares_memory(call[0].raw,raw) for call in calls)
    assert len({call[2].y for call in calls})==3
    assert all(call[0].origin==Point(0,0) for call in calls)


@pytest.mark.parametrize('bounds',[
    Rect(1024,19,110,73),Rect(1024,1008,110,73),Rect(959,96,110,73),
    Rect(2200,96,110,73),Rect(960,96,1200,73),Rect(1024,96,110,147),
])
def test_stage_highlight_rejects_entries_outside_search_or_larger_than_border(bounds):
    vision,calls,_=detector_with_recorded_search()
    entry=Detection('stage_01',1.,bounds,Point(bounds.x,bounds.y))
    assert vision.stage_highlights(np.zeros((1306,2322,3),np.uint8),entry)==()
    assert calls==[]


def test_baseline_stage_deduplicates_before_filtering_containing_borders():
    # Repeated horizontal pixels admit overlapping placements. Early containment
    # filtering changes which placements survive suppression (one vs two).
    rng=np.random.default_rng(901)
    raw=rng.integers(20,230,(120,140,3),dtype=np.uint8)
    rows=rng.integers(20,230,(30,1,3),dtype=np.uint8)
    raw[40:70,10:110]=np.repeat(rows,100,axis=1)
    template=TemplateData(np.repeat(rows,30,axis=1).copy())
    region=Rect(0,0,140,120)
    scene=Scene('border',region,.99,.99,None)
    vision=ChaosVision.__new__(ChaosVision)
    vision.settings=SimpleNamespace(scenes={'stage_selected':scene})
    vision._templates=SimpleNamespace(get=lambda _:template)
    vision._matcher=TemplateMatcher(vision._templates)
    entry=Detection('stage_03',1.,Rect(37,48,4,5),Point(39,50))
    button=Detection('stage_confirm',1.,Rect(1,90,10,10),Point(6,95))
    vision.control=lambda frame,key: {'stage_03':entry,'stage_confirm':button}.get(key)
    selection=StageSelection.__new__(StageSelection)
    selection.vision=vision;selection.stage_entry_key='stage_03'
    borders=vision.stage_highlights(raw,entry)
    # Search must retain non-containing candidates until after deduplication.
    assert any(border.bounds.right<entry.bounds.right for border in borders)
    assert selection.selected(raw)==button
