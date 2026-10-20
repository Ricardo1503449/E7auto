"""Hero filtering, selection, recruitment and departure."""
from __future__ import annotations
from typing import Callable
from e7auto.core.types import Rect, ClientRect, ClientPoint
from e7auto.vision.frames import frame_view
from .configuration import ChaosSettings
from .contracts import ChaosVisionPort


class HeroRecruitment:
    def __init__(self, settings: ChaosSettings, vision: ChaosVisionPort, *,
                 frame: Callable, wait: Callable, tap: Callable, act: Callable,
                 control: Callable, first: Callable):
        self.settings, self.vision = settings, vision
        self.frame, self.wait, self.tap = frame, wait, tap
        self.act, self.control, self.first = act, control, first
        self._left_names = tuple(key for key in settings.scenes if key.startswith('selected_name_'))

    def filter(self, attribute, profession=False):
        # Existing callers use True for thief; restoration explicitly requests knight.
        profession = "thief" if profession is True else profession
        if profession not in (False, "thief", "knight"):
            raise ValueError("unsupported recruitment profession")
        self.tap(self.wait("open_filter", lambda f: self.first(f, ("filter_dark", "filter_light"))))
        # Only these historical attributes are cleared; do not introduce fire
        # or another profession as a new cleanup/dependency policy.
        desired = [(name, False, "filter_icon_"+name)
                   for name in ("dark", "water", "light") if name != attribute]
        desired.append((attribute, True, "filter_icon_"+attribute))
        if profession:
            desired.append((profession, True, "filter_icon_"+profession+"_filter"))
        pending = None

        def observe(frame):
            states = []
            for name, wanted, off_key in desired:
                selected = self.vision.control(frame, "filter_"+name+"_selected")
                hit = selected or self.vision.control(frame, off_key)
                if hit is None:
                    return None  # Missing pixels never prove a toggle is off.
                states.append((name, wanted, selected is not None, hit))
            if pending is not None and not any(
                    name == pending[0] and active == pending[1]
                    for name, _, active, _ in states):
                return None  # Wait for the last input; never toggle it again.
            evidence = tuple((name, active, hit) for name, _, active, hit in states)
            for name, wanted, active, hit in states:
                if active != wanted:
                    return evidence, hit, (name, wanted)
            button = self.first(frame, ("filter_light", "filter_dark"))
            return (evidence, button, None) if button is not None else None

        while True:
            _, button, change = self.wait("filter_state", observe)
            self.tap(button)
            if change is None:
                return
            pending = change


    def selection(self, frame, hero, target):
        """Use fresh detail-name evidence, or the border at the original click."""
        if target.key != 'hero_'+hero:
            raise ValueError('selection target does not match requested hero')
        left_key = 'selected_name_'+hero
        name = self.vision.control(frame, left_key) if left_key in self.settings.scenes else None
        # An explicitly recognized different name vetoes both branches, even
        # when the target name also matches. Do not load another lineup's names.
        for key in self._left_names:
            if key != left_key and self.vision.control(frame, key) is not None:
                return None
        if name is not None:
            return name
        dx, dy, w, h = self.settings.geometry["hero_selection_offset"]
        if isinstance(target.anchor, ClientPoint):
            t=frame_view(frame).transform
            local=ClientRect(round(target.bounds.x+dx*t.scale_x),round(target.bounds.y+dy*t.scale_y),round(w*t.scale_x),round(h*t.scale_y))
        else:
            local=Rect(target.bounds.x+dx,target.bounds.y+dy,w,h)
        border = self.vision.control(frame, "hero_selected_border", local)
        if border is None:
            return None
        if not (border.bounds.x <= target.anchor.x <= border.bounds.right and border.bounds.y <= target.anchor.y <= border.bounds.bottom):
            return None
        # Different Detection keys keep name and border stability separate.
        return border

    def _selected_button(self, frame, hero, target, key):
        selected = self.selection(frame, hero, target)
        if selected is None:
            return None
        button = self.vision.control(frame, key)
        return (selected, button) if button is not None else None


    def choose(self, hero, *, confirm_key):
        if self.settings.scenes[confirm_key].action is None:
            raise ValueError('scene is recognition-only: '+confirm_key)
        target = self.control("hero_"+hero)
        self.tap(target)
        # Name confirmation survives row reordering and clipped right edges;
        # border fallback stays bound to the pre-click row, never any gold card.
        _, button = self.wait(confirm_key, lambda f: self._selected_button(f, hero, target, confirm_key))
        return button


    def recruit(self, hero, *, result_key=None):
        # One fresh stable window covers both the selected identity and button.
        # Keep the last frame's button anchor; do not start another _act wait.
        button = self.choose(hero, confirm_key='recruit_confirm')
        self.tap(button)
        self.control(result_key or "recruited_"+hero)


    def depart(self, hero):
        def target(frame):
            if self.vision.control(frame, "depart_title") is not None:
                return self.vision.control(frame, "hero_"+hero)
            return None

        clicked = self.wait("departure_target", target)
        self.tap(clicked)

        def ready(frame):
            if self.vision.control(frame, "depart_title") is None:
                return None
            return self._selected_button(frame, hero, clicked, 'depart_confirm')

        _, button = self.wait("departure_confirm", ready)
        self.tap(button)  # Never replay the irreversible departure submission.
        self.control("depart_missing")
