"""Explicit feature selection and production service composition."""
from __future__ import annotations
import uuid
from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.runtime.session import RuntimeSession
from e7auto.runtime.contracts import RunPolicy
from e7auto.features.shop.flow import ShopFlow
from e7auto.features.penguin.flow import PenguinFlow
from e7auto.resources.templates import TemplateRepository
from e7auto.runtime.dependencies import AutomationDependencies, SystemClock

NORMAL_COMPLETION = frozenset({
    StopReason.BUDGET_COMPLETE, StopReason.REFRESH_STRATEGY_EXHAUSTED,
    StopReason.PENGUIN_LIMIT_COMPLETE, StopReason.PENGUIN_FUNDS_COMPLETE,
})
RUN_POLICY = RunPolicy(
    normal_completion=NORMAL_COMPLETION,
    expected_stops=NORMAL_COMPLETION | {StopReason.PURCHASE_FUNDS_INSUFFICIENT, StopReason.MANUAL_F5},
)


def create_production_session(config, feature_id, overlay, logger, on_snapshot):
    """Called by the worker thread; native capture must never be imported at module scope."""
    if feature_id not in {"shop", "penguin"}:
        raise ValueError(f"Unknown feature: {feature_id}")
    templates = TemplateRepository(config)
    if feature_id == "shop":
        from e7auto.features.shop.vision import ShopVision
        vision = ShopVision(config, templates)
    else:
        from e7auto.features.penguin.vision import PenguinVision
        vision = PenguinVision(config, templates)
    from e7auto.platform.wgc_capture import WindowsGraphicsCaptureService
    from e7auto.platform.input import Win32WindowMessageInputService
    from e7auto.platform.windows import Win32F5HotkeyService, Win32RuntimeEnvironment, Win32WindowService

    dependencies = AutomationDependencies(
        windows=Win32WindowService(),
        capture=WindowsGraphicsCaptureService(logger=logger),
        inputs=Win32WindowMessageInputService(),
        overlay=overlay,
        vision=vision,
        clock=SystemClock(),
        logger=logger,
        runtime=Win32RuntimeEnvironment(),
    )
    return AutomationSession(config, dependencies, Win32F5HotkeyService(), on_snapshot)

class AutomationSession:
    """Application facade retaining the two supported start requests."""
    def __init__(self, config, dependencies, hotkeys, on_snapshot):
        self._config = config
        self._dependencies = dependencies
        self._runner = RuntimeSession(dependencies, hotkeys, on_snapshot)

    def request_f5_stop(self):
        self._runner.request_f5_stop()

    def run(
        self,
        refresh_limit: int,
        run_id: str | None = None,
        enabled_optional_target_ids: frozenset[str] = frozenset(),
        *,
        continuous_refresh: bool = False,
    ) -> RuntimeSnapshot:
        if isinstance(refresh_limit, bool) or not isinstance(refresh_limit, int) or refresh_limit < 0:
            raise ValueError("refresh_limit must be a non-negative integer")
        run_id = run_id or uuid.uuid4().hex[:12]
        selectable_ids = {
            target.target_id for target in self._config.targets if target.user_selectable
        }
        unknown_optional_ids = set(enabled_optional_target_ids) - selectable_ids
        if unknown_optional_ids:
            raise ValueError(
                f"Unknown or non-selectable optional targets: {sorted(unknown_optional_ids)}"
            )
        enabled_target_ids = frozenset(
            target.target_id
            for target in self._config.targets
            if not target.user_selectable or target.target_id in enabled_optional_target_ids
        )
        initial = RuntimeSnapshot.initial(
            run_id,
            tuple((target.target_id, target.display_name) for target in self._config.targets),
            refresh_limit,
        )
        self._dependencies.logger.event(
            "refresh_mode",
            continuous_refresh=continuous_refresh,
        )
        self._dependencies.logger.event(
            "target_selection",
            enabled=",".join(sorted(enabled_target_ids)),
            disabled=",".join(sorted(selectable_ids - set(enabled_target_ids))),
        )
        return self._runner.run(
            initial,
            lambda control, publisher: ShopFlow(
                self._config, self._dependencies, control, publisher, enabled_target_ids,
                continuous_refresh=continuous_refresh,
            ),
            RUN_POLICY,
        )

    def run_penguins(self, purchase_limit: int, run_id: str | None = None) -> RuntimeSnapshot:
        if (isinstance(purchase_limit, bool) or not isinstance(purchase_limit, int)
                or not 0 <= purchase_limit <= 2_147_483_647):
            raise ValueError("purchase_limit must be a bounded non-negative integer")

        initial = RuntimeSnapshot.penguins(run_id or uuid.uuid4().hex[:12], purchase_limit)
        return self._runner.run(
            initial,
            lambda control, publisher: PenguinFlow(self._config, self._dependencies, control, publisher),
            RUN_POLICY,
        )
