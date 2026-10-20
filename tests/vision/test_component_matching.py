"""Chosen identities, false evidence, original grid ownership and routing."""
import gc
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from e7auto.configuration.loader import load_config
from e7auto.core.recognition import ComponentLayout
from e7auto.core.types import ClientPoint, ClientRect, Rect, Size
from e7auto.features.chaos.vision import ChaosVision
from e7auto.features.penguin.configuration import with_penguin_config
from e7auto.features.penguin.vision import PenguinVision
from e7auto.resources.templates import TemplateData, TemplateRepository
from e7auto.vision.candidates import Candidate, CandidateCache
from e7auto.vision.components import ComponentMatcher
from e7auto.vision.frames import FrameView, CoordinateTransform
from e7auto.vision.matching import TemplateMatcher
from tests.helpers.chaos_game import chaos_config
from tests.helpers.component_images import component_frame, stamp_part
from tests.helpers.paths import ROOT


CHAOS_KEYS = '''hero_xiaochun hero_twins hero_ivanna hero_arowell hero_teacher hero_shadow_rose
bench_teacher bench_arowell journey_close rank_close contract ticket_thief ticket_soul
ticket_warrior ticket_knight ticket_all purchase_item purchase_health_restore
rank_value_1 rank_value_2 rank_value_3 rank_value_4 rank_value_5'''.split()
ALL_KEYS = CHAOS_KEYS + ['login_reward_close', 'altar_close']
SIZES = [(2322,1306), (1536,864), (1920,1080), (2304,1296), (2560,1440)]


@pytest.fixture(scope='module')
def profiles():
    result = []
    for config in (chaos_config(), chaos_config('budget'),
                   with_penguin_config(load_config(ROOT/'config/internal.yaml', template_profile='penguin'))):
        repo = TemplateRepository(config)
        vision = PenguinVision(config, repo) if 'penguin_altar_close' in config.component_layouts else ChaosVision(config, repo)
        result.append((config, repo, vision))
    yield result
    for _, _, vision in result:
        vision.clear_template_cache()


