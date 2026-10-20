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
from e7auto.runtime.stop_control import StopExecution, StopController
from .configuration import ChaosConfig
from .contracts import AutoState, Detection, MapView, Node, ChaosVisionPort
from .state import RoundState, choose_node
from .stage_selection import StageSelection, DateChangePending
from .recruitment import HeroRecruitment
from .lineups import RecruitmentPolicy
from .events.context import EventContext
from .events.entry import run as run_event


class BattleDefeated(Exception):
    """Unwind the current node/event to the round owner without counting it."""
    def __init__(self, detection: Detection):
        self.detection = detection
        super().__init__("three-frame FAILED confirmation")


class ChaosFlow:
    def __init__(self, config: ChaosConfig, dependencies: AutomationDependencies[ChaosVisionPort],
                 control: StopController, publisher: SnapshotPublisher):
        self.runtime = RuntimeContext(config, dependencies, control, publisher,
                                      on_recovery=self._recovered, discard_interrupted_frames=True)
        self.settings = config.chaos
        self.target_stage = self.settings.target_stage
        if (type(self.target_stage) is not int or not 1 <= self.target_stage <= 5
                or self.target_stage not in self.settings.stage_selection["lobby_scenes"]):
            raise ValueError("target stage lacks a verified lobby scene")
        self.vision = dependencies.vision
        self.state: RoundState | None = None
        self.stages = StageSelection(self.runtime, self.settings, self.vision,
                                     frame=self._frame, wait=self._wait, tap=self._tap,
                                     delay=self._delay, lobby_guard=self._lobby_guard)
        self.recruitment = HeroRecruitment(self.settings, self.vision,
                                          frame=self._frame, wait=self._wait, tap=self._tap,
                                          act=self._act, control=self._control, first=self._first)
        self._map_handoff: tuple[MapView, RoundState | None, int] | None = None
        self._battle_pending = False
        self._battle_deadline: float | None = None
        self._battle_levelup_closed = False
        self._at_home_after_reset = False
        self.daily_reset = DailyResetHandler(
            self.runtime, self.settings.daily_reset, self._entry_observation,
            lambda frame: self._entry_observation(frame, 'home_battle'))

    def _recovered(self, _reason):
        self._map_handoff = None
        self.stages.invalidate_start()
        self.vision.reset_temporal()

    def cached_game_frame(self):
        return self.runtime.cached_game_frame()

    def release_cached_game_frame(self):
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
        self._map_handoff = None
        self.stages.invalidate_start()
        self.runtime.control.checkpoint()
        return self._checked_frame(self.runtime.capture())

    def _checked_frame(self, frame):
        frame = self.vision.prepare_frame(frame)
        if not self._battle_pending:
            return frame
        previous, stable = None, 0
        while (hit := self.vision.control(frame, "failed")) is not None:
            # While FAILED is suspected, no result/auto detection may authorize input.
            if self.runtime.active_monotonic() >= self._battle_deadline:
                raise StopExecution(StopReason.CHAOS_BATTLE_TIMEOUT)
            signature = self._signature(hit)
            stable = stable+1 if signature == previous else 1
            previous = signature
            if stable >= self.settings.timing["stable_frames"]:
                raise BattleDefeated(hit)
            self.runtime.control.checkpoint()
            self.runtime.deps.clock.sleep(min(self.settings.timing["poll_ms"]/1000,
                                             self._battle_deadline-self.runtime.active_monotonic()))
            generation = self.runtime.network_recovery_generation
            frame = self.vision.prepare_frame(self.runtime.capture())
            if generation != self.runtime.network_recovery_generation:
                previous, stable = None, 0
        return frame

    def _entry_observation(self, frame, key):
        """Adapt Chaos detections to the shared guarded navigation contract."""
        hit = self.vision.control(self._checked_frame(frame), key)
        return None if hit is None else Observation(hit.key, hit.confidence, hit.bounds, hit.anchor)

    @staticmethod
    def _signature(value):
        if isinstance(value, Detection):
            return value.key, value.bounds.x//4, value.bounds.y//4
        if isinstance(value, MapView):
            return value.floor, tuple(sorted((n.kind, n.detection.bounds.x//4, n.detection.bounds.y//4) for n in value.nodes))
        if isinstance(value, tuple):
            return tuple(ChaosFlow._signature(v) for v in value)
        return value

    def _wait(self, name, detector, *, timeout_ms=None, reason=StopReason.RECOGNITION_TIMEOUT,
              moving_text=False, single_detection=False, confirmed_auto_state=False):
        timeout_ms = timeout_ms or self.settings.timing["page_timeout_ms"]
        deadline = self.runtime.active_monotonic()+timeout_ms/1000
        previous, stable = None, 0
        while self.runtime.active_monotonic() <= deadline:
            sampled_at = self.runtime.active_monotonic()
            generation = self.runtime.network_recovery_generation
            value = detector(self._frame())
            if generation != self.runtime.network_recovery_generation:
                previous, stable = None, 0
            signature = self._signature(value) if value is not None else None
            stable = stable+1 if signature is not None and signature == previous else int(signature is not None)
            previous = signature
            text_hit = moving_text and isinstance(value, Detection) and value.key == "text_continue"
            signal_hit = single_detection and isinstance(value, Detection)
            auto_hit = confirmed_auto_state and (value is AutoState.ROTATING or value is AutoState.STATIC)
            if value is not None and (text_hit or signal_hit or auto_hit or stable >= self.settings.timing["stable_frames"]):
                if isinstance(value, MapView):
                    self._map_handoff = value, self.state, self.runtime.capture_count
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
        return self._wait(key, lambda f: self.vision.control(f, key), **kwargs)

    def _tap(self, hit, *, settle_ms=None):
        self._map_handoff = None
        self.stages.invalidate_start()
        self.vision.invalidate_event_recognition()
        self.runtime.click("chaos_"+hit.key, hit.anchor)
        self._delay((self.settings.timing["action_settle_ms"] if settle_ms is None else settle_ms)/1000)

    def _act(self, key, guard=None, *, settle_ms=None):
        if self.settings.scenes[key].action is None:
            raise ValueError(f"scene is recognition-only: {key}")
        hit = self._wait(key, lambda f: self.vision.control(f, key) if guard is None or guard(f) else None)
        self._tap(hit, settle_ms=settle_ms)

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
                self._tap(defeated.detection)
                self._control("journey_result")
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
            timeout_ms=self.settings.timing["entry_timeout_ms"],
        )
        self._act("exploration_card")
        self._act("intro_enter")
        self._wait_round_lobby(observe_ms=0)
        if self._at_home_after_reset:
            raise StopExecution(StopReason.RECOGNITION_TIMEOUT, 'date changed again while reentering exploration')

    def _lobby_ready(self, frame):
        if self.vision.control(frame, 'start_exploration') is None:
            return None
        return self._entry_observation(frame, 'lobby_research')

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
            raise DateChangePending()
        return self.vision.control(frame, 'lobby_research') is not None

    def _return_home(self):
        if self._at_home_after_reset:
            return
        for _ in range(3):
            try:
                self._act('exploration_back', self._lobby_guard)
                break
            except DateChangePending:
                self._wait_round_lobby(observe_ms=0)
                if self._at_home_after_reset:
                    return
        else:
            raise StopExecution(StopReason.RECOGNITION_TIMEOUT, 'unstable date popup during final lobby exit')
        self._control("intro_enter")
        self._act("exploration_back", lambda f: self.vision.control(f, "intro_enter") is not None)
        self._control("battle_title")
        self._act("battle_title")
        self._control("home_battle")

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
        self._tap(contract)
        region = self._card_region(contract, "contract_selected")
        self._act("supply_confirm", lambda f: self.vision.control(f, "contract_selected", region) is not None)
        for hero in self.settings.heroes:
            self._act("ticket_"+hero["ticket"])
            self._control("title_"+hero["ticket"])
            self.recruitment.filter(hero["attribute"])
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

        def fail(detail):
            phase = 'enter_button' if enter_seen else 'node_selection'
            raise StopExecution(StopReason.RECOGNITION_TIMEOUT,
                                f'chaos timeout: node_enter; node={node.kind}; floor={floor}; '
                                f'attempts={attempts}/{timing["map_node_max_attempts"]}; '
                                f'phase={phase}; {detail}')

        def check_deadline():
            if runtime.active_monotonic() >= deadline:
                fail('timed out')

        self._tap(node.detection)
        check_deadline()

        def observe(frame):
            nonlocal attempts, last_click, generation, enter_seen, previous, stable
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
            return None

        try:
            button = self._wait('node_enter', observe,
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
        self._tap(self._control("bench_teacher"))
        def replace(frame):
            selected = self.vision.control(frame, "bench_selected")
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
        _, _, button = self._wait("replace_knight", replace)
        self._tap(button)
        self._wait("teacher_deployed", lambda f: self.vision.control(f, "bench_arowell") if self.vision.control(f, "bench_teacher") is None else None)
        self.state.pending_teacher = False

    def _ensure_auto(self, result_names, *, result_detector=None):
        def observe(frame, *, rotating_only=False):
            result = (self._first(frame, result_names) if result_detector is None
                      else result_detector(frame))
            if result is not None:
                return result
            mode = self.vision.auto_state(frame, self.runtime.active_monotonic())
            return mode if mode == AutoState.ROTATING or (mode == AutoState.STATIC and not rotating_only) else None
        single_signal = any(key in result_names for key in ("levelup", "victory", "story_skip"))
        def wait_auto(name, detector):
            started = self.runtime.active_monotonic()
            self.vision.begin_auto_recognition(started)
            outcome = "interrupted"
            try:
                result = self._wait(name, detector, single_detection=single_signal, confirmed_auto_state=True)
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
            self.runtime.click("chaos_enable_auto", anchor)
            self.vision.reset_temporal()
            mode = wait_auto("auto_rotating_or_result", lambda f: observe(f, rotating_only=True))
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
        while self.runtime.active_monotonic() < deadline - 1e-6:
            sampled_at = self.runtime.active_monotonic()
            generation = self.runtime.network_recovery_generation
            frame = self._frame()
            if generation != self.runtime.network_recovery_generation:
                previous, stable = None, 0
            now = self.runtime.active_monotonic()
            if now >= deadline:
                break
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
                    if phase == "observe":
                        send(hit, "opening")
                        continue
                    signature = self._signature(hit)
                else:
                    result = self._first(frame, result_names)
                    if result is not None:
                        return result  # Caller starts its original settlement delay.
            stable = stable+1 if signature is not None and signature == previous else int(signature is not None)
            previous = signature
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
        self._control(preparation)
        if (self.settings.lineup.recruitment_policy is RecruitmentPolicy.TEACHER_FOR_AROWELL
                and self.state.pending_teacher):
            if "arowell" in self.state.ranks:
                self._swap_teacher()
            else:
                # Four remaining heroes are filled automatically by the game.
                self.state.pending_teacher = False
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
                                timeout_ms=max(1, round(remaining*1000)), reason=StopReason.CHAOS_BATTLE_TIMEOUT)
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
            nonlocal next_probe, generation, probes, match_seconds, resuming
            now = runtime.active_monotonic()
            if now >= deadline:
                raise StopExecution(StopReason.CHAOS_BATTLE_TIMEOUT)
            recovered = generation != runtime.network_recovery_generation
            generation = runtime.network_recovery_generation
            resuming = resuming or recovered
            story = self._first(frame, story_names)
            if story is not None:
                return story
            if recovered or now >= next_probe-1e-6:
                next_probe = now+self.settings.timing["battle_signal_poll_ms"]/1000
                # A popup can obscure VICTORY before the next scheduled probe.
                # Never make a transient/background banner the only entry gate.
                for key in ("levelup", "victory"):
                    measured = perf_counter()
                    hit = self.vision.control(frame, key)
                    match_seconds[key] += perf_counter()-measured
                    probes[key] += 1
                    if hit is not None:
                        return hit
                # Recovery can miss VICTORY and initially return a transition
                # frame. Only this recovery phase also accepts popup/results.
                if resuming:
                    return self._first(frame, resume_names)
            return None

        try:
            result = None if self.state.auto_confirmed else self._ensure_auto(
                story_names+("levelup", "victory"), result_detector=probe)
            while result is None:
                result = probe(self._frame())
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
        while runtime.active_monotonic() < deadline-1e-6:
            sampled_at = runtime.active_monotonic()
            generation = runtime.network_recovery_generation
            frame = self._frame()
            now = runtime.active_monotonic()
            if now >= deadline:
                break
            if generation != runtime.network_recovery_generation:
                previous, stable = None, 0
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
            self._tap(result, settle_ms=self.settings.timing["loot_entry_settle_ms"])
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
        self._act("journey_close", lambda f: self.vision.control(f, "journey_result") is not None)
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
            self._tap(ticket)
            self._control("title_all")
            self.recruitment.filter("dark", profession=True if hero == "teacher" else "knight")
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
            self._act("continue_exploration")
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
            result = self._wait("continue_destination", target)
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
        while runtime.active_monotonic() < deadline-1e-6:
            sampled_at = runtime.active_monotonic()
            generation = runtime.network_recovery_generation
            frame = self._frame()
            if generation != runtime.network_recovery_generation:
                previous, stable = None, 0
            title = self.vision.control(frame, "levelup")
            close = self.vision.control(frame, "levelup_close")
            value, stage = None, name
            if title is not None or close is not None:
                if title is not None and close is not None and not self._battle_levelup_closed:
                    value, stage = (title, close), "levelup_close"
            else:
                value = detector(frame)
                if value is None and resume_action and action_key is not None:
                    value, stage = self.vision.control(frame, action_key), action_key
            signature = (stage, self._signature(value)) if value is not None else None
            stable = stable+1 if signature is not None and signature == previous else int(signature is not None)
            previous = signature
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
        outcome = "interrupted"
        try:
            if reward_key is None:
                cards = self._wait("three_known_loot_cards", self.vision.loot_cards, reason=StopReason.CHAOS_UNKNOWN_CONTENT)
            else:
                cards = self._wait_battle_transition(
                    "three_known_loot_cards", self.vision.loot_cards,
                    started+self.settings.timing["page_timeout_ms"]/1000,
                    action_key=reward_key, retry_settle_ms=self.settings.timing["loot_entry_settle_ms"],
                    reason=StopReason.CHAOS_UNKNOWN_CONTENT)
            outcome = "confirmed"
        except StopExecution as exc:
            outcome = exc.reason.value
            raise
        finally:
            summary = self.vision.end_loot_recognition()
            self.runtime.deps.logger.event("chaos_loot_recognition", outcome=outcome,
                                           elapsed_ms=round((self.runtime.active_monotonic()-started)*1000,3),
                                           **summary)
        chosen = min(cards, key=lambda h: (self.settings.loot_priorities[h.key], h.bounds.x+h.bounds.width/2))
        self.state.reward_stage = "selecting"
        self._tap(chosen, settle_ms=0)
        region = self._card_region(chosen, "loot_selected")
        def selected(frame):
            return self.vision.control(frame, "loot_selected", region) is not None
        self._act("loot_confirm", selected)
        self.state.reward_stage = "submitted"

    def _camp(self):
        self._wait("camp", lambda f: self._first(f, ("camp_rank", "camp_rank_done")))
        hero = self.state.next_rank_hero()
        if hero:
            self._act("camp_rank")
            self._rank_up(hero)
            self._control("camp_rank_done")
        self._act("camp_leave")
        self._map(self.state.floor)

    def _rank_up(self, hero, *, steps=1, battle_reward=False):
        """Camp and event ranks use the same confirmed, once-only state update."""
        rank = self.state.ranks[hero]
        if (not 1 <= rank < 5 or type(steps) is not int or not 1 <= steps <= 4
                or (battle_reward and steps != 1)):
            raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, "invalid rank-up target")
        expected = min(5, rank + steps)
        self._control("rank_title")
        button = self.recruitment.choose(hero, confirm_key='rank_confirm')
        self._tap(button)
        if self.state.node_kind == "rest" and not battle_reward:
            self._tap(self._camp_rank_result(hero, rank, expected))
            return
        if battle_reward:
            # This separate reward layout has no before/after panel; its known
            # single-rank flow retains the confirmed completion-card signal.
            self._control("event_battle_rank_done")
            after = expected
        else:
            def result(frame):
                observed = self.vision.rank_result(frame)
                return observed if observed == (rank, expected) else None
            _, after = self._wait(f"rank_result_{rank}_to_{expected}", result)
        self.state.ranks[hero] = after
        self.runtime.deps.logger.event("chaos_rank_changed", hero=hero, rank_before=rank,
                                       rank_after=after, node_kind=self.state.node_kind)
        if self.state.node_kind == "event" and not battle_reward:
            self._event_context().close_reward_cards(close_key="rank_close")
        else:
            self._act("continue_exploration" if battle_reward else "rank_close")

    def _camp_rank_result(self, hero, rank, expected):
        """Observe rank progress and close authorization on the same fresh frames."""
        timeout = self.settings.timing["page_timeout_ms"]/1000
        deadline = self.runtime.active_monotonic()+timeout
        result_previous = joint_previous = None
        result_stable = joint_stable = 0
        recorded = False
        while self.runtime.active_monotonic() <= deadline:
            sampled_at = self.runtime.active_monotonic()
            generation = self.runtime.network_recovery_generation
            frame = self._frame()
            observed = self.vision.rank_result(frame)
            valid = observed == (rank, expected)
            button = self.vision.control(frame, "rank_close") if valid else None
            if generation != self.runtime.network_recovery_generation:
                result_previous = joint_previous = None
                result_stable = joint_stable = 0
            result_signature = observed if valid else None
            result_stable = (result_stable+1 if result_signature is not None and result_signature == result_previous
                             else int(result_signature is not None))
            result_previous = result_signature
            joint_signature = self._signature((observed, button)) if valid and button is not None else None
            joint_stable = (joint_stable+1 if joint_signature is not None and joint_signature == joint_previous
                            else int(joint_signature is not None))
            joint_previous = joint_signature
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
                            self.recruitment.depart)

    def _event(self):
        run_event(self._event_context())

    def _supply(self):
        self._act("supply_get_loot", settle_ms=self.settings.timing["supply_loot_entry_settle_ms"])
        self._choose_loot()
        self._control("supply_loot_done")
        self.state.reward_stage = "claimed"
        self._act("supply_leave")
        self._map(self.state.floor)

    def _shop_offer(self, identity, button_keys, *, unavailable_identity=None):
        def entry(frame):
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
        return self._wait(identity+"_purchase_entry", entry)

    def _shop_purchase(self, button, purchase_item, done):
        self._tap(button)
        guard = lambda f: self.vision.control(f, "purchase_title") is not None and self.vision.control(f, purchase_item) is not None
        self._act("purchase_confirm", guard)
        # A completed neighbouring card must never authorize leaving this purchase.
        pad = self.settings.geometry["shop_result_padding"]
        r = button.bounds
        px,py = round(pad*self.runtime.transform.scale_x),round(pad*self.runtime.transform.scale_y)
        region = ClientRect(r.x-px,r.y-py,r.width+px*2,r.height+py*2)
        self._wait(done, lambda f: self.vision.control(f, done, region))

    def _shop(self):
        _, button, state = self._shop_offer("investment", ("investment_entry",),
                                          unavailable_identity="investment_insufficient_item")
        if state != "unavailable":
            self._shop_purchase(button, "purchase_item", "investment_done")
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

        def destination(frame):
            prompt = self.vision.control(frame, "shop_leave_prompt")
            if prompt is not None:
                # A visible prompt blocks the map, including while closing.
                if confirmation_sent:
                    return None
                button = self.vision.control(frame, "shop_leave_confirm")
                return (prompt, button) if button is not None else None
            view = self.vision.map_view(frame)
            return view if view is not None and view.floor == floor else None

        self._act("shop_leave")
        result = self._wait("shop_leave_destination", destination)
        if isinstance(result, MapView):
            return
        if self.settings.scenes["shop_leave_confirm"].action is None:
            raise ValueError("scene is recognition-only: shop_leave_confirm")
        confirmation_sent = True
        self._tap(result[1])
        self._wait(f"floor_{floor}_map", destination, timeout_ms=self.settings.timing["entry_timeout_ms"])
