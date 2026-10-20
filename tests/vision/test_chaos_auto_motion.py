"""Reference-pose classification and strict OFF evidence boundaries."""
import json

import cv2
import numpy as np
import pytest
import yaml

from e7auto.features.chaos.auto_motion import AutoStateEvidence, AutoIconState
from e7auto.features.chaos.contracts import AutoState
from tests.helpers.paths import ROOT
from tests.helpers.chaos_images import auto_icon_image


@pytest.fixture
def evidence():
    return AutoStateEvidence(yaml.safe_load((ROOT/'config/chaos.yaml').read_text(encoding='utf8'))['auto_motion'])


@pytest.mark.parametrize('angle',[-90,-75,-10,10,75,90,170])
def test_one_reliable_offset_proves_active_without_previous_sample(evidence,angle):
    assert evidence.observe(0,.9,angle) is AutoState.ROTATING
    assert evidence.summary()['static_samples']==0


@pytest.mark.parametrize('angle',[-9.999,0,9.999,180])
def test_near_reference_is_unknown_without_three_frames(evidence,angle):
    assert evidence.observe(0,.9,angle) is AutoState.UNKNOWN


def test_bad_observation_does_not_delay_next_reliable_active_frame(evidence):
    assert evidence.observe(0,.2,None) is AutoState.UNKNOWN
    assert evidence.observe(.1,.9,45,reliable=False) is AutoState.UNKNOWN
    assert evidence.observe(.2,.9,45) is AutoState.ROTATING
    assert evidence.observe(2.,.9,-45) is AutoState.ROTATING
    assert evidence.summary()['reference_rejects']==1


def test_static_needs_three_frames_and_restarts_after_visibility_loss(evidence):
    assert evidence.observe(0.,.9,0) is AutoState.UNKNOWN
    assert evidence.observe(.1,.9,0) is AutoState.UNKNOWN
    assert evidence.observe(.2,.9,0) is AutoState.STATIC
    assert evidence.summary()['static_span_ms']==200
    assert evidence.observe(.3,.2,None) is AutoState.UNKNOWN
    assert evidence.observe(.4,.9,0) is AutoState.UNKNOWN
    assert evidence.observe(.5,.9,0) is AutoState.UNKNOWN
    assert evidence.observe(.6,.9,0) is AutoState.STATIC


@pytest.mark.parametrize('interruption',['unreliable','offset','middle_angle','long_gap','reset'])
def test_static_evidence_cannot_cross_interruptions(evidence,interruption):
    evidence.observe(0.,.9,0);evidence.observe(.1,.9,0)
    if interruption=='unreliable':evidence.observe(.2,.9,0,reliable=False)
    elif interruption=='offset':evidence.observe(.2,.9,20)
    elif interruption=='middle_angle':evidence.observe(.2,.9,7)
    elif interruption=='reset':evidence.reset()
    assert evidence.observe(.351,.9,0) is AutoState.UNKNOWN
    assert evidence.summary()['static_samples']==1


def test_fast_duplicate_and_sparse_static_samples_never_authorize_click(evidence):
    for i in range(10):assert evidence.observe(i*.01,.9,0) is AutoState.UNKNOWN
    assert evidence.summary()['static_samples']==1
    assert evidence.observe(.09,.9,30) is AutoState.UNKNOWN
    assert evidence.summary()['reason']=='non_increasing_time'
    for i in range(4):assert evidence.observe(2+i*.3,.9,0) is AutoState.UNKNOWN


@pytest.mark.parametrize('angle,expected',[(-5,AutoState.STATIC),(5,AutoState.STATIC),(-5.001,AutoState.UNKNOWN),(5.001,AutoState.UNKNOWN)])
def test_static_angle_band_boundary(evidence,angle,expected):
    for i in range(3):state=evidence.observe(i*.1,.9,angle)
    assert state is expected


def test_static_counter_is_bounded_without_angle_history(evidence):
    for i in range(10):state=evidence.observe(i*.1,.9,[-5,5][i%2])
    assert state is AutoState.STATIC and evidence.static_count==3
    assert not hasattr(evidence,'samples')
    evidence.reset()
    assert evidence.summary()['evidence_resets']==1 and evidence.static_count==0
    evidence.begin()
    assert evidence.summary()['observations']==0


