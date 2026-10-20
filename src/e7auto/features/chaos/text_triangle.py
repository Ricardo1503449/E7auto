"""Local edge contrast for the pale, downward text triangle on bright scenery."""
from collections.abc import Mapping

import cv2
import numpy as np

from e7auto.core.types import Rect


def bright_triangle(bgr: np.ndarray, size: tuple[int, int], settings: Mapping[str, float]) -> tuple[Rect, float] | None:
    """Require positive contrast along all three sides, independent of absolute color.

    Size comes from the approved triangle template, after normal window adaptation.
    Each edge compares nearby inner and outer samples; unrelated or multiple shapes
    are rejected. Coordinates are local to the existing text-continuation ROI.
    """
    width, height = size
    distance = int(settings['edge_distance_px'])
    padding = distance + 2
    rows, columns = bgr.shape[:2]
    if width + 2*padding > columns or height + 2*padding > rows:
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    xx, yy = np.meshgrid(np.arange(padding, columns-width-padding+1),
                         np.arange(padding, rows-height-padding+1))
    vertices = [(0., 0.), (width-1., 0.), ((width-1)/2, height-1.)]
    supports, contrasts, brightness = [], [], []
    margin = settings['edge_margin_fraction']
    for edge in range(3):
        start = np.array(vertices[edge])
        delta = np.array(vertices[(edge+1) % 3]) - start
        inward = np.array([-delta[1], delta[0]]) / np.linalg.norm(delta)
        differences, inner_pixels = [], []
        for fraction in np.linspace(margin, 1-margin, int(settings['edge_samples'])):
            point = start + fraction*delta
            ix, iy = np.rint(point + inward*distance).astype(int)
            ox, oy = np.rint(point - inward*distance).astype(int)
            inside, outside = gray[yy+iy, xx+ix], gray[yy+oy, xx+ox]
            inner_pixels.append(inside)
            differences.append(inside-outside)
        differences = np.array(differences)
        supports.append((differences >= settings['pixel_contrast_min']).mean(axis=0))
        contrasts.append(np.median(differences, axis=0))
        brightness.append(np.mean(inner_pixels, axis=0))
    support = np.min(supports, axis=0)
    contrast = np.min(contrasts, axis=0)
    accepted = ((support >= settings['edge_support_min'])
                & (contrast >= settings['edge_contrast_min'])
                & (np.min(brightness, axis=0) >= settings['interior_brightness_min']))
    if not np.any(accepted):
        return None
    # Support is primary; contrast breaks ties without letting one strong edge
    # compensate for a missing side. Multiple spatially distinct hits are unsafe.
    quality = support + np.minimum(contrast, 30)/300
    _, score, _, (x, y) = cv2.minMaxLoc(np.where(accepted, quality, -1.))
    ys, xs = np.nonzero(accepted)
    if np.any((abs(xs-x) > width/2) | (abs(ys-y) > height/2)):
        return None
    return Rect(x+padding, y+padding, width, height), min(1., float(score)/1.1)
