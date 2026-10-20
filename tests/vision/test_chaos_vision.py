import cv2
import numpy as np
import pytest

from e7auto.configuration.loader import load_config
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.features.chaos.vision import ChaosVision
from e7auto.features.chaos.contracts import AutoState
from e7auto.core.types import Rect, Size
from e7auto.vision.frames import CoordinateTransform, adapt_frame
from e7auto.resources.templates import TemplateRepository
from tests.helpers.paths import ROOT
from tests.helpers.chaos_images import loot_frame, auto_icon_image


@pytest.fixture
def vision():
    config=with_chaos_config(load_config(ROOT/'config/internal.yaml',template_profile='chaos'))
    templates=TemplateRepository(config)
    detector=ChaosVision(config,templates)
    yield detector,config,templates
    detector.close_loot_recognition()


def paste(frame,config,templates,key,x,y):
    data=templates.get(config.chaos.scenes[key].template_key).image
    frame[y:y+len(data),x:x+data.shape[1]]=data
    return Rect(x,y,data.shape[1],len(data))


def exploration_entry_frame():
    path = ROOT / 'tests/fixtures/chaos/resolution/exploration_card_1536.png'
    raw = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert raw is not None and raw.shape == (864, 1536, 3)
    transform = CoordinateTransform(Size(2322, 1306), Size(1536, 864))
    return raw, transform


def test_real_small_client_exploration_text_and_card_center_click(vision):
    detector, _, _ = vision
    raw, transform = exploration_entry_frame()
    hit = detector.control(adapt_frame(raw, transform), 'exploration_card')
    assert hit is not None
    # Interior around the card centre, measured from the real failure screenshot.
    assert 170 <= hit.anchor.x < 235 and 470 <= hit.anchor.y < 560
    assert transform.point(hit.anchor) is hit.anchor


def test_real_small_client_dark_filter_button_center(vision):
    detector, config, _ = vision
    path = ROOT/'tests/fixtures/chaos/recruitment/filter_dark_1536.png'
    raw = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    transform = CoordinateTransform(config.baseline_client_size, Size(raw.shape[1], raw.shape[0]))
    frame = adapt_frame(raw, transform)
    hit = detector.control(frame, 'filter_dark')
    assert hit is not None
    # Interior of the actual filter button in the original 1536x864 failure.
    assert 830 <= hit.anchor.x < 985 and 20 <= hit.anchor.y < 65
    assert transform.point(hit.anchor) is hit.anchor
    assert detector.control(frame, 'filter_light') is None


def story_skip_frame():
    path = ROOT/'tests/fixtures/chaos/resolution/story_skip_1536.png'
    raw = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert raw is not None and raw.shape == (864, 1536, 3)
    return raw, CoordinateTransform(Size(2322, 1306), Size(1536, 864))


def test_real_small_client_story_skip_text_and_preserved_button_click(vision):
    detector, _, _ = vision
    raw, transform = story_skip_frame()
    hit = detector.control(adapt_frame(raw, transform), 'story_skip')
    assert hit is not None
    assert 1423 <= hit.bounds.x < 1430 and 1468 <= hit.bounds.right < 1479
    # Keep the old text-and-arrow button centre, rather than the cropped text centre.
    assert 1462 <= hit.anchor.x <= 1470 and 40 <= hit.anchor.y <= 51
    assert hit.anchor.x > hit.bounds.x + hit.bounds.width/2 + 10
    assert transform.point(hit.anchor) is hit.anchor


@pytest.mark.parametrize('columns', [(1420, 1450), (1450, 1478)])
def test_story_skip_requires_both_characters(vision, columns):
    detector, _, _ = vision
    raw, transform = story_skip_frame()
    left, right = columns
    raw[28:63, left:right] = 0
    assert detector.control(adapt_frame(raw, transform), 'story_skip') is None


@pytest.mark.parametrize('size', [(2322, 1306), (1536, 864), (1920, 1080), (2560, 1440)])
def test_story_skip_crop_keeps_original_button_anchor_across_sizes(vision, size):
    detector, config, templates = vision
    scene = config.chaos.scenes['story_skip']
    data = templates.get(scene.template_key)
    reference = np.zeros((1306, 2322, 3), np.uint8)
    # Original source: full template at (2154,45), text crop offset (2,7).
    reference[52:85, 2156:2226] = data.image
    raw = reference if size == (2322, 1306) else cv2.resize(
        reference, size, interpolation=cv2.INTER_AREA if size[0] < 2322 else cv2.INTER_LINEAR)
    transform = CoordinateTransform(config.baseline_client_size, Size(*size))
    hit = detector.control(adapt_frame(raw, transform), 'story_skip')
    assert hit is not None
    if size == (2322, 1306):
        assert (hit.anchor.x, hit.anchor.y) == (2215, 70)
    else:
        assert abs(hit.anchor.x - 2215*transform.scale_x) <= 2
        assert abs(hit.anchor.y - 69.5*transform.scale_y) <= 2
    assert transform.point(hit.anchor) is hit.anchor


