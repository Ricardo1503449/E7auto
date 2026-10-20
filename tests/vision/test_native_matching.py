from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import gc
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from e7auto.core.types import Point, Rect, Size, ClientPoint, ClientRect
from e7auto.resources.templates import TemplateData, TemplateRepository
from e7auto.vision.candidates import CandidateCache, sampling_phases
from e7auto.vision.frames import CoordinateTransform, adapt_frame, frame_view
from e7auto.vision.matching import TemplateMatcher
from tests.helpers.chaos_game import chaos_config
from tests.helpers.paths import ROOT


def data(seed=1):
    rng=np.random.default_rng(seed)
    image=rng.integers(40,240,(32,48,3),dtype=np.uint8)
    mask=np.zeros((32,48),np.uint8);mask[2:-2,3:-3]=255
    return TemplateData(image,mask)


def test_candidate_phases_are_bounded_and_identity_is_single():
    assert sampling_phases((1.,1.))==((0.,0.),)
    assert len(set(sampling_phases((1536/2322,864/1306))))==16


def test_nested_crops_keep_raw_pixels_and_global_origin():
    raw=np.arange(90*160*3,dtype=np.uint8).reshape(90,160,3)
    frame=adapt_frame(raw,CoordinateTransform(Size(2322,1306),Size(160,90)))
    outer=frame.crop(Rect(400,200,1200,800))
    inner=Rect(500,300,200,200)
    direct,nested=frame.crop(inner),outer.crop(inner)
    assert direct.origin==nested.origin
    np.testing.assert_array_equal(direct.raw,nested.raw)
    assert np.shares_memory(direct.raw,raw)
    with pytest.raises(ValueError,match='ROI outside frame'):
        frame.crop(Rect(-100,-100,4000,3000))
    clipped=frame.crop(Rect(-100,-100,4000,3000),clip=True)
    assert clipped.bounds==ClientRect(0,0,160,90)


@pytest.mark.parametrize('size',[(160,90),(106,60),(320,180)])
def test_strict_and_clipped_regions_keep_parent_geometry(size):
    transform=CoordinateTransform(Size(160,90),Size(*size))
    raw=np.arange(size[0]*size[1]*3,dtype=np.uint8).reshape(size[1],size[0],3)
    frame=frame_view(adapt_frame(raw,transform))
    outer=frame.crop(Rect(40,20,80,50))
    inner=Rect(50,30,20,10)
    direct,nested=frame.crop(inner),outer.crop(inner)
    assert direct.bounds==nested.bounds==transform.rect(inner)
    assert nested.transform==transform and np.shares_memory(nested.raw,raw)
    np.testing.assert_array_equal(direct.raw,nested.raw)
    point=ClientPoint(nested.bounds.x,nested.bounds.y)
    assert transform.point(point)==point
    for method in (frame.region,frame.crop):
        with pytest.raises(ValueError,match='ROI outside frame'):
            method(Rect(-1,0,161,90))
    outside_child=Rect(10,10,100,40)
    with pytest.raises(ValueError,match='ROI outside frame'):
        outer.crop(outside_child)
    clipped=outer.crop(outside_child,clip=True)
    assert clipped.bounds==outer.region(outside_child,clip=True)
    assert clipped.transform==transform
    np.testing.assert_array_equal(clipped.raw,frame.crop(clipped.bounds).raw)
    b=outer.bounds
    for request in (ClientRect(b.x-20,b.y,5,5),ClientRect(b.right+20,b.y,5,5),
                    ClientRect(b.x,b.y-20,5,5),ClientRect(b.x,b.bottom+20,5,5)):
        with pytest.raises(ValueError,match='ROI outside frame'):
            outer.crop(request)
        empty=outer.crop(request,clip=True)
        assert empty.raw.size==0 and empty.transform==transform
        assert b.x<=empty.origin.x<=b.right and b.y<=empty.origin.y<=b.bottom
    for clip in (False,True):
        with pytest.raises(ValueError,match='Negative region size'):
            frame.crop(Rect(10,10,-1,5),clip=clip)