def selected(profiles, name):
    key = 'penguin_altar_close' if name == 'altar_close' else ('login_reward_close' if name == 'login_reward_close' else 'chaos:'+name)
    config, repo, vision = next(p for p in profiles if key in p[0].component_layouts)
    roi = config.rois[key] if name == 'altar_close' else config.chaos.scenes[name].region
    data = repo.get(key)
    height, width = data.image.shape[:2]
    origin = (roi.x+(roi.width-width)//2, roi.y+(roi.height-height)//2)
    return config, repo, vision, key, roi, origin


@pytest.mark.parametrize('name', ALL_KEYS)
@pytest.mark.parametrize('size', SIZES)
def test_all_chosen_identities_keep_parent_click_geometry(profiles, name, size, monkeypatch):
    config, repo, vision, key, roi, origin = selected(profiles, name)
    layout = config.component_layouts[key]
    frame = component_frame(repo.get(key), layout, size, origin=origin)
    monkeypatch.setattr(vision._matcher.baseline, 'match',
                        lambda *a, **k: pytest.fail('selected template entered whole-template matching'))
    monkeypatch.setattr(vision._matcher.baseline, 'locate_all',
                        lambda *a, **k: pytest.fail('selected template entered whole-template matching'))
    hit = vision.control(frame, name)
    assert hit is not None, (name, size)
    sx, sy = frame.transform.scale_x, frame.transform.scale_y
    if name == 'altar_close':
        action, offset = (.5,.5), (0,0)
    else:
        scene = config.chaos.scenes[name]
        action, offset = scene.action or (.5,.5), scene.action_offset
    h, w = repo.get(key).image.shape[:2]
    expected = ((origin[0]+w*action[0]+offset[0])*sx, (origin[1]+h*action[1]+offset[1])*sy)
    assert isinstance(hit.anchor, ClientPoint)
    assert abs(hit.anchor.x-expected[0]) <= 2 and abs(hit.anchor.y-expected[1]) <= 2
    child = frame.crop(roi)
    nested = vision.control(child, name)
    assert nested is not None and nested.anchor == hit.anchor
    if size == (2322,1306):
        vision.clear_template_cache()
        assert vision.control(frame, name) == hit


def test_real_xiaochun_failure_is_fixed_without_lowering_gates(profiles, monkeypatch):
    config, repo, vision, _, _, _ = selected(profiles, 'hero_xiaochun')
    raw = cv2.imdecode(np.fromfile(ROOT/'tests/fixtures/chaos/resolution/hero_xiaochun_2304.png',np.uint8),1)
    frame = FrameView(raw, CoordinateTransform(Size(2322,1306),Size(2304,1296)))
    scene = config.chaos.scenes['hero_xiaochun']
    assert (scene.threshold, scene.structure) == (.94,.8)
    monkeypatch.setattr(vision._matcher, 'locate_all',
                        lambda *a, **k: pytest.fail('selected hero entered whole-template matching'))
    hit = vision.control(frame, 'hero_xiaochun')
    assert hit is not None and 1250 < hit.anchor.x < 1600 and 340 < hit.anchor.y < 490
    vision.clear_template_cache()
    assert vision.control(frame, 'hero_xiaochun') == hit


def test_real_baseline_teacher_shift_keeps_identity_and_parent_click(profiles):
    config, repo, vision, key, roi, _ = selected(profiles, 'hero_teacher')
    raw = cv2.imdecode(np.frombuffer(
        (ROOT/'tests/fixtures/chaos/recruitment/hero_teacher_baseline_2322.png').read_bytes(),
        np.uint8), cv2.IMREAD_COLOR)
    frame = FrameView(raw, CoordinateTransform(Size(2322,1306), Size(2322,1306)))
    scene = config.chaos.scenes['hero_teacher']
    assert (scene.threshold, scene.structure) == (.92,.8)
    assert not vision._matcher.baseline.locate_all(frame, key, roi, .92, .8, limit=1)
    hit = vision.control(frame, 'hero_teacher')
    assert hit is not None
    assert hit.bounds == ClientRect(663,752,293,46)
    assert hit.anchor == ClientPoint(810,775)
    assert vision.control(frame.crop(roi), 'hero_teacher') == hit
    vision.clear_template_cache()
    assert vision.control(frame, 'hero_teacher') == hit


@pytest.mark.parametrize('name', ALL_KEYS)
@pytest.mark.parametrize('missing', ['primary', 'secondary'])
@pytest.mark.parametrize('size', [(2322,1306),(1536,864)])
def test_one_component_never_authorizes_a_hit(profiles, name, missing, size):
    config, repo, vision, key, _, origin = selected(profiles, name)
    frame = component_frame(repo.get(key), config.component_layouts[key], size, origin=origin,
                            **{missing: False})
    assert vision.control(frame, name) is None


@pytest.mark.parametrize('shift', [(4,5),(-4,-5),(0,7),(5,0)])
@pytest.mark.parametrize('name', ['hero_xiaochun','contract','rank_close','rank_value_3','purchase_item'])
@pytest.mark.parametrize('size', [(2322,1306),(2304,1296)])
def test_small_game_layout_offsets_are_accepted(profiles, name, shift, size):
    config, repo, vision, key, _, origin = selected(profiles, name)
    frame = component_frame(repo.get(key), config.component_layouts[key], size, origin=origin, shift=shift)
    assert vision.control(frame, name) is not None


@pytest.mark.parametrize('shift', [(20,0),(0,22),(-20,0),(0,-22)])
@pytest.mark.parametrize('size', [(2322,1306),(1536,864)])
def test_other_rows_and_outside_tolerance_cannot_supply_the_icon(profiles, shift, size):
    config, repo, vision, key, _, origin = selected(profiles, 'hero_xiaochun')
    frame = component_frame(repo.get(key), config.component_layouts[key], size, origin=origin, shift=shift)
    assert vision.control(frame, 'hero_xiaochun') is None


@pytest.mark.parametrize('size', [(2322,1306),(2304,1296)])
def test_wrong_profession_and_left_detail_name_do_not_match(profiles, size):
    config, repo, vision, key, roi, origin = selected(profiles, 'hero_xiaochun')
    layout = config.component_layouts[key]
    frame = component_frame(repo.get(key), layout, origin=origin, secondary=False).raw.copy()
    other = repo.get('chaos:hero_twins')
    stamp_part(frame, other, config.component_layouts['chaos:hero_twins'].secondary, origin)
    transform = CoordinateTransform(Size(2322,1306),Size(*size))
    assert vision.control(FrameView(cv2.resize(frame,size),transform),'hero_xiaochun') is None
    frame = component_frame(repo.get(key), layout, size, origin=(70,200))
    assert vision.control(frame,'hero_xiaochun') is None


@pytest.mark.parametrize('size', [(2322,1306),(1536,864)])
def test_existing_aliases_share_recipes_but_keep_scene_identity(profiles, size):
    config, repo, vision, key, _, origin = selected(profiles, 'rank_close')
    frame = component_frame(repo.get(key), config.component_layouts[key], size, origin=origin)
    hit, alias = vision.control(frame,'rank_close'), vision.control(frame,'event_reward_close')
    assert hit is not None and alias is not None and hit.anchor == alias.anchor
    assert hit.key != alias.key
    config, repo, vision, key, _, origin = selected(profiles, 'login_reward_close')
    frame = component_frame(repo.get(key), config.component_layouts[key], size, origin=origin)
    assert vision.control(frame,'buff_effect_close').anchor == vision.control(frame,'login_reward_close').anchor


@pytest.mark.parametrize('size', [(2322,1306),(1536,864)])
def test_unselected_controls_keep_old_path(profiles, monkeypatch, size):
    for config, repo, vision in profiles:
        monkeypatch.setattr(vision._components,'locate_all',lambda *a,**k: pytest.fail('unselected template entered components'))
        if isinstance(vision, ChaosVision):
            vision.control(FrameView(np.zeros((size[1],size[0],3),np.uint8),
                           CoordinateTransform(Size(2322,1306),Size(*size))), 'stage_confirm')
        else:
            vision.control(FrameView(np.zeros((size[1],size[0],3),np.uint8),
                           CoordinateTransform(Size(2322,1306),Size(*size))), 'forest_back')


@pytest.mark.parametrize('scale', [(1.,1.),(.8,.8)])
def test_component_views_keep_parent_alive_and_do_not_rescale_or_double_charge(scale):
    rng = np.random.default_rng(12)
    data = TemplateData(rng.integers(20,230,(40,90,3),dtype=np.uint8),np.full((40,90),255,np.uint8))
    cache = CandidateCache(4*1024*1024)
    parent = cache.get(data,scale,phase=(0.,0.) if scale == (1.,1.) else (.25,.5))
    if scale == (1.,1.):
        assert parent.image is data.image and parent.mask is data.mask
    charged = cache.live_bytes
    part = cache.part(parent,Rect(45,0,45,40))
    assert cache.live_bytes == charged
    assert part.parent is parent and np.shares_memory(part.image,parent.image)
    assert not part.image.flags.writeable and not part.mask.flags.writeable
    del parent
    cache.clear(); gc.collect()
    assert cache.live_bytes == charged
    del part; gc.collect()
    assert cache.live_bytes == 0


@pytest.mark.parametrize('offset,expected', [((3,5),True),((-3,-5),True),((4,0),False),((0,6),False)])
def test_fractional_client_tolerance_rejects_outward_rounding_overrun(offset, expected):
    # At 1536x864 the exact bounds are +/-3.969 and +/-5.293, not +/-4 and +/-6.
    pattern = np.random.default_rng(85).integers(60,240,(5,5,3),dtype=np.uint8)
    part = Candidate(pattern,None,(5,5),(5,5),(1.,1.),cache_key=('boundary-pattern',))
    source = np.zeros((60,70,3),np.uint8)
    x,y = 25+offset[0],25+offset[1]
    source[y:y+5,x:x+5] = pattern
    matcher = TemplateMatcher(SimpleNamespace())
    components = ComponentMatcher(SimpleNamespace(),matcher,{})
    result = components._secondary(source,part,(25,25),(6*1536/2322,8*864/1306),
        (100,200),ClientRect(100,200,70,60),.99999,None,True)
    assert (result is not None) is expected


def test_secondary_multiple_placements_and_missing_edge_pixels_are_rejected():
    pattern = np.random.default_rng(84).integers(60,240,(5,2,3),dtype=np.uint8)
    part = Candidate(pattern,None,(2,5),(2,5),(1.,1.),cache_key=('ambiguity-pattern',))
    source = np.zeros((60,70,3),np.uint8)
    source[25:30,22:24] = pattern
    source[25:30,28:30] = pattern
    matcher = ComponentMatcher(SimpleNamespace(),TemplateMatcher(SimpleNamespace()),{})
    assert matcher._secondary(source,part,(25,25),(6,8),(0,0),ClientRect(0,0,70,60),.99999,None,True) is None
    source[25:30,28:30] = 0
    assert matcher._secondary(source,part,(25,25),(6,8),(0,0),ClientRect(0,0,70,60),.99999,None,True) is not None
    assert matcher._secondary(source,part,(25,25),(6,8),(0,0),ClientRect(23,0,47,60),.99999,None,True) is None


@pytest.mark.parametrize('size', [(2322,1306),(1536,864),(2304,1296),(2560,1440)])
def test_rank_columns_compare_all_values_and_require_digit_five(profiles, size):
    config, repo, vision, _, _, _ = selected(profiles, 'rank_value_1')
    raw = np.full((1306,2322,3),24,np.uint8)
    for value, origin in ((2,(1360,380)), (5,(1600,380))):
        key = 'chaos:rank_value_'+str(value)
        layout = config.component_layouts[key]
        stamp_part(raw,repo.get(key),layout.primary,origin)
        shift = (3,4)
        stamp_part(raw,repo.get(key),layout.secondary,origin,shift)
    scaled = cv2.resize(raw,size,interpolation=cv2.INTER_AREA if size[0] < 2322 else cv2.INTER_LINEAR)
    frame = FrameView(scaled,CoordinateTransform(Size(2322,1306),Size(*size)))
    assert vision.rank_result(frame) == (2,5)
    # Leave the rank icon intact, but remove the digit 5 from the result column.
    raw[375:450,1661:1701] = 24
    frame = FrameView(cv2.resize(raw,size),frame.transform)
    assert vision.rank_result(frame) is None
