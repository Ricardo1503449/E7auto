"""Node selection retries own one target; enter acknowledgment is sticky."""
from dataclasses import replace

import numpy as np
import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.ports import DisplayGeometry
from e7auto.core.types import ClientPoint, ClientRect, Rect, Size
from e7auto.features.chaos.flow import ChaosFlow
from e7auto.features.chaos.state import RoundState
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers.chaos_game import ChaosGame, chaos_config

SIZES = [(2322, 1306), (1536, 864)]
KINDS = ['normal', 'elite', 'boss', 'rest', 'loot', 'event', 'shop']


class NodeGame(ChaosGame):
    def __init__(self, config, size, kind, dropped):
        super().__init__(config)
        self.size, self.routes, self.dropped = size, (kind,), dropped
        self.sent, self.maps = [], []
        self.on_map = None
        self._pixels = np.zeros((size[1], size[0], 3), np.uint8)

    def capture_client(self, window, bounds):
        self._model_frame = super().capture_client(window, bounds)
        return self._pixels

    def prepare_frame(self, frame):
        return self._model_frame

    def network_connection_error(self, frame):
        return super().network_connection_error(self._model_frame)

    def network_retry(self, frame):
        return super().network_retry(self._model_frame)

    def scaled(self, hit):
        sx, sy = self.size[0]/2322, self.size[1]/1306
        b = hit.bounds
        current = replace(hit, bounds=ClientRect(round(b.x*sx), round(b.y*sy),
                                                max(1, round(b.width*sx)), max(1, round(b.height*sy))),
                          anchor=ClientPoint(round(hit.anchor.x*sx), round(hit.anchor.y*sy)))
        self.points[current.anchor] = current.key
        return current

    def _hit(self, key, frame):
        return self.scaled(super()._hit(key, frame))

    def map_view(self, frame):
        view = super().map_view(frame)
        if view is not None:
            view = replace(view, nodes=tuple(replace(n, detection=self.scaled(n.detection)) for n in view.nodes))
        if self.on_map:
            view = self.on_map(self, view)
        if view is not None:
            for node in view.nodes:
                self.points[node.detection.anchor] = node.detection.key
        self.maps.append((self.captures, self.clock.now, view))
        return view

    def click(self, window, point):
        if not self.network_remaining:
            key = self.points.get(point)
            self.sent.append((key, self.clock.now, point, self.captures))
            if key and key.startswith('node_') and key != 'node_enter' and self.dropped:
                self.dropped -= 1
                self.actions.append(key)
                if self.on_click:
                    self.on_click(self, key)
                return
        super().click(window, point)


@pytest.fixture(scope='module')
def configs():
    return {name: chaos_config(name) for name in ['dark_twins', 'budget']}


def setup(config, size, *, kind='elite', dropped=1, timing=None):
    if timing:
        config = replace(config, chaos=replace(config.chaos, timing=dict(config.chaos.timing, **timing)))
    game = NodeGame(config, size, kind, dropped)
    deps = game.dependencies()
    if size != (2322, 1306):
        deps.windows.display_geometry = DisplayGeometry(
            1, r'\\.\DISPLAY1', Rect(0, 0, 2560, 1440), Size(2560, 1440), 120)
    stop = StopController()
    flow = ChaosFlow(config, deps, stop, SnapshotPublisher(
        RuntimeSnapshot.chaos_run('node-retry', 1), lambda _: None))
    flow.runtime.prepare()
    flow.state = RoundState.for_lineup(config.chaos.lineup)
    game.state = 'map'
    node = flow._map(1).nodes[0]
    game.maps.clear()
    assert flow.runtime.transform.actual == Size(*size)
    return game, flow, stop, node


def selections(game):
    return [s for s in game.sent if s[0] != 'node_enter']


class NodeEntered(Exception):
    pass


