"""Game-side date reset transitions and round recovery, without real input."""
from dataclasses import replace

import pytest

from e7auto.bootstrap import AutomationSession
from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


@pytest.fixture(scope='module')
def config():
    return chaos_config()


class ResetGame(ChaosGame):
    def __init__(self, config, *, monthly=True, reset_round=1, delay=0., stuck=None,
                 network_after=None, lose_once=False, buff=False):
        super().__init__(config, event_type='event_trap', network_after=network_after)
        self.monthly, self.reset_round, self.reset_delay = monthly, reset_round, delay
        self.stuck, self.lose_once = stuck, lose_once
        self.reset_due = None
        self.reset_fired = False
        self.lost = False
        self.closures = 0
        self.ignored_closes = 0
        self.click_times = []
        self.after_signin_delay = 0.
        self.destination_due = None
        self.partial = None
        self.buff = buff
        self.destination_state = None
        self.after_reward_delay = 0.
        self.ignored_by_key = {}

    def capture_client(self, window, bounds):
        if self.reset_due is not None and self.clock.now >= self.reset_due and self.state == 'hall':
            self.reset_due = None;self.reset_fired = True;self.state = 'date'
        if self.destination_due is not None and self.clock.now >= self.destination_due:
            self.destination_due = None;self.state = self.destination_state
        if self.lose_once and self.state == 'battle' and self.clock.now-self.battle_started >= 2:
            self.lose_once = False;self.lost = True;self.state = 'failed'
        return super().capture_client(window, bounds)

    def _visible(self):
        # Deliberately expose background identities to prove popup precedence.
        if self.state == 'date':
            return ['lobby_research','start_exploration','home_battle','date_change_title'] + (
                [] if self.partial == 'date' else ['date_change_close'])
        if self.state == 'signin':return ['home_battle', 'daily_signin_confirm']
        if self.state == 'login_reward':
            return ['home_battle','login_reward_title'] + ([] if self.partial == 'reward' else ['login_reward_close'])
        if self.state == 'buff':
            return ['home_battle'] + ([] if self.partial == 'buff_title' else ['buff_effect_title']) + (
                [] if self.partial == 'buff_close' else ['login_reward_close'])
        if self.state in ('loading', 'blank'):return []
        return super()._visible()

    def control(self, frame, key, region=None):
        # Both names recognize the exact same visible control at the same point.
        if key == 'buff_effect_close':
            hit = super().control(frame, 'login_reward_close', region)
            return replace(hit, key=key) if hit is not None else None
        return super().control(frame, key, region)

    def click(self, window, point):
        if self.network_remaining:
            return super().click(window, point)
        key = self.points.get(point)
        if key == 'login_reward_close' and self.state == 'buff':key = 'buff_effect_close'
        if key in ('date_change_close','daily_signin_confirm','login_reward_close','buff_effect_close','failed'):
            expected = {'date_change_close':'date','daily_signin_confirm':'signin',
                        'login_reward_close':'login_reward','buff_effect_close':'buff','failed':'failed'}[key]
            assert self.state == expected
            self.actions.append(key);self.click_times.append((key,self.clock.now))
            if key == 'failed':self.state = 'journey'
            elif self.stuck == key:pass
            elif self.ignored_by_key.get(key, 0):self.ignored_by_key[key] -= 1
            elif key.endswith('close') and self.ignored_closes:
                self.ignored_closes -= 1
            elif key == 'date_change_close':self.state = 'signin'
            elif key == 'daily_signin_confirm':
                self.state = 'loading'
                self.destination_state = 'login_reward' if self.monthly else 'buff' if self.buff else 'home'
                self.destination_due = self.clock.now + self.after_signin_delay
            elif key == 'login_reward_close' and self.buff:
                self.state = 'loading';self.destination_state = 'buff'
                self.destination_due = self.clock.now + self.after_reward_delay
            else:self.state = 'home'
            if self.network_after == key and not self.network_injected:
                self.network_remaining, self.network_injected = 4, True
            if self.on_click:self.on_click(self,key)
            return
        super().click(window,point)
        if key == 'journey_close':
            self.closures += 1
            if self.lost:
                self.completed -= 1;self.lost = False
            if not self.reset_fired and self.closures == self.reset_round:
                self.reset_due = self.clock.now + self.reset_delay


