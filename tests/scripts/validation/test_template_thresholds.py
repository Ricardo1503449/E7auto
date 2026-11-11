"""Inventory covers every PNG, real use aliases and explicit pending/special gates."""
from pathlib import Path
import sys

import pytest

from scripts.validation.template_thresholds import COLOR, build_inventory, markdown
from scripts.validation import template_thresholds
from tests.helpers.paths import ROOT


@pytest.fixture(scope='module')
def report():
    return build_inventory(ROOT, ROOT)


def test_every_png_and_distinct_use_is_listed_with_blank_user_decisions(report):
    rows = report['rows']
    assert len({row['id'] for row in rows}) == len(rows)
    assert {row['file'] for row in rows} == {
        p.relative_to(ROOT).as_posix() for p in (ROOT/'assets/templates').rglob('*.png')}
    confirm = [row for row in rows if row['key'] == 'unclaimed_confirm']
    assert {'unclaimed_confirm', 'abandon_confirm', 'story_skip_confirm'} <= {row['use'] for row in confirm}
    assert any(row['status'] == '未启用/历史保留' for row in rows)
    assert all(row['user_score'] is None and row['user_structure'] is None for row in rows)


def test_network_inventory_uses_shared_configuration_for_all_three_features(report):
    network = [row for row in report['rows'] if row['use'].startswith('网络/')]
    assert len(network) == 30
    for feature in ('shop', 'penguin', 'chaos'):
        rows = [row for row in network if row['feature'] == feature]
        assert len(rows) == 10
        for row in rows:
            loading = row['key'].startswith('network_loading_crystal_')
            name = 'loading' if loading else 'retry' if row['key'] == 'network_retry' else 'error'
            assert row['score_current'] == .90
            assert row['structure_current'] == (.60 if loading else .80)
            assert row['score_source'].endswith('network.'+name+'.color')
            assert row['structure_source'].endswith('network.'+name+'.structure')
            if feature == 'chaos' and not loading:
                assert '基准match，非基准locate_all(limit=1)' in row['note']


def test_inventory_uses_effective_loot_and_special_scores_instead_of_generic_defaults(report):
    rows = report['rows']
    loot = [row for row in rows if row['use'] == 'loot_old_sword/完整名称']
    assert len(loot) == 1
    assert loot[0]['score_current'] == .9 and loot[0]['structure_current'] == .8
    assert loot[0]['score_source'].endswith('loot_recognition.name_threshold')
    assert '无整图门槛' in loot[0]['note']
    glyphs = [row for row in rows if row['metric'] == '字形相似度']
    assert glyphs and all(row['structure_current'] is None for row in glyphs)
    assert all(row['structure_formula'] == '不适用' for row in glyphs)
    confirm = next(row for row in rows if row['feature'] == 'shop' and row['key'] == 'confirm_button')
    assert confirm['structure_current'] == .8 and '真实正反例' in confirm['note']
    assert '结构已启用' in confirm['note']
    assert confirm['formula_current'] == COLOR
    sold = next(row for row in rows if row['key'] == 'purchased_button')
    assert sold['structure_current'] == .8
    assert sold['score_current'] == .85
    assert sold['score_source'].endswith('vision.purchased_button_confidence')
    assert confirm['score_current'] == .85
    assert confirm['score_source'].endswith('vision.confirm_button_confidence')
    for key in ('penguin_buy_5100', 'quantity_max', 'purchase_cancel'):
        matching = [row for row in rows if row['feature'] == 'penguin' and row['key'] == key]
        assert matching
        for row in matching:
            body = '排除可变金额' in row['use']
            if key == 'penguin_buy_5100' and not body:
                assert row['status'] == '用途已移除'
                assert row['score_current'] is None and row['structure_current'] is None
                assert row['score_source'] == row['structure_source'] == ''
                assert row['formula_current'] == '不再运行'
                continue
            assert row['score_current'] == .9 and row['structure_current'] == .8
            source = 'vision.penguin_purchase_body_confidence' if body else 'vision.penguin_control_thresholds.penguin_'+key
            assert row['score_source'].endswith(source)
    reviewed = next(row for row in rows if row['feature'] == 'penguin' and row['key'] == 'penguin_buy_102')
    assert reviewed['score_current'] == .90 and reviewed['structure_current'] == .80
    import yaml
    configured = yaml.safe_load((ROOT/'config/internal.yaml').read_text(encoding='utf-8'))['vision']['template_structure_thresholds']
    assert set(configured) <= {row['runtime_key'] for row in rows}
    for key, value in configured.items():
        matching = [row for row in rows if row['runtime_key'] == key and 'template_structure_thresholds' in row['structure_source']]
        assert matching and all(row['structure_current'] == value for row in matching)
    parameters = {row['source'] for row in report['specialized_parameters']}
    assert 'config/chaos.yaml:auto_motion.active_min_angle_deg' in parameters
    assert 'config/internal.yaml:vision.penguin_price_digit_margin' in parameters
    rendered = markdown(report)
    assert all(row['id'] in rendered and row['file'] in rendered for row in rows)


def test_export_never_overwrites_existing_user_review(tmp_path, monkeypatch):
    output = tmp_path/'review.md'
    output.write_text('user decisions', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['template_thresholds', '--before-root', str(ROOT), '--output', str(output)])
    monkeypatch.setattr(template_thresholds, 'build_inventory', lambda *args: pytest.fail('must preserve decisions first'))
    with pytest.raises(FileExistsError, match='Preserve existing review decisions'):
        template_thresholds.main()
    assert output.read_text(encoding='utf-8') == 'user decisions'
