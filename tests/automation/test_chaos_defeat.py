
from e7auto.runtime.stop_control import StopController
"""Confirmed battle defeat unwinds the round, closes settlement and restarts."""
from dataclasses import replace

import pytest

from e7auto.bootstrap import AutomationSession
from e7auto.core.domain import StopReason
from tests.helpers.chaos_game import ChaosGame, chaos_config


class DefeatGame(ChaosGame):
    def __init__(self, *, losses=1, defeat_kind='normal', defeat_floor=1,
                 stuck=None, network_after=None):
        config = chaos_config()
        config = replace(config, chaos=replace(config.chaos, timing=dict(
            config.chaos.timing, page_timeout_ms=4000)))
        super().__init__(config, network_after=network_after, event_type='event_trap')
        self.losses = losses
        self.defeat_kind = defeat_kind
        self.defeat_floor = defeat_floor
        self.failed_round = False
        self.stuck = stuck
        self.failure_reads = []
        self.click_times = []
        self.round_starts = 0

    def capture_client(self, window, bounds):
        if (self.state=='battle' and self.clock.now-self.battle_started>=2
                and self.losses and self.kind==self.defeat_kind and self.floor==self.defeat_floor):
            self.fail_battle = True
            self.losses -= 1
            self.failed_round = True
        frame = super().capture_client(window,bounds)
        self.fail_battle = False
        return frame

    def control(self, frame, key, region=None):
        hit = super().control(frame,key,region)
        if key=='failed':
            self.failure_reads.append((self.clock.now,frame['state'],bool(hit)))
        return hit

    def _visible(self):
        if self.state=='blank':return []
        visible = super()._visible()
        if self.failed_round and self.state=='journey':
            if self.stuck=='result':return ['journey_close']
            if self.stuck=='close':return ['journey_result']
        return visible

    def click(self, window, point):
        key = self.points.get(point)
        if not self.network_remaining and key=='failed':
            assert self.state=='failed'
            self.actions.append(key)
            self.click_times.append((key,self.clock.now))
            if self.stuck!='failed':self.state='journey'
            if self.network_after==key and not self.network_injected:
                self.network_remaining,self.network_injected=4,True
            if self.on_click:self.on_click(self,key)
            return
        lost_close = not self.network_remaining and key=='journey_close' and self.failed_round
        starting = not self.network_remaining and key=='start_exploration'
        super().click(window,point)
        if lost_close:
            self.completed -= 1
            self.failed_round = False
            if self.stuck=='lobby':self.state='blank'
        if starting:self.round_starts+=1


def session_for(game):
    snapshots=[]
    session=AutomationSession(game.config,game.dependencies(),StopController(),snapshots.append)
    return session,snapshots


@pytest.mark.parametrize('kind,floor',[('normal',1),('elite',1),('boss',1),('boss',2),('boss',3),('event',1)])
def test_defeat_restarts_all_battle_types_without_counting_failed_round(kind,floor):
    game=DefeatGame(defeat_kind=kind,defeat_floor=floor)
    session,snapshots=session_for(game)
    final=session.run_chaos(1)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    assert final.rounds_completed==game.completed==1 and game.round_starts==2
    assert final.defeats == 1
    assert game.state=='home'
    assert game.actions.count('failed')==1 and game.actions.count('journey_close')==2
    fail=game.actions.index('failed');close=game.actions.index('journey_close')
    assert close==fail+1
    assert game.actions[close+1]=='start_exploration'
    reads=[t for t,_,hit in game.failure_reads if hit]
    assert len(reads)==3 and reads[-1]-reads[0]>=.2-1e-6
    assert game.click_times[0][1]==reads[-1]
    assert all(s.rounds_completed<=1 for s in snapshots)
    assert next(s for s in snapshots if s.defeats).rounds_completed == 0


