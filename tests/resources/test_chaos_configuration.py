import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from e7auto.configuration.loader import load_config
from e7auto.configuration.models import ConfigError
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.features.chaos.lineups import BUDGET_SCENES, DARK_TWINS_SCENES, RecruitmentPolicy, get_lineup
from e7auto.resources.templates import TemplateRepository
from tests.helpers.paths import ROOT


@pytest.fixture
def installation(tmp_path):
    (tmp_path / "config").mkdir()
    for name in ("internal.yaml", "chaos.yaml"):
        shutil.copyfile(ROOT / "config" / name, tmp_path / "config" / name)
    # No shop, penguin, shared digits, calibration docs or artifacts are installed.
    for feature in ("chaos", "common"):
        target = tmp_path / "assets/templates" / feature
        target.mkdir(parents=True)
        shutil.copyfile(ROOT / "assets/templates" / feature / "manifest.json", target / "manifest.json")
        for entry in json.loads((target / "manifest.json").read_text(encoding="utf8"))["templates"].values():
            if feature == "common" and not entry["file"].startswith(("network/", "daily_reset/")):
                continue
            destination = target / entry["file"];destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "assets/templates" / feature / entry["file"], destination)
    return tmp_path


def load(root):
    return with_chaos_config(load_config(root / "config/internal.yaml", template_profile="chaos"))


@pytest.mark.parametrize('value', [0, -1, True, 1.5, None])
@pytest.mark.parametrize('key', ['loot_entry_settle_ms', 'supply_loot_entry_settle_ms'])
def test_invalid_loot_entry_delay_rejects_start(tmp_path, value, key):
    raw = yaml.safe_load((ROOT/'config/chaos.yaml').read_text(encoding='utf8'))
    raw['timing'][key] = value
    path = tmp_path/'chaos.yaml'
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    base = load_config(ROOT/'config/internal.yaml', template_profile='chaos')
    with pytest.raises(ConfigError, match='timing'):
        with_chaos_config(base, path)


def test_missing_supply_loot_entry_delay_rejects_start(tmp_path):
    raw = yaml.safe_load((ROOT/'config/chaos.yaml').read_text(encoding='utf8'))
    del raw['timing']['supply_loot_entry_settle_ms']
    path = tmp_path/'chaos.yaml'
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    base = load_config(ROOT/'config/internal.yaml', template_profile='chaos')
    with pytest.raises(ConfigError, match='timing'):
        with_chaos_config(base, path)


@pytest.mark.parametrize('key', ['event_choice_retry_ms', 'event_choice_max_attempts'])
@pytest.mark.parametrize('value', [0, -1, True, 1.5, None, 'missing'])
def test_invalid_event_choice_retry_parameters_reject_start(tmp_path, key, value):
    raw = yaml.safe_load((ROOT/'config/chaos.yaml').read_text(encoding='utf8'))
    if value == 'missing':
        del raw['timing'][key]
    else:
        raw['timing'][key] = value
    path = tmp_path/'chaos.yaml'
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    base = load_config(ROOT/'config/internal.yaml', template_profile='chaos')
    with pytest.raises(ConfigError, match='timing'):
        with_chaos_config(base, path)


@pytest.mark.parametrize('value', [300, 15000])
def test_event_choice_retry_interval_must_fit_between_settle_and_page_timeout(tmp_path, value):
    raw = yaml.safe_load((ROOT/'config/chaos.yaml').read_text(encoding='utf8'))
    raw['timing']['event_choice_retry_ms'] = value
    path = tmp_path/'chaos.yaml'
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    base = load_config(ROOT/'config/internal.yaml', template_profile='chaos')
    with pytest.raises(ConfigError, match='choice timing'):
        with_chaos_config(base, path)


@pytest.mark.parametrize('key', ['map_node_retry_ms', 'map_node_max_attempts'])
@pytest.mark.parametrize('value', [0, -1, True, 1.5, None, 'missing'])
def test_invalid_map_node_retry_parameters_reject_start(tmp_path, key, value):
    raw = yaml.safe_load((ROOT/'config/chaos.yaml').read_text(encoding='utf8'))
    if value == 'missing':
        del raw['timing'][key]
    else:
        raw['timing'][key] = value
    path = tmp_path/'chaos.yaml'
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    base = load_config(ROOT/'config/internal.yaml', template_profile='chaos')
    with pytest.raises(ConfigError, match='timing'):
        with_chaos_config(base, path)


@pytest.mark.parametrize('value', [300, 15000])
def test_map_node_retry_interval_must_fit_between_settle_and_page_timeout(tmp_path, value):
    raw = yaml.safe_load((ROOT/'config/chaos.yaml').read_text(encoding='utf8'))
    raw['timing']['map_node_retry_ms'] = value
    path = tmp_path/'chaos.yaml'
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    base = load_config(ROOT/'config/internal.yaml', template_profile='chaos')
    with pytest.raises(ConfigError, match='map node timing'):
        with_chaos_config(base, path)


