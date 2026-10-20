"""Same-frame component matching on one parent grid at native or scaled size."""
from math import ceil, floor

import cv2
import numpy as np

from e7auto.core.types import ClientRect
from e7auto.vision.candidates import sampling_phases
from e7auto.vision.frames import frame_view


class ComponentMatcher:
    def __init__(self, templates, matcher, layouts):
        self.templates = templates
        self.matcher = matcher
        self.layouts = layouts
        for key, layout in layouts.items():
            data = templates.get(key)
            support = np.ones(data.image.shape[:2], bool) if data.mask is None else data.mask > 0
            covered = np.zeros(support.shape, bool)
            for rect in (layout.primary, layout.secondary):
                if (rect.x < 0 or rect.y < 0 or rect.right > support.shape[1]
                        or rect.bottom > support.shape[0]
                        or not np.any(support[rect.y:rect.bottom, rect.x:rect.right])):
                    raise ValueError(f'empty or invalid component support: {key}')
                if np.any(covered[rect.y:rect.bottom, rect.x:rect.right]):
                    raise ValueError(f'overlapping components: {key}')
                covered[rect.y:rect.bottom, rect.x:rect.right] = True
            if np.any(support & ~covered):
                raise ValueError(f'component layout omits template evidence: {key}')

    @staticmethod
    def _offset(parent, part, rect):
        return (round(parent.offset[0]+rect.x*parent.scale[0]-part.offset[0]),
                round(parent.offset[1]+rect.y*parent.scale[1]-part.offset[1]))

    @staticmethod
    def _support(part):
        if part.mask is None:
            return (0, 0, *part.size)
        ys, xs = np.nonzero(part.mask > 0)
        return (int(xs.min()), int(ys.min()), int(xs.max())+1, int(ys.max())+1)

    @staticmethod
    def _complete(support, x, y, region):
        left, top, right, bottom = support
        return (x+left >= region.x and y+top >= region.y
                and x+right <= region.right and y+bottom <= region.bottom)

    def _proposals(self, prepared, data, rect, scale, threshold, limit, force_color, *, native):
        factor = .5 if (not native and prepared.source.shape[0]*prepared.source.shape[1] > 120_000
                       and min(rect.width*scale[0], rect.height*scale[1]) > 26) else 1.
        parent = self.matcher.cache.get(data, (scale[0]*factor, scale[1]*factor))
        part = self.matcher.cache.part(parent, rect)
        source = prepared.half() if factor < 1 else prepared.source
        w, h = part.size
        if h > source.shape[0] or w > source.shape[1]:
            return [], factor
        correlation = part.color_correlation and not force_color
        values = cv2.matchTemplate(source, part.image,
                                  cv2.TM_CCOEFF_NORMED if correlation else cv2.TM_SQDIFF_NORMED,
                                  mask=part.mask)
        values = values if correlation else 1-values
        values = np.nan_to_num(values, nan=-np.inf, posinf=-np.inf, neginf=-np.inf)
        proposals = []
        for _ in range(max(12, limit*3)):
            _, score, _, (x, y) = cv2.minMaxLoc(values)
            if not np.isfinite(score) or score < threshold-.20:
                break
            proposals.append(((x+part.offset[0])/factor, (y+part.offset[1])/factor))
            radius = max(3, round(min(part.content_size)*.3))
            values[max(0,y-radius):y+radius+1, max(0,x-radius):x+radius+1] = -np.inf
        return proposals, factor

    def _secondary(self, source, part, predicted, tolerance, origin, region, threshold, structure, force_color):
        px, py = predicted
        tx, ty = tolerance
        w, h = part.size
        left, top = max(0, floor(px-tx)), max(0, floor(py-ty))
        right = min(source.shape[1], ceil(px+tx)+w)
        bottom = min(source.shape[0], ceil(py+ty)+h)
        if right-left < w or bottom-top < h:
            return None
        patch = source[top:bottom, left:right]
        support = self._support(part)
        # A constant search window cannot pass a positive structure gate. Avoid
        # including ignored padding (or the nearby primary text) in this proof.
        supported_window = patch[support[1]:patch.shape[0]-h+support[3],
                                 support[0]:patch.shape[1]-w+support[2]]
        if structure is not None and not np.any(cv2.meanStdDev(supported_window)[1]):
            return None
        small = (patch.shape[0]-h+1)*(patch.shape[1]-w+1) <= 49
        scores = self.matcher.scores(patch, part, threshold, structure, force_color=force_color,
                                     prepared=self.matcher.cache.score_data(part) if small else None)
        if scores is None:
            return None
        color, shapes = scores
        ys, xs = np.indices(color.shape)
        valid = (np.isfinite(color) & (np.abs(left+xs-px) <= tx+1e-9)
                 & (np.abs(top+ys-py) <= ty+1e-9)
                 & (left+xs+origin[0]+support[0] >= region.x)
                 & (top+ys+origin[1]+support[1] >= region.y)
                 & (left+xs+origin[0]+support[2] <= region.right)
                 & (top+ys+origin[1]+support[3] <= region.bottom))
        # Adjacent accepted pixels describe one placement plateau. Two disconnected
        # placements are ambiguous and may not borrow the other item's component.
        count, _ = cv2.connectedComponents(valid.astype(np.uint8), connectivity=8)
        if count != 2:
            return None
        color[~valid] = -np.inf
        _, score, _, (x, y) = cv2.minMaxLoc(color)
        return score, None if shapes is None else float(shapes[y, x])

    @staticmethod
    def _distinct(hit, hits):
        return not any(abs(hit.anchor.x-old.anchor.x) < min(hit.bounds.width, old.bounds.width)*.5
                       and abs(hit.anchor.y-old.anchor.y) < min(hit.bounds.height, old.bounds.height)*.5
                       for old in hits)

    def locate_all(self, frame, key, roi, threshold, structure=None, *, limit=12, force_color=True):
        view = frame_view(frame)
        layout = self.layouts[key]
        search = view.crop(roi)
        prepared = self.matcher.prepare_search(frame).region(frame, search)
        if prepared.source is None:
            return ()
        self.matcher.cache.geometry(view.transform)
        data = self.templates.get(key)
        scale = view.transform.scale_x, view.transform.scale_y
        parent = self.matcher.cache.get(data, scale)
        primary = self.matcher.cache.part(parent, layout.primary)
        local = ((prepared.source.shape[0]-primary.size[1]+1)
                 * (prepared.source.shape[1]-primary.size[0]+1) <= 1024)
        proposals, factor = (None, 1.) if local else self._proposals(
            prepared, data, layout.primary, scale, threshold, limit, force_color, native=False)
        hits = self._search(prepared, search.bounds, data, key, layout, scale, proposals,
                            threshold, structure, limit, force_color)
        if not hits and proposals is not None and factor < 1:
            proposals, _ = self._proposals(prepared, data, layout.primary, scale,
                                           threshold, limit, force_color, native=True)
            hits = self._search(prepared, search.bounds, data, key, layout, scale, proposals,
                                threshold, structure, limit, force_color)
        return hits

    def _search(self, prepared, region, data, key, layout, scale, proposals,
                threshold, structure, limit, force_color):
        source, origin = prepared.source, prepared.origin
        hits = []
        tolerance = layout.tolerance[0]*scale[0], layout.tolerance[1]*scale[1]
        for phase in sampling_phases(scale):
            parent = self.matcher.cache.get(data, scale, phase=phase)
            primary = self.matcher.cache.part(parent, layout.primary)
            secondary = self.matcher.cache.part(parent, layout.secondary)
            po = self._offset(parent, primary, layout.primary)
            so = self._offset(parent, secondary, layout.secondary)
            primary_support = self._support(primary)
            w, h = primary.size
            if source.shape[0] < h or source.shape[1] < w:
                continue
            areas = [(0, 0, source)] if proposals is None else []
            for cx, cy in proposals or ():
                x = max(0, round(cx-primary.offset[0])-1)
                y = max(0, round(cy-primary.offset[1])-1)
                areas.append((x, y, source[y:min(source.shape[0],y+h+2), x:min(source.shape[1],x+w+2)]))
            for ox, oy, area in areas:
                if area.shape[0] < h or area.shape[1] < w:
                    continue
                small = (area.shape[0]-h+1)*(area.shape[1]-w+1) <= 49
                values = self.matcher.scores(area, primary, threshold, structure, force_color=force_color,
                    prepared=self.matcher.cache.score_data(primary) if small else None)
                if values is None:
                    continue
                color, shapes = values
                for _ in range(max(5, limit*3)):
                    _, score, _, (x, y) = cv2.minMaxLoc(color)
                    if not np.isfinite(score):
                        break
                    px, py = ox+x, oy+y
                    shape_score = None if shapes is None else float(shapes[y, x])
                    hit = self.matcher.observation(key, score, shape_score,
                        origin[0]+px-po[0], origin[1]+py-po[1], parent, region)
                    if (hit is not None and self._distinct(hit, hits)
                            and self._complete(primary_support, origin[0]+px, origin[1]+py, region)):
                        other = self._secondary(source, secondary, (px-po[0]+so[0], py-po[1]+so[1]),
                            tolerance, origin, region, threshold, structure, force_color)
                        if other is not None:
                            hit = self.matcher.observation(key, min(score, other[0]),
                                None if shapes is None else min(shape_score, other[1]),
                                origin[0]+px-po[0], origin[1]+py-po[1], parent, region)
                            hits.append(hit)
                    color[max(0,y-h//2):y+h//2+1, max(0,x-w//2):x+w//2+1] = -np.inf
            if limit == 1 and hits:
                return (max(hits, key=lambda hit: hit.confidence),)
        hits.sort(key=lambda hit: (-hit.confidence, hit.bounds.y, hit.bounds.x, hit.candidate_id))
        return tuple(hits[:limit])
