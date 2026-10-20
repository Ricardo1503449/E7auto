from __future__ import annotations
from threading import RLock
import cv2
import numpy as np
from e7auto.core.types import ClientPoint, ClientRect, Rect
from e7auto.vision.frames import FrameView, frame_view
from e7auto.vision.candidates import CandidateCache, sampling_phases, ScoreData
from e7auto.core.observations import Observation, MatchGeometry
from e7auto.vision.baseline import BaselineMatcher


class _PreparedRegion:
    """One invocation's pixels; coarse proposals and native retries share them."""
    def __init__(self, view):
        self.view = view
        self.source = None
        self._half = None
        self._lock = RLock()
        if not view.raw.size or not np.any(cv2.meanStdDev(view.raw)[1]):
            return
        pad = 0 if view.transform.is_identity else 3
        source = np.ascontiguousarray(view.raw)
        if pad:
            source = np.pad(source, ((pad, pad), (pad, pad), (0, 0)))
        self.source = source.view()
        self.source.setflags(write=False)
        self.origin = (view.origin.x-pad, view.origin.y-pad)

    def half(self):
        with self._lock:
            if self._half is None:
                self._half = cv2.resize(self.source, None, fx=.5, fy=.5, interpolation=cv2.INTER_AREA)
                self._half.setflags(write=False)
            return self._half


class SearchPreparation:
    """Scoped to one frame/discovery, never retained by the matcher or its cache.

    Only the complete supplied view is shared across templates. Different local
    ROIs are prepared separately, avoiding an accumulating collection of pixels.
    """
    def __init__(self, frame):
        self._frame = frame
        self._bounds = frame_view(frame).bounds
        self._full = None
        self._lock = RLock()

    def region(self, frame, view):
        if frame is not self._frame:
            raise ValueError('search preparation belongs to another frame')
        if view.bounds != self._bounds:
            return _PreparedRegion(view)
        with self._lock:
            if self._full is None:
                self._full = _PreparedRegion(view)
            return self._full


