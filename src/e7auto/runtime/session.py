from __future__ import annotations

from typing import Callable
import traceback

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.ports import HotkeyService
from e7auto.runtime.dependencies import AutomationDependencies
from e7auto.runtime.contracts import FeatureFlow, RunPolicy
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopExecution, StopController


class RuntimeSession:
    def __init__(
        self,
        dependencies: AutomationDependencies,
        hotkeys: HotkeyService,
        on_snapshot: Callable[[RuntimeSnapshot], None],
    ) -> None:
        self._dependencies = dependencies
        self._hotkeys = hotkeys
        self._on_snapshot = on_snapshot
        self._control = StopController()

    def request_f5_stop(self) -> None:
        if self._control.request(StopReason.MANUAL_F5):
            self._dependencies.logger.event("stop_requested", source="F5")

    def run(self, initial: RuntimeSnapshot,
            flow_factory: Callable[[StopController, SnapshotPublisher], FeatureFlow],
            policy: RunPolicy) -> RuntimeSnapshot:
        publisher = SnapshotPublisher(initial, self._on_snapshot)
        self._on_snapshot(initial)
        registered = False
        engine: FeatureFlow | None = None
        reason = StopReason.INTERNAL_ERROR
        detail = ""
        try:
            registered = self._hotkeys.register_f5(self.request_f5_stop)
            if not registered:
                reason = StopReason.HOTKEY_FAILURE
                detail = "RegisterHotKey(F5) failed"
                return publisher.finalize(reason)
            engine = flow_factory(self._control, publisher)
            engine.execute()
            reason = StopReason.INTERNAL_ERROR
            detail = "engine returned without terminal reason"
        except StopExecution as exc:
            reason = exc.reason
            detail = exc.detail
        except Exception as exc:
            reason = StopReason.INTERNAL_ERROR
            detail = repr(exc)
            self._dependencies.logger.event("internal_error", error=detail, traceback=traceback.format_exc())
        finally:
            if engine is not None and reason in policy.normal_completion:
                try:
                    engine.finish_normal_run(reason)
                except StopExecution as exc:
                    reason = exc.reason
                    detail = exc.detail
                except Exception as exc:
                    reason = StopReason.INTERNAL_ERROR
                    detail = f"normal completion cleanup failed: {exc!r}"
                    self._dependencies.logger.event("internal_error", error=detail, traceback=traceback.format_exc())
            try:
                if reason not in policy.expected_stops:
                    self._dependencies.logger.save_stop_snapshot(
                        engine.cached_game_frame() if engine is not None else None,
                        stop_reason=reason.value,
                        stopped_monotonic=self._dependencies.clock.monotonic(),
                    )
            except Exception as exc:
                # A diagnostic writer must never replace the automation failure or skip cleanup.
                try:
                    self._dependencies.logger.event(
                        "stop_snapshot", outcome="failed", stop_reason=reason.value,
                        source="last_successful_capture", detail=f"snapshot writer failed: {exc!r}",
                    )
                except Exception:
                    pass
            finally:
                if engine is not None:
                    engine.release_cached_game_frame()
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
                feature_id=final.feature_id,
                purchases_completed=final.purchases_completed,
                purchase_limit=final.purchase_limit,
            )
            self._dependencies.logger.close()
        return publisher.snapshot
