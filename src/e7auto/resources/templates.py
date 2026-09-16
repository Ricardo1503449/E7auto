from __future__ import annotations
from dataclasses import dataclass
import cv2
import numpy as np
from e7auto.configuration.models import AppConfig
from e7auto.core.ports import Frame
@dataclass(frozen=True, slots=True)
class TemplateData:
    image: Frame
    mask: np.ndarray | None = None


class TemplateRepository:
    def __init__(self, config: AppConfig):
        self._templates: dict[str, TemplateData] = {}
        for key, path in config.template_paths.items():
            try:
                # Read via Python so Windows paths containing Unicode work too.
                encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
                loaded = (
                    cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
                    if encoded.size
                    else None
                )
            except (OSError, cv2.error) as exc:
                raise ValueError(f"Cannot load template {key}: {path}") from exc
            if loaded is None or loaded.size == 0:
                raise ValueError(f"Cannot load template {key}: {path}")
            if loaded.ndim != 3 or loaded.shape[2] not in (3, 4):
                raise ValueError(f"Template must have 3 or 4 channels: {key}: {path}")
            image = np.ascontiguousarray(loaded[:, :, :3])
            mask: np.ndarray | None = None
            if loaded.shape[2] == 4 and not np.all(loaded[:, :, 3] == 255):
                mask = np.ascontiguousarray(loaded[:, :, 3])
                if not np.any(mask):
                    raise ValueError(f"Template alpha mask is empty: {key}: {path}")
            self._templates[key] = TemplateData(image, mask)

    def get(self, key: str) -> TemplateData:
        try:
            return self._templates[key]
        except KeyError as exc:
            raise KeyError(f"Unknown template: {key}") from exc
