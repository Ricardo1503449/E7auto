"""Runtime parameters are configured independently of template provenance."""
from dataclasses import replace
from pathlib import Path
from tests.helpers.paths import ROOT

import pytest
import yaml

from e7auto.configuration.models import ConfigError, EntryThresholds
from e7auto.core.types import Rect
from e7auto.configuration.loader import load_config
from e7auto.features.penguin.vision import PenguinVision
from e7auto.features.penguin.configuration import with_penguin_config
from e7auto.features.shop.vision import ShopVision as OpenCvGameVision
from e7auto.resources.templates import TemplateRepository




@pytest.fixture
def configuration(tmp_path):
    source = ROOT / "config/internal.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["template_manifests"] = {
        key: str((source.parent / value).resolve()) for key, value in raw["template_manifests"].items()
    }
    return tmp_path / "internal.yaml", raw


@pytest.mark.parametrize("value", [None, 0, -1, True, "2", float("nan"), float("inf"), 4.01])
def test_digit_smoothing_sigma_rejects_invalid_values(configuration, value):
    path, raw = configuration
    raw["vision"]["digit_smoothing_sigma"] = value
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="vision.digit_smoothing_sigma"):
        load_config(path)


@pytest.mark.parametrize("sigma", [1.5, 2.0, 4.0])
def test_digit_smoothing_sigma_is_loaded_by_both_features(configuration, sigma):
    path, raw = configuration
    raw["vision"]["digit_smoothing_sigma"] = sigma
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = load_config(path)
    shop = OpenCvGameVision(config, TemplateRepository(config))
    penguin_config = with_penguin_config(config)
    penguin = PenguinVision(penguin_config, TemplateRepository(penguin_config))
    assert config.digit_smoothing_sigma == sigma
    assert shop._digits._smoothing_sigma == sigma
    assert penguin._digits._smoothing_sigma == sigma


@pytest.mark.parametrize('value',[0,-1,True,1.5,'64',None])
def test_candidate_cache_budget_rejects_invalid_values(configuration,value):
    path,raw=configuration
    raw['vision']['candidate_cache_mib']=value
    path.write_text(yaml.safe_dump(raw),encoding='utf-8')
    with pytest.raises(ConfigError,match='vision.candidate_cache_mib'):
        load_config(path)


def test_candidate_cache_budget_defaults_to_64_mib(configuration):
    path,raw=configuration
    raw['vision'].pop('candidate_cache_mib')
    path.write_text(yaml.safe_dump(raw),encoding='utf-8')
    assert load_config(path).candidate_cache_mib==64


@pytest.mark.parametrize('value', [None, 0, -1, True, 250.5, '1000', float('inf')])
def test_invalid_capture_frame_timeout_is_rejected(configuration, value):
    path, raw = configuration
    raw['timing']['capture_frame_timeout_ms'] = value
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    with pytest.raises(ConfigError, match='timing.capture_frame_timeout_ms'):
        load_config(path)


@pytest.mark.parametrize('value,expected', [(750, 750), (1000, 1000), (None, 1000)])
def test_capture_frame_timeout_configuration_and_legacy_default(configuration, value, expected):
    path, raw = configuration
    if value is None:
        raw['timing'].pop('capture_frame_timeout_ms', None)
    else:
        raw['timing']['capture_frame_timeout_ms'] = value
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    assert load_config(path).timing.capture_frame_timeout_ms == expected


CAPTURE_RECOVERY_DEFAULTS = {
    'capture_recovery_timeout_ms': 3000,
    'capture_rebuild_cooldown_ms': 60000,
    'capture_rebuild_window_ms': 600000,
    'capture_rebuild_max_attempts': 3,
}


@pytest.mark.parametrize('field', CAPTURE_RECOVERY_DEFAULTS)
@pytest.mark.parametrize('value', [None, 0, -1, True, 250.5, '1000', float('inf')])
def test_invalid_capture_recovery_setting_is_rejected(configuration, field, value):
    path, raw = configuration
    raw['timing'][field] = value
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    with pytest.raises(ConfigError, match=f'timing.{field}'):
        load_config(path)


@pytest.mark.parametrize('field,default', CAPTURE_RECOVERY_DEFAULTS.items())
@pytest.mark.parametrize('explicit', [False, True])
def test_capture_recovery_setting_override_and_legacy_default(configuration, field, default, explicit):
    path, raw = configuration
    if explicit:
        raw['timing'][field] = default + 1
    else:
        raw['timing'].pop(field, None)
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    assert getattr(load_config(path).timing, field) == default + int(explicit)