@pytest.mark.parametrize('key,value', [
    ('sample_ms', 0), ('active_min_angle_deg', 0), ('active_min_angle_deg', 90),
    ('active_min_angle_deg', 11), ('static_confirm_frames', 2), ('static_confirm_frames', 151),
    ('static_max_gap_ms', 99), ('static_max_gap_ms', 15000), ('static_max_angle_deg', 10),
    ('core_radius', 23), ('core_radius', 0), ('reference_score', 0),
    ('center_search_radius', 0), ('center_search_radius', 20),
    ('reference_margin', float('nan')), ('reference_margin', 1.1), ('angle_step', 90),
    ('visible_score', True), ('visible_score', float('nan')),
])
def test_invalid_auto_reference_parameters_reject_start(tmp_path, key, value):
    raw = yaml.safe_load((ROOT/'config/chaos.yaml').read_text(encoding='utf8'))
    raw['auto_motion'][key] = value
    path = tmp_path/'chaos.yaml'
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    base = load_config(ROOT/'config/internal.yaml', template_profile='chaos')
    with pytest.raises(ConfigError, match='motion'):
        with_chaos_config(base, path)


@pytest.mark.parametrize('lineup_id,inactive', [('dark_twins', BUDGET_SCENES), ('budget', DARK_TWINS_SCENES)])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_lineup_does_not_load_other_lineup_resources(installation, lineup_id, inactive, damage):
    for key in inactive:
        path = image_path(installation, 'chaos', key)
        if damage == 'missing':path.unlink()
        else:path.write_bytes(b'bad PNG')
    config = with_chaos_config(load_config(installation / 'config/internal.yaml', template_profile='chaos'), lineup_id=lineup_id)
    assert not inactive & config.chaos.scenes.keys()
    assert not any(key.removeprefix('chaos:') in inactive for key in config.template_paths)
    assert TemplateRepository(config).get('chaos:hero_ivanna').image.size > 0


@pytest.mark.parametrize('key', sorted(BUDGET_SCENES))
@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'missing_scene'])
def test_budget_resources_and_bindings_are_required(installation, key, damage):
    if damage == 'missing_scene':
        p = installation / 'config/chaos.yaml'
        raw = yaml.safe_load(p.read_text(encoding='utf8'));del raw['templates'][key]
        p.write_text(yaml.safe_dump(raw), encoding='utf8')
    else:
        p = image_path(installation, 'chaos', key)
        if damage == 'missing':p.unlink()
        else:p.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        with_chaos_config(load_config(installation / 'config/internal.yaml', template_profile='chaos'), lineup_id='budget')


@pytest.mark.parametrize('lineup_id', ['dark_twins', 'budget'])
@pytest.mark.parametrize('key,damage', [('hero_ivanna','missing'), ('hero_teacher','corrupt')])
def test_both_lineups_require_shared_hero_resources(installation, lineup_id, key, damage):
    p = image_path(installation, 'chaos', key)
    if damage == 'missing':p.unlink()
    else:p.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        with_chaos_config(load_config(installation / 'config/internal.yaml', template_profile='chaos'), lineup_id=lineup_id)


@pytest.mark.parametrize('lineup_id', ['dark_twins', 'budget'])
@pytest.mark.parametrize('field', ['rank_priority', 'recruitment_policy'])
def test_lineup_policy_mismatch_rejects_start(installation, lineup_id, field):
    p = installation / 'config/chaos.yaml';raw = yaml.safe_load(p.read_text(encoding='utf8'))
    raw['lineups'][lineup_id][field] = [] if field == 'rank_priority' else 'unknown_policy'
    p.write_text(yaml.safe_dump(raw), encoding='utf8')
    with pytest.raises(ConfigError, match='invalid lineup'):
        with_chaos_config(load_config(installation / 'config/internal.yaml', template_profile='chaos'), lineup_id=lineup_id)


def test_disabling_later_recruitment_does_not_switch_scene_dependencies(installation, monkeypatch):
    # A roster without later recruitment can still use dark-twins identities.
    # Resource declarations must not be inferred from the recruitment policy.
    lineup = replace(get_lineup('dark_twins'), recruitment_policy=RecruitmentPolicy.NONE)
    p = installation / 'config/chaos.yaml';raw = yaml.safe_load(p.read_text(encoding='utf8'))
    raw['lineups']['dark_twins']['recruitment_policy'] = 'none'
    p.write_text(yaml.safe_dump(raw), encoding='utf8')
    for key in BUDGET_SCENES:
        image_path(installation, 'chaos', key).unlink()
    monkeypatch.setattr('e7auto.features.chaos.configuration.get_lineup', lambda key: lineup)
    config = load(installation)
    assert DARK_TWINS_SCENES <= config.chaos.scenes.keys()
    assert not BUDGET_SCENES & config.chaos.scenes.keys()
    assert config.chaos.lineup.recruitment_policy is RecruitmentPolicy.NONE
    assert TemplateRepository(config).get('chaos:hero_twins').image.size > 0


def test_unknown_lineup_is_rejected_without_defaulting():
    with pytest.raises(ConfigError, match='unknown Chaos lineup'):
        with_chaos_config(load_config(ROOT / 'config/internal.yaml', template_profile='chaos'), lineup_id='unknown')


@pytest.mark.parametrize('key', ['filter_icon_fire', 'filter_fire_selected', 'hero_ivanna',
                                 'selected_name_ivanna', 'recruited_ivanna'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'missing_scene'])
