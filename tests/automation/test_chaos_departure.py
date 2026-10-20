"""Departure, lineup-specific recovery and irreversible submission boundaries."""
from dataclasses import replace
import struct
import pytest
from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


class DepartureGame(ChaosGame):
    knight_selected = False

    def _visible(self):
        if self.state == 'depart_select':
            return ['depart_title'] + ['hero_'+h for h in self.ranks] + (
                ['hero_selected_border', 'selected_name_'+self.hero, 'depart_confirm'] if self.selection else [])
        if self.state == 'depart_missing':return ['depart_missing', 'event_reward_close']
        if self.state == 'phantom_reward':return ['event_reward_close']
        if self.state == 'rose_recruited':return ['recruited_shadow_rose_all', 'continue_exploration']
        keys=super()._visible()
        if self.state == 'recruit' and self.filter_open and self.config.chaos.lineup.key == 'budget':
            keys=[k for k in keys if k not in ('filter_icon_thief_filter','filter_thief_selected')]
            keys.append('filter_knight_selected' if self.knight_selected else 'filter_icon_knight_filter')
        if self.state == 'prepare' and 'arowell' not in self.ranks:
            keys=[k for k in keys if k not in ('bench_teacher','bench_arowell')]
        return keys

    def event_card_changed(self, frame, previous):
        return (previous=='depart_missing' and frame['state']=='phantom_reward') or super().event_card_changed(frame,previous)

    def click(self, window, point):
        if self.network_remaining:return super().click(window,point)
        key=self.points.get(point)
        if key=='event_red_phantom':self.state='depart_select';self.selection=False
        elif key=='depart_confirm':
            assert self.state=='depart_select' and self.selection
            assert self.hero==self.config.chaos.lineup.departure_hero
            del self.ranks[self.hero];self.recruited.discard(self.hero);self.state='depart_missing'
        elif key=='event_reward_close' and self.state=='depart_missing':self.state='phantom_reward'
        elif key=='event_reward_close' and self.state=='phantom_reward':self.state='text'
        elif key=='filter_icon_knight_filter':self.knight_selected=True
        elif key=='recruit_confirm' and self.ticket=='all' and self.hero=='shadow_rose':
            assert self.selection and self.knight_selected and self.attributes=={'dark'}
            assert 'shadow_rose' not in self.recruited
            self.recruited.add('shadow_rose');self.ranks['shadow_rose']=1;self.state='rose_recruited';self.selection=False
        elif key=='continue_exploration' and self.state=='rose_recruited':self._map_next()
        else:
            super().click(window,point)
            if key=='ticket_all' and self.config.chaos.lineup.key=='budget':self.hero='shadow_rose'
            return
        self.actions.append(key)
        if key==self.network_after and not self.network_injected:self.network_remaining=1;self.network_injected=True
        if self.on_click:self.on_click(self,key)


@pytest.fixture(scope='module')
def configs():return {key:chaos_config(key) for key in ('dark_twins','budget')}


def setup(configs, lineup='dark_twins', floor=2, **kwargs):
    game=DepartureGame(configs[lineup],event_type='event_red_phantom',**kwargs)
    stop=StopController();flow=ChaosFlow(game.config,game.dependencies(),stop,SnapshotPublisher(RuntimeSnapshot.chaos_run('departure',1),lambda _:None))
    flow.runtime.prepare();flow.state=RoundState.for_lineup(game.config.chaos.lineup)
    flow.state.floor=game.floor=floor;flow.state.node_kind=game.kind=game.state='event';game.recruited=set(game.ranks)
    return game,flow,stop


def voucher(game, flow, floor=2):
    game.floor=flow.state.floor=flow.state.battle_floor=floor
    game.state,game.kind='claimed','boss';flow._voucher()