def prepared(game):
    flow = ChaosFlow(game.config,game.dependencies(),StopController(),
        SnapshotPublisher(RuntimeSnapshot.chaos_run('date-reset',1),lambda _:None))
    flow.runtime.prepare()
    return flow


def run(game, rounds):
    session = AutomationSession(game.config,game.dependencies(),StopController(),lambda _:None)
    return session.run_chaos(rounds)


@pytest.mark.parametrize('monthly',[False,True])
@pytest.mark.parametrize('buff',[False,True])
def test_optional_monthly_reward_and_late_destination(config,monthly,buff):
    game=ResetGame(config,monthly=monthly,buff=buff);game.state='date';game.after_signin_delay=2.
    game.after_reward_delay=2.
    flow=prepared(game);flow._wait_round_lobby()
    assert flow._at_home_after_reset and game.state=='home'
    assert game.actions==['date_change_close','daily_signin_confirm']+(['login_reward_close'] if monthly else [])+(['buff_effect_close'] if buff else [])
    assert game.clock.now>=2.


def test_normal_lobby_wait_is_bounded_and_does_not_restart_timer(config):
    game=ResetGame(config);game.state='hall'
    flow=prepared(game);flow._wait_round_lobby()
    assert not flow._at_home_after_reset and game.actions==[]
    assert 2.2-1e-6<=game.clock.now<2.4


def test_delayed_popup_during_observation_takes_precedence(config):
    game=ResetGame(config);game.state='hall';game.reset_due=1.
    flow=prepared(game);flow._wait_round_lobby()
    assert flow._at_home_after_reset and game.state=='home'
    assert game.actions[0]=='date_change_close'


@pytest.mark.parametrize('partial',['date','reward'])
def test_partial_popup_never_authorizes_background_or_close(config,partial):
    game=ResetGame(config);game.state='date';game.partial=partial
    flow=prepared(game)
    with pytest.raises(StopExecution) as error:flow._wait_round_lobby()
    assert error.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert not flow._at_home_after_reset
    assert game.actions==([] if partial=='date' else ['date_change_close','daily_signin_confirm'])


@pytest.mark.parametrize('state',['blank','signin','buff'])
def test_unknown_page_and_confirmation_without_date_context_do_not_click(config,state):
    game=ResetGame(config);game.state=state
    flow=prepared(game)
    with pytest.raises(StopExecution):flow._wait_round_lobby()
    assert game.actions==[] and game.clock.now<16


@pytest.mark.parametrize('key',['date_change_close','daily_signin_confirm','login_reward_close'])
def test_network_after_input_preserves_progress_and_does_not_replay(config,key):
    game=ResetGame(config,network_after=key);game.state='date'
    flow=prepared(game);flow._wait_round_lobby()
    assert game.actions.count(key)==1
    assert game.actions.count('network_retry')==4
    assert game.state=='home' and flow.runtime.active_monotonic()<5


@pytest.mark.parametrize('key',['date_change_close','daily_signin_confirm','login_reward_close'])
def test_stop_after_each_input_prevents_further_inputs(config,key):
    game=ResetGame(config);game.state='date'
    flow=prepared(game)
    game.on_click=lambda _,k:flow.runtime.control.request(StopReason.MANUAL_STOP) if k==key else None
    with pytest.raises(StopExecution) as error:flow._wait_round_lobby()
    assert error.value.reason is StopReason.MANUAL_STOP and game.actions[-1]==key


@pytest.mark.parametrize('key,maximum',[('date_change_close',3),('daily_signin_confirm',1),('login_reward_close',3)])
def test_stuck_controls_have_bounded_retries_and_timeout(config,key,maximum):
    game=ResetGame(config,stuck=key);game.state='date'
    flow=prepared(game)
    with pytest.raises(StopExecution):flow._wait_round_lobby()
    assert game.actions.count(key)==maximum and not flow._at_home_after_reset
    times=[t for k,t in game.click_times if k==key]
    assert all(b-a>=1.5-1e-6 for a,b in zip(times,times[1:]))
    assert game.clock.now<33


