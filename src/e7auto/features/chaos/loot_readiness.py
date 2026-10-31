"""Card completeness and settling, following the validated event readiness gate.

Only descriptors and numeric geometry survive an observation. This gate neither
identifies loot nor supplies click coordinates; native matching still does both.
"""
from __future__ import annotations

import cv2
import numpy as np

from e7auto.core.types import ClientRect, Rect


class LootReadiness:
    def __init__(self, settings, templates):
        index = settings.loot_recognition
        self.p = index['readiness']
        self.stable_frames = settings.timing['stable_frames']
        self.bands = tuple(Rect(*index[name]) for name in ('icon_band', 'name_band'))
        # The validated 128x16 title grid defines a physical sampling step.
        # Icon/name bands have different extents: derive their grid sizes rather
        # than incorrectly comparing equal grid offsets as equal pixel offsets.
        name = self.bands[1]
        self.step = np.array((name.width/self.p['feature_width'],
                              name.height/self.p['feature_height']))
        self.sizes = tuple((max(1, round(b.width/self.step[0])),
                            max(1, round(b.height/self.step[1]))) for b in self.bands)
        self.steps = tuple(np.array((b.width/w, b.height/h))
                           for b, (w, h) in zip(self.bands, self.sizes))
        features = [[], []]
        for key in settings.available_loot:
            data = templates.get(settings.scenes[key].template_key)
            spec = index['template_regions'][key]
            detail = self._detail(data.image)
            if data.mask is not None:
                detail *= data.mask > 0
            for i, (label, band) in enumerate(zip(('icon', 'name'), self.bands)):
                x, y, w, h = spec[label]
                dx = spec['origin'][0]-index['card_offset'][0]+x-band.x
                dy = spec['origin'][1]-index['card_offset'][1]+y-band.y
                canvas = np.zeros((band.height, band.width), np.float32)
                canvas[dy:dy+h, dx:dx+w] = detail[y:y+h, x:x+w]
                part = cv2.resize(canvas, self.sizes[i], interpolation=cv2.INTER_AREA)
                if not np.any(part):
                    raise ValueError(f'Empty loot readiness {label}: {key}')
                features[i].append(part)
        self.features = tuple(np.asarray(parts, np.float32) for parts in features)
        self.energy = tuple((parts*parts).sum(axis=(1, 2)) for parts in self.features)
        self.contrast = tuple(parts.sum(axis=(1, 2)) for parts in self.features)
        for array in (*self.features, *self.energy, *self.contrast, self.step, *self.steps):
            array.setflags(write=False)
        radius = self.p['feature_shift']
        offsets = sorted(((dx, dy) for dy in range(-radius, radius+1)
                          for dx in range(-radius, radius+1)), key=lambda p: p[0]**2+p[1]**2)
        self.offsets = tuple((offset, tuple(tuple(np.rint(np.array(offset)*self.step/step).astype(int))
                                           for step in self.steps)) for offset in offsets)
        self.padding = tuple(tuple(max(abs(parts[i][axis]) for _, parts in self.offsets)
                                   for axis in (0, 1)) for i in (0, 1))
        self.reset()

    def reset(self):
        self._geometry = None
        self._previous = self._positions = self._counts = None

    def _detail(self, raw, sx=1., sy=1.):
        gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
        k = self.p['contrast_kernel']
        kernel = np.ones((max(3, round(k*sy)) | 1, max(3, round(k*sx)) | 1), np.uint8)
        return cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel).astype(np.float32)/255

    def _queries(self, card):
        sx, sy = card.transform.scale_x, card.transform.scale_y
        result = []
        for band, size in zip(self.bands, self.sizes):
            x, y = round(band.x*sx), round(band.y*sy)
            region = ClientRect(card.origin.x+x, card.origin.y+y,
                                round(band.right*sx)-x, round(band.bottom*sy)-y)
            pixels = card.crop(region).raw
            query = cv2.resize(self._detail(pixels, sx, sy), size, interpolation=cv2.INTER_AREA)
            query.setflags(write=False)
            result.append(query)
        return tuple(result)

    def _complete(self, queries):
        count = len(self.features[0])
        scores = np.zeros(count, np.float32)
        positions = np.zeros((count, 2), np.int16)
        part_scores = np.zeros((count, 2), np.float32)
        part_positions = np.zeros((count, 2, 2), np.float64)
        padded = tuple(np.pad(query, ((py, py), (px, px)))
                       for query, (px, py) in zip(queries, self.padding))
        for offset, parts in self.offsets:
            similarities = []
            for i, ((dx, dy), (px, py), query, features) in enumerate(
                    zip(parts, self.padding, queries, self.features)):
                h, w = query.shape
                current = padded[i][py+dy:py+dy+h, px+dx:px+dx+w]
                energy = self.energy[i]+(current*current).sum()
                similarity = 2*(features*current).sum(axis=(1, 2))/np.maximum(energy, 1e-10)
                better = similarity > part_scores[:, i]
                part_positions[better, i] = np.array((dx, dy))*self.steps[i]
                part_scores[:, i] = np.maximum(part_scores[:, i], similarity)
                similarities.append(similarity)
            weakest = np.minimum(*similarities)
            better = weakest > scores
            positions[better] = offset
            scores = np.maximum(scores, weakest)
        enough_contrast = np.logical_and.reduce([
            query.sum() >= contrast*self.p['contrast_ratio_min']
            for query, contrast in zip(queries, self.contrast)])
        aligned = (np.ptp(part_positions, axis=1) <= self.p['alignment_tolerance']*self.step+1e-9).all(axis=1)
        return (scores >= self.p['similarity_min']) & enough_contrast & aligned, positions

    def observe(self, cards, *, stable=True):
        geometry = tuple((card.transform, card.origin, card.raw.shape) for card in cards)
        if geometry != self._geometry:
            self.reset()
            self._geometry = geometry
        queries = tuple(self._queries(card) for card in cards)
        results = tuple(self._complete(query) for query in queries)
        if not all(np.any(complete) for complete, _ in results):
            self._previous = self._positions = self._counts = None
            return False
        counts = []
        for slot, (query, (complete, positions)) in enumerate(zip(queries, results)):
            unchanged = False
            if self._previous is not None:
                previous = self._previous[slot]
                unchanged = all(np.abs(part-old).sum()/max(part.sum(), old.sum(), 1e-10)
                                <= self.p['feature_change_max'] for part, old in zip(query, previous))
            same_position = (positions == (positions if self._positions is None else self._positions[slot])).all(axis=1)
            previous_count = 0 if self._counts is None else self._counts[slot]
            count = np.where(complete, np.where(unchanged & same_position, previous_count+1, 1), 0)
            counts.append(np.minimum(count, self.stable_frames))
        self._previous = queries
        self._positions = tuple(position for _, position in results)
        self._counts = tuple(counts)
        return all(np.any(count >= (self.stable_frames if stable else 1)) for count in counts)
