"""Opt-in configuration and resources for the shared date-change workflow."""
from dataclasses import dataclass
from math import isfinite
from typing import Mapping

import yaml

from e7auto.configuration.models import AppConfig, ConfigError
from e7auto.configuration.components import load_component_layouts
from e7auto.core.recognition import ComponentLayout
from e7auto.core.types import Rect
from e7auto.resources.manifest import load_template_manifest

DAILY_RESET_KEYS = frozenset({
    'date_change_title', 'date_change_close', 'daily_signin_confirm',
    'login_reward_title', 'login_reward_close',
    'buff_effect_title',
})
_BUTTONS = frozenset({'date_change_close', 'daily_signin_confirm', 'login_reward_close'})


@dataclass(frozen=True, slots=True)
class DailyResetScene:
    template_key: str
    region: Rect
    threshold: float
    structure: float
    action: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class DailyResetSettings:
    scenes: Mapping[str, DailyResetScene]
    observe_ms: int
    poll_ms: int
    stable_frames: int
    page_timeout_ms: int
    close_retry_ms: int
    close_max_attempts: int
    component_layouts: Mapping[str, ComponentLayout]


def load_daily_reset(config: AppConfig):
    """Only callers opting in read these shared images and their settings."""
    try:
        raw = yaml.safe_load(config.source_path.read_text(encoding='utf-8'))['daily_reset']
        fields = {'observe_ms', 'poll_ms', 'stable_frames', 'page_timeout_ms',
                  'close_retry_ms', 'close_max_attempts'}
        if set(raw) != fields | {'templates', 'component_matching'}:
            raise ValueError('unknown or missing daily-reset setting')
        if any(type(raw[k]) is not int or raw[k] <= 0 for k in fields):
            raise ValueError('daily-reset timing must be positive integers')
        if (raw['stable_frames'] < 3 or raw['poll_ms'] > 1000
                or not raw['poll_ms'] * raw['stable_frames'] < raw['close_retry_ms'] < raw['page_timeout_ms']
                or raw['observe_ms'] >= raw['page_timeout_ms']):
            raise ValueError('invalid daily-reset evidence/timing limits')
        if set(raw['templates']) != DAILY_RESET_KEYS:
            raise ValueError('daily-reset templates must name exactly the supported resource identities')
        size = config.baseline_client_size
        paths, sizes = load_template_manifest(config.template_manifest_paths['common'],
            (size.width, size.height), keys=DAILY_RESET_KEYS)
        scenes = {}
        for key, value in raw['templates'].items():
            if set(value) != {'roi', 'threshold', 'structure_threshold'}:
                raise ValueError(f'invalid daily-reset scene: {key}')
            roi = value['roi']
            if not isinstance(roi, list) or len(roi) != 4 or any(type(n) is not int for n in roi):
                raise ValueError(f'invalid daily-reset ROI: {key}')
            area = Rect(*roi)
            if not (0 <= area.x < area.right <= size.width and 0 <= area.y < area.bottom <= size.height):
                raise ValueError(f'daily-reset ROI outside baseline: {key}')
            if sizes[key][0] > area.width or sizes[key][1] > area.height:
                raise ValueError(f'daily-reset ROI smaller than template: {key}')
            scores = value['threshold'], value['structure_threshold']
            if any(type(n) not in (int, float) or not isfinite(n) or not 0 < n <= 1 for n in scores):
                raise ValueError(f'invalid daily-reset thresholds: {key}')
            scenes[key] = DailyResetScene(key, area, *scores, (.5, .5) if key in _BUTTONS else None)
        # Same pixels, ROI and thresholds; the caller still distinguishes the
        # popup by its title and maintains independent close progress.
        scenes['buff_effect_close'] = scenes['login_reward_close']
        components = load_component_layouts(raw['component_matching'], sizes, {'login_reward_close'})
        return DailyResetSettings(scenes=scenes, component_layouts=components,
                                  **{k: raw[k] for k in fields}), paths
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        raise ConfigError([f'Daily reset configuration: {exc}']) from exc