@pytest.fixture(params=[False,True],ids=['fixed','localized'])
def icon(evidence,request):
    directory=ROOT/'assets/templates/chaos'
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf8'))
    template=cv2.imread(str(directory/manifest['templates']['auto_static']['file']))
    detector=AutoIconState(evidence.settings,template,localize=request.param)
    base=np.zeros((110,110,3),np.uint8);base[5:110,1:110]=template[:105,:109]
    return detector,base


@pytest.mark.parametrize('angle',[-90,-45,-10,10,45,90])
def test_real_template_angle_offset_confirms_in_one_frame(icon,angle):
    detector,base=icon
    assert detector.observe(auto_icon_image(base,angle),0.) is AutoState.ROTATING
    assert abs(detector.summary()['reference_angle_deg'])>=10
    assert detector.summary()['first_active_ms']==0


@pytest.mark.parametrize('case',['blank','flash','noise','ring_only','centre_only','uniform_ring','random_ring'])
def test_unrelated_or_incomplete_pixels_remain_unknown(icon,case):
    detector,base=icon;rng=np.random.default_rng(72)
    image=base.copy()
    if case=='blank':image[:]=0
    elif case=='flash':image[:]=255
    elif case=='noise':image=rng.integers(0,256,base.shape,dtype=np.uint8)
    elif case=='ring_only':image[detector.core_mask]=0
    elif case=='centre_only':image[~detector.core_mask]=0
    elif case=='uniform_ring':image[~detector.core_mask]=255
    else:
        image=np.repeat((rng.integers(0,2,base.shape[:2],dtype=np.uint8)*255)[:,:,None],3,axis=2)
        image[detector.core_mask]=base[detector.core_mask]
    assert all(detector.observe(image,i*.1) is AutoState.UNKNOWN for i in range(18))


def test_reference_pose_needs_three_frames_and_bad_frame_breaks_off_evidence(icon):
    detector,base=icon
    assert detector.observe(base,0.) is AutoState.UNKNOWN
    assert detector.observe(base,.1) is AutoState.UNKNOWN
    assert detector.observe(base,.2) is AutoState.STATIC
    assert detector.observe(np.zeros_like(base),.3) is AutoState.UNKNOWN
    assert detector.center_offset is None
    assert detector.observe(auto_icon_image(base,30),.4) is AutoState.ROTATING
    assert detector.observe(base,.5) is AutoState.UNKNOWN


@pytest.mark.parametrize('offset',[(-3,-3),(3,3),(0,-1)])
def test_localized_center_is_shared_by_ring_and_anchor(evidence,offset):
    template=cv2.imread(str(ROOT/'assets/templates/chaos/battle/auto_static.png'))
    detector=AutoIconState(evidence.settings,template,localize=True)
    base=np.zeros((110,110,3),np.uint8);base[5:110,1:110]=template[:105,:109]
    rotated=auto_icon_image(base,45)
    image=cv2.warpAffine(rotated,np.float32([[1,0,offset[0]],[0,1,offset[1]]]),(110,110))
    assert detector.observe(image,0) is AutoState.ROTATING
    assert detector.center_offset==offset


def test_position_changes_cannot_accumulate_off_evidence(evidence):
    template=cv2.imread(str(ROOT/'assets/templates/chaos/battle/auto_static.png'))
    detector=AutoIconState(evidence.settings,template,localize=True)
    base=np.zeros((110,110,3),np.uint8);base[5:110,1:110]=template[:105,:109]
    for i in range(20):
        dx=3 if i%2 else -3
        image=cv2.warpAffine(base,np.float32([[1,0,dx],[0,1,0]]),(110,110))
        assert detector.observe(image,i*.1) is AutoState.UNKNOWN
    assert detector.evidence.summary()['static_samples']==1


@pytest.mark.parametrize('second_time',[0.,-.1])
def test_duplicate_or_reversed_time_cannot_authorize_state_or_anchor(icon,second_time):
    detector,base=icon
    assert detector.observe(auto_icon_image(base,30),0.) is AutoState.ROTATING
    assert detector.observe(auto_icon_image(base,45),second_time) is AutoState.UNKNOWN
    assert detector.center_offset is None
    assert detector.summary()['reason']=='non_increasing_time'