@pytest.mark.parametrize('region', [
    Rect(606, 316, 513, 925), Rect(1163, 316, 513, 925),
    Rect(1720, 316, 513, 925),
])
def test_real_small_client_exploration_text_rejects_neighbour_cards(vision, region):
    detector, config, _ = vision
    raw, transform = exploration_entry_frame()
    frame = adapt_frame(raw, transform)
    scene = config.chaos.scenes['exploration_card']
    assert not detector._matcher.locate_all(
        frame, scene.template_key, frame.region(region),
        scene.threshold, scene.structure, limit=1)


def test_exploration_icon_alone_does_not_authorize_entry(vision):
    detector, _, _ = vision
    raw, transform = exploration_entry_frame()
    # Erase the four-character caption while leaving the spiral visible.
    raw[220:264, 98:215] = 0
    assert detector.control(adapt_frame(raw, transform), 'exploration_card') is None


@pytest.mark.parametrize('stamp,old_score', [('170131', .755102), ('170345', .747475)])
def test_real_small_client_center_and_reference_offset_confirm_active(vision, stamp, old_score):
    detector, config, _ = vision
    path = ROOT / f'tests/fixtures/chaos/auto_motion/client_1536_{stamp}.png'
    raw = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    transform = CoordinateTransform(Size(2322, 1306), Size(1536, 864))
    frame = adapt_frame(raw, transform)
    # OFF returns to the fixed reference pose; these real frames are offset.
    assert detector.auto_state(frame,0.) is AutoState.ROTATING
    motion=detector._motion
    assert motion.center_offset==(0,0)
    assert motion.summary()['last_score']>.85
    r=config.chaos.auto_motion['roi']
    bounds=frame.region(Rect(*r))
    anchor=detector.auto_anchor()
    assert (anchor.x,anchor.y)==(bounds.x+bounds.width//2,bounds.y+bounds.height//2)
    assert transform.point(anchor) is anchor
    assert abs(motion.summary()['reference_angle_deg'])>=10

    removed = raw.copy()
    rect = transform.rect(Rect(r[0]+33, r[1]+33, 44, 44))
    removed[rect.y:rect.bottom, rect.x:rect.right] = 0
    detector.reset_temporal()
    for i in range(22):
        assert detector.auto_state(adapt_frame(removed, transform), 1+i*.1) is AutoState.UNKNOWN
    assert detector.auto_anchor() is None


@pytest.mark.parametrize('size', [(2322, 1306), (1920, 1080), (1536, 864)])
@pytest.mark.parametrize('shift', [(0, 0), (-70, 40)])
def test_boss_all_ticket_click_lands_inside_recruit_button(vision, size, shift):
    detector, _, _ = vision
    frame = cv2.imread(str(ROOT / 'tests/fixtures/chaos/boss_all_ticket.png'))
    assert frame is not None
    dx, dy = shift
    if shift != (0, 0):
        frame = cv2.warpAffine(frame, np.float32([[1, 0, dx], [0, 1, dy]]), (2322, 1306))
    transform = CoordinateTransform(Size(2322, 1306), Size(*size))
    frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    hit = detector.control(adapt_frame(frame, transform), 'ticket_all')
    assert hit is not None
    # An interior rectangle of the actual blue button, measured independently
    # from the identity-template anchor in the September 24 failure capture.
    button = transform.rect(Rect(1500 + dx, 940 + dy, 280, 45))
    point = transform.point(hit.anchor)
    assert button.x <= point.x < button.right
    assert button.y <= point.y < button.bottom


def test_all_three_loot_identities_required_and_selection_is_target_local(vision):
    detector,config,templates=vision
    keys=('loot_ominous_crystal','loot_old_sword','loot_lightning')
    frame=loot_frame(config,templates,keys)
    assert tuple(h.key for h in detector.loot_cards(frame))==keys
    # Unknown third card must fail the whole choice, even with two strong identities.
    missing=frame.copy();missing[100:1070,1500:2100]=0
    assert detector.loot_cards(missing) is None
    selected=templates.get(config.chaos.scenes['loot_selected'].template_key)
    image=frame[196:196+selected.image.shape[0],354:354+selected.image.shape[1]]
    mask=selected.mask>0;image[mask]=selected.image[mask]
    assert detector.control(frame,'loot_selected',Rect(300,100,570,970)) is not None
    assert detector.control(frame,'loot_selected',Rect(880,100,570,970)) is None


def test_map_associates_moving_triangles_instead_of_fixed_node_slots(vision):
    detector,config,templates=vision
    frame=np.zeros((1306,2322,3),np.uint8)
    paste(frame,config,templates,'map_title_outer',20,15)
    paste(frame,config,templates,'map_hero',560,1170)
    for key,x,y in [('node_normal',320,420),('node_rest',1350,760)]:
        body=paste(frame,config,templates,key,x,y)
        tri=templates.get(config.chaos.scenes['node_triangle'].template_key).image
        paste(frame,config,templates,'node_triangle',x+(body.width-tri.shape[1])//2,y-70)
    view=detector.map_view(frame)
    assert view is not None and view.floor==1
    assert {n.kind for n in view.nodes}=={'normal','rest'}
    frame[1100:]=0
    assert detector.map_view(frame) is None


def test_auto_hidden_resets_window_and_rotating_is_not_static(vision):
    detector,config,templates=vision
    frame=np.zeros((1306,2322,3),np.uint8)
    glyph=templates.get(config.chaos.scenes['auto_static'].template_key).image
    base=np.zeros((110,110,3),np.uint8);base[5:110,1:110]=glyph[:105,:109]
    frame[7:117,1989:2099]=base
    for i in range(18):state=detector.auto_state(frame,i*.1)
    assert state==AutoState.STATIC
    blank=np.zeros_like(frame)
    assert detector.auto_state(blank,1.8)==AutoState.UNKNOWN
    assert detector.auto_state(frame,1.9)==AutoState.UNKNOWN
    detector.reset_temporal()
    for i in range(12):
        frame[7:117,1989:2099]=auto_icon_image(base, i*10)
        state=detector.auto_state(frame,2.+i*.1)
    assert state==AutoState.ROTATING


@pytest.mark.parametrize('size', [(2322, 1306), (1920, 1080), (1536, 864), (3072, 1728)])
@pytest.mark.parametrize('direction', [-1, 0, 1])
def test_auto_motion_template_sequence_at_supported_sizes(vision, size, direction):
    detector, config, templates = vision
    glyph = templates.get(config.chaos.scenes['auto_static'].template_key).image
    base = np.zeros((110, 110, 3), np.uint8)
    base[5:110, 1:110] = glyph[:105, :109]
    transform = CoordinateTransform(Size(2322, 1306), Size(*size))
    states = []
    for i in range(18):
        frame = np.zeros((1306, 2322, 3), np.uint8)
        frame[7:117, 1989:2099] = auto_icon_image(base, direction*i*5)
        frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA if size[0]<=2322 else cv2.INTER_LINEAR)
        states.append(detector.auto_state(adapt_frame(frame, transform), i*.1))
    assert states[-1] is (AutoState.STATIC if direction == 0 else AutoState.ROTATING)
    assert (AutoState.ROTATING if direction == 0 else AutoState.STATIC) not in states


@pytest.mark.parametrize('keys,expected',[
    (('loot_old_sword','loot_ominous_crystal','loot_lightning'),'loot_ominous_crystal'),
    (('loot_old_sword','loot_star_shard','loot_confession'),'loot_old_sword'),
    (('loot_star_shard','loot_old_sword','loot_confession'),'loot_star_shard'),
    (('loot_summon_mark','loot_dimension_pouch','loot_ancient_key'),'loot_ancient_key'),
    (('loot_sun_shield','loot_star_book','loot_summon_mark'),'loot_star_book'),
    (('loot_dice_hero','loot_tablet_sleeping_forest','loot_ancient_key'),'loot_dice_hero'),
    (('loot_summon_mark','loot_automaton_heart','loot_twin_star_ring'),'loot_summon_mark'),
])
def test_canonical_loot_choices_keep_priorities_and_leftmost_ties(vision,keys,expected):
    detector,config,templates=vision
    frame=loot_frame(config,templates,keys)
    cards=detector.loot_cards(frame)
    assert cards is not None and tuple(h.key for h in cards)==keys
    chosen=min(cards,key=lambda h:(config.chaos.loot_priorities[h.key],h.bounds.x+h.bounds.width/2))
    assert chosen.key==expected
