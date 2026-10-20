"""Title indexes shortlist event identities; unchanged full templates authorize hits."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from threading import Event
from time import perf_counter

import cv2
import numpy as np

from e7auto.core.types import Point, Rect, ClientPoint, ClientRect
from e7auto.vision.frames import frame_view
from e7auto.vision.matching import TemplateMatcher
from e7auto.vision.baseline import scores as baseline_scores
from .contracts import Detection
from .event_readiness import EventReadiness


@dataclass(frozen=True)
class Identity:
    key: str
    image: np.ndarray
    mask: np.ndarray
    origin: tuple[int, int]


class EventRecognizer:
    def __init__(self, settings, templates, matcher=None):
        self.matcher = matcher if matcher is not None else TemplateMatcher(templates)
        self.settings, self.p = settings, settings.event_recognition
        self.region = settings.regions['event_options']
        self.identities = {}
        groups = {2: [], 3: []}
        self.features, self.weights = {}, {}
        for key, spec in self.p['templates'].items():
            data = templates.get(settings.scenes[key].template_key)
            image = data.image
            mask = data.mask if data.mask is not None else np.full(image.shape[:2], 255, np.uint8)
            h, w = image.shape[:2]
            item = Identity(key,image,mask,tuple(spec['origin']))
            parts = []
            for title, slot, weight in zip(spec['titles'], self.p['layouts'][spec['layout']], spec['weights']):
                x, y, width, height = title
                ink = self._ink(image[y:y+height, x:x+width]) * (mask[y:y+height, x:x+width] > 0)
                if weight > 0 and not np.any(ink):
                    raise ValueError(f'Empty event title index: {key}')
                canvas = np.zeros((slot[3], slot[2]), np.float32)
                dx, dy = spec['origin'][0]+x-slot[0], spec['origin'][1]+y-slot[1]
                canvas[dy:dy+height, dx:dx+width] = ink
                parts.append(self._feature(canvas))
            self.identities[key] = item
            groups[spec['layout']].append((key, parts, spec['weights']))
            for array in (item.image, item.mask):
                array.setflags(write=False)
        self.keys = {}
        for layout, items in groups.items():
            self.keys[layout] = tuple(item[0] for item in items)
            self.features[layout] = np.asarray([item[1] for item in items], np.float32)
            self.weights[layout] = np.asarray([item[2] for item in items], np.float32)
            self.features[layout].setflags(write=False)
            self.weights[layout].setflags(write=False)
        self.readiness = EventReadiness(settings, self.identities)
        self._pool = None
        self.active = False
        self.stats = {}
        self.invalidate()

    def _ink(self, image):
        pixels = image.astype(np.int16)
        return ((pixels.min(2) > self.p['foreground_min'])
                & (np.ptp(pixels, axis=2) < self.p['channel_spread_max'])).astype(np.float32)

    def _feature(self, ink):
        return cv2.resize(ink, (self.p['feature_width'], self.p['feature_height']), interpolation=cv2.INTER_AREA)

    def invalidate(self):
        self.candidate = None
        self.readiness.reset()

    def begin(self):
        self.active = True
        self.invalidate()
        self.stats = dict(event_frames=0, event_index_queries=0, event_candidates_checked=0,
                          event_cache_checks=0, event_cache_failures=0, event_fallbacks=0,
                          event_readiness_waits=0,
                          event_recognition_ms=0.)

    def end(self):
        result = {k: round(v, 3) if isinstance(v, float) else v for k, v in self.stats.items()}
        self.active = False
        self.invalidate()
        return result

    def close(self):
        pool, self._pool = self._pool, None
        try:
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
        finally:
            self.active = False
            self.invalidate()

    def rank(self, layout, query):
        features, weights = self.features[layout], self.weights[layout]
        shift = self.p['feature_shift']
        padded = np.pad(query, ((0, 0), (shift, shift), (shift, shift)), mode='constant')
        scores = np.full(len(features), np.inf, np.float32)
        h, w = query.shape[-2:]
        for dy in range(2*shift+1):
            for dx in range(2*shift+1):
                q = padded[:, dy:dy+h, dx:dx+w]
                error = ((features-q)**2).sum(axis=(2, 3))
                energy = (features**2+q**2).sum(axis=(2, 3))
                per_title = error / np.maximum(energy, 1e-6)
                distance = (per_title*weights).sum(1) / weights.sum(1)
                scores = np.minimum(scores, distance)
        return np.argsort(scores, kind='stable'), scores

    def _verify_baseline(self, view, key, previous, cancelled, try_reference, small_source):
        """Restore the original reference/local/half-size event search at 1:1."""
        item, scene = self.identities[key], self.settings.scenes[key]
        source = view.raw
        h, w = item.image.shape[:2]

        def match_at(region):
            x, y = max(0,region.x), max(0,region.y)
            right, bottom = min(source.shape[1],region.right), min(source.shape[0],region.bottom)
            if right <= x or bottom <= y:
                return None
            result = baseline_scores(source[y:bottom,x:right],item.image,item.mask,scene.threshold,scene.structure)
            if result is None:
                return None
            _, score, _, (dx,dy) = cv2.minMaxLoc(result[0])
            bounds = ClientRect(view.origin.x+x+dx,view.origin.y+y+dy,w,h)
            ax,ay = scene.action or (.5,.5)
            ox,oy = scene.action_offset
            return Detection(key,min(1.,float(score)),bounds,
                             ClientPoint(round(bounds.x+w*ax)+ox,round(bounds.y+h*ay)+oy))

        x,y = item.origin if previous is None else (previous.bounds.x,previous.bounds.y)
        pad = self.p['local_padding']
        local = Rect(x-view.origin.x-pad,y-view.origin.y-pad,w+2*pad,h+2*pad)
        if try_reference or previous is not None:
            hit = match_at(local)
            if hit is not None or previous is not None:
                return hit
        if cancelled is not None and cancelled.is_set():
            return None
        data = self.matcher._templates.get(scene.template_key)
        self.matcher.cache.geometry(view.transform)
        prepared = self.matcher.cache.baseline_projection(data,.5)
        if small_source is None:
            small_source = cv2.resize(source,None,fx=.5,fy=.5,interpolation=cv2.INTER_AREA)
        values = cv2.matchTemplate(small_source,prepared.image,cv2.TM_SQDIFF_NORMED,mask=prepared.mask)
        values = np.nan_to_num(values,nan=np.inf,posinf=np.inf,neginf=np.inf)
        for _ in range(5):
            if cancelled is not None and cancelled.is_set():
                return None
            difference,_,(xx,yy),_ = cv2.minMaxLoc(values)
            if not np.isfinite(difference) or difference > 1-scene.threshold+.20:
                break
            radius = max(3,round(min(w,h)*.5*.3))
            values[max(0,yy-radius):yy+radius+1,max(0,xx-radius):xx+radius+1] = np.inf
            xx,yy = round(xx/.5),round(yy/.5)
            x,y = max(0,xx-5),max(0,yy-5)
            hit = match_at(Rect(x,y,min(source.shape[1],xx+w+6)-x,min(source.shape[0],yy+h+6)-y))
            if hit is not None:
                return hit
        return None

    def verify(self, source, key, previous=None, cancelled=None, *, try_reference=True, small_source=None, preparation=None):
        view = frame_view(source)
        if view.transform.is_identity:
            return self._verify_baseline(view,key,previous,cancelled,try_reference,small_source)
        item = self.identities[key]
        scene = self.settings.scenes[key]
        sx,sy = view.transform.scale_x,view.transform.scale_y
        pad_x,pad_y = self.p['local_padding']*sx,self.p['local_padding']*sy
        if previous is None:
            r = view.transform.rect(Rect(*item.origin,item.image.shape[1],item.image.shape[0]))
        else:
            r = previous.bounds
        local = ClientRect(round(r.x-pad_x),round(r.y-pad_y),round(r.width+2*pad_x),round(r.height+2*pad_y))
        def search(region):
            region = view.region(region, clip=True)
            hits = self.matcher.locate_all(view,scene.template_key,region,scene.threshold,scene.structure,
                                           limit=1,cancelled=cancelled,preparation=preparation)
            if not hits:
                return None
            hit = hits[0]
            ax,ay = scene.action or (.5,.5)
            dx,dy = scene.action_offset
            b = hit.bounds
            return Detection(key,hit.confidence,b,hit.geometry.point((ax,ay),(dx,dy)),hit)
        if previous is not None and previous.evidence is not None:
            observed=self.matcher.recheck(view,previous.evidence,scene.threshold,scene.structure)
            if observed is not None:
                return Detection(key,observed.confidence,observed.bounds,observed.geometry.point(scene.action or (.5,.5),scene.action_offset),observed)
        if previous is not None:
            hit=search(previous.bounds)
            if hit is not None:
                return hit
        elif try_reference:
            origin_x,origin_y=item.origin[0]*sx,item.origin[1]*sy
            for candidate in self.matcher.candidates(view,scene.template_key):
                if cancelled is not None and cancelled.is_set():
                    return None
                w,h=candidate.size
                bounds=ClientRect(round(origin_x-candidate.offset[0]),round(origin_y-candidate.offset[1]),w,h)
                patch=view.crop(bounds,clip=True)
                if patch.bounds!=bounds:continue
                scores=self.matcher.scores(patch.raw,candidate,scene.threshold,scene.structure,force_color=True)
                if scores is not None:
                    hit=self.matcher.observation(scene.template_key,scores[0][0,0],float(scores[1][0,0]),
                                                 bounds.x,bounds.y,candidate,view.bounds)
                    if hit is not None:
                        return Detection(key,hit.confidence,hit.bounds,hit.geometry.point(scene.action or (.5,.5),scene.action_offset),hit)
        if try_reference or previous is not None:
            hit = search(local)
            if hit is not None or previous is not None:
                return hit
        if cancelled is not None and cancelled.is_set():
            return None
        return search(view.bounds)

    def _discover_group(self, layout, query, source, small_source, cancelled, preparation=None):
        order, _ = self.rank(layout, query)
        stats = dict(event_index_queries=1, event_candidates_checked=0, event_fallbacks=0)
        for position, index in enumerate(order):
            if cancelled.is_set():
                break
            if position == self.p['top_k']:
                stats['event_fallbacks'] += 1
            stats['event_candidates_checked'] += 1
            hit = self.verify(source, self.keys[layout][int(index)], cancelled=cancelled,
                              try_reference=position < self.p['top_k'], small_source=small_source,
                              preparation=preparation)
            if hit is not None:
                cancelled.set()
                return hit, stats
        return None, stats

    def _discover(self, source, queries):
        if self._pool is None:
            self._pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='chaos-event')
        # One read-only coarse image belongs to this discovery frame only.
        # Cached identity rechecks never enter this path. Nonreference workers
        # share only this invocation's full ROI preparation, not scores or pixels
        # from another frame; local regions retain their own preparation.
        small_source = None
        preparation = None
        if source.transform.is_identity:
            small_source = cv2.resize(source.raw,None,fx=.5,fy=.5,interpolation=cv2.INTER_AREA)
            small_source.setflags(write=False)
        else:
            preparation = self.matcher.prepare_search(source)
        cancelled=Event()
        futures=[]
        winner = None
        try:
            for layout in (2, 3):
                futures.append(self._pool.submit(self._discover_group, layout, queries[layout],
                                                 source, small_source, cancelled, preparation))
            for future in as_completed(futures):
                hit, _ = future.result()
                if hit is not None and winner is None:
                    winner = hit
                    cancelled.set()
            # Workers have drained; only the owner updates shared statistics.
            for future in futures:
                _, stats = future.result()
                for key, value in stats.items():
                    self.stats[key] += value
            return winner
        except BaseException:
            cancelled.set()
            for future in futures:
                future.cancel()
            wait(futures)
            self.invalidate()
            raise

    def recognize(self, frame):
        if not self.active:
            self.begin()
            try:
                # Preserve one-shot, read-only inspection. The production entry
                # owns begin/end and requires readiness on consecutive new frames.
                return self._recognize(frame, stable=False)
            finally:
                self.end()
        return self._recognize(frame, stable=True)

    def _recognize(self, frame, *, stable):
        started = perf_counter()
        self.stats['event_frames'] += 1
        try:
            source = frame_view(frame).crop(self.region)
            pixels=source.raw.view()
            pixels.setflags(write=False)
            source=type(source)(pixels,source.transform,source.origin)
            if self.candidate is not None:
                self.stats['event_cache_checks'] += 1
                self.candidate = self.verify(source, self.candidate.key, self.candidate)
                if self.candidate is None:
                    self.stats['event_cache_failures'] += 1
                    self.readiness.reset()
                return self.candidate
            queries = {}
            for layout, slots in self.p['layouts'].items():
                parts = []
                for x, y, w, h in slots:
                    pixels = source.crop(Rect(x,y,w,h)).raw
                    parts.append(self._feature(self._ink(pixels)))
                queries[layout] = np.asarray(parts, np.float32)
                queries[layout].setflags(write=False)
            # Intro/fade frames can have no visible option titles at either size.
            # Return for a fresh frame instead of exhausting every full template.
            # Cached identities above still recheck the current pixels as before.
            if not any(np.any(query) for query in queries.values()):
                self.readiness.reset()
                return None
            if not self.readiness.observe(source, stable=stable):
                self.stats['event_readiness_waits'] += 1
                return None
            self.candidate = self._discover(source, queries)
            return self.candidate
        finally:
            self.stats['event_recognition_ms'] += (perf_counter()-started)*1000