@pytest.mark.parametrize("feature", ["shop", "penguin"])
@pytest.mark.parametrize("name", ["color", "structure"])
@pytest.mark.parametrize("value", [None, True, 0, 1.01, float("nan"), float("inf")])
def test_invalid_entry_thresholds_are_rejected(configuration, feature, name, value):
    path, raw = configuration
    raw["vision"]["entry_thresholds"][feature][name] = value
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match=f"vision.entry_thresholds.{feature}.{name}"):
        load_config(path)


@pytest.mark.parametrize("value", [None, {"x": -1, "y": 16}, {"x": 18, "y": -1},
                                  {"x": True, "y": 16}, {"x": 1.5, "y": 16},
                                  {"x": 10000, "y": 16}, {"x": 18, "y": 10000}])
def test_invalid_purchased_button_padding_is_rejected(configuration, value):
    path, raw = configuration
    raw["vision"]["purchased_button_padding"] = value
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="vision.purchased_button_padding"):
        load_config(path)


@pytest.mark.parametrize("value", [None, {"x": -1, "y": 25, "width": 210, "height": 62},
                                  {"x": 115, "y": 25, "width": 0, "height": 62},
                                  {"x": 115, "y": 25, "width": True, "height": 62},
                                  {"x": 115, "y": 25, "width": 490, "height": 62},
                                  {"x": 115, "y": 25, "width": 210, "height": 94}])
def test_invalid_penguin_price_rectangle_is_rejected(configuration, value):
    path, raw = configuration
    raw["vision"]["penguin_price_rect"] = value
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="vision.penguin_price_rect"):
        with_penguin_config(load_config(path, template_profile="penguin"))


def test_zero_padding_is_a_valid_exact_template_search(configuration):
    path, raw = configuration
    raw["vision"]["purchased_button_padding"] = {"x": 0, "y": 0}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_config(path).purchased_button_padding.x == 0


def test_both_entry_paths_use_shared_roi_and_their_configured_thresholds(monkeypatch):
    config = load_config(ROOT / "config/internal.yaml")
    requested = Rect(10, 200, 220, 950)
    config = replace(config, rois={**config.rois, "left_icon_column": requested},
                     entry_thresholds={"shop": EntryThresholds(.91, .83), "penguin": EntryThresholds(.97, .86)})
    shop = OpenCvGameVision(config, TemplateRepository(config))
    penguin_config = with_penguin_config(config)
    penguin = PenguinVision(penguin_config, TemplateRepository(penguin_config))
    calls = []

    def record(frame, key, roi, threshold, *, structure_threshold):
        calls.append((key, roi, threshold, structure_threshold))
        return None

    monkeypatch.setattr(shop, "match_entry", record)
    monkeypatch.setattr(penguin, "match_entry", record)
    shop.main_shop_icon(object())
    penguin.control(object(), "sanctuary_entry")
    assert calls == [("main_shop_icon", requested, .91, .83),
                     ("penguin_sanctuary_entry", requested, .97, .86)]


def test_penguin_control_parameters_are_not_generated_from_manifest(monkeypatch):
    config = load_config(ROOT / "config/internal.yaml", template_profile="penguin")
    requested = Rect(1700, 200, 250, 100)
    config = with_penguin_config(replace(
        config, rois={**config.rois, "penguin_forest_entry": requested}, penguin_control_confidence=.975,
    ))
    vision = PenguinVision(config, TemplateRepository(config))
    calls = []
    monkeypatch.setattr(vision, "match", lambda frame, key, roi, threshold: calls.append((key, roi, threshold)))
    vision.control(object(), "forest_entry")
    assert calls == [("penguin_forest_entry", requested, .975)]


@pytest.mark.parametrize('value', [True, 0, -1, 1.01, float('nan'), float('inf'), '0.8'])
def test_invalid_per_template_structure_threshold_is_rejected(configuration, value):
    path, raw = configuration
    raw['vision']['template_structure_thresholds'] = {'purchased_button': value}
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    with pytest.raises(ConfigError, match='template_structure_thresholds'):
        load_config(path)


@pytest.mark.parametrize('key', ['typo', 'sky_stone_digit_1', 'main_shop_icon', 'chaos:contract'])
def test_structure_overrides_do_not_silently_target_unknown_or_specialized_templates(configuration, key):
    path, raw = configuration
    raw['vision']['template_structure_thresholds'] = {key: .8}
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    with pytest.raises(ConfigError, match='supported single-gate template'):
        load_config(path)