def test_ivanna_recruitment_resources_are_required(installation, key, damage):
    if damage == 'missing_scene':
        path = installation / 'config/chaos.yaml'
        raw = yaml.safe_load(path.read_text(encoding='utf8'))
        del raw['templates'][key]
        path.write_text(yaml.safe_dump(raw), encoding='utf8')
    else:
        path = image_path(installation, 'chaos', key)
        if damage == 'missing':path.unlink()
        else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_preserved_peini_resources_are_not_runtime_dependencies(installation, damage):
    catalog = json.loads((installation / 'assets/templates/chaos/manifest.json').read_text(encoding='utf8'))
    for key in ('hero_peini', 'selected_name_peini', 'recruited_peini'):
        assert key in catalog['templates']
        path = image_path(installation, 'chaos', key)
        if damage == 'missing':path.unlink()
        else:path.write_bytes(b'bad PNG')
    config = load(installation)
    assert dict(config.chaos.heroes[1]) == {'key': 'ivanna', 'ticket': 'soul', 'attribute': 'fire'}
    assert not any('peini' in key for key in config.template_paths)
    assert not any('peini' in key for key in config.chaos.scenes)
    repository = TemplateRepository(config)
    assert repository.get('chaos:hero_ivanna').image.size > 0


@pytest.mark.parametrize('key,attribute', [('peini', 'water'), ('ivanna', 'water')])
def test_initial_soul_hero_must_be_ivanna_with_fire_attribute(installation, key, attribute):
    path = installation / 'config/chaos.yaml'
    raw = yaml.safe_load(path.read_text(encoding='utf8'))
    raw['lineups']['dark_twins']['heroes'][1].update(key=key, attribute=attribute)
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    with pytest.raises(ConfigError, match='invalid initial hero order'):load(installation)


@pytest.mark.parametrize('key', ['story_skip', 'story_skip_prompt'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_story_resources_are_required(installation, key, damage):
    path = image_path(installation, 'chaos', key)
    if damage == 'missing':path.unlink()
    else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)


def test_story_bindings_reuse_confirm_with_approved_rois(installation):
    config = load(installation)
    scenes = config.chaos.scenes
    assert scenes['story_skip_confirm'].template_key == scenes['unclaimed_confirm'].template_key
    assert scenes['story_skip_confirm'].region == scenes['unclaimed_confirm'].region
    assert scenes['story_skip_prompt'].action is None
    repository = TemplateRepository(config)
    for key in ['story_skip', 'story_skip_prompt']:
        assert repository.get(scenes[key].template_key).mask.any()
        assert scenes[key].threshold == (.93 if key == 'story_skip' else .97)
        assert scenes[key].structure == .8
    assert scenes['story_skip_confirm'].threshold == .97
    assert scenes['story_skip_confirm'].structure == .8
    assert repository.get(scenes['story_skip'].template_key).image.shape == (33, 70, 3)


@pytest.mark.parametrize('key', ['story_timeout_ms', 'story_retry_ms', 'story_max_attempts'])
@pytest.mark.parametrize('value', [None, 0])
def test_story_timing_is_required_and_positive(installation, key, value):
    path = installation / 'config/chaos.yaml'
    raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    if value is None:del raw['timing'][key]
    else:raw['timing'][key]=value
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    with pytest.raises(ConfigError, match='invalid Chaos timing'):load(installation)


@pytest.mark.parametrize('missing', [False, True])
@pytest.mark.parametrize('key', ['battle_result_settle_ms', 'battle_signal_poll_ms'])
def test_battle_result_timing_is_required_and_positive(installation, missing, key):
    path = installation / 'config/chaos.yaml'
    raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    if missing:
        del raw['timing'][key]
    else:
        raw['timing'][key] = 0
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    with pytest.raises(ConfigError, match='invalid Chaos timing'):
        load(installation)


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
@pytest.mark.parametrize('key', [
    'investment_insufficient_item', 'health_restore', 'health_restore_unavailable',
    'shop_health_entry', 'purchase_health_restore', 'health_restore_done',
])
def test_shop_health_resources_are_required(installation, damage, key):
    path = image_path(installation, 'chaos', key)
    if damage == 'missing':path.unlink()
    else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)


def test_investment_entry_uses_shared_button_without_old_numbered_template(installation):
    image_path(installation, 'chaos', 'investment_entry').unlink()
    config = load(installation)
    assert config.chaos.scenes['investment_entry'].template_key == 'chaos:shop_health_entry'
    assert config.chaos.scenes['investment_entry'].template_key == config.chaos.scenes['shop_health_entry'].template_key
    assert 'chaos:investment_entry' not in config.template_paths


def image_path(root, feature, key):
    directory=root/'assets/templates'/feature
    catalog=json.loads((directory/'manifest.json').read_text(encoding='utf8'))
    return directory/catalog['templates'][key]['file']


