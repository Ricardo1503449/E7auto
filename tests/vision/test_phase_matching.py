"""Real-scale regressions, phase search reference and false-positive controls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from e7auto.core.types import Size, Rect, ClientPoint
from e7auto.features.chaos.stage_selection import StageSelection
from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository, TemplateData
from e7auto.vision.candidates import CandidateCache, sampling_phases
from e7auto.vision.frames import adapt_frame, CoordinateTransform
from e7auto.vision.matching import TemplateMatcher
from tests.helpers.chaos_game import chaos_config
from tests.helpers.paths import ROOT


@pytest.fixture(scope='module')
def setup():
    config=chaos_config();templates=TemplateRepository(config);vision=ChaosVision(config,templates)
    yield config,templates,vision
    vision.close_event_recognition();vision.close_loot_recognition();vision.clear_template_cache()


def read(name):
    path=ROOT/'tests/fixtures/chaos'/name
    raw=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),cv2.IMREAD_COLOR)
    return adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(raw.shape[1],raw.shape[0])))


@pytest.mark.parametrize('name,key',[
    ('resolution/stage_selected_1536.png','stage_selected'),
    ('resolution/hero_ivanna_1536.png','hero_ivanna'),
    ('supply/client_1536_get_loot.png','supply_get_loot'),
])
def test_real_failure_matches_exhaustive_phase_reference(setup,name,key):
    config,_,vision=setup;scene=config.chaos.scenes[key];frame=read(name)
    results=[]
    for exhaustive in (False,True):
        vision._matcher.cache.clear()
        hits=vision._matcher.locate_all(frame,scene.template_key,scene.region,scene.threshold,scene.structure,
                                       limit=1,exhaustive=exhaustive)
        assert len(hits)==1
        hit=hits[0]
        assert hit.confidence>=scene.threshold and hit.structure_confidence>=scene.structure
        assert hit.geometry is not None and isinstance(hit.anchor,ClientPoint)
        results.append(hit)
    assert results[0].bounds==results[1].bounds
    assert results[0].anchor==results[1].anchor
    assert results[0].confidence==pytest.approx(results[1].confidence,abs=2e-6)


def test_stage_selection_confirms_only_the_highlighted_row(setup):
    config,_,vision=setup;frame=read('resolution/stage_selected_1536.png')
    selection=StageSelection.__new__(StageSelection);selection.vision=vision
    for stage in range(1,6):
        selection.stage_entry_key=f'stage_{stage:02d}'
        hit=selection.selected(frame)
        assert (hit is not None)==(stage==3)
        if hit is not None:
            assert 585<=hit.anchor.x<=950 and 750<=hit.anchor.y<=825


def test_ivanna_identity_does_not_select_other_configured_heroes(setup):
    config,_,vision=setup;frame=read('resolution/hero_ivanna_1536.png')
    keys=[key for key in config.chaos.scenes if key.startswith('hero_') and key!='hero_selected_border']
    detected=[key for key in keys if vision.control(frame,key) is not None]
    assert detected==['hero_ivanna']
    hit=vision.control(frame,'hero_ivanna')
    assert 425<=hit.anchor.x<=768 and 355<=hit.anchor.y<=450
    assert vision.control(frame,'recruit_confirm') is None  # Disabled until a hero is selected.


def test_wrong_pages_and_removed_targets_do_not_pass(setup):
    config,_,vision=setup
    for name,key,erase in [
        ('resolution/stage_selected_1536.png','stage_selected',Rect(650,245,810,110)),
        ('resolution/hero_ivanna_1536.png','hero_ivanna',Rect(425,355,150,60)),
    ]:
        frame=read(name);scene=config.chaos.scenes[key]
        raw=frame.raw.copy();raw[erase.y:erase.bottom,erase.x:erase.right]=0
        changed=adapt_frame(raw,frame.transform)
        assert vision.control(changed,key) is None
        for value in (0,127,255):
            assert vision.control(adapt_frame(np.full_like(raw,value),frame.transform),key) is None
    assert vision.control(read('resolution/stage_selected_1536.png'),'hero_ivanna') is None
    assert vision.control(read('resolution/hero_ivanna_1536.png'),'stage_selected') is None


def test_real_roi_motion_does_not_change_sampling_or_click(setup):
    config,_,vision=setup;frame=read('resolution/hero_ivanna_1536.png');scene=config.chaos.scenes['hero_ivanna']
    hits=[]
    for dx,dy in ((0,0),(1,0),(-1,0),(0,1),(0,-1),(5,-3)):
        region=replace(scene.region,x=scene.region.x+dx,y=scene.region.y+dy,width=scene.region.width-dx)
        hits.append(vision.control(frame,'hero_ivanna',region))
    assert all(hit is not None for hit in hits)
    assert len({hit.anchor for hit in hits})==1


def test_fixed_scale_is_independent_of_canvas_rounding_and_hidden_rgb():
    rng=np.random.default_rng(29);pixels=rng.integers(0,255,(47,159,3),np.uint8)
    mask=np.zeros((47,159),np.uint8);mask[3:44,4:156]=255
    data=TemplateData(pixels,mask);cache=CandidateCache();scale=(1536/2322,864/1306)
    alternate=pixels.copy();alternate[mask==0]^=255;other=TemplateData(alternate,mask)
    for phase in sampling_phases(scale):
        a,b=cache.get(data,scale,phase=phase),cache.get(other,scale,phase=phase)
        assert a.scale==scale and a.content_size==(159*scale[0],47*scale[1])
        assert a.offset==(1+phase[0],1+phase[1])
        np.testing.assert_array_equal(a.image,b.image)
        np.testing.assert_array_equal(a.mask,b.mask)


def test_phase_and_score_cache_do_not_change_results_with_eviction_or_threads(setup):
    config,_,vision=setup;frame=read('resolution/hero_ivanna_1536.png');scene=config.chaos.scenes['hero_ivanna']
    def match(_):
        return vision._matcher.locate_all(frame,scene.template_key,scene.region,scene.threshold,scene.structure,limit=1)
    expected=match(None)
    with ThreadPoolExecutor(3) as pool:
        assert all(result==expected for result in pool.map(match,range(6)))
    vision._matcher.cache.clear()
    assert match(None)==expected
    assert vision._matcher.cache.peak_bytes<=vision._matcher.cache.max_bytes


def test_unknown_template_is_still_an_error_on_a_blank_frame():
    class Missing:
        def get(self,key):raise KeyError(key)
    with pytest.raises(KeyError):
        TemplateMatcher(Missing()).match(np.zeros((20,20,3),np.uint8),'unknown',Rect(0,0,20,20),.9)


def test_fractional_coverage_is_pixel_area_without_stretching_colors():
    data=TemplateData(np.full((4,4,3),80,np.uint8),np.full((4,4),255,np.uint8))
    candidate=CandidateCache().get(data,(.5,.5),phase=(.5,.5))
    axis=np.array([0,.5,1,.5,0],np.float32)
    np.testing.assert_array_equal(candidate.mask,axis[:,None]*axis[None,:])
    assert np.all(candidate.image[candidate.mask>0]==80)
    assert candidate.content_size==(2.,2.)


def test_cached_placement_rechecks_current_pixels_and_survives_template_eviction(setup):
    config,_,vision=setup;frame=read('resolution/hero_ivanna_1536.png');scene=config.chaos.scenes['hero_ivanna']
    matcher=vision._matcher
    hit=matcher.locate_all(frame,scene.template_key,scene.region,scene.threshold,scene.structure,limit=1)[0]
    matcher.cache.clear()
    observed=matcher.recheck(frame,hit,scene.threshold,scene.structure)
    assert observed is not None and observed.bounds==hit.bounds and observed.anchor==hit.anchor
    raw=frame.raw.copy();raw[350:420,425:570]=0
    assert matcher.recheck(adapt_frame(raw,frame.transform),hit,scene.threshold,scene.structure) is None
    wrong_scale=adapt_frame(frame.raw,CoordinateTransform(Size(2322,1306),Size(1920,1080)))
    assert matcher.recheck(wrong_scale,hit,scene.threshold,scene.structure) is None


def test_score_preprocessing_is_immutable_budgeted_and_released():
    import gc
    pixels=np.arange(32*48*3,dtype=np.uint8).reshape(32,48,3)
    cache=CandidateCache(2*1024*1024);candidate=cache.get(TemplateData(pixels),(.66,.66))
    prepared=cache.score_data(candidate)
    used=cache.live_bytes
    assert used==candidate.nbytes+prepared.nbytes and used<cache.max_bytes
    assert not prepared.weighted.flags.writeable
    assert cache.score_data(candidate) is prepared
    cache.clear()
    assert cache.live_bytes==used
    del prepared,candidate;gc.collect()
    assert cache.live_bytes==0


def test_native_proposals_recover_a_glyph_missed_by_half_size_shortlist(setup,monkeypatch):
    config,_,vision=setup;original=read('resolution/rank_list_baseline.png')
    raw=cv2.resize(original,(1536,864),interpolation=cv2.INTER_AREA)
    frame=adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(1536,864)))
    scene=config.chaos.scenes['hero_xiaochun'];matcher=vision._matcher;calls=[];propose=matcher._proposals
    def record(*args,**kwargs):
        calls.append(kwargs.get('native',False))
        return propose(*args,**kwargs)
    monkeypatch.setattr(matcher,'_proposals',record)
    found=matcher.locate_all(frame,scene.template_key,scene.region,scene.threshold,scene.structure,limit=1)
    reference=matcher.locate_all(frame,scene.template_key,scene.region,scene.threshold,scene.structure,limit=1,exhaustive=True)
    assert len(found)==len(reference)==1 and found[0].bounds==reference[0].bounds
    assert calls==[False,True]
