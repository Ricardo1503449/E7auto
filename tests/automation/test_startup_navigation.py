from dataclasses import replace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.observations import Observation
from e7auto.core.types import ClientPoint, ClientRect, Point, Size
from e7auto.runtime.context import RuntimeContext
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from e7auto.vision.frames import CoordinateTransform
from tests.helpers import make_config, make_dependencies, ScriptedVision


class NavigationGame(ScriptedVision):
    def __init__(self, *, state="other", menu_on=1, tavern_on=1, wake_on=1):
        super().__init__()
        self.state = state
        self.menu_on, self.tavern_on, self.wake_on = menu_on, tavern_on, wake_on
        self.attempts = dict(menu=0, tavern=0, wake=0)
        self.events = []
        self.home_frames = self.menu_frames = self.tavern_frames = 0
        self.before_detect = lambda kind: None

    def observation(self, kind):
        point = dict(home=ClientPoint(5, 5), menu=ClientPoint(90, 10), tavern=ClientPoint(80, 60))[kind]
        return Observation(kind, 1., ClientRect(point.x-2, point.y-2, 4, 4), point)

    def home(self, frame):
        self.before_detect("home")
        self.home_frames += 1
        return self.observation("home") if self.state == "home" else None

    def navigation_menu(self, frame):
        self.before_detect("menu")
        self.menu_frames += 1
        return self.observation("menu") if self.state == "other" else None

    def navigation_tavern(self, frame):
        self.before_detect("tavern")
        self.tavern_frames += 1
        return self.observation("tavern") if self.state == "sidebar" else None

    def click(self, window, point):
        kind = {ClientPoint(90, 10): "menu", ClientPoint(80, 60): "tavern", Point(50, 40): "wake"}[point]
        self.attempts[kind] += 1
        self.events.append((kind, self.clock.now, point))
        succeeds = self.attempts[kind] == getattr(self, kind+"_on")
        if succeeds:
            self.state = {"menu": "sidebar", "tavern": "home", "wake": "other"}[kind]


def runtime(game):
    config = make_config(stable_frames=3)
    config = replace(config, timing=replace(config.timing, poll_interval_ms=100))
    deps, *_ = make_dependencies(game)
    game.clock = deps.clock
    deps.inputs.click = game.click
    ctx = RuntimeContext(config, deps, StopController(),
                         SnapshotPublisher(RuntimeSnapshot.initial("navigation", (), 3), lambda _: None))
    ctx.prepare()
    return ctx


def wait(ctx, game, name="main_shop_icon"):
    return ctx.wait_startup_icon(name, game.home)


@pytest.mark.parametrize("name", ["main_shop_icon", "sanctuary_entry", "home_battle"])
@pytest.mark.parametrize("menu_on,tavern_on", [(1, 1), (2, 1), (1, 2), (2, 2)])
def test_returns_then_confirms_feature_entry(name, menu_on, tavern_on):
    game = NavigationGame(menu_on=menu_on, tavern_on=tavern_on)
    ctx = runtime(game)
    assert wait(ctx, game, name).object_id == "home"
    assert game.attempts == dict(menu=menu_on, tavern=tavern_on, wake=0)
    assert ctx.startup_return_attempts == dict(menu=menu_on, tavern=tavern_on)
    for kind in ("menu", "tavern"):
        events = [e for e in game.events if e[0] == kind]
        if len(events) == 2:
            assert 3.0-1e-9 <= events[1][1]-events[0][1] < 3.11


def test_visible_home_never_scans_navigation():
    game = NavigationGame(state="home")
    ctx = runtime(game)
    assert wait(ctx, game).object_id == "home"
    assert game.menu_frames == game.tavern_frames == 0 and not game.events


def test_wake_can_reveal_other_page_and_keeps_wake_count():
    game = NavigationGame(state="sleep", wake_on=2)
    ctx = runtime(game)
    assert wait(ctx, game).object_id == "home"
    assert [e[0] for e in game.events] == ["wake", "wake", "menu", "tavern"]
    assert ctx.startup_wake_attempts == 2


def test_initial_open_sidebar_does_not_use_tavern_branch():
    game = NavigationGame(state="sidebar", wake_on=None)
    ctx = runtime(game)
    with pytest.raises(StopExecution) as exc:
        wait(ctx, game)
    assert exc.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert game.attempts == dict(menu=0, tavern=0, wake=2)
    assert game.tavern_frames == 0


