"""Explicit shared resources and recognition settings for startup navigation."""
from math import isfinite
from .models import NavigationTemplate
from .schema import NAVIGATION_TEMPLATE_KEYS
from e7auto.core.types import Rect


def load_startup_navigation(root, baseline, sizes, errors):
    result = {}
    raw = root.get("startup_navigation")
    if not isinstance(raw, dict) or set(raw) != NAVIGATION_TEMPLATE_KEYS:
        errors.append("startup_navigation must declare exactly the three shared navigation templates")
        return result
    for key, value in raw.items():
        try:
            if not isinstance(value, dict) or set(value) != {"roi", "threshold", "structure_threshold"}:
                raise ValueError("unknown or missing fields")
            roi = value["roi"]
            if not isinstance(roi, list) or len(roi) != 4 or any(type(n) is not int for n in roi):
                raise ValueError("ROI must contain four integers")
            region = Rect(*roi)
            if not (0 <= region.x < region.right <= baseline.width and
                    0 <= region.y < region.bottom <= baseline.height):
                raise ValueError("ROI outside client")
            if key in sizes and (sizes[key][0] > region.width or sizes[key][1] > region.height):
                raise ValueError("ROI smaller than template")
            thresholds = value["threshold"], value["structure_threshold"]
            if any(type(n) not in (int, float) or not isfinite(n) or not 0 < n <= 1 for n in thresholds):
                raise ValueError("thresholds must be finite values in (0, 1]")
            result[key] = NavigationTemplate(region, *thresholds)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"startup_navigation.{key}: {exc}")
    return result
