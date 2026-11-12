"""Guarded three-floor flow; successful irreversible actions are never replayed."""
from __future__ import annotations

from math import ceil
from time import perf_counter

from e7auto.core.domain import OverlayActivityStatus, RunState, StopReason
from e7auto.core.observations import Observation
from e7auto.core.types import Point, Rect, ClientRect
from e7auto.runtime.context import RuntimeContext
from e7auto.runtime.daily_reset import DailyResetHandler, DailyResetResult
from e7auto.runtime.dependencies import AutomationDependencies
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.action_retry import Transition, click_and_wait, observation_signature
from e7auto.runtime.confirmation import Confirmation
from e7auto.runtime.capture import RecoveryProbe
from e7auto.runtime.stop_control import StopExecution, StopController
from .configuration import ChaosConfig
from .contracts import AutoState, Detection, MapView, Node, ChaosVisionPort
from .state import RoundState, choose_node
from .stage_selection import StageSelection, DateChangePending
from .recruitment import HeroRecruitment
from .lineups import RecruitmentPolicy
from .events.context import EventContext
from .events.entry import run as run_event
from .events.rewards import CardWindow


class BattleDefeated(Exception):
    """Unwind the current node/event to the round owner without counting it."""
    def __init__(self, detection: Detection):
        self.detection = detection
        super().__init__("three-frame FAILED confirmation")


