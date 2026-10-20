from dataclasses import replace
from threading import Barrier, current_thread, local
import numpy as np
import pytest

from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository
from tests.helpers.chaos_game import chaos_config
from tests.helpers.chaos_images import loot_frame

KEYS = ('loot_ominous_crystal','loot_old_sword','loot_lightning')


def test_anchor_fingerprint_reuses_only_complete_unchanged_area(setup, monkeypatch):
    vision, config, templates = setup
    frame = loot_frame(config, templates, KEYS)
    scene = config.chaos.scenes['loot_reroll_anchor']
    original = vision._matcher.locate_all
    calls = []
    def locate(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(vision._matcher, 'locate_all', locate)
    vision.begin_loot_recognition()
    first = vision._loot._anchors(frame, scene)
    assert len(first) == 3
    assert vision._loot._anchors(frame.copy(), scene) == first
    assert len(calls) == 1 and vision._loot.stats['anchor_cache_hits'] == 1
    changed = frame.copy()
    changed[scene.region.y, scene.region.x] ^= 1
    assert vision._loot._anchors(changed, scene) == first
    assert len(calls) == 2
    extra = frame.copy()
    anchor = templates.get(scene.template_key).image
    extra[987:987+anchor.shape[0], 800:800+anchor.shape[1]] = anchor
    assert len(vision._loot._anchors(extra, scene)) == 4
    assert vision._loot._anchor_cache is None
    assert vision._loot._anchors(np.zeros_like(frame), scene) == ()
    assert len(calls) == 4
    assert vision._loot._anchors(frame, scene) == first
    vision.reset_temporal()
    assert vision._loot._anchors(frame, scene) == first
    assert len(calls) == 6
    vision.end_loot_recognition()
    assert vision._loot._anchor_cache is None


def test_scaled_anchor_fingerprint_and_scene_change(setup, monkeypatch):
    import cv2
    from e7auto.core.types import Size
    from e7auto.vision.frames import FrameView, CoordinateTransform
    vision, config, templates = setup
    raw = cv2.resize(loot_frame(config, templates, KEYS), (1536, 864), interpolation=cv2.INTER_AREA)
    transform = CoordinateTransform(Size(2322, 1306), Size(1536, 864))
    scene = config.chaos.scenes['loot_reroll_anchor']
    original = vision._matcher.locate_all
    calls = []
    def locate(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(vision._matcher, 'locate_all', locate)
    vision.begin_loot_recognition()
    first = vision._loot._anchors(FrameView(raw, transform), scene)
    assert len(first) == 3
    assert vision._loot._anchors(FrameView(raw.copy(), transform), scene) == first
    assert len(calls) == 1
    stricter = replace(scene, threshold=scene.threshold+.01)
    assert vision._loot._anchors(FrameView(raw, transform), stricter) == first
    assert len(calls) == 2
    templates._templates[scene.template_key] = replace(templates.get(scene.template_key))
    assert vision._loot._anchors(FrameView(raw, transform), stricter) == first
    assert len(calls) == 3


@pytest.fixture
def setup():
    config=chaos_config()
    templates=TemplateRepository(config)
    vision=ChaosVision(config,templates)
    yield vision,config,templates
    vision.close_loot_recognition()


def test_three_frames_reuse_candidates_but_verify_every_current_icon_and_name(setup):
    vision,config,templates=setup
    vision.begin_loot_recognition()
    for _ in range(3):
        assert tuple(h.key for h in vision.loot_cards(loot_frame(config,templates,KEYS)))==KEYS
    summary=vision.end_loot_recognition()
    assert summary['index_queries']==3 and summary['cache_checks']==6
    assert summary['candidates_checked']==9 and summary['frames']==3
    assert summary['parallel_batches']==3 and summary['card_jobs']==9
    assert vision._loot.cache==[None]*3


def test_september23_loot_is_indexed_verified_and_cached(setup):
    vision,config,templates=setup
    keys=('loot_alchemy_flask','loot_sealed_letter','loot_temple_investigation_badge')
    assert [config.chaos.loot_priorities[key] for key in keys]==[1,1,2]
    assert set(keys)<=set(item.key for item in vision._loot.identities)
    frame=loot_frame(config,templates,keys)
    vision.begin_loot_recognition()
    for _ in range(3):
        assert tuple(hit.key for hit in vision.loot_cards(frame))==keys
    stats=vision.end_loot_recognition()
    assert stats['index_queries']==3 and stats['cache_checks']==6
    assert stats.get('fallback_cards',0)==0


def test_missing_anchors_never_authorize_cards_or_query_index(setup):
    vision,config,templates=setup
    frame=loot_frame(config,templates,KEYS)
    frame[950:1090]=0
    vision.begin_loot_recognition()
    assert vision.loot_cards(frame) is None
    assert vision.end_loot_recognition()['index_queries']==0
    assert vision._loot._pool is None


def test_three_workers_overlap_without_mutating_owner_cache_or_totals(setup,monkeypatch):
    vision,config,templates=setup
    barrier=Barrier(3)
    original=vision._loot._recognize_card
    def work(card,anchor,index):
        assert current_thread().name.startswith('chaos-loot')
        assert not card.raw.flags.writeable
        barrier.wait(timeout=5)
        result=original(card,anchor,index)
        assert vision._loot.cache==[None]*3
        assert vision._loot.stats['candidates_checked']==0
        return result
    monkeypatch.setattr(vision._loot,'_recognize_card',work)
    vision.begin_loot_recognition()
    assert tuple(h.key for h in vision.loot_cards(loot_frame(config,templates,KEYS)))==KEYS
    summary=vision.end_loot_recognition()
    assert summary['candidates_checked']==3 and summary['index_queries']==3
    assert summary['parallel_batches']==1 and summary['card_jobs']==3
    assert summary['cards_wall_ms']>0
    assert not vision._loot.features.flags.writeable
    assert all(not item.icon.image.flags.writeable for item in vision._loot.identities)


def test_pool_is_reused_across_sessions_and_closed_idempotently(setup):
    vision,config,templates=setup
    frame=loot_frame(config,templates,KEYS)
    assert vision.loot_cards(frame)
    pool=vision._loot._pool
    threads=tuple(pool._threads)
    assert 1<=len(threads)<=3
    assert vision.loot_cards(frame.copy())
    assert vision._loot._pool is pool
    vision.close_loot_recognition()
    vision.close_loot_recognition()
    assert vision._loot._pool is None
    assert all(not thread.is_alive() for thread in threads)
    assert vision._loot.cache==[None]*3


def test_worker_failure_drains_other_workers_and_discards_all_results(setup,monkeypatch):
    vision,config,templates=setup
    barrier=Barrier(3)
    original=vision._loot._recognize_card
    completed=[]
    def work(card,anchor,index):
        barrier.wait(timeout=5)
        if anchor.x<800:raise RuntimeError('card worker failed')
        result=original(card,anchor,index)
        completed.append(result[0].key)
        return result
    monkeypatch.setattr(vision._loot,'_recognize_card',work)
    vision.begin_loot_recognition()
    with pytest.raises(RuntimeError,match='card worker failed'):
        vision.loot_cards(loot_frame(config,templates,KEYS))
    assert set(completed)==set(KEYS[1:])
    assert vision._loot.cache==[None]*3 and vision._loot.layout is None
    assert vision.end_loot_recognition()['candidates_checked']==0


def test_changed_card_breaks_stability_then_rediscovers_only_that_slot(setup):
    vision,config,templates=setup
    vision.begin_loot_recognition()
    assert vision.loot_cards(loot_frame(config,templates,KEYS))
    changed=('loot_ancient_key',*KEYS[1:])
    frame=loot_frame(config,templates,changed)
    assert vision.loot_cards(frame) is None
    assert tuple(h.key for h in vision.loot_cards(frame.copy()))==changed
    summary=vision.end_loot_recognition()
    assert summary['index_queries']==4 and summary['cache_failures']==1


def test_name_corruption_is_not_hidden_by_cached_icon(setup):
    vision,config,templates=setup
    frame=loot_frame(config,templates,KEYS)
    vision.begin_loot_recognition()
    assert vision.loot_cards(frame)
    frame[460:530,354:814]=0
    assert vision.loot_cards(frame) is None
    assert vision.end_loot_recognition()['cache_failures']==1


def test_icon_corruption_breaks_cached_evidence_without_same_frame_rediscovery(setup):
    vision,config,templates=setup
    frame=loot_frame(config,templates,KEYS)
    vision.begin_loot_recognition()
    assert vision.loot_cards(frame)
    frame[240:450,414:754]=0
    assert vision.loot_cards(frame) is None
    summary=vision.end_loot_recognition()
    assert summary['index_queries']==3 and summary['cache_failures']==1


def test_name_window_expands_only_within_its_own_card(setup):
    vision,config,templates=setup
    frame=loot_frame(config,templates,KEYS)
    for slot,key in enumerate(KEYS):
        spec=config.chaos.loot_recognition['template_regions'][key]
        x,y,w,h=spec['name']
        data=templates.get(config.chaos.scenes[key].template_key).image[y:y+h,x:x+w].copy()
        ax=round(548+580.5*slot)
        frame[455:535,ax-159:ax+231]=0
        x=ax+spec['origin'][0]+x+15;y=987+spec['origin'][1]+y
        frame[y:y+h,x:x+w]=data
    vision.begin_loot_recognition()
    assert tuple(h.key for h in vision.loot_cards(frame))==KEYS
    assert vision.end_loot_recognition()['name_expansions']==3


def test_longer_name_cannot_pass_as_matching_substring(setup):
    vision,config,templates=setup
    frame=loot_frame(config,templates,KEYS)
    # Extra bright glyph-like material outside the complete first name.
    frame[475:490,767:775]=230
    assert vision.loot_cards(frame) is None


def test_recovery_and_reentry_clear_all_candidates(setup):
    vision,config,templates=setup
    frame=loot_frame(config,templates,KEYS)
    vision.begin_loot_recognition()
    assert vision.loot_cards(frame)
    vision.reset_temporal()
    assert vision.loot_cards(frame.copy())
    assert vision.end_loot_recognition()['index_queries']==6
    vision.begin_loot_recognition()
    assert vision.loot_cards(frame.copy())
    assert vision.end_loot_recognition()['index_queries']==3


def test_moved_layout_breaks_evidence_and_rediscovers(setup):
    vision,config,templates=setup
    vision.begin_loot_recognition()
    assert vision.loot_cards(loot_frame(config,templates,KEYS))
    frame=loot_frame(config,templates,KEYS,shift=(12,0))
    assert vision.loot_cards(frame) is None
    assert tuple(h.key for h in vision.loot_cards(frame.copy()))==KEYS
    summary=vision.end_loot_recognition()
    assert summary['layout_resets']==1 and summary['index_queries']==6


def test_top_five_miss_falls_back_locally_and_stops_after_clear_hit(setup,monkeypatch):
    vision,config,templates=setup
    original=vision._loot.rank
    def delayed(card, *, stats=None):
        order,_=original(card,stats=stats)
        order=np.concatenate((order[1:6],order[:1],order[6:]))
        scores=np.empty(len(order),np.float32)
        scores[order]=np.arange(len(order))*.1
        return order,scores
    monkeypatch.setattr(vision._loot,'rank',delayed)
    vision.begin_loot_recognition()
    assert tuple(h.key for h in vision.loot_cards(loot_frame(config,templates,KEYS)))==KEYS
    summary=vision.end_loot_recognition()
    assert summary['fallback_cards']==3 and summary['candidates_checked']==18


def test_near_equal_verified_candidates_fail_closed(setup,monkeypatch):
    vision,config,templates=setup
    original_rank=vision._loot.rank
    original_verify=vision._loot.verify
    worker=local()
    def ranking(card, *, stats=None):
        order,scores=original_rank(card,stats=stats)
        worker.pair=[int(order[0]),int(order[1])]
        scores[order[1]]=scores[order[0]]
        return order,scores
    def verification(card,anchor,index,name_image=None, *, stats=None):
        if index==worker.pair[1]:
            good=original_verify(card,anchor,worker.pair[0],name_image,stats=stats)
            return replace(good,key='ambiguous_test_identity') if good else None
        return original_verify(card,anchor,index,name_image,stats=stats)
    monkeypatch.setattr(vision._loot,'rank',ranking)
    monkeypatch.setattr(vision._loot,'verify',verification)
    vision.begin_loot_recognition()
    assert vision.loot_cards(loot_frame(config,templates,KEYS)) is None
    assert vision.end_loot_recognition()['ambiguous_cards']==3
