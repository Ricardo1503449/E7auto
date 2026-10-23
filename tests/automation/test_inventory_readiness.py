from dataclasses import replace
from collections import deque

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.features.shop.contracts import ScrollMovementObservation, PurchaseOutcome
from e7auto.features.shop.contracts import RowPixelChange
from e7auto.core.types import ClientRect
from e7auto.features.shop.flow import ShopFlow
from e7auto.features.shop.readiness import SceneWindow
from e7auto.features.shop.scrolling import FrameSample, ScrollProgress, ScrollServices, scroll_to_bottom
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from tests.helpers import FakeClock, FakeLogger, ScriptedVision, make_config, make_dependencies, match
from tests.automation.support import run_session


def pair(change=0, y=0):
    return ScrollMovementObservation(0, 0, 0, 0, y, .99, maximum_row_changed_fraction=change)


def window_config():
    config = make_config(stable_frames=3)
    return replace(config, timing=replace(config.timing, scan_timeout_ms=200),
                   inventory_readiness=replace(config.inventory_readiness, minimum_stable_ms=20))


def test_pixel_window_checks_first_to_last_not_just_neighbor_frames():
    config = window_config()
    window = SceneWindow(3, config.scroll, config.inventory_readiness)
    frames = [object(), object(), object()]
    def measure(a, b):
        return pair(.025 if a is frames[0] and b is frames[2] else .015)
    assert not window.observe(frames[0], 0, 0, "ready", measure)
    assert not window.observe(frames[1], .01, 0, "ready", measure)
    assert not window.observe(frames[2], .02, 0, "ready", measure)
    assert window.count == 1


@pytest.mark.parametrize("kind", ["overlay", "same_timestamp", "recovery", "large_change", "nan_change"])
def test_readiness_evidence_cannot_cross_invalid_frames(kind):
    config = window_config()
    window = SceneWindow(3, config.scroll, config.inventory_readiness)
    window.observe(object(), 0, 0, "ready", lambda *_: pair())
    window.observe(object(), .01, 0, "ready", lambda *_: pair())
    generation, timestamp, scene, change = 0, .02, "ready", 0
    if kind == "overlay": scene = "reward_overlay"
    if kind == "same_timestamp": timestamp = .01
    if kind == "recovery": generation = 1
    if kind == "large_change": change = .1
    if kind == "nan_change": change = float("nan")
    assert not window.observe(object(), timestamp, generation, scene, lambda *_: pair(change))
    assert window.count <= 1


def test_minimum_duration_requires_temporally_separated_evidence():
    config = window_config()
    window = SceneWindow(3, config.scroll, config.inventory_readiness)
    for t in (0, .001, .002):
        assert not window.observe(object(), t, 0, "ready", lambda *_: pair())
    assert window.observe(object(), .02, 0, "ready", lambda *_: pair())
    window.reset()
    assert window.first is window.previous is None


def engine(vision, config=None):
    clock = FakeClock()
    deps, _, inputs, _, logger = make_dependencies(vision, clock=clock)
    config = config or window_config()
    publisher = SnapshotPublisher(RuntimeSnapshot.initial("readiness", (("wood", "木材"),), 3), lambda _: None)
    flow = ShopFlow(config, deps, StopController(), publisher, frozenset({"wood", "ore"}))
    flow.runtime.prepare()
    return flow, clock, inputs, logger


def test_blank_refresh_animation_cannot_produce_an_empty_scan_or_scroll_reference():
    class Vision(ScriptedVision):
        scenes = deque(["incomplete_rows", "incomplete_rows", "ready", "ready", "ready"])
        def inventory_scene_state(self, frame, screen, *, slot_ids=None):
            return self.scenes.popleft()
    vision = Vision(top=[(), (), ()])
    flow, clock, _, _ = engine(vision)
    assert flow.inventory.stable_scan("top", set()) == ()
    reference = flow.inventory.take_scroll_reference()
    assert reference.frame is vision.scan_frames[-1]
    assert clock.now >= .04
    assert len(vision.scan_frames) == 3
    assert flow.inventory.reference is None


def test_reference_is_exact_last_validated_frame_with_no_extra_capture():
    vision = ScriptedVision(top=[(), (), ()])
    flow, _, _, _ = engine(vision)
    flow.inventory.stable_scan("top", set())
    validated = flow.inventory.reference.frame
    observed = []
    vision.inventory_scroll_movement = lambda before, after: observed.append(before) or vision.scroll_movement
    calls = flow.runtime.deps.capture.calls
    flow._scroll_to_bottom()
    assert observed == [validated]
    assert flow.runtime.deps.capture.calls - calls == 3


@pytest.mark.parametrize("cause", ["stale", "recovery"])
def test_invalid_reference_cannot_send_any_wheel(cause):
    flow, clock, inputs, _ = engine(ScriptedVision(top=[(), (), ()]))
    flow.inventory.stable_scan("top", set())
    if cause == "stale": clock.now += 1
    else: flow.runtime.network_recovery_generation += 1
    with pytest.raises(StopExecution, match="reference"):
        flow._scroll_to_bottom()
    assert not any(action == "scroll" for action, *_ in inputs.actions)


