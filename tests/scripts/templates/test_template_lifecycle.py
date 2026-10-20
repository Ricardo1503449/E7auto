import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from scripts.common.candidates import write_candidate_png
from scripts.templates import register, check_changes
from tests.helpers.template_projects import project  # noqa: F401


def lifecycle_plan(project, operation):
    root,candidate,source=project
    data=(root/'assets/templates/shop/entry.png').read_bytes()
    image=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_UNCHANGED)
    write_candidate_png(candidate,image,png_bytes=data)
    metadata={'operation':operation,'previous_filename':'entry.png','source':'native test pixels'}
    filename=None
    if operation=='relocate':
        filename='controls/entry.png';metadata['target_filename']=filename
    else:metadata['retirement_reason']='awaiting canonical three-choice source'
    source.write_text(yaml.safe_dump(metadata),encoding='utf8')
    return register.prepare(root,'shop','entry',candidate,source,filename,operation=operation)


@pytest.mark.parametrize('operation',['relocate','retire'])
def test_lifecycle_prepare_is_read_only_and_apply_keeps_provenance_and_exact_bytes(project,operation):
    root,_,_=project;before=check_changes.snapshot(root)
    plan=lifecycle_plan(project,operation)
    assert check_changes.snapshot(root)==before
    tx=register.apply(root,plan)
    catalog=json.loads((root/'assets/templates/shop/manifest.json').read_bytes())
    assert not (root/'assets/templates/shop/entry.png').exists()
    assert catalog['templates']['other']==json.loads(before['assets/templates/shop/manifest.json'])['templates']['other']
    if operation=='relocate':
        assert (root/'assets/templates/shop/controls/entry.png').read_bytes()==before['assets/templates/shop/entry.png']
        assert catalog['templates']['entry']['file']=='controls/entry.png'
    else:
        assert 'entry' not in catalog['templates'] and 'entry' in catalog['retired_templates']
    assert check_changes.check(before,check_changes.snapshot(root))==([],1)
    assert register.recover(root,tx)=='committed'


@pytest.mark.parametrize('operation',['relocate','retire'])
def test_lifecycle_rejects_changed_pixels_and_changed_registered_files(project,operation):
    root,candidate,source=project;plan=lifecycle_plan(project,operation)
    write_candidate_png(candidate,np.zeros((12,10,3),np.uint8))
    with pytest.raises(ValueError,match='preserve'):
        register.prepare(root,'shop','entry',candidate,source,plan['filename'],operation=operation)
    plan=lifecycle_plan(project,operation)
    (root/'assets/templates/shop/entry.png').write_bytes(b'external edit')
    with pytest.raises(ValueError,match='integrity|changed'):
        register.apply(root,plan)
    assert (root/'assets/templates/shop/entry.png').read_bytes()==b'external edit'


def test_relocation_refuses_existing_destination_and_invalid_source_target(project):
    root,candidate,source=project;plan=lifecycle_plan(project,'relocate')
    destination=root/'assets/templates/shop/controls/entry.png';destination.parent.mkdir();destination.write_bytes(b'user file')
    with pytest.raises(ValueError,match='already exists'):
        register.prepare(root,'shop','entry',candidate,source,plan['filename'],operation='relocate')
    destination.unlink()
    with pytest.raises(ValueError,match='target'):
        register.prepare(root,'shop','entry',candidate,source,'other-path.png',operation='relocate')


@pytest.mark.parametrize('operation',['relocate','retire'])
def test_deletion_failure_after_unlink_rolls_back_every_formal_file(project,operation,monkeypatch):
    root,_,_=project;before=check_changes.snapshot(root);plan=lifecycle_plan(project,operation)
    target=root/'assets/templates/shop/entry.png';original=Path.unlink;failed=[]
    def unlink(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if path==target and not failed:
            failed.append(True);raise OSError('failure after old PNG deletion')
        return result
    monkeypatch.setattr(Path,'unlink',unlink)
    with pytest.raises(ValueError,match='rolled back'):
        register.apply(root,plan)
    assert check_changes.snapshot(root)==before


def test_external_write_after_deletion_is_preserved_as_recovery_conflict(project,monkeypatch):
    root,_,_=project;plan=lifecycle_plan(project,'retire');target=root/'assets/templates/shop/entry.png'
    original=Path.unlink
    def unlink(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if path==target:
            path.write_bytes(b'external replacement');raise OSError('external race')
        return result
    monkeypatch.setattr(Path,'unlink',unlink)
    with pytest.raises(ValueError,match='recover transaction'):
        register.apply(root,plan)
    assert target.read_bytes()==b'external replacement'
    transaction=next((root/'artifacts/template-registration/transactions').iterdir())
    with pytest.raises(ValueError,match='conflict'):
        register.recover(root,transaction.name)


def test_retired_key_can_be_registered_again_from_a_new_canonical_source(project):
    root,candidate,source=project
    register.apply(root,lifecycle_plan(project,'retire'))
    write_candidate_png(candidate,np.full((8,9,3),90,np.uint8))
    source.write_text('source: new canonical choice card\n',encoding='utf8')
    plan=register.prepare(root,'shop','entry',candidate,source,'loot/entry.png')
    register.apply(root,plan)
    catalog=json.loads((root/'assets/templates/shop/manifest.json').read_bytes())
    assert 'entry' not in catalog['retired_templates']
    assert catalog['templates']['entry']['file']=='loot/entry.png'


def test_guard_rejects_manual_move_and_removal_without_lifecycle_record(project):
    root,candidate,source=project
    register.apply(root,register.prepare(root,'shop','entry',candidate,source))
    before=check_changes.snapshot(root)
    for operation in ['move','remove']:
        after=dict(before);catalog=json.loads(after['assets/templates/shop/manifest.json'])
        if operation=='move':
            after['assets/templates/shop/controls/entry.png']=after.pop('assets/templates/shop/entry.png')
            catalog['templates']['entry']['file']='controls/entry.png'
        else:
            after.pop('assets/templates/shop/entry.png');del catalog['templates']['entry']
        after['assets/templates/shop/manifest.json']=register.encoded(catalog)
        problems,_=check_changes.check(before,after)
        assert any('provenance' in p for p in problems)


def test_byte_preserving_export_checks_pixels_before_writing(project):
    _,candidate,_=project;image=np.zeros((8,9,3),np.uint8)
    encoded=cv2.imencode('.png',image,[cv2.IMWRITE_PNG_COMPRESSION,9])[1].tobytes()
    write_candidate_png(candidate,image,png_bytes=encoded)
    assert candidate.read_bytes()==encoded
    with pytest.raises(ValueError,match='match candidate pixels'):
        write_candidate_png(candidate,np.ones_like(image),png_bytes=encoded)
    assert candidate.read_bytes()==encoded
