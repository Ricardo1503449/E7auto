from __future__ import annotations

from typing import Callable
import uuid

from ..config import AppConfig
from ..domain import RuntimeSnapshot, StopReason
from ..ports import HotkeyService
from .dependencies import AutomationDependencies
from .engine import AutomationEngine
from .snapshots import SnapshotPublisher
from .stop_control import StopExecution, StopController


class AutomationSession:
    def __init__(
        self,
        config: AppConfig,
        dependencies: AutomationDependencies,
        hotkeys: HotkeyService,
        on_snapshot: Callable[[RuntimeSnapshot], None],
    ) -> None:
        self._config = config
        self._dependencies = dependencies
        self._hotkeys = hotkeys
        self._on_snapshot = on_snapshot
        self._control = StopController()

    def request_f5_stop(self) -> None:
        if self._control.request(StopReason.MANUAL_F5):
            self._dependencies.logger.event("stop_requested", source="F5")

    def run(
        self,
        refresh_limit: int,
        run_id: str | None = None,
        enabled_optional_target_ids: frozenset[str] = frozenset(),
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
        publisher = SnapshotPublisher(initial, self._on_snapshot)
        self._on_snapshot(initial)
        self._dependencies.logger.event(
            "target_selection",
            enabled=",".join(sorted(enabled_target_ids)),
            disabled=",".join(sorted(selectable_ids - set(enabled_target_ids))),
        )
        registered = False
        engine: AutomationEngine | None = None
        reason = StopReason.INTERNAL_ERROR
        detail = ""
        try:
            registered = self._hotkeys.register_f5(self.request_f5_stop)
            if not registered:
                reason = StopReason.HOTKEY_FAILURE
                detail = "RegisterHotKey(F5) failed"
                return publisher.finalize(reason)
            engine = AutomationEngine(
                self._config,
                self._dependencies,
                self._control,
                publisher,
                enabled_target_ids,
            )
            engine.execute()
            reason = StopReason.INTERNAL_ERROR
            detail = "engine returned without terminal reason"
        except StopExecution as exc:
            reason = exc.reason
            detail = exc.detail
        except Exception as exc:
            reason = StopReason.INTERNAL_ERROR
            detail = repr(exc)
        finally:
            if engine is not None and reason in {
                StopReason.BUDGET_COMPLETE,
                StopReason.REFRESH_STRATEGY_EXHAUSTED,
            }:
                try:
                    engine.finish_normal_run(reason)
                except StopExecution as exc:
                    reason = exc.reason
                    detail = exc.detail
                except Exception as exc:
                    reason = StopReason.INTERNAL_ERROR
                    detail = f"normal completion cleanup failed: {exc!r}"
            try:
                self._dependencies.capture.close()
            except Exception as exc:
                detail = f"{detail}; capture close failed: {exc}".strip("; ")
            if registered:
                try:
                    self._hotkeys.unregister_f5()
                except Exception as exc:
                    detail = f"{detail}; hotkey unregister failed: {exc}".strip("; ")
            final = publisher.finalize(reason)
            self._dependencies.logger.event(
                "run_stopped",
                reason=reason.value,
                detail=detail,
                refresh_spent=final.refresh_spent,
                refresh_limit=final.refresh_limit,
            )
            self._dependencies.logger.close()
        return publisher.snapshot