@pytest.mark.parametrize('lineup',['dark_twins','budget'])
@pytest.mark.parametrize('floor',[1,2,3])
def test_departure_confirms_target_missing_two_distinct_cards_and_map(configs,lineup,floor):
    game,flow,_=setup(configs,lineup,floor);hero=flow.settings.lineup.departure_hero
    scene=flow.settings.scenes['event_red_phantom'];size=struct.unpack('>II',game.config.template_paths[scene.template_key].read_bytes()[16:24])
    assert tuple(round(o+n*a) for o,n,a in zip((220,1069),size,scene.action))==(480,1090)
    flow._event()
    assert game.actions==['event_red_phantom','hero_'+hero,'depart_confirm','event_reward_close','event_reward_close','text_continue']
    assert hero not in flow.state.ranks and hero in flow.state.departed_heroes
    assert game.state=='map' and game.floor==flow.state.floor==floor
    assert flow.state.reward_stage=='event_closed'
    assert len([d for k,d in flow.runtime.deps.logger.events if k=='chaos_hero_departed'])==1


@pytest.mark.parametrize('stage',['before_teacher','pending_swap','after_swap'])
def test_arowell_departure_never_looks_for_removed_replacement(configs,stage,monkeypatch):
    game,flow,_=setup(configs)
    if stage!='before_teacher':
        game.ranks['teacher']=flow.state.ranks['teacher']=1;game.recruited.add('teacher')
        flow.state.pending_teacher=stage=='pending_swap';game.swapped=stage=='after_swap'
    flow._event();assert not flow.state.pending_teacher
    if stage=='before_teacher':
        game.state,game.kind='prepare','normal';flow._fight('prepare_normal',('victory',))
        voucher(game,flow)
        assert flow.state.ranks['teacher']==1 and not flow.state.pending_teacher
    def no_swap():raise AssertionError('removed Arowell cannot be replaced')
    monkeypatch.setattr(flow,'_swap_teacher',no_swap)
    game.state,game.kind='prepare','normal';flow._fight('prepare_normal',('victory',))
    assert 'replace_button' not in game.actions and 'bench_teacher' not in game.actions


@pytest.mark.parametrize('floor',[1,2])
def test_budget_recruits_departed_rose_once_at_second_boss_resets_rank(configs,floor):
    game,flow,_=setup(configs,'budget',floor);game.ranks['shadow_rose']=flow.state.ranks['shadow_rose']=4
    flow._event();flow.state.ranks['xiaochun']=5
    assert flow.state.next_rank_hero()=='teacher'
    if floor==1:
        voucher(game,flow,1);assert 'ticket_all' not in game.actions
        assert not flow.state.second_floor_voucher_done
    voucher(game,flow)
    assert game.state=='rose_recruited' and flow.state.ranks['shadow_rose']==1
    assert 'shadow_rose' not in flow.state.departed_heroes and flow.state.second_floor_voucher_done
    assert flow.state.next_rank_hero()=='shadow_rose' and not flow.state.pending_teacher
    assert 'filter_icon_knight_filter' in game.actions and 'filter_icon_thief_filter' not in game.actions
    actions=list(game.actions);flow._voucher();assert game.actions==actions
    flow._continue(3,allow_unclaimed=True);assert game.state=='map' and game.floor==3


@pytest.mark.parametrize('departed,spent,floor',[(False,False,2),(True,True,2),(True,False,3)])
def test_no_restore_without_eligible_unused_second_floor_voucher(configs,departed,spent,floor):
    game,flow,_=setup(configs,'budget',floor)
    if departed:flow._event()
    flow.state.second_floor_voucher_done=spent;voucher(game,flow,floor)
    assert 'ticket_all' not in game.actions


@pytest.mark.parametrize('after',['depart_confirm','event_reward_close','recruit_confirm'])
def test_recovery_does_not_repeat_departure_or_recruitment(configs,after):
    game,flow,_=setup(configs,'budget',network_after=after);flow._event();voucher(game,flow)
    assert game.actions.count('depart_confirm')==game.actions.count('recruit_confirm')==1
    assert game.actions.count('event_reward_close')==2 and game.actions.count('network_retry')==1
    assert flow.state.ranks['shadow_rose']==1


@pytest.mark.parametrize('missing', [('depart_title',), ('selected_name_arowell','hero_selected_border'),
                                     ('depart_confirm',), ('depart_missing',)])
