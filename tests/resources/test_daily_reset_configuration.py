"""Shared date-reset assets load only for explicit consumers."""
import json
import shutil
from pathlib import Path

import pytest
import yaml

from e7auto.configuration.daily_reset import DAILY_RESET_KEYS, load_daily_reset
from e7auto.configuration.loader import load_config
from e7auto.configuration.models import ConfigError
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.features.penguin.configuration import with_penguin_config
from e7auto.resources.templates import TemplateRepository
from tests.helpers.paths import ROOT


@pytest.fixture
def installation(tmp_path):
    (tmp_path/'config').mkdir()
    for name in ('internal.yaml','chaos.yaml'):
        shutil.copyfile(ROOT/'config'/name,tmp_path/'config'/name)
    for feature in ('common','chaos','shop','penguin'):
        shutil.copytree(ROOT/'assets/templates'/feature,tmp_path/'assets/templates'/feature)
    return tmp_path


def path_for(root,key):
    base=root/'assets/templates/common'
    catalog=json.loads((base/'manifest.json').read_text(encoding='utf-8'))
    return base/catalog['templates'][key]['file']


@pytest.mark.parametrize('lineup',['dark_twins','budget'])
def test_both_lineups_load_shared_daily_resources_without_chaos_copies(installation,lineup):
    config=with_chaos_config(load_config(installation/'config/internal.yaml',template_profile='chaos'),lineup_id=lineup)
    templates=TemplateRepository(config)
    for key in DAILY_RESET_KEYS:
        assert config.template_paths[key]==path_for(installation,key)
        assert config.chaos.scenes[key].template_key==key
        assert templates.get(key).mask.any()
        assert not list((installation/'assets/templates/chaos').rglob(key+'.png'))
    assert config.chaos.daily_reset.observe_ms==2000
    assert config.chaos.scenes['date_change_close'].region.width==390
    assert templates.get('date_change_close').image.shape[:2]==(41,147)
    assert config.chaos.scenes['buff_effect_close'] == config.chaos.scenes['login_reward_close']
    assert config.chaos.scenes['buff_effect_title'].region.x == 280
    assert 'buff_effect_close' not in config.template_paths


@pytest.mark.parametrize('key',sorted(DAILY_RESET_KEYS))
@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_consumer_rejects_missing_or_corrupt_shared_resource(installation,key,damage):
    p=path_for(installation,key)
    if damage=='missing':p.unlink()
    else:p.write_bytes(b'bad png')
    base=load_config(installation/'config/internal.yaml',template_profile='chaos')
    for lineup in ('dark_twins','budget'):
        with pytest.raises(ConfigError,match='Daily reset'):
            with_chaos_config(base,lineup_id=lineup)


@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_non_consumers_never_read_daily_resources_or_settings(installation,damage):
    for key in DAILY_RESET_KEYS:
        p=path_for(installation,key)
        if damage=='missing':p.unlink()
        else:p.write_bytes(b'bad png')
    p=installation/'config/internal.yaml'
    raw=yaml.safe_load(p.read_text(encoding='utf-8'));raw['daily_reset']={'invalid':True}
    p.write_text(yaml.safe_dump(raw),encoding='utf-8')
    shop=load_config(p)
    penguin=with_penguin_config(load_config(p,template_profile='penguin'))
    for config in (shop,penguin):
        assert not DAILY_RESET_KEYS & config.template_paths.keys()
        assert TemplateRepository(config)


def test_other_feature_resources_missing_do_not_block_daily_reset(installation):
    shutil.rmtree(installation/'assets/templates/shop')
    shutil.rmtree(installation/'assets/templates/penguin')
    config=with_chaos_config(load_config(installation/'config/internal.yaml',template_profile='chaos'))
    assert DAILY_RESET_KEYS <= config.template_paths.keys()


@pytest.mark.parametrize('field,value',[
    ('observe_ms',-1),('observe_ms',15000),('stable_frames',1),('poll_ms',2000),
    ('close_retry_ms',100),('close_max_attempts',False),('unknown',1),
])
def test_invalid_shared_timing_is_rejected(installation,field,value):
    p=installation/'config/internal.yaml';raw=yaml.safe_load(p.read_text(encoding='utf-8'))
    raw['daily_reset'][field]=value;p.write_text(yaml.safe_dump(raw),encoding='utf-8')
    with pytest.raises(ConfigError,match='Daily reset'):
        load_daily_reset(load_config(p,template_profile='chaos'))


@pytest.mark.parametrize('change',['missing_key','unknown_key','roi','threshold'])
def test_invalid_shared_scene_is_rejected(installation,change):
    p=installation/'config/internal.yaml';raw=yaml.safe_load(p.read_text(encoding='utf-8'))
    scenes=raw['daily_reset']['templates']
    if change=='missing_key':del scenes['date_change_title']
    elif change=='unknown_key':scenes['unknown']=scenes['date_change_title']
    elif change=='roi':scenes['date_change_title']['roi']=[2300,0,100,100]
    else:scenes['date_change_title']['threshold']=float('nan')
    p.write_text(yaml.safe_dump(raw),encoding='utf-8')
    with pytest.raises(ConfigError,match='Daily reset'):
        load_daily_reset(load_config(p,template_profile='chaos'))
