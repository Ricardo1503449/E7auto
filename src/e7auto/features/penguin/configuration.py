from __future__ import annotations
from dataclasses import replace
import yaml
from e7auto.configuration.models import AppConfig, ConfigError
from e7auto.configuration.components import load_component_layouts
from e7auto.configuration.schema import COMMON_TEMPLATE_KEYS, PENGUIN_CONTROLS
from e7auto.resources.manifest import load_template_manifest
CONTROLS = PENGUIN_CONTROLS

def with_penguin_config(config: AppConfig) -> AppConfig:
    try:
        registered, sizes = load_template_manifest(
            config.template_manifest_paths["penguin"],
            (config.baseline_client_size.width, config.baseline_client_size.height),
            prefix="penguin_",
        )
        expected = {f"penguin_{name}" for name in CONTROLS}
        if set(registered) != expected:
            raise ValueError("penguin catalog must contain exactly the configured controls")
        price = config.penguin_price_rect
        button_width, button_height = sizes["penguin_penguin_buy_5100"]
        if not (0 <= price.x < price.right <= button_width
                and 0 <= price.y < price.bottom <= button_height):
            raise ValueError("vision.penguin_price_rect must fit inside the purchase-button template")
        for key, (width, height) in sizes.items():
            roi_key = "left_icon_column" if key == "penguin_sanctuary_entry" else key
            search = config.rois.get(roi_key)
            if search is None:
                raise ValueError(f"rois.{roi_key} is required")
            if not (0 <= search.x < search.right <= config.baseline_client_size.width
                    and 0 <= search.y < search.bottom <= config.baseline_client_size.height
                    and search.width >= width and search.height >= height):
                raise ValueError(f"rois.{roi_key} must fit the client and contain the template")
        paths = {key: config.template_paths[key] for key in COMMON_TEMPLATE_KEYS}
        paths.update(registered)
        raw = yaml.safe_load(config.source_path.read_text(encoding='utf-8'))
        components = load_component_layouts(raw['penguin_component_matching'], sizes,
                                            {'altar_close'}, prefix='penguin_')
        return replace(config, template_paths=paths, component_layouts=components)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ConfigError([f"Penguin templates: {exc}"]) from exc