class ChaosFlow:
    _REUSABLE_CONTROLS = frozenset({
        'intro_enter', 'battle_title', 'event_rank_enter', 'rank_title', 'camp_rank',
        'continue_exploration', 'event_tablet_continue', 'unclaimed_confirm',
        'journey_close', 'start_battle', 'camp_leave', 'supply_leave', 'shop_leave',
        'filter_dark', 'filter_light', 'depart_title',
    })
    def __init__(self, config: ChaosConfig, dependencies: AutomationDependencies[ChaosVisionPort],
                 control: StopController, publisher: SnapshotPublisher):
        self.runtime = RuntimeContext(config, dependencies, control, publisher,
                                      on_recovery=self._recovered)
        self.settings = config.chaos
        self.target_stage = self.settings.target_stage
        if (type(self.target_stage) is not int or not 1 <= self.target_stage <= 5
                or self.target_stage not in self.settings.stage_selection["lobby_scenes"]):
            raise ValueError("target stage lacks a verified lobby scene")
        self.vision = dependencies.vision
        self.state: RoundState | None = None
        self.stages = StageSelection(self.runtime, self.settings, self.vision,
                                     frame=self._frame, wait=self._wait, tap=self._tap,
                                     delay=self._delay, lobby_guard=self._lobby_guard,
                                     transition=self._transition)
        self.recruitment = HeroRecruitment(self.settings, self.vision,
                                          frame=self._frame, wait=self._wait, tap=self._tap,
                                          act=self._act, control=self._control, first=self._first,
                                          transition=self._transition, take_confirmed=self._take_confirmed)
        self._map_handoff: tuple[MapView, RoundState | None, int] | None = None
        self._battle_pending = False
        self._battle_deadline: float | None = None
        self._battle_levelup_closed = False
        self._at_home_after_reset = False
        self._confirmed: dict[str, Confirmation] = {}
        self._card_handoff: Confirmation | None = None
        self._event_terminal: Confirmation | None = None
        self.daily_reset = DailyResetHandler(
            self.runtime, self.settings.daily_reset, self._entry_observation,
            lambda frame: self._entry_observation(frame, 'home_battle'))

    def _recovered(self, _reason):
        self._confirmed.clear()
        self._card_handoff = self._event_terminal = None
        self._map_handoff = None
        self.stages.invalidate_start()
        self.vision.reset_temporal()

    def cached_game_frame(self):
        return self.runtime.cached_game_frame()

    def release_cached_game_frame(self):
        self._confirmed.clear()
        self._card_handoff = self._event_terminal = None
        self._map_handoff = None
        self.stages.invalidate_start()
        self.state = None
        try:
            self.vision.reset_temporal()
        finally:
            try:
                try:
                    self.vision.close_event_recognition()
                finally:
                    self.vision.close_loot_recognition()
            finally:
                self.runtime.release_cached_game_frame()

    def finish_normal_run(self, _reason):
        # Returning home is part of execute, before normal completion is published.
        self.runtime.control.checkpoint()

    def _frame(self):
        self._confirmed.clear()
        self._card_handoff = self._event_terminal = None
        self._map_handoff = None
        self.stages.invalidate_start()
        self.runtime.control.checkpoint()
        return self._checked_frame(self.runtime.capture())

    def _checked_frame(self, frame):
        if not self._battle_pending:
            return self.vision.prepare_frame(frame)
        previous, stable = None, 0

        def reset():
            nonlocal previous, stable
            previous, stable = None, 0

        def observe(current):
            nonlocal previous, stable
            current = self.vision.prepare_frame(current)
            hit = self.vision.control(current, "failed")
            signature = self._signature(hit) if hit is not None else None
            stable = stable + 1 if signature is not None and signature == previous else int(signature is not None)
            previous = signature
            return current, hit

        probe = RecoveryProbe(observe, reset)
        frame, hit = observe(frame)
        while hit is not None:
            # While FAILED is suspected, no result/auto detection may authorize input.
            if self.runtime.active_monotonic() >= self._battle_deadline:
                raise StopExecution(StopReason.CHAOS_BATTLE_TIMEOUT)
            if stable >= self.settings.timing["stable_frames"]:
                raise BattleDefeated(hit)
            self.runtime.control.checkpoint()
            self.runtime.deps.clock.sleep(min(self.settings.timing["poll_ms"]/1000,
                                             self._battle_deadline-self.runtime.active_monotonic()))
            frame, hit = probe.capture(self.runtime)
        return frame

    def _entry_observation(self, frame, key):
        """Adapt Chaos detections to the shared guarded navigation contract."""
        hit = self.vision.control(self._checked_frame(frame), key)
        return None if hit is None else Observation(hit.key, hit.confidence, hit.bounds, hit.anchor)

    @staticmethod
    def _signature(value):
        if isinstance(value, (Detection, Observation)):
            return observation_signature(value)
        if isinstance(value, MapView):
            return value.floor, tuple(sorted((n.kind, n.detection.bounds.x//4, n.detection.bounds.y//4) for n in value.nodes))
        if isinstance(value, tuple):
            return tuple(ChaosFlow._signature(v) for v in value)
        return value

    def _remember(self, value, *, name=None):
        if name is not None:
            self._confirmed[name] = Confirmation.record(self.runtime, (self.state, value))
        elif isinstance(value, Detection):
            if value.key in self._REUSABLE_CONTROLS or value.key.startswith('hero_'):
                self._remember(value, name=value.key)
        elif isinstance(value, tuple):
            for part in value:
                self._remember(part)

    def _take_confirmed(self, name):
        evidence = self._confirmed.pop(name, None)
        value = evidence.take(self.runtime) if evidence is not None else None
        return value[1] if value is not None and value[0] is self.state else None

    def _joint_probe(self, detector, handoff=None):
        previous, stable, generation, last = None, 0, self.runtime.network_recovery_generation, None
        def reset():
            nonlocal previous, stable, generation, last
            previous, stable, last = None, 0, None
            generation = self.runtime.network_recovery_generation
        def observe(frame, value):
            nonlocal previous, stable, generation, last
            if generation != self.runtime.network_recovery_generation:
                previous, stable = None, 0
                generation = self.runtime.network_recovery_generation
            last = detector(frame, value) if detector is not None and value is not None else None
            key = self._signature((value, last)) if last is not None else None
            stable = stable+1 if key is not None and key == previous else int(key is not None)
            previous = key
        def retain():
            if stable >= self.settings.timing['stable_frames']:
                self._remember(last)
                if handoff is not None:
                    self._remember(last, name=handoff)
        return observe, retain, reset

    def _recovery_frame(self, frame):
        """Single-frame preparation only; the normal frame guard owns FAILED confirmation."""
        frame = self.vision.prepare_frame(frame)
        if self._battle_pending and self.vision.control(frame, "failed") is not None:
            return None
        return frame

    def _wait(self, name, detector, *, timeout_ms=None, reason=StopReason.RECOGNITION_TIMEOUT,
              moving_text=False, single_detection=False, confirmed_auto_state=False,
              alongside=None, handoff=None, reset_detector=None, after_sample=None):
        timeout_ms = timeout_ms or self.settings.timing["page_timeout_ms"]
        deadline = self.runtime.active_monotonic()+timeout_ms/1000
        previous, stable = None, 0
        observe_joint, retain_joint, reset_joint = self._joint_probe(alongside, handoff)
        card_window = None
        sampled_recovery = False

        def reset():
            nonlocal previous, stable, sampled_recovery
            previous, stable = None, 0
            reset_joint()
            if card_window is not None:
                card_window.reset()
            if sampled_recovery:
                self.vision.reset_temporal()
            sampled_recovery = False
            if reset_detector is not None:
                reset_detector()

        def observe(frame):
            nonlocal previous, stable, card_window, sampled_recovery
            if self.runtime.handling_network:
                sampled_recovery = True
                frame = self._recovery_frame(frame)
                if frame is None:
                    reset()
                    return None
            value = detector(frame)
            observe_joint(frame, value)
            if isinstance(value, Detection) and value.key == 'event_reward_close':
                if card_window is None:
                    card_window = CardWindow(self.settings, self.vision, value.key)
                card_window.observe(self.runtime, frame, value)
            elif card_window is not None:
                card_window.reset()
            signature = self._signature(value) if value is not None else None
            stable = stable+1 if signature is not None and signature == previous else int(signature is not None)
            previous = signature
            return value

        probe = RecoveryProbe(observe, reset)
        while self.runtime.active_monotonic() <= deadline:
            sampled_at = self.runtime.active_monotonic()
            value = probe.capture(self.runtime, self._frame)
            if self.runtime.active_monotonic() > deadline:
                break
            if after_sample is not None:
                after_sample()
            text_hit = moving_text and isinstance(value, Detection) and value.key == "text_continue"
            signal_hit = single_detection and isinstance(value, Detection)
            auto_hit = confirmed_auto_state and (value is AutoState.ROTATING or value is AutoState.STATIC)
            if value is not None and (text_hit or signal_hit or auto_hit or stable >= self.settings.timing["stable_frames"]):
                if isinstance(value, MapView):
                    self._map_handoff = value, self.state, self.runtime.capture_count
                if stable >= self.settings.timing['stable_frames']:
                    self._remember(value)
                    retain_joint()
                    if card_window is not None:
                        self._card_handoff = Confirmation.record(self.runtime, (self.state, card_window))
                self.runtime.deps.logger.event("chaos_confirmed", stage=name)
                return value
            self.runtime.control.checkpoint()
            pause = self.settings.timing["poll_ms"]/1000 - (self.runtime.active_monotonic()-sampled_at)
            if pause > 1e-6:
                self.runtime.deps.clock.sleep(pause)
        raise StopExecution(reason, f"chaos timeout: {name}")

    def _delay(self, seconds):
        end = self.runtime.active_monotonic()+seconds
        while self.runtime.active_monotonic() < end - 1e-6:
            sampled_at = self.runtime.active_monotonic()
            self._frame()  # Network remains observable throughout business delays.
            remaining = end-self.runtime.active_monotonic()
            pause = min(remaining, self.settings.timing["network_poll_ms"]/1000 - (self.runtime.active_monotonic()-sampled_at))
            if pause > 1e-6:
                self.runtime.deps.clock.sleep(pause)
            self.runtime.control.checkpoint()

    def _control(self, key, **kwargs):
        hit = self._take_confirmed(key)
        if hit is not None:
            self.runtime.deps.logger.event('chaos_confirmed', stage=key, reused=True)
            return hit
        return self._wait(key, lambda f: self.vision.control(f, key), **kwargs)

    def _tap(self, hit, *, settle_ms=None):
        self._confirmed.clear()
        self._card_handoff = self._event_terminal = None
        self._map_handoff = None
        self.stages.invalidate_start()
        self.vision.invalidate_event_recognition()
        self.vision.invalidate_loot_recognition()
        self.runtime.click("chaos_"+hit.key, hit.anchor)
        self._delay((self.settings.timing["action_settle_ms"] if settle_ms is None else settle_ms)/1000)

    def _act(self, key, guard=None, *, settle_ms=None, reuse_guarded=False):
        if self.settings.scenes[key].action is None:
            raise ValueError(f"scene is recognition-only: {key}")
        hit = self._take_confirmed(key) if guard is None or reuse_guarded else None
        if hit is None:
            hit = self._wait(key, lambda f: self.vision.control(f, key) if guard is None or guard(f) else None)
        if key == "event_rank_enter":
            return self._transition(hit, "event_rank_page", lambda f: self.vision.control(f, "rank_title"),
                                    alongside=self._rank_companion)
        if key == "event_tablet_continue":
            return self._transition(hit, "event_forfeit_prompt", lambda f: self.vision.control(f, "unclaimed_prompt"),
                                    guard=guard, alongside=lambda f, _: self.vision.control(f, 'unclaimed_confirm'))
        self._tap(hit, settle_ms=settle_ms)

    def _transition(self, hit, name, detector, *, source=None, responded=None, guard=None,
                    settle_ms=None, timeout_ms=None, outer_deadline=None, click=None,
                    single_success=None, reason=StopReason.RECOGNITION_TIMEOUT, alongside=None, handoff=None):
        """Only explicit reversible actions opt into retries; _tap stays single-shot."""
        def original(frame):
            if guard is not None and not guard(frame):
                return None
            hits = self.vision.controls(frame, hit.key)
            return hits[0] if len(hits) == 1 else None
        observe_joint, retain_joint, reset_joint = self._joint_probe(alongside, handoff)
        sampled_recovery = False
        def reset_observation():
            nonlocal sampled_recovery
            reset_joint()
            if sampled_recovery:
                self.vision.reset_temporal()
            sampled_recovery = False
        def observe(frame):
            nonlocal sampled_recovery
            if self.runtime.handling_network:
                sampled_recovery = True
                frame = self._recovery_frame(frame)
                if frame is None:
                    reset_joint()
                    return Transition(blocked=True)
            value = detector(frame)
            observe_joint(frame, value)
            return Transition(value, responded=value is not None or bool(responded and responded(frame)))
        deadline = self.runtime.active_monotonic() + max(
            timeout_ms or self.settings.timing["page_timeout_ms"],
            self.runtime.config.click_retry.minimum_timeout_ms) / 1000
        if outer_deadline is not None:
            deadline = min(deadline, outer_deadline)
        result = click_and_wait(
            self.runtime, name="chaos_"+hit.key, stage=name, initial=hit,
            click=click or (lambda current: self._tap(current, settle_ms=0)),
            source=source or original, observe=observe, signature=self._signature,
            target_signature=self._signature, capture=self._frame, settle=self._delay,
            settle_ms=self.settings.timing["action_settle_ms"] if settle_ms is None else settle_ms,
            timeout_ms=timeout_ms or self.settings.timing["page_timeout_ms"],
            outer_deadline=deadline, stable_frames=self.settings.timing["stable_frames"],
            single_success=single_success, reason=reason, reset_observation=reset_observation)
        if isinstance(result, MapView):
            self._map_handoff = result, self.state, self.runtime.capture_count
        if single_success is None or not single_success(result):
            self._remember(result)
            retain_joint()
        self.runtime.deps.logger.event("chaos_confirmed", stage=name)
        self.runtime.control.checkpoint()
        if self.runtime.active_monotonic() >= deadline:
            raise StopExecution(reason, f"chaos timeout: {name}")
        return result

    def _click_to(self, key, next_key, *, guard=None, watch_date=False):
        hit = self._control(key) if guard is None else self._wait(
            key, lambda f: self.vision.control(f, key) if guard(f) else None)
        def destination(frame):
            if watch_date and self.vision.control(frame, "date_change_title") is not None:
                if self.runtime.handling_network:
                    return None
                raise DateChangePending()
            return self.vision.control(frame, next_key)
        companion = self._rank_companion if next_key == 'rank_title' else (
            (lambda f, _: self._first(f, ('filter_dark', 'filter_light')))
            if next_key.startswith('title_') else None)
        return self._transition(hit, next_key, destination, guard=guard, alongside=companion)

    def _rank_companion(self, frame, _title):
        hero = self.state.next_rank_hero()
        return self.vision.control(frame, 'hero_'+hero) if hero is not None else None

    def _same_card(self, frame, original, key):
        hits = self.vision.controls(frame, key, self._card_region(original, key))
        return hits[0] if len(hits) == 1 else None

    def _select_card(self, card, selected_key, confirm_key, *, settle_ms=None, source=None):
        region = self._card_region(card, selected_key)
        selected_seen = False
        def detector(frame):
            nonlocal selected_seen
            selected = self.vision.control(frame, selected_key, region)
            selected_seen = selected is not None
            return self.vision.control(frame, confirm_key) if selected_seen else None
        return self._transition(card, confirm_key, detector,
                                source=source or (lambda f: self._same_card(f, card, card.key)),
                                responded=lambda _: selected_seen, settle_ms=settle_ms)

    def _map_destination(self, frame, floor):
        view = self.vision.map_view(frame)
        return view if view is not None and view.floor == floor else None

    def _first(self, frame, names):
        for key in names:
            hit = self.vision.control(frame, key)
            if hit is not None:
                return hit
        return None

    def _map(self, floor):
        def detect(frame):
            view = self.vision.map_view(frame)
            return view if view is not None and view.floor == floor else None
        return self._wait(f"floor_{floor}_map", detect, timeout_ms=self.settings.timing["entry_timeout_ms"])

    def _map_for_selection(self, floor):
        """Consume the preceding stable map once, only within the same round/frame."""
        self.runtime.control.checkpoint()
        handoff, self._map_handoff = self._map_handoff, None
        if handoff is not None:
            view, state, capture_count = handoff
            if state is self.state and view.floor == floor and capture_count == self.runtime.capture_count:
                return view
        view = self._map(floor)
        self._map_handoff = None
        return view

    def execute(self):
        self.runtime.prepare()
        self.runtime.publisher.mutate(lambda s: s.with_overlay_status(OverlayActivityStatus.RUNNING))
        main_detector = lambda frame: self._entry_observation(frame, "home_battle")
        main = self.runtime.wait_startup_icon(
            "home_battle", main_detector,
            minimum_stable_frames=self.settings.timing["stable_frames"],
        )
        self.runtime.transition(RunState.EXPLORING_CHAOS)
        self._enter_lobby(main)
        while self.runtime.publisher.snapshot.rounds_completed < self.runtime.publisher.snapshot.round_limit:
            if self._at_home_after_reset:
                self._enter_lobby()
            self.stages.invalidate_start()
            self.state = RoundState.for_lineup(self.settings.lineup)
            self.vision.reset_temporal()
            self._start_round()
            try:
                self._explore()
            except BattleDefeated as defeated:
                self.runtime.publisher.mutate(lambda s: s.with_chaos_defeat())
                self.runtime.deps.logger.event("chaos_round_failed", floor=self.state.floor,
                                               node_kind=self.state.node_kind)
                self._transition(defeated.detection, "journey_result",
                                 lambda f: self.vision.control(f, "journey_result"),
                                 alongside=lambda f, _: self.vision.control(f, 'journey_close'))
                self._close_journey()
                continue  # Failure recovery never consumes the success target.
            # _close_journey confirmed either the unobstructed lobby or reset-home.
            if not self.state.counted:
                self.runtime.publisher.mutate(lambda s: s.with_chaos_round())
                self.state.counted = True
        self._return_home()
        raise StopExecution(StopReason.CHAOS_COMPLETE)

    def _enter_lobby(self, main=None):
        main_detector = lambda frame: self._entry_observation(frame, "home_battle")
        if main is None:
            main = self.runtime.wait_stable_observation(
                'home_battle', main_detector, self.settings.timing['entry_timeout_ms'],
                minimum_stable_frames=self.settings.timing['stable_frames'])
        self.runtime.enter_with_retry(
            entry_name="chaos", main=main, main_detector=main_detector,
            destination_name="battle_title",
            destination_detector=lambda frame: self._entry_observation(frame, "battle_title"),
            click_action="chaos_home_battle",
        )
        self._click_to("exploration_card", "intro_enter")
        self._transition(self._control("intro_enter"), "exploration_lobby",
                         lambda f: self._first(f, ("date_change_title", "lobby_research")),
                         single_success=lambda _: True)
        self._prepare_entry_lobby()
        if self._at_home_after_reset:
            raise StopExecution(StopReason.RECOGNITION_TIMEOUT, 'date changed again while reentering exploration')

    def _lobby_ready(self, frame):
        if self.vision.control(frame, 'start_exploration') is None:
            return None
        return self._entry_observation(frame, 'lobby_research')

    def _prepare_entry_lobby(self):
        """Clear a saved exploration before any target-stage selection."""
        self._map_handoff = None
        self.stages.invalidate_start()
        self._at_home_after_reset = False

        def wait(name, detector, **kwargs):
            deadline = self.runtime.active_monotonic()+self.settings.timing['page_timeout_ms']/1000

            def observe(frame):
                if self.vision.control(frame, 'date_change_title') is not None:
                    if self.runtime.handling_network:
                        return None
                    raise DateChangePending()
                return detector(frame)

            value = self._wait(name, observe, **kwargs)
            # Matching and confirmation logging must not authorize a late input.
            if self.runtime.active_monotonic() > deadline:
                raise StopExecution(StopReason.RECOGNITION_TIMEOUT, f'chaos timeout: {name}')
            return value

        def lobby(frame, *, cleared=False):
            if self.vision.control(frame, 'abandon_prompt') is not None:
                return None
            research = self.vision.control(frame, 'lobby_research')
            if research is None:
                return None
            start = self.vision.control(frame, 'start_exploration')
            abandon = self.vision.control(frame, 'lobby_abandon')
            if (start is None) == (abandon is None) or (cleared and abandon is not None):
                return None
            return research, start if start is not None else abandon

        prompt_seen = False
        def confirmation(frame):
            nonlocal prompt_seen
            if self.vision.control(frame, 'date_change_title') is not None:
                if self.runtime.handling_network:
                    return None
                raise DateChangePending()
            prompt = self.vision.control(frame, 'abandon_prompt')
            prompt_seen = prompt is not None
            button = self.vision.control(frame, 'abandon_confirm')
            return (prompt, button) if prompt is not None and button is not None else None

        try:
            _, button = wait('entry_lobby', lobby)
            if button.key == 'start_exploration':
                return
            _, confirmation_button = self._transition(button, 'entry_abandon_confirmation', confirmation,
                                                       guard=self._lobby_guard,
                                                       responded=lambda _: prompt_seen)
            self.runtime.deps.logger.event('chaos_saved_exploration', phase='abandon_submitted')
            self._tap(confirmation_button)
            wait('entry_abandon_result', lambda f: self.vision.control(f, 'journey_result'),
                 alongside=lambda f, _: self.vision.control(f, 'journey_close'))
            self._close_journey()
            if not self._at_home_after_reset:
                wait('entry_abandon_cleared', lambda f: lobby(f, cleared=True))
            self.runtime.deps.logger.event('chaos_saved_exploration', phase='cleared')
        except DateChangePending:
            # Only this entry boundary invokes date recovery; no global interception.
            self._wait_round_lobby(observe_ms=0)

    def _wait_round_lobby(self, *, observe_ms=None):
        self._map_handoff = None
        self.stages.invalidate_start()
        self._at_home_after_reset = self.daily_reset.wait_for_ready(
            self._lobby_ready, observe_ms=observe_ms) is DailyResetResult.HOME
        if self._at_home_after_reset:
            self.vision.reset_temporal()

    def _lobby_guard(self, frame):
        # Even a suspected popup blocks the next underlying lobby input.
        if self.vision.control(frame, 'date_change_title') is not None:
            if self.runtime.handling_network:
                return False
            raise DateChangePending()
        return self.vision.control(frame, 'lobby_research') is not None

    def _return_home(self):
        if self._at_home_after_reset:
            return
        for _ in range(3):
            try:
                self._click_to('exploration_back', 'intro_enter', guard=self._lobby_guard, watch_date=True)
                break
            except DateChangePending:
                self._wait_round_lobby(observe_ms=0)
                if self._at_home_after_reset:
                    return
        else:
            raise StopExecution(StopReason.RECOGNITION_TIMEOUT, 'unstable date popup during final lobby exit')
        self._click_to("exploration_back", "battle_title", guard=lambda f: self.vision.control(f, "intro_enter") is not None)
        self._click_to("battle_title", "home_battle")

    def _start_round(self):
        for _ in range(3):
            try:
                self.stages.ensure()
                start = self.stages.take_start()
                if start is None:
                    continue  # An intervening frame/recovery/input requires fresh confirmation.
                self._tap(start)
                break
            except DateChangePending:
                self._wait_round_lobby(observe_ms=0)
                if self._at_home_after_reset:
                    self._enter_lobby()
        else:
            raise StopExecution(StopReason.RECOGNITION_TIMEOUT, 'unstable lobby confirmation before exploration')
        contract = self._control("contract")
        button = self._select_card(contract, "contract_selected", "supply_confirm",
                                   settle_ms=self.settings.timing["contract_select_settle_ms"])
        self._tap(button)
        for hero in self.settings.heroes:
            self._click_to("ticket_"+hero["ticket"], "title_"+hero["ticket"])
            self.recruitment.filter(hero["attribute"], hero=hero['key'])
            if hero['key'] == 'teacher':
                self.recruitment.recruit('teacher', result_key='recruited_teacher_initial')
            else:
                self.recruitment.recruit(hero["key"])
        self._act("recruit_enter")
        self._map(1)


    def _enter_node(self, node: Node):
        """Retry only the original selectable node, until enter is observed."""
        timing, runtime = self.settings.timing, self.runtime
        floor, origin = self.state.floor, node.detection.bounds
        deadline = runtime.active_monotonic()+timing['page_timeout_ms']/1000
        attempts, last_click = 1, runtime.active_monotonic()
        generation = runtime.network_recovery_generation
        enter_seen = False
        previous, stable = None, 0
        pending_retry = None

        def fail(detail):
            phase = 'enter_button' if enter_seen else 'node_selection'
            raise StopExecution(StopReason.RECOGNITION_TIMEOUT,
                                f'chaos timeout: node_enter; node={node.kind}; floor={floor}; '
                                f'attempts={attempts}/{timing["map_node_max_attempts"]}; '
                                f'phase={phase}; {detail}')

        def check_deadline():
            if not runtime.handling_network and runtime.active_monotonic() >= deadline:
                fail('timed out')

        self._tap(node.detection)
        check_deadline()

        def observe(frame):
            nonlocal generation, enter_seen, previous, stable, pending_retry
            pending_retry = None
            check_deadline()
            if generation != runtime.network_recovery_generation:
                generation = runtime.network_recovery_generation
                previous, stable = None, 0
            button = self.vision.control(frame, 'node_enter')
            check_deadline()
            if button is not None:
                # Acknowledgment is sticky, including across recovery. A later
                # missed button must never reopen map-node clicking.
                enter_seen = True
                previous, stable = None, 0
                return button
            if enter_seen:
                return None
            view = self.vision.map_view(frame)
            check_deadline()
            matches = [] if view is None or view.floor != floor else [
                current for current in view.nodes if current.kind == node.kind
                and origin.x <= current.detection.bounds.x+current.detection.bounds.width/2 < origin.right
                and origin.y <= current.detection.bounds.y+current.detection.bounds.height/2 < origin.bottom]
            current = matches[0].detection if len(matches) == 1 else None
            signature = self._signature(current) if current is not None else None
            stable = stable+1 if signature is not None and signature == previous else int(signature is not None)
            previous = signature
            now = runtime.active_monotonic()
            if (stable >= timing['stable_frames']
                    and now-last_click >= timing['map_node_retry_ms']/1000):
                pending_retry = current
            return None

        def retry():
            nonlocal pending_retry, attempts, last_click, previous, stable
            current, pending_retry = pending_retry, None
            if current is not None:
                if attempts >= timing['map_node_max_attempts']:
                    fail('same selectable node remains after maximum attempts')
                runtime.deps.logger.event('chaos_node_click_retry', node=node.kind, floor=floor,
                                          next_attempt=attempts+1)
                check_deadline()
                attempts += 1
                last_click = runtime.active_monotonic()
                # The immutable original bounds identify the node; the newest
                # measured ClientPoint owns the retry. No priority reselection.
                self._tap(current)
                previous, stable = None, 0
                check_deadline()

        try:
            def reset_node():
                nonlocal previous, stable, generation, pending_retry
                previous, stable = None, 0
                pending_retry = None
                generation = runtime.network_recovery_generation
            button = self._wait('node_enter', observe, reset_detector=reset_node, after_sample=retry,
                                timeout_ms=max(1, ceil((deadline-runtime.active_monotonic())*1000)))
        except StopExecution as exc:
            if exc.reason == StopReason.RECOGNITION_TIMEOUT and exc.detail == 'chaos timeout: node_enter':
                fail('timed out')
            raise
        check_deadline()
        self._tap(button, settle_ms=timing['event_entry_settle_ms'] if node.kind == 'event' else None)

    def _explore(self):
        while True:
            view = self._map_for_selection(self.state.floor)
            try:
                node = choose_node(view.nodes)
            except ValueError as exc:
                raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, str(exc)) from exc
            self.state.node_kind = node.kind
            self.state.reward_stage = None
            self._enter_node(node)
            if node.kind in ("normal", "elite", "boss"):
                self.state.battle_floor = self.state.floor
                if self._battle(node.kind):
                    return
            elif node.kind == "rest":
                self._camp()
            elif node.kind == "event":
                self._event()
            elif node.kind == "loot":
                self._supply()
            elif node.kind == "shop":
                self._shop()
            else:
                raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, node.kind)

    def _swap_teacher(self):
        teacher = self._control("bench_teacher")
        selected_seen = False
        def replace(frame):
            nonlocal selected_seen
            selected = self.vision.control(frame, "bench_selected")
            selected_seen = selected is not None
            if selected is None:
                return None
            knights = self.vision.controls(frame, "formation_knight")
            if len(knights) > 1:
                raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, "multiple formation knights")
            if len(knights) != 1:
                return None
            knight = knights[0]
            dx, dy, w, h = self.settings.scenes["replace_button"].relative_offset
            t = self.runtime.transform
            region = ClientRect(round(knight.anchor.x+dx*t.scale_x), round(knight.anchor.y+dy*t.scale_y), round(w*t.scale_x), round(h*t.scale_y))
            arrows = self.vision.controls(frame, "replace_button", region)
            return (selected, knight, arrows[0]) if len(arrows) == 1 else None
        _, _, button = self._transition(teacher, "replace_knight", replace,
                                       responded=lambda _: selected_seen,
                                       guard=lambda f: self.vision.control(f, "start_battle") is not None)
        self._tap(button)
        self._wait("teacher_deployed", lambda f: self.vision.control(f, "bench_arowell") if self.vision.control(f, "bench_teacher") is None else None)
        self.state.pending_teacher = False

    def _ensure_auto(self, result_names, *, result_detector=None, reset_result=None):
        def observe(frame, *, rotating_only=False):
            result = (self._first(frame, result_names) if result_detector is None
                      else result_detector(frame))
            if result is not None:
                return result
            mode = self.vision.auto_state(frame, self.runtime.deps.clock.monotonic())
            return mode if mode == AutoState.ROTATING or (mode == AutoState.STATIC and not rotating_only) else None
        single_signal = any(key in result_names for key in ("levelup", "victory", "story_skip"))
        def wait_auto(name, detector):
            started = self.runtime.active_monotonic()
            self.vision.begin_auto_recognition(self.runtime.deps.clock.monotonic())
            outcome = "interrupted"
            try:
                result = self._wait(name, detector, single_detection=single_signal, confirmed_auto_state=True,
                                    reset_detector=reset_result)
                outcome = result.key if isinstance(result, Detection) else result.value
                return result
            except StopExecution as exc:
                outcome = exc.reason.value
                raise
            finally:
                self.runtime.deps.logger.event(
                    "chaos_auto_recognition", stage=name, outcome=outcome,
                    elapsed_ms=round((self.runtime.active_monotonic()-started)*1000, 3),
                    **self.vision.auto_recognition_summary())

        mode = wait_auto("auto_state_or_result", observe)
        if isinstance(mode, Detection):
            return mode  # A battle may finish while the network is reconnecting.
        if mode == AutoState.STATIC:
            anchor = self.vision.auto_anchor()
            if anchor is None:
                raise StopExecution(StopReason.RECOGNITION_TIMEOUT, 'auto icon has no confirmed anchor')
            # The motion recognizer still owns fixed-centre/reference confirmation.
            # Retry only a freshly confirmed STATIC state, never a missing ROTATING hit.
            static_anchor = None
            sampled_recovery = False
            def reset_auto():
                nonlocal static_anchor, sampled_recovery
                static_anchor = None
                if sampled_recovery:
                    self.vision.reset_temporal()
                sampled_recovery = False
                if reset_result is not None:
                    reset_result()
            def auto_observation(frame):
                nonlocal static_anchor, sampled_recovery
                if self.runtime.handling_network:
                    sampled_recovery = True
                    frame = self._recovery_frame(frame)
                    if frame is None:
                        static_anchor = None
                        return Transition(blocked=True)
                value = observe(frame)
                static_anchor = self.vision.auto_anchor() if value is AutoState.STATIC else None
                if value is AutoState.STATIC:
                    return Transition()
                return Transition(value)
            def click_auto(point):
                self.runtime.click("chaos_enable_auto", point)
                self.vision.reset_temporal()
                self.vision.begin_auto_recognition(self.runtime.deps.clock.monotonic())
            auto_started, auto_outcome = self.runtime.active_monotonic(), "interrupted"
            try:
                mode = click_and_wait(self.runtime, name="chaos_enable_auto", initial=anchor,
                                      click=click_auto, source=lambda _: static_anchor, observe=auto_observation,
                                      signature=self._signature,
                                      target_signature=lambda p: (p.x//4, p.y//4), capture=self._frame,
                                      timeout_ms=self.settings.timing["page_timeout_ms"],
                                      outer_deadline=self._battle_deadline,
                                      reset_observation=reset_auto,
                                      single_success=lambda value: value is AutoState.ROTATING or
                                      (single_signal and isinstance(value, Detection)))
                auto_outcome = mode.key if isinstance(mode, Detection) else mode.value
            except StopExecution as exc:
                auto_outcome = exc.reason.value
                raise
            finally:
                self.runtime.deps.logger.event("chaos_auto_recognition", stage="auto_rotating_or_result",
                                               outcome=auto_outcome,
                                               elapsed_ms=round((self.runtime.active_monotonic()-auto_started)*1000, 3),
                                               **self.vision.auto_recognition_summary())
            if isinstance(mode, Detection):
                return mode
        self.state.auto_confirmed = True
        return None

    def _skip_boss_story(self, first, result_names, battle_deadline):
        """Skip consecutive story segments; only positive result evidence exits."""
        deadline = min(battle_deadline, self.runtime.active_monotonic()
                       + self.settings.timing["story_timeout_ms"]/1000)
        timeout_reason = (StopReason.CHAOS_BATTLE_TIMEOUT if deadline == battle_deadline
                          else StopReason.RECOGNITION_TIMEOUT)
        phase, attempts, sent_at = "observe", 0, 0.
        previous, stable = None, 0

        def send(hit, next_phase):
            nonlocal phase, attempts, sent_at, previous, stable
            now = self.runtime.active_monotonic()
            if now >= deadline:
                raise StopExecution(timeout_reason, "boss story deadline")
            if phase != next_phase:
                attempts = 0
            if attempts >= self.settings.timing["story_max_attempts"]:
                raise StopExecution(StopReason.RECOGNITION_TIMEOUT, f"boss story {next_phase} did not respond")
            phase, attempts, sent_at = next_phase, attempts+1, now
            previous, stable = None, 0
            self._tap(hit, settle_ms=min(self.settings.timing["action_settle_ms"],
                                        max(0., (deadline-now)*1000)))

        # The approved single-frame skip signal itself authorizes this click.
        if first.key == "story_skip":
            send(first, "opening")

        def reset():
            nonlocal previous, stable
            previous, stable = None, 0

        def observe(frame):
            nonlocal previous, stable
            prompt = self.vision.control(frame, "story_skip_prompt")
            hit, signature = None, None
            if prompt is not None:
                # Never click the skip control behind the modal or a generic confirm.
                hit = self.vision.control(frame, "story_skip_confirm")
                if hit is not None:
                    signature = self._signature((prompt, hit))
            elif phase == "closing":
                # Fresh consecutive absence prevents replaying a submitted modal.
                signature = "dismissed"
            else:
                hit = self.vision.control(frame, "story_skip")
                if hit is not None:
                    signature = self._signature(hit)
                else:
                    result = self._first(frame, result_names)
                    if result is not None:
                        return prompt, hit, signature, result
            stable = stable+1 if signature is not None and signature == previous else int(signature is not None)
            previous = signature
            return prompt, hit, signature, None

        observation = RecoveryProbe(observe, reset)
        while self.runtime.active_monotonic() < deadline - 1e-6:
            sampled_at = self.runtime.active_monotonic()
            prompt, hit, signature, result = observation.capture(self.runtime, self._frame)
            now = self.runtime.active_monotonic()
            if now >= deadline:
                break
            if result is not None:
                return result  # Caller starts its original settlement delay.
            if prompt is None and phase == "observe" and hit is not None:
                send(hit, "opening")
                continue
            if stable >= self.settings.timing["stable_frames"]:
                if signature == "dismissed":
                    phase, attempts, previous, stable = "observe", 0, None, 0
                elif prompt is not None and phase != "closing":
                    send(hit, "closing")
                    continue
                elif now-sent_at >= self.settings.timing["story_retry_ms"]/1000:
                    send(hit, phase)  # Retry only the same freshly stable control.
                    continue
            pause = min(deadline-self.runtime.active_monotonic(),
                        self.settings.timing["poll_ms"]/1000-(self.runtime.active_monotonic()-sampled_at))
            if pause > 1e-6:
                self.runtime.deps.clock.sleep(pause)
        raise StopExecution(timeout_reason, f"boss story timeout: {phase}")

    def _fight(self, preparation, result_names):
        """Observe battle completion; the caller owns reward/continue inputs."""
        self._battle_levelup_closed = False
        self._control(preparation, alongside=lambda f, _: self.vision.control(f, 'start_battle'))
        if (self.settings.lineup.recruitment_policy is RecruitmentPolicy.TEACHER_FOR_AROWELL
                and self.state.pending_teacher):
            if "arowell" in self.state.ranks:
                self._swap_teacher()
            else:
                # Four remaining heroes are filled automatically by the game.
                self.state.pending_teacher = False
        start = self._take_confirmed('start_battle')
        if start is None:
            start = self._wait("start_battle", lambda f: self.vision.control(f, "start_battle")
                               if self.vision.control(f, preparation) is not None else None)
        self._battle_pending = True
        self._battle_deadline = self.runtime.active_monotonic()+self.settings.timing["battle_timeout_ms"]/1000
        recovery_generation = self.runtime.network_recovery_generation
        try:
            self._tap(start)
            # Preserve the existing battle budget starting after the click buffer.
            self._battle_deadline = self.runtime.active_monotonic()+self.settings.timing["battle_timeout_ms"]/1000
            return self._await_battle_result(preparation, result_names, recovery_generation=recovery_generation)
        finally:
            self._battle_pending = False
            self._battle_deadline = None

    def _await_battle_result(self, preparation, result_names, *, recovery_generation):
        if "levelup" in result_names:
            return self._await_levelup_or_victory(preparation, result_names, recovery_generation)
        # Event battles and the final boss keep their original result contracts.
        names = result_names
        story_names = ("story_skip_prompt", "story_skip") if preparation == "prepare_boss" else ()
        signal_names = story_names + names
        result = None
        if not self.state.auto_confirmed:
            result = self._ensure_auto(signal_names)
        deadline = self._battle_deadline
        while True:
            if result is None:
                self._delay(min(self.settings.timing["settlement_poll_ms"]/1000, max(0., deadline-self.runtime.active_monotonic())))
            if self.runtime.active_monotonic() >= deadline:
                raise StopExecution(StopReason.CHAOS_BATTLE_TIMEOUT)
            if result is None:
                result = self._first(self._frame(), signal_names)
                if result is None:
                    continue
            if result.key in story_names:
                self._battle_pending = False  # Boss story is positive victory evidence.
                result = self._skip_boss_story(result, names, deadline)
            remaining = deadline-self.runtime.active_monotonic()
            if remaining <= 0:
                raise StopExecution(StopReason.CHAOS_BATTLE_TIMEOUT)
            # Discard the trigger frame; all actions need fresh stable evidence.
            result = self._wait("battle_result", lambda f: self._first(f, names),
                                timeout_ms=max(1, round(remaining*1000)), reason=StopReason.CHAOS_BATTLE_TIMEOUT,
                                alongside=lambda f, hit: self.vision.control(f, 'journey_close')
                                if hit.key == 'journey_result' else None)
            self._battle_pending = False
            return result

    def _await_levelup_or_victory(self, preparation, result_names, recovery_generation):
        """Probe the popup first, then VICTORY on the same guarded frame."""
        runtime = self.runtime
        deadline = self._battle_deadline
        started, wall_started = runtime.active_monotonic(), runtime.deps.clock.monotonic()
        captures, capture_seconds = runtime.capture_count, runtime.capture_seconds
        story_names = ("story_skip_prompt", "story_skip") if preparation == "prepare_boss" else ()
        resume_names = tuple(dict.fromkeys(("levelup_close",) + tuple(
            key for key in result_names if key not in ("levelup", "victory"))))
        next_probe, generation = started, recovery_generation
        resuming = False
        probes = dict.fromkeys(("levelup", "victory"), 0)
        match_seconds = dict.fromkeys(probes, 0.)
        outcome, last_phase = "interrupted", started
        signal = None

        def mark(phase, **fields):
            nonlocal last_phase
            now = runtime.active_monotonic()
            runtime.deps.logger.event(
                "chaos_battle_settlement", phase=phase, preparation=preparation,
                elapsed_ms=round((now-started)*1000, 3),
                phase_elapsed_ms=round((now-last_phase)*1000, 3),
                wall_elapsed_ms=round((runtime.deps.clock.monotonic()-wall_started)*1000, 3),
                **fields)
            last_phase = now

        def probe(frame):
            nonlocal next_probe, generation, probes, match_seconds, resuming, signal
            now = runtime.active_monotonic()
            if not runtime.handling_network and now >= deadline:
                raise StopExecution(StopReason.CHAOS_BATTLE_TIMEOUT)
            recovered = generation != runtime.network_recovery_generation
            generation = runtime.network_recovery_generation
            resuming = resuming or recovered
            if signal is not None and signal.key not in story_names:
                return signal  # A result trigger is followed by fresh settlement confirmation.
            signal = None  # A story skip may authorize input, so keep its current-frame hit.
            story = self._first(frame, story_names)
            if story is not None:
                signal = story
                return signal
            if recovered or runtime.handling_network or now >= next_probe-1e-6:
                next_probe = now+self.settings.timing["battle_signal_poll_ms"]/1000
                # A popup can obscure VICTORY before the next scheduled probe.
                # Never make a transient/background banner the only entry gate.
                for key in ("levelup", "victory"):
                    measured = perf_counter()
                    hit = self.vision.control(frame, key)
                    match_seconds[key] += perf_counter()-measured
                    probes[key] += 1
                    if hit is not None:
                        signal = hit
                        return signal
                # Recovery can miss VICTORY and initially return a transition
                # frame. Only this recovery phase also accepts popup/results.
                if resuming:
                    signal = self._first(frame, resume_names)
                    return signal
            return None

        def reset_signal():
            nonlocal signal, generation, next_probe, resuming
            signal = None
            generation = runtime.network_recovery_generation
            next_probe = runtime.active_monotonic()
            resuming = True

        def observe_signal(frame):
            if runtime.handling_network:
                frame = self._recovery_frame(frame)
                if frame is None:
                    reset_signal()
                    return None
            return probe(frame)

        observation = RecoveryProbe(observe_signal, reset_signal)

        try:
            result = None if self.state.auto_confirmed else self._ensure_auto(
                story_names+("levelup", "victory"), result_detector=probe, reset_result=reset_signal)
            while result is None:
                result = observation.capture(runtime, self._frame)
                if result is None:
                    pause = min(deadline, next_probe, runtime.active_monotonic()
                                + self.settings.timing["network_poll_ms"]/1000)-runtime.active_monotonic()
                    if pause > 1e-6:
                        runtime.deps.clock.sleep(pause)
            if result.key in story_names:
                self._battle_pending = False
                result = self._skip_boss_story(
                    result, ("levelup", "victory")+resume_names, deadline)
            mark("signal", signal=result.key)
            result = self._observe_battle_settlement(result_names, deadline, mark)
            outcome = result.key
            return result
        except StopExecution as exc:
            outcome = exc.reason.value
            raise
        except BattleDefeated:
            outcome = "defeated"
            raise
        finally:
            runtime.deps.logger.event(
                "chaos_battle_settlement_summary", preparation=preparation, outcome=outcome,
                elapsed_ms=round((runtime.active_monotonic()-started)*1000, 3),
                wall_elapsed_ms=round((runtime.deps.clock.monotonic()-wall_started)*1000, 3),
                capture_count=runtime.capture_count-captures,
                capture_ms=round((runtime.capture_seconds-capture_seconds)*1000, 3),
                levelup_checks=probes["levelup"], levelup_match_ms=round(match_seconds["levelup"]*1000, 3),
                victory_checks=probes["victory"], victory_match_ms=round(match_seconds["victory"]*1000, 3))

    def _observe_battle_settlement(self, result_names, deadline, mark):
        """Close a jointly stable popup early; otherwise observe the full window."""
        runtime = self.runtime
        observe_until = runtime.active_monotonic()+self.settings.timing["battle_result_settle_ms"]/1000
        final_names = tuple(key for key in result_names if key != "levelup")
        previous, stable = None, 0
        closed = seen = complete = False

        def reset():
            nonlocal previous, stable
            previous, stable = None, 0

        def observe(frame):
            nonlocal previous, stable, seen, complete
            if runtime.handling_network:
                frame = self._recovery_frame(frame)
                if frame is None:
                    reset()
                    return None
            now = runtime.active_monotonic()
            title = self.vision.control(frame, "levelup")
            close = self.vision.control(frame, "levelup_close")
            value, signature = None, None
            if title is not None or close is not None:
                if not seen:
                    mark("popup_visible")
                    seen = True
                if title is not None and close is not None and not closed:
                    if not complete:
                        mark("popup_complete")
                        complete = True
                    value, signature = close, self._signature((title, close))
            elif closed or now >= observe_until-1e-6:
                value = self._first(frame, final_names)
                signature = None if value is None else self._signature(value)
            stable = stable+1 if signature is not None and signature == previous else int(signature is not None)
            previous = signature
            return value

        observation = RecoveryProbe(observe, reset)
        while runtime.active_monotonic() < deadline-1e-6:
            sampled_at = runtime.active_monotonic()
            value = observation.capture(runtime, self._frame)
            if runtime.active_monotonic() >= deadline:
                break
            if stable >= self.settings.timing["stable_frames"]:
                self._battle_pending = False
                if value.key == "levelup_close":
                    mark("popup_stable")
                    runtime.deps.logger.event("chaos_confirmed", stage="levelup_close")
                    closed = True  # Remains submitted across recovery; never replay.
                    self._battle_levelup_closed = True
                    self._tap(value, settle_ms=0)
                    mark("close_submitted")
                    self._delay(min(self.settings.timing["action_settle_ms"]/1000,
                                    max(0., deadline-runtime.active_monotonic())))
                    previous, stable = None, 0
                    continue
                mark("result_confirmed", result=value.key, levelup_closed=closed)
                runtime.deps.logger.event("chaos_confirmed", stage="battle_result")
                return value
            pause = min(deadline-runtime.active_monotonic(),
                        self.settings.timing["poll_ms"]/1000-(runtime.active_monotonic()-sampled_at))
            if pause > 1e-6:
                runtime.deps.clock.sleep(pause)
        raise StopExecution(StopReason.CHAOS_BATTLE_TIMEOUT, "chaos timeout: battle settlement")

    def _battle(self, kind):
        third_boss = kind == "boss" and self.state.battle_floor == 3
        names = ("journey_result",) if third_boss else ("levelup", "victory" if kind == "normal" else kind+"_choose_reward")
        result = self._fight("prepare_"+kind, names)
        if third_boss:
            self._close_journey()
            return True
        if kind in ("elite", "boss"):
            reward_key = kind+"_choose_reward"
            if result.key != reward_key or self.settings.scenes[reward_key].action is None:
                raise ValueError("battle result is not an actionable reward entry")
            # _fight already supplied three fresh stable frames for this button.
            self._tap(result)
            self._choose_loot(reward_key=reward_key)
            self._control(kind+"_claim_complete")
            self.state.reward_stage = "claimed"
        if kind == "boss":
            self._voucher()
            next_floor = self.state.battle_floor+1
            self._continue(next_floor, allow_unclaimed=True)
            self.state.floor = next_floor
        elif kind == "normal":
            self._continue(self.state.floor, watch_levelup=True)
        else:
            self._continue(self.state.floor)
        return False

    def _close_journey(self):
        close = self._take_confirmed('journey_close')
        if close is None:
            close = self._wait("journey_close", lambda f: self.vision.control(f, "journey_close")
                               if self.vision.control(f, "journey_result") is not None else None)
        self._transition(close, "journey_closed",
                         lambda f: self.vision.control(f, "date_change_title") or self._lobby_ready(f),
                         guard=lambda f: self.vision.control(f, "journey_result") is not None)
        self._wait_round_lobby()

    def _voucher(self):
        second_floor = self.state.battle_floor == 2
        if second_floor and self.state.second_floor_voucher_done:
            return
        policy = self.settings.lineup.recruitment_policy
        hero = None
        if second_floor:
            if policy is RecruitmentPolicy.TEACHER_FOR_AROWELL:
                hero = "teacher"
            elif (policy is RecruitmentPolicy.RESTORE_SHADOW_ROSE
                  and "shadow_rose" in self.state.departed_heroes
                  and "shadow_rose" not in self.state.ranks):
                hero = "shadow_rose"
        if hero is not None and hero in self.state.ranks:
            raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, "repeated special recruitment")

        def detect(frame):
            if hero is not None:
                # A required replacement cannot silently fall through to forfeiture.
                return self.vision.control(frame, "ticket_all")
            if self.vision.control(frame, "boss_claim_complete") is not None:
                return self.vision.control(frame, "continue_exploration")
            return None

        ticket = self._wait("boss_voucher_or_skip", detect)
        if hero is not None:
            self._transition(ticket, "title_all", lambda f: self.vision.control(f, "title_all"),
                             alongside=lambda f, _: self._first(f, ('filter_dark', 'filter_light')))
            self.recruitment.filter("dark", profession=True if hero == "teacher" else "knight", hero=hero)
            self.recruitment.recruit(hero, result_key="recruited_shadow_rose_all" if hero == "shadow_rose" else None)
            self.state.ranks[hero] = 1
            self.state.departed_heroes.discard(hero)
            if hero == "teacher":
                self.state.pending_teacher = "arowell" in self.state.ranks
            self.runtime.deps.logger.event("chaos_hero_recruited", hero=hero, rank=1,
                                           purpose="replacement" if hero == "shadow_rose" else "teacher")
        if second_floor:
            self.state.second_floor_voucher_done = True

    def _continue(self, floor, allow_unclaimed=False, *, watch_levelup=False):
        if watch_levelup:
            if self.settings.scenes["continue_exploration"].action is None:
                raise ValueError("scene is recognition-only: continue_exploration")
            button = self._wait_battle_transition(
                "continue_exploration", lambda f: self.vision.control(f, "continue_exploration"),
                self.runtime.active_monotonic()+self.settings.timing["page_timeout_ms"]/1000)
            self._tap(button)
        else:
            button = self._control("continue_exploration")
        def target(frame):
            if allow_unclaimed:
                prompt = self.vision.control(frame, "unclaimed_prompt")
                if prompt is not None:
                    button = self.vision.control(frame, "unclaimed_confirm")
                    return (prompt, button) if button is not None else None
            view = self.vision.map_view(frame)
            if view and view.floor == floor:
                return view
            return None
        if watch_levelup:
            result = self._wait_battle_transition(
                "continue_destination", target,
                self.runtime.active_monotonic()+self.settings.timing["page_timeout_ms"]/1000,
                action_key="continue_exploration")
        else:
            result = self._transition(button, "continue_destination", target,
                                      responded=lambda f: allow_unclaimed and
                                      self.vision.control(f, "unclaimed_prompt") is not None)
        if isinstance(result, tuple):
            if self.settings.scenes["unclaimed_confirm"].action is None:
                raise ValueError("scene is recognition-only: unclaimed_confirm")
            self._tap(result[1])
            self._map(floor)

    def _card_region(self, hit, scene):
        base = self.runtime.transform.rect(self.settings.scenes[scene].region)
        half = round(self.settings.geometry["loot_selection_half_width"]*self.runtime.transform.scale_x)
        cx = hit.bounds.x+hit.bounds.width//2
        return ClientRect(cx-half, base.y, half*2, base.height)

    def _wait_battle_transition(self, name, detector, deadline, *, action_key=None,
                                retry_settle_ms=None, reason=StopReason.RECOGNITION_TIMEOUT):
        """Keep the battle popup guard until the requested next page is stable."""
        runtime = self.runtime
        retry_settle_ms = self.settings.timing["action_settle_ms"] if retry_settle_ms is None else retry_settle_ms
        previous, stable = None, 0
        resume_action = False

        def reset():
            nonlocal previous, stable
            previous, stable = None, 0
            self.vision.invalidate_loot_recognition()

        def observe(frame):
            nonlocal previous, stable
            if runtime.handling_network:
                frame = self._recovery_frame(frame)
                if frame is None:
                    reset()
                    return None, name
            title = self.vision.control(frame, "levelup")
            close = self.vision.control(frame, "levelup_close")
            value, stage = None, name
            if title is not None or close is not None:
                self.vision.invalidate_loot_recognition()
                if title is not None and close is not None and not self._battle_levelup_closed:
                    value, stage = (title, close), "levelup_close"
            else:
                value = detector(frame)
                if value is None and resume_action and action_key is not None:
                    value, stage = self.vision.control(frame, action_key), action_key
            signature = (stage, self._signature(value)) if value is not None else None
            stable = stable+1 if signature is not None and signature == previous else int(signature is not None)
            previous = signature
            return value, stage

        observation = RecoveryProbe(observe, reset)
        while runtime.active_monotonic() < deadline-1e-6:
            sampled_at = runtime.active_monotonic()
            value, stage = observation.capture(runtime, self._frame)
            if runtime.active_monotonic() >= deadline:
                break
            if stable >= self.settings.timing["stable_frames"]:
                runtime.deps.logger.event("chaos_confirmed", stage=stage)
                if runtime.active_monotonic() >= deadline:
                    break
                if stage == name:
                    if isinstance(value, MapView):
                        self._map_handoff = value, self.state, runtime.capture_count
                    return value
                if stage == "levelup_close":
                    self._battle_levelup_closed = True
                    resume_action = True
                    hit, settle_ms = value[1], self.settings.timing["action_settle_ms"]
                else:
                    # Only a newly closed popup permits this single confirmed retry.
                    resume_action = False
                    hit, settle_ms = value, retry_settle_ms
                self._tap(hit, settle_ms=min(settle_ms, max(0., deadline-runtime.active_monotonic())*1000))
                runtime.deps.logger.event(
                    "chaos_battle_transition_levelup_closed" if stage == "levelup_close"
                    else "chaos_battle_transition_action_resumed",
                    target=name, action_key=action_key)
                previous, stable = None, 0
                continue
            runtime.control.checkpoint()
            pause = min(deadline-runtime.active_monotonic(),
                        self.settings.timing["poll_ms"]/1000-(runtime.active_monotonic()-sampled_at))
            if pause > 1e-6:
                runtime.deps.clock.sleep(pause)
        raise StopExecution(reason, f"chaos timeout: {name}")

    def _choose_loot(self, *, reward_key=None):
        self.vision.begin_loot_recognition()
        started = self.runtime.active_monotonic()
        deadline = started+self.settings.timing["page_timeout_ms"]/1000
        outcome = "interrupted"
        try:
            if reward_key is None:
                cards = self._wait("three_known_loot_cards", self.vision.loot_cards, reason=StopReason.CHAOS_UNKNOWN_CONTENT)
            else:
                cards = self._wait_battle_transition(
                    "three_known_loot_cards", self.vision.loot_cards,
                    deadline, action_key=reward_key,
                    reason=StopReason.CHAOS_UNKNOWN_CONTENT)
            self.runtime.control.checkpoint()
            if self.runtime.active_monotonic() >= deadline:
                raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, 'chaos timeout: three_known_loot_cards')
            outcome = "confirmed"
        except StopExecution as exc:
            outcome = exc.reason.value
            raise
        finally:
            summary = self.vision.end_loot_recognition()
            self.runtime.deps.logger.event("chaos_loot_recognition", outcome=outcome,
                                           elapsed_ms=round((self.runtime.active_monotonic()-started)*1000,3),
                                           **summary)
        self.runtime.control.checkpoint()
        if self.runtime.active_monotonic() >= deadline:
            raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, 'chaos timeout: three_known_loot_cards')
        chosen = min(cards, key=lambda h: (self.settings.loot_priorities[h.key], h.bounds.x+h.bounds.width/2))
        self.state.reward_stage = "selecting"
        def source(frame):
            current = self.vision.loot_cards(frame)
            if current is None:
                return None
            hits = [h for h in current if h.key == chosen.key and
                    chosen.bounds.x <= h.anchor.x <= chosen.bounds.right]
            return hits[0] if len(hits) == 1 else None
        button = self._select_card(chosen, "loot_selected", "loot_confirm", settle_ms=0, source=source)
        self._tap(button)
        self.state.reward_stage = "submitted"

    def _camp(self):
        self._wait("camp", lambda f: self._first(f, ("camp_rank", "camp_rank_done")),
                   alongside=lambda f, _: self.vision.control(f, 'camp_leave'))
        hero = self.state.next_rank_hero()
        if hero:
            self._click_to("camp_rank", "rank_title")
            self._rank_up(hero, rank_ready=True)
        self._transition(self._control("camp_leave"), "camp_left",
                         lambda f: self._map_destination(f, self.state.floor))

    def _rank_up(self, hero, *, steps=1, battle_reward=False, rank_ready=False):
        """Camp and event ranks use the same confirmed, once-only state update."""
        rank = self.state.ranks[hero]
        if (not 1 <= rank < 5 or type(steps) is not int or not 1 <= steps <= 4
                or (battle_reward and steps != 1)):
            raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, "invalid rank-up target")
        expected = min(5, rank + steps)
        if not rank_ready:
            self._control("rank_title", alongside=lambda f, _: self.vision.control(f, 'hero_'+hero))
        button = self.recruitment.choose(hero, confirm_key='rank_confirm')
        self._tap(button)
        if self.state.node_kind == "rest" and not battle_reward:
            close = self._camp_rank_result(hero, rank, expected)
            self._transition(close, "camp_rank_done", lambda f: self.vision.control(f, "camp_rank_done"),
                             guard=lambda f: self.vision.rank_result(f) == (rank, expected),
                             alongside=lambda f, _: self.vision.control(f, 'camp_leave'))
            return
        if battle_reward:
            # This separate reward layout has no before/after panel; its known
            # single-rank flow retains the confirmed completion-card signal.
            self._control("event_battle_rank_done",
                          alongside=lambda f, _: self.vision.control(f, 'continue_exploration'))
            after = expected
        else:
            card_window = CardWindow(self.settings, self.vision, 'rank_close')
            def result(frame):
                observed = self.vision.rank_result(frame)
                close = self.vision.control(frame, 'rank_close') if observed == (rank, expected) else None
                card_window.observe(self.runtime, frame, close)
                return observed if observed == (rank, expected) else None
            _, after = self._wait(f"rank_result_{rank}_to_{expected}", result,
                                  reset_detector=card_window.reset)
            if card_window.frames:
                self._card_handoff = Confirmation.record(self.runtime, (self.state, card_window))
        self.state.ranks[hero] = after
        self.runtime.deps.logger.event("chaos_rank_changed", hero=hero, rank_before=rank,
                                       rank_after=after, node_kind=self.state.node_kind)
        if self.state.node_kind == "event" and not battle_reward:
            self._event_context().close_reward_cards(close_key="rank_close")
        else:
            key = "continue_exploration" if battle_reward else "rank_close"
            self._transition(self._control(key), "rank_closed",
                             lambda f: self.vision.control(f, "text_continue") or self._map_destination(f, self.state.floor),
                             guard=lambda f: self.vision.control(f, "event_battle_rank_done") is not None
                             if battle_reward else self.vision.rank_result(f) == (rank, expected),
                             single_success=lambda hit: isinstance(hit, Detection) and hit.key == "text_continue")

    def _camp_rank_result(self, hero, rank, expected):
        """Observe rank progress and close authorization on the same fresh frames."""
        timeout = self.settings.timing["page_timeout_ms"]/1000
        deadline = self.runtime.active_monotonic()+timeout
        result_previous = joint_previous = None
        result_stable = joint_stable = 0
        recorded = False

        def reset():
            nonlocal result_previous, joint_previous, result_stable, joint_stable
            result_previous = joint_previous = None
            result_stable = joint_stable = 0

        def observe(frame):
            nonlocal result_previous, joint_previous, result_stable, joint_stable
            observed = self.vision.rank_result(frame)
            valid = observed == (rank, expected)
            button = self.vision.control(frame, "rank_close") if valid else None
            result_signature = observed if valid else None
            result_stable = (result_stable+1 if result_signature is not None and result_signature == result_previous
                             else int(result_signature is not None))
            result_previous = result_signature
            joint_signature = self._signature((observed, button)) if valid and button is not None else None
            joint_stable = (joint_stable+1 if joint_signature is not None and joint_signature == joint_previous
                            else int(joint_signature is not None))
            joint_previous = joint_signature
            return button

        probe = RecoveryProbe(observe, reset)
        while self.runtime.active_monotonic() <= deadline:
            sampled_at = self.runtime.active_monotonic()
            button = probe.capture(self.runtime, self._frame)
            if self.runtime.active_monotonic() > deadline:
                break
            if not recorded and result_stable >= self.settings.timing["stable_frames"]:
                self.state.ranks[hero] = expected
                self.runtime.deps.logger.event("chaos_rank_changed", hero=hero, rank_before=rank,
                                               rank_after=expected, node_kind=self.state.node_kind)
                self.runtime.deps.logger.event("chaos_confirmed", stage=f"rank_result_{rank}_to_{expected}")
                recorded = True
                deadline = self.runtime.active_monotonic()+timeout  # Original close-phase budget, once.
            if recorded and joint_stable >= self.settings.timing["stable_frames"]:
                if self.settings.scenes["rank_close"].action is None:
                    raise ValueError("scene is recognition-only: rank_close")
                self.runtime.deps.logger.event("chaos_confirmed", stage="rank_close")
                return button
            self.runtime.control.checkpoint()
            pause = self.settings.timing["poll_ms"]/1000-(self.runtime.active_monotonic()-sampled_at)
            if pause > 1e-6:
                self.runtime.deps.clock.sleep(pause)
        stage = "rank_close" if recorded else f"rank_result_{rank}_to_{expected}"
        raise StopExecution(StopReason.RECOGNITION_TIMEOUT, f"chaos timeout: {stage}")

    def _event_context(self):
        return EventContext(self.runtime, self.vision, self.settings, self.state, self.state.floor,
                            self._frame, self._wait, self._tap, self._act, self._fight, self._rank_up,
                            self.recruitment.depart, self._take_card, self._remember_terminal,
                            self._take_terminal)

    def _take_card(self, close_key):
        evidence, self._card_handoff = self._card_handoff, None
        confirmed = evidence.take(self.runtime) if evidence is not None else None
        window = confirmed[1] if confirmed is not None and confirmed[0] is self.state else None
        return window if window is not None and window.close_key == close_key else None

    def _remember_terminal(self, result):
        self._event_terminal = Confirmation.record(self.runtime, (self.state, result))
        if isinstance(result, MapView):
            self._map_handoff = result, self.state, self.runtime.capture_count

    def _take_terminal(self):
        evidence, self._event_terminal = self._event_terminal, None
        value = evidence.take(self.runtime) if evidence is not None else None
        if value is None or value[0] is not self.state:
            return None
        result = value[1]
        return result if not isinstance(result, MapView) or result.floor == self.state.floor else None

    def _event(self):
        run_event(self._event_context())

    def _supply(self):
        self._act("supply_get_loot")
        self._choose_loot()
        self._control("supply_loot_done", alongside=lambda f, _: self.vision.control(f, 'supply_leave'))
        self.state.reward_stage = "claimed"
        self._transition(self._control("supply_leave"), "supply_left",
                         lambda f: self._map_destination(f, self.state.floor))

    def _shop_offer(self, identity, button_keys, *, unavailable_identity=None):
        pending = self._take_confirmed('offer:'+identity)
        if pending is not None:
            return pending[:3]
        return self._wait(identity+'_purchase_entry', lambda f: self._shop_offer_state(
            f, identity, button_keys, unavailable_identity=unavailable_identity),
            alongside=lambda f, _: self.vision.control(f, 'shop_leave'))

    def _shop_offer_state(self, frame, identity, button_keys, *, unavailable_identity=None):
        if unavailable_identity is not None:
            unavailable = self.vision.control(frame, unavailable_identity)
            if unavailable is not None:
                return unavailable, None, "unavailable"
        item = self.vision.control(frame, identity)
        if item is None:
            return None
        cx = item.bounds.x+item.bounds.width/2
        buttons = [b for key in button_keys for b in self.vision.controls(frame, key)
                   if abs(b.anchor.x-cx) < self.settings.geometry["shop_entry_max_dx"]*self.runtime.transform.scale_x
                   and 0 < b.anchor.y-item.bounds.bottom < self.settings.geometry["shop_entry_max_dy"]*self.runtime.transform.scale_y]
        if len(buttons) != 1:
            return None
        button = buttons[0]
        return item, button, button.key

    def _shop_purchase(self, button, purchase_item, done, *, next_offer=False):
        popup_seen = False
        def destination(frame):
            nonlocal popup_seen
            popup_seen = self.vision.control(frame, "purchase_title") is not None
            if popup_seen and self.vision.control(frame, purchase_item) is not None:
                return self.vision.control(frame, "purchase_confirm")
            return None
        confirm = self._transition(button, "purchase_confirm", destination,
                                   responded=lambda _: popup_seen,
                                   source=lambda f: self._same_card(f, button, button.key)
                                   if self.vision.control(f, "investment" if purchase_item == "purchase_item"
                                                          else "health_restore") is not None else None)
        self._tap(confirm)
        # A completed neighbouring card must never authorize leaving this purchase.
        pad = self.settings.geometry["shop_result_padding"]
        r = button.bounds
        px,py = round(pad*self.runtime.transform.scale_x),round(pad*self.runtime.transform.scale_y)
        region = ClientRect(r.x-px,r.y-py,r.width+px*2,r.height+py*2)
        def health_offer(frame, _):
            offer = self._shop_offer_state(frame, 'health_restore', ('shop_health_entry',),
                                           unavailable_identity='health_restore_unavailable')
            if offer is None:
                return None
            leave = self.vision.control(frame, 'shop_leave') if offer[2] == 'unavailable' else None
            return (*offer, leave)
        self._wait(done, lambda f: self.vision.control(f, done, region),
                   alongside=health_offer if next_offer else
                   (lambda f, _: self.vision.control(f, 'shop_leave')),
                   handoff='offer:health_restore' if next_offer else None)

    def _shop(self):
        _, button, state = self._shop_offer("investment", ("investment_entry",),
                                          unavailable_identity="investment_insufficient_item")
        if state != "unavailable":
            self._shop_purchase(button, "purchase_item", "investment_done", next_offer=True)
            _, button, state = self._shop_offer("health_restore", ("shop_health_entry",),
                                              unavailable_identity="health_restore_unavailable")
            if state == "shop_health_entry":
                self._shop_purchase(button, "purchase_health_restore", "health_restore_done")
            else:
                self.runtime.deps.logger.event("chaos_shop_skipped", item="health_restore", state=state)
        else:
            self.runtime.deps.logger.event("chaos_shop_skipped", item="investment", state=state)
        floor = self.state.floor
        confirmation_sent = False
        prompt_seen = False

        def destination(frame):
            nonlocal prompt_seen
            prompt = self.vision.control(frame, "shop_leave_prompt")
            prompt_seen = prompt is not None
            if prompt is not None:
                # A visible prompt blocks the map, including while closing.
                if confirmation_sent:
                    return None
                button = self.vision.control(frame, "shop_leave_confirm")
                return (prompt, button) if button is not None else None
            view = self.vision.map_view(frame)
            return view if view is not None and view.floor == floor else None

        result = self._transition(self._control("shop_leave"), "shop_leave_destination", destination,
                                  responded=lambda _: prompt_seen)
        if isinstance(result, MapView):
            return
        if self.settings.scenes["shop_leave_confirm"].action is None:
            raise ValueError("scene is recognition-only: shop_leave_confirm")
        confirmation_sent = True
        self._tap(result[1])
        self._wait(f"floor_{floor}_map", destination, timeout_ms=self.settings.timing["entry_timeout_ms"])