@pytest.mark.parametrize('size',[(160,90),(106,60),(320,180)])
def test_matcher_rejects_implicit_truncation_and_incomplete_clipped_search(size):
    templates=SimpleNamespace(get=lambda _:data())
    matcher=TemplateMatcher(templates)
    transform=CoordinateTransform(Size(160,90),Size(*size))
    raw=np.random.default_rng(71).integers(20,230,(size[1],size[0],3),dtype=np.uint8)
    frame=frame_view(adapt_frame(raw,transform))
    outside=Rect(-20,0,80,60)
    with pytest.raises(ValueError,match='ROI outside frame'):
        matcher.match(frame,'sample',outside,.95)
    with pytest.raises(ValueError,match='ROI outside frame'):
        matcher.locate_all(frame,'sample',outside,.95,.8)
    # Explicit edge windows can be empty or too small, but cannot be accepted
    # as evidence for a full template.
    for roi in (Rect(-10,-10,12,12),Rect(180,100,10,10)):
        clipped=frame.crop(roi,clip=True)
        assert matcher.match(clipped,'sample',clipped.bounds,.95) is None
        assert matcher.locate_all(clipped,'sample',clipped.bounds,.95,.8)==()


def test_recheck_clips_only_ignored_candidate_padding_at_frame_edge():
    original=data();cache=CandidateCache();scale=(2/3,2/3)
    candidate=cache.get(original,scale);w,h=candidate.size
    raw=np.zeros((40,60,3),np.uint8)
    raw[:h-1,:w-1]=candidate.image[1:,1:]
    frame=frame_view(adapt_frame(raw,CoordinateTransform(Size(90,60),Size(60,40))))
    matcher=TemplateMatcher(SimpleNamespace(get=lambda _:original),cache)
    evidence=matcher.observation('item',1.,1.,-1,-1,candidate,frame.bounds)
    assert evidence is not None
    observed=matcher.recheck(frame,evidence,.97,.8)
    assert observed is not None and observed.bounds==evidence.bounds
    # Removing retained foreground must never be treated as harmless padding.
    incomplete=frame.crop(ClientRect(6,0,54,40))
    assert matcher.recheck(incomplete,evidence,.97,.8) is None


def test_cache_is_lazy_thread_safe_immutable_and_releases_evicted_variants():
    cache=CandidateCache(2*1024*1024);original=data()
    assert cache.generations==cache.live_bytes==0
    with ThreadPoolExecutor(4) as pool:
        candidates=list(pool.map(lambda _:cache.get(original,(31/48,21/32)),range(8)))
    assert cache.generations==1 and all(c is candidates[0] for c in candidates)
    candidate=candidates.pop();candidates.clear()
    assert not candidate.image.flags.writeable and not candidate.mask.flags.writeable
    used=cache.live_bytes
    cache.clear()
    assert cache.live_bytes==used  # An active reader survives eviction, still counted.
    del candidate;gc.collect()
    assert cache.live_bytes==0 and cache.peak_bytes<=cache.max_bytes


def test_cache_budget_fails_before_generation_and_effective_masks_do_not_alias():
    original=data();cache=CandidateCache(100)
    with pytest.raises(ValueError,match='budget'):
        cache.get(original,(31/48,21/32))
    assert cache.generations==0
    cache=CandidateCache()
    mask=original.mask.copy();mask[6:12]=0
    body=TemplateData(original.image,mask)
    assert cache.get(original,(31/48,21/32)) is not cache.get(body,(31/48,21/32))


def test_hidden_rgb_cannot_bleed_into_scaled_foreground():
    original=data();pixels=original.image.copy();pixels[original.mask==0]=255-original.image[original.mask==0]
    alternate=TemplateData(pixels,original.mask)
    cache=CandidateCache()
    for size in ((31,21),(65,45)):
        a,b=cache.get(original,(size[0]/48,size[1]/32)),cache.get(alternate,(size[0]/48,size[1]/32))
        np.testing.assert_array_equal(a.image,b.image)
        np.testing.assert_array_equal(a.mask,b.mask)
    identity=cache.get(original,(1.,1.))
    assert identity.image is original.image and identity.mask is original.mask


