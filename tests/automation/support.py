from __future__ import annotations

from dataclasses import replace

from e7auto.automation import AutomationSession
from e7auto.config import RefreshStrategyConfig
from e7auto.domain import RuntimeSnapshot
from tests.helpers import (
    FakeHotkeys,
    FakeClock,
    FakeInput,
    FakeOverlay,
    FakeRuntimeEnvironment,
    FakeWindowService,
    ScriptedVision,
    make_config,
    make_dependencies,
)


def run_session(
    vision: ScriptedVision,
    *,
    limit: int = 0,
    windows: FakeWindowService | None = None,
    inputs: FakeInput | None = None,
    overlay: FakeOverlay | None = None,
    hotkeys: FakeHotkeys | None = None,
    config=None,
    enabled_optional_target_ids: frozenset[str] = frozenset(),
    clock: FakeClock | None = None,
    runtime: FakeRuntimeEnvironment | None = None,
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
    fake_hotkeys = hotkeys or FakeHotkeys()
    final = AutomationSession(config or make_config(), deps, fake_hotkeys, snapshots.append).run(
        limit,
        "test-run",
        enabled_optional_target_ids=enabled_optional_target_ids,
    )
    return final, snapshots, fake_windows, fake_inputs, fake_overlay, fake_hotkeys, logger


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
