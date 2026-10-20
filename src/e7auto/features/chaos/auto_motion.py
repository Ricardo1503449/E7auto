"""Recognize auto state against its fixed OFF pose; only OFF needs time evidence."""
import cv2
import numpy as np

from .contracts import AutoState


class AutoStateEvidence:
    """A reliable offset proves ON; consecutive reference poses permit OFF."""
    def __init__(self, settings):
        self.settings = settings
        self.begin()

    def _clear_static(self):
        self.static_count = 0
        self.static_started = self.static_last = None

    def begin(self):
        self._clear_static()
        self.last_observed = None
        self.observations = self.misses = self.resets = self.gap_resets = 0
        self.reference_rejects = 0
        self.last_score = self.angle = None
        self.state, self.reason = AutoState.UNKNOWN, 'no_samples'

    def reset(self):
        self._clear_static()
        self.last_observed = None
        self.resets += 1
        self.state, self.reason = AutoState.UNKNOWN, 'evidence_reset'

    def observe(self, now, score, angle, *, reliable=True):
        p = self.settings
        self.observations += 1
        self.last_score = float(score)
        self.angle = angle
        self.state = AutoState.UNKNOWN
        if self.last_observed is not None and now <= self.last_observed:
            self.reset()
            self.reason = 'non_increasing_time'
            return self.state
        self.last_observed = now
        if score < p['visible_score'] or not reliable:
            self._clear_static()
            if score < p['visible_score']:
                self.misses += 1
                self.reason = 'icon_not_visible'
            else:
                self.reference_rejects += 1
                self.reason = 'reference_unreliable'
            return self.state
        period = p['period_degrees']
        angle = (angle+period/2) % period-period/2
        self.angle = angle
        if abs(angle) >= p['active_min_angle_deg']:
            self._clear_static()
            self.state, self.reason = AutoState.ROTATING, 'offset_from_static_reference'
            return self.state
        if abs(angle) > p['static_max_angle_deg']:
            self._clear_static()
            self.reason = 'between_state_angles'
            return self.state
        if self.static_last is not None:
            gap = (now-self.static_last)*1000
            if gap > p['static_max_gap_ms']+1e-6:
                self._clear_static()
                self.gap_resets += 1
            elif gap < p['sample_ms']-1e-6:
                self.reason = 'sample_too_soon'
                return self.state
        if self.static_count == 0:
            self.static_started = now
        self.static_last = now
        self.static_count = min(self.static_count+1, p['static_confirm_frames'])
        if self.static_count >= p['static_confirm_frames']:
            self.state, self.reason = AutoState.STATIC, 'static_reference_confirmed'
        else:
            self.reason = 'static_confirmation_pending'
        return self.state

    def summary(self):
        return dict(state=self.state.value, reason=self.reason,
            observations=self.observations, misses=self.misses,
            evidence_resets=self.resets, gap_resets=self.gap_resets,
            reference_rejects=self.reference_rejects, last_score=self.last_score,
            reference_angle_deg=self.angle, static_samples=self.static_count,
            static_span_ms=round((self.static_last-self.static_started)*1000,3) if self.static_count else 0.,
            criteria={key:self.settings[key] for key in (
                'visible_score','active_min_angle_deg','sample_ms','static_confirm_frames',
                'static_max_gap_ms','static_max_angle_deg')})


