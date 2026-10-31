"""Array-only Chaos recognition, dynamic associations and auto-motion evidence."""
from __future__ import annotations

from math import ceil, floor
import cv2
import numpy as np

from e7auto.core.types import Point, Rect, ClientPoint, ClientRect
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.matching import TemplateMatcher
from e7auto.vision.candidates import CandidateCache
from e7auto.vision.components import ComponentMatcher
from e7auto.vision.navigation import StartupNavigationVision
from e7auto.vision.frames import frame_view
from .configuration import ChaosConfig
from .contracts import AutoState, Detection, MapView, Node
from .text_triangle import bright_triangle
from .loot import LootRecognizer
from .event_recognition import EventRecognizer
from .auto_motion import AutoIconState


NODE_KINDS = ("normal", "elite", "boss", "event", "loot", "rest", "shop")


class ChaosVision:
    def __init__(self, config: ChaosConfig, templates: TemplateRepository):
        self.settings = config.chaos
        self._templates = templates
        self._matcher = TemplateMatcher(templates, CandidateCache(config.candidate_cache_mib*1024*1024))
        self._components = ComponentMatcher(templates, self._matcher, config.component_layouts)
        self._navigation = StartupNavigationVision(config, self._matcher)
        node_shapes = [templates.get(self.settings.scenes["node_"+kind].template_key).image.shape
                       for kind in NODE_KINDS]
        self._node_search_size = max(s[1] for s in node_shapes), max(s[0] for s in node_shapes)
        self._loot = LootRecognizer(self.settings, templates, self._matcher)
        self._events = EventRecognizer(self.settings, templates, self._matcher)
        p = self.settings.auto_motion
        self._motion_roi = Rect(*p["roi"])
        glyph = templates.get(self.settings.scenes["auto_static"].template_key).image
        self._motion = AutoIconState(p, glyph)
        self._motion_geometry = None
        self._motion_bounds = self._motion_roi

    def prepare_frame(self, frame: object) -> object:
        return self._matcher.prepare_bgr(frame)

    def navigation_menu(self, frame):
        return self._navigation.navigation_menu(frame)

    def navigation_tavern(self, frame):
        return self._navigation.navigation_tavern(frame)

    def rank_result(self, frame: object) -> tuple[int, int] | None:
        """Read one rank per configured result column, without an arrow template."""
        values = []
        for side in ("before", "after"):
            region = self.settings.regions["rank_result_" + side]
            hits = [rank for rank in range(1, 6)
                    if self.control(frame, f"rank_value_{rank}", region) is not None]
            if len(hits) != 1:
                return None
            values.append(hits[0])
        before, after = values
        return (before, after) if before < after else None

    def controls(self, frame: object, key: str, region: Rect | None = None) -> tuple[Detection, ...]:
        return self._locate(frame, key, region, limit=12)

    def stage_highlights(self, frame: object, entry: Detection) -> tuple[Detection, ...]:
        scene = self.settings.scenes["stage_selected"]
        view = frame_view(frame)
        region, bounds = view.region(scene.region, clip=True), entry.bounds
        height, width = self._templates.get(scene.template_key).image.shape[:2]
        extra=0 if view.transform.is_identity else 1
        width, height = ceil(width*view.transform.scale_x)+extra, ceil(height*view.transform.scale_y)+extra
        if (bounds.width > width or bounds.height > height
                or bounds.x < region.x or bounds.right > region.right
                or bounds.y < region.y or bounds.bottom > region.bottom):
            return ()
        # A containing border can start between entry.bottom-height and entry.y.
        # Include the full extent of every such border, clipped to the configured ROI.
        top = max(region.y, bounds.bottom-height)
        bottom = min(region.bottom, bounds.y+height)
        if bottom-top < height:
            return ()
        band = ClientRect(region.x, top, region.width, bottom-top)
        return self._locate(frame, "stage_selected", band, limit=12)

    def _locate(self, frame: object, key: str, region: Rect | None, *, limit: int) -> tuple[Detection, ...]:
        view = frame_view(frame)
        scene = self.settings.scenes[key]
        allowed = view.region(scene.region, clip=True)
        region = allowed if region is None else self._intersection(view.region(region, clip=True), allowed)
        if region is None:
            return ()
        matcher = (self._components if scene.template_key in self._components.layouts
                   else self._matcher)
        observations = matcher.locate_all(view, scene.template_key, region, scene.threshold, scene.structure, limit=limit)
        action = scene.action or (.5,.5)
        dx,dy = scene.action_offset
        sx,sy = view.transform.scale_x,view.transform.scale_y
        if view.transform.is_identity:
            return tuple(Detection(key,o.confidence,o.bounds,
                         ClientPoint(round(o.bounds.x+o.bounds.width*action[0])+dx,
                                     round(o.bounds.y+o.bounds.height*action[1])+dy)) for o in observations)
        return tuple(Detection(key,o.confidence,o.bounds,
                     o.geometry.point(action,(dx,dy)) if o.geometry is not None else
                     ClientPoint(round(o.bounds.x+o.bounds.width*action[0]+dx*sx),
                                 round(o.bounds.y+o.bounds.height*action[1]+dy*sy))) for o in observations)

    def control(self, frame: object, key: str, region: Rect | None = None) -> Detection | None:
        found = self._locate(frame, key, region, limit=1)
        if found:
            return found[0]
        if key != "text_continue":
            return None
        scene = self.settings.scenes[key]
        view = frame_view(frame)
        roi = view.region(scene.region, clip=True) if region is None else self._intersection(
            view.region(region, clip=True), view.region(scene.region, clip=True))
        if roi is None:
            return None
        source = self._matcher.crop(self._matcher.prepare_bgr(frame), roi)
        template = self._templates.get(scene.template_key).image
        sx, sy = view.transform.scale_x, view.transform.scale_y
        settings = dict(self.settings.text_triangle)
        settings["edge_distance_px"] = max(1,round(settings["edge_distance_px"]*min(sx,sy)))
        hit = bright_triangle(source, (max(1,round(template.shape[1]*sx)),max(1,round(template.shape[0]*sy))), settings)
        if hit is None:
            return None
        local, score = hit
        bounds = ClientRect(roi.x+local.x, roi.y+local.y, local.width, local.height)
        return Detection(key, score, bounds, ClientPoint(bounds.x+bounds.width//2, bounds.y+bounds.height//2))

    @staticmethod
    def _intersection(a: Rect, b: Rect) -> Rect | None:
        x, y, right, bottom = max(a.x, b.x), max(a.y, b.y), min(a.right, b.right), min(a.bottom, b.bottom)
        return ClientRect(x, y, right-x, bottom-y) if right > x and bottom > y else None

    def event_card_snapshot(self, frame: object) -> object:
        """Observe static card information, excluding animated hero artwork."""
        roi = self.settings.regions["event_card_change"]
        return self._matcher.crop(self._matcher.prepare_bgr(frame), roi).copy()

    def event_card_changed(self, frame: object, previous: object) -> bool:
        current = self.event_card_snapshot(frame)
        if not isinstance(previous, np.ndarray) or previous.shape != current.shape:
            return False
        difference = np.mean(np.abs(current.astype(np.int16)-previous.astype(np.int16)))
        return bool(difference >= self.settings.geometry["event_card_change_min"])

    def event_card_stable(self, frame: object, previous: object) -> bool:
        current = self.event_card_snapshot(frame)
        if not isinstance(previous, np.ndarray) or previous.shape != current.shape:
            return False
        difference = np.mean(np.abs(current.astype(np.int16)-previous.astype(np.int16)))
        return bool(difference <= self.settings.geometry["event_card_stable_max"])

    def map_view(self, frame: object) -> MapView | None:
        if self.control(frame, "map_hero") is None:
            return None
        floors = [i for i, key in enumerate(("map_title_outer", "map_title_center", "map_title_deep"), 1) if self.control(frame, key)]
        if len(floors) != 1:
            return None
        triangles = self.controls(frame, "node_triangle")
        if not triangles:
            return None
        view = frame_view(frame)
        sx,sy = view.transform.scale_x,view.transform.scale_y
        map_region = view.region(self.settings.regions["map"], clip=True)
        candidates = []
        for triangle in triangles:
            region = self._intersection(self._node_region(triangle.bounds, sx, sy), map_region)
            if region is None:
                return None
            for kind in NODE_KINDS:
                for hit in self._locate(view, "node_"+kind, region, limit=12):
                    # Overlapping triangle windows can rediscover the same object.
                    # Use the matcher's spatial duplicate rule, not score equality.
                    if not any(n.kind == kind and self._same_node(n.detection, hit) for n in candidates):
                        candidates.append(Node(kind, hit))
        available = []
        p = self.settings.geometry
        for triangle in triangles:
            tx = triangle.bounds.x + triangle.bounds.width/2
            ty = triangle.bounds.y + triangle.bounds.height/2
            matches = [n for n in candidates if abs(n.detection.bounds.x+n.detection.bounds.width/2-tx) <= p["node_triangle_max_dx"]*sx
                       and 0 < n.detection.bounds.y+n.detection.bounds.height/2-ty <= p["node_triangle_max_dy"]*sy+n.detection.bounds.height/2]
            if len(matches) != 1:
                return None
            if matches[0] not in available:
                available.append(matches[0])
        return MapView(floors[0], tuple(available))

    def _node_region(self, triangle: Rect, sx=1., sy=1.) -> Rect:
        """Envelope of every full template allowed by the existing pairing bounds."""
        tx, ty = triangle.x+triangle.width/2, triangle.y+triangle.height/2
        extra=0 if (sx,sy)==(1.,1.) else 1
        width, height = (ceil(self._node_search_size[0]*sx)+extra,ceil(self._node_search_size[1]*sy)+extra)
        p = self.settings.geometry
        # Pairing requires |cx-tx|<=dx and 0<cy-ty<=dy+node_height/2.
        # Include the entire largest node, even at those limits or mid-bounce.
        left = floor(tx-p["node_triangle_max_dx"]*sx-width/2)
        top = floor(ty-height/2)
        right = ceil(tx+p["node_triangle_max_dx"]*sx+width/2)
        bottom = ceil(ty+p["node_triangle_max_dy"]*sy+height)
        return ClientRect(left, top, right-left, bottom-top)

    @staticmethod
    def _same_node(a: Detection, b: Detection) -> bool:
        ax, ay = a.bounds.x+a.bounds.width/2, a.bounds.y+a.bounds.height/2
        bx, by = b.bounds.x+b.bounds.width/2, b.bounds.y+b.bounds.height/2
        return abs(ax-bx) < b.bounds.width*.5 and abs(ay-by) < b.bounds.height*.5

    def loot_cards(self, frame: object) -> tuple[Detection, ...] | None:
        return self._loot.recognize(frame)

    def begin_loot_recognition(self) -> None:
        self._loot.begin()

    def end_loot_recognition(self) -> dict:
        return self._loot.end()

    def invalidate_loot_recognition(self) -> None:
        self._loot.invalidate()

    def close_loot_recognition(self) -> None:
        self._loot.close()

    def reset_temporal(self) -> None:
        self._motion.reset()
        self._loot.invalidate()
        self._events.invalidate()

    def begin_event_recognition(self) -> None:
        self._events.begin()

    def end_event_recognition(self) -> dict:
        return self._events.end()

    def invalidate_event_recognition(self) -> None:
        self._events.invalidate()

    def close_event_recognition(self) -> None:
        self._events.close()

    def event_choice(self, frame: object) -> Detection | None:
        return self._events.recognize(frame)

    def auto_state(self, frame: object, now: float) -> AutoState:
        view = frame_view(frame)
        roi = view.region(self._motion_roi)
        geometry = (view.transform,roi)
        if view.transform.is_identity:
            if geometry != self._motion_geometry:
                # Baseline uses the same reference rule at its fixed centre.
                # Scaled phase generation and localization remain non-reference only.
                glyph=self._templates.get(self.settings.scenes['auto_static'].template_key).image
                started=self._motion.started
                self._motion=AutoIconState(self.settings.auto_motion,glyph)
                self._motion.begin(started)
                self._motion_geometry,self._motion_bounds=geometry,roi
            return self._motion.observe(self._matcher.crop(view,roi),now)
        if geometry != self._motion_geometry:
            p = dict(self.settings.auto_motion)
            sx,sy = view.transform.scale_x,view.transform.scale_y
            p['roi'] = (roi.x,roi.y,roi.width,roi.height)
            pad=0 if view.transform.is_identity else 1
            p['template_offset'] = (round(p['template_offset'][0]*sx)-pad,round(p['template_offset'][1]*sy)-pad)
            p['core_radius'] *= min(sx,sy)
            p['center_search_radius'] = max(1,ceil(p['center_search_radius']*max(sx,sy)))
            p['ring_radii'] = [r*min(sx,sy) for r in p['ring_radii']]
            key = self.settings.scenes['auto_static'].template_key
            candidates = tuple(self._matcher.candidates(view,key))
            started = self._motion.started
            reserve=(2*p['center_search_radius']+1)**2*(ceil(2*p['core_radius'])+1)**2*32+roi.width*roi.height*80
            self._motion = self._matcher.cache.preprocessing(
                lambda:AutoIconState(p,candidates[0].image,localize=True,alternatives=tuple(c.image for c in candidates[1:])),reserve)
            self._motion.begin(started)
            self._motion_geometry,self._motion_bounds = geometry,roi
        return self._motion.observe(self._matcher.crop(view,roi),now)

    def begin_auto_recognition(self, now: float) -> None:
        self._motion.begin(now)

    def auto_anchor(self) -> Point | None:
        offset = self._motion.center_offset
        if offset is None:
            return None
        r = self._motion_bounds
        return ClientPoint(r.x+r.width//2+offset[0], r.y+r.height//2+offset[1])

    def auto_recognition_summary(self) -> dict:
        return self._motion.summary()

    def network_connection_error(self, frame):
        return self.control(frame, "network_connection_abnormal")

    def network_retry(self, frame):
        return self.control(frame, "network_retry")

    def clear_template_cache(self):
        glyph=self._templates.get(self.settings.scenes['auto_static'].template_key).image
        self._motion=AutoIconState(self.settings.auto_motion,glyph)
        self._motion_geometry=None
        self._motion_bounds=self._motion_roi
        self._matcher.cache.clear()