def test_lost_date_close_retries_only_stable_same_control(config):
    game=ResetGame(config);game.state='date';game.ignored_closes=1
    flow=prepared(game);flow._wait_round_lobby()
    assert game.actions==['date_change_close','date_change_close','daily_signin_confirm','login_reward_close']


@pytest.mark.parametrize('monthly',[False,True])
@pytest.mark.parametrize('reset_round',[1,2])
@pytest.mark.parametrize('buff',[False,True])
def test_round_resume_and_final_round_home_preserve_counters(config,monthly,reset_round,buff):
    game=ResetGame(config,monthly=monthly,reset_round=reset_round,buff=buff)
    final=run(game,2)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    assert final.rounds_completed==game.completed==2 and game.state=='home'
    assert game.actions.count('start_exploration')==2
    assert game.actions.count('home_battle')==(2 if reset_round==1 else 1)
    assert game.actions.count('daily_signin_confirm')==1
    if reset_round==2:
        assert game.actions[-1]==('buff_effect_close' if buff else 'login_reward_close' if monthly else 'daily_signin_confirm')


def test_reward_and_buff_share_pixels_but_have_independent_retry_budgets(config):
    game=ResetGame(config,buff=True);game.state='date'
    game.ignored_by_key={'login_reward_close':2,'buff_effect_close':2}
    flow=prepared(game);flow._wait_round_lobby()
    assert game.actions==['date_change_close','daily_signin_confirm']+['login_reward_close']*3+['buff_effect_close']*3
    assert game.state=='home'


@pytest.mark.parametrize('partial',['buff_title','buff_close','conflicting'])
def test_buff_partial_or_conflicting_titles_block_background_home(config,partial,monkeypatch):
    game=ResetGame(config,buff=True);game.state='date';game.partial=partial
    visible=game._visible
    monkeypatch.setattr(game,'_visible',lambda: visible()+(['login_reward_title'] if game.state=='buff' and partial=='conflicting' else []))
    flow=prepared(game)
    with pytest.raises(StopExecution) as error:flow._wait_round_lobby()
    assert error.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert 'buff_effect_close' not in game.actions and not flow._at_home_after_reset


@pytest.mark.parametrize('boundary',['network','stop','stuck'])
def test_buff_close_recovery_stop_and_retry_limit(config,boundary):
    game=ResetGame(config,buff=True,network_after='buff_effect_close' if boundary=='network' else None,
                   stuck='buff_effect_close' if boundary=='stuck' else None)
    game.state='date';flow=prepared(game)
    if boundary=='stop':
        game.on_click=lambda _,key: flow.runtime.control.request(StopReason.MANUAL_STOP) if key=='buff_effect_close' else None
    if boundary=='network':
        flow._wait_round_lobby()
        assert game.state=='home' and game.actions.count('network_retry')==4
    else:
        with pytest.raises(StopExecution) as error:flow._wait_round_lobby()
        assert error.value.reason is (StopReason.MANUAL_STOP if boundary=='stop' else StopReason.RECOGNITION_TIMEOUT)
    assert game.actions.count('buff_effect_close')==(3 if boundary=='stuck' else 1)


def test_buff_cannot_transition_back_to_login_reward(config):
    game=ResetGame(config,buff=True);game.state='date';flow=prepared(game)
    game.on_click=lambda g,key:setattr(g,'state','login_reward') if key=='buff_effect_close' else None
    with pytest.raises(StopExecution):flow._wait_round_lobby()
    assert game.actions.count('login_reward_close')==1 and game.actions.count('buff_effect_close')==1


def test_buff_transition_gets_its_own_deadline(config):
    game=ResetGame(config,buff=True);game.state='date'
    game.after_reward_delay=12.;game.ignored_by_key={'buff_effect_close':2}
    flow=prepared(game);flow._wait_round_lobby()
    assert game.state=='home' and game.actions.count('buff_effect_close')==3


