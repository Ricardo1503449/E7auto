"""Direct regression checks for offline extraction; no game or runtime flow."""
import cv2
import numpy as np
import pytest

from scripts.calibration.build_chaos_review import apply_review_mask, crop_checked, make_candidate, match_candidate


def test_crop_rejects_desktop_overflow_and_keeps_exact_pixels():
    original=np.arange(60*90*4,dtype=np.uint8).reshape(60,90,4)
    cropped=crop_checked(original,[11,13,25,20])
    assert np.array_equal(cropped,original[13:33,11:36])
    for rect in [[-1,0,5,5],[0,0,91,5],[0,50,5,11],[0,0,0,5]]:
        with pytest.raises(ValueError):crop_checked(original,rect)


def test_mask_never_repaints_original_rgb_or_restores_transparent_pixels():
    original=np.zeros((70,120,4),np.uint8);original[:,:,:3]=(75,40,50);original[:,:,3]=255
    cv2.putText(original,'TEST',(8,44),cv2.FONT_HERSHEY_SIMPLEX,.8,(240,240,240,255),2)
    original[:10,:,3]=0
    candidate=make_candidate(original,'ink',None)
    assert np.array_equal(candidate[:,:,:3],original[:,:,:3])
    assert not candidate[:10,:,3].any()
    assert 20<np.count_nonzero(candidate[:,:,3])<70*120


def test_nonconstant_masked_match_finds_translated_evidence_and_rejects_blank():
    t=np.zeros((44,80,4),np.uint8);t[:,:,:3]=(42,25,33);t[:,:,3]=255
    cv2.putText(t,'R2',(8,31),cv2.FONT_HERSHEY_SIMPLEX,.9,(240,240,240,255),2)
    t=make_candidate(t,'ink',None)
    scene=np.full((180,330,3),27,np.uint8);scene[67:111,143:223]=t[:,:,:3]
    result=match_candidate(scene,t,[90,20,220,150])
    assert result['score']>.99 and result['structure']>.99
    assert result['location']==[143,67,80,44]
    blank=match_candidate(np.full_like(scene,27),t,[90,20,220,150])
    assert blank['score']<.9 or blank['structure']<.8


def test_local_roi_cannot_accept_evidence_elsewhere():
    t=np.zeros((40,80,4),np.uint8);t[:,:,3]=255
    cv2.putText(t,'YES',(4,29),0,.7,(230,230,230,255),2)
    t=make_candidate(t,'ink',None)
    scene=np.zeros((180,300,3),np.uint8);scene[90:130,190:270]=t[:,:,:3]
    r=match_candidate(scene,t,[0,0,150,170])
    assert r['score']<.9 or r['structure']<.8


def test_empty_mask_is_an_error_not_a_successful_template():
    source=np.full((50,50,4),30,np.uint8);source[:,:,3]=255
    with pytest.raises(ValueError):make_candidate(source,'white',None)


def test_review_silhouette_preserves_colors_and_existing_transparency():
    source=np.full((20,40,4),180,np.uint8);source[:2,:,3]=0
    mask=np.zeros((20,40),np.uint8);mask[:15,5:35]=255
    result=apply_review_mask(source,mask)
    assert np.array_equal(result[:,:,:3],source[:,:,:3])
    assert not result[:2,:,3].any() and not result[:,0,3].any()
    assert result[5,10,3]==180
    for invalid in [np.zeros((20,40),np.uint8),np.ones((10,40),np.uint8),np.ones((20,40,3),np.uint8),np.ones((20,40),np.float32)]:
        with pytest.raises(ValueError):apply_review_mask(source,invalid)


def test_low_contrast_description_mask_keeps_text_without_card_background():
    source=np.zeros((80,160,4),np.uint8);source[:,:,:3]=(70,45,60);source[:,:,3]=255
    cv2.putText(source,'TEXT',(12,50),0,.8,(140,135,145,255),2)
    result=make_candidate(source,'regions',[[0,0,1,1,'glyph']])
    assert np.array_equal(result[:,:,:3],source[:,:,:3])
    assert result[35:50,14:85,3].any()
    assert not result[:10,:,3].any()
    assert np.count_nonzero(result[:,:,3])<source.shape[0]*source.shape[1]/3