def test_per_template_structure_gates_allow_explicit_pending_and_legacy_absence(configuration):
    path, raw = configuration
    raw['vision']['template_structure_thresholds'] = {'purchased_button': .8, 'network_retry': None}
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    assert load_config(path).template_structure_thresholds == {'purchased_button': .8, 'network_retry': None}
    raw['vision'].pop('template_structure_thresholds')
    path.write_text(yaml.safe_dump(raw), encoding='utf8')
    assert load_config(path).template_structure_thresholds == {}


@pytest.mark.parametrize('value', [None, True, 0, -1, 1.01, float('nan'), float('inf'), '0.9'])
@pytest.mark.parametrize('parameter', ['purchased_button_confidence', 'penguin_control_thresholds',
                                       'penguin_purchase_body_confidence', 'confirm_button_confidence',
                                       'shop_default_confidence'])
def test_reviewed_color_gates_reject_invalid_values(configuration, value, parameter):
    path, raw = configuration
    raw['vision'][parameter] = {'penguin_quantity_max': value} if parameter == 'penguin_control_thresholds' else value
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    with pytest.raises(ConfigError, match=parameter):
        load_config(path)


@pytest.mark.parametrize('value', [None, [], '0.9'])
def test_per_penguin_control_gates_require_a_mapping(configuration, value):
    path, raw = configuration
    raw['vision']['penguin_control_thresholds'] = value
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    with pytest.raises(ConfigError, match='penguin_control_thresholds'):
        load_config(path)


@pytest.mark.parametrize('key', ['quantity_max', 'penguin_sanctuary_entry', 'purchased_button', 'typo',
                                'penguin_penguin_buy_5100'])
def test_per_penguin_control_gates_reject_unknown_and_separate_entry_keys(configuration, key):
    path, raw = configuration
    raw['vision']['penguin_control_thresholds'] = {key: .9}
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    with pytest.raises(ConfigError, match='supported penguin control'):
        load_config(path)


def test_reviewed_gates_preserve_legacy_defaults_when_absent(configuration):
    path, raw = configuration
    raw['vision'].pop('purchased_button_confidence')
    raw['vision'].pop('confirm_button_confidence')
    raw['vision'].pop('penguin_control_thresholds')
    raw['vision'].pop('penguin_purchase_body_confidence')
    raw['vision'].pop('shop_default_confidence')
    raw['vision']['default_confidence'] = .91
    raw['vision']['anchor_confidence'] = .975
    raw['vision']['penguin_control_confidence'] = .965
    path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    config = load_config(path)
    assert config.purchased_button_confidence == .975
    assert config.confirm_button_confidence == .975
    assert config.penguin_control_thresholds == {}
    assert config.penguin_control_confidence == .965
    assert config.penguin_purchase_body_confidence == .965
    assert config.shop_default_confidence == .91


def test_sold_out_override_does_not_change_other_shop_anchors(monkeypatch):
    config = replace(load_config(ROOT/'config/internal.yaml'), purchased_button_confidence=.85,
                     anchor_confidence=.93)
    vision = OpenCvGameVision(config, TemplateRepository(config))
    calls = []
    monkeypatch.setattr(vision, 'match', lambda frame, key, roi, threshold: calls.append((key, threshold)))
    vision.purchased_button(object(), config.slots[0].item_roi)
    vision.shop_ready(object())
    assert calls == [('purchased_button', .85), ('shop_refresh_button', .93)]
    legacy = replace(config, purchased_button_confidence=None, anchor_confidence=.975)
    legacy_vision = OpenCvGameVision(legacy, TemplateRepository(legacy))
    monkeypatch.setattr(legacy_vision, 'match', lambda frame, key, roi, threshold: calls.append((key, threshold)))
    legacy_vision.purchased_button(object(), legacy.slots[0].item_roi)
    assert calls[-1] == ('purchased_button', .975)


