"""Boss stories are optional; skip is single-frame and settlement stays separate."""
from dataclasses import replace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


class StoryGame(ChaosGame):
    def __init__(self, config, *, segments=2, with_levelup=True, stuck=None,
                 transition_delay=.6, initial_prompt=False, **kwargs):
        super().__init__(config, **kwargs)
        self.remaining = segments
        self.with_levelup = with_levelup
        self.stuck = stuck
        self.transition_delay = transition_delay
        self.initial_prompt = initial_prompt
        self.transition_at = None
        self.observations = []
        self.submitted = []
        self.prompt_button = True
        self.blank_story = False

    def capture_client(self, window, bounds):
        if self.state == 'battle' and self.clock.now-self.battle_started >= 2 and self.remaining:
            self.state = 'story_prompt' if self.initial_prompt else 'story'
        if self.state == 'story_transition' and self.clock.now-self.transition_at >= self.transition_delay:
            self.state = ('story' if self.remaining else 'journey' if self.floor == 3
                          else 'story_levelup' if self.with_levelup else 'settlement')
        return super().capture_client(window, bounds)

    def _visible(self):
        if self.state == 'story':
            return [] if self.blank_story else ['story_skip']
        if self.state == 'story_prompt':
            # Both a background skip and a covered result must be ignored.
            return ['story_skip_prompt', 'story_skip', 'boss_choose_reward'] + (['story_skip_confirm'] if self.prompt_button else [])
        if self.state == 'story_transition':
            return []
        if self.state == 'story_levelup':
            return ['levelup', 'levelup_close', 'boss_choose_reward']
        return super()._visible()

    def control(self, frame, key, region=None):
        hit = super().control(frame, key, region)
        self.observations.append((key, self.clock.now, bool(hit), frame['state']))
        return hit

    def click(self, window, point):
        key = self.points.get(point)
        if self.network_remaining or key not in ('story_skip', 'story_skip_confirm', 'levelup_close'):
            return super().click(window, point)
        self.actions.append(key)
        self.submitted.append((key, self.clock.now))
        if key == 'story_skip':
            assert self.state == 'story'
            if self.stuck != key:
                self.state = 'story_prompt'
        elif key == 'story_skip_confirm':
            assert self.state == 'story_prompt' and self.prompt_button
            if self.stuck != key:
                self.remaining -= 1
                self.state, self.transition_at = 'story_transition', self.clock.now
        else:
            assert self.state == 'story_levelup'
            self.state = 'settlement'
        if key == self.network_after and not self.network_injected:
            self.network_remaining, self.network_injected = 4, True
        if self.on_click:
            self.on_click(self, key)