@pytest.mark.parametrize('actual',[Size(1536,864),Size(1920,1080),Size(2322,1306),Size(3072,1728)])
def test_client_points_are_never_rescaled(actual):
    transform=CoordinateTransform(Size(2322,1306),actual)
    for x in (0,1,actual.width//2,actual.width-1):
        point=ClientPoint(x,actual.height//2)
        assert transform.point(point) is point
    assert transform.point(Point(1161,653))==ClientPoint(round(actual.width/2),round(actual.height/2))


@pytest.fixture(scope='module')
def supply():
    config=chaos_config();templates=TemplateRepository(config)
    image=cv2.imdecode(np.frombuffer((ROOT/'tests/fixtures/chaos/supply/client_1536_get_loot.png').read_bytes(),np.uint8),1)
    return config,templates,image


def test_real_supply_roi_invariance_cache_eviction_and_negative_frames(supply):
    config,templates,image=supply
    transform=CoordinateTransform(config.baseline_client_size,Size(1536,864))
    frame=adapt_frame(image,transform)
    scene=config.chaos.scenes['supply_get_loot']
    matcher=TemplateMatcher(templates)
    hits=[]
    for roi in (scene.region,replace(scene.region,y=scene.region.y-1),replace(scene.region,y=scene.region.y+1),Rect(1500,600,822,400)):
        hit=matcher.match_entry(frame,scene.template_key,roi,scene.threshold,structure_threshold=scene.structure)
        assert hit is not None and hit.structure_confidence>=scene.structure
        hits.append(hit)
        matcher.cache.clear()
    assert len({h.bounds for h in hits})==1
    assert len({h.anchor for h in hits})==1
    assert max(h.confidence for h in hits)-min(h.confidence for h in hits)<2e-6
    assert hits[0].anchor==ClientPoint(1182,498)
    for raw in (np.zeros_like(image),np.full_like(image,127),np.roll(image,500,axis=0)):
        assert matcher.match_entry(adapt_frame(raw,transform),scene.template_key,scene.region,scene.threshold,
                                   structure_threshold=scene.structure) is None


def test_multiple_candidates_do_not_create_extra_objects():
    original=data();cache=CandidateCache();variant=cache.get(original,(2/3,2/3))
    w,h=variant.size
    image=np.zeros((100,150,3),np.uint8)
    image[20:20+h,30:30+w]=variant.image;image[60:60+h,90:90+w]=variant.image
    frame=adapt_frame(image,CoordinateTransform(Size(225,150),Size(150,100)))
    matcher=TemplateMatcher(SimpleNamespace(get=lambda _:original),cache)
    hits=matcher.locate_all(frame,'item',Rect(0,0,225,150),.97,.8)
    assert len(hits)==2
    cache.clear()
    assert matcher.locate_all(frame,'item',Rect(0,0,225,150),.97,.8)==hits


@pytest.mark.parametrize('floating',[False,True])
def test_exact_location_scores_match_opencv_equations(floating):
    original=data()
    candidate=CandidateCache().get(original,(31/48,21/32)) if floating else original
    rng=np.random.default_rng(19)
    patch=np.clip(candidate.image.astype(np.int16)+rng.integers(-10,11,candidate.image.shape),0,255).astype(np.uint8)
    scores=TemplateMatcher.scores(patch,candidate,0.,0.,force_color=True)
    assert scores is not None
    color=1-cv2.matchTemplate(patch,candidate.image,cv2.TM_SQDIFF_NORMED,mask=candidate.mask)[0,0]
    structure=cv2.matchTemplate(patch,candidate.image,cv2.TM_CCOEFF_NORMED,mask=candidate.mask)[0,0]
    assert scores[0][0,0]==pytest.approx(color,abs=2e-6)
    assert scores[1][0,0]==pytest.approx(structure,abs=2e-6)
    assert TemplateMatcher.scores(np.zeros_like(patch),candidate,.9,.8,force_color=True) is None


@pytest.mark.parametrize('size',[(2322,1306),(1920,1080),(1536,864),(3072,1728)])
def test_compound_loot_uses_coherent_parent_geometry(supply,size):
    from e7auto.features.chaos.vision import ChaosVision
    from tests.helpers.chaos_images import loot_frame
    config,templates,_=supply
    detector=ChaosVision(config,templates)
    keys=('loot_ominous_crystal','loot_old_sword','loot_lightning')
    raw=cv2.resize(loot_frame(config,templates,keys),size,interpolation=cv2.INTER_AREA if size[0]<2322 else cv2.INTER_LINEAR)
    frame=adapt_frame(raw,CoordinateTransform(config.baseline_client_size,Size(*size)))
    try:
        hits=detector.loot_cards(frame)
        assert hits is not None and tuple(hit.key for hit in hits)==keys
        assert all(isinstance(hit.anchor,ClientPoint) for hit in hits)
        assert all(hit.bounds.x<=hit.anchor.x<hit.bounds.right and hit.bounds.y<=hit.anchor.y<hit.bounds.bottom for hit in hits)
        detector.clear_template_cache()
        assert detector._matcher.cache.live_bytes==0
    finally:
        detector.close_loot_recognition()
        detector.close_event_recognition()
