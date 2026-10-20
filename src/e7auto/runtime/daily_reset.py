"""Explicitly invoked date-change recovery; independent of feature navigation."""
from enum import Enum, auto
from typing import Callable

from e7auto.configuration.daily_reset import DailyResetSettings
from e7auto.core.domain import StopReason
from e7auto.core.observations import Observation
from e7auto.runtime.stop_control import StopExecution

Detector = Callable[[object], Observation | None]


class DailyResetResult(Enum):
    READY = auto()
    HOME = auto()


def _signature(hit):
    return None if hit is None else (hit.object_id, hit.bounds.x // 4, hit.bounds.y // 4)


class DailyResetHandler:
    def __init__(self, runtime, settings: DailyResetSettings,
                 observe: Callable[[object, str], Observation | None], home: Detector):
        self.runtime, self.settings = runtime, settings
        self.observe, self.home = observe, home

    def _frame(self):
        self.runtime.control.checkpoint()
        generation = self.runtime.network_recovery_generation
        frame = self.runtime.capture()
        return frame, generation != self.runtime.network_recovery_generation

    def _pause(self):
        self.runtime.control.checkpoint()
        self.runtime.deps.clock.sleep(self.settings.poll_ms / 1000)

    def _timeout(self, stage):
        raise StopExecution(StopReason.RECOGNITION_TIMEOUT, f'daily reset timeout: {stage}')

    def wait_for_ready(self, ready: Detector, *, observe_ms: int | None = None) -> DailyResetResult:
        """Prefer a popup over the underlying page; absence never authorizes input."""
        duration = self.settings.observe_ms if observe_ms is None else observe_ms
        if type(duration) is not int or duration < 0 or duration >= self.settings.page_timeout_ms:
            raise ValueError('invalid daily-reset observation window')
        deadline = self.runtime.active_monotonic() + self.settings.page_timeout_ms / 1000
        first_ready = None
        previous, stable = None, 0
        while self.runtime.active_monotonic() <= deadline:
            frame, recovered = self._frame()
            if recovered:
                previous, stable = None, 0
                self._pause()
                continue
            date = self.observe(frame, 'date_change_title')
            hit = date if date is not None else ready(frame)
            signature = ('date' if date is not None else 'ready', _signature(hit)) if hit is not None else None
            stable = stable + 1 if signature is not None and signature == previous else int(signature is not None)
            previous = signature
            if stable >= self.settings.stable_frames:
                if date is not None:
                    self._handle()
                    return DailyResetResult.HOME
                now = self.runtime.active_monotonic()
                if first_ready is None:
                    first_ready = now  # Repeated lobby hits do not restart this clock.
                if now - first_ready >= duration / 1000 - 1e-6:
                    return DailyResetResult.READY
            self._pause()
        self._timeout('return to ready page')

    def _handle(self):
        # Each invocation owns its state. Neither a reconnect nor a repeated frame
        # resubmits sign-in confirmation or resets a close attempt budget.
        stage = 'date'
        deadline = self.runtime.active_monotonic() + self.settings.page_timeout_ms / 1000
        previous, stable = None, 0
        attempts = dict.fromkeys(('date', 'reward', 'buff'), 0)
        sent_signatures, sent_times = {}, {}
        self.runtime.deps.logger.event('daily_reset_started')
        while self.runtime.active_monotonic() <= deadline:
            frame, recovered = self._frame()
            if recovered:
                previous, stable = None, 0
                self._pause()
                continue
            date = self.observe(frame, 'date_change_title')
            date_close = self.observe(frame, 'date_change_close')
            reward = self.observe(frame, 'login_reward_title')
            reward_close = self.observe(frame, 'login_reward_close')
            buff = self.observe(frame, 'buff_effect_title')
            buff_close = self.observe(frame, 'buff_effect_close')
            event, hit = None, None
            if stage == 'date':
                if date is not None and date_close is not None:
                    event, hit = 'date', date_close
                elif attempts['date'] and date is None and date_close is None:
                    # A confirmation button has meaning only after closing date.
                    hit = self.observe(frame, 'daily_signin_confirm')
                    if hit is not None:
                        event = 'signin'
            elif date is None and date_close is None and self.observe(frame, 'daily_signin_confirm') is None:
                # Check both titles on this frame before choosing a close. Their
                # shared close pixels cannot identify which popup is active.
                # Conflicting/partial titles block background-home completion.
                if reward is not None or buff is not None:
                    if reward is not None and buff is None and reward_close is not None and stage != 'buff':
                        event, hit = 'reward', reward_close
                    elif buff is not None and reward is None and buff_close is not None:
                        event, hit = 'buff', buff_close
                elif reward_close is None and buff_close is None:
                    hit = self.home(frame)
                    if hit is not None:
                        event = 'home'
            # Include both title and button evidence so moving/changing titles
            # cannot build stable close authorization from the button alone.
            title = date if event == 'date' else reward if event == 'reward' else buff if event == 'buff' else None
            signature = (event, _signature(hit), _signature(title)) if hit is not None else None
            stable = stable + 1 if signature is not None and signature == previous else int(signature is not None)
            previous = signature
            if stable >= self.settings.stable_frames:
                if event == 'home':
                    self.runtime.deps.logger.event('daily_reset_completed')
                    return
                if event == 'signin':
                    self.runtime.click('daily_reset_signin_confirm', hit.anchor)
                    stage = 'reward_or_buff_or_home'
                    deadline = self.runtime.active_monotonic() + self.settings.page_timeout_ms / 1000
                    previous, stable = None, 0
                elif event in attempts:
                    now = self.runtime.active_monotonic()
                    count = attempts[event]
                    retry = (signature == sent_signatures.get(event) and count < self.settings.close_max_attempts
                             and now - sent_times.get(event, now) >= self.settings.close_retry_ms / 1000)
                    if not count or retry:
                        self.runtime.click('daily_reset_' + event + '_close', hit.anchor, attempt=count + 1)
                        attempts[event] += 1
                        sent_signatures[event], sent_times[event] = signature, self.runtime.active_monotonic()
                        previous, stable = None, 0
                        if event in ('reward', 'buff') and not count:
                            stage = event
                            deadline = sent_times[event] + self.settings.page_timeout_ms / 1000
            self._pause()
        self._timeout(stage)