@pytest.mark.parametrize('lineup_id', ['dark_twins', 'budget'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'missing_scene', 'missing_index'])
def test_torn_map_initial_variant_resource_and_bindings_required(installation, lineup_id, damage):
    key = 'event_torn_map_path'
    if damage in ('missing_scene', 'missing_index'):
        path = installation/'config/chaos.yaml'
        raw = yaml.safe_load(path.read_text(encoding='utf8'))
        if damage == 'missing_scene':
            del raw['templates'][key]
        else:
            del raw['event_recognition']['templates'][key]
        path.write_text(yaml.safe_dump(raw), encoding='utf8')
    else:
        path = image_path(installation, 'chaos', key)
        if damage == 'missing':
            path.unlink()
        else:
            path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        with_chaos_config(load_config(installation/'config/internal.yaml', template_profile='chaos'),
                          lineup_id=lineup_id)


@pytest.mark.parametrize('lineup_id', ['dark_twins', 'budget'])
def test_torn_map_variant_loads_without_other_feature_assets(installation, lineup_id):
    config = with_chaos_config(load_config(installation/'config/internal.yaml', template_profile='chaos'),
                               lineup_id=lineup_id)
    assert 'event_torn_map_path' in config.chaos.event_recognition['templates']
    assert TemplateRepository(config).get('chaos:event_torn_map_path').image.shape[:2] == (157, 1216)


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_vestment_available_subchoice_resource_is_required(installation, damage):
    path = image_path(installation, 'chaos', 'event_vestment_with_loot')
    if damage == 'missing':path.unlink()
    else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)


def test_isolated_loading_and_resource_aliases(installation):
    config = load(installation)
    assert len(config.chaos.scenes) == 270
    assert len(config.template_paths) == 263
    repository = TemplateRepository(config)
    for names in [("camp_leave", "supply_leave", "shop_leave"), ("elite_choose_reward", "boss_choose_reward"), ("unclaimed_confirm", "shop_leave_confirm"), ("investment_entry", "shop_health_entry")]:
        images = [repository.get(config.chaos.scenes[n].template_key) for n in names]
        assert all(image is images[0] for image in images)
    assert config.chaos.scenes["boss_choose_reward"].region != config.chaos.scenes["elite_choose_reward"].region
    assert config.chaos.scenes["event_torch"].region.width == 2322
    assert not any("digit" in name or "penguin" in name for name in config.template_paths)
    assert repository.get(config.chaos.scenes['rank_close'].template_key) is repository.get(config.chaos.scenes['event_reward_close'].template_key)
    assert config.chaos.loot_priorities['loot_summon_mark']==2
    assert len(config.chaos.available_loot)==76
    assert 'loot_summon_mark' in config.chaos.available_loot
    assert 'chaos:loot_summon_mark' in config.template_paths
    assert config.chaos.pending_loot_templates==()


@pytest.mark.parametrize('damage', ['missing', 'unknown', 'layout', 'outside_template', 'outside_slot', 'weight', 'scene'])
def test_event_index_registration_is_required_and_validated(installation,damage):
    path=installation/'config/chaos.yaml';raw=yaml.safe_load(path.read_text(encoding='utf8'))
    entries=raw['event_recognition']['templates'];entry=entries['event_observatory']
    if damage=='missing':del entries['event_observatory']
    if damage=='unknown':entries['event_unknown']=dict(entry)
    if damage=='layout':entry['layout']=3
    if damage=='outside_template':entry['titles'][0][2]=9999
    if damage=='outside_slot':entry['origin'][0]=0
    if damage=='weight':entry['weights'][0]=float('nan')
    if damage=='scene':raw['templates']['event_observatory']['search_roi']='map'
    path.write_text(yaml.safe_dump(raw),encoding='utf8')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('damage',['missing','corrupt'])
@pytest.mark.parametrize('key',['event_backpack','event_spirit_door','event_ghost'])
def test_new_event_options_are_required_resources(installation, damage, key):
    path = image_path(installation, 'chaos', key)
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        load(installation)


@pytest.mark.parametrize('damage',['missing','corrupt'])
@pytest.mark.parametrize('key',[
    'event_erased_records', 'event_fairy_dance', 'event_blue_flame_altar',
    'event_broken_mask', 'event_ancient_corridor',
])
def test_five_event_options_are_required_resources(installation, damage, key):
    path = image_path(installation, 'chaos', key)
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        load(installation)


@pytest.mark.parametrize('damage',['missing','corrupt'])
@pytest.mark.parametrize('key', [
    'event_angry_inscription', 'event_ancient_stele', 'event_blood_meteorite',
    'event_windy_cliff', 'event_forked_path', 'event_starlight_flower', 'event_grieving_woman',
])
def test_september24_event_resources_are_required(installation, damage, key):
    path = image_path(installation, 'chaos', key)
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        load(installation)


