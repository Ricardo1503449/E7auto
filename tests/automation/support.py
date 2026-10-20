from __future__ import annotations

from e7auto.runtime.stop_control import StopController

from dataclasses import replace

from e7auto.bootstrap import AutomationSession
from e7auto.configuration.models import RefreshStrategyConfig
from e7auto.core.domain import RuntimeSnapshot
from tests.helpers import FakeClock, FakeInput, FakeOverlay, FakeRuntimeEnvironment, FakeWindowService, ScriptedVision, make_config, make_dependencies


def run_session(
    vision: ScriptedVision,
    *,
    limit: int = 0,
    windows: FakeWindowService | None = None,
    inputs: FakeInput | None = None,
    overlay: FakeOverlay | None = None,
    control: StopController | None = None,
    config=None,
    enabled_optional_target_ids: frozenset[str] = frozenset(),
    clock: FakeClock | None = None,
    runtime: FakeRuntimeEnvironment | None = None,
    continuous_refresh: bool = False,
):
    snapshots: list[RuntimeSnapshot] = []
    deps, fake_windows, fake_inputs, fake_overlay, logger = make_dependencies(
        vision,
        windows=windows,
        inputs=inputs,
        overlay=overlay,
        clock=clock,
        runtime=runtime,
    )
    stop_control = control or StopController()
    final = AutomationSession(config or make_config(), deps, stop_control, snapshots.append).run(
        limit,
        "test-run",
        enabled_optional_target_ids=enabled_optional_target_ids,
        continuous_refresh=continuous_refresh,
    )
    return final, snapshots, fake_windows, fake_inputs, fake_overlay, stop_control, logger


def balances_for_refreshes(count: int, start: int = 1000) -> list[int]:
    values: list[int] = []
    balance = start
    for _ in range(count):
        values.extend((balance, balance - 3))
        balance -= 3
    return values


def compact_strategy_config(
    batches: tuple[int, int, int, int] = (1, 1, 1, 1),
    waits: tuple[int, int, int] = (5, 180, 5),
):
    return replace(
        make_config(),
        refresh_strategy=RefreshStrategyConfig(batches, waits),
    )
