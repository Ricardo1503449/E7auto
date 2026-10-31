"""Card-local recognition; caches identities and anchor fingerprints, never frames."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor, wait
from time import perf_counter
import cv2
import numpy as np

from e7auto.core.types import Point, Rect, ClientPoint, ClientRect
from e7auto.vision.frames import frame_view
from e7auto.vision.candidates import sampling_phases
from e7auto.core.observations import MatchGeometry
from e7auto.vision.matching import TemplateMatcher
from e7auto.vision.baseline import scores as baseline_scores
from .contracts import Detection
from .loot_readiness import LootReadiness


@dataclass(frozen=True)
class Part:
    image: np.ndarray
    mask: np.ndarray
    rect: Rect


@dataclass(frozen=True)
class Identity:
    key: str
    icon: Part
    name: Part
    origin: tuple[int, int]
    size: tuple[int, int]


class LootRecognizer:
    def __init__(self, settings, templates, matcher):
        self.settings, self.p, self.matcher = settings, settings.loot_recognition, matcher
        self._templates = templates
        self.identities = []
        features, masks = [], []
        self.icon_band = Rect(*self.p['icon_band'])
        self.name_band = Rect(*self.p['name_band'])
        n = self.p['feature_size']
        for key in settings.available_loot:
            data = templates.get(settings.scenes[key].template_key)
            spec = self.p['template_regions'][key]
            parts = []
            for part in ('icon', 'name'):
                r = Rect(*spec[part])
                mask = (np.full(data.image.shape[:2], 255, np.uint8) if data.mask is None else data.mask)[r.y:r.bottom, r.x:r.right].copy()
                if not np.any(mask):
                    raise ValueError(f'Empty loot {part} mask: {key}')
                pixels = data.image[r.y:r.bottom, r.x:r.right].copy()
                if part == 'name':
                    pixels = cv2.GaussianBlur(pixels,(3,3),0)
                parts.append(Part(pixels, mask, r))
            item = Identity(key, *parts, tuple(spec['origin']), (data.image.shape[1], data.image.shape[0]))
            self.identities.append(item)
            # Align all icon features in the same card-relative canvas. Only original
            # foreground pixels contribute; no wallpaper or text enters the index.
            band = self.icon_band
            canvas = np.zeros((band.height, band.width, 3), np.float32)
            weights = np.zeros(canvas.shape[:2], np.float32)
            x = item.origin[0]-self.p['card_offset'][0]+item.icon.rect.x-band.x
            y = item.origin[1]-self.p['card_offset'][1]+item.icon.rect.y-band.y
            h, w = item.icon.mask.shape
            weights[y:y+h, x:x+w] = item.icon.mask/255.
            canvas[y:y+h, x:x+w] = item.icon.image * (item.icon.mask[:, :, None]/255.)
            small_mask = cv2.resize(weights, (n,n), interpolation=cv2.INTER_AREA)
            small = cv2.resize(canvas, (n,n), interpolation=cv2.INTER_AREA)
            features.append(small / np.maximum(small_mask[:, :, None], 1e-6) / 255.)
            masks.append(small_mask[:, :, None])
        self.features = np.stack(features)
        self.weights = np.stack(masks)
        self.energy = (self.features**2*self.weights).sum(axis=(1,2,3))
        self.identities = tuple(self.identities)
        for item in self.identities:
            for part in (item.icon, item.name):
                part.image.setflags(write=False)
                part.mask.setflags(write=False)
        for array in (self.features, self.weights, self.energy):
            array.setflags(write=False)
        self._pool: ThreadPoolExecutor | None = None
        self.readiness = LootReadiness(settings, templates)
        self.active = False
        self.stats = {}
        self.invalidate()

    def invalidate(self):
        self.cache = [None, None, None]
        self.layout = None
        self._anchor_cache = None
        self.readiness.reset()

    def begin(self):
        self.active = True
        self.invalidate()
        self.stats = dict(frames=0, anchor_ms=0., anchor_cache_hits=0, index_ms=0., icon_ms=0., name_ms=0.,
                          recognition_ms=0., first_frame_ms=0., index_queries=0,
                          candidates_checked=0, cache_checks=0, cache_failures=0,
                          name_expansions=0, fallback_cards=0, ambiguous_cards=0,
                          layout_resets=0, last_keys=[], parallel_workers=3,
                          parallel_batches=0, card_jobs=0, cards_wall_ms=0.,
                          readiness_waits=0, readiness_ms=0.)

    def end(self):
        result = {k: round(v,3) if isinstance(v,float) else v for k,v in self.stats.items()}
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

    def _add(self, key, value=1, *, stats=None):
        target = self.stats if stats is None else stats
        target[key] = target.get(key, 0) + value

    def _anchors(self, frame, scene):
        # Compare the COMPLETE native search area of each new frame. A
        # changed pixel anywhere (including a fourth anchor) forces full search.
        # Keep only the digest and detections, not any captured pixel buffer.
        source = self.matcher.crop(frame, scene.region)
        fingerprint = (source.shape, source.dtype.str, sha256(source).digest())
        data = self._templates.get(scene.template_key)
        cached = self._anchor_cache
        if cached is not None and cached[0] == scene and cached[1] is data and cached[2] == fingerprint:
            self._add('anchor_cache_hits')
            return cached[3]
        anchors = tuple(sorted(self.matcher.locate_all(
            frame, scene.template_key, scene.region, scene.threshold, scene.structure, limit=6),
            key=lambda o:o.bounds.x))
        self._anchor_cache = (scene, data, fingerprint, anchors) if len(anchors) == 3 else None
        return anchors

    def rank(self, card, *, stats=None):
        started = perf_counter()
        view = frame_view(card)
        band, n = self._band(view,self.icon_band), self.p['feature_size']
        query = cv2.resize(view.raw[band.y:band.bottom,band.x:band.right], (n,n), interpolation=cv2.INTER_AREA).astype(np.float32)/255.
        shift = self.p['feature_shift']
        padded = np.pad(query, ((shift,shift),(shift,shift),(0,0)), mode='edge')
        distances = np.full(len(self.identities), np.inf, np.float32)
        for dy in range(2*shift+1):
            for dx in range(2*shift+1):
                q = padded[dy:dy+n, dx:dx+n]
                error = ((self.features-q)**2*self.weights).sum(axis=(1,2,3))
                energy = (q**2*self.weights).sum(axis=(1,2,3))
                distances = np.minimum(distances, error / np.maximum(np.sqrt(self.energy*energy),1e-6))
        self._add('index_queries', stats=stats)
        self._add('index_ms', (perf_counter()-started)*1000, stats=stats)
        return np.argsort(distances, kind='stable'), distances

    @staticmethod
    def _match(source, part, region, color_min, structure_min, *, baseline=False):
        x,y = max(0,region.x),max(0,region.y)
        right,bottom = min(source.shape[1],region.right),min(source.shape[0],region.bottom)
        area = source[y:bottom,x:right]
        if area.shape[0] < part.image.shape[0] or area.shape[1] < part.image.shape[1]:
            return None
        if baseline:
            values=baseline_scores(area,part.image,part.mask,color_min,structure_min)
            if values is None:return None
            _,score,_,point=cv2.minMaxLoc(values[0])
            return x+point[0],y+point[1],min(1.,float(score))
        h,w=part.image.shape[:2]
        cx,cy=(area.shape[1]-w)//2,(area.shape[0]-h)//2
        # The geometric reference is only a proposal; current pixels must pass
        # both unchanged gates before it can avoid the padded local search.
        exact=TemplateMatcher.scores(area[cy:cy+h,cx:cx+w],part,color_min,structure_min,force_color=True)
        if exact is not None:
            return x+cx,y+cy,min(1.,float(exact[0][0,0]))
        scores=TemplateMatcher.scores(area,part,color_min,structure_min,force_color=True)
        if scores is None:
            return None
        _,score,_,point = cv2.minMaxLoc(scores[0])
        return x+point[0], y+point[1], min(1.,float(score))

    @staticmethod
    def _band(view, region):
        sx,sy=view.transform.scale_x,view.transform.scale_y
        x,y=round(region.x*sx),round(region.y*sy)
        return Rect(x,y,round(region.right*sx)-x,round(region.bottom*sy)-y)

    def _full_name(self, card, part, match):
        view=frame_view(card)
        band=self._band(view,self.name_band)
        name=view.raw[band.y:band.bottom,band.x:band.right].astype(np.int16)
        glyphs=(name.min(axis=2)>self.p['name_foreground_min'])&(np.ptp(name,axis=2)<self.p['name_channel_spread_max'])
        x,y,_=match
        sx,sy=view.transform.scale_x,view.transform.scale_y
        px,py=max(1,round(3*sx)),max(1,round(3*sy))
        left,top=max(0,x-band.x-px),max(0,y-band.y-py)
        right,bottom=min(band.width,x-band.x+part.rect.width+px),min(band.height,y-band.y+part.rect.height+py)
        glyphs[top:bottom,left:right]=False
        return np.count_nonzero(glyphs)<=self.p['name_extra_pixels_max']*sx*sy

    def verify(self, card, anchor, index, name_image=None, *, stats=None):
        view=frame_view(card)
        item=self.identities[index]
        data=self._templates.get(self.settings.scenes[item.key].template_key)
        self._add('candidates_checked',stats=stats)
        sx,sy=view.transform.scale_x,view.transform.scale_y
        name_image=cv2.GaussianBlur(view.raw,(3,3),0) if name_image is None else name_image
        # Each candidate is a parent geometry. Both parts and their relative
        # offsets derive from it; icon/name cannot independently choose sizes.
        for phase in sampling_phases((sx,sy)):
            parent=self.matcher.cache.get(data,(sx,sy),phase=phase) if (sx,sy)!=(1.,1.) else None
            offset=parent.offset if parent is not None else (0.,0.)
            origin=((item.origin[0]-self.p['card_offset'][0])*sx-offset[0],
                    (item.origin[1]-self.p['card_offset'][1])*sy-offset[1])
            blurred=self.matcher.cache.get(data,(sx,sy),phase=phase,blur=True) if (sx,sy)!=(1.,1.) else None
            parts=[]
            for label,original in (('icon',item.icon),('name',item.name)):
                r=original.rect
                if parent is None:
                    part=original
                else:
                    sample=parent.part_rect(r);x,y,w,h=sample.x,sample.y,sample.width,sample.height
                    variant=parent if label=='icon' else blurred
                    mask=None if variant.mask is None else variant.mask[y:y+h,x:x+w]
                    part=Part(variant.image[y:y+h,x:x+w],mask,Rect(x,y,w,h))
                parts.append((part,parent if label=='icon' else blurred))
            part=parts[0][0]
            padx,pady=max(1,round(self.p['local_padding']*sx)),max(1,round(self.p['local_padding']*sy))
            expected=Rect(round(origin[0])+part.rect.x-padx,round(origin[1])+part.rect.y-pady,
                          part.rect.width+2*padx,part.rect.height+2*pady)
            started=perf_counter()
            icon=self._match(view.raw,part,expected,self.p['icon_threshold'],self.p['icon_structure'],baseline=view.transform.is_identity)
            self._add('icon_ms',(perf_counter()-started)*1000,stats=stats)
            if icon is None:
                continue
            origin=(icon[0]-part.rect.x,icon[1]-part.rect.y)
            part=parts[1][0]
            expected=Rect(origin[0]+part.rect.x-padx,origin[1]+part.rect.y-pady,part.rect.width+2*padx,part.rect.height+2*pady)
            started=perf_counter()
            name=self._match(name_image,part,expected,self.p['name_threshold'],self.p['name_structure'],baseline=view.transform.is_identity)
            if name is None or not self._full_name(view,part,name):
                self._add('name_expansions',stats=stats)
                name=self._match(name_image,part,self._band(view,self.name_band),self.p['name_threshold'],self.p['name_structure'],baseline=view.transform.is_identity)
            accepted=name is not None and self._full_name(view,part,name)
            self._add('name_ms',(perf_counter()-started)*1000,stats=stats)
            if not accepted:
                continue
            ox,oy=view.origin.x+name[0]-part.rect.x,view.origin.y+name[1]-part.rect.y
            geometry=MatchGeometry(ox+offset[0],oy+offset[1],item.size[0]*sx,item.size[1]*sy,sx,sy)
            bounds=geometry.bounds
            scene=self.settings.scenes[item.key]
            action=scene.action or (.5,.5)
            click=geometry.point(action,scene.action_offset)
            if view.transform.is_identity:
                click=ClientPoint(round(bounds.x+bounds.width*action[0])+scene.action_offset[0],
                                  round(bounds.y+bounds.height*action[1])+scene.action_offset[1])
            hit=Detection(item.key,min(icon[2],name[2]),bounds,click)
            return hit
        return None

    def discover(self, card, anchor, name_image=None, *, stats=None):
        order, scores = self.rank(card, stats=stats)
        winner = None
        winner_index = None
        for position,index in enumerate(order):
            if winner is not None and scores[index] > scores[winner_index]+self.p['ambiguity_margin']:
                break  # A clear verified hit ends this card; no remaining library scan.
            if position == self.p['top_k'] and winner is None:
                self._add('fallback_cards', stats=stats)
            hit = self.verify(card,anchor,int(index),name_image, stats=stats)
            if hit is None:
                continue
            if winner is not None:
                self._add('ambiguous_cards', stats=stats)
                return None,None
            winner,winner_index = hit,int(index)
        return winner,winner_index

    def _recognize_card(self, card, anchor, index):
        """Worker task: read pixels/index only, return slot evidence and local totals."""
        stats = {}
        name_image = cv2.GaussianBlur(frame_view(card).raw,(3,3),0)
        if index is None:
            hit,index = self.discover(card,anchor,name_image,stats=stats)
        else:
            self._add('cache_checks',stats=stats)
            hit = self.verify(card,anchor,index,name_image,stats=stats)
            if hit is None:
                index = None
                self._add('cache_failures',stats=stats)
        return hit,index,stats

    def _evaluate_cards(self, jobs):
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='chaos-loot')
        futures = []
        try:
            for job in jobs:
                futures.append(self._pool.submit(self._recognize_card,*job))
            # Submission order is left/middle/right, independent of completion order.
            return [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            wait(futures)
            self.invalidate()
            raise

    def recognize(self, frame):
        if not self.active:
            self.begin()
            try:
                return self._recognize(frame, stable=False)
            finally:
                self.end()
        return self._recognize(frame, stable=True)

    def _recognize(self, frame, *, stable):
        started = perf_counter()
        self._add('frames')
        try:
            prepared = frame_view(frame)
            sx,sy = prepared.transform.scale_x,prepared.transform.scale_y
            scene = self.settings.scenes['loot_reroll_anchor']
            stamp = perf_counter()
            anchors = self._anchors(prepared, scene)
            self._add('anchor_ms',(perf_counter()-stamp)*1000)
            if len(anchors) != 3:
                self.invalidate()
                return None
            layout = tuple((o.bounds.x,o.bounds.y) for o in anchors)
            low,high = (v*sx for v in self.p['anchor_spacing'])
            if (max(y for x,y in layout)-min(y for x,y in layout)>self.p['anchor_y_tolerance']*sy
                    or any(not low <= b[0]-a[0] <= high for a,b in zip(layout,layout[1:]))):
                self.invalidate()
                return None
            if self.layout is not None and any(abs(a-b)>self.p['layout_tolerance']*scale for old,new in zip(self.layout,layout) for a,b,scale in zip(old,new,(sx,sy))):
                self.invalidate()
                self._add('layout_resets')
                self.layout = layout
                return None  # Break consecutive-frame evidence after a layout change.
            self.layout = layout
            jobs = []
            for slot,anchor in enumerate(anchors):
                dx,dy,w,h = self.p['card_offset']
                x,y=round(anchor.bounds.x+dx*sx),round(anchor.bounds.y+dy*sy)
                region=ClientRect(x,y,round(anchor.bounds.x+(dx+w)*sx)-x,round(anchor.bounds.y+(dy+h)*sy)-y)
                allowed = prepared.region(self.settings.regions['loot_cards'])
                if region.x<allowed.x or region.y<allowed.y or region.right>allowed.right or region.bottom>allowed.bottom:
                    self.invalidate()
                    return None
                card = prepared.crop(region)
                pixels=card.raw.view()
                pixels.setflags(write=False)
                card=type(card)(pixels,card.transform,card.origin)
                jobs.append((card,anchor.bounds,self.cache[slot]))
            found = [None]*3

            def evaluate(slots):
                if not slots:
                    return False
                self._add('parallel_batches')
                self._add('card_jobs', len(slots))
                stamp = perf_counter()
                try:
                    results = self._evaluate_cards([jobs[slot] for slot in slots])
                finally:
                    self._add('cards_wall_ms', (perf_counter()-stamp)*1000)
                failed = False
                for slot, (hit, index, stats) in zip(slots, results):
                    for key, value in stats.items():
                        self._add(key, value)
                    self.cache[slot], found[slot] = index, hit
                    failed |= bool(stats.get('cache_failures'))
                return failed

            cached = [slot for slot, job in enumerate(jobs) if job[2] is not None]
            missing = [slot for slot, job in enumerate(jobs) if job[2] is None]
            # As with events, current-pixel cache verification comes first. A
            # failure breaks this frame; it never falls through to discovery.
            if evaluate(cached):
                self.readiness.reset()
                self.stats['last_keys'] = [h.key if h else None for h in found]
                return None
            if missing:
                stamp = perf_counter()
                try:
                    ready = self.readiness.observe(tuple(job[0] for job in jobs), stable=stable)
                finally:
                    self._add('readiness_ms', (perf_counter()-stamp)*1000)
                if not ready:
                    self._add('readiness_waits')
                    self.stats['last_keys'] = [h.key if h else None for h in found]
                    return None
                evaluate(missing)
            if any(hit is None for hit in found):
                self.readiness.reset()
                self.stats['last_keys'] = [h.key if h else None for h in found]
                return None
            self.stats['last_keys'] = [h.key for h in found]
            return tuple(found)
        finally:
            elapsed = (perf_counter()-started)*1000
            self._add('recognition_ms',elapsed)
            if self.stats['frames']==1:
                self.stats['first_frame_ms']=elapsed