def setup_story(*, floor=1, auto_confirmed=True, timing=None, kind='boss', **kwargs):
    config = chaos_config()
    if timing:
        config = replace(config, chaos=replace(config.chaos, timing=dict(config.chaos.timing, **timing)))
    game = StoryGame(config, **kwargs)
    stop = StopController()
    flow = ChaosFlow(config, game.dependencies(), stop,
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('story', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState(floor=floor, battle_floor=floor, node_kind=kind, auto_confirmed=auto_confirmed)
    game.state, game.kind, game.floor, game.auto = 'prepare', kind, floor, True
    return game, flow, stop


@pytest.mark.parametrize('floor', [1, 2, 3])
@pytest.mark.parametrize('segments', [0, 1, 3])
def test_optional_and_consecutive_story_segments_rejoin_existing_floor_flow(floor, segments):
    game, flow, _ = setup_story(floor=floor, segments=segments)
    ended = flow._battle('boss')
    assert ended == (floor == 3)
    assert game.state == ('hall' if floor == 3 else 'map')
    assert game.actions.count('story_skip') == game.actions.count('story_skip_confirm') == segments
    if segments:
        first_hit = next(t for key,t,hit,_ in game.observations if key=='story_skip' and hit)
        assert next(t for key,t in game.submitted if key=='story_skip') == first_hit
    if floor < 3:
        result_hit = next(t for key,t,hit,state in game.observations
                          if key in ('levelup','boss_choose_reward') and hit and state!='story_prompt')
        closed = [t for key,t in game.submitted if key=='levelup_close']
        if segments:
            assert len(closed)==1
            assert .2-1e-6 <= closed[0]-result_hit < .4
        assert not any(key.startswith('story_') and t>result_hit for key,t,_,_ in game.observations)
    else:
        assert 'levelup_close' not in game.actions


@pytest.mark.parametrize('floor', [1, 3])
def test_story_found_during_auto_confirmation_never_clicks_auto(floor):
    game, flow, _ = setup_story(floor=floor, auto_confirmed=False, network_after='start_battle')
    flow._battle('boss')
    assert 'enable_auto' not in game.actions
    assert game.actions.count('story_skip_confirm') == 2


def test_single_unstable_skip_detection_authorizes_first_click(monkeypatch):
    game, flow, _ = setup_story(segments=1)
    original = game.control
    count = 0
    def detect(frame, key, region=None):
        nonlocal count
        hit = original(frame,key,region)
        if key=='story_skip' and hit:
            count += 1
            # A second sample cannot form stable evidence; the first must suffice.
            return hit if count==1 else None
        return hit
    monkeypatch.setattr(game,'control',detect)
    flow._battle('boss')
    assert count == 1 and game.actions.count('story_skip') == 1


def test_existing_prompt_is_handled_before_background_skip_or_result():
    game, flow, _ = setup_story(segments=1, initial_prompt=True)
    flow._battle('boss')
    assert 'story_skip' not in game.actions
    assert game.actions.count('story_skip_confirm')==1
    probes=[x for x in game.observations if x[0]=='story_skip_confirm' and x[2]]
    assert len(probes)>=3 and probes[2][1]-probes[0][1]>=.2-1e-6


def test_no_skip_during_transition_is_not_a_completion_signal():
    game, flow, _ = setup_story(segments=2, transition_delay=3)
    flow._battle('boss')
    confirms=[t for key,t in game.submitted if key=='story_skip_confirm']
    assert len(confirms)==2 and confirms[1]-confirms[0]>=3
    assert game.actions.index('levelup_close')>max(i for i,key in enumerate(game.actions) if key=='story_skip_confirm')


@pytest.mark.parametrize('stuck', ['story_skip','story_skip_confirm'])
def test_unresponsive_controls_have_bounded_stable_retries(stuck):
    game, flow, _ = setup_story(stuck=stuck)
    with pytest.raises(StopExecution) as error:
        flow._battle('boss')
    assert error.value.reason is StopReason.RECOGNITION_TIMEOUT
    times=[t for key,t in game.submitted if key==stuck]
    assert len(times)==3 and all(b-a>=2-1e-6 for a,b in zip(times,times[1:]))
    assert 'boss_choose_reward' not in game.actions


def test_missing_prompt_never_authorizes_generic_confirm():
    game, flow, _ = setup_story(stuck='story_skip')
    original=game._visible
    game._visible=lambda: original()+(['story_skip_confirm'] if game.state=='story' else [])
    with pytest.raises(StopExecution):
        flow._battle('boss')
    assert 'story_skip_confirm' not in game.actions


def test_missing_confirm_button_does_not_click_behind_prompt():
    game, flow, _ = setup_story(timing={'story_timeout_ms':4000})
    game.prompt_button=False
    with pytest.raises(StopExecution):
        flow._battle('boss')
    assert game.actions==['start_battle','story_skip']


@pytest.mark.parametrize('timing,reason', [
    ({'story_timeout_ms':4000},StopReason.RECOGNITION_TIMEOUT),
    ({'battle_timeout_ms':7000},StopReason.CHAOS_BATTLE_TIMEOUT),
])
def test_unknown_transition_obeys_story_and_original_battle_deadlines(timing,reason):
    game, flow, _ = setup_story(transition_delay=999,timing=timing)
    with pytest.raises(StopExecution) as error:
        flow._battle('boss')
    assert error.value.reason is reason
    assert game.actions==['start_battle','story_skip','story_skip_confirm']


@pytest.mark.parametrize('reason',[StopReason.MANUAL_STOP])
def test_stop_after_skip_prevents_confirmation(reason):
    game, flow, stop = setup_story()
    def interrupt(g,key):
        if key=='story_skip':
            if reason is StopReason.MANUAL_STOP:stop.request(reason)
            else:g.state='failed'
    game.on_click=interrupt
    with pytest.raises(StopExecution) as error:
        flow._battle('boss')
    assert error.value.reason is reason
    assert game.actions==['start_battle','story_skip']


@pytest.mark.parametrize('network_after',['story_skip','story_skip_confirm'])
def test_network_recovery_keeps_story_progress_and_pauses_timeout(network_after):
    game, flow, _ = setup_story(segments=1,network_after=network_after,
                               timing={'story_timeout_ms':5000,'battle_timeout_ms':15000})
    flow._battle('boss')
    assert game.actions.count('network_retry')==4
    assert game.actions.count('story_skip')==game.actions.count('story_skip_confirm')==1
    assert game.clock.now-flow.runtime.active_monotonic()>=40


@pytest.mark.parametrize('kind,result_names',[('normal',('levelup','victory')),
                                           ('elite',('levelup','elite_choose_reward')),
                                           ('event',('event_reward_close',))])
def test_non_boss_battles_do_not_probe_story_scenes(kind,result_names):
    game, flow, _ = setup_story(kind=kind,segments=0,event_type='event_trap')
    flow._fight('prepare_'+kind,result_names)
    assert not any(key.startswith('story_') for key,_,_,_ in game.observations)


def test_failed_detection_ends_at_first_story_signal(monkeypatch):
    game, flow, _ = setup_story(segments=2)
    original = game.control
    story_seen = False
    def detect(frame, key, region=None):
        nonlocal story_seen
        assert not (story_seen and key == 'failed')
        hit = original(frame, key, region)
        if key == 'story_skip' and hit is not None:
            story_seen = True
        return hit
    monkeypatch.setattr(game, 'control', detect)
    flow._battle('boss')
    assert story_seen and game.actions.count('story_skip_confirm') == 2
