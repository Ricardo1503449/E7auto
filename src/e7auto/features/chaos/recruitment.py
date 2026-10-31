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
                 control: Callable, first: Callable, transition: Callable | None = None):
        self.settings, self.vision = settings, vision
        self.frame, self.wait, self.tap = frame, wait, tap
        self.act, self.control, self.first = act, control, first
        self.transition = transition
        self._left_names = tuple(key for key in settings.scenes if key.startswith('selected_name_'))
        self._hero_keys = tuple(key for key in settings.scenes if key.startswith('hero_') and key != 'hero_selected_border')

    def filter(self, attribute, profession=False):
        # Existing callers use True for thief; restoration explicitly requests knight.
        profession = "thief" if profession is True else profession
        if profession not in (False, "thief", "knight"):
            raise ValueError("unsupported recruitment profession")
        # Only these historical attributes are cleared; do not introduce fire
        # or another profession as a new cleanup/dependency policy.
        desired = [(name, False, "filter_icon_"+name)
                   for name in ("dark", "water", "light") if name != attribute]
        desired.append((attribute, True, "filter_icon_"+attribute))
        if profession:
            desired.append((profession, True, "filter_icon_"+profession+"_filter"))
        def read_states(frame):
            states = []
            for name, wanted, off_key in desired:
                selected = self.vision.control(frame, "filter_"+name+"_selected")
                hit = selected or self.vision.control(frame, off_key)
                if hit is None:
                    return None  # Missing pixels never prove a toggle is off.
                states.append((name, wanted, selected is not None, hit))
            return states

        def any_filter(frame):
            return any(self.vision.control(frame, "filter_"+name+"_selected") is not None
                       or self.vision.control(frame, off_key) is not None for name, _, off_key in desired)

        def decision(frame, states):
            if states is None:
                return None
            evidence = tuple((name, active, hit) for name, _, active, hit in states)
            for name, wanted, active, hit in states:
                if active != wanted:
                    return evidence, hit, (name, wanted)
            button = self.first(frame, ("filter_light", "filter_dark"))
            return (evidence, button, None) if button is not None else None

        def observe(frame):
            return decision(frame, read_states(frame))

        opening = self.wait("open_filter", lambda f: self.first(f, ("filter_dark", "filter_light")))
        state = self.transition(opening, "filter_state", observe, responded=any_filter)
        while True:
            _, button, change = state
            if change is None:
                def closed(frame):
                    if any_filter(frame):
                        return None
                    button = self.first(frame, ("filter_light", "filter_dark"))
                    hero = self.first(frame, self._hero_keys)
                    # Button colour alone does not identify an open/closed panel.
                    return (button, hero) if button is not None and hero is not None else None
                self.transition(button, "filter_closed", closed,
                                source=lambda f: self.first(f, ("filter_light", "filter_dark"))
                                if read_states(f) is not None else None)
                return
            name, wanted = change
            off_key = next(key for item, _, key in desired if item == name)
            responded = False
            def next_state(frame):
                nonlocal responded
                states = read_states(frame)
                if states is None:
                    on = self.vision.control(frame, "filter_"+name+"_selected")
                    off = None if on is not None else self.vision.control(frame, off_key)
                    responded = (on is not None) if wanted else (off is not None)
                    return None
                responded = any(item == name and active == wanted for item, _, active, _ in states)
                return decision(frame, states) if responded else None
            def source(frame):
                states = read_states(frame)
                if states is None:
                    return None
                return next((hit for item, _, active, hit in states if item == name and active != wanted), None)
            state = self.transition(button, "filter_state", next_state,
                                    source=source, responded=lambda _: responded)


    def selection(self, frame, hero, target, *, on_response=None):
        """Use fresh detail-name evidence, or the border at the original click."""
        if target.key != 'hero_'+hero:
            raise ValueError('selection target does not match requested hero')
        left_key = 'selected_name_'+hero
        name = self.vision.control(frame, left_key) if left_key in self.settings.scenes else None
        if name is not None and on_response is not None:
            on_response()
        # An explicitly recognized different name vetoes both branches, even
        # when the target name also matches. Do not load another lineup's names.
        for key in self._left_names:
            if key != left_key and self.vision.control(frame, key) is not None:
                if on_response is not None:
                    on_response()
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
        if on_response is not None:
            on_response()
        if not (border.bounds.x <= target.anchor.x <= border.bounds.right and border.bounds.y <= target.anchor.y <= border.bounds.bottom):
            return None
        # Different Detection keys keep name and border stability separate.
        return border

    def _selected_button(self, frame, hero, target, key, *, on_response=None):
        selected = self.selection(frame, hero, target, on_response=on_response)
        if selected is None:
            return None
        button = self.vision.control(frame, key)
        return (selected, button) if button is not None else None


    def choose(self, hero, *, confirm_key):
        if self.settings.scenes[confirm_key].action is None:
            raise ValueError('scene is recognition-only: '+confirm_key)
        target = self.control("hero_"+hero)
        # Name confirmation survives row reordering and clipped right edges;
        # border fallback stays bound to the pre-click row, never any gold card.
        seen = False
        def response():
            nonlocal seen
            seen = True
        _, button = self.transition(target, confirm_key,
                                   lambda f: self._selected_button(f, hero, target, confirm_key, on_response=response),
                                   responded=lambda _: seen)
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

        seen = False
        def response():
            nonlocal seen
            seen = True
        def ready(frame):
            if self.vision.control(frame, "depart_title") is None:
                return None
            return self._selected_button(frame, hero, clicked, 'depart_confirm', on_response=response)

        _, button = self.transition(clicked, "departure_confirm", ready, source=target,
                                   responded=lambda _: seen)
        self.tap(button)  # Never replay the irreversible departure submission.
        self.control("depart_missing")