def test_defeat_reset_resumes_without_counting_failed_round(config):
    game=ResetGame(config,lose_once=True)
    final=run(game,1)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    assert final.rounds_completed==game.completed==1
    assert game.actions.count('failed')==1 and game.actions.count('start_exploration')==2
    assert game.actions.count('home_battle')==2


def test_popup_after_observation_is_checked_before_next_start(config):
    # Fire after the observation window, during the remaining joint confirmation.
    game=ResetGame(config,delay=2.6)
    final=run(game,2)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    close=game.actions.index('journey_close')
    reset=game.actions.index('date_change_close')
    next_start=game.actions.index('start_exploration',close)
    assert close<reset<next_start
    assert final.rounds_completed==2 and game.actions.count('start_exploration')==2


def test_popup_during_final_lobby_exit_does_not_reenter(config):
    game=ResetGame(config,delay=2.6)
    final=run(game,1)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    assert game.actions.count('home_battle')==1 and game.actions.count('start_exploration')==1
    assert game.actions.count('date_change_close')==1 and game.state=='home'


def test_window_change_before_close_prevents_input(config):
    game=ResetGame(config);game.state='date'
    flow=prepared(game)
    def change(_):
        if game.captures==4:
            windows=flow.runtime.deps.windows
            windows.state=replace(windows.state,client_bounds=replace(windows.state.client_bounds,x=1))
    game.on_capture=change
    with pytest.raises(StopExecution) as error:flow._wait_round_lobby()
    assert error.value.reason is StopReason.WINDOW_ABNORMAL and game.actions==[]


def test_transient_home_frame_does_not_skip_delayed_reward(config,monkeypatch):
    game=ResetGame(config);game.state='date';game.after_signin_delay=.5
    visible=game._visible
    flashes=0
    def blink():
        nonlocal flashes
        if game.state=='loading' and flashes<1:
            flashes+=1;return ['home_battle']
        return visible()
    monkeypatch.setattr(game,'_visible',blink)
    flow=prepared(game);flow._wait_round_lobby()
    assert flashes==1 and game.actions[-1]=='login_reward_close' and game.state=='home'


def test_reentry_rechecks_stage_and_never_probes_date_inside_round(config):
    game=ResetGame(config)
    observe=game.control
    phases=[]
    def tracking(frame,key,region=None):
        if key.startswith('date_change') or key.startswith('login_reward') or key=='daily_signin_confirm':
            phases.append(frame['state'])
        return observe(frame,key,region)
    game.control=tracking
    def change_stage(g,key):
        if key=='daily_signin_confirm':g.current_stage=3
    game.on_click=change_stage
    final=run(game,2)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    assert game.actions.count('stage_confirm')==1 and game.current_stage==1
    assert not set(phases)&{'battle','map','node','event_options','prepare','contract','recruit'}


def test_date_change_waits_for_selector_to_return_to_lobby(config, monkeypatch):
    game=ResetGame(config);game.state='hall';game.current_stage=3;game.stage_scrolled=False
    flow=prepared(game)
    from e7auto.features.chaos.state import RoundState
    flow.state=RoundState.for_lineup(config.chaos.lineup)
    def late_popup(g,key):
        if key=='open_stage' and not g.reset_fired:
            g.reset_due=g.clock.now+.2
    game.on_click=late_popup
    observe=game.control;panel_dates=[];lobby_dates=[]
    def control(frame,key,region=None):
        if key=='date_change_title':
            if frame['state']=='stage_panel':panel_dates.append(frame)
            if frame['state'] in ('hall','date'):lobby_dates.append(frame)
        return observe(frame,key,region)
    monkeypatch.setattr(game,'control',control)
    flow._start_round()
    assert game.state=='map' and game.current_stage==1
    assert game.actions.count('daily_signin_confirm')==1
    assert game.actions.count('stage_confirm')==1 and game.actions.count('start_exploration')==1
    assert panel_dates==[] and lobby_dates
    assert game.actions.index('stage_confirm') < game.actions.index('daily_signin_confirm') < game.actions.index('start_exploration')
