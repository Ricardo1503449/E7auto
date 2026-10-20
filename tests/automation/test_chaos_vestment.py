import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.events.dispatcher import HANDLERS
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config


def vestment_flow(**kwargs):
    game=ChaosGame(chaos_config(),event_type='event_vestment',**kwargs)
    stop=StopController()
    flow=ChaosFlow(game.config,game.dependencies(),stop,SnapshotPublisher(RuntimeSnapshot.chaos_run('vestment',1),lambda _:None))
    flow.runtime.prepare();flow.state=RoundState(node_kind='event')
    game.kind='event';game.state='event'
    return game,flow,stop


@pytest.mark.parametrize('has_loot', [False, True])
def test_subchoice_is_handled_locally_then_returns_to_original_map(has_loot):
    game,flow,_=vestment_flow(vestment_has_loot=has_loot);flow._event()
    key = 'event_vestment_with_loot' if has_loot else 'event_vestment_no_loot'
    assert game.actions==['event_vestment',key,'text_continue']
    assert key not in HANDLERS and key not in game.config.chaos.event_recognition['templates']
    if has_loot:
        # Registered dimensions and calibrated crop origin; no repeated image scan.
        import struct
        scene = game.config.chaos.scenes[key]
        width, height = struct.unpack('>II', game.config.template_paths[scene.template_key].read_bytes()[16:24])
        assert (round(308 + width * scene.action[0]), round(1069 + height * scene.action[1])) == (480, 1090)
    assert game.state=='map' and game.floor==flow.state.floor==1
    assert not flow.state.counted and not game.recruited


@pytest.mark.parametrize('has_loot', [False, True])
@pytest.mark.parametrize('stage', ['first', 'second'])
def test_reconnection_keeps_current_stage_without_replaying_previous_choice(has_loot, stage):
    key = 'event_vestment_with_loot' if has_loot else 'event_vestment_no_loot'
    after = 'event_vestment' if stage == 'first' else key
    game,flow,_=vestment_flow(network_after=after,vestment_has_loot=has_loot);flow._event()
    assert game.actions.count('network_retry')==4
    for action in ['event_vestment',key,'text_continue']:assert game.actions.count(action)==1
    assert game.state=='map'


def test_unknown_second_stage_stops_without_selecting_any_subchoice(monkeypatch):
    game,flow,_=vestment_flow(vestment_has_loot=True);original=game._visible
    monkeypatch.setattr(game,'_visible',lambda:[] if game.state=='vestment_subchoice' else original())
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.CHAOS_UNKNOWN_CONTENT
    assert game.actions==['event_vestment']


@pytest.mark.parametrize('has_loot', [False, True])
def test_subchoice_cannot_be_used_as_a_global_event_identity(has_loot):
    game,flow,_=vestment_flow(vestment_has_loot=has_loot);game.state='vestment_subchoice'
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.CHAOS_UNKNOWN_CONTENT and game.actions==[]


def test_other_event_during_second_stage_does_not_trigger_redispatch(monkeypatch):
    game,flow,_=vestment_flow();original=game._visible
    monkeypatch.setattr(game,'_visible',lambda:['event_tablet'] if game.state=='vestment_subchoice' else original())
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.CHAOS_UNKNOWN_CONTENT
    assert game.actions==['event_vestment']


@pytest.mark.parametrize('has_loot', [False, True])
@pytest.mark.parametrize('stage', ['first', 'second'])
def test_manual_stop_after_either_choice_prevents_later_steps(has_loot, stage):
    key = 'event_vestment_with_loot' if has_loot else 'event_vestment_no_loot'
    at = 'event_vestment' if stage == 'first' else key
    game,flow,stop=vestment_flow(vestment_has_loot=has_loot);cut=[]
    def clicked(g,key):
        if key==at:cut.append(len(g.actions));stop.request(StopReason.MANUAL_STOP)
    game.on_click=clicked
    with pytest.raises(StopExecution) as error:flow._event()
    assert error.value.reason==StopReason.MANUAL_STOP and len(game.actions)==cut[0]
