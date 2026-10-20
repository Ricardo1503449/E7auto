"""Independent game-like composition and full-frame resizing for component tests."""
import cv2
import numpy as np

from e7auto.core.types import Size
from e7auto.vision.frames import CoordinateTransform, FrameView


def stamp_part(frame, data, rect, origin, shift=(0, 0)):
    x, y = origin[0]+rect.x+shift[0], origin[1]+rect.y+shift[1]
    pixels = data.image[rect.y:rect.bottom, rect.x:rect.right]
    mask = (np.ones(pixels.shape[:2], bool) if data.mask is None else
            data.mask[rect.y:rect.bottom, rect.x:rect.right] > 0)
    target = frame[y:y+rect.height, x:x+rect.width]
    assert target.shape == pixels.shape
    target[mask] = pixels[mask]


def component_frame(data, layout, size=(2322, 1306), *, origin=(700, 340), shift=(0, 0),
                    primary=True, secondary=True, duplicates=()):
    frame = np.full((1306, 2322, 3), 24, np.uint8)
    if primary:
        stamp_part(frame, data, layout.primary, origin)
    if secondary:
        stamp_part(frame, data, layout.secondary, origin, shift)
    for offset in duplicates:
        stamp_part(frame, data, layout.secondary, origin, offset)
    if size != (2322, 1306):
        frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA if size[0] < 2322 else cv2.INTER_LINEAR)
    return FrameView(frame, CoordinateTransform(Size(2322, 1306), Size(*size)))
