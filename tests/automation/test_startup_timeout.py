from __future__ import annotations

from dataclasses import replace

import pytest

from e7auto.automation import AutomationEngine, SnapshotPublisher, StopController
from e7auto.automation.stop_control import StopExecution
from e7auto.domain import RuntimeSnapshot, StopReason
from tests.helpers import FakeClock, FakeInput, ScriptedVision, make_config, make_dependencies

from .support import run_session


def startup_config():
    config = make_config()
    return replace(
        config,
        timing=replace(
            config.timing, poll_interval_ms=100, entry_timeout_ms=5000,
            scan_timeout_ms=1000, stable_frames=3,
        ),
    )


@pytest.mark.parametrize("ready_after", [0.0, 6.0, 9.0, 10.5])
def test_startup_waits_up_to_ten_seconds_and_continues_when_ready(ready_after: float) -> None:
    clock = FakeClock()
    click_times: list[float] = []

    class DelayedMainVision(ScriptedVision):
        def main_shop_icon(self, frame):
            if clock.monotonic() < ready_after:
                return None
            return super().main_shop_icon(frame)

    class TimedInput(FakeInput):
        def click(self, window, point):
            click_times.append(clock.monotonic())
            super().click(window, point)

    final, _, _, inputs, _, _, _ = run_session(
        DelayedMainVision(top=[()], bottom=[()]),
        clock=clock,
        inputs=TimedInput(),
        config=startup_config(),
    )

    if ready_after > 10:
        assert final.stop_reason is StopReason.RECOGNITION_TIMEOUT
        assert inputs.actions == []
        assert 10 <= clock.monotonic() < 10.2
    else:
        assert final.stop_reason is StopReason.BUDGET_COMPLETE
        # Three stable frames are still required; no fixed ten-second sleep.
        assert ready_after + 0.19 <= click_times[0] < ready_after + 0.4


def test_reentry_main_icon_still_times_out_after_five_seconds() -> None:
    config = startup_config()
    clock = FakeClock()
    deps, _, inputs, _, _ = make_dependencies(
        ScriptedVision(main_visible=False), clock=clock,
    )
    initial = RuntimeSnapshot.initial(
        "reentry-timeout", tuple((t.target_id, t.display_name) for t in config.targets), 0,
    )
    engine = AutomationEngine(
        config, deps, StopController(), SnapshotPublisher(initial, lambda _: None),
        frozenset(t.target_id for t in config.targets),
    )
    engine._prepare()

    with pytest.raises(StopExecution) as stopped:
        engine._enter_store()

    assert stopped.value.reason is StopReason.RECOGNITION_TIMEOUT
    assert stopped.value.detail == "timeout waiting for main_shop_icon"
    assert 5 <= clock.monotonic() < 5.2
    assert inputs.actions == []
