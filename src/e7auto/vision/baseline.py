"""Validated 1:1 matching, isolated from non-reference-size adaptation.

Preserves the pre-native-migration OpenCV scoring, coarse shortlist and local
search. Only the result adapter uses the current client-coordinate contract.
Changes for scaled clients must not alter this implementation implicitly.
"""
import cv2
import numpy as np

from e7auto.core.observations import MatchGeometry, Observation
from e7auto.core.types import ClientPoint, ClientRect
from e7auto.vision.frames import frame_view


def scores(source, template, mask, threshold, structure_threshold=None, *, correlation=False):
    h, w = template.shape[:2]
    if source.shape[0] < h or source.shape[1] < w:
        return None
    if correlation:
        color = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED, mask=mask)
    else:
        difference = cv2.matchTemplate(source, template, cv2.TM_SQDIFF_NORMED, mask=mask)
        difference = np.nan_to_num(difference, nan=np.inf, posinf=np.inf, neginf=np.inf)
        color = 1-difference
    accepted = np.isfinite(color) & (color >= threshold)
    if not np.any(accepted):
        return None
    structure = None
    if structure_threshold is not None:
        pixels = template.reshape(-1, 3) if mask is None else template[mask > 0]
        if not pixels.size or not np.any(np.ptp(pixels, axis=0)):
            return None
        structure = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED, mask=mask)
        accepted &= np.isfinite(structure) & (structure >= structure_threshold)
    return (np.where(accepted, color, -np.inf), structure) if np.any(accepted) else None


def observation(key, score, structure, x, y, width, height, region):
    bounds = ClientRect(x, y, width, height)
    geometry = MatchGeometry(x, y, width, height, 1., 1.)
    return Observation(key, min(1., float(score)), bounds,
                       ClientPoint(x+width//2, y+height//2), region, structure,
                       (width, height, 0, 0), geometry)


class BaselineMatcher:
    def __init__(self, templates, cache):
        self.templates, self.cache = templates, cache

    def match(self, frame, key, roi, threshold, structure_threshold=None, *, force_color=False):
        view = frame_view(frame).crop(roi)
        data = self.templates.get(key)
        values = scores(np.ascontiguousarray(view.raw), data.image, data.mask,
                        threshold, structure_threshold,
                        correlation=data.mask is None and not force_color)
        if values is None:
            return None
        color, structure = values
        _, score, _, (x, y) = cv2.minMaxLoc(color)
        h, w = data.image.shape[:2]
        return observation(key, score, None if structure is None else float(structure[y, x]),
                           view.origin.x+x, view.origin.y+y, w, h, view.bounds)

    def locate_all(self, frame, key, roi, threshold, structure_threshold, *, limit=12,
                   cancelled=None):
        view = frame_view(frame).crop(roi)
        source = np.ascontiguousarray(view.raw)
        data = self.templates.get(key)
        template = data.image
        h, w = template.shape[:2]
        if h > source.shape[0] or w > source.shape[1]:
            return ()
        scale = .5 if source.shape[0]*source.shape[1] > 350_000 and min(h, w) > 26 else 1.
        self.cache.geometry(view.transform)
        prepared = self.cache.baseline_projection(data, scale)
        small = cv2.resize(source, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else source
        values = cv2.matchTemplate(small, prepared.image, cv2.TM_SQDIFF_NORMED, mask=prepared.mask)
        values = np.nan_to_num(values, nan=np.inf, posinf=np.inf, neginf=np.inf)
        hits = []
        for _ in range(max(5, limit*3)):
            if cancelled is not None and cancelled.is_set():
                return ()
            difference, _, (xx, yy), _ = cv2.minMaxLoc(values)
            if not np.isfinite(difference) or difference > 1-threshold+.20:
                break
            radius = max(3, round(min(w, h)*scale*.3))
            values[max(0, yy-radius):yy+radius+1, max(0, xx-radius):xx+radius+1] = np.inf
            xx, yy = round(xx/scale), round(yy/scale)
            x, y = max(0, xx-5), max(0, yy-5)
            area = source[y:min(len(source), yy+h+6), x:min(source.shape[1], xx+w+6)]
            result = scores(area, template, prepared.full_mask, threshold, structure_threshold)
            if result is None:
                continue
            color, structure = result
            _, score, _, (dx, dy) = cv2.minMaxLoc(color)
            hit = observation(key, score, float(structure[dy, dx]),
                              view.origin.x+x+dx, view.origin.y+y+dy, w, h, view.bounds)
            if any(abs(hit.anchor.x-old.anchor.x) < w*.5 and abs(hit.anchor.y-old.anchor.y) < h*.5 for old in hits):
                continue
            hits.append(hit)
        return tuple(sorted(hits, key=lambda hit: -hit.confidence)[:limit])