def test_missing_departure_evidence_stops_without_inventing_roster_change(configs,missing,monkeypatch):
    game,flow,_=setup(configs);original=game.control
    monkeypatch.setattr(game,'control',lambda f,k,region=None:None if k in missing else original(f,k,region))
    with pytest.raises(StopExecution):flow._event()
    assert 'arowell' in flow.state.ranks and not flow.state.departed_heroes
    assert game.actions.count('depart_confirm')==int('depart_missing' in missing)
    assert 'event_reward_close' not in game.actions


@pytest.mark.parametrize('missing',['selected_name_arowell','hero_selected_border'])
def test_departure_accepts_either_selection_evidence(configs,missing,monkeypatch):
    game,flow,_=setup(configs);original=game.control
    monkeypatch.setattr(game,'control',lambda f,k,region=None:None if k==missing else original(f,k,region))
    flow._event()
    assert game.actions.count('depart_confirm')==1
    assert 'arowell' in flow.state.departed_heroes and 'arowell' not in flow.state.ranks


def test_conflicting_left_name_blocks_departure_even_with_target_border(configs,monkeypatch):
    game,flow,_=setup(configs);original=game.control
    def control(frame,key,region=None):
        if frame['state']=='depart_select' and frame['selected'] and key=='selected_name_twins':
            return game._hit(key,frame)
        return original(frame,key,region)
    monkeypatch.setattr(game,'control',control)
    with pytest.raises(StopExecution):flow._event()
    assert 'depart_confirm' not in game.actions and not flow.state.departed_heroes


@pytest.mark.parametrize('border_only',[False,True])
def test_departure_recovery_rebuilds_three_joint_frames(configs,monkeypatch,border_only):
    game,flow,_=setup(configs);original=game.control;probes=[]
    def control(frame,key,region=None):
        if border_only and key=='selected_name_arowell':return None
        hit=original(frame,key,region)
        if key=='depart_confirm' and hit:
            probes.append(flow.runtime.network_recovery_generation)
            if len(probes)==2:game.network_remaining=1
        return hit
    monkeypatch.setattr(game,'control',control)
    flow._event()
    assert probes==[0,0,1,1,1]
    assert game.actions.count('depart_confirm')==1


@pytest.mark.parametrize('at',['depart_confirm','event_reward_close'])
def test_manual_stop_after_submission_does_not_replay(configs,at):
    game,flow,stop=setup(configs);cut=[]
    def click(g,k):
        if k==at:cut.append(len(g.actions));stop.request(StopReason.MANUAL_STOP)
    game.on_click=click
    with pytest.raises(StopExecution):flow._event()
    assert len(game.actions)==cut[0] and game.actions.count('depart_confirm')==1


def test_target_already_absent_stops_before_selecting_event(configs):
    game,flow,_=setup(configs);flow.state.record_departure('arowell')
    with pytest.raises(StopExecution):flow._event()
    assert not game.actions


def test_same_missing_card_cannot_count_as_second_card(configs):
    game,flow,_=setup(configs)
    game.on_click=lambda g,k:setattr(g,'state','depart_missing') if k=='event_reward_close' else None
    with pytest.raises(StopExecution):flow._event()
    assert game.actions.count('event_reward_close')==flow.settings.timing['event_close_max_attempts']
    assert 'text_continue' not in game.actions and flow.state.reward_stage=='hero_departed'


@pytest.mark.parametrize('missing',['ticket_all','recruited_shadow_rose_all'])
def test_restore_requires_ticket_and_correct_completion_before_state_update(configs,missing,monkeypatch):
    game,flow,_=setup(configs,'budget');flow._event();original=game.control
    monkeypatch.setattr(game,'control',lambda f,k,region=None:None if k==missing else original(f,k,region))
    with pytest.raises(StopExecution):voucher(game,flow)
    assert 'shadow_rose' not in flow.state.ranks and 'shadow_rose' in flow.state.departed_heroes
    assert not flow.state.second_floor_voucher_done
    assert game.actions.count('recruit_confirm')==int(missing!='ticket_all')


def test_new_round_discards_departure_and_voucher_state(configs):
    game,flow,_=setup(configs,'budget');flow._event();voucher(game,flow)
    fresh=RoundState.for_lineup(flow.settings.lineup)
    assert not fresh.departed_heroes and not fresh.second_floor_voucher_done
    assert set(fresh.ranks)==set(h['key'] for h in flow.settings.heroes)