@pytest.mark.parametrize('override', [.85, None])
@pytest.mark.parametrize('identity_present', [True, False])
def test_confirm_button_override_preserves_identity_guard_and_other_anchors(monkeypatch, override, identity_present):
    from e7auto.core.observations import Observation
    from e7auto.core.types import ClientPoint, ClientRect

    config = replace(load_config(ROOT/'config/internal.yaml'), confirm_button_confidence=override,
                     anchor_confidence=.93)
    vision = OpenCvGameVision(config, TemplateRepository(config))
    calls = []

    def match(frame, key, roi, threshold):
        calls.append((key, threshold))
        if key == 'friendship_points_confirm' and not identity_present:
            return None
        return Observation(key, .99, ClientRect(10, 20, 30, 40), ClientPoint(25, 40))

    monkeypatch.setattr(vision, 'match', match)
    result = vision.confirm_dialog(object(), 'friendship_points')
    assert (result is not None) is identity_present
    assert calls[0] == ('friendship_points_confirm', config.shop_default_confidence)
    if identity_present:
        assert calls[1] == ('confirm_button', .93 if override is None else override)
    else:
        assert len(calls) == 1
    vision.shop_ready(object())
    assert calls[-1] == ('shop_refresh_button', .93)
    assert vision._matcher.structure_threshold('confirm_button') == .8


@pytest.mark.parametrize('override', [.85, None])
def test_shop_default_gate_keeps_network_and_reward_overlay_independent(monkeypatch, override):
    import numpy as np
    from e7auto.core.observations import Observation
    from e7auto.core.types import ClientPoint, ClientRect

    config = load_config(ROOT/'config/internal.yaml')
    config = replace(config, default_confidence=.93, shop_default_confidence=override,
                     inventory_readiness=replace(config.inventory_readiness, reward_overlay_confidence=.87))
    vision = OpenCvGameVision(config, TemplateRepository(config))
    expected = .93 if override is None else override
    calls = []

    def record(frame, key, roi, threshold):
        calls.append((key, threshold))
        if key == 'insufficient_funds':
            return None
        return Observation(key, .99, ClientRect(10, 20, 30, 40), ClientPoint(25, 40))

    monkeypatch.setattr(vision._matcher, 'match', record)
    monkeypatch.setattr(vision._matcher, 'match_bgr', record)
    monkeypatch.setattr(vision._network, 'match', record)
    frame = np.zeros((1306, 2322, 3), np.uint8)
    assert vision.refresh_confirm_dialog(frame) is not None
    assert calls[-2:] == [('refresh_confirm_prompt', expected), ('refresh_confirm_button', config.anchor_confidence)]
    assert vision.confirm_dialog(frame, 'friendship_points') is not None
    assert calls[-2:] == [('friendship_points_confirm', expected), ('confirm_button', config.confirm_button_confidence)]
    vision.purchase_outcome(frame, 'friendship_points', config.slots[0].item_roi)
    assert calls[-2:] == [('insufficient_funds', config.anchor_confidence), ('friendship_points_purchased', expected)]
    calls.clear()
    vision.scan_inventory(frame, 'top')
    assert {key for key, _ in calls} == {getattr(target, field) for target in config.targets
                                        for field in ('template', 'purchased_template')}
    assert all(threshold == expected for _, threshold in calls)
    assert vision.inventory_scene_state(frame, 'top') == 'reward_overlay'
    assert calls[-1] == (config.targets[0].template, .87)
    vision.network_connection_error(frame)
    vision.network_retry(frame)
    assert calls[-2:] == [('network_connection_abnormal', .93), ('network_retry', .93)]


def test_penguin_overrides_bind_body_without_changing_other_controls(monkeypatch):
    config = with_penguin_config(load_config(ROOT/'config/internal.yaml', template_profile='penguin'))
    config = replace(config, penguin_control_confidence=.96, penguin_purchase_body_confidence=.89,
                     penguin_control_thresholds={
        'penguin_quantity_max': .91, 'penguin_purchase_cancel': .92})
    vision = PenguinVision(config, TemplateRepository(config))
    calls = []
    monkeypatch.setattr(vision, 'match', lambda frame, key, roi, threshold: calls.append((key, threshold)))
    for name in ('quantity_max', 'purchase_cancel', 'forest_entry'):
        vision.control(object(), name)
    assert calls == [('penguin_quantity_max', .91), ('penguin_purchase_cancel', .92),
                     ('penguin_forest_entry', .96)]
    monkeypatch.setattr(vision, 'control', lambda *args: object())
    monkeypatch.setattr(vision._body_matcher, 'match',
                        lambda frame, key, roi, threshold: calls.append((key+'/body', threshold)))
    assert vision.dialog(object()) is None
    assert calls[-1] == ('penguin_penguin_buy_5100/body', .89)
    vision._config = replace(config, penguin_purchase_body_confidence=None)
    assert vision.dialog(object()) is None
    assert calls[-1] == ('penguin_penguin_buy_5100/body', .96)