def test_refresh_balance_success_does_not_reuse_unready_top_result():
    class Vision(ScriptedVision):
        def inventory_scene_state(self, frame, screen, *, slot_ids=None): return "incomplete_rows"
    vision = Vision(balances=[97, 97, 97])
    flow, _, _, _ = engine(vision)
    assert flow.refresh.wait_for_refresh_balance(100, 97) is None
    assert flow.inventory.reference is None
    assert vision.scan_calls == []


def test_purchase_invalidates_reference_and_waits_until_toast_disappears():
    class Vision(ScriptedVision):
        scenes = deque(["ready", "reward_overlay", "reward_overlay", "ready"])
        def inventory_scene_state(self, frame, screen, *, slot_ids=None):
            return self.scenes.popleft() if screen == "top" and self.scenes else "ready"
    vision = Vision(top=[(match("wood"),), ()], purchase=[PurchaseOutcome.SUCCESS])
    config = make_config()
    config = replace(config, timing=replace(config.timing, scan_timeout_ms=100))
    final, _, _, inputs, _, _, logger = run_session(vision, config=config)
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert final.targets[0].acquired == 1
    assert len([a for a, *_ in inputs.actions if a == "scroll"]) == 1
    assert vision.scan_calls == ["top", "top", "bottom"]


def test_permanent_toast_times_out_before_any_scroll():
    class Vision(ScriptedVision):
        def inventory_scene_state(self, frame, screen, *, slot_ids=None): return "reward_overlay"
    final, _, _, inputs, _, _, _ = run_session(Vision())
    assert final.stop_reason is StopReason.RECOGNITION_TIMEOUT
    assert not any(a == "scroll" for a, *_ in inputs.actions)


def test_stop_during_scene_check_cannot_produce_an_action():
    control = StopController()
    class Vision(ScriptedVision):
        def inventory_scene_state(self, frame, screen, *, slot_ids=None):
            control.request(StopReason.MANUAL_STOP)
            return "ready"
    final, _, _, inputs, _, _, _ = run_session(Vision(), control=control)
    assert final.stop_reason is StopReason.MANUAL_STOP
    assert not any(a == "scroll" for a, *_ in inputs.actions)


def test_slow_scene_measurement_cannot_return_after_scan_deadline():
    vision = ScriptedVision()
    flow, clock, _, _ = engine(vision, make_config())
    def slow(*args):
        clock.sleep(.1)
        return "ready"
    vision.inventory_scene_state = slow
    with pytest.raises(StopExecution, match="timeout waiting"):
        flow.inventory.stable_scan("top", set())
    assert flow.inventory.reference is None


def test_frame_interrupted_by_recovery_is_discarded_before_scene_or_inventory_detection():
    vision = ScriptedVision(top=[(), (), ()])
    flow, clock, _, _ = engine(vision)
    original = flow.runtime.capture
    interrupted = []
    def capture():
        frame = original()
        if not interrupted:
            interrupted.append(frame)
            flow.runtime.network_recovery_generation += 1
        return frame
    flow.runtime.capture = capture
    flow.inventory.stable_scan("top", set())
    assert all(frame is not interrupted[0] for frame in vision.scan_frames)
    assert flow.inventory.reference.generation == 1


def test_ready_refresh_result_carries_the_same_validated_frame_into_scan_and_scroll():
    vision = ScriptedVision(top=[(), (), ()], balances=[97, 97, 97])
    flow, clock, _, _ = engine(vision)
    flow.state.pending_top_scan = flow.refresh.wait_for_refresh_balance(100, 97)
    assert flow.state.pending_top_scan == ()
    reference = flow.inventory.reference
    calls = flow.runtime.deps.capture.calls
    flow.inventory.scan_viewport("top", set())
    assert flow.inventory.reference is reference
    assert flow.runtime.deps.capture.calls == calls


def test_identical_empty_inventory_keys_cannot_hide_a_changing_scene():
    vision = ScriptedVision(top=[()] * 5)
    flow, _, _, _ = engine(vision)
    vision.inventory_scene_stability = lambda first, after, screen: pair(.1 if int(after[0, 0, 0]) <= 3 else 0)
    flow.inventory.stable_scan("top", set())
    assert len(vision.scan_frames) == 5
    assert flow.inventory.reference.frame is vision.scan_frames[-1]


def test_expired_refresh_reuse_is_discarded_before_a_new_top_scan():
    vision = ScriptedVision(top=[(), (), ()])
    flow, clock, _, _ = engine(vision)
    flow.inventory.reference = FrameSample(object(), 0)
    flow.state.pending_top_scan = (match("wood"),)
    clock.now = 1
    flow.inventory.scan_viewport("top", set())
    assert flow.state.pending_top_scan is None
    assert not vision.purchase_queries


