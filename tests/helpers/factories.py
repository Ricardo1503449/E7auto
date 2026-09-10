from __future__ import annotations

from .services import (
    FakeClock,
    FakeWindowService,
    FakeCapture,
    FakeInput,
    FakeRuntimeEnvironment,
    FakeOverlay,
    FakeLogger,
)
from .vision import ScriptedVision
from e7auto.automation import AutomationDependencies


def make_dependencies(
    vision: ScriptedVision,
    *,
    windows: FakeWindowService | None = None,
    inputs: FakeInput | None = None,
    overlay: FakeOverlay | None = None,
    logger: FakeLogger | None = None,
    clock: FakeClock | None = None,
    runtime: FakeRuntimeEnvironment | None = None,
) -> tuple[AutomationDependencies, FakeWindowService, FakeInput, FakeOverlay, FakeLogger]:
    window_service = windows or FakeWindowService()
    input_service = inputs or FakeInput()
    overlay_service = overlay or FakeOverlay()
    run_logger = logger or FakeLogger()
    clock_service = clock or FakeClock()
    runtime_environment = runtime or FakeRuntimeEnvironment()
    dependencies = AutomationDependencies(
        windows=window_service,
        capture=FakeCapture(),
        inputs=input_service,
        overlay=overlay_service,
        vision=vision,
        clock=clock_service,
        logger=run_logger,
        runtime=runtime_environment,
    )
    return dependencies, window_service, input_service, overlay_service, run_logger