@pytest.mark.parametrize("failed", ["menu", "tavern"])
def test_two_failed_clicks_stop_without_wake_or_restarting(failed):
    game = NavigationGame(**{failed+"_on": None})
    ctx = runtime(game)
    with pytest.raises(StopExecution) as exc:
        wait(ctx, game)
    assert exc.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert game.attempts[failed] == 2 and game.attempts["wake"] == 0
    before = list(game.events)
    with pytest.raises(StopExecution):
        wait(ctx, game)
    assert game.events == before


@pytest.mark.parametrize("phase", ["menu", "tavern"])
def test_retry_requires_source_still_visible(phase):
    game = NavigationGame(**{phase+"_on": None})
    ctx = runtime(game)
    def change(kind):
        if game.attempts[phase] and ctx.active_monotonic() >= 2.9:
            game.state = "unknown"
    game.before_detect = change
    with pytest.raises(StopExecution):
        wait(ctx, game)
    assert game.attempts[phase] == 1 and game.attempts["wake"] == 0


def test_partial_home_confirmation_never_retries_original_button():
    game = NavigationGame(menu_on=None)
    ctx = runtime(game)
    def change(kind):
        if game.attempts["menu"] and ctx.active_monotonic() >= 3.1:
            game.state = "home"
    game.before_detect = change
    with pytest.raises(StopExecution):
        wait(ctx, game)
    assert game.attempts == dict(menu=1, tavern=0, wake=0)


def test_home_can_appear_without_tavern_click():
    game = NavigationGame()
    ctx = runtime(game)
    click = game.click
    def jump(window, point):
        click(window, point)
        game.state = "home"
    ctx.deps.inputs.click = jump
    assert wait(ctx, game).object_id == "home"
    assert game.attempts == dict(menu=1, tavern=0, wake=0)


@pytest.mark.parametrize("failure", ["stop", "window", "input"])
def test_guards_before_second_menu_click(failure):
    game = NavigationGame(menu_on=None)
    ctx = runtime(game)
    def trigger(kind):
        if game.attempts["menu"] and ctx.active_monotonic() >= 3.1:
            if failure == "stop":
                ctx.control.request(StopReason.MANUAL_STOP)
            elif failure == "window":
                ctx.deps.windows.abnormal_on_inspect = 0
            else:
                def fail(*_):raise OSError("input rejected")
                ctx.deps.inputs.click = fail
    game.before_detect = trigger
    with pytest.raises(StopExecution) as exc:
        wait(ctx, game)
    assert exc.value.reason is {"stop": StopReason.MANUAL_STOP, "window": StopReason.WINDOW_ABNORMAL,
                                "input": StopReason.INPUT_FAILURE}[failure]
    assert game.attempts["menu"] == 1 and game.attempts["wake"] == 0


@pytest.mark.parametrize("phase", ["menu", "tavern"])
def test_recovery_keeps_attempts_and_effective_time_discards_interrupted_frame(phase):
    game = NavigationGame(**{phase+"_on": 2})
    ctx = runtime(game)
    capture = ctx.capture
    injected = False
    captures_after = 0
    def recover():
        nonlocal injected, captures_after
        frame = capture()
        if game.attempts[phase] and not injected:
            injected = True
            ctx.network_recovery_generation += 1
            game.clock.sleep(12)
            ctx.network_paused_seconds += 12
            return "interrupted"
        if injected:captures_after += 1
        return frame
    ctx.capture = recover
    original = game.home
    def home(frame):
        assert frame != "interrupted" if isinstance(frame, str) else True
        return original(frame)
    assert ctx.wait_startup_icon("home", home).object_id == "home"
    assert injected and captures_after >= 3
    events = [e for e in game.events if e[0] == phase]
    assert 15.0-1e-9 <= events[1][1]-events[0][1] < 15.11
    assert game.attempts[phase] == 2 and game.attempts["wake"] == 0


def test_client_anchors_are_not_scaled_twice():
    game = NavigationGame()
    ctx = runtime(game)
    ctx.transform = CoordinateTransform(Size(100, 80), Size(150, 120))
    assert wait(ctx, game).object_id == "home"
    assert [e[2] for e in game.events] == [ClientPoint(90, 10), ClientPoint(80, 60)]


def test_normal_wait_does_not_start_navigation():
    game = NavigationGame()
    ctx = runtime(game)
    with pytest.raises(StopExecution):
        ctx.wait_stable_observation("home", game.home, 300)
    assert not game.events and game.menu_frames == game.tavern_frames == 0