def test_purchase_invalidates_any_previous_reference_even_if_it_then_stops():
    vision = ScriptedVision(top=[(), (), ()], purchase=[PurchaseOutcome.SUCCESS])
    flow, _, _, _ = engine(vision)
    flow.inventory.stable_scan("top", set())
    assert flow.inventory.reference is not None
    flow.inventory.purchase(match("wood"))
    assert flow.inventory.reference is None


@pytest.mark.parametrize("scene,change", [("reward_overlay", 0), ("ready", .1)])
def test_after_scroll_checks_reject_static_toast_or_changing_pixels(scene, change):
    config = make_config()
    clock, logger = FakeClock(), FakeLogger()
    services = ScrollServices(capture=object, dispatch_scroll=lambda *_: None, checkpoint=lambda: None,
                              sleep=clock.sleep, active_monotonic=clock.monotonic,
                              measure_stability=lambda *_: pair(change),
                              measure_movement=lambda *_: pytest.fail("Unready scene entered content verification"),
                              logger=logger, scene_state=lambda _: scene)
    with pytest.raises(StopExecution):
        scroll_to_bottom(config.scroll, 3, services, ScrollProgress(),
                         reference=FrameSample(object(), 0), readiness=config.inventory_readiness)
    assert clock.now == pytest.approx(.8)


def test_after_scroll_checks_detect_slow_drift_across_the_whole_window():
    config = make_config()
    clock, logger = FakeClock(), FakeLogger()
    frames = iter(range(20))
    services = ScrollServices(capture=lambda: next(frames), dispatch_scroll=lambda *_: None,
                              checkpoint=lambda: None, sleep=clock.sleep, active_monotonic=clock.monotonic,
                              measure_stability=lambda a, b: pair(abs(b - a) * .015),
                              measure_movement=lambda *_: pytest.fail("Slow drift was authorized"),
                              logger=logger, scene_state=lambda _: "ready")
    with pytest.raises(StopExecution):
        scroll_to_bottom(config.scroll, 3, services, ScrollProgress(),
                         reference=FrameSample(object(), 0), readiness=config.inventory_readiness)


def test_scanner_passes_its_actual_view_to_every_scene_comparison():
    class Vision(ScriptedVision):
        def __init__(self):
            super().__init__(top=[()] * 3, bottom=[()] * 3)
            self.views = []
        def inventory_scene_stability(self, before, after, screen, *, slot_ids=None):
            self.views.append(screen)
            return self.default_scroll_stability
        def inventory_scroll_stability(self, before, after):
            # Mirror production delegation; the cached bottom scan reuses this evidence.
            return self.inventory_scene_stability(before, after, "bottom")
    vision = Vision()
    final, *_ = run_session(vision, config=make_config(stable_frames=3))
    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert vision.views == ["top"] * 3 + ["bottom"] * 3


def test_scroll_trace_keeps_each_row_and_separates_neighbor_from_span_changes():
    config = make_config()
    clock, logger = FakeClock(), FakeLogger()
    frames = iter(range(3))
    def stability(a, b):
        fraction = .015 if (a, b) == (0, 2) else .01
        rows = (RowPixelChange("bottom_3", ClientRect(1002, 301, 176, 172), .001),
                RowPixelChange("bottom_4", ClientRect(1002, 564, 176, 172), fraction))
        return replace(pair(fraction), row_pixel_changes=rows)
    movement = ScrollMovementObservation(20, .4, 255, 0, -394, .4, True, "matched")
    services = ScrollServices(capture=lambda: next(frames), dispatch_scroll=lambda *_: None,
                              checkpoint=lambda: None, sleep=clock.sleep, active_monotonic=clock.monotonic,
                              measure_stability=stability, measure_movement=lambda *_: movement,
                              logger=logger, scene_state=lambda _: "ready")
    scroll_to_bottom(config.scroll, 3, services, ScrollProgress(), reference=FrameSample(object(), 0),
                     readiness=config.inventory_readiness)
    trace = next(fields for event, fields in logger.events if event == "scroll_settle_trace")
    assert trace["row_regions"] == "bottom_3:1002,301,176,172|bottom_4:1002,564,176,172"
    assert trace["pair_row_changes"].split(";")[-1] == "bottom_3:0.001000|bottom_4:0.010000"
    assert trace["span_row_changes"].split(";")[-1] == "bottom_3:0.001000|bottom_4:0.015000"
    assert trace["maximum_row_changed_fraction"].endswith("0.015000")


def test_rejected_scene_window_retains_the_offending_row_details():
    config = window_config()
    window = SceneWindow(3, config.scroll, config.inventory_readiness)
    change = RowPixelChange("top_2", ClientRect(1002, 432, 176, 172), .04)
    window.observe(object(), 0, 0, "ready", lambda *_: pair())
    window.observe(object(), .01, 0, "ready", lambda *_: replace(pair(.04), row_pixel_changes=(change,)))
    assert window.reason == "changing_pixels_or_position"
    assert window.pair_rows == (change,)
    assert window.span_rows == ()
