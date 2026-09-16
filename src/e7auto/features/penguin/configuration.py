from __future__ import annotations
from dataclasses import replace
from e7auto.configuration.models import AppConfig, ConfigError
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
        return replace(config, template_paths=paths)
    except (ValueError, KeyError, TypeError) as exc:
        raise ConfigError([f"Penguin templates: {exc}"]) from exc