class AutoIconState:
    """Shared reference-angle recognition, optionally locating a scaled centre."""
    def __init__(self, settings, template, *, localize=False, alternatives=()):
        self.settings, self.localize = settings, localize
        self.evidence = AutoStateEvidence(settings)
        _, _, width, height = settings['roi']
        y, x = np.indices((height,width))
        self.core_mask = np.hypot(x-width/2,y-height/2) <= settings['core_radius']
        self.core_y, self.core_x = np.nonzero(self.core_mask)
        angles = np.deg2rad(np.arange(0,360,settings['angle_step']))
        radii = np.arange(settings['ring_radii'][0],settings['ring_radii'][1]+1)[:,None]
        self.map_x = (width/2+radii*np.cos(angles)).astype(np.float32)
        self.map_y = (height/2+radii*np.sin(angles)).astype(np.float32)
        half = settings['period_degrees']//settings['angle_step']//2
        self.shifts = np.arange(-half,half)
        self.indices = (np.arange(len(angles))[None,:]-self.shifts[:,None]) % len(angles)
        cores, rings = [], []
        dx, dy = settings['template_offset']
        for image in (template, *alternatives):
            base = np.zeros((height,width,3),np.uint8)
            h,w = min(len(image),height-dy),min(image.shape[1],width-dx)
            base[dy:dy+h,dx:dx+w] = image[:h,:w]
            white = self._white(base)
            cores.append(white[self.core_mask])
            rings.append(self._polar(white))
        self.core_variants = np.stack(cores)
        self.ring_variants = np.stack(rings)
        if localize:
            radius = settings['center_search_radius']
            self.offsets = sorted(((dx,dy) for dy in range(-radius,radius+1)
                for dx in range(-radius,radius+1)),key=lambda p:(p[0]**2+p[1]**2,p))
            self.search_x = self.core_x[None,:]+np.array([p[0] for p in self.offsets])[:,None]
            self.search_y = self.core_y[None,:]+np.array([p[1] for p in self.offsets])[:,None]
        self.begin()

    def _white(self, image):
        p = image.astype(np.int16)
        return ((p.min(2)>self.settings['white_min'])
                & (np.ptp(p,axis=2)<self.settings['channel_spread_max'])).astype(np.float32)

    def _polar(self, white, offset=(0,0)):
        return cv2.remap(white,self.map_x+offset[0],self.map_y+offset[1],cv2.INTER_LINEAR)

    def _center_score(self, values):
        values = values[...,None,:]
        total = values.sum(axis=-1)+self.core_variants.sum(axis=-1)
        return (2*np.minimum(values,self.core_variants).sum(axis=-1)/np.maximum(total,1)).max(axis=-1)

    def _locate_center(self, white):
        scores = self._center_score(white[self.search_y,self.search_x])
        index = int(scores.argmax())
        return float(scores[index]), self.offsets[index]

    def _reference_match(self, ring):
        best = (0.,0.,0.)
        for reference in self.ring_variants:
            total = float(reference.sum()+ring.sum())
            if total == 0:
                continue
            shifted = reference[:,self.indices].transpose(1,0,2)
            scores = 2*np.minimum(shifted,ring).sum(axis=(1,2))/total
            index = int(scores.argmax())
            distance = abs(np.arange(len(scores))-index)
            distance = np.minimum(distance,len(scores)-distance)
            margin = float(scores[index]-scores[distance>1].max())
            if float(scores[index]) > best[1]:
                best = (float(self.shifts[index]*self.settings['angle_step']),float(scores[index]),margin)
        return best

    def begin(self, now=None):
        self.evidence.begin()
        self.started = now
        self.first_visible_ms = self.first_active_ms = None
        self.center_offset = None
        self.reference_score = self.reference_margin = None

    def reset(self):
        self.evidence.reset()
        self.center_offset = None
        self.reference_score = self.reference_margin = None

    def observe(self, image, now):
        if self.started is None:
            self.started = now
        self.reference_score = self.reference_margin = None
        if self.evidence.last_observed is not None and now <= self.evidence.last_observed:
            self.center_offset = None
            return self.evidence.observe(now, 0., None)
        white = self._white(image)
        if self.localize:
            score, offset = self._locate_center(white)
        else:
            score, offset = float(self._center_score(white[self.core_y,self.core_x])), (0,0)
        if score < self.settings['visible_score']:
            self.center_offset = None
            return self.evidence.observe(now,score,None)
        if self.first_visible_ms is None:
            self.first_visible_ms = round((now-self.started)*1000,3)
        if self.center_offset is not None and offset != self.center_offset:
            self.evidence.reset()  # Never combine OFF evidence from different centres.
        self.center_offset = offset
        angle, quality, margin = self._reference_match(self._polar(white,offset))
        self.reference_score, self.reference_margin = quality,margin
        reliable = quality >= self.settings['reference_score'] and margin >= self.settings['reference_margin']
        state = self.evidence.observe(now,score,angle,reliable=reliable)
        if state is AutoState.ROTATING and self.first_active_ms is None:
            self.first_active_ms = round((now-self.started)*1000,3)
        return state

    def summary(self):
        result = self.evidence.summary()
        result.update(method='static_reference_ring', localized=self.localize,
            center_offset=self.center_offset, reference_score=self.reference_score,
            reference_margin=self.reference_margin, first_visible_ms=self.first_visible_ms,
            first_active_ms=self.first_active_ms,
            visible_to_active_ms=round(self.first_active_ms-self.first_visible_ms,3) if self.first_active_ms is not None else None)
        result['criteria'].update({key:self.settings[key] for key in (
            'reference_score','reference_margin','core_radius')})
        if self.localize:
            result['criteria']['center_search_radius'] = self.settings['center_search_radius']
        return result