@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_loot_anchor_is_a_required_resource(installation,damage):
    path=image_path(installation,'chaos','loot_reroll_anchor')
    if damage=='missing':path.unlink()
    else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('key', ['event_withered_garden', 'event_prayer_room', 'event_golden_patterns', 'event_clear_pond'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_garden_prayer_golden_pond_resources_are_required(installation, key, damage):
    path = image_path(installation, 'chaos', key)
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        load(installation)


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_pond_branch_resource_is_required(installation, damage):
    path = image_path(installation, 'chaos', 'event_clear_pond_low_health')
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        load(installation)


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_warning_sign_branch_resource_is_required(installation, damage):
    path = image_path(installation, 'chaos', 'event_warning_sign_low_health')
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        load(installation)


@pytest.mark.parametrize('weights', [[0., 0.], [-.1, 1.], [1., 1.1], [True, 0.], [1., float('nan')]])
def test_pond_index_invalid_title_weights_are_rejected(weights):
    from copy import deepcopy
    from e7auto.features.chaos.configuration import _event_recognition
    from e7auto.resources.templates import TemplateRepository

    config = load(ROOT)
    repo = TemplateRepository(config)
    settings = config.chaos
    sizes = {scene.template_key: (repo.get(scene.template_key).image.shape[1],
                                 repo.get(scene.template_key).image.shape[0])
             for scene in settings.scenes.values()}
    index = deepcopy(settings.event_recognition)
    index['templates']['event_clear_pond_low_health']['weights'] = weights
    with pytest.raises(ValueError, match='invalid event title metadata'):
        _event_recognition(index, sizes, settings.scenes, settings.regions)


@pytest.mark.parametrize('field,bad', [
    ('contrast_kernel', 2), ('contrast_kernel', 8), ('contrast_kernel', 33),
    ('contrast_kernel', True), ('feature_shift', -1), ('feature_shift', 7),
    ('feature_shift', 1.5), ('similarity_min', 0), ('similarity_min', 1),
    ('alignment_tolerance', -1), ('alignment_tolerance', 3), ('alignment_tolerance', True),
    ('similarity_min', float('nan')), ('contrast_ratio_min', -0.1),
    ('contrast_ratio_min', float('inf')), ('feature_change_max', True),
    ('feature_change_max', 0),
])
def test_invalid_event_readiness_calibration_is_rejected(field, bad):
    from copy import deepcopy
    from e7auto.features.chaos.configuration import _event_recognition

    settings = load(ROOT).chaos
    index = deepcopy(settings.event_recognition)
    index['readiness'][field] = bad
    # Readiness is checked before template geometry; invalid settings cannot
    # silently fall back to exhaustive discovery on a transition frame.
    with pytest.raises(ValueError, match='invalid event readiness'):
        _event_recognition(index, {}, settings.scenes, settings.regions)


@pytest.mark.parametrize('bad', [None, {}, {'unknown': 1}])
def test_incomplete_event_readiness_settings_are_rejected(bad):
    from copy import deepcopy
    from e7auto.features.chaos.configuration import _event_recognition

    settings = load(ROOT).chaos
    index = deepcopy(settings.event_recognition)
    index['readiness'] = bad
    with pytest.raises(ValueError, match='invalid event readiness settings'):
        _event_recognition(index, {}, settings.scenes, settings.regions)


@pytest.mark.parametrize('damage',['missing','corrupt'])
@pytest.mark.parametrize('key',['loot_alchemy_flask','loot_sealed_letter','loot_temple_investigation_badge','loot_painting_suppression_night'])
def test_september23_loot_requires_registered_resources(installation,damage,key):
    path=image_path(installation,'chaos',key)
    if damage=='missing':path.unlink()
    else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)


LATE_LOOT_PRIORITIES = {
    'loot_beacon_ring': 0,
    'loot_summoning_ritual_disk': 0,
    'loot_painting_flame_hymn': 1,
    'loot_tablet_spirit_king_invitation': 2,
    'loot_statue_blind_saint': 0,
    'loot_painting_road_to_sky_gate': 0,
}


SEPTEMBER24_LOOT_PRIORITIES = {
    'loot_watcher_breastplate': 1,
    'loot_glowing_stone': 2,
    'loot_statue_watcher': 1,
}


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
@pytest.mark.parametrize('key', SEPTEMBER24_LOOT_PRIORITIES)
def test_september24_loot_requires_registered_resources(installation, damage, key):
    path = image_path(installation, 'chaos', key)
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        load(installation)


def test_september24_loot_priorities_and_index_membership(installation):
    from e7auto.features.chaos.loot import LootRecognizer
    from e7auto.vision.matching import TemplateMatcher

    config = load(installation)
    repository = TemplateRepository(config)
    index = LootRecognizer(config.chaos, repository, TemplateMatcher(repository))
    try:
        identities = [identity.key for identity in index.identities]
        assert len(identities) == 76
        for key, priority in SEPTEMBER24_LOOT_PRIORITIES.items():
            assert config.chaos.loot_priorities[key] == priority
            assert identities.count(key) == 1
    finally:
        index.close()


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
@pytest.mark.parametrize('key', LATE_LOOT_PRIORITIES)
def test_late_loot_requires_registered_resources(installation, damage, key):
    path = image_path(installation, 'chaos', key)
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        load(installation)


def test_late_loot_priorities_and_index_membership(installation):
    from e7auto.features.chaos.loot import LootRecognizer
    from e7auto.vision.matching import TemplateMatcher

    config = load(installation)
    repository = TemplateRepository(config)
    index = LootRecognizer(config.chaos, repository, TemplateMatcher(repository))
    try:
        identities = [identity.key for identity in index.identities]
        for key, priority in LATE_LOOT_PRIORITIES.items():
            assert config.chaos.loot_priorities[key] == priority
            assert identities.count(key) == 1
        assert config.chaos.loot_priorities['loot_painting_suppression_night'] == 1
        assert config.chaos.loot_recognition['template_regions']['loot_painting_suppression_night'] == {
            'icon': [84, 0, 157, 120], 'name': [23, 184, 293, 56], 'origin': [-133, -711],
        }
    finally:
        index.close()


@pytest.mark.parametrize('damage',['missing_identity','outside_part','outside_band','invalid_score','zero_candidates','bad_origin'])
def test_loot_index_config_rejects_unsafe_subregions(installation,damage):
    path=installation/'config/chaos.yaml';raw=yaml.safe_load(path.read_text(encoding='utf-8'))
    p=raw['loot_recognition']
    if damage=='missing_identity':del p['template_regions']['loot_old_sword']
    if damage=='outside_part':p['template_regions']['loot_old_sword']['icon']=[0,0,9999,20]
    if damage=='outside_band':p['name_band']=[450,285,390,80]
    if damage=='invalid_score':p['name_structure']=float('nan')
    if damage=='zero_candidates':p['top_k']=0
    if damage=='bad_origin':p['template_regions']['loot_old_sword']['origin']=[0,0]
    path.write_text(yaml.safe_dump(raw),encoding='utf-8')
    with pytest.raises(ConfigError):load(installation)


def test_september22_loot_priorities_follow_approved_review_order(installation):
    config=load(installation)
    expected={
        'moon_charm':1, 'shrine_fragment':2, 'statue_star_child':0,
        'tablet_comet_prophecy':2, 'tablet_silver_oath':2, 'chalice_dark_communion':2,
        'painting_star_mineral':0, 'time_sand':2, 'painting_ruins_below':1,
        'dice_fate':0, 'transcendence_crystal':2, 'statue_whisperer':1,
        'statue_lost_light':2, 'statue_guardian_judgment':1, 'statue_prayer':2,
        'chalice_seal_hymn':2, 'tablet_comet_altar':2, 'starlight_bottle':1,
        'dimension_crystal_pillar':2, 'dice_revival':1, 'dead_compass':2,
        'tablet_flame_record':2, 'guardian_claw':1,
    }
    for name,tier in expected.items():
        key='loot_'+name
        assert key in config.chaos.available_loot
        assert config.chaos.loot_priorities[key]==tier
        scene=config.chaos.scenes[key]
        assert scene.template_key=='chaos:'+key
        assert scene.region==config.chaos.regions['loot_cards']


@pytest.mark.parametrize("relative", ["chaos/shop_leave.png", "chaos/loot_selected.png", "common/network/network_retry.png"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_required_shared_or_deduplicated_resource_rejects_start(installation, relative, damage):
    feature, filename = relative.split('/',1)
    p = image_path(installation,feature,Path(filename).stem)
    if damage == "missing":
        p.unlink()
    else:
        p.write_bytes(b"not a PNG")
    with pytest.raises(ConfigError):
        load(installation)


@pytest.mark.parametrize("change", ["unknown_scene", "missing_scene", "unknown_resource", "wrong_known_resource", "other_feature", "bad_roi", "bad_anchor", "bad_threshold"])
def test_invalid_bindings_fail_closed(installation, change):
    path = installation / "config/chaos.yaml";raw = yaml.safe_load(path.read_text(encoding="utf8"))
    scene = raw["templates"]["camp_leave"]
    if change == "unknown_scene":raw["templates"]["guess_button"] = dict(scene)
    if change == "missing_scene":del raw["templates"]["camp_leave"]
    if change == "unknown_resource":scene["resource"]["key"] = "missing"
    if change == "wrong_known_resource":scene["resource"]["key"] = "purchase_confirm"
    if change == "other_feature":scene["resource"]["feature"] = "shop"
    if change == "bad_roi":raw["rois"][scene["search_roi"]][0] = -1
    if change == "bad_anchor":scene["action"] = [2, .5]
    if change == "bad_threshold":scene["threshold"] = float("nan")
    path.write_text(yaml.safe_dump(raw),encoding="utf8")
    with pytest.raises(ConfigError):load(installation)


def test_gray_reference_does_not_become_a_runtime_dependency(installation):
    image_path(installation,'chaos','node_gray_boss').unlink()
    assert "node_gray_boss" not in load(installation).chaos.scenes


@pytest.mark.parametrize('key',['stage_one','stage_two','stage_three','stage_four','stage_five','stage_01','stage_02','stage_03','stage_04','stage_05','stage_selected','stage_confirm'])
def test_stage_switch_resources_are_required(installation,key):
    image_path(installation,'chaos',key).unlink()
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('target',[0,6,-1,True,1.5,'3'])
def test_invalid_target_stage_rejected(installation,target):
    with pytest.raises(ConfigError):
        with_chaos_config(load_config(installation/'config/internal.yaml',template_profile='chaos'),target_stage=target)


@pytest.mark.parametrize('missing',range(1,6))
def test_missing_competitor_lobby_binding_rejects_any_target(installation,missing):
    path=installation/'config/chaos.yaml';raw=yaml.safe_load(path.read_text(encoding='utf8'))
    raw['stage_selection']['lobby_scenes'].pop(missing)
    path.write_text(yaml.safe_dump(raw),encoding='utf8')
    with pytest.raises(ConfigError,match='all five lobby'):
        with_chaos_config(load_config(installation/'config/internal.yaml',template_profile='chaos'))


@pytest.mark.parametrize('target',range(1,6))
def test_registered_targets_and_stage_resources_load_without_difficulty(installation,target):
    config=with_chaos_config(load_config(installation/'config/internal.yaml',template_profile='chaos'),target_stage=target)
    assert config.chaos.target_stage==target and 'difficulty_one' not in config.chaos.scenes
    assert 'chaos:difficulty_one' not in config.template_paths
    assert all(config.chaos.scenes[f'stage_{n:02d}'].threshold==.985 for n in range(1,6))
    assert all(f'chaos:stage_{n:02d}' in config.template_paths for n in range(1,6))


@pytest.mark.parametrize('value',[0,-.01,1,True,float('nan')])
def test_invalid_lobby_ambiguity_margin_rejects_start(installation,value):
    path=installation/'config/chaos.yaml';raw=yaml.safe_load(path.read_text(encoding='utf8'))
    raw['stage_selection']['lobby_ambiguity_margin']=value
    path.write_text(yaml.safe_dump(raw),encoding='utf8')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('key',['stage_two','stage_four','stage_five'])
def test_corrupt_lobby_template_rejects_start(installation,key):
    image_path(installation,'chaos',key).write_bytes(b'corrupt')
    with pytest.raises(ConfigError):load(installation)


def test_lobby_text_roi_replaces_stage_bar_for_every_stage(installation):
    config = load(installation)
    assert 'stage_bar' not in config.chaos.regions
    region = config.chaos.regions['lobby_stage_text']
    assert (region.x, region.y, region.width, region.height) == (1720, 1025, 220, 90)
    assert all(config.chaos.scenes[key].region == region for key in config.chaos.stage_selection['lobby_scenes'].values())


@pytest.mark.parametrize('key', ['stage_one', 'stage_two', 'stage_three', 'stage_four', 'stage_five'])
def test_lobby_stage_cannot_use_an_unrelated_roi(installation, key):
    path = installation/'config/chaos.yaml';raw = yaml.safe_load(path.read_text(encoding='utf8'))
    raw['templates'][key]['search_roi'] = 'bottom_right'
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    with pytest.raises(ConfigError, match='lobby stage text region'):load(installation)


@pytest.mark.parametrize('offset', [[0, -125], [-250, -125], [500, -125], [250, 0],
                                   [250, -50], [250, -300], [True, -125], [250, -125.5]])
def test_invalid_stage_selector_offset_rejects_start(installation, offset):
    path = installation/'config/chaos.yaml';raw = yaml.safe_load(path.read_text(encoding='utf8'))
    raw['stage_selection']['open_offset'] = offset
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    with pytest.raises(ConfigError, match='opening offset'):load(installation)


def test_lobby_start_requires_actionable_button(installation):
    path = installation/'config/chaos.yaml';raw = yaml.safe_load(path.read_text(encoding='utf8'))
    raw['templates']['start_exploration']['action'] = None
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    with pytest.raises(ConfigError, match='requires a click anchor'):load(installation)


@pytest.mark.parametrize('change',['reverse','outside','zero_steps','huge_limit','zero_distance','huge_distance','fractional_distance','slow_frame_check'])
def test_invalid_stage_drag_configuration_rejects_start(installation,change):
    path=installation/'config/chaos.yaml';raw=yaml.safe_load(path.read_text(encoding='utf8'));s=raw['stage_selection']
    if change=='reverse':s['step_distance_px']=-500
    if change=='outside':s['drag_start']=[1.2,.3]
    if change=='zero_steps':s['step_ms']=0
    if change=='huge_limit':s['max_drags']=1000
    if change=='zero_distance':s['step_distance_px']=0
    if change=='huge_distance':s['step_distance_px']=10000
    if change=='fractional_distance':s['step_distance_px']=1.5
    if change=='slow_frame_check':s['frame_check_ms']=1000
    path.write_text(yaml.safe_dump(raw),encoding='utf8')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('offset', [[0, 5000], [0, 350.5]])
def test_invalid_recruit_click_offset_rejects_start(installation,offset):
    path=installation/'config/chaos.yaml';raw=yaml.safe_load(path.read_text(encoding='utf8'))
    raw['templates']['ticket_thief']['action_offset']=offset
    path.write_text(yaml.safe_dump(raw),encoding='utf8')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('key',['event_trap','prepare_event','event_tablet','event_tablet_continue','event_vestment','event_vestment_no_loot','event_golem','event_giant_eye','event_prison','event_cage','event_altar'])
@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_event_resources_are_required(installation,key,damage):
    p=image_path(installation,'chaos',key)
    if damage=='missing':p.unlink()
    else:p.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('pending',[['loot_summon_mark'],['loot_ominous_crystal'],['loot_summon_mark','loot_summon_mark'],['unknown_loot']])
def test_invalid_pending_loot_is_rejected(installation,pending):
    path=installation/'config/chaos.yaml';raw=yaml.safe_load(path.read_text(encoding='utf8'))
    raw['pending_loot_templates']=pending
    path.write_text(yaml.safe_dump(raw),encoding='utf8')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('key',['loot_summon_mark','loot_tablet_sleeping_forest'])
@pytest.mark.parametrize('damage',['missing','corrupt','missing_scene'])
def test_approved_loot_is_required_including_restored_summon_mark(installation,key,damage):
    if damage=='missing_scene':
        path=installation/'config/chaos.yaml'
        raw=yaml.safe_load(path.read_text(encoding='utf8'))
        del raw['templates'][key]
        raw['pending_loot_templates']=[key]
        path.write_text(yaml.safe_dump(raw),encoding='utf8')
    else:
        path=image_path(installation,'chaos',key)
        if damage=='missing':path.unlink()
        else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('key,value',[
    ('edge_distance_px',0),('edge_samples',11.5),('edge_support_min',.1),
    ('interior_brightness_min',100),('pixel_contrast_min',float('nan')),
])
def test_invalid_triangle_settings_reject_start(installation,key,value):
    path=installation/'config/chaos.yaml';raw=yaml.safe_load(path.read_text(encoding='utf8'))
    raw['text_triangle'][key]=value
    path.write_text(yaml.safe_dump(raw),encoding='utf8')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('key', ['event_warning_sign', 'event_mysterious_footprints', 'event_old_armory', 'event_old_armory_left', 'event_blood_circle', 'event_broken_candlestick'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_warning_footprints_armory_circle_resources_are_required(installation, key, damage):
    path = image_path(installation, 'chaos', key)
    if damage == 'missing':path.unlink()
    else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('key', ['event_grimoire', 'rank_value_1', 'rank_value_2', 'rank_value_3', 'rank_value_4', 'rank_value_5'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_single_rank_resources_are_required(installation, key, damage):
    path=image_path(installation,'chaos',key)
    if damage=='missing':path.unlink()
    else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('case', ['missing_before','missing_after','overlap','outside','too_small'])
def test_invalid_rank_columns_reject_startup(installation, case):
    path=installation/'config/chaos.yaml';raw=yaml.safe_load(path.read_text(encoding='utf8'))
    if case=='missing_before':del raw['rois']['rank_result_before']
    elif case=='missing_after':del raw['rois']['rank_result_after']
    elif case=='overlap':raw['rois']['rank_result_after']=[1400,335,380,170]
    elif case=='outside':raw['rois']['rank_result_before']=[0,335,235,170]
    else:raw['rois']['rank_result_after']=[1570,335,100,170]
    path.write_text(yaml.safe_dump(raw),encoding='utf8')
    with pytest.raises(ConfigError):load(installation)


def test_original_combination_images_are_not_startup_dependencies(installation):
    manifest=json.loads((installation/'assets/templates/chaos/manifest.json').read_text(encoding='utf8'))
    for key in ['rank_12','rank_23','rank_34','rank_45']:
        assert key not in manifest['templates']
        assert key in manifest['retired_templates']
        assert not (ROOT/'assets/templates/chaos/camp'/f'{key}.png').exists()
    config=load(installation)
    assert all('chaos:rank_value_'+str(n) in config.template_paths for n in range(1,6))


@pytest.mark.parametrize('lineup_id,key', [
    (lineup,key) for lineup in ('dark_twins','budget')
    for key in ('event_red_phantom','depart_title','depart_confirm','depart_missing','ticket_all','title_all')
] + [('budget',key) for key in ('filter_icon_knight_filter','filter_knight_selected','recruited_shadow_rose_all')])
@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_departure_and_voucher_resources_are_required(installation,lineup_id,key,damage):
    path=image_path(installation,'chaos',key)
    if damage=='missing':path.unlink()
    else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        with_chaos_config(load_config(installation/'config/internal.yaml',template_profile='chaos'),lineup_id=lineup_id)


@pytest.mark.parametrize('lineup_id',['dark_twins','budget'])
def test_wrong_departure_target_rejects_startup(installation,lineup_id):
    p=installation/'config/chaos.yaml';raw=yaml.safe_load(p.read_text(encoding='utf8'))
    raw['lineups'][lineup_id]['departure_hero']='xiaochun'
    p.write_text(yaml.safe_dump(raw),encoding='utf8')
    with pytest.raises(ConfigError,match='invalid lineup'):
        with_chaos_config(load_config(installation/'config/internal.yaml',template_profile='chaos'),lineup_id=lineup_id)


@pytest.mark.parametrize('lineup_id',['dark_twins','budget'])
@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_broken_cage_resource_is_required(installation,lineup_id,damage):
    path=image_path(installation,'chaos','event_broken_cage')
    if damage=='missing':path.unlink()
    else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):
        with_chaos_config(load_config(installation/'config/internal.yaml',template_profile='chaos'),lineup_id=lineup_id)


@pytest.mark.parametrize('key',['event_sharp_teeth','event_collapsed_stairs'])
@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_teeth_stairs_resources_are_required(installation,key,damage):
    path=image_path(installation,'chaos',key)
    if damage=='missing':path.unlink()
    else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)


@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_bone_throne_resource_is_required(installation,damage):
    path=image_path(installation,'chaos','event_bone_throne')
    if damage=='missing':path.unlink()
    else:path.write_bytes(b'bad PNG')
    with pytest.raises(ConfigError):load(installation)
