"""Approved selection, isolated loading and complete component evidence."""
from types import SimpleNamespace

import numpy as np
import pytest

from e7auto.configuration.components import load_component_layouts
from e7auto.configuration.loader import load_config
from e7auto.core.recognition import ComponentLayout
from e7auto.core.types import Rect
from e7auto.features.penguin.configuration import with_penguin_config
from e7auto.resources.templates import TemplateData
from e7auto.vision.components import ComponentMatcher
from e7auto.vision.matching import TemplateMatcher
from tests.helpers.chaos_game import chaos_config
from tests.helpers.paths import ROOT


def specification():
    return {'tolerance':[6,8], 'templates':{'target':{'primary':[0,0,20,20],'secondary':[20,0,20,20]}}}


def test_exact_approved_resource_union_is_25_with_explicit_alias_reuse():
    first, second = chaos_config(), chaos_config('budget')
    penguin = with_penguin_config(load_config(ROOT/'config/internal.yaml',template_profile='penguin'))
    selected = set(first.component_layouts)|set(second.component_layouts)|set(penguin.component_layouts)
    assert len(selected) == 25
    assert len([key for key in selected if key.startswith('chaos:')]) == 23
    assert set(penguin.component_layouts) == {'penguin_altar_close'}
    assert not load_config(ROOT/'config/internal.yaml',template_profile='shop').component_layouts
    assert first.chaos.scenes['event_reward_close'].template_key == 'chaos:rank_close'
    assert first.chaos.scenes['buff_effect_close'].template_key == 'login_reward_close'
    assert 'chaos:hero_shadow_rose' not in first.component_layouts
    assert all(key in config.template_paths for config in (first,second,penguin) for key in config.component_layouts)


@pytest.mark.parametrize('change', ['unknown','missing','outside','overlap','negative','fractional'])
def test_invalid_component_metadata_is_rejected(change):
    raw = specification()
    if change == 'unknown': raw['templates']['unapproved'] = raw['templates']['target']
    elif change == 'missing': raw['templates'] = {}
    elif change == 'outside': raw['templates']['target']['secondary'] = [20,0,21,20]
    elif change == 'overlap': raw['templates']['target']['secondary'] = [19,0,20,20]
    elif change == 'negative': raw['tolerance'] = [-1,8]
    else: raw['templates']['target']['primary'] = [0.,0,20,20]
    with pytest.raises(ValueError):
        load_component_layouts(raw,{'target':(40,20)},{'target'})


def test_inactive_resource_is_not_opened_or_required():
    assert load_component_layouts(specification(),{}, {'target'}) == {}


@pytest.mark.parametrize('layout', [
    ComponentLayout(Rect(0,0,19,20),Rect(20,0,20,20),(6,8)),
    ComponentLayout(Rect(0,0,21,20),Rect(20,0,20,20),(6,8)),
    ComponentLayout(Rect(0,0,20,20),Rect(40,0,1,20),(6,8)),
])
def test_component_layout_cannot_drop_or_duplicate_original_evidence(layout):
    data = TemplateData(np.ones((20,40,3),np.uint8)*120,None)
    repo = SimpleNamespace(get=lambda _:data)
    with pytest.raises(ValueError):
        ComponentMatcher(repo,TemplateMatcher(repo),{'test':layout})