@pytest.mark.parametrize('lineup', ['dark_twins', 'budget'])
@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
def test_first_dropped_click_retries_original_node_for_all_routes(configs, monkeypatch, lineup, kind, size):
    game, flow, _, _ = setup(configs[lineup], size, kind=kind)

    def entered(*args):
        raise NodeEntered

    for method in ['_battle', '_camp', '_supply', '_event', '_shop']:
        monkeypatch.setattr(flow, method, entered)
    with pytest.raises(NodeEntered):
        flow._explore()
    clicks = selections(game)
    assert len(clicks) == 2 and all(c[0] == 'node_'+kind for c in clicks)
    assert clicks[1][1]-clicks[0][1] >= 1.5-1e-9
    assert clicks[1][3] >= clicks[0][3]+3
    assert game.actions.count('node_enter') == 1
    assert flow.state.node_kind == kind
    expected = 4. if kind == 'event' else .3
    assert game.clock.now-game.sent[-1][1] == pytest.approx(expected)


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
@pytest.mark.parametrize('kind', KINDS)
def test_successful_first_click_does_not_reinspect_map(configs, size, kind):
    game, flow, _, node = setup(configs['dark_twins'], size, kind=kind, dropped=0)
    flow._enter_node(node)
    assert game.actions == ['node_'+kind, 'node_enter']
    assert not game.maps
    assert flow._map_handoff is None


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
@pytest.mark.parametrize('maximum', [1, 2, 3])
def test_attempt_limit_includes_first_click(configs, size, maximum):
    game, flow, _, node = setup(configs['dark_twins'], size, dropped=99,
                               timing={'map_node_max_attempts': maximum})
    with pytest.raises(StopExecution) as error:
        flow._enter_node(node)
    assert error.value.reason == StopReason.RECOGNITION_TIMEOUT
    assert f'attempts={maximum}/{maximum}' in error.value.detail
    assert 'phase=node_selection' in error.value.detail
    assert len(selections(game)) == maximum and 'node_enter' not in game.actions
    assert len([e for e, _ in flow.runtime.deps.logger.events if e == 'chaos_node_click_retry']) == maximum-1


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
def test_last_allowed_attempt_can_succeed(configs, size):
    game, flow, _, node = setup(configs['dark_twins'], size, dropped=2)
    flow._enter_node(node)
    assert len(selections(game)) == 3 and game.actions.count('node_enter') == 1


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
@pytest.mark.parametrize('mode', ['unknown', 'floor', 'kind', 'outside', 'ambiguous', 'moving'])
def test_uncertain_original_target_cannot_authorize_retry(configs, size, mode):
    game, flow, _, node = setup(configs['dark_twins'], size, dropped=99,
                               timing={'page_timeout_ms': 2000})
    counter = 0

    def observe(g, view):
        nonlocal counter
        counter += 1
        if mode == 'unknown':
            return None
        if mode == 'floor':
            return replace(view, floor=2)
        current = view.nodes[0]
        if mode == 'kind':
            return replace(view, nodes=(replace(current, kind='rest'),))
        if mode == 'ambiguous':
            return replace(view, nodes=(current, replace(current, detection=replace(current.detection,
                           bounds=replace(current.detection.bounds, x=current.detection.bounds.x+2)))))
        dx = current.detection.bounds.width*2 if mode == 'outside' else (8 if counter % 2 else -8)
        hit = replace(current.detection, bounds=replace(current.detection.bounds, x=current.detection.bounds.x+dx))
        return replace(view, nodes=(replace(current, detection=hit),))

    game.on_map = observe
    with pytest.raises(StopExecution):
        flow._enter_node(node)
    assert len(selections(game)) == 1 and 'node_enter' not in game.actions


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
@pytest.mark.parametrize('recovery', [False, True])
def test_even_one_enter_frame_permanently_suppresses_node_retry(configs, monkeypatch, size, recovery):
    game, flow, _, node = setup(configs['dark_twins'], size, dropped=99,
                               timing={'page_timeout_ms': 2000})
    original = game.control
    seen = 0

    def control(frame, key, region=None):
        nonlocal seen
        if key != 'node_enter':
            return original(frame, key, region)
        seen += 1
        if seen == 1:
            return game._hit(key, frame)
        if seen == 2 and recovery:
            game.network_remaining = 4
        return None

    monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution) as error:
        flow._enter_node(node)
    assert 'phase=enter_button' in error.value.detail
    assert len(selections(game)) == 1 and not game.maps and 'node_enter' not in game.actions
    assert bool(game.actions.count('network_retry')) is recovery


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
def test_enter_evidence_wins_on_the_frame_a_retry_becomes_due(configs, monkeypatch, size):
    game, flow, _, node = setup(configs['dark_twins'], size, dropped=99,
                               timing={'page_timeout_ms': 2000})
    start = flow.runtime.active_monotonic()
    original = game.control
    pulse = []
    def control(frame, key, region=None):
        if key != 'node_enter':
            return original(frame, key, region)
        if not pulse and game.clock.now-start >= 1.5-1e-9:
            pulse.append(game.clock.now)
            return game._hit(key, frame)
        return None
    monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution) as error:
        flow._enter_node(node)
    assert pulse and 'phase=enter_button' in error.value.detail
    assert len(selections(game)) == 1 and 'node_enter' not in game.actions


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
def test_retry_and_enter_use_latest_client_anchor_without_reselection(configs, monkeypatch, size):
    game, flow, _, node = setup(configs['dark_twins'], size)
    latest = []

    def view(g, observed):
        current = observed.nodes[0]
        hit = replace(current.detection, bounds=replace(current.detection.bounds, x=current.detection.bounds.x+2),
                      anchor=ClientPoint(current.detection.anchor.x+2, current.detection.anchor.y))
        latest.append(hit.anchor)
        # A new, higher-priority node never replaces the already chosen target.
        other = replace(current, kind='rest', detection=replace(current.detection, key='node_rest',
                        bounds=replace(current.detection.bounds, x=0), anchor=ClientPoint(10, 10)))
        return replace(observed, nodes=(other, replace(current, detection=hit)))

    game.on_map = view
    control = game.control
    buttons = []

    def button(frame, key, region=None):
        hit = control(frame, key, region)
        if key == 'node_enter' and hit is not None:
            x = (size[0]-200)//4*4+len(buttons)
            hit = replace(hit, bounds=ClientRect(x, size[1]-80, 100, 30), anchor=ClientPoint(x+50, size[1]-65))
            buttons.append(hit.anchor)
            game.points[hit.anchor] = hit.key
        return hit

    monkeypatch.setattr(game, 'control', button)
    flow._enter_node(node)
    assert selections(game)[1][2] == latest[-1] != selections(game)[0][2]
    assert game.sent[-1][2] == buttons[-1] and len(buttons) == 3
    assert 'node_rest' not in game.actions


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
@pytest.mark.parametrize('phase', ['first', 'retry', 'enter'])
@pytest.mark.parametrize('boundary', ['stop', 'window'])
def test_all_inputs_preserve_stop_and_window_guards(configs, monkeypatch, size, phase, boundary):
    game, flow, stop, node = setup(configs['dark_twins'], size, dropped=0 if phase == 'enter' else 1)

    def reject():
        if boundary == 'stop':
            stop.request(StopReason.MANUAL_STOP)
        else:
            windows = flow.runtime.deps.windows
            windows.state = replace(windows.state, exists=False)

    if phase == 'first':
        reject()
    elif phase == 'retry':
        def view(g, observed):
            if g.clock.now >= 1.7:
                reject()
            return observed
        game.on_map = view
    else:
        original = game.control
        count = 0
        def control(frame, key, region=None):
            nonlocal count
            result = original(frame, key, region)
            if key == 'node_enter' and result is not None:
                count += 1
                if count == 3:
                    reject()
            return result
        monkeypatch.setattr(game, 'control', control)
    with pytest.raises(StopExecution) as error:
        flow._enter_node(node)
    assert error.value.reason == (StopReason.MANUAL_STOP if boundary == 'stop' else StopReason.WINDOW_ABNORMAL)
    assert len(selections(game)) == (0 if phase == 'first' else 1)
    assert 'node_enter' not in game.actions


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
def test_recovery_rebuilds_three_frame_target_evidence(configs, size):
    game, flow, _, node = setup(configs['dark_twins'], size)
    generations = []
    def view(g, observed):
        generations.append(flow.runtime.network_recovery_generation)
        if len(generations) == 1:
            g.clock.sleep(1.1)
        if len(generations) == 2:
            g.network_remaining = 4
        return observed
    game.on_map = view
    flow._enter_node(node)
    assert generations[:2] == [0, 0] and generations[2:] == [1, 1, 1]
    assert game.actions.count('network_retry') == 4
    assert len(selections(game)) == 2 and game.actions.count('node_enter') == 1


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
def test_retries_keep_one_original_deadline(configs, size):
    game, flow, _, node = setup(configs['dark_twins'], size, dropped=99,
                               timing={'page_timeout_ms': 2000})
    start = flow.runtime.active_monotonic()
    with pytest.raises(StopExecution) as error:
        flow._enter_node(node)
    assert error.value.reason == StopReason.RECOGNITION_TIMEOUT
    assert flow.runtime.active_monotonic()-start <= 2.1
    assert len(selections(game)) == 2


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
def test_recovery_does_not_refund_attempts_or_active_deadline(configs, size):
    game, flow, _, node = setup(configs['dark_twins'], size, dropped=99,
                               timing={'page_timeout_ms': 4000})
    def clicked(g, key):
        if key == 'node_elite' and g.actions.count(key) == 2:
            g.network_remaining = 4
    game.on_click = clicked
    start = flow.runtime.active_monotonic()
    with pytest.raises(StopExecution) as error:
        flow._enter_node(node)
    assert error.value.reason == StopReason.RECOGNITION_TIMEOUT
    assert len(selections(game)) == 3
    assert game.actions.count('network_retry') == 4
    assert flow.runtime.active_monotonic()-start <= 4.1
    assert game.clock.now-start > 40.


@pytest.mark.parametrize('size', SIZES, ids=['baseline', 'native'])
@pytest.mark.parametrize('slow', ['recognition', 'retry_log'])
def test_slow_work_cannot_authorize_input_after_deadline(configs, monkeypatch, size, slow):
    game, flow, _, node = setup(configs['dark_twins'], size)
    if slow == 'recognition':
        original = game.control
        def control(frame, key, region=None):
            result = original(frame, key, region)
            if key == 'node_enter':
                game.clock.sleep(16.)
            return result
        monkeypatch.setattr(game, 'control', control)
    else:
        logger = flow.runtime.deps.logger
        original = logger.event
        def event(name, **fields):
            original(name, **fields)
            if name == 'chaos_node_click_retry':
                game.clock.sleep(16.)
        monkeypatch.setattr(logger, 'event', event)
    with pytest.raises(StopExecution) as error:
        flow._enter_node(node)
    assert error.value.reason == StopReason.RECOGNITION_TIMEOUT
    assert len(selections(game)) == 1 and 'node_enter' not in game.actions
