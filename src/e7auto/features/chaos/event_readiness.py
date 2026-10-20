"""Cheap title completeness checks before the event identity search.

Only numeric title descriptors survive a frame. This gate never identifies an
event for dispatch or supplies input coordinates; full matching still does both.
"""
from __future__ import annotations

import cv2
import numpy as np

from e7auto.core.types import Rect


class EventReadiness:
    def __init__(self, settings, identities):
        index = settings.event_recognition
        self.p = index['readiness']
        self.layouts = index['layouts']
        self.size = (index['feature_width'], index['feature_height'])
        self.stable_frames = settings.timing['stable_frames']
        self.features, self.energy, self.contrast = {}, {}, {}
        for layout, slots in self.layouts.items():
            references = []
            for key, spec in index['templates'].items():
                if spec['layout'] != layout:
                    continue
                item = identities[key]
                detail = self._detail(item.image) * (item.mask > 0)
                parts = []
                # Disabled titles have zero ranking weight, but their visible
                # gray strokes still belong to a complete options layout.
                for (x, y, w, h), (xx, yy, ww, hh) in zip(spec['titles'], slots):
                    canvas = np.zeros((hh, ww), np.float32)
                    dx, dy = spec['origin'][0]+x-xx, spec['origin'][1]+y-yy
                    canvas[dy:dy+h, dx:dx+w] = detail[y:y+h, x:x+w]
                    part = self._feature(canvas)
                    if not np.any(part):
                        raise ValueError(f'Empty event readiness title: {key}')
                    parts.append(part)
                references.append(parts)
            features = np.asarray(references, np.float32)
            self.features[layout] = features
            self.energy[layout] = (features*features).sum(axis=(2, 3))
            self.contrast[layout] = features.sum(axis=(2, 3))
            for value in (features, self.energy[layout], self.contrast[layout]):
                value.setflags(write=False)
        self.reset()

    def reset(self):
        self._geometry = None
        self._previous, self._positions, self._counts = {}, {}, {}

    def _detail(self, raw, sx=1., sy=1.):
        gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
        k = self.p['contrast_kernel']
        kernel = np.ones((max(3, round(k*sy)) | 1, max(3, round(k*sx)) | 1), np.uint8)
        # Local contrast retains gray letters and suppresses slow background
        # shading. There is no global bright-pixel threshold here.
        return cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel).astype(np.float32)/255

    def _feature(self, detail):
        return cv2.resize(detail, self.size, interpolation=cv2.INTER_AREA)

    def _complete(self, layout, query):
        features = self.features[layout]
        shift = self.p['feature_shift']
        padded = np.pad(query, ((0, 0), (shift, shift), (shift, shift)))
        h, w = query.shape[-2:]
        scores = np.zeros(len(features), np.float32)
        positions = np.zeros((len(features), 2), np.int16)
        title_scores = np.zeros(features.shape[:2], np.float32)
        title_positions = np.zeros((*features.shape[:2], 2), np.int16)
        # A single translation must fit EVERY title in a candidate layout.
        # Taking the weakest title prevents a clear left title hiding a missing
        # right title. The existing identity-index weights remain unchanged.
        offsets = sorted(((dy, dx) for dy in range(2*shift+1) for dx in range(2*shift+1)),
                         key=lambda p: (p[0]-shift)**2+(p[1]-shift)**2)
        for dy, dx in offsets:
            current = padded[:, dy:dy+h, dx:dx+w]
            energy = self.energy[layout] + (current*current).sum(axis=(1, 2))
            similarity = 2*(features*current).sum(axis=(2, 3))/np.maximum(energy, 1e-10)
            better_title = similarity > title_scores
            title_positions[better_title] = (dx-shift, dy-shift)
            title_scores = np.maximum(title_scores, similarity)
            weakest = similarity.min(axis=1)
            better = weakest > scores
            positions[better] = (dx-shift, dy-shift)
            scores = np.maximum(scores, weakest)
        contrast = query.sum(axis=(1, 2))
        enough_contrast = (contrast >= self.contrast[layout]*self.p['contrast_ratio_min']).all(axis=1)
        # A compromise between a settled title and a still-moving title can
        # otherwise obtain a moderate joint score. Their individually measured
        # offsets must also agree, within the descriptor's sampling tolerance.
        aligned = (np.ptp(title_positions, axis=1) <= self.p['alignment_tolerance']).all(axis=1)
        return (scores >= self.p['similarity_min']) & enough_contrast & aligned, positions

    def observe(self, source, *, stable=True):
        geometry = (source.transform, source.origin, source.raw.shape)
        if geometry != self._geometry:
            self.reset()
            self._geometry = geometry
        ready = False
        for layout, slots in self.layouts.items():
            query = np.asarray([
                self._feature(self._detail(source.crop(Rect(*slot)).raw,
                                           source.transform.scale_x, source.transform.scale_y))
                for slot in slots], np.float32)
            complete, positions = self._complete(layout, query)
            previous = self._previous.get(layout)
            unchanged = False
            if previous is not None:
                change = np.abs(query-previous).sum(axis=(1, 2))
                scale = np.maximum(query.sum(axis=(1, 2)), previous.sum(axis=(1, 2)))
                unchanged = bool(np.all(change/np.maximum(scale, 1e-10) <= self.p['feature_change_max']))
            same_position = (positions == self._positions.get(layout, positions)).all(axis=1)
            counts = np.where(complete,
                              np.where(unchanged & same_position, self._counts.get(layout, 0)+1, 1), 0)
            self._counts[layout] = np.minimum(counts, self.stable_frames)
            query.setflags(write=False)
            self._previous[layout], self._positions[layout] = query, positions
            ready |= bool(np.any(counts >= (self.stable_frames if stable else 1)))
        return ready
