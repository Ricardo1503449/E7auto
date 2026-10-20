from dataclasses import replace
import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config
from tests.helpers.chaos_images import loot_frame
from e7auto.features.chaos.vision import ChaosVision
from e7auto.resources.templates import TemplateRepository


@pytest.mark.parametrize('kind', ['elite', 'boss', 'supply'])
@pytest.mark.parametrize('battle_ms,supply_ms', [(1000, 3000), (1700, 1500), (1000, 2300)])
def test_loot_entry_wait_is_configured_total_delay_before_first_identity_query(monkeypatch, kind, battle_ms, supply_ms):
    config = chaos_config()
    config = replace(config, chaos=replace(config.chaos, timing=dict(
        config.chaos.timing, loot_entry_settle_ms=battle_ms, supply_loot_entry_settle_ms=supply_ms)))
    settle_ms = supply_ms if kind == 'supply' else battle_ms
    game = ChaosGame(config)
    flow = ChaosFlow(config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('loot-delay', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState(floor=1, battle_floor=1)
    game.state, game.kind = ('supply' if kind == 'supply' else 'settlement'), kind
    clicked_at = []
    game.on_click = lambda _game, key: clicked_at.append(flow.runtime.active_monotonic())
    monkeypatch.setattr(flow, '_fight', lambda *args: flow._control(kind+'_choose_reward'))
    class Observed(Exception):
        pass
    def first_query(*, reward_key=None):
        assert reward_key == (None if kind == 'supply' else kind+'_choose_reward')
        assert len(clicked_at) == 1
        assert flow.runtime.active_monotonic()-clicked_at[0] == pytest.approx(settle_ms/1000)
        raise Observed
    monkeypatch.setattr(flow, '_choose_loot', first_query)
    with pytest.raises(Observed):
        flow._supply() if kind == 'supply' else flow._battle(kind)


@pytest.mark.parametrize('interruption', ['network', 'manual'])
def test_loot_entry_delay_observes_recovery_and_stop_without_replaying_claim(monkeypatch, interruption):
    config = chaos_config()
    game = ChaosGame(config, network_after='supply_get_loot' if interruption == 'network' else None)
    stop = StopController()
    flow = ChaosFlow(config, game.dependencies(), stop,
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('loot-delay-guard', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState()
    game.state, game.kind = 'supply', 'supply'
    started = []
    def clicked(_game, key):
        if key == 'supply_get_loot':
            started.append(flow.runtime.active_monotonic())
            if interruption == 'manual':
                stop.request(StopReason.MANUAL_STOP)
    game.on_click = clicked
    class Observed(Exception):
        pass
    def first_query():
        assert interruption == 'network'
        assert flow.runtime.active_monotonic()-started[0] == pytest.approx(config.chaos.timing['supply_loot_entry_settle_ms']/1000)
        assert flow.runtime.network_recovery_generation == 1
        raise Observed
    monkeypatch.setattr(flow, '_choose_loot', first_query)
    with pytest.raises(StopExecution if interruption == 'manual' else Observed) as caught:
        flow._supply()
    if interruption == 'manual':
        assert caught.value.reason is StopReason.MANUAL_STOP
    assert game.actions.count('supply_get_loot') == 1


@pytest.mark.parametrize('failure',[None,'unknown','manual'])
def test_loot_session_ends_before_input_and_logs_once_on_every_exit(monkeypatch,failure):
    config=chaos_config()
    config=replace(config,chaos=replace(config.chaos,timing=dict(config.chaos.timing,page_timeout_ms=500)))
    game=ChaosGame(config,unknown_loot=failure=='unknown')
    stop=StopController()
    flow=ChaosFlow(config,game.dependencies(),stop,SnapshotPublisher(RuntimeSnapshot.chaos_run('loot-session',1),lambda _:None))
    flow.runtime.prepare()
    flow.state=RoundState()
    game.state='loot';game.kind='elite'
    session={'active':False,'ended':0,'samples':[]}
    def begin():session['active']=True
    def end():
        session['active']=False;session['ended']+=1
        return {'frames':len(session['samples'])}
    original=game.loot_cards
    def detect(frame):
        assert session['active']
        session['samples'].append(game.captures)
        if failure=='manual':stop.request(StopReason.MANUAL_STOP)
        return original(frame)
    def clicked(g,key):
        assert not session['active']
        if key=='loot_ominous_crystal':assert len(session['samples'])==3
    monkeypatch.setattr(game,'begin_loot_recognition',begin)
    monkeypatch.setattr(game,'end_loot_recognition',end)
    monkeypatch.setattr(game,'loot_cards',detect)
    game.on_click=clicked
    if failure:
        with pytest.raises(StopExecution):flow._choose_loot()
        assert 'loot_confirm' not in game.actions
    else:
        flow._choose_loot()
        assert game.actions.count('loot_confirm')==1
        assert len(set(session['samples']))==3
    assert session['ended']==1
    summaries=[fields for event,fields in flow.runtime.deps.logger.events if event=='chaos_loot_recognition']
    assert len(summaries)==1
    assert summaries[0]['outcome']==('confirmed' if failure is None else StopReason.CHAOS_UNKNOWN_CONTENT.value if failure=='unknown' else StopReason.MANUAL_STOP.value)


@pytest.mark.parametrize('condition', ['ready', 'marker_flicker', 'button_flicker', 'wrong_card', 'missing_button'])
def test_selected_loot_checks_marker_and_button_immediately_without_identity_recheck(monkeypatch, condition):
    config = chaos_config()
    config = replace(config, chaos=replace(config.chaos, timing=dict(config.chaos.timing, page_timeout_ms=600)))
    game = ChaosGame(config, wrong_selection=condition == 'wrong_card')
    flow = ChaosFlow(config, game.dependencies(), StopController(),
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('loot-confirm', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState()
    game.state, game.kind = 'loot', 'elite'
    clicks, samples, button_frames = {}, [], []
    game.on_click = lambda _game, key: clicks.update({key: game.clock.now})
    original = game.control

    def detect(frame, key, region=None):
        assert key not in config.chaos.loot_priorities, 'selected item must not be recognized again'
        if key == 'loot_selected':
            samples.append((game.clock.now, game.captures))
            assert region is not None
            if condition == 'marker_flicker' and len(samples) == 2:
                return None
        if key == 'loot_confirm':
            assert samples[-1][1] == game.captures
            button_frames.append(game.captures)
            if condition == 'missing_button' or (condition == 'button_flicker' and len(samples) == 2):
                return None
        return original(frame, key, region)

    monkeypatch.setattr(game, 'control', detect)
    if condition in ('wrong_card', 'missing_button'):
        with pytest.raises(StopExecution) as caught:
            flow._choose_loot()
        assert caught.value.reason is StopReason.RECOGNITION_TIMEOUT
        assert game.actions == ['loot_ominous_crystal']
        assert flow.state.reward_stage == 'selecting'
        if condition == 'wrong_card':
            assert button_frames == []
    else:
        flow._choose_loot()
        count = 3 if condition == 'ready' else 5
        assert len(samples) == len({capture for _, capture in samples}) == count
        assert clicks['loot_confirm'] - clicks['loot_ominous_crystal'] == pytest.approx((count - 1) * .1)
        assert game.actions == ['loot_ominous_crystal', 'loot_confirm']
        assert flow.state.reward_stage == 'submitted'
        assert game.clock.now - clicks['loot_confirm'] == pytest.approx(config.chaos.timing['action_settle_ms'] / 1000)
    assert samples[0][0] == clicks['loot_ominous_crystal']


@pytest.mark.parametrize('interruption', ['manual', 'network'])
def test_selected_loot_confirmation_keeps_stop_and_recovery_guards(monkeypatch, interruption):
    config = chaos_config()
    game = ChaosGame(config)
    stop = StopController()
    flow = ChaosFlow(config, game.dependencies(), stop,
                     SnapshotPublisher(RuntimeSnapshot.chaos_run('loot-confirm-guard', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState()
    game.state, game.kind = 'loot', 'elite'
    generations = []
    original = game.control

    def detect(frame, key, region=None):
        hit = original(frame, key, region)
        if key == 'loot_selected':
            generations.append(flow.runtime.network_recovery_generation)
            if len(generations) == 2:
                if interruption == 'manual':
                    stop.request(StopReason.MANUAL_STOP)
                else:
                    game.network_remaining = 1
        return hit

    monkeypatch.setattr(game, 'control', detect)
    if interruption == 'manual':
        with pytest.raises(StopExecution) as caught:
            flow._choose_loot()
        assert caught.value.reason is StopReason.MANUAL_STOP
        assert game.actions == ['loot_ominous_crystal']
    else:
        flow._choose_loot()
        assert generations == [0, 0, 1, 1, 1]
        assert game.actions == ['loot_ominous_crystal', 'network_retry', 'loot_confirm']


@pytest.mark.parametrize('stop_frame',[1,3])
def test_manual_stop_during_parallel_image_work_never_dispatches_selection_and_closes_pool(monkeypatch,stop_frame):
    config=chaos_config()
    game=ChaosGame(config)
    stop=StopController()
    templates=TemplateRepository(config)
    vision=ChaosVision(config,templates)
    frame=loot_frame(config,templates,('loot_ominous_crystal','loot_old_sword','loot_lightning'))
    for name in ('begin_loot_recognition','end_loot_recognition','close_loot_recognition'):
        monkeypatch.setattr(game,name,getattr(vision,name))
    monkeypatch.setattr(game,'loot_cards',lambda _:vision.loot_cards(frame))
    original=vision._loot._recognize_card
    def work(*args):
        if vision._loot.stats['frames']==stop_frame:
            stop.request(StopReason.MANUAL_STOP)
        return original(*args)
    monkeypatch.setattr(vision._loot,'_recognize_card',work)
    flow=ChaosFlow(config,game.dependencies(),stop,SnapshotPublisher(RuntimeSnapshot.chaos_run('parallel-stop',1),lambda _:None))
    flow.runtime.prepare()
    flow.state=RoundState();game.state='loot';game.kind='elite'
    try:
        with pytest.raises(StopExecution) as caught:flow._choose_loot()
        assert caught.value.reason==StopReason.MANUAL_STOP
        assert game.actions==[]
        pool=vision._loot._pool
        threads=tuple(pool._threads)
        summaries=[fields for event,fields in flow.runtime.deps.logger.events if event=='chaos_loot_recognition']
        assert len(summaries)==1
        assert summaries[0]['outcome']==('manual_stop' if stop_frame==1 else 'confirmed')
        assert summaries[0]['card_jobs']==3*stop_frame
    finally:
        flow.release_cached_game_frame()
    assert vision._loot._pool is None and all(not thread.is_alive() for thread in threads)