def test_repeated_failures_do_not_consume_success_target_or_reuse_round_state():
    game=DefeatGame(losses=2,defeat_kind='boss',defeat_floor=2)
    session,_=session_for(game)
    final=session.run_chaos(2)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    assert game.round_starts==4 and final.rounds_completed==game.completed==2
    assert final.defeats == 2
    assert game.actions.count('failed')==2
    assert game.actions.count('enable_auto')==4
    # Failed second-floor bosses never grant the fifth hero's recruitment ticket.
    assert game.actions.count('recruit_confirm')==18


@pytest.mark.parametrize('stuck',['failed','result','close','lobby'])
def test_failed_recovery_requires_each_destination_without_reclick_or_restart(stuck):
    game=DefeatGame(stuck=stuck)
    session,_=session_for(game)
    final=session.run_chaos(1)
    assert final.stop_reason is StopReason.RECOGNITION_TIMEOUT
    assert final.rounds_completed==0 and game.round_starts==1
    assert final.defeats == 1
    assert game.actions.count('failed') == (2 if stuck == 'failed' else 1)
    assert game.actions.count('journey_close')==(1 if stuck=='lobby' else 0)


@pytest.mark.parametrize('after',['start_battle','failed','journey_close'])
def test_reconnect_during_defeat_recovery_does_not_replay_inputs(after):
    game=DefeatGame(network_after=after)
    session,_=session_for(game)
    final=session.run_chaos(1)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    assert game.actions.count('network_retry')==4
    assert game.actions.count('failed')==1 and game.round_starts==2
    assert final.rounds_completed==1
    assert final.defeats == 1


@pytest.mark.parametrize('boundary',['confirming','dismissed','closed'])
def test_manual_stop_stops_without_followup_input_or_completed_round(boundary):
    game=DefeatGame()
    session,_=session_for(game)
    cutoff=[]
    def capture(g):
        hits=sum(hit for _,_,hit in g.failure_reads)
        reached=(boundary=='confirming' and g.state=='failed' and hits==1
                 or boundary=='dismissed' and 'failed' in g.actions
                 or boundary=='closed' and 'journey_close' in g.actions)
        if reached and not cutoff:
            cutoff.append(len(g.actions));session.request_stop()
    game.on_capture=capture
    final=session.run_chaos(1)
    assert final.stop_reason is StopReason.MANUAL_STOP
    assert len(game.actions)==cutoff[0] and final.rounds_completed==0
    assert final.defeats == (0 if boundary == 'confirming' else 1)


def test_network_recovery_resets_failed_stability(monkeypatch):
    game=DefeatGame()
    session,_=session_for(game)
    def capture(g):
        if sum(hit for _,_,hit in g.failure_reads)==2 and not g.network_injected:
            g.network_remaining,g.network_injected=1,True
    game.on_capture=capture
    final=session.run_chaos(1)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    hits=[t for t,_,hit in game.failure_reads if hit]
    assert len(hits)==5 and hits[2]-hits[1]>=10
    assert final.defeats == 1


@pytest.mark.parametrize('count',[1,2])
def test_transient_failed_match_never_authorizes_dismissal(monkeypatch,count):
    game=DefeatGame(losses=0)
    original=game.control
    transient=[]
    def detect(frame,key,region=None):
        if key=='failed' and frame['state']=='battle' and len(transient)<count:
            transient.append(game.clock.now)
            return game._hit(key,frame)
        return original(frame,key,region)
    monkeypatch.setattr(game,'control',detect)
    session,_=session_for(game)
    final=session.run_chaos(1)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    assert 'failed' not in game.actions and game.round_starts==1
    assert final.defeats == 0


def test_failed_is_not_probed_outside_pending_battle(monkeypatch):
    game=DefeatGame(losses=0)
    session,_=session_for(game)
    final=session.run_chaos(1)
    assert final.stop_reason is StopReason.CHAOS_COMPLETE
    # The first success frames can still be checked until stable confirmation.
    assert all(state in ('battle','settlement','journey','event_reward') for _,state,_ in game.failure_reads)
    assert game.failure_reads
