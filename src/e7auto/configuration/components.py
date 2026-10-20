"""Explicit, feature-owned component layouts; no extra image dependencies."""
from e7auto.core.recognition import ComponentLayout
from e7auto.core.types import Rect


def load_component_layouts(value, sizes, allowed, *, prefix=''):
    if not isinstance(value, dict) or set(value) != {'tolerance', 'templates'}:
        raise ValueError('invalid component_matching settings')
    tolerance = value['tolerance']
    if (not isinstance(tolerance, list) or len(tolerance) != 2
            or any(type(n) is not int or n < 0 for n in tolerance)):
        raise ValueError('component tolerance must contain two nonnegative baseline integers')
    templates = value['templates']
    if not isinstance(templates, dict) or set(templates) != set(allowed):
        raise ValueError('component templates must exactly match the approved identities')
    layouts = {}
    for name, entry in templates.items():
        if not isinstance(entry, dict) or set(entry) != {'primary', 'secondary'}:
            raise ValueError(f'invalid component parts: {name}')
        parts = []
        for role in ('primary', 'secondary'):
            raw = entry[role]
            if (not isinstance(raw, list) or len(raw) != 4
                    or any(type(n) is not int for n in raw)):
                raise ValueError(f'invalid component rectangle: {name}/{role}')
            rect = Rect(*raw)
            if rect.x < 0 or rect.y < 0 or rect.width <= 0 or rect.height <= 0:
                raise ValueError(f'invalid component extent: {name}/{role}')
            parts.append(rect)
        a, b = parts
        if min(a.right, b.right) > max(a.x, b.x) and min(a.bottom, b.bottom) > max(a.y, b.y):
            raise ValueError(f'overlapping component rectangles: {name}')
        key = prefix + name
        # Inactive lineup resources are neither loaded nor opened for validation.
        if key not in sizes:
            continue
        width, height = sizes[key]
        if any(r.right > width or r.bottom > height for r in parts):
            raise ValueError(f'component outside template: {name}')
        layouts[key] = ComponentLayout(*parts, tuple(tolerance))
    return layouts