class TemplateMatcher:
    """Match scaled immutable templates directly against captured client pixels."""
    def __init__(self, templates, cache=None):
        self._templates = templates
        self.cache = cache if cache is not None else CandidateCache()
        self.baseline = BaselineMatcher(templates, self.cache)

    @staticmethod
    def prepare_bgr(frame):
        return frame_view(frame)

    @staticmethod
    def crop(frame, roi):
        return np.ascontiguousarray(frame_view(frame).crop(roi).raw)

    @staticmethod
    def prepare_search(frame):
        return SearchPreparation(frame)

    def candidates(self, frame, key):
        view = frame_view(frame)
        self.cache.geometry(view.transform)
        data = self._templates.get(key)
        scale=(view.transform.scale_x,view.transform.scale_y)
        for phase in sampling_phases(scale):
            yield self.cache.get(data,scale,phase=phase)

    @staticmethod
    def scores(source, candidate, threshold, structure_threshold=None, *, force_color=False,prepared=None):
        template, mask = candidate.image,candidate.mask
        h,w = template.shape[:2]
        if source.shape[0]<h or source.shape[1]<w:
            return None
        correlation=getattr(candidate,"color_correlation",mask is None) and not force_color
        structured=getattr(candidate,'structured',None)
        if structured is None:
            pixels=template.reshape(-1,3) if mask is None else template[mask>0]
            structured=bool(pixels.size and np.any(np.ptp(pixels,axis=0)))
        if not structured:
            return None
        rows,columns=source.shape[0]-h+1,source.shape[1]-w+1
        if rows*columns<=49:
            return TemplateMatcher._small_scores(source,candidate,threshold,structure_threshold,correlation,prepared)
        color = (1-cv2.matchTemplate(source,template,cv2.TM_SQDIFF_NORMED,mask=mask)
                 if not correlation else cv2.matchTemplate(source,template,cv2.TM_CCOEFF_NORMED,mask=mask))
        accepted = np.isfinite(color)&(color>=threshold)
        structure = None
        if structure_threshold is not None:
            if not np.any(accepted):
                return None
            ys,xs=np.nonzero(accepted)
            x0,y0,x1,y1=int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max())
            local=cv2.matchTemplate(source[y0:y1+h,x0:x1+w],template,cv2.TM_CCOEFF_NORMED,mask=mask)
            structure=np.full(color.shape,-np.inf,np.float32)
            structure[y0:y1+1,x0:x1+1]=local
            accepted &= np.isfinite(structure)&(structure>=structure_threshold)
        if not np.any(accepted):
            return None
        return np.where(accepted,color,-np.inf),structure

    @staticmethod
    def _small_scores(source,candidate,threshold,structure_threshold,correlation,prepared=None):
        t,mask=candidate.image,candidate.mask
        h,w=t.shape[:2];rows,columns=source.shape[0]-h+1,source.shape[1]-w+1
        prepared=ScoreData(candidate) if prepared is None else prepared
        ys,xs=prepared.ys,prepared.xs
        weights,squared=prepared.weights,prepared.squared
        energy,weighted=prepared.energy,prepared.weighted
        centered,centered_sum,variance=prepared.centered,prepared.centered_sum,prepared.variance
        color=np.full((rows,columns),-np.inf,np.float32);structure=np.full_like(color,-np.inf)
        locations=[(x,y) for y in range(rows) for x in range(columns)]
        chunk=max(1,262144//max(1,len(ys)*3))
        for start in range(0,len(locations),chunk):
            positions=locations[start:start+chunk]
            values=np.stack([source[y+ys,x+xs] for x,y in positions]).astype(np.float64)
            observed_energy=np.einsum('n,pnc,pnc->p',squared,values,values)
            denominator=np.sqrt(energy*observed_energy)
            cross=np.einsum('nc,pnc->p',weighted,values)
            with np.errstate(invalid='ignore',divide='ignore'):
                scores=1-(observed_energy+energy-2*cross)/denominator
            accepted=np.isfinite(scores)&(scores>=threshold)
            if structure_threshold is not None or correlation:
                if not correlation and not np.any(accepted):continue
                means=np.einsum('n,pnc->pc',weights,values)/weights.sum()
                weighted_values=np.einsum('n,pnc->pc',squared,values)
                observed_variance=observed_energy-2*np.einsum('pc,pc->p',means,weighted_values)+(means*means).sum(axis=1)*squared.sum()
                numerator=np.einsum('nc,pnc->p',centered,values)-np.einsum('pc,c->p',means,centered_sum)
                with np.errstate(invalid='ignore',divide='ignore'):
                    structures=numerator/np.sqrt(variance*np.maximum(observed_variance,0))
                structures[observed_variance<=np.maximum(observed_energy,1)*1e-12]=-np.inf
                if correlation:scores=structures;accepted=np.isfinite(scores)&(scores>=threshold)
                if structure_threshold is not None:accepted&=np.isfinite(structures)&(structures>=structure_threshold)
            else:
                structures=np.full(len(positions),-np.inf)
            for i,(x,y) in enumerate(positions):
                if accepted[i]:color[y,x]=scores[i];structure[y,x]=structures[i]
        return (color,structure if structure_threshold is not None else None) if np.any(np.isfinite(color)) else None

    @staticmethod
    def observation(key,score,structure,x,y,candidate,region):
        width,height=candidate.content_size
        geometry=MatchGeometry(x+candidate.offset[0],y+candidate.offset[1],width,height,*candidate.scale)
        bounds=geometry.bounds
        if bounds.x<region.x or bounds.y<region.y or bounds.right>region.right or bounds.bottom>region.bottom:
            return None
        anchor=ClientPoint(x+candidate.size[0]//2,y+candidate.size[1]//2) if candidate.scale==(1.,1.) else geometry.point()
        return Observation(key,min(1.,float(score)),bounds,anchor,region,structure,
                           (*candidate.size,round(candidate.phase[0]*4),round(candidate.phase[1]*4)),geometry)

    def match(self,frame,template_key,roi,threshold):
        return self.match_bgr(frame,template_key,roi,threshold)

    def recheck(self,frame,evidence,threshold,structure_threshold):
        """Re-evaluate current pixels at a previous placement; never reuse scores."""
        view=frame_view(frame)
        scale=(view.transform.scale_x,view.transform.scale_y)
        g=evidence.geometry
        if g is None or evidence.candidate_id is None or (g.scale_x,g.scale_y)!=scale:return None
        if view.transform.is_identity:
            return self.baseline.match(view,evidence.object_id,evidence.bounds,threshold,
                                       structure_threshold,force_color=True)
        phase=tuple(v/4 for v in evidence.candidate_id[-2:])
        self.cache.geometry(view.transform)
        candidate=self.cache.get(self._templates.get(evidence.object_id),scale,phase=phase)
        if candidate.content_size!=(g.width,g.height):return None
        w,h=candidate.size
        bounds=ClientRect(round(g.x-candidate.offset[0]),round(g.y-candidate.offset[1]),w,h)
        patch=view.crop(bounds,clip=True)
        if patch.bounds==bounds:
            pixels=patch.raw
        else:
            pixels=np.zeros((h,w,3),np.uint8)
            ignored=np.ones((h,w),bool)
            x,y=patch.origin.x-bounds.x,patch.origin.y-bounds.y
            if patch.raw.size:
                pixels[y:y+patch.shape[0],x:x+patch.shape[1]]=patch.raw
                ignored[y:y+patch.shape[0],x:x+patch.shape[1]]=False
            if candidate.mask is None or np.any(candidate.mask[ignored]>0):return None
        scores=self.scores(pixels,candidate,threshold,structure_threshold,force_color=True,
                           prepared=self.cache.score_data(candidate))
        if scores is None:return None
        return self.observation(evidence.object_id,scores[0][0,0],float(scores[1][0,0]),
                                bounds.x,bounds.y,candidate,view.bounds)

    def match_entry(self,frame,template_key,roi,threshold,*,structure_threshold):
        return self.match_bgr(frame,template_key,roi,threshold,structure_threshold=structure_threshold)

    def match_bgr(self,bgr_frame,template_key,roi,threshold,*,structure_threshold=None):
        if frame_view(bgr_frame).transform.is_identity:
            return self.baseline.match(bgr_frame,template_key,roi,threshold,structure_threshold)
        hits=self._search(bgr_frame,template_key,roi,threshold,structure_threshold,limit=1,force_color=False)
        return hits[0] if hits else None

    def locate_all(self,frame,template_key,roi,threshold,structure_threshold,*,limit=12,cancelled=None,exhaustive=False,preparation=None):
        if frame_view(frame).transform.is_identity:
            return self.baseline.locate_all(frame,template_key,roi,threshold,structure_threshold,
                                           limit=limit,cancelled=cancelled)
        return self._search(frame,template_key,roi,threshold,structure_threshold,limit=limit,
                            force_color=True,cancelled=cancelled,exhaustive=exhaustive,preparation=preparation)

    def _proposals(self,source,data,scale,threshold,limit,force_color,*,native=False,prepared_region=None):
        # One broad native/coarse color query proposes locations. It cannot authorize
        # a detection; every proposal is verified on original pixels with both gates.
        factor=.5 if not native and source.shape[0]*source.shape[1]>120_000 and min(data.image.shape[:2])*min(scale)>26 else 1.
        candidate=self.cache.get(data,(scale[0]*factor,scale[1]*factor))
        small=(prepared_region.half() if prepared_region is not None else
               cv2.resize(source,None,fx=factor,fy=factor,interpolation=cv2.INTER_AREA)) if factor<1 else source
        w,h=candidate.size
        if h>small.shape[0] or w>small.shape[1]:
            return []
        correlation=candidate.color_correlation and not force_color
        scores=cv2.matchTemplate(small,candidate.image,cv2.TM_CCOEFF_NORMED if correlation else cv2.TM_SQDIFF_NORMED,mask=candidate.mask)
        scores=scores if correlation else 1-scores
        scores=np.nan_to_num(scores,nan=-np.inf,posinf=-np.inf,neginf=-np.inf)
        locations=[]
        for _ in range(max(12,limit*3)):
            _,score,_,(x,y)=cv2.minMaxLoc(scores)
            if not np.isfinite(score) or score<threshold-.20:
                break
            # Return an estimate of the content origin, independent of canvas pads.
            locations.append(((x+candidate.offset[0])/factor,(y+candidate.offset[1])/factor))
            radius=max(3,round(min(candidate.content_size)*.3))
            scores[max(0,y-radius):y+radius+1,max(0,x-radius):x+radius+1]=-np.inf
        return locations

    def _search(self,frame,key,roi,threshold,structure_threshold,*,limit,force_color,cancelled=None,exhaustive=False,native_proposals=False,preparation=None,prepared_region=None):
        view=frame_view(frame).crop(roi)
        data=self._templates.get(key)
        if prepared_region is None:
            scope=self.prepare_search(frame) if preparation is None else preparation
            prepared_region=scope.region(frame,view)
        if prepared_region.source is None:
            return ()
        scale=(view.transform.scale_x,view.transform.scale_y)
        self.cache.geometry(view.transform)
        source,origin=prepared_region.source,prepared_region.origin
        # Small regions already are local verification windows; do not add a
        # proposal gate. Exhaustive is an offline same-algorithm search oracle.
        first=self.cache.get(data,scale)
        local=(source.shape[0]-first.size[1]+1)*(source.shape[1]-first.size[0]+1)<=1024
        proposals=None if exhaustive or local or (scale==(1.,1.) and (source.shape[0]*source.shape[1]<=120_000 or min(data.image.shape[:2])<=26)) else self._proposals(source,data,scale,threshold,limit,force_color,native=native_proposals,prepared_region=prepared_region)
        content_width,content_height=first.content_size
        hits=[]
        def covered(point):
            cx,cy=point[0]+origin[0]+content_width/2,point[1]+origin[1]+content_height/2
            return any(abs(cx-hit.anchor.x)+2<min(content_width,hit.bounds.width)*.5 and
                       abs(cy-hit.anchor.y)+2<min(content_height,hit.bounds.height)*.5 for hit in hits)
        for candidate in self.candidates(frame,key):
            if cancelled is not None and cancelled.is_set():return ()
            w,h=candidate.size
            if h>source.shape[0] or w>source.shape[1]:continue
            regions=[(0,0,source)] if proposals is None else []
            if proposals is not None:
                for cx,cy in proposals:
                    if limit>1 and covered((cx,cy)):continue
                    x,y=max(0,round(cx-candidate.offset[0])-1),max(0,round(cy-candidate.offset[1])-1)
                    right,bottom=min(source.shape[1],x+w+2),min(source.shape[0],y+h+2)
                    regions.append((x,y,source[y:bottom,x:right]))
            prepared=None
            for ox,oy,area in regions:
                if area.shape[0]>=h and area.shape[1]>=w and (area.shape[0]-h+1)*(area.shape[1]-w+1)<=49 and prepared is None:
                    prepared=self.cache.score_data(candidate)
                values=self.scores(area,candidate,threshold,structure_threshold,force_color=force_color,prepared=prepared)
                if values is None:continue
                color,structure=values
                for _ in range(max(5,limit*3)):
                    _,score,_,(x,y)=cv2.minMaxLoc(color)
                    if not np.isfinite(score):break
                    hit=self.observation(key,score,None if structure is None else float(structure[y,x]),
                                         origin[0]+ox+x,origin[1]+oy+y,candidate,view.bounds)
                    if hit is not None:
                        if not any(abs(hit.anchor.x-old.anchor.x)<min(hit.bounds.width,old.bounds.width)*.5 and abs(hit.anchor.y-old.anchor.y)<min(hit.bounds.height,old.bounds.height)*.5 for old in hits):
                            hits.append(hit)
                    color[max(0,y-h//2):y+h//2+1,max(0,x-w//2):x+w//2+1]=-np.inf
            del prepared
            if limit==1 and hits:return (max(hits,key=lambda hit:hit.confidence),)
            if proposals is not None and all(covered(point) for point in proposals):break
        if (not hits and not exhaustive and not native_proposals and proposals is not None
                and source.shape[0]*source.shape[1]>120_000 and min(data.image.shape[:2])*min(scale)>26
                and (cancelled is None or not cancelled.is_set())):
            # A half-size proposal can hide small glyphs. Retry proposal generation
            # once at native scale, retaining the same candidates and final gates.
            return self._search(frame,key,roi,threshold,structure_threshold,limit=limit,force_color=force_color,
                                cancelled=cancelled,native_proposals=True,prepared_region=prepared_region)
        hits.sort(key=lambda hit:(-hit.confidence,hit.bounds.y,hit.bounds.x,hit.candidate_id))
        distinct=[]
        for hit in hits:
            if not any(abs(hit.anchor.x-other.anchor.x)<min(hit.bounds.width,other.bounds.width)*.5
                       and abs(hit.anchor.y-other.anchor.y)<min(hit.bounds.height,other.bounds.height)*.5 for other in distinct):
                distinct.append(hit)
        return tuple(distinct[:limit])
